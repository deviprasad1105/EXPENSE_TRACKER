"""Subscription schedules: when each charge falls, recording charges as they come due, and reminders."""

import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import false, or_, select, true
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import (
    FREQ_CUSTOM,
    FREQ_HALF_YEARLY,
    FREQ_MONTHLY,
    FREQ_QUARTERLY,
    FREQ_WEEKLY,
    FREQ_YEARLY,
    ROLE_ADMIN,
    STATUS_APPROVED,
    Expense,
    Notification,
    Subscription,
    SubscriptionCharge,
    User,
)
from ..timeutil import now_local, today_local
from .accounts import primary_account_id
from .budgets import check_budgets
from .jobs import claim_run
from .mailer import send_email

log = logging.getLogger(__name__)

KIND_DUE = "SUBSCRIPTION_DUE"
KIND_CHARGED = "SUBSCRIPTION_CHARGED"
KIND_ENDED = "SUBSCRIPTION_ENDED"

# (count, unit) of each fixed frequency
STEP = {
    FREQ_WEEKLY: (1, "WEEK"),
    FREQ_MONTHLY: (1, "MONTH"),
    FREQ_QUARTERLY: (3, "MONTH"),
    FREQ_HALF_YEARLY: (6, "MONTH"),
    FREQ_YEARLY: (1, "YEAR"),
}
# Average days per unit, for monthly-equivalent costs
UNIT_DAYS = {"DAY": 1, "WEEK": 7, "MONTH": 365.25 / 12, "YEAR": 365.25}
# Never record more than this many missed charges at once (e.g. a weekly subscription after months of downtime)
MAX_CATCH_UP = 60


def step_of(sub: Subscription) -> tuple[int, str]:
    if sub.frequency == FREQ_CUSTOM:
        return max(1, sub.interval_count), sub.interval_unit
    return STEP[sub.frequency]


def add_months(d: date, months: int, anchor_day: int) -> date:
    """`d` moved by `months`, on `anchor_day` or the last day of a shorter month (31 Jan + 1 month = 28/29 Feb)."""
    total = d.year * 12 + d.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(anchor_day, calendar.monthrange(year, month)[1]))


def nth_charge(start: date, count: int, unit: str, n: int) -> date:
    """Date of the n-th charge (n = 0 is `start`). Computed from the start, so month-end days don't drift."""
    if unit == "DAY":
        return start + timedelta(days=count * n)
    if unit == "WEEK":
        return start + timedelta(weeks=count * n)
    months = count * n * (12 if unit == "YEAR" else 1)
    return add_months(start, months, start.day)


