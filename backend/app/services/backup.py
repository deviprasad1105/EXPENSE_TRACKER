"""Backups taken before an admin deletes data, and restoring them.

A backup is a gzip-compressed JSON document: {"version": 1, "tables": {table_name: [row, ...]}, ...}.
Rows keep their original primary keys, so restoring puts everything back exactly as it was,
including the links between rows (expenses → daily items, reporting lines, shares).
"""

import base64
import gzip
import json
from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import Boolean, Date, DateTime, LargeBinary, Numeric, or_, select
from sqlalchemy.orm import Session

from ..models import (
    Account,
    AccountTransfer,
    Budget,
    DailyItem,
    DataBackup,
    DataShare,
    Expense,
    Fund,
    Notification,
    Subscription,
    SubscriptionCharge,
    User,
    UserAvatar,
    UserBackground,
    UsernameChangeRequest,
)
from ..timeutil import now_local

KIND_USER = "USER"
KIND_USER_DATA = "USER_DATA"
KIND_EXPENSE = "EXPENSE"
KIND_FUND = "FUND"
KIND_DAILY_ITEM = "DAILY_ITEM"
KIND_SUBSCRIPTION = "SUBSCRIPTION"

# Restore order matters: parents before children
MODELS = [
    User, UserAvatar, UserBackground, Account, DailyItem, Expense, Fund, AccountTransfer, Budget, Subscription,
    SubscriptionCharge, DataShare, Notification, UsernameChangeRequest,
]
BY_TABLE = {m.__tablename__: m for m in MODELS}


