from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, resolve_target
from ..models import ROLE_ADMIN, Fund, User
from ..schemas import FundIn, FundOut
from ..services.accounts import resolve_account
from ..services.aliases import label_new
from .expenses import like_term
from ..services.backup import KIND_FUND, create_backup, row_to_dict

router = APIRouter(prefix="/api/funds", tags=["funds"])


@router.get("", response_model=list[FundOut])
def list_funds(
    user_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    search: str | None = None,
    account_id: int | None = None,
    viewer: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = resolve_target(db, viewer, user_id)
    stmt = select(Fund).where(Fund.user_id == target.user.id)
    if start:
        stmt = stmt.where(Fund.fund_date >= start)
    if end:
        stmt = stmt.where(Fund.fund_date <= end)
    if account_id:
        stmt = stmt.where(Fund.account_id == account_id)
    if search:
        term = f"%{like_term(search)}%"
        stmt = stmt.where(or_(func.lower(Fund.source).like(term, escape="\\"), func.lower(Fund.raw_name).like(term, escape="\\"),
                              func.lower(Fund.note).like(term, escape="\\")))
    return db.scalars(stmt.order_by(Fund.fund_date.desc(), Fund.id.desc())).all()


@router.post("", response_model=FundOut, status_code=201)
def add_fund(data: FundIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id, write=True)
    fund = Fund(**data.model_dump(), user_id=target.user.id)
    fund.account_id = resolve_account(db, target.user.id, data.account_id)
    label_new(db, fund)
    db.add(fund)
    db.commit()
    return fund


@router.delete("/{fund_id}", status_code=204)
def delete_fund(fund_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fund = db.get(Fund, fund_id)
    if not fund:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fund entry not found")
    target = resolve_target(db, viewer, fund.user_id, write=True)
    if viewer.role == ROLE_ADMIN:
        create_backup(
            db, KIND_FUND, target.user, viewer, {"funds": [row_to_dict(fund)]},
            summary=f"Fund '{fund.source}' {fund.amount} on {fund.fund_date} of @{target.user.username}",
        )
    db.delete(fund)
    db.commit()