def next_charge_on_or_after(sub: Subscription, day: date) -> date | None:
    """First charge on or after `day`, or None if the subscription has ended by then."""
    count, unit = step_of(sub)
    if day <= sub.start_date:
        n = 0
    else:
        # Jump close to `day`, then step forward
        approx_days = count * UNIT_DAYS[unit]
        n = max(0, int((day - sub.start_date).days // approx_days) - 1)
    while (d := nth_charge(sub.start_date, count, unit, n)) < day:
        n += 1
    if sub.end_date and d > sub.end_date:
        return None
    return d


def charge_after(sub: Subscription, charged_on: date) -> date | None:
    return next_charge_on_or_after(sub, charged_on + timedelta(days=1))


def monthly_cost(sub: Subscription) -> Decimal:
    """What the subscription costs per month on average (a yearly ₹1200 plan is ₹100 a month)."""
    count, unit = step_of(sub)
    months = Decimal(str(count * UNIT_DAYS[unit])) / Decimal(str(UNIT_DAYS["MONTH"]))
    return (Decimal(sub.amount) / months).quantize(Decimal("0.01"))


def refresh_next_charge(sub: Subscription, today: date | None = None):
    """Set next_charge_date from the schedule: the first charge from today on (past charges are not back-filled)."""
    sub.next_charge_date = next_charge_on_or_after(sub, today or today_local())
    sub.reminded_for = None


def _has_expense_feature(user: User) -> bool:
    return user.role == ROLE_ADMIN or user.feature_expense


def record_due_charges(db: Session, sub: Subscription, user: User, today: date) -> int:
    """Record every charge that has come due (up to today) and move next_charge_date on. Returns how many."""
    recorded = 0
    while sub.next_charge_date and sub.next_charge_date <= today and recorded < MAX_CATCH_UP:
        day = sub.next_charge_date
        exists = db.scalar(
            select(SubscriptionCharge.id).where(SubscriptionCharge.subscription_id == sub.id, SubscriptionCharge.charge_date == day)
        )
        if not exists:
            charge = SubscriptionCharge(subscription_id=sub.id, charge_date=day, amount=sub.amount)
            if sub.auto_log_expense and _has_expense_feature(user):
                expense = Expense(
                    user_id=user.id,
                    expense_date=day,
                    item_name=sub.name[:100],
                    category=sub.category,
                    quantity=1,
                    amount=sub.amount,
                    note=f"Subscription charge{f' ({sub.payment_method})' if sub.payment_method else ''}",
                    is_extra=True,
                    account_id=primary_account_id(db, user.id),
                )
                db.add(expense)
                db.flush()
                charge.expense_id = expense.id
            db.add(charge)
            recorded += 1
        sub.next_charge_date = charge_after(sub, day)
    if recorded:
        last = " Logged as an expense." if sub.auto_log_expense and _has_expense_feature(user) else ""
        db.add(Notification(user_id=user.id, kind=KIND_CHARGED, ref_date=today,
                            message=f"{sub.name}: ₹{sub.amount} charged.{last}"))
    if sub.next_charge_date is None and sub.end_date and sub.end_date <= today:
        sub.is_active = False
        db.add(Notification(user_id=user.id, kind=KIND_ENDED, ref_date=today,
                            message=f"{sub.name} has ended (end date {sub.end_date:%d %b %Y}). It is now paused."))
    return recorded


def send_due_reminder(db: Session, sub: Subscription, user: User, today: date, email: bool = True) -> bool:
    """Remind once per charge, `remind_days_before` days ahead. Returns True if a reminder was sent."""
    due = sub.next_charge_date
    if not due or sub.remind_days_before <= 0 or sub.reminded_for == due:
        return False
    if not (today <= due <= today + timedelta(days=sub.remind_days_before)):
        return False
    days = (due - today).days
    when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
    last = " This is the last charge before it ends." if sub.end_date and charge_after(sub, due) is None else ""
    via = f" via {sub.payment_method}" if sub.payment_method else ""
    message = f"{sub.name} renews {when} ({due:%d %b %Y}): ₹{sub.amount}{via}.{last} Pause it in Subscriptions if you don't want it."
    db.add(Notification(user_id=user.id, kind=KIND_DUE, ref_date=due, message=message[:500]))
    if email:
        send_email(user.email, f"Reminder: {sub.name} renews {when}", f"Hi {user.full_name},\n\n{message}\n")
    sub.reminded_for = due
    return True


def process_user(db: Session, user: User, today: date | None = None, email: bool = True) -> tuple[int, int]:
    """Record due charges and send reminders for one user's active subscriptions. Returns (charges, reminders)."""
    today = today or today_local()
    charges = reminders = 0
    subs = db.scalars(select(Subscription).where(Subscription.user_id == user.id, Subscription.is_active == true()))
    for sub in subs:
        charges += record_due_charges(db, sub, user, today)
        if sub.is_active:
            reminders += send_due_reminder(db, sub, user, today, email=email)
    if charges:
        # Charges logged as expenses count towards budgets
        db.flush()
        check_budgets(db, user, today, today, email=email)
    return charges, reminders


def subscription_job():
    """Hourly: record charges that came due and send "renews soon" reminders. Safe to run any number of times."""
    db = SessionLocal()
    try:
        now = now_local()
        if not claim_run(db, "subscriptions", f"{now:%Y-%m-%dT%H}"):
            return
        today = now.date()
        users = db.scalars(
            select(User).where(
                User.status == STATUS_APPROVED,
                User.is_locked == false(),
                or_(User.feature_subscription == true(), User.role == ROLE_ADMIN),
                User.id.in_(select(Subscription.user_id).where(Subscription.is_active == true())),
            )
        ).all()
        charges = reminders = 0
        for user in users:
            try:
                c, r = process_user(db, user, today)
                db.commit()
                charges, reminders = charges + c, reminders + r
            except Exception:
                db.rollback()
                log.exception("Subscriptions: processing @%s failed", user.username)
        if charges or reminders:
            log.info("Subscriptions: recorded %d charge(s), sent %d reminder(s)", charges, reminders)
    finally:
        db.close()
