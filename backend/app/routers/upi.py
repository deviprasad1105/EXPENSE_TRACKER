"""UPI tracker: the user's UPI IDs, their on / off switch, the private forwarding link, and what was recorded.

`router` needs the UPI tracker in the user's plan (added in main.py). `hook_router` is the forwarding link itself:
it has no sign-in (an SMS-forwarding app can't sign in), so the secret token in the URL identifies the user.
"""

import json
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import STATUS_APPROVED, UPI_CREATED, Account, Expense, Fund, UpiEvent, UpiId, User
from ..ratelimit import RateLimiter
from ..schemas import (
    UpiActivityOut, UpiDay, UpiEventOut, UpiIdIn, UpiIdOut, UpiMessageIn, UpiSettingsIn, UpiSettingsOut, UpiTokenOut,
)
from ..services import upi as svc
from ..timeutil import today_local

router = APIRouter(prefix="/api/upi", tags=["upi"])
hook_router = APIRouter(prefix="/api/upi", tags=["upi"])

HOOK_PATH = "/api/upi/hook/"
hook_limiter = RateLimiter(max_hits=30, per_seconds=60)


def _settings(db: Session, user: User) -> UpiSettingsOut:
    ids = db.scalars(select(UpiId).where(UpiId.user_id == user.id).order_by(UpiId.id)).all()
    return UpiSettingsOut(enabled=user.upi_enabled, has_token=bool(user.upi_token_hash), token_hint=user.upi_token_hint,
                          upi_ids=[UpiIdOut.model_validate(u) for u in ids])


def _check_account(db: Session, user: User, account_id: int | None):
    if account_id is not None:
        a = db.get(Account, account_id)
        if not a or a.user_id != user.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid account")


def _load_id(db: Session, user: User, upi_id: int) -> UpiId:
    u = db.get(UpiId, upi_id)
    if not u or u.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "UPI ID not found")
    return u


# The tracker is personal: these act on the signed-in user only, never on someone whose data they can view
@router.get("/settings", response_model=UpiSettingsOut)
def get_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _settings(db, user)


