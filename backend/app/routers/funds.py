from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import Target, get_current_user, resolve_target
from ..models import ROLE_ADMIN, Fund, IncomeCategory, TransactionAlias, User
from ..schemas import FundIn, FundOut, FundUpdate
from ..services.accounts import resolve_account
from ..services.aliases import label_new, payee_key
from .expenses import like_term
from ..services.backup import KIND_FUND, create_backup, row_to_dict

router = APIRouter(prefix="/api/funds", tags=["funds"])

# Income categories everyone starts with; the user adds their own (Settings → Categories → Income)
INCOME_DEFAULTS = ("Salary", "Bank Interest", "Dividend", "Refunds", "Cashback", "Rental", "Sale", "Reimbursement",
                   "Money received")
FALLBACK = "Money received"


def user_income_categories(db: Session, user_id: int, include_hidden: bool = False) -> list[str]:
    """The income categories a user can pick: the defaults, the ones they added, and every one used in their money
    received or aliases, without the ones they hid."""
    used = db.scalars(select(Fund.category).where(Fund.user_id == user_id, Fund.category.is_not(None)).distinct()).all()
    alias = db.scalars(select(TransactionAlias.income_category).where(TransactionAlias.user_id == user_id,
                                                                       TransactionAlias.income_category.is_not(None))).all()
    own = db.execute(select(IncomeCategory.name, IncomeCategory.is_hidden).where(IncomeCategory.user_id == user_id)).all()
    names = {c for c in (*used, *alias, *INCOME_DEFAULTS) if c} | {n for n, _ in own}
    hidden = set() if include_hidden else {n.casefold() for n, h in own if h}
    return sorted((n for n in names if n.casefold() not in hidden), key=str.casefold)


def _to_out(fund: Fund, target: Target) -> FundOut:
    out = FundOut.model_validate(fund)
    # As for expenses: the owner (and admin) can change it at any time; viewers never
    out.editable = target.writable
    return out


@router.get("", response_model=list[FundOut])
def list_funds(
    user_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    search: str | None = None,
    account_id: int | None = None,
    category: str | None = None,
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
    if category:
        stmt = stmt.where(Fund.category == category)
    if search:
        term = f"%{like_term(search)}%"
        stmt = stmt.where(or_(func.lower(Fund.source).like(term, escape="\\"), func.lower(Fund.raw_name).like(term, escape="\\"),
                              func.lower(Fund.note).like(term, escape="\\"), func.lower(Fund.category).like(term, escape="\\")))
    return [_to_out(f, target) for f in db.scalars(stmt.order_by(Fund.fund_date.desc(), Fund.id.desc()))]


@router.get("/categories", response_model=list[str])
def categories(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id)
    return user_income_categories(db, target.user.id)


@router.post("", response_model=FundOut, status_code=201)
def add_fund(data: FundIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id, write=True)
    fund = Fund(**data.model_dump(), user_id=target.user.id)
    fund.category = (data.category or "").strip() or None
    fund.account_id = resolve_account(db, target.user.id, data.account_id)
    # A category the user picked stays; without one, the alias's income category, else money received
    label_new(db, fund, set_category=fund.category is None)
    fund.category = fund.category or FALLBACK
    db.add(fund)
    db.commit()
    return _to_out(fund, target)


@router.put("/{fund_id}", response_model=FundOut)
def update_fund(fund_id: int, data: FundUpdate, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fund = db.get(Fund, fund_id)
    if not fund:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fund entry not found")
    target = resolve_target(db, viewer, fund.user_id, write=True)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("source") and changes["source"] != fund.source:
        if fund.alias_id is None:
            fund.payee_key = payee_key(fund.raw_name or changes["source"])
        else:
            # Renamed by hand: it no longer follows the alias (raw_name keeps the original description)
            fund.alias_id = None
    for key, value in changes.items():
        if value is not None or key == "note":
            setattr(fund, key, value.strip() if isinstance(value, str) and key != "note" else value)
    db.commit()
    return _to_out(fund, target)


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
