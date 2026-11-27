"""Auto-tracking: the phone app sends bank SMS and bank-app notifications; they become records (see services/autotrack.py).

`router` is for the signed-in user (settings, the review list) and needs the UPI tracker in their plan (main.py).
`device_router` is what the phone uses in the background: it has no sign-in session (those expire), so a token made
for the phone identifies the user. That token can only send messages, and making a new one or unlinking stops it.
"""

import logging
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import FEATURE_UPI, get_current_user, has_feature
from ..models import MSG_BILL, MSG_FAILED, MSG_PENDING, STATUS_APPROVED, BankMessage, User
from ..ratelimit import RateLimiter
from ..schemas import (
    AutoTrackSettingsIn, AutoTrackSettingsOut, BankAcceptIn, BankBulkIn, BankBatchOut, BankMessageIn, BankMessageOut, BankMessagesIn,
    DeviceTokenOut,
)
from ..services import autotrack as svc
from ..timeutil import today_local

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/autotrack", tags=["autotrack"])
device_router = APIRouter(prefix="/api/autotrack/device", tags=["autotrack"])

# A first scan of the inbox sends a few batches of up to 500 at once; after that it's one message at a time
device_limiter = RateLimiter(max_hits=120, per_seconds=60)
_TZ = ZoneInfo(settings.timezone)


def _settings(db: Session, user: User) -> AutoTrackSettingsOut:
    counts = dict(db.execute(
        select(BankMessage.status, func.count()).where(BankMessage.user_id == user.id, BankMessage.status.in_((MSG_PENDING, MSG_FAILED)))
        .group_by(BankMessage.status)).all())
    return AutoTrackSettingsOut(
        enabled=user.autotrack_enabled, mode=user.autotrack_mode, since=user.autotrack_since,
        device_linked=bool(user.autotrack_token_hash), device_hint=user.autotrack_token_hint,
        pending=counts.get(MSG_PENDING, 0), failed=counts.get(MSG_FAILED, 0),
        last_received_at=db.scalar(select(func.max(BankMessage.received_at)).where(BankMessage.user_id == user.id)),
    )


def _received(m: BankMessageIn) -> datetime | None:
    if m.received_ms:
        try:
            return datetime.fromtimestamp(m.received_ms / 1000, _TZ).replace(tzinfo=None, microsecond=0)
        except (OverflowError, OSError, ValueError):
            return None
    if m.received_at:
        return m.received_at.astimezone(_TZ).replace(tzinfo=None) if m.received_at.tzinfo else m.received_at
    return None


def _record_batch(db: Session, user: User, messages: list[BankMessageIn]) -> BankBatchOut:
    """Record each message on its own: one that fails (a message nobody foresaw, a database hiccup) is kept as
    "couldn't read" instead of failing the whole batch, which the phone would otherwise send again and again."""
    results, counts = [], Counter()
    # Oldest first, so "the same payment as an earlier message" points the right way
    for m in sorted(messages, key=lambda m: _received(m) or datetime.max):
        if not m.body.strip():
            counts["EMPTY"] += 1
            continue
        try:
            with db.begin_nested():
                msg, new = svc.record_message(db, user, m.sender, m.body, _received(m), m.source)
        except Exception:  # noqa: BLE001 - any one message must not stop the others
            log.exception("Auto-tracking: couldn't record a message for user %s", user.id)
            msg, new = svc.record_failed(db, user, m.sender, m.body, _received(m), m.source), True
        counts[msg.status if new else "SEEN"] += 1
        if new:
            results.append(msg)
    db.commit()
    return BankBatchOut(results=[BankMessageOut.model_validate(r) for r in results], counts=dict(counts))


def _load(db: Session, user: User, message_id: int) -> BankMessage:
    msg = db.get(BankMessage, message_id)
    if not msg or msg.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    return msg


# The tracker is personal: everything here acts on the signed-in user only
@router.get("/settings", response_model=AutoTrackSettingsOut)
def get_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _settings(db, user)