@router.patch("/settings", response_model=UpiSettingsOut)
def update_settings(data: UpiSettingsIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if data.enabled and not db.scalar(select(UpiId.id).where(UpiId.user_id == user.id, UpiId.is_active.is_(True))):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add your UPI ID first")
    user.upi_enabled = data.enabled
    db.commit()
    return _settings(db, user)


@router.post("/token", response_model=UpiTokenOut)
def create_token(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A new private forwarding link. The old one stops working. The token is shown only this once."""
    token = svc.new_token(user)
    db.commit()
    return UpiTokenOut(token=token, hook_path=HOOK_PATH + token)


@router.delete("/token", response_model=UpiSettingsOut)
def delete_token(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.upi_token_hash = user.upi_token_hint = None
    db.commit()
    return _settings(db, user)


@router.post("/ids", response_model=UpiIdOut, status_code=201)
def add_upi_id(data: UpiIdIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vpa = data.vpa.lower()
    if db.scalar(select(UpiId.id).where(UpiId.user_id == user.id, UpiId.vpa == vpa)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{vpa} is already added")
    _check_account(db, user, data.account_id)
    u = UpiId(user_id=user.id, vpa=vpa, account_id=data.account_id, is_active=data.is_active)
    db.add(u)
    db.commit()
    return u


@router.put("/ids/{upi_id}", response_model=UpiIdOut)
def update_upi_id(upi_id: int, data: UpiIdIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    u = _load_id(db, user, upi_id)
    vpa = data.vpa.lower()
    if db.scalar(select(UpiId.id).where(UpiId.user_id == user.id, UpiId.vpa == vpa, UpiId.id != u.id)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{vpa} is already added")
    _check_account(db, user, data.account_id)
    u.vpa, u.account_id, u.is_active = vpa, data.account_id, data.is_active
    db.commit()
    return u


@router.delete("/ids/{upi_id}", status_code=204)
def delete_upi_id(upi_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    u = _load_id(db, user, upi_id)
    db.delete(u)
    db.flush()
    # Tracking needs at least one UPI ID
    if not db.scalar(select(UpiId.id).where(UpiId.user_id == user.id, UpiId.is_active.is_(True))):
        user.upi_enabled = False
    db.commit()


def _event_out(db: Session, ev: UpiEvent) -> UpiEventOut:
    out = UpiEventOut.model_validate(ev)
    row = db.get(Expense, ev.expense_id) if ev.expense_id else db.get(Fund, ev.fund_id) if ev.fund_id else None
    if row is not None:
        out.name = row.item_name if isinstance(row, Expense) else row.source
        out.alias_id = row.alias_id
    return out


@router.get("/activity", response_model=UpiActivityOut)
def activity(days: int = 30, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Messages received in the last `days` days, and per day what was paid and received through UPI."""
    days = max(1, min(days, 366))
    since = today_local() - timedelta(days=days - 1)
    events = db.scalars(
        select(UpiEvent).where(UpiEvent.user_id == user.id, UpiEvent.received_at >= since)
        .order_by(UpiEvent.received_at.desc(), UpiEvent.id.desc()).limit(1000)
    ).all()
    out = [_event_out(db, ev) for ev in events]
    per_day: dict = defaultdict(lambda: {"paid": Decimal(0), "received": Decimal(0), "count": 0})
    for ev in events:
        # Only payments still on the books count (an expense deleted since is no longer spending)
        if ev.status != UPI_CREATED or not (ev.expense_id or ev.fund_id) or not ev.txn_date:
            continue
        d = per_day[ev.txn_date]
        d["paid" if ev.direction == svc.DEBIT else "received"] += ev.amount or 0
        d["count"] += 1
    day_rows = [UpiDay(date=k, **v) for k, v in sorted(per_day.items(), reverse=True)]
    return UpiActivityOut(events=out, days=day_rows)


@router.post("/messages", response_model=UpiEventOut, status_code=201)
def add_message(data: UpiMessageIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A message pasted on the tracker page, recorded exactly as a forwarded one would be."""
    ev = svc.record_message(db, user, data.text)
    db.commit()
    return _event_out(db, ev)


@router.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Remove a message from the list (a recorded expense or money received stays: delete that on its own page)."""
    ev = db.get(UpiEvent, event_id)
    if not ev or ev.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    db.delete(ev)
    db.commit()


async def _message_text(request: Request) -> str:
    """The message from whatever the forwarding app sends: JSON ({"text"|"message"|"body"|"msg"|"sms": …}), a form, or plain text."""
    raw = (await request.body())[:8000].decode("utf-8", errors="replace")
    ctype = request.headers.get("content-type", "")
    if "json" in ctype or raw.lstrip().startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError:
            return raw
        if isinstance(data, dict):
            for k in ("text", "message", "body", "msg", "sms", "content"):
                if isinstance(data.get(k), str):
                    return data[k]
            return " ".join(str(v) for v in data.values() if isinstance(v, str))
        return str(data)
    if "form" in ctype:
        form = await request.form()
        for k in ("text", "message", "body", "msg", "sms", "content"):
            if form.get(k):
                return str(form.get(k))
    return raw


@hook_router.post("/hook/{token}")
async def hook(token: str, request: Request, db: Session = Depends(get_db)):
    """The private forwarding link. Answers 200 with what happened, so forwarding apps don't keep retrying."""
    user = svc.user_for_token(db, token)
    if not user or user.status != STATUS_APPROVED or user.is_locked or user.org_locked:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown link")
    if not hook_limiter.hit(f"user:{user.id}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many messages; try again in a minute")
    text = (await _message_text(request)).strip()[:2000]
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send the message text")
    ev = svc.record_message(db, user, text)
    db.commit()
    return {"status": ev.status, "reason": ev.reason, "amount": ev.amount and float(ev.amount), "direction": ev.direction}
