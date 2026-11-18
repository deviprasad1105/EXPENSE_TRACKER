"""Credit cards: what each card owes, its bill and due date, and reminders as the due date gets near.

A card's bill comes from its statement SMS (auto-tracking), is typed in, or, when neither has come, is estimated
from the card's due day (Credit Cards page) and what it owes. A bill counts as paid once payments into the card since
its statement cover it (a transfer from the bank, or money received on the card), or when the user marks it paid.

Reminders go once each: a few days before the due date (the card's remind_days), on the day, and the day after if it
is still unpaid. They are a notification and an email, sent by the evening job (and caught up on opening the page).
"""

import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import false, func, select, true
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import STATUS_APPROVED, Account, AccountTransfer, CardBill, Fund, Notification, User
from ..timeutil import now_local, today_local
from .accounts import balances
from .jobs import claim_run
from .mailer import send_email

log = logging.getLogger(__name__)

CARD = "CREDIT_CARD"
SRC_SMS, SRC_MANUAL, SRC_ESTIMATE = "SMS", "MANUAL", "ESTIMATE"
PAID, DUE, OVERDUE = "PAID", "DUE", "OVERDUE"
STAGE_BEFORE, STAGE_TODAY, STAGE_OVERDUE = "BEFORE", "TODAY", "OVERDUE"
# A statement message and an estimate (or two messages) within this many days of each other are the same bill
SAME_BILL_DAYS = 7
# With no statement date, payments this long before the due date count towards the bill
PAY_WINDOW_DAYS = 25


def day_in_month(year: int, month: int, day: int) -> date:
    """The given day of a month, or its last day for a short month (31 → 30 Nov)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _add_months(d: date, months: int, day: int) -> date:
    m = d.month - 1 + months
    return day_in_month(d.year + m // 12, m % 12 + 1, day)


def next_due_date(card: Account, today: date) -> date | None:
    """This month's due day if it hasn't passed, else next month's."""
    if not card.due_day:
        return None
    this = day_in_month(today.year, today.month, card.due_day)
    return this if this >= today else _add_months(today, 1, card.due_day)


def statement_date_for(card: Account, due: date) -> date | None:
    """The latest statement day before `due` (the card's statement_day)."""
    if not card.statement_day:
        return None
    s = day_in_month(due.year, due.month, card.statement_day)
    return s if s < due else _add_months(due, -1, card.statement_day)


def owed(balance: Decimal) -> Decimal:
    """A card's balance is negative while money is owed on it."""
    return max(Decimal(0), -Decimal(balance))


def paid_since(db: Session, card_id: int, since: date) -> Decimal:
    """Payments into the card since `since`: transfers to it and money received on it (refunds, cashback)."""
    t = db.scalar(select(func.coalesce(func.sum(AccountTransfer.amount), 0)).where(
        AccountTransfer.to_account_id == card_id, AccountTransfer.transfer_date >= since))
    f = db.scalar(select(func.coalesce(func.sum(Fund.amount), 0)).where(Fund.account_id == card_id, Fund.fund_date >= since))
    return Decimal(t or 0) + Decimal(f or 0)


def bill_state(db: Session, bill: CardBill, today: date) -> dict:
    since = bill.statement_date or bill.due_date - timedelta(days=PAY_WINDOW_DAYS)
    paid_amount = paid_since(db, bill.account_id, since)
    paid = bill.paid_at is not None or paid_amount >= bill.total_due
    days_left = (bill.due_date - today).days
    return {
        "paid_amount": min(paid_amount, bill.total_due),
        "paid": paid,
        "days_left": days_left,
        "status": PAID if paid else (OVERDUE if days_left < 0 else DUE),
    }


def find_bill(db: Session, card_id: int, due: date) -> CardBill | None:
    return db.scalar(select(CardBill).where(
        CardBill.account_id == card_id,
        CardBill.due_date.between(due - timedelta(days=SAME_BILL_DAYS), due + timedelta(days=SAME_BILL_DAYS)),
    ).order_by(CardBill.due_date.desc()).limit(1))


def save_bill(db: Session, card: Account, due: date, total: Decimal, source: str, min_due: Decimal | None = None,
              statement_date: date | None = None) -> CardBill:
    """Add a bill, or update the one for the same cycle. A typed-in bill isn't overwritten by a message or estimate."""
    bill = find_bill(db, card.id, due)
    if bill and bill.source == SRC_MANUAL and source != SRC_MANUAL:
        return bill
    if bill is None:
        bill = CardBill(user_id=card.user_id, account_id=card.id, due_date=due, total_due=total, source=source)
        db.add(bill)
    if bill.due_date != due:
        bill.reminded = None  # a new due date gets its own reminders
    bill.due_date, bill.total_due, bill.source = due, total, source
    bill.min_due = min_due
    bill.statement_date = statement_date or bill.statement_date or statement_date_for(card, due)
    db.flush()
    return bill


