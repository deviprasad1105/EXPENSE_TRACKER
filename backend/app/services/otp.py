"""One-time codes sent by email: confirming the address at sign-up, approving a password or username change, and
restoring or permanently erasing a deleted account (sent to the email that account used).

Each code is 6 digits, works once, and expires after a number of minutes the admin sets (Admin → Settings).
Only the newest code for a user and purpose is valid, and OTP_MAX_ATTEMPTS wrong tries disable it.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..models import OTP_LOGIN, OTP_PASSWORD, OTP_PURGE, OTP_RESTORE, OTP_SIGNUP, OTP_USERNAME, AppSetting, EmailOtp, RecoveryOtp, User
from ..timeutil import now_local
from .mailer import send_email

KEY_SIGNUP_MINUTES = "otp_signup_minutes"
KEY_ACCOUNT_MINUTES = "otp_account_minutes"
MIN_MINUTES, MAX_MINUTES = 1, 60

_PURPOSE_TEXT = {
    OTP_SIGNUP: ("confirm your email address", "confirm your email"),
    OTP_PASSWORD: ("change your password", "password change"),
    OTP_USERNAME: ("change your username", "username change"),
    OTP_LOGIN: ("sign in", "sign in"),
}


def _setting_minutes(db: Session, key: str, default: int) -> int:
    row = db.get(AppSetting, key)
    try:
        return int(row.value) if row else default
    except ValueError:
        return default


def otp_minutes(db: Session) -> dict:
    return {
        "signup_minutes": _setting_minutes(db, KEY_SIGNUP_MINUTES, settings.otp_signup_minutes),
        "account_minutes": _setting_minutes(db, KEY_ACCOUNT_MINUTES, settings.otp_account_minutes),
    }


def set_otp_minutes(db: Session, signup_minutes: int, account_minutes: int, actor: str) -> None:
    now = now_local()
    for key, value in ((KEY_SIGNUP_MINUTES, signup_minutes), (KEY_ACCOUNT_MINUTES, account_minutes)):
        row = db.get(AppSetting, key)
        if row:
            row.value, row.updated_at, row.updated_by = str(value), now, actor
        else:
            db.add(AppSetting(key=key, value=str(value), updated_at=now, updated_by=actor))


def minutes_for(db: Session, purpose: str) -> int:
    m = otp_minutes(db)
    return m["signup_minutes"] if purpose == OTP_SIGNUP else m["account_minutes"]


def _hash(user_id: int, purpose: str, code: str) -> str:
    msg = f"{user_id}:{purpose}:{code}".encode()
    return hmac.new(settings.jwt_secret.encode(), msg, hashlib.sha256).hexdigest()


def _latest(db: Session, user_id: int, purpose: str) -> EmailOtp | None:
    return db.scalar(
        select(EmailOtp)
        .where(EmailOtp.user_id == user_id, EmailOtp.purpose == purpose)
        .order_by(EmailOtp.created_at.desc(), EmailOtp.id.desc())
        .limit(1)
    )


def resend_wait_seconds(db: Session, user_id: int, purpose: str) -> int:
    """Seconds until another code may be sent (0 = now). Stops one account's inbox being flooded."""
    last = _latest(db, user_id, purpose)
    if not last:
        return 0
    left = settings.otp_resend_seconds - (now_local() - last.created_at).total_seconds()
    return max(0, int(left + 0.999))


def issue(db: Session, user: User, purpose: str) -> tuple[datetime, bool]:
    """Create a new code (older ones stop working), commit, and email it. Returns (expires_at, email_sent)."""
    now = now_local()
    db.execute(
        update(EmailOtp)
        .where(EmailOtp.user_id == user.id, EmailOtp.purpose == purpose, EmailOtp.used_at.is_(None), EmailOtp.expires_at > now)
        .values(expires_at=now)
    )
    minutes = minutes_for(db, purpose)
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = now + timedelta(minutes=minutes)
    db.add(EmailOtp(user_id=user.id, purpose=purpose, code_hash=_hash(user.id, purpose, code), created_at=now, expires_at=expires))
    db.commit()

    action, subject = _PURPOSE_TEXT[purpose]
    sent = send_email(
        user.email,
        f"Expense Tracker: your code to {subject}",
        f"Hi {user.full_name},\n\n"
        f"Your one-time code to {action} is:\n\n    {code}\n\n"
        f"It expires in {minutes} minute(s), at {expires:%d %b %Y %H:%M:%S}, and works only once.\n"
        "Never share this code. If you didn't ask for it, ignore this email"
        + (" and consider changing your password.\n" if purpose != OTP_SIGNUP else ".\n"),
    )
    return expires, sent


