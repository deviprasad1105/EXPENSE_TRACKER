import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.orm import Session, defer

from ..accounting.lifecycle import delete_books
from ..config import settings
from ..database import get_db
from ..deps import require_admin, would_create_cycle
from ..importer.models import ImportBatch, ImportChoice, ImportRecord
from ..models import (
    ROLE_ADMIN,
    ROLE_ORG_ADMIN,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    Account,
    AccountDeletionRequest,
    AccountTransfer,
    BankMessage,
    CardBill,
    Budget,
    DailyItem,
    DataBackup,
    DataShare,
    EmailOtp,
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
    TransactionAlias,
    UpiEvent,
    UpiId,
    User,
    UserCategory,
    UserAvatar,
    UserBackground,
    UsernameChangeRequest,
)
from ..schemas import (
    AccountDeletionOut,
    AdminReason,
    AdminUserUpdate,
    BackupOut,
    ManagedUserOut,
    OtpSettings,
    UsernameDecision,
    UsernameRequestOut,
)
from ..security import hash_password
from ..services import mfa, otp
from ..services.mailer import send_email
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


@router.get("/users", response_model=list[ManagedUserOut])
def list_users(status_filter: str | None = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    stmt = select(User).order_by(User.created_at.desc())
    if status_filter:
        stmt = stmt.where(User.status == status_filter)
    return db.scalars(stmt).all()


@router.post("/users/{user_id}/approve", response_model=ManagedUserOut)
def approve(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _get_user(db, user_id)
    if not user.email_verified:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "This user hasn't confirmed their email yet. They must enter the emailed code first."
        )
    user.status = STATUS_APPROVED
    user.approved_at = now_local()
    db.add(Notification(user_id=user.id, kind="INFO", message=f"Welcome! Your account was approved by {admin.username}."))
    db.commit()
    return user


@router.post("/users/{user_id}/reject", response_model=ManagedUserOut)
def reject(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _get_user(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot reject yourself")
    user.status = STATUS_REJECTED
    db.commit()
    return user


@router.patch("/users/{user_id}", response_model=ManagedUserOut)
def update_user(user_id: int, data: AdminUserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _get_user(db, user_id)
    if data.role is not None and data.role != user.role:
        if user.id == admin.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot change your own role")
        if user.role == ROLE_ORG_ADMIN:
            # No longer runs an organisation: its members become ordinary users
            for member in user.org_members:
                member.org_admin_id = None
        user.role = data.role
        if user.role in (ROLE_ADMIN, ROLE_ORG_ADMIN):
            # Admins report to nobody, and an org admin heads their organisation rather than belonging to one
            user.org_admin_id = None
        if user.role == ROLE_ADMIN:
            user.manager_id = None
    if data.status is not None:
        if data.status == STATUS_APPROVED and not user.email_verified:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This user hasn't confirmed their email yet")
        user.status = data.status
        if data.status == STATUS_APPROVED and not user.approved_at:
            user.approved_at = now_local()
    if data.clear_manager:
        user.manager_id = None
    elif data.manager_id is not None:
        _get_user(db, data.manager_id)
        if user.role == ROLE_ADMIN:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "An admin cannot have a manager")
        if would_create_cycle(db, user.id, data.manager_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That would create a loop: the new manager reports to this user")
        user.manager_id = data.manager_id
    if data.clear_org:
        user.org_admin_id = None
    elif data.org_admin_id is not None:
        org_admin = _get_user(db, data.org_admin_id)
        if org_admin.role != ROLE_ORG_ADMIN:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That user is not an organisation admin")
        if user.role in (ROLE_ADMIN, ROLE_ORG_ADMIN):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Admins and organisation admins cannot belong to an organisation")
        user.org_admin_id = org_admin.id
    if data.full_name:
        user.full_name = data.full_name
    if data.email:
        user.email = data.email
    if data.new_password:
        user.password_hash = hash_password(data.new_password)
        user.token_version += 1
        user.failed_logins, user.login_blocked_until = 0, None
    granted = []
    for feature, label in (
        ("feature_expense", "Expense tracking"), ("feature_split", "Split bills"), ("feature_subscription", "Subscriptions"),
        ("feature_accounting", "Accounting"), ("feature_upi", "UPI tracker"),
    ):
        value = getattr(data, feature)
        if value is not None and value != getattr(user, feature):
            setattr(user, feature, value)
            if value:
                granted.append(label)
    # Only new access is announced; removing access is never notified to the user
    if granted:
        db.add(Notification(user_id=user.id, kind="INFO", message=f"{', '.join(granted)} added to your plan by {admin.username}."))
    db.commit()
    return user


def _delete_user_data(db: Session, user_id: int):
    """Expenses, funds, daily items, subscriptions, budgets and accounts of a user (explicit deletes, so it doesn't rely
    on DB cascades). Accounting books go first: their ledgers point at the accounts deleted below."""
    delete_books(db, user_id)
    db.execute(delete(ImportRecord).where(ImportRecord.user_id == user_id))
    db.execute(delete(ImportBatch).where(ImportBatch.user_id == user_id))
    db.execute(delete(ImportChoice).where(ImportChoice.user_id == user_id))
    subs = select(Subscription.id).where(Subscription.user_id == user_id)
    db.execute(delete(SubscriptionCharge).where(SubscriptionCharge.subscription_id.in_(subs)))
    db.execute(delete(Subscription).where(Subscription.user_id == user_id))
    db.execute(delete(BankMessage).where(BankMessage.user_id == user_id))
    db.execute(delete(UpiEvent).where(UpiEvent.user_id == user_id))
    db.execute(delete(UpiId).where(UpiId.user_id == user_id))
    db.execute(delete(Expense).where(Expense.user_id == user_id))
    db.execute(delete(Fund).where(Fund.user_id == user_id))
    db.execute(delete(TransactionAlias).where(TransactionAlias.user_id == user_id))
    db.execute(delete(UserCategory).where(UserCategory.user_id == user_id))
    db.execute(delete(DailyItem).where(DailyItem.user_id == user_id))
    db.execute(delete(Budget).where(Budget.user_id == user_id))
    db.execute(delete(CardBill).where(CardBill.user_id == user_id))
    db.execute(delete(AccountTransfer).where(AccountTransfer.user_id == user_id))
    db.execute(delete(Account).where(Account.user_id == user_id))


def _leave_split_groups(db: Session, user: User) -> None:
    """Take a user who is being deleted out of split bill groups without changing anyone's balance.

    Bills, shares and settlements point at the member row, not the user, so the row stays and becomes a guest with
    their name: every amount and balance is exactly as before. Groups they own pass to another app user in the group
    (an active group admin first); a group with nobody else on the app is deleted, as only they could see it. Bills
    and payments they recorded are credited to the group's owner."""
    name = f"{user.full_name or user.username} (deleted account)"[:100]
    for member in db.scalars(select(SplitMember).where(SplitMember.user_id == user.id)):
        member.user_id = None
        member.guest_name = name
        member.is_admin = False
    db.execute(update(SplitMember).where(SplitMember.added_by == user.id).values(added_by=None))
    db.flush()

    for group in db.scalars(select(SplitGroup).where(SplitGroup.owner_id == user.id)).all():
        users = [m for m in group.members if m.user_id is not None]
        if not users:
            db.delete(group)
            continue
        heir = min(users, key=lambda m: (not m.active, not m.is_admin, m.joined_at, m.id))
        group.owner_id = heir.user_id
        heir.is_admin = True
    db.flush()

    for model in (SplitBill, SplitSettlement):
        for row in db.scalars(select(model).where(model.created_by == user.id)):
            row.created_by = row.group.owner_id
    db.flush()


@router.delete("/users/{user_id}", response_model=BackupOut)
def delete_user(
    user_id: int, reason: str | None = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Delete an account and all its data, even someone in split bill groups (they stay in those groups as a guest).
    A full backup is saved first and can be restored later."""
    user = _get_user(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete yourself")
    return delete_account(db, user, admin, reason, keep_splits_as_guest=True)


def delete_account(db: Session, user: User, actor: User, reason: str | None, keep_splits_as_guest: bool = False) -> DataBackup:
    """Back up, then delete `user` and all their data. Shared by the admin and org admin routes.

    Someone in split bill groups is refused unless `keep_splits_as_guest` (the admin route): an org admin's member can
    share groups with people outside the organisation, whose bills the org admin has no say over."""
    in_splits = db.scalar(
        select(
            exists().where(SplitMember.user_id == user.id)
            | exists().where(SplitGroup.owner_id == user.id)
            | exists().where(SplitBill.created_by == user.id)
            | exists().where(SplitSettlement.created_by == user.id)
        )
    )
    if in_splits and not keep_splits_as_guest:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"@{user.username} is part of split bill groups (now or in the past). They can be deleted once those groups "
            "are settled and deleted, or by an administrator",
        )
    tables = snapshot_user(db, user)
    reports = [u.id for u in user.reports]
    org_members = [u.id for u in user.org_members]
    # Their places in split groups, so restoring the account turns those guests back into them
    split_members = list(db.scalars(select(SplitMember.id).where(SplitMember.user_id == user.id)))
    expenses = len(tables["expenses"])
    backup = create_backup(
        db, KIND_USER, user, actor, tables,
        summary=f"Account @{user.username} ({user.full_name}) with {expenses} expenses, "
        f"{len(tables['funds'])} funds and {len(tables['daily_items'])} daily items",
        reason=reason, extra={"reports": reports, "org_members": org_members, "split_members": split_members},
    )
    for member in user.reports:
        member.manager_id = None
    for member in user.org_members:
        member.org_admin_id = None
    _delete_user_data(db, user.id)
    if in_splits:
        _leave_split_groups(db, user)
    for model, col in (
        (UserAvatar, UserAvatar.user_id), (UserBackground, UserBackground.user_id), (Notification, Notification.user_id),
        (UsernameChangeRequest, UsernameChangeRequest.user_id), (PasswordReset, PasswordReset.user_id),
        (EmailOtp, EmailOtp.user_id),
    ):
        db.execute(delete(model).where(col == user.id))
    db.execute(delete(DataShare).where(or_(DataShare.owner_id == user.id, DataShare.viewer_id == user.id)))
    # Deletion requests stay as a record; a pending one is fulfilled by this delete, whoever started it
    db.flush()  # gives the backup its id
    for req in db.scalars(select(AccountDeletionRequest).where(AccountDeletionRequest.user_id == user.id)):
        req.user_id = None
        if req.status == STATUS_PENDING:
            req.status, req.decided_at = STATUS_APPROVED, now_local()
            req.decided_by, req.decided_by_username, req.backup_id = actor.id, actor.username, backup.id
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


@router.post("/users/{user_id}/lock", response_model=ManagedUserOut)
def lock_user(user_id: int, data: AdminReason, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Locked users cannot sign in and their current sessions stop working immediately.
    Locking an org admin also locks out everyone in their organisation."""
    user = _get_user(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot lock your own account")
    lock_account(user, admin, data.reason)
    db.commit()
    return user


def lock_account(user: User, actor: User, reason: str | None) -> None:
    user.is_locked, user.locked_at, user.locked_by, user.lock_reason = True, now_local(), actor.username, reason
    user.token_version += 1


def unlock_account(db: Session, user: User, actor: User) -> None:
    was_locked = user.is_locked
    user.is_locked, user.locked_at, user.locked_by, user.lock_reason = False, None, None, None
    if was_locked:
        by = f" by {actor.username}" if actor.role == ROLE_ADMIN else ""
        db.add(Notification(user_id=user.id, kind="INFO", message=f"Your account was unlocked{by}."))


@router.post("/users/{user_id}/unlock", response_model=ManagedUserOut)
def unlock_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _get_user(db, user_id)
    unlock_account(db, user, admin)
    db.commit()
    return user


@router.post("/users/{user_id}/mfa/reset", response_model=ManagedUserOut)
def reset_user_mfa(user_id: int, data: AdminReason, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Turn two-step sign-in off for someone who lost their phone and recovery codes. They sign in with their password
    and can set it up again. Their sessions end, and they are emailed."""
    user = _get_user(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Turn off your own two-step sign-in in Settings → Security")
    if not user.mfa_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Two-step sign-in is not on for this user")
    mfa.turn_off(user)
    user.token_version += 1
    reason = f" Reason: {data.reason}" if data.reason else ""
    text = f"Two-step sign-in was turned off for your account by the administrator {admin.username}.{reason}"
    db.add(Notification(user_id=user.id, kind="SECURITY", message=text))
    db.commit()
    send_email(
        user.email,
        "Expense Tracker: two-step sign-in was turned off",
        f"Hi {user.full_name},\n\n{text}\n\nYou can sign in with your password and turn it on again in Settings → Security.\n"
        "If you didn't ask for this, contact an administrator straight away.\n",
    )
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
    counts = restore_backup(db, backup, admin.username)
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


# ---------- account deletion requests ----------
@router.get("/deletion-requests", response_model=list[AccountDeletionOut])
def list_deletion_requests(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.scalars(select(AccountDeletionRequest).order_by(AccountDeletionRequest.created_at.desc())).all()


def _pending_deletion(db: Session, request_id: int) -> AccountDeletionRequest:
    req = db.get(AccountDeletionRequest, request_id)
    if not req or req.status != STATUS_PENDING or req.user_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pending request not found")
    return req


@router.post("/deletion-requests/{request_id}/approve", response_model=AccountDeletionOut)
def approve_deletion_request(
    request_id: int, data: UsernameDecision, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Delete the account as the user asked: the same as deleting it from the Users tab (backed up first; in split
    groups they stay as a guest). They get an email, since they can no longer sign in to see a notification."""
    req = _pending_deletion(db, request_id)
    user = db.get(User, req.user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Another administrator has to approve your own request")
    email, name = user.email, user.full_name
    why = f"Requested by the user: {req.reason}" if req.reason else "Requested by the user"
    delete_account(db, user, admin, why[:500], keep_splits_as_guest=True)
    db.refresh(req)
    req.admin_comment = data.comment
    db.commit()
    note = f"\n\nNote from the administrator: {data.comment}" if data.comment else ""
    send_email(
        email,
        "Your Expense Tracker account has been deleted",
        f"Hi {name},\n\nAs you asked, your Expense Tracker account @{req.username} and all its data have been deleted.{note}\n\n"
        "A backup is kept. Using a code sent to this email you can bring the account back, or erase that backup for "
        f"good, here:\n{settings.frontend_url.rstrip('/')}/recover-account\n\n"
        "If you didn't ask for this, reply to this email or contact an administrator as soon as possible.\n",
    )
    return req


@router.post("/deletion-requests/{request_id}/reject", response_model=AccountDeletionOut)
def reject_deletion_request(
    request_id: int, data: UsernameDecision, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    req = _pending_deletion(db, request_id)
    req.status, req.decided_at, req.admin_comment = STATUS_REJECTED, now_local(), data.comment
    req.decided_by, req.decided_by_username = admin.id, admin.username
    reason = f" Reason: {data.comment}" if data.comment else ""
    db.add(
        Notification(
            user_id=req.user_id,
            kind="DELETION_DECISION",
            message=f"Your request to delete your account was not approved by {admin.username}.{reason}",
        )
    )
    db.commit()
    return req


# ---------- app settings ----------
@router.get("/settings/otp", response_model=OtpSettings)
def get_otp_settings(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return OtpSettings(**otp.otp_minutes(db))


@router.put("/settings/otp", response_model=OtpSettings)
def update_otp_settings(data: OtpSettings, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """How long emailed codes stay valid: sign-up email confirmation, and password / username changes.
    Codes already sent keep the expiry they were sent with."""
    otp.set_otp_minutes(db, data.signup_minutes, data.account_minutes, admin.username)
    db.commit()
    return OtpSettings(**otp.otp_minutes(db))
