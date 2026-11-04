from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, resolve_target
from ..models import User
from ..services.reports import build_excel, build_pdf
from ..services.summary import balance, expenses_between, summarize, trend
from ..timeutil import today_local, validate_range

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/export")
def export(
    format: str = Query("xlsx", pattern="^(xlsx|pdf)$"),
    start: date | None = None,
    end: date | None = None,
    user_id: int | None = None,
    granularity: str = Query("daily", pattern="^(daily|weekly|monthly|quarterly|yearly)$"),
    viewer: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = resolve_target(db, viewer, user_id)
    today = today_local()
    start = start or today.replace(day=1)
    end = end or today
    validate_range(start, end)

    expenses = expenses_between(db, target.user.id, start, end)
    summary = summarize(expenses, start, end, (target.user.approved_at or target.user.created_at).date())
    summary["granularity"] = granularity
    summary["trend"] = trend(db, target.user.id, start, end, granularity)
    bal = balance(db, target.user.id)
    filename = f"expenses_{target.user.username}_{start}_{end}.{format}"

    if format == "xlsx":
        content = build_excel(target.user, expenses, summary, bal)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        content = build_pdf(target.user, expenses, summary, bal)
        media = "application/pdf"
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
