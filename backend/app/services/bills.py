"""Bills you pay yourself (electricity, mobile, rent, insurance, EMIs...): due dates, marking them paid, spotting the
payment among expenses, and "due soon" / "overdue" reminders.

A bill's schedule works like a subscription's (same frequency fields, day of the month kept), but nothing is charged
automatically: the bill now due stays due until it is marked paid or skipped.
"""

import re
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select, true
from sqlalchemy.orm import Session

from ..accounting.autopost import charge_day_locked
from ..models import ROLE_ADMIN, Account, Bill, BillPayment, Expense, Notification, User
from ..timeutil import today_local
from .accounts import primary_account_id
from .budgets import check_budgets
from .mailer import send_email
from .subscriptions import next_charge_on_or_after

KIND_DUE = "BILL_DUE"
KIND_OVERDUE = "BILL_OVERDUE"

# How far around the due date a payment is looked for among expenses
MATCH_BEFORE_DAYS = 25
MATCH_AFTER_DAYS = 10
# Words that say nothing about who a bill is paid to
_GENERIC = {"bill", "bills", "payment", "pay", "paid", "monthly", "the", "and", "for", "my", "home", "house", "ltd",
            "limited", "india", "pvt", "of", "to"}


def next_due_after(bill: Bill, due: date) -> date | None:
    """The bill after the one due on `due`, or None if the bill has ended."""
    return next_charge_on_or_after(bill, due + timedelta(days=1))


def bill_status(bill: Bill, today: date | None = None) -> str:
    today = today or today_local()
    if not bill.is_active:
        return "PAUSED"
    if not bill.next_due_date:
        return "DONE"
    if bill.next_due_date < today:
        return "OVERDUE"
    if bill.next_due_date <= today + timedelta(days=max(bill.remind_days_before, 0)):
        return "DUE_SOON"
    return "UPCOMING"


def _words(*texts: str | None) -> set[str]:
    words = set()
    for t in texts:
        words |= {w for w in re.findall(r"[a-z0-9]+", (t or "").lower()) if len(w) >= 3 and w not in _GENERIC}
    return words


def linked_expense_ids(db: Session, user_id: int) -> set[int]:
    return set(db.scalars(
        select(BillPayment.expense_id).where(BillPayment.user_id == user_id, BillPayment.expense_id.is_not(None))
    ))


def last_payment(db: Session, bill: Bill) -> BillPayment | None:
    return db.scalar(
        select(BillPayment).where(BillPayment.bill_id == bill.id, BillPayment.amount.is_not(None))
        .order_by(BillPayment.due_date.desc(), BillPayment.id.desc()).limit(1)
    )


def find_matches(db: Session, bill: Bill, today: date | None = None, linked: set[int] | None = None) -> list[Expense]:
    """Expenses around the due date that look like this bill's payment (its name or biller in the expense), best first.
    Expenses already linked to a bill payment, and ones before the last payment of this bill, are left out."""
    due = bill.next_due_date
    words = _words(bill.name, bill.biller)
    if not due or not words:
        return []
    today = today or today_local()
    start = due - timedelta(days=MATCH_BEFORE_DAYS)
    last = last_payment(db, bill)
    if last and last.paid_on and last.paid_on >= start:
        start = last.paid_on + timedelta(days=1)
    end = max(due + timedelta(days=MATCH_AFTER_DAYS), today)
    linked = linked_expense_ids(db, bill.user_id) if linked is None else linked
    candidates = db.scalars(
        select(Expense).where(Expense.user_id == bill.user_id, Expense.expense_date >= start, Expense.expense_date <= end)
    ).all()
    scored = []
    for e in candidates:
        if e.id in linked:
            continue
        hits = len(words & _words(e.item_name, e.raw_name, e.note, e.payee_key))
        if not hits:
            continue
        close = bill.amount is not None and abs(Decimal(e.amount) - bill.amount) <= bill.amount * Decimal("0.15")
        scored.append((-(hits + 2 * close), abs((e.expense_date - due).days), e.id, e))
    return [e for *_, e in sorted(scored)]


def check_account(db: Session, user_id: int, account_id: int | None) -> int | None:
    """The account id if it is one of the user's (None stays None)."""
    if account_id is None:
        return None
    acc = db.get(Account, account_id)
    if not acc or acc.user_id != user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That account isn't one of yours")
    return acc.id


def _advance(bill: Bill) -> None:
    bill.next_due_date = next_due_after(bill, bill.next_due_date)
    if bill.next_due_date is None:
        bill.is_active = False