def seconds_left(expires: datetime) -> int:
    return max(0, int((expires - now_local()).total_seconds()))


def require_sent(sent: bool) -> None:
    if not sent:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "We couldn't send the email. Try again in a minute.")


def _check(db: Session, otp: EmailOtp | RecoveryOtp | None, given_hash: str) -> None:
    """Mark `otp` used if `given_hash` matches it (the caller commits); otherwise count the miss and raise 400."""
    now = now_local()
    if not otp or otp.used_at or otp.expires_at <= now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The code has expired or was not requested. Send a new code.")
    if not hmac.compare_digest(otp.code_hash, given_hash):
        otp.failed_attempts += 1
        left = settings.otp_max_attempts - otp.failed_attempts
        if left <= 0:
            otp.expires_at = now
        db.commit()
        if left <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Too many wrong codes. Send a new code.")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"The code is incorrect ({left} attempt(s) left)")
    otp.used_at = now


def consume(db: Session, user: User, purpose: str, code: str | None) -> None:
    """Check the code and mark it used (the caller commits). Raises 400 with a clear reason when it doesn't match."""
    _check(db, _latest(db, user.id, purpose), _hash(user.id, purpose, (code or "").strip()))


# ---------- deleted accounts: codes tied to the email address ----------
_RECOVERY_TEXT = {
    OTP_RESTORE: ("restore your deleted account", "restore your account"),
    OTP_PURGE: ("permanently delete your account's data. This cannot be undone", "erase your data"),
}


def normal_email(email: str) -> str:
    return email.strip().lower()


def _recovery_hash(email: str, purpose: str, code: str) -> str:
    msg = f"recover:{email}:{purpose}:{code}".encode()
    return hmac.new(settings.jwt_secret.encode(), msg, hashlib.sha256).hexdigest()


def _latest_recovery(db: Session, email: str, purpose: str) -> RecoveryOtp | None:
    return db.scalar(
        select(RecoveryOtp)
        .where(RecoveryOtp.email == email, RecoveryOtp.purpose == purpose)
        .order_by(RecoveryOtp.created_at.desc(), RecoveryOtp.id.desc())
        .limit(1)
    )


def recovery_wait_seconds(db: Session, email: str, purpose: str) -> int:
    last = _latest_recovery(db, normal_email(email), purpose)
    if not last:
        return 0
    left = settings.otp_resend_seconds - (now_local() - last.created_at).total_seconds()
    return max(0, int(left + 0.999))


def issue_recovery(db: Session, email: str, username: str, purpose: str) -> tuple[datetime, bool]:
    """Email a code for the deleted account @username to `email` (older codes stop working). Commits."""
    email, now = normal_email(email), now_local()
    db.execute(
        update(RecoveryOtp)
        .where(RecoveryOtp.email == email, RecoveryOtp.purpose == purpose, RecoveryOtp.used_at.is_(None), RecoveryOtp.expires_at > now)
        .values(expires_at=now)
    )
    minutes = minutes_for(db, purpose)
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = now + timedelta(minutes=minutes)
    db.add(RecoveryOtp(email=email, purpose=purpose, code_hash=_recovery_hash(email, purpose, code), created_at=now, expires_at=expires))
    db.commit()

    action, subject = _RECOVERY_TEXT[purpose]
    sent = send_email(
        email,
        f"Expense Tracker: your code to {subject}",
        f"Hi,\n\nSomeone asked to {action} for the deleted Expense Tracker account @{username}.\n\n"
        f"Your one-time code is:\n\n    {code}\n\n"
        f"It expires in {minutes} minute(s), at {expires:%d %b %Y %H:%M:%S}, and works only once.\n"
        "Never share this code. If you didn't ask for it, ignore this email: nothing changes without the code.\n",
    )
    return expires, sent


def consume_recovery(db: Session, email: str, purpose: str, code: str | None) -> None:
    email = normal_email(email)
    _check(db, _latest_recovery(db, email, purpose), _recovery_hash(email, purpose, (code or "").strip()))


__all__ = [
    "OTP_PASSWORD", "OTP_PURGE", "OTP_RESTORE", "OTP_SIGNUP", "OTP_USERNAME", "consume", "consume_recovery", "issue",
    "issue_recovery", "otp_minutes", "set_otp_minutes",
]
