from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import (
    OTP_PASSWORD,
    OTP_USERNAME,
    ROLE_ADMIN,
    STATUS_PENDING,
    Notification,
    User,
    UserAvatar,
    UserBackground,
    UsernameChangeRequest,
)
from ..schemas import (
    OtpRequestIn,
    OtpSentOut,
    PasswordChange,
    PreferencesUpdate,
    ProfileOut,
    ProfileUpdate,
    TokenOut,
    UserBrief,
    UsernameChange,
    UsernameRequestIn,
    UsernameRequestOut,
    UserOut,
)
from ..security import create_access_token, hash_password, verify_password
from ..services import otp
from ..timeutil import now_local

router = APIRouter(prefix="/api/profile", tags=["profile"])

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def username_locked_until(user: User) -> datetime | None:
    """When the user may next change their own username, or None if they can change it now."""
    if not user.username_changed_at:
        return None
    until = user.username_changed_at + timedelta(days=settings.username_lock_days)
    return until if now_local() < until else None


def username_taken(db: Session, username: str, exclude_id: int) -> bool:
    stmt = select(User.id).where(func.lower(User.username) == username.lower(), User.id != exclude_id)
    return db.scalar(stmt) is not None


def email_taken(db: Session, email: str, exclude_id: int | None = None) -> bool:
    """Emails are compared without letter case: Ann@X.com and ann@x.com are the same address."""
    stmt = select(User.id).where(func.lower(User.email) == email.strip().lower())
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    return db.scalar(stmt) is not None


def account_exists(db: Session, username: str, email: str) -> bool:
    """A new account clashes with an existing username or email in any letter case."""
    return username_taken(db, username, -1) or email_taken(db, email)


def _pending_request(db: Session, user_id: int) -> UsernameChangeRequest | None:
    return db.scalar(
        select(UsernameChangeRequest).where(
            UsernameChangeRequest.user_id == user_id, UsernameChangeRequest.status == STATUS_PENDING
        )
    )


@router.get("", response_model=ProfileOut)
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    locked = username_locked_until(user)
    pending = _pending_request(db, user.id)
    return ProfileOut(
        user=UserOut.model_validate(user),
        manager=UserBrief.model_validate(user.manager) if user.manager else None,
        can_change_username=locked is None,
        username_locked_until=locked,
        pending_username_request=UsernameRequestOut.model_validate(pending) if pending else None,
    )