def pay(db: Session, bill: Bill, owner: User, *, expense_id: int | None, amount: Decimal | None, paid_on: date | None,
        log_expense: bool, account_id: int | None) -> BillPayment:
    """Mark the bill now due as paid and move on to the next one. Either links an expense already recorded (e.g. by
    auto-tracking), or logs a new one when asked (and the user has expense tracking), or just records the payment."""
    if not bill.next_due_date:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "There is no bill waiting to be paid")
    due = bill.next_due_date
    payment = BillPayment(bill_id=bill.id, user_id=bill.user_id, due_date=due)
    if expense_id is not None:
        expense = db.get(Expense, expense_id)
        if not expense or expense.user_id != bill.user_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Expense not found")
        if expense.id in linked_expense_ids(db, bill.user_id):
            raise HTTPException(status.HTTP_409_CONFLICT, "That expense is already the payment of a bill")
        payment.expense_id, payment.amount, payment.paid_on = expense.id, expense.amount, expense.expense_date
    else:
        amount = amount if amount is not None else bill.amount
        if amount is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter the amount you paid")
        paid_on = paid_on or today_local()
        payment.amount, payment.paid_on = amount, paid_on
        if log_expense and (owner.role == ROLE_ADMIN or owner.feature_expense):
            if charge_day_locked(db, owner.id, paid_on):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{paid_on:%d %b %Y} is in a locked accounting period. Pick another date, or don't log an expense.")
            account = check_account(db, owner.id, account_id if account_id is not None else bill.account_id)
            expense = Expense(
                user_id=owner.id, expense_date=paid_on, item_name=bill.name[:100], category=bill.category, quantity=1,
                amount=amount, note=f"Bill payment (due {due:%d %b %Y})"[:500], is_extra=True,
                account_id=account or primary_account_id(db, owner.id),
            )
            db.add(expense)
            db.flush()
            payment.expense_id, payment.made_expense = expense.id, True
    db.add(payment)
    _advance(bill)
    db.flush()
    if payment.made_expense:
        check_budgets(db, owner, payment.paid_on, payment.paid_on, email=False)
    return payment


def skip(db: Session, bill: Bill) -> BillPayment:
    """Nothing to pay this time (e.g. a credit carried over): move on to the next bill."""
    if not bill.next_due_date:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "There is no bill waiting to be paid")
    payment = BillPayment(bill_id=bill.id, user_id=bill.user_id, due_date=bill.next_due_date)
    db.add(payment)
    _advance(bill)
    db.flush()
    return payment


def undo(db: Session, bill: Bill, payment: BillPayment) -> None:
    """Undo the latest payment (or skip): that bill is due again. An expense it logged is removed; a linked one stays."""
    latest = db.scalar(
        select(BillPayment).where(BillPayment.bill_id == bill.id).order_by(BillPayment.due_date.desc(), BillPayment.id.desc()).limit(1)
    )
    if not latest or latest.id != payment.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only the latest payment can be undone")
    if payment.made_expense and payment.expense_id:
        expense = db.get(Expense, payment.expense_id)
        if expense:
            db.delete(expense)
    bill.next_due_date = payment.due_date
    bill.is_active = True
    bill.reminded_for = bill.overdue_reminded_for = None
    db.delete(payment)
    db.flush()


def _remind(db: Session, user: User, bill: Bill, kind: str, subject: str, message: str, email: bool) -> None:
    db.add(Notification(user_id=user.id, kind=kind, ref_date=bill.next_due_date, message=message[:500]))
    if email:
        send_email(user.email, subject, f"Hi {user.full_name},\n\n{message}\n")


def send_reminders(db: Session, user: User, today: date | None = None, email: bool = True) -> int:
    """"Due soon" (remind_days_before ahead) and "overdue" (the day after) reminders, each once per bill."""
    today = today or today_local()
    sent = 0
    linked = linked_expense_ids(db, user.id)
    bills = db.scalars(select(Bill).where(Bill.user_id == user.id, Bill.is_active == true(), Bill.next_due_date.is_not(None)))
    for bill in bills:
        due = bill.next_due_date
        about = f" about ₹{bill.amount:,}" if bill.amount else ""
        to = f" to {bill.biller}" if bill.biller and bill.biller.lower() not in bill.name.lower() else ""
        if due < today:
            if bill.overdue_reminded_for == due:
                continue
            match = next(iter(find_matches(db, bill, today, linked)), None)
            if match:
                message = (f"{bill.name} was due on {due:%d %b}. It looks paid: ₹{match.amount:,} on "
                           f"{match.expense_date:%d %b} ({match.item_name}). Confirm it in Bills & Subscriptions → Bills.")
            else:
                message = f"{bill.name}{to} was due on {due:%d %b %Y} and isn't marked paid yet.{about and ' Amount:' + about + '.'}"
            _remind(db, user, bill, KIND_OVERDUE, f"Overdue: {bill.name}", message, email)
            bill.overdue_reminded_for = due
            sent += 1
        elif bill.remind_days_before > 0 and due <= today + timedelta(days=bill.remind_days_before) and bill.reminded_for != due:
            if next(iter(find_matches(db, bill, today, linked)), None):
                bill.reminded_for = due  # already looks paid: no "due soon" nagging
                continue
            days = (due - today).days
            when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
            message = f"{bill.name}{to} is due {when} ({due:%d %b %Y}).{about and ' Usually' + about + '.'}"
            _remind(db, user, bill, KIND_DUE, f"Reminder: {bill.name} is due {when}", message, email)
            bill.reminded_for = due
            sent += 1
    return sent
