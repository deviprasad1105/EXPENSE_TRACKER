"""Passcode that opens the app on a device where the user is already signed in. Asked only while the admin has
turned passcodes on (Admin -> Settings); see services/security_policy.py.

Forgot it? Sign out and sign in with the password: the app then opens without the passcode, and it can be changed
here with the password. Too many wrong passcodes in a row sign the user out everywhere.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Notification, User
from ..ratelimit import RateLimiter
from ..schemas import PasscodeSetIn, PasscodeVerifyIn
from ..security import hash_password, verify_password
from ..services import security_policy as policy
from ..timeutil import now_local

router = APIRouter(prefix="/api/profile/passcode", tags=["passcode"])

passcode_limiter = RateLimiter(10, 60)


class PasscodeStatus(BaseModel):
    required: bool
    set: bool


def _limit(user: User) -> None:
    if not passcode_limiter.hit(f"passcode:{user.id}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Wait a minute and try again.")


@router.get("", response_model=PasscodeStatus)
def passcode_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return PasscodeStatus(required=policy.passcode_required(db), set=user.passcode_set)


@router.put("", response_model=PasscodeStatus)
def set_passcode(data: PasscodeSetIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Choose a passcode. Changing one already set needs the account password."""
    _limit(user)
    if user.passcode_hash and not verify_password(data.password or "", user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter your account password to change the passcode")
    if len(set(data.passcode)) == 1 or data.passcode in "0123456789" or data.passcode in "9876543210":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a passcode that is harder to guess (not 1111 or 1234)")
    user.passcode_hash, user.passcode_set_at, user.passcode_failures = hash_password(data.passcode), now_local(), 0
    db.commit()
    return PasscodeStatus(required=policy.passcode_required(db), set=True)


@router.post("/verify")
def verify_passcode(data: PasscodeVerifyIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Unlock the app. After PASSCODE_MAX_FAILURES wrong ones in a row the user is signed out everywhere (401)."""
    _limit(user)
    if not user.passcode_hash:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a passcode first")
    if verify_password(data.passcode, user.passcode_hash):
        user.passcode_failures = 0
        db.commit()
        return {"ok": True}
    user.passcode_failures += 1
    left = policy.PASSCODE_MAX_FAILURES - user.passcode_failures
    policy.record_login(db, user, request, ok=False, detail="Wrong passcode")
    if left <= 0:
        user.passcode_failures = 0
        user.token_version += 1
        db.add(Notification(user_id=user.id, kind="SECURITY", message=(
            f"You were signed out everywhere after {policy.PASSCODE_MAX_FAILURES} wrong passcodes. "
            "Sign in with your password; you can then change the passcode in Settings.")))
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Too many wrong passcodes. Sign in with your password.")
    db.commit()
    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Wrong passcode ({left} attempt(s) left)")
