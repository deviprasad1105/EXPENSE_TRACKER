"""One-time codes sent by email: confirming the address at sign-up, and approving a password or username change.

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
from ..models import OTP_PASSWORD, OTP_SIGNUP, OTP_USERNAME, AppSetting, EmailOtp, User
from ..timeutil import now_local
from .mailer import send_email

KEY_SIGNUP_MINUTES = "otp_signup_minutes"
KEY_ACCOUNT_MINUTES = "otp_account_minutes"
MIN_MINUTES, MAX_MINUTES = 1, 60

_PURPOSE_TEXT = {
    OTP_SIGNUP: ("confirm your email address", "confirm your email"),
    OTP_PASSWORD: ("change your password", "password change"),
    OTP_USERNAME: ("change your username", "username change"),
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


def consume(db: Session, user: User, purpose: str, code: str | None) -> None:
    """Check the code and mark it used (the caller commits). Raises 400 with a clear reason when it doesn't match."""
    code = (code or "").strip()
    otp = _latest(db, user.id, purpose)
    now = now_local()
    if not otp or otp.used_at or otp.expires_at <= now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The code has expired or was not requested. Send a new code.")
    if not hmac.compare_digest(otp.code_hash, _hash(user.id, purpose, code)):
        otp.failed_attempts += 1
        left = settings.otp_max_attempts - otp.failed_attempts
        if left <= 0:
            otp.expires_at = now
        db.commit()
        if left <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Too many wrong codes. Send a new code.")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"The code is incorrect ({left} attempt(s) left)")
    otp.used_at = now


__all__ = ["OTP_PASSWORD", "OTP_SIGNUP", "OTP_USERNAME", "consume", "issue", "otp_minutes", "set_otp_minutes"]