# ---------- (de)serialising rows ----------
def _encode(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return value


def row_to_dict(obj) -> dict:
    return {c.key: _encode(getattr(obj, c.key)) for c in obj.__table__.columns}


def dict_to_row(model, data: dict):
    values = {}
    for c in model.__table__.columns:
        if c.key not in data:
            continue
        v = data[c.key]
        if v is not None:
            if isinstance(c.type, DateTime):
                v = datetime.fromisoformat(v)
            elif isinstance(c.type, Date):
                v = date.fromisoformat(v)
            elif isinstance(c.type, Numeric):
                v = Decimal(v)
            elif isinstance(c.type, LargeBinary):
                v = base64.b64decode(v)
            elif isinstance(c.type, Boolean):
                v = bool(v)
        values[c.key] = v
    return model(**values)


def _pk(model, data: dict):
    cols = model.__table__.primary_key.columns
    return tuple(data[c.key] for c in cols) if len(cols) > 1 else data[next(iter(cols)).key]


# ---------- snapshots ----------
def _rows(db: Session, model, *where) -> list[dict]:
    return [row_to_dict(o) for o in db.scalars(select(model).where(*where))]


def snapshot_user_data(db: Session, user_id: int) -> dict:
    return {
        "accounts": _rows(db, Account, Account.user_id == user_id),
        "account_transfers": _rows(db, AccountTransfer, AccountTransfer.user_id == user_id),
        "budgets": _rows(db, Budget, Budget.user_id == user_id),
        "daily_items": _rows(db, DailyItem, DailyItem.user_id == user_id),
        "expenses": _rows(db, Expense, Expense.user_id == user_id),
        "funds": _rows(db, Fund, Fund.user_id == user_id),
        "subscriptions": _rows(db, Subscription, Subscription.user_id == user_id),
        "subscription_charges": _rows(
            db, SubscriptionCharge,
            SubscriptionCharge.subscription_id.in_(select(Subscription.id).where(Subscription.user_id == user_id)),
        ),
    }


def snapshot_user(db: Session, user: User) -> dict:
    tables = {
        "app_users": [row_to_dict(user)],
        "user_avatars": _rows(db, UserAvatar, UserAvatar.user_id == user.id),
        "user_backgrounds": _rows(db, UserBackground, UserBackground.user_id == user.id),
        **snapshot_user_data(db, user.id),
        "data_shares": _rows(db, DataShare, or_(DataShare.owner_id == user.id, DataShare.viewer_id == user.id)),
        "notifications": _rows(db, Notification, Notification.user_id == user.id),
        "username_change_requests": _rows(db, UsernameChangeRequest, UsernameChangeRequest.user_id == user.id),
    }
    return tables


def create_backup(
    db: Session, kind: str, user: User, admin: User, tables: dict, summary: str, reason: str | None = None,
    extra: dict | None = None,
) -> DataBackup:
    doc = {"version": 1, "kind": kind, "created_at": now_local().isoformat(), "tables": tables, **(extra or {})}
    payload = gzip.compress(json.dumps(doc, separators=(",", ":")).encode())
    backup = DataBackup(
        kind=kind,
        user_id=user.id,
        username=user.username,
        summary=summary,
        reason=reason,
        row_count=sum(len(rows) for rows in tables.values()),
        payload=payload,
        size_bytes=len(payload),
        created_by=admin.username,
    )
    db.add(backup)
    return backup


def load_backup(backup: DataBackup) -> dict:
    return json.loads(gzip.decompress(backup.payload))


# ---------- restore ----------
def restore_backup(db: Session, backup: DataBackup, admin: User) -> dict:
    """Put the backed-up rows back. Rows that already exist are left alone. Returns counts per table."""
    if backup.restored_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "This backup has already been restored")
    doc = load_backup(backup)
    tables: dict = doc["tables"]

    users_rows = tables.get("app_users", [])
    if users_rows:
        u = users_rows[0]
        if db.get(User, u["id"]) is None:
            clash = db.scalar(select(User).where(or_(User.username == u["username"], User.email == u["email"])))
            if clash:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"Cannot restore: username '{u['username']}' or email '{u['email']}' now belongs to @{clash.username}",
                )
    elif db.get(User, backup.user_id) is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"User @{backup.username} no longer exists. Restore that user's account backup first.",
        )

    def exists(model, pk) -> bool:
        return db.get(model, pk) is not None

    counts: dict[str, int] = {}
    for model in MODELS:
        rows = tables.get(model.__tablename__, [])
        added = 0
        for data in rows:
            data = dict(data)
            if exists(model, _pk(model, data)):
                continue
            # Drop references to users / items that no longer exist instead of failing
            if model is User and data.get("manager_id") and not exists(User, data["manager_id"]):
                data["manager_id"] = None
            if model in (Expense, Fund) and data.get("account_id") and not exists(Account, data["account_id"]):
                data["account_id"] = None
            if model is AccountTransfer and not (exists(Account, data["from_account_id"]) and exists(Account, data["to_account_id"])):
                continue
            if model is Expense:
                if data.get("created_by") and not exists(User, data["created_by"]):
                    data["created_by"] = None
                if data.get("daily_item_id") and not exists(DailyItem, data["daily_item_id"]):
                    data["daily_item_id"] = None
            if model is SubscriptionCharge:
                if not exists(Subscription, data["subscription_id"]):
                    continue
                if data.get("expense_id") and not exists(Expense, data["expense_id"]):
                    data["expense_id"] = None
            if model is DataShare and not (exists(User, data["owner_id"]) and exists(User, data["viewer_id"])):
                continue
            if model is UsernameChangeRequest and data.get("decided_by") and not exists(User, data["decided_by"]):
                data["decided_by"] = None
            if "user_id" in data and model is not User and not exists(User, data["user_id"]):
                continue
            db.add(dict_to_row(model, data))
            added += 1
        db.flush()
        if added:
            counts[model.__tablename__] = added

    # Re-attach people who reported to a restored user (only if nobody else became their manager)
    for report_id in doc.get("reports", []):
        member = db.get(User, report_id)
        if member and member.manager_id is None and users_rows:
            member.manager_id = users_rows[0]["id"]

    backup.restored_at = now_local()
    backup.restored_by = admin.username
    return counts
