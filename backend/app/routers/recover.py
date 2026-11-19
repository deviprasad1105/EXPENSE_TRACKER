"""The owner of a deleted account brings it back, or erases its backup for good, with a code sent to its email.

Deleting an account keeps a full backup (see services/backup.py). Whoever can read the email the account used can:
- RESTORE: put the account and all its data back, exactly as they were (same username and password);
- PURGE: permanently delete every backup of that account, so nothing of it is left.

Asking for a code never says whether a deleted account used that email, so the page can't be used to find accounts.

The same choice is offered to someone who signs up again with that email, once they've entered the code that confirms
it (so it too is only shown to whoever reads the email): restoring cancels the new sign-up.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    OTP_RESTORE,
    ROLE_ADMIN,
    STATUS_PENDING,
    AccountDeletionRequest,
    DataBackup,
    EmailOtp,
    Notification,
    RecoveryOtp,
    User,
)
from ..schemas import OtpSentOut, RecoverCodeIn, RecoverConfirmIn, SignupChoiceIn
from ..security import create_ticket, decode_ticket
from ..services import otp
from ..services.backup import KIND_USER, restore_backup
from ..services.mailer import send_email
from ..timeutil import now_local
from .auth import _client_ip, approval_message, login_limiter, public_form_limiter

router = APIRouter(prefix="/api/auth/recover", tags=["auth"])

# Proof that a new sign-up confirmed its email, sent back with the choice about the deleted account that used it
SIGNUP_CHOICE_TICKET = "deleted-choice"
SIGNUP_CHOICE_MINUTES = 30


def deleted_account_backups(db: Session, email: str) -> list[DataBackup]:
    """Account backups made under this email whose account is still deleted, newest first."""
    return list(db.scalars(
        select(DataBackup)
        .where(DataBackup.kind == KIND_USER, func.lower(DataBackup.email) == otp.normal_email(email), DataBackup.restored_at.is_(None))
        .order_by(DataBackup.created_at.desc(), DataBackup.id.desc())
    ))


def _tell_admins(db: Session, message: str) -> None:
    for admin_id in db.scalars(select(User.id).where(User.role == ROLE_ADMIN)):
        db.add(Notification(user_id=admin_id, kind="INFO", message=message))


@router.post("/send-code", response_model=OtpSentOut)
def send_code(data: RecoverCodeIn, request: Request, db: Session = Depends(get_db)):
    if not public_form_limiter.hit(f"recover:{_client_ip(request)}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Wait a minute and try again.")
    email = otp.normal_email(data.email)
    backups = deleted_account_backups(db, email)
    if backups and not otp.recovery_wait_seconds(db, email, data.action):
        expires, _ = otp.issue_recovery(db, email, backups[0].username, data.action)
    else:
        # Same answer whether or not a deleted account used this email
        expires = now_local() + timedelta(minutes=otp.minutes_for(db, data.action))
    return OtpSentOut(
        message=f"If a deleted account used {email}, we've emailed a code there. "
        "It can take a minute; check your spam folder too.",
        expires_at=expires,
        expires_in_seconds=otp.seconds_left(expires),
    )


@router.post("/confirm")
def confirm(data: RecoverConfirmIn, request: Request, db: Session = Depends(get_db)):
    if not login_limiter.hit(f"recover-confirm:{_client_ip(request)}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Wait a minute and try again.")
    email = otp.normal_email(data.email)
    otp.consume_recovery(db, email, data.action, data.code)
    backups = deleted_account_backups(db, email)
    if not backups:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "There is no deleted account for this email any more.")
    return _restore(db, backups[0], email) if data.action == OTP_RESTORE else _purge(db, backups, email)


def offer_for_new_signup(db: Session, user: User) -> dict | None:
    """After a sign-up's email code: the deleted account that used the same email, if any, and a ticket to choose."""
    backups = deleted_account_backups(db, user.email)
    if not backups:
        return None
    email = otp.normal_email(user.email)
    return {
        "username": backups[0].username,
        "deleted_at": backups[0].created_at.isoformat(),
        "ticket": create_ticket(SIGNUP_CHOICE_TICKET, user.id, user.token_version, SIGNUP_CHOICE_MINUTES, email=email),
    }