@router.put("/settings", response_model=AutoTrackSettingsOut)
def update_settings(data: AutoTrackSettingsIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if data.since and data.since > today_local():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The start date can't be in the future")
    user.autotrack_enabled, user.autotrack_mode = data.enabled, data.mode
    user.autotrack_since = data.since or user.autotrack_since or svc.default_since(db, user.id)
    db.commit()
    return _settings(db, user)


@router.post("/device-token", response_model=DeviceTokenOut)
def create_device_token(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Link this phone: a new token for it (any phone linked before stops sending). Shown only this once."""
    token = svc.new_device_token(user)
    db.commit()
    return DeviceTokenOut(token=token)


@router.delete("/device-token", response_model=AutoTrackSettingsOut)
def delete_device_token(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.autotrack_token_hash = user.autotrack_token_hint = None
    db.commit()
    return _settings(db, user)


@router.post("/messages", response_model=BankBatchOut, status_code=201)
def add_messages(data: BankMessagesIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Messages pasted on the page, or a scan of the phone's inbox run from the app while it's open."""
    return _record_batch(db, user, data.messages)


@router.get("/messages", response_model=list[BankMessageOut])
def list_messages(status_filter: str | None = None, limit: int = 200, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    stmt = select(BankMessage).where(BankMessage.user_id == user.id)
    if status_filter:
        stmt = stmt.where(BankMessage.status.in_(status_filter.upper().split(",")))
    stmt = stmt.order_by(BankMessage.received_at.desc(), BankMessage.id.desc()).limit(max(1, min(limit, 1000)))
    return list(db.scalars(stmt))


@router.get("/bills", response_model=list[BankMessageOut])
def list_bills(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Bills that are due (from card statements and bill messages), soonest first; past ones for a week."""
    since = today_local() - timedelta(days=7)
    rows = db.scalars(select(BankMessage).where(BankMessage.user_id == user.id, BankMessage.status == MSG_BILL)).all()
    rows = [r for r in rows if not r.due_date or r.due_date >= since]
    return sorted(rows, key=lambda r: (r.due_date is None, r.due_date or today_local()))


@router.post("/messages/{message_id}/accept", response_model=BankMessageOut)
def accept_message(message_id: int, data: BankAcceptIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    msg = _load(db, user, message_id)
    svc.accept(db, user, msg, data.model_dump(exclude_unset=True))
    db.commit()
    return msg


@router.post("/messages/accept-all")
def accept_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Record every waiting message whose details are complete and that doesn't look already recorded."""
    done, skipped = 0, 0
    for msg in db.scalars(select(BankMessage).where(BankMessage.user_id == user.id, BankMessage.status == MSG_PENDING)
                          .order_by(BankMessage.received_at)):
        if not svc.ready_to_accept(msg):
            skipped += 1
            continue
        try:
            svc.create_record(db, user, msg)
            done += 1
        except HTTPException as e:
            msg.reason = str(e.detail)
            skipped += 1
    db.commit()
    return {"recorded": done, "left": skipped}


@router.post("/messages/bulk")
def bulk_messages(data: BankBulkIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Record, skip or remove many messages at once. Returns how many were done and why any were left."""
    result = svc.bulk(db, user, data.ids, data.action, data.model_dump(include={"account_id", "category", "suggestion"}))
    db.commit()
    return result


@router.get("/gaps")
def balance_gaps(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Money that moved with no bank message, found from the balance the bank's messages show (last 90 days)."""
    return svc.balance_gaps(db, user)


@router.post("/gaps/{key}/review", response_model=BankMessageOut)
def review_gap(key: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    msg = svc.review_gap(db, user, key[:80])
    db.commit()
    return msg


@router.post("/messages/{message_id}/dismiss", response_model=BankMessageOut)
def dismiss_message(message_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    msg = _load(db, user, message_id)
    svc.dismiss(msg)
    db.commit()
    return msg


@router.delete("/messages/{message_id}", status_code=204)
def delete_message(message_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Remove a message from the list (anything recorded from it stays: delete that on its own page)."""
    db.delete(_load(db, user, message_id))
    db.commit()


# ---------- the phone, in the background ----------
def _device_user(db: Session, token: str | None) -> User:
    user = svc.user_for_device_token(db, token)
    if not user or user.status != STATUS_APPROVED or user.is_locked or user.org_locked:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This phone is not linked any more. Open the app to link it again.")
    if not has_feature(user, FEATURE_UPI):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Auto-tracking is not part of your plan")
    if not device_limiter.hit(f"user:{user.id}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many messages; try again in a minute")
    return user


@device_router.get("/ping")
def ping(x_device_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    user = _device_user(db, x_device_token)
    return {"username": user.username, "enabled": user.autotrack_enabled}


@device_router.post("/messages", response_model=BankBatchOut)
def device_messages(data: BankMessagesIn, x_device_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    """New bank messages from the phone. Answers 200 with what became of each, so the phone doesn't send them again."""
    return _record_batch(db, _device_user(db, x_device_token), data.messages)