def current_bill(db: Session, card: Account, today: date, owes: Decimal) -> CardBill | None:
    """The bill to show and remind about: the oldest one still unpaid (an overdue bill before next month's), else the
    latest if it is still to come, else an estimate for the next due date (when the card has a due day and owes money).

    An unpaid estimate follows what the card owes; one overtaken by a later bill is dropped."""
    bills = list(db.scalars(select(CardBill).where(CardBill.account_id == card.id).order_by(CardBill.due_date.desc()).limit(6)))
    paid = {b.id: bill_state(db, b, today)["paid"] for b in bills}
    for b in list(bills):
        if b.source == SRC_ESTIMATE and not paid[b.id] and any(o.due_date > b.due_date for o in bills):
            db.delete(b)
            bills.remove(b)
    unpaid = sorted((b for b in bills if not paid[b.id]), key=lambda b: b.due_date)
    if unpaid:
        bill = unpaid[0]
        if bill.source == SRC_ESTIMATE and owes > 0:
            bill.total_due = owes
        db.flush()
        return bill
    latest = bills[0] if bills else None
    if latest and latest.due_date >= today:
        return latest
    due = next_due_date(card, today)
    if due and owes > 0:
        same = find_bill(db, card.id, due)
        if same and bill_state(db, same, today)["paid"]:
            return same  # this cycle is already paid: don't turn it back into an unpaid estimate
        return save_bill(db, card, due, owes, SRC_ESTIMATE)
    db.flush()
    return latest


def _remind(db: Session, user: User, card: Account, bill: CardBill, state: dict, email: bool) -> bool:
    if state["paid"] or not card.remind_on or not card.is_active:
        return False
    days = state["days_left"]
    if 0 < days <= card.remind_days:
        stage, when = STAGE_BEFORE, f"is due in {days} day{'s' if days != 1 else ''}, on {bill.due_date:%d %b}"
    elif days == 0:
        stage, when = STAGE_TODAY, "is due today"
    elif -3 <= days < 0:
        stage, when = STAGE_OVERDUE, f"was due on {bill.due_date:%d %b} and isn't paid yet"
    else:
        return False
    sent = set((bill.reminded or "").split(",")) - {""}
    # A reminder that would repeat a more urgent one already sent isn't sent (e.g. BEFORE after TODAY)
    if stage in sent or (stage == STAGE_BEFORE and sent & {STAGE_TODAY, STAGE_OVERDUE}) or (stage == STAGE_TODAY and STAGE_OVERDUE in sent):
        return False
    left = bill.total_due - state["paid_amount"]
    minimum = f" (minimum {bill.min_due})" if bill.min_due else ""
    estimate = " (estimated from what the card owes)" if bill.source == SRC_ESTIMATE else ""
    message = f"{card.name} bill of {left}{minimum}{estimate} {when}."
    db.add(Notification(user_id=user.id, kind="CARD_DUE", message=message, ref_date=bill.due_date))
    bill.reminded = ",".join(sorted(sent | {stage}))
    if email:
        send_email(user.email, f"Expense Tracker: {card.name} bill {when.split(',')[0]}",
                   f"Hi {user.full_name},\n\n{message}\n\nPay it on time to avoid late fees and interest.\n")
    return True


def cards_of(db: Session, user_id: int, active_only: bool = False) -> list[Account]:
    stmt = select(Account).where(Account.user_id == user_id, Account.kind == CARD)
    if active_only:
        stmt = stmt.where(Account.is_active == true())
    return list(db.scalars(stmt.order_by(Account.name)))


def run_for_user(db: Session, user: User, today: date, email: bool = True) -> int:
    """Refresh estimates and send any reminders that are due for the user's cards. Returns how many were sent."""
    cards = cards_of(db, user.id, active_only=True)
    if not cards:
        return 0
    bal = balances(db, user.id, today.replace(day=1))
    sent = 0
    for card in cards:
        bill = current_bill(db, card, today, owed(bal.get(card.id, {}).get("balance", 0)))
        if bill:
            sent += _remind(db, user, card, bill, bill_state(db, bill, today), email)
    db.flush()
    return sent


def card_reminder_job():
    """Every evening (with the daily reminder): card bill reminders for everyone with a credit card."""
    db = SessionLocal()
    try:
        today = today_local()
        if not claim_run(db, "card_reminders", today.isoformat()):
            return
        users = db.scalars(select(User).where(
            User.status == STATUS_APPROVED, User.is_locked == false(), User.feature_expense == true(),
            User.id.in_(select(Account.user_id).where(Account.kind == CARD, Account.is_active == true())),
        )).all()
        total = 0
        for user in users:
            try:
                total += run_for_user(db, user, today)
                db.commit()
            except Exception:
                db.rollback()
                log.exception("Card reminders: @%s failed", user.username)
        log.info("Card reminders: sent %d for %s", total, today)
    finally:
        db.close()


def mark_paid(bill: CardBill, paid: bool) -> None:
    bill.paid_at = now_local() if paid else None