@router.patch("", response_model=UserOut)
def update_profile(data: ProfileUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if data.full_name:
        user.full_name = data.full_name
    if data.email and data.email != user.email:
        if email_taken(db, data.email, user.id):
            raise HTTPException(status.HTTP_409_CONFLICT, "That email is already used by another account")
        user.email = data.email
    db.commit()
    return user


@router.patch("/preferences", response_model=UserOut)
def update_preferences(data: PreferencesUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if data.show_tour is not None:
        user.show_tour = data.show_tour
    if data.theme is not None:
        user.theme = data.theme
    if data.ask_account is not None:
        user.ask_account = data.ask_account
    db.commit()
    return user


@router.post("/otp", response_model=OtpSentOut)
def send_change_code(data: OtpRequestIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Email the code a password change or a username change (or request) needs."""
    wait = otp.resend_wait_seconds(db, user.id, data.purpose)
    if wait:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Wait {wait} second(s) before asking for another code.")
    expires, sent = otp.issue(db, user, data.purpose)
    otp.require_sent(sent)
    return OtpSentOut(message=f"We emailed a code to {user.email}.", expires_at=expires, expires_in_seconds=otp.seconds_left(expires))


@router.post("/password", response_model=TokenOut)
def change_password(data: PasswordChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Needs the current password and the emailed code. Signs out every other device; the response carries a new
    token for this one."""
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    otp.consume(db, user, OTP_PASSWORD, data.otp)
    user.password_hash = hash_password(data.new_password)
    user.token_version += 1
    db.commit()
    return TokenOut(access_token=create_access_token(user.id, user.token_version))


@router.post("/username", response_model=UserOut)
def change_username(data: UsernameChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Self-service username change, allowed once every USERNAME_LOCK_DAYS days."""
    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password is incorrect")
    locked = username_locked_until(user)
    if locked:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"You changed your username recently. You can change it again on {locked:%d %b %Y %H:%M}, "
            "or raise a request to the admin.",
        )
    if data.new_username == user.username:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That is already your username")
    if username_taken(db, data.new_username, user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "That username is already taken")
    otp.consume(db, user, OTP_USERNAME, data.otp)
    user.username = data.new_username
    user.username_changed_at = now_local()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That username is already taken")
    return user


@router.get("/username-requests", response_model=list[UsernameRequestOut])
def my_username_requests(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = (
        select(UsernameChangeRequest)
        .where(UsernameChangeRequest.user_id == user.id)
        .order_by(UsernameChangeRequest.created_at.desc())
    )
    return db.scalars(stmt).all()


@router.post("/username-requests", response_model=UsernameRequestOut, status_code=201)
def request_username_change(
    data: UsernameRequestIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """While the 15-day lock is active the user asks the admin to change the username for them."""
    if not username_locked_until(user):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can change your username yourself right now")
    if _pending_request(db, user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a pending username request")
    if data.new_username == user.username:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That is already your username")
    if username_taken(db, data.new_username, user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "That username is already taken")
    otp.consume(db, user, OTP_USERNAME, data.otp)
    req = UsernameChangeRequest(
        user_id=user.id, current_username=user.username, requested_username=data.new_username, reason=data.reason
    )
    db.add(req)
    for admin_id in db.scalars(select(User.id).where(User.role == ROLE_ADMIN)):
        db.add(
            Notification(
                user_id=admin_id,
                kind="USERNAME_REQUEST",
                message=f"{user.username} requested a username change to '{data.new_username}'",
            )
        )
    db.commit()
    return req


@router.delete("/username-requests/{request_id}", status_code=204)
def cancel_username_request(request_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    req = db.get(UsernameChangeRequest, request_id)
    if not req or req.user_id != user.id or req.status != STATUS_PENDING:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pending request not found")
    db.delete(req)
    db.commit()


@router.put("/avatar", response_model=UserOut)
async def upload_avatar(
    file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please upload a JPG, PNG, WEBP or GIF image")
    content = await file.read()
    if len(content) > settings.max_avatar_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Image is too large (max 2 MB)")
    avatar = db.get(UserAvatar, user.id)
    now = now_local()
    if avatar:
        avatar.image, avatar.mime_type, avatar.updated_at = content, file.content_type, now
    else:
        db.add(UserAvatar(user_id=user.id, image=content, mime_type=file.content_type, updated_at=now))
    user.avatar_updated_at = now
    db.commit()
    return user


@router.delete("/avatar", response_model=UserOut)
def remove_avatar(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    avatar = db.get(UserAvatar, user.id)
    if avatar:
        db.delete(avatar)
    user.avatar_updated_at = None
    db.commit()
    return user


@router.get("/avatar/{user_id}")
def get_avatar(user_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    avatar = db.get(UserAvatar, user_id)
    if not avatar:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No profile photo")
    return Response(avatar.image, media_type=avatar.mime_type, headers={"Cache-Control": "private, max-age=86400"})


# ---------- custom background ----------
@router.put("/background", response_model=UserOut)
async def upload_background(
    file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """A picture shown behind the app for this user. Settings → Appearance → Reset goes back to the default."""
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please upload a JPG, PNG, WEBP or GIF image")
    content = await file.read()
    if len(content) > settings.max_background_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Image is too large (max {settings.max_background_bytes // 1_000_000} MB)")
    bg = db.get(UserBackground, user.id)
    now = now_local()
    if bg:
        bg.image, bg.mime_type, bg.updated_at = content, file.content_type, now
    else:
        db.add(UserBackground(user_id=user.id, image=content, mime_type=file.content_type, updated_at=now))
    user.background_updated_at = now
    db.commit()
    return user


@router.delete("/background", response_model=UserOut)
def reset_background(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bg = db.get(UserBackground, user.id)
    if bg:
        db.delete(bg)
    user.background_updated_at = None
    db.commit()
    return user


@router.get("/background")
def get_background(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bg = db.get(UserBackground, user.id)
    if not bg:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No background image")
    return Response(bg.image, media_type=bg.mime_type, headers={"Cache-Control": "private, max-age=86400"})
