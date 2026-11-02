from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Expense, Fund
from ..timeutil import today_local


def balance(db: Session, user_id: int) -> dict:
    funds = db.scalar(select(func.coalesce(func.sum(Fund.amount), 0)).where(Fund.user_id == user_id)) or 0
    spent = db.scalar(select(func.coalesce(func.sum(Expense.amount), 0)).where(Expense.user_id == user_id)) or 0
    return {"total_funds": float(funds), "total_expenses": float(spent), "balance": float(Decimal(funds) - Decimal(spent))}


def expenses_between(db: Session, user_id: int, start: date, end: date) -> list[Expense]:
    stmt = (
        select(Expense)
        .where(Expense.user_id == user_id, Expense.expense_date >= start, Expense.expense_date <= end)
        .order_by(Expense.expense_date, Expense.id)
    )
    return list(db.scalars(stmt))


def summarize(expenses: list[Expense], start: date, end: date, since: date | None = None) -> dict:
    """Totals for the period. Days before `since` (e.g. when the account was created) never count as missed."""
    by_day: dict[date, Decimal] = defaultdict(Decimal)
    by_category: dict[str, Decimal] = defaultdict(Decimal)
    by_item: dict[str, dict] = defaultdict(lambda: {"quantity": 0, "amount": Decimal(0), "count": 0})
    total = Decimal(0)
    for e in expenses:
        total += e.amount
        by_day[e.expense_date] += e.amount
        by_category[e.category] += e.amount
        item = by_item[e.item_name]
        item["quantity"] += e.quantity
        item["amount"] += e.amount
        item["count"] += 1

    days = (end - start).days + 1
    daily = []
    d = start
    while d <= end:
        daily.append({"date": d.isoformat(), "amount": float(by_day.get(d, 0))})
        d += timedelta(days=1)

    today = today_local()
    elapsed_end = min(end, today)
    first_day = max(start, since) if since else start
    missed = [
        x["date"] for x in daily if x["amount"] == 0 and first_day <= date.fromisoformat(x["date"]) <= elapsed_end
    ]

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total": float(total),
        "count": len(expenses),
        "days": days,
        "average_per_day": float(total / days) if days else 0,
        "daily": daily,
        "missed_days": missed,
        "by_category": sorted(
            ({"category": k, "amount": float(v)} for k, v in by_category.items()), key=lambda x: -x["amount"]
        ),
        "by_item": sorted(
            (
                {"item": k, "quantity": v["quantity"], "count": v["count"], "amount": float(v["amount"])}
                for k, v in by_item.items()
            ),
            key=lambda x: -x["amount"],
        ),
    }


GRANULARITIES = ("daily", "weekly", "monthly", "quarterly", "yearly")


def bucket(d: date, granularity: str) -> tuple[date, str]:
    """The first day of the period `d` falls in, and a short label for it."""
    if granularity == "weekly":
        start = d - timedelta(days=d.weekday())  # weeks start on Monday
        return start, f"Wk {start:%d %b}"
    if granularity == "monthly":
        return d.replace(day=1), f"{d:%b %Y}"
    if granularity == "quarterly":
        q = (d.month - 1) // 3
        return date(d.year, q * 3 + 1, 1), f"Q{q + 1} {d.year}"
    if granularity == "yearly":
        return date(d.year, 1, 1), str(d.year)
    return d, f"{d:%d %b}"


def trend(db: Session, user_id: int, start: date, end: date, granularity: str) -> list[dict]:
    """Expenses and funds per period between start and end, including empty periods."""
    periods: dict[date, dict] = {}
    d = start
    while d <= end:
        key, label = bucket(d, granularity)
        periods.setdefault(key, {"period": key.isoformat(), "label": label, "expenses": Decimal(0), "funds": Decimal(0)})
        d += timedelta(days=1)

    spent = db.execute(
        select(Expense.expense_date, Expense.amount).where(
            Expense.user_id == user_id, Expense.expense_date >= start, Expense.expense_date <= end
        )
    )
    for day, amount in spent:
        periods[bucket(day, granularity)[0]]["expenses"] += amount
    added = db.execute(
        select(Fund.fund_date, Fund.amount).where(Fund.user_id == user_id, Fund.fund_date >= start, Fund.fund_date <= end)
    )
    for day, amount in added:
        periods[bucket(day, granularity)[0]]["funds"] += amount

    return [{**p, "expenses": float(p["expenses"]), "funds": float(p["funds"])} for p in periods.values()]
