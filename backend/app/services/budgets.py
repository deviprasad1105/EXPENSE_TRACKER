"""Monthly budgets: how much of each limit is used, and alerts when spending reaches it."""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from ..models import BUDGET_CATEGORY, BUDGET_ITEM, Budget, Expense, Notification, User
from .mailer import send_email

KIND_BUDGET = "BUDGET_ALERT"
LEVELS = ("OK", "WARN", "OVER")


def month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return start, end


def spent_for(db: Session, budget: Budget, day: date) -> Decimal:
    start, end = month_bounds(day)
    stmt = select(func.coalesce(func.sum(Expense.amount), 0)).where(
        Expense.user_id == budget.user_id, Expense.expense_date >= start, Expense.expense_date < end
    )
    if budget.scope == BUDGET_CATEGORY:
        stmt = stmt.where(func.lower(Expense.category) == budget.target.lower())
    elif budget.scope == BUDGET_ITEM:
        stmt = stmt.where(func.lower(Expense.item_name) == budget.target.lower())
    return Decimal(db.scalar(stmt) or 0)


def level_of(spent: Decimal, budget: Budget) -> tuple[float, str]:
    percent = float(spent / budget.monthly_limit * 100) if budget.monthly_limit else 0.0
    if percent >= 100:
        return percent, "OVER"
    if percent >= budget.alert_percent:
        return percent, "WARN"
    return percent, "OK"


def label(budget: Budget) -> str:
    return "Overall spending" if budget.target == "*" else budget.target


def status_of(db: Session, budget: Budget, day: date) -> dict:
    spent = spent_for(db, budget, day)
    percent, level = level_of(spent, budget)
    return {"spent": spent, "percent": round(percent, 1), "level": level}


def check_budgets(db: Session, user: User, day: date, today: date, email: bool = True) -> int:
    """Notify once per level per month when spending in the current month reaches a budget's alert or limit.

    Only the current month is checked: back-dating an expense into an older month doesn't send alerts.
    """
    if (day.year, day.month) != (today.year, today.month):
        return 0
    sent = 0
    month = f"{today:%Y-%m}"
    for budget in db.scalars(select(Budget).where(Budget.user_id == user.id, Budget.is_active == true())):
        spent = spent_for(db, budget, today)
        percent, level = level_of(spent, budget)
        prev_month, _, prev_level = (budget.alerted or "").partition(":")
        already = LEVELS.index(prev_level) if prev_month == month and prev_level in LEVELS else 0
        if LEVELS.index(level) <= already:
            continue
        what = label(budget)
        if level == "OVER":
            msg = f"Budget exceeded: {what} is at ₹{spent:,.2f} of ₹{budget.monthly_limit:,.2f} this month ({percent:.0f}%)."
        else:
            msg = (f"Budget alert: {what} has used {percent:.0f}% of its ₹{budget.monthly_limit:,.2f} limit this month "
                   f"(₹{spent:,.2f} spent, ₹{budget.monthly_limit - spent:,.2f} left).")
        db.add(Notification(user_id=user.id, kind=KIND_BUDGET, ref_date=today, message=msg[:500]))
        if email:
            send_email(user.email, f"{'Over budget' if level == 'OVER' else 'Budget alert'}: {what}", f"Hi {user.full_name},\n\n{msg}\n")
        budget.alerted = f"{month}:{level}"
        sent += 1
    return sent
