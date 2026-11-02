import logging
from datetime import date, timedelta

from sqlalchemy import false, or_, select, true
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import ROLE_ADMIN, STATUS_APPROVED, Expense, Notification, User
from ..timeutil import today_local
from .jobs import claim_run
from .mailer import send_email

log = logging.getLogger(__name__)

KIND_MISSED = "MISSED_ENTRY"
CATCH_UP_DAYS = 7


def _has_entries(db: Session, user_id: int, day: date) -> bool:
    return db.scalar(select(Expense.id).where(Expense.user_id == user_id, Expense.expense_date == day).limit(1)) is not None


def _already_notified(db: Session, user_id: int, day: date) -> bool:
    return (
        db.scalar(
            select(Notification.id)
            .where(Notification.user_id == user_id, Notification.kind == KIND_MISSED, Notification.ref_date == day)
            .limit(1)
        )
        is not None
    )


def notify_missed(db: Session, user: User, day: date, email: bool = False) -> bool:
    """Create a 'you did not add expenses' notification for `day` if needed. Returns True if one was created."""
    if _has_entries(db, user.id, day) or _already_notified(db, user.id, day):
        return False
    message = f"You have not added any expenses for {day.strftime('%d %b %Y')}. Please add them."
    db.add(Notification(user_id=user.id, kind=KIND_MISSED, message=message, ref_date=day))
    if user.manager_id:
        db.add(Notification(user_id=user.manager_id, kind="TEAM_MISSED", ref_date=day,
                            message=f"{user.full_name} ({user.username}) has not added expenses for {day.strftime('%d %b %Y')}."))
    if email:
        send_email(user.email, "Expense Tracker reminder", f"Hi {user.full_name},\n\n{message}\n")
    return True


def ensure_missed_day_notifications(db: Session, user: User):
    """Back-fill reminders for recent past days with no entries (covers days the scheduler did not run)."""
    if not (user.feature_expense or user.role == ROLE_ADMIN):
        return
    today = today_local()
    since = (user.approved_at or user.created_at).date()
    created = False
    for offset in range(CATCH_UP_DAYS, 0, -1):
        day = today - timedelta(days=offset)
        if day >= since:
            created |= notify_missed(db, user, day)
    if created:
        db.commit()


def daily_reminder_job():
    """Scheduled every evening: remind every approved user who has not entered today's expenses."""
    db = SessionLocal()
    try:
        today = today_local()
        if not claim_run(db, "daily_reminder", today.isoformat()):
            return
        users = db.scalars(
            select(User).where(
                User.status == STATUS_APPROVED,
                User.is_locked == false(),
                or_(User.feature_expense == true(), User.role == ROLE_ADMIN),
            )
        ).all()
        count = sum(notify_missed(db, u, today, email=True) for u in users)
        db.commit()
        log.info("Daily reminder: notified %d user(s) for %s", count, today)
    finally:
        db.close()
