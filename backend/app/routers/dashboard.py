from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, resolve_target
from ..models import User
from ..services.insights import build
from ..services.summary import balance, expenses_between, summarize, trend
from ..timeutil import today_local, validate_range

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(
    user_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    granularity: str = Query("daily", pattern="^(daily|weekly|monthly|quarterly|yearly)$"),
    viewer: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = resolve_target(db, viewer, user_id)
    today = today_local()
    start = start or today.replace(day=1)
    end = end or today
    validate_range(start, end)
    since = (target.user.approved_at or target.user.created_at).date()
    period = summarize(expenses_between(db, target.user.id, start, end), start, end, since)
    today_total = next((d["amount"] for d in period["daily"] if d["date"] == today.isoformat()), None)
    if today_total is None:
        today_total = summarize(expenses_between(db, target.user.id, today, today), today, today)["total"]
    return {
        "user": {"id": target.user.id, "username": target.user.username, "full_name": target.user.full_name},
        "access": target.level,
        "writable": target.writable,
        **balance(db, target.user.id),
        "today_total": today_total,
        "period": period,
        "granularity": granularity,
        "trend": trend(db, target.user.id, start, end, granularity),
    }


@router.get("/insights")
def insights(
    user_id: int | None = None,
    month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM; default this month"),
    viewer: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A month (this one by default) compared with the month before and the six before that: expenses, subscriptions,
    split bills, budgets."""
    target = resolve_target(db, viewer, user_id)
    today = today_local()
    chosen = date(int(month[:4]), int(month[5:]), 1) if month else None
    if chosen and chosen > today:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose this month or an earlier one")
    return build(db, target.user.id, today, chosen)
