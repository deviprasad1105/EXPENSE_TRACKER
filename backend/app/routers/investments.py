"""Investments page: what the user holds (mutual funds, shares, crypto, gold...) with whatever details are known.

Units, amount invested and current value are optional. Totals add up only the figures that are known, and a gain is
worked out only for holdings with both the amount invested and the current value.
"""

from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, resolve_target
from ..models import Account, Holding, User
from ..schemas import HOLDING_KINDS, HoldingGroup, HoldingIn, HoldingOut, InvestmentsOut
from ..services.accounts import balances
from ..timeutil import today_local

router = APIRouter(prefix="/api/investments", tags=["investments"])

ZERO = Decimal(0)
CENT = Decimal("0.01")


def _pct(gain: Decimal, cost: Decimal) -> float | None:
    return round(float(gain / cost * 100), 2) if cost else None


def holding_out(h: Holding) -> HoldingOut:
    out = HoldingOut.model_validate(h)
    if h.units:
        if h.invested is not None:
            out.avg_cost = (h.invested / h.units).quantize(CENT)
        if h.current_value is not None:
            out.price = (h.current_value / h.units).quantize(CENT)
    if h.invested is not None and h.current_value is not None:
        out.gain = h.current_value - h.invested
        out.gain_percent = _pct(out.gain, h.invested)
    return out


def _totals(rows: list[HoldingOut]) -> dict:
    """Sums of the known figures; the gain only over holdings where both figures are known."""
    invested = sum((r.invested for r in rows if r.invested is not None), ZERO)
    value = sum((r.current_value for r in rows if r.current_value is not None), ZERO)
    both = [r for r in rows if r.gain is not None]
    gain = sum((r.gain for r in both), ZERO) if both else None
    cost = sum((r.invested for r in both), ZERO)
    return {"invested": invested, "current_value": value, "gain": gain, "gain_percent": _pct(gain, cost) if both else None}


def _holding(db: Session, viewer: User, holding_id: int) -> Holding:
    h = db.get(Holding, holding_id)
    if not h:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Investment not found")
    resolve_target(db, viewer, h.user_id, write=True)
    return h


@router.get("", response_model=InvestmentsOut)
def list_investments(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Everything held, biggest first within each kind, plus totals per kind. Sold holdings are listed but not counted."""
    target = resolve_target(db, viewer, user_id)
    uid = target.user.id
    rows = [holding_out(h) for h in db.scalars(select(Holding).where(Holding.user_id == uid))]
    order = {k: i for i, k in enumerate(HOLDING_KINDS)}
    rows.sort(key=lambda r: (not r.is_active, order.get(r.kind, 99), -(r.current_value or r.invested or 0), r.name.lower()))
    held = [r for r in rows if r.is_active]

    by_kind: dict[str, list[HoldingOut]] = defaultdict(list)
    for r in held:
        by_kind[r.kind].append(r)
    groups = [HoldingGroup(kind=k, count=len(v), **_totals(v)) for k, v in by_kind.items()]
    groups.sort(key=lambda g: -g.current_value)

    accounts = list(db.scalars(select(Account).where(Account.user_id == uid, Account.kind == "INVESTMENT",
                                                     Account.is_active.is_(True))))
    bal = balances(db, uid, today_local().replace(day=1)) if accounts else {}
    return InvestmentsOut(
        holdings=rows, groups=groups, **_totals(held), unvalued=sum(1 for r in held if r.current_value is None),
        accounts_balance=sum((bal.get(a.id, {}).get("balance", ZERO) for a in accounts), ZERO) if accounts else None,
        accounts=[a.name for a in accounts],
    )


@router.post("", response_model=HoldingOut, status_code=201)
def add_holding(data: HoldingIn, user_id: int | None = None, viewer: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id, write=True)
    h = Holding(user_id=target.user.id, **data.model_dump())
    db.add(h)
    db.commit()
    return holding_out(h)


@router.put("/{holding_id}", response_model=HoldingOut)
def update_holding(holding_id: int, data: HoldingIn, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    h = _holding(db, viewer, holding_id)
    for key, value in data.model_dump().items():
        setattr(h, key, value)
    db.commit()
    return holding_out(h)


@router.delete("/{holding_id}", status_code=204)
def delete_holding(holding_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_holding(db, viewer, holding_id))
    db.commit()
