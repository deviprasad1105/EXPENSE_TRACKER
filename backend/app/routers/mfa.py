"""Two-step sign-in (authenticator app): turn it on, off, and make new recovery codes. Each user chooses for themselves
in Settings → Security; an admin can turn it off for someone who lost their phone (admin router)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Notification, User
from ..ratelimit import RateLimiter
from ..schemas import MfaConfirmIn, MfaEnableIn, MfaRecoveryCodesOut, MfaSetupIn, MfaSetupOut, MfaStatusOut
from ..security import verify_password
from ..services import mfa
from ..services.mailer import send_email
from ..timeutil import now_local

router = APIRouter(prefix="/api/profile/mfa", tags=["two-step sign-in"])

# Wrong passwords / codes here, per user: slows down guessing from a session left open
mfa_limiter = RateLimiter(10, 60)


def _limit(user: User) -> None:
    if not mfa_limiter.hit(f"mfa-settings:{user.id}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Wait a minute and try again.")


def _check_password(user: User, password: str) -> None:
    if not verify_password(password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password is incorrect")


def _status(user: User) -> MfaStatusOut:
    return MfaStatusOut(enabled=user.mfa_enabled, enabled_at=user.mfa_enabled_at, recovery_codes_left=mfa.recovery_left(user))


def _tell(db: Session, user: User, subject: str, text: str) -> None:
    db.add(Notification(user_id=user.id, kind="SECURITY", message=text))
    send_email(
        user.email,
        f"Expense Tracker: {subject}",
        f"Hi {user.full_name},\n\n{text}\n\nIf this wasn't you, change your password straight away and contact an administrator.\n",
    )


@router.get("", response_model=MfaStatusOut)
def mfa_status(user: User = Depends(get_current_user)):
    return _status(user)


@router.post("/setup", response_model=MfaSetupOut)
def start_setup(data: MfaSetupIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Step 1: a new secret for the authenticator app (QR code). Nothing changes until it is confirmed with a code."""
    _limit(user)
    if user.mfa_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Two-step sign-in is already on. Turn it off first to move it to a new phone.")
    _check_password(user, data.password)
    secret = mfa.new_secret()
    user.mfa_pending_secret = mfa.seal(secret)
    db.commit()
    return MfaSetupOut(secret=secret, otpauth_uri=mfa.otpauth_uri(secret, user.username))


@router.post("/enable", response_model=MfaRecoveryCodesOut)
def enable(data: MfaEnableIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Step 2: the first code from the app proves it was added correctly. Returns the recovery codes, shown once."""
    _limit(user)
    if user.mfa_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Two-step sign-in is already on")
    secret = mfa.unseal(user.mfa_pending_secret)
    if not secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Start again: the setup has expired")
    step = mfa.matching_step(secret, data.code)
    if step is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The code is incorrect. Check that your phone's time is set automatically.")
    user.mfa_secret, user.mfa_pending_secret = user.mfa_pending_secret, None
    user.mfa_enabled, user.mfa_enabled_at, user.mfa_last_step = True, now_local(), step
    codes = mfa.new_recovery_codes(user)
    _tell(db, user, "two-step sign-in is on",
          "Two-step sign-in was turned on for your account. From now on, signing in also needs the 6-digit code "
          "from your authenticator app.")
    db.commit()
    return MfaRecoveryCodesOut(recovery_codes=codes)


@router.post("/disable", response_model=MfaStatusOut)
def disable(data: MfaConfirmIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _limit(user)
    if not user.mfa_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Two-step sign-in is already off")
    _check_password(user, data.password)
    if not mfa.verify(user, data.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The code is incorrect")
    mfa.turn_off(user)
    _tell(db, user, "two-step sign-in is off",
          "Two-step sign-in was turned off for your account. Signing in now needs only your password.")
    db.commit()
    return _status(user)


@router.post("/recovery-codes", response_model=MfaRecoveryCodesOut)
def regenerate_recovery_codes(data: MfaConfirmIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """New recovery codes; the old ones stop working."""
    _limit(user)
    if not user.mfa_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Turn on two-step sign-in first")
    _check_password(user, data.password)
    if not mfa.verify(user, data.code, allow_recovery=False):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The code is incorrect: use the code from your authenticator app")
    codes = mfa.new_recovery_codes(user)
    db.add(Notification(user_id=user.id, kind="SECURITY", message="New recovery codes were made; the old ones no longer work."))
    db.commit()
    return MfaRecoveryCodesOut(recovery_codes=codes)
