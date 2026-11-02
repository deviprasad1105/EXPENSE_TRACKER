from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import Target, get_current_user, resolve_target
from ..models import ROLE_ADMIN, Subscription, SubscriptionCharge, User
from ..schemas import (
    SubscriptionBulkIn,
    SubscriptionChargeOut,
    SubscriptionIn,
    SubscriptionOut,
    SubscriptionSmsIn,
    SubscriptionSuggestion,
    SubscriptionToggle,
)
from ..services.backup import KIND_SUBSCRIPTION, create_backup, row_to_dict
from ..services.subscription_import import suggestions_from_expenses, suggestions_from_sms
from ..services.subscriptions import monthly_cost, process_user, refresh_next_charge
from ..timeutil import now_local, today_local

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])

# Changing any of these moves the schedule, so the next charge date is recalculated
SCHEDULE_FIELDS = ("frequency", "interval_count", "interval_unit", "start_date", "end_date")


def _totals(db: Session, ids: list[int]) -> dict[int, tuple[Decimal, int]]:
    if not ids:
        return {}
    rows = db.execute(
        select(SubscriptionCharge.subscription_id, func.sum(SubscriptionCharge.amount), func.count(SubscriptionCharge.id))
        .where(SubscriptionCharge.subscription_id.in_(ids))
        .group_by(SubscriptionCharge.subscription_id)
    ).all()
    return {sid: (Decimal(total or 0), count) for sid, total, count in rows}


def _to_out(sub: Subscription, totals: dict[int, tuple[Decimal, int]]) -> SubscriptionOut:
    out = SubscriptionOut.model_validate(sub)
    out.monthly_cost = monthly_cost(sub)
    out.days_until = (sub.next_charge_date - today_local()).days if sub.next_charge_date else None
    out.total_charged, out.charge_count = totals.get(sub.id, (Decimal(0), 0))
    return out


def _check(data: SubscriptionIn):
    if data.end_date and data.end_date < data.start_date:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The end date is before the start date")


def _load(db: Session, viewer: User, sub_id: int, write: bool = False) -> tuple[Subscription, Target]:
    sub = db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")
    return sub, resolve_target(db, viewer, sub.user_id, write=write)


def _set_active(sub: Subscription, active: bool):
    if sub.is_active == active:
        return
    sub.is_active = active
    sub.paused_at = None if active else now_local()
    sub.updated_at = now_local()
    if active:
        # Charges that fell while it was paused are skipped, not recorded afterwards
        refresh_next_charge(sub)


def _create(db: Session, target: Target, data: SubscriptionIn) -> Subscription:
    _check(data)
    sub = Subscription(**data.model_dump(), user_id=target.user.id)
    if not data.is_active:
        sub.paused_at = now_local()
    refresh_next_charge(sub)
    db.add(sub)
    return sub


@router.get("", response_model=list[SubscriptionOut])
def list_subscriptions(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id)
    subs = db.scalars(
        select(Subscription).where(Subscription.user_id == target.user.id).order_by(Subscription.is_active.desc(), Subscription.next_charge_date, Subscription.name)
    ).all()
    totals = _totals(db, [s.id for s in subs])
    return [_to_out(s, totals) for s in subs]


