import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import delete, exists, or_, select
from sqlalchemy.orm import Session, defer

from ..accounting.lifecycle import delete_books
from ..database import get_db
from ..deps import require_admin, would_create_cycle
from ..models import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    Account,
    AccountTransfer,
    Budget,
    DailyItem,
    DataBackup,
    DataShare,
    Expense,
    Fund,
    Notification,
    PasswordReset,
    SplitBill,
    SplitGroup,
    SplitMember,
    SplitSettlement,
    Subscription,
    SubscriptionCharge,
    User,
    UserAvatar,
    UserBackground,
    UsernameChangeRequest,
)
from ..schemas import AdminReason, AdminUserUpdate, BackupOut, UsernameDecision, UsernameRequestOut, UserOut
from ..security import hash_password
from ..services.backup import (
    KIND_USER,
    KIND_USER_DATA,
    create_backup,
    load_backup,
    restore_backup,
    snapshot_user,
    snapshot_user_data,
)
from ..timeutil import now_local
from .profile import username_taken

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(status_filter: str | None = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    stmt = select(User).order_by(User.created_at.desc())
    if status_filter:
        stmt = stmt.where(User.status == status_filter)
    return db.scalars(stmt).all()


@router.post("/users/{user_id}/approve", response_model=UserOut)
def approve(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _get_user(db, user_id)
    user.status = STATUS_APPROVED
    user.approved_at = now_local()
    db.add(Notification(user_id=user.id, kind="INFO", message=f"Welcome! Your account was approved by {admin.username}."))
    db.commit()
    return user


@router.post("/users/{user_id}/reject", response_model=UserOut)
def reject(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _get_user(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot reject yourself")
    user.status = STATUS_REJECTED
    db.commit()
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, data: AdminUserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _get_user(db, user_id)
    if data.role is not None:
        if user.id == admin.id and data.role != user.role:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot change your own role")
        user.role = data.role
    if data.status is not None:
        user.status = data.status
        if data.status == STATUS_APPROVED and not user.approved_at:
            user.approved_at = now_local()
    if data.clear_manager:
        user.manager_id = None
    elif data.manager_id is not None:
        _get_user(db, data.manager_id)
        if would_create_cycle(db, user.id, data.manager_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That would create a loop: the new manager reports to this user")
        user.manager_id = data.manager_id
    if data.full_name:
        user.full_name = data.full_name
    if data.email:
        user.email = data.email
    if data.new_password:
        user.password_hash = hash_password(data.new_password)
        user.token_version += 1
        user.failed_logins, user.login_blocked_until = 0, None
    granted, revoked = [], []
    for feature, label in (
        ("feature_expense", "Expense tracking"), ("feature_split", "Split bills"), ("feature_subscription", "Subscriptions"),
        ("feature_accounting", "Accounting"),
    ):
        value = getattr(data, feature)
        if value is not None and value != getattr(user, feature):
            setattr(user, feature, value)
            (granted if value else revoked).append(label)
    if granted or revoked:
        parts = ([f"{', '.join(granted)} added to"] if granted else []) + ([f"{', '.join(revoked)} removed from"] if revoked else [])
        db.add(Notification(user_id=user.id, kind="INFO", message=f"{' and '.join(parts)} your plan by {admin.username}."))
    db.commit()
    return user


def _delete_user_data(db: Session, user_id: int):
    """Expenses, funds, daily items, subscriptions, budgets and accounts of a user (explicit deletes, so it doesn't rely
    on DB cascades). Accounting books go first: their ledgers point at the accounts deleted below."""
    delete_books(db, user_id)
    subs = select(Subscription.id).where(Subscription.user_id == user_id)
    db.execute(delete(SubscriptionCharge).where(SubscriptionCharge.subscription_id.in_(subs)))
    db.execute(delete(Subscription).where(Subscription.user_id == user_id))
    db.execute(delete(Expense).where(Expense.user_id == user_id))
    db.execute(delete(Fund).where(Fund.user_id == user_id))
    db.execute(delete(DailyItem).where(DailyItem.user_id == user_id))
    db.execute(delete(Budget).where(Budget.user_id == user_id))
    db.execute(delete(AccountTransfer).where(AccountTransfer.user_id == user_id))
    db.execute(delete(Account).where(Account.user_id == user_id))


@router.delete("/users/{user_id}", response_model=BackupOut)
def delete_user(
    user_id: int, reason: str | None = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Delete an account and all its data. A full backup is saved first and can be restored later."""
    user = _get_user(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete yourself")
    in_splits = db.scalar(
        select(
            exists().where(SplitMember.user_id == user.id)
            | exists().where(SplitGroup.owner_id == user.id)
            | exists().where(SplitBill.created_by == user.id)
            | exists().where(SplitSettlement.created_by == user.id)
        )
    )
    if in_splits:
        # Deleting them would change other people's balances
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"@{user.username} is part of split bill groups (now or in the past). They can be deleted once those groups are settled and deleted",
        )
    tables = snapshot_user(db, user)
    reports = [u.id for u in user.reports]
    expenses = len(tables["expenses"])
    backup = create_backup(
        db, KIND_USER, user, admin, tables,
        summary=f"Account @{user.username} ({user.full_name}) with {expenses} expenses, "
        f"{len(tables['funds'])} funds and {len(tables['daily_items'])} daily items",
        reason=reason, extra={"reports": reports},
    )
    for member in user.reports:
        member.manager_id = None
    _delete_user_data(db, user.id)
    for model, col in (
        (UserAvatar, UserAvatar.user_id), (UserBackground, UserBackground.user_id), (Notification, Notification.user_id),
        (UsernameChangeRequest, UsernameChangeRequest.user_id), (PasswordReset, PasswordReset.user_id),
    ):
        db.execute(delete(model).where(col == user.id))
    db.execute(delete(DataShare).where(or_(DataShare.owner_id == user.id, DataShare.viewer_id == user.id)))
    db.flush()
    db.delete(user)
    db.commit()
    return backup


@router.post("/users/{user_id}/clear-data", response_model=BackupOut)
def clear_user_data(user_id: int, data: AdminReason, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Delete all expenses, funds and daily items of a user but keep the account. Backed up first."""
    user = _get_user(db, user_id)
    tables = snapshot_user_data(db, user.id)
    if not any(tables.values()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"@{user.username} has no data to delete")
    backup = create_backup(
        db, KIND_USER_DATA, user, admin, tables,
        summary=f"Data of @{user.username}: {len(tables['expenses'])} expenses, {len(tables['funds'])} funds, "
        f"{len(tables['daily_items'])} daily items, {len(tables['subscriptions'])} subscriptions",
        reason=data.reason,
    )
    _delete_user_data(db, user.id)
    db.add(Notification(user_id=user.id, kind="INFO", message=f"An administrator ({admin.username}) cleared your expense data."))
    db.commit()
    return backup


@router.post("/users/{user_id}/lock", response_model=UserOut)
def lock_user(user_id: int, data: AdminReason, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Locked users cannot sign in and their current sessions stop working immediately."""
    user = _get_user(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot lock your own account")
    user.is_locked, user.locked_at, user.locked_by, user.lock_reason = True, now_local(), admin.username, data.reason
    user.token_version += 1
    db.commit()
    return user


@router.post("/users/{user_id}/unlock", response_model=UserOut)
def unlock_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _get_user(db, user_id)
    user.is_locked, user.locked_at, user.locked_by, user.lock_reason = False, None, None, None
    db.add(Notification(user_id=user.id, kind="INFO", message=f"Your account was unlocked by {admin.username}."))
    db.commit()
    return user


# ---------- backups ----------
def _get_backup(db: Session, backup_id: int) -> DataBackup:
    backup = db.get(DataBackup, backup_id)
    if not backup:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Backup not found")
    return backup


@router.get("/backups", response_model=list[BackupOut])
def list_backups(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    stmt = select(DataBackup).options(defer(DataBackup.payload)).order_by(DataBackup.created_at.desc(), DataBackup.id.desc())
    return db.scalars(stmt).all()


@router.get("/backups/{backup_id}/download")
def download_backup(backup_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """The full dump as readable JSON."""
    backup = _get_backup(db, backup_id)
    body = json.dumps(load_backup(backup), indent=2, ensure_ascii=False)
    name = f"backup_{backup.id}_{backup.kind.lower()}_{backup.username}_{backup.created_at:%Y%m%d_%H%M%S}.json"
    return Response(body, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/backups/{backup_id}/restore")
def restore(backup_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    backup = _get_backup(db, backup_id)
    counts = restore_backup(db, backup, admin)
    db.commit()
    return {"message": f"Restored {sum(counts.values())} rows for @{backup.username}", "restored": counts}


@router.delete("/backups/{backup_id}", status_code=204)
def delete_backup(backup_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Permanently discard a backup."""
    db.delete(_get_backup(db, backup_id))
    db.commit()


# ---------- username change requests ----------
class UsernameRequestAdminOut(UsernameRequestOut):
    full_name: str


@router.get("/username-requests", response_model=list[UsernameRequestAdminOut])
def list_username_requests(
    status_filter: str | None = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    stmt = select(UsernameChangeRequest).order_by(UsernameChangeRequest.created_at.desc())
    if status_filter:
        stmt = stmt.where(UsernameChangeRequest.status == status_filter)
    return [
        UsernameRequestAdminOut(**UsernameRequestOut.model_validate(r).model_dump(), full_name=r.user.full_name)
        for r in db.scalars(stmt)
    ]


def _pending(db: Session, request_id: int) -> UsernameChangeRequest:
    req = db.get(UsernameChangeRequest, request_id)
    if not req or req.status != STATUS_PENDING:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pending request not found")
    return req


@router.post("/username-requests/{request_id}/approve", response_model=UsernameRequestOut)
def approve_username_request(
    request_id: int, data: UsernameDecision, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Admin applies the new username. The user's own 15-day lock restarts from now."""
    req = _pending(db, request_id)
    if username_taken(db, req.requested_username, req.user_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "That username has been taken since the request was made")
    now = now_local()
    req.user.username = req.requested_username
    req.user.username_changed_at = now
    req.status, req.decided_at, req.decided_by, req.admin_comment = STATUS_APPROVED, now, admin.id, data.comment
    db.add(
        Notification(
            user_id=req.user_id,
            kind="USERNAME_REQUEST",
            message=f"Your username was changed to '{req.requested_username}' by {admin.username}. "
            "Use it the next time you log in.",
        )
    )
    db.commit()
    return req


@router.post("/username-requests/{request_id}/reject", response_model=UsernameRequestOut)
def reject_username_request(
    request_id: int, data: UsernameDecision, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    req = _pending(db, request_id)
    req.status, req.decided_at, req.decided_by, req.admin_comment = STATUS_REJECTED, now_local(), admin.id, data.comment
    reason = f" Reason: {data.comment}" if data.comment else ""
    db.add(
        Notification(
            user_id=req.user_id,
            kind="USERNAME_REQUEST",
            message=f"Your request to change your username to '{req.requested_username}' was rejected.{reason}",
        )
    )
    db.commit()
    return req