@router.post("/after-signup")
def after_signup(data: SignupChoiceIn, request: Request, db: Session = Depends(get_db)):
    if not login_limiter.hit(f"recover-signup:{_client_ip(request)}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Wait a minute and try again.")
    ticket = decode_ticket(data.ticket, SIGNUP_CHOICE_TICKET)
    user = db.get(User, ticket["sub"]) if ticket else None
    if (
        not user
        or user.token_version != ticket["ver"]
        or user.status != STATUS_PENDING
        or not user.email_verified
        or otp.normal_email(user.email) != ticket.get("email")
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "This has expired. The email we sent when the account was deleted has a link to restore it or erase its data.",
        )
    email = ticket["email"]
    backups = deleted_account_backups(db, email)
    if not backups:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "There is no deleted account for this email any more.")
    if data.action == OTP_RESTORE:
        _drop_new_signup(db, user)
        out = _restore(db, backups[0], email)
        out["message"] += " Your new sign-up was cancelled."
        return out
    out = _purge(db, backups, email)
    out["message"] += " Your new account is waiting for an administrator's approval."
    return out


def _drop_new_signup(db: Session, user: User) -> None:
    """Cancel a sign-up that was never approved (it has no data), so the old account can have its email back.
    Not saved unless the restore that follows succeeds."""
    db.execute(delete(EmailOtp).where(EmailOtp.user_id == user.id))
    db.execute(delete(Notification).where(Notification.user_id == user.id))
    db.execute(delete(Notification).where(Notification.kind == "APPROVAL", Notification.message == approval_message(user.username)))
    db.delete(user)
    db.flush()


def _restore(db: Session, backup: DataBackup, email: str) -> dict:
    """Bring back the most recently deleted account (an email can have had several over time)."""
    # Refused (and nothing is saved, the code included) if the username or email now belongs to someone else
    taken = db.scalar(
        select(User).where(or_(func.lower(User.username) == backup.username.lower(), func.lower(User.email) == email))
    )
    if taken or db.get(User, backup.user_id) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"The account can't come back: the username @{backup.username} or this email now belongs to another account. "
            "You can still erase the old data, or ask an administrator for help.",
        )
    restore_backup(db, backup, f"@{backup.username} (self)")
    user = db.get(User, backup.user_id)
    db.add(Notification(user_id=user.id, kind="SECURITY", message="Your account was restored using a code sent to your email."))
    _tell_admins(db, f"@{user.username} restored their deleted account using a code sent to {email}.")
    db.commit()
    send_email(
        email,
        "Your Expense Tracker account is back",
        f"Hi {user.full_name},\n\nYour account @{user.username} and all its data have been restored. "
        "Sign in with your username and your old password (use 'Forgot password?' if you don't remember it).\n\n"
        "If this wasn't you, change your password and contact an administrator.\n",
    )
    message = f"Your account @{user.username} is back with all its data. Sign in with your old password."
    if user.is_locked:
        message += " It is still locked by an administrator, so contact them before signing in."
    return {"message": message, "username": user.username}


def _purge(db: Session, backups: list[DataBackup], email: str) -> dict:
    """Erase every backup of the deleted account(s) that used this email: account backups and earlier data backups."""
    user_ids = {b.user_id for b in backups}
    username = backups[0].username
    gone = db.scalars(select(DataBackup).where(DataBackup.user_id.in_(user_ids))).all()
    gone_ids = [b.id for b in gone]
    for req in db.scalars(select(AccountDeletionRequest).where(AccountDeletionRequest.backup_id.in_(gone_ids))):
        req.backup_id = None
    db.execute(delete(DataBackup).where(DataBackup.id.in_(gone_ids)))
    db.flush()  # saves the code as used, so it survives the clean-up of unused ones below
    db.execute(delete(RecoveryOtp).where(RecoveryOtp.email == email, RecoveryOtp.used_at.is_(None)))
    _tell_admins(db, f"The owner of the deleted account @{username} permanently erased its data ({len(gone_ids)} backup(s)).")
    db.commit()
    send_email(
        email,
        "Your Expense Tracker data has been permanently deleted",
        f"Hi,\n\nAs you asked, every backup of the deleted account @{username} has been permanently deleted. "
        "It can no longer be restored by you or by an administrator.\n",
    )
    return {"message": f"All data of the deleted account @{username} has been permanently deleted. It can't be restored.", "username": username}