@router.get("/summary")
def summary(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Monthly and yearly cost of active subscriptions, what renews in the next 30 days, and cost by payment app."""
    target = resolve_target(db, viewer, user_id)
    subs = db.scalars(select(Subscription).where(Subscription.user_id == target.user.id)).all()
    active = [s for s in subs if s.is_active]
    today = today_local()
    monthly = sum((monthly_cost(s) for s in active), Decimal(0))
    upcoming = sorted(
        (s for s in active if s.next_charge_date and s.next_charge_date <= today + timedelta(days=30)),
        key=lambda s: s.next_charge_date,
    )
    by_method: dict[str, Decimal] = defaultdict(Decimal)
    for s in active:
        by_method[s.payment_method or "OTHER"] += monthly_cost(s)
    paused = [s for s in subs if not s.is_active]
    return {
        "active_count": len(active),
        "paused_count": len(paused),
        "monthly_total": float(monthly),
        "yearly_total": float(monthly * 12),
        # What pausing currently saves per month
        "paused_monthly": float(sum((monthly_cost(s) for s in paused if not (s.end_date and s.end_date < today)), Decimal(0))),
        "due_next_7_days": float(sum((s.amount for s in upcoming if s.next_charge_date <= today + timedelta(days=7)), Decimal(0))),
        "upcoming": [
            {"id": s.id, "name": s.name, "amount": float(s.amount), "date": s.next_charge_date,
             "days_until": (s.next_charge_date - today).days, "payment_method": s.payment_method}
            for s in upcoming
        ],
        "by_payment_method": [{"method": k, "monthly": float(v)} for k, v in sorted(by_method.items(), key=lambda kv: -kv[1])],
    }


@router.post("", response_model=SubscriptionOut, status_code=201)
def add_subscription(
    data: SubscriptionIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    target = resolve_target(db, viewer, user_id, write=True)
    sub = _create(db, target, data)
    db.flush()
    # A charge due today is recorded straight away
    process_user(db, target.user, email=False)
    db.commit()
    return _to_out(sub, _totals(db, [sub.id]))


@router.post("/bulk", response_model=list[SubscriptionOut], status_code=201)
def add_many(
    data: SubscriptionBulkIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Add several at once, e.g. the suggestions picked from an SMS import."""
    target = resolve_target(db, viewer, user_id, write=True)
    subs = [_create(db, target, item) for item in data.items]
    db.flush()
    process_user(db, target.user, email=False)
    db.commit()
    totals = _totals(db, [s.id for s in subs])
    return [_to_out(s, totals) for s in subs]


@router.put("/{sub_id}", response_model=SubscriptionOut)
def update_subscription(sub_id: int, data: SubscriptionIn, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sub, target = _load(db, viewer, sub_id, write=True)
    _check(data)
    values = data.model_dump(exclude={"source", "is_active"})
    schedule_changed = any(values[f] != getattr(sub, f) for f in SCHEDULE_FIELDS)
    for key, value in values.items():
        setattr(sub, key, value)
    sub.updated_at = now_local()
    if schedule_changed:
        refresh_next_charge(sub)
    _set_active(sub, data.is_active)
    db.flush()
    process_user(db, target.user, email=False)
    db.commit()
    return _to_out(sub, _totals(db, [sub.id]))


@router.post("/toggle", response_model=list[SubscriptionOut])
def toggle(data: SubscriptionToggle, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Pause or resume one or many subscriptions at once. Paused ones get no reminders and log no charges."""
    subs = db.scalars(select(Subscription).where(Subscription.id.in_(data.ids))).all()
    if len(subs) != len(set(data.ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")
    for owner_id in {s.user_id for s in subs}:
        resolve_target(db, viewer, owner_id, write=True)
    for sub in subs:
        _set_active(sub, data.is_active)
    db.commit()
    totals = _totals(db, [s.id for s in subs])
    return [_to_out(s, totals) for s in subs]


@router.delete("/{sub_id}", status_code=204)
def delete_subscription(sub_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Charges already logged as expenses stay in the expense list."""
    sub, target = _load(db, viewer, sub_id, write=True)
    if viewer.role == ROLE_ADMIN:
        create_backup(
            db, KIND_SUBSCRIPTION, target.user, viewer,
            {"subscriptions": [row_to_dict(sub)], "subscription_charges": [row_to_dict(c) for c in sub.charges]},
            summary=f"Subscription '{sub.name}' {sub.amount} {sub.frequency.lower()} of @{target.user.username}",
        )
    db.delete(sub)
    db.commit()


@router.get("/{sub_id}/charges", response_model=list[SubscriptionChargeOut])
def charges(sub_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sub, _ = _load(db, viewer, sub_id)
    return sub.charges


# ---------- finding subscriptions ----------
@router.post("/import/sms", response_model=list[SubscriptionSuggestion])
def import_from_sms(
    data: SubscriptionSmsIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Suggestions from pasted bank / UPI messages (AutoPay mandates, repeated debits). Nothing is saved."""
    target = resolve_target(db, viewer, user_id, write=True)
    return suggestions_from_sms(db, target.user.id, data.text, today_local())


@router.get("/import/detect", response_model=list[SubscriptionSuggestion])
def detect_from_expenses(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Suggestions from the expense history: the same item paid at a regular interval. Nothing is saved."""
    target = resolve_target(db, viewer, user_id, write=True)
    return suggestions_from_expenses(db, target.user.id, today_local())
