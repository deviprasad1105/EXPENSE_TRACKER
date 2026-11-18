"""Investments page: what the user holds (mutual funds, shares, crypto, gold...) with whatever details are known.

Units, amount invested and current value are optional. Totals add up only the figures that are known, and a gain is
worked out only for holdings with both the amount invested and the current value.

A holding can be part of another (`parent_id`), e.g. each stock under its broker's total line. Those are listed under
their parent but left out of every total, since the parent's figures already include them.
"""

from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
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
    # Lines that are part of another are in their parent's figures already
    held = [r for r in rows if r.is_active and r.parent_id is None]

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


def _check_parent(db: Session, user_id: int, parent_id: int | None, holding: Holding | None = None) -> None:
    """The parent must be the same user's top-level line, and a line with lines under it can't go under another."""
    if parent_id is None:
        return
    parent = db.get(Holding, parent_id)
    if not parent or parent.user_id != user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The investment it is part of wasn't found")
    if parent.parent_id is not None or (holding and parent.id == holding.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "It can only be part of a top-level investment")
    if holding and db.scalar(select(Holding.id).where(Holding.parent_id == holding.id).limit(1)) is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This investment has others under it, so it can't go under another")


@router.post("", response_model=HoldingOut, status_code=201)
def add_holding(data: HoldingIn, user_id: int | None = None, viewer: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id, write=True)
    _check_parent(db, target.user.id, data.parent_id)
    h = Holding(user_id=target.user.id, **data.model_dump())
    db.add(h)
    db.commit()
    return holding_out(h)


@router.put("/{holding_id}", response_model=HoldingOut)
def update_holding(holding_id: int, data: HoldingIn, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    h = _holding(db, viewer, holding_id)
    _check_parent(db, h.user_id, data.parent_id, h)
    for key, value in data.model_dump().items():
        setattr(h, key, value)
    db.commit()
    return holding_out(h)


@router.delete("/{holding_id}", status_code=204)
def delete_holding(holding_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Deletes the lines under it too (they are part of it)."""
    h = _holding(db, viewer, holding_id)
    db.execute(delete(Holding).where(Holding.parent_id == h.id))
    db.delete(h)
    db.commit()
