import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import ORG_LOCKED_MESSAGE, accessible_users, can_write, get_current_user
from ..models import ROLE_ADMIN, STATUS_APPROVED, STATUS_PENDING, STATUS_REJECTED, Notification, PasswordReset, User
from ..ratelimit import RateLimiter
from ..schemas import AccessibleUser, ForgotPasswordIn, RegisterIn, ResetPasswordIn, TokenOut, UserOut
from ..security import create_access_token, hash_password, verify_password
from ..services.mailer import send_email
from ..timeutil import now_local

router = APIRouter(prefix="/api/auth", tags=["auth"])

login_limiter = RateLimiter(settings.login_ip_max_attempts, 60)
# Registration and forgot-password send notifications and emails, so they get a tighter per-IP limit
public_form_limiter = RateLimiter(5, 60)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/register", status_code=201)
def register(data: RegisterIn, request: Request, db: Session = Depends(get_db)):
    if not public_form_limiter.hit(f"register:{_client_ip(request)}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Wait a minute and try again.")
    exists = db.scalar(select(User.id).where(or_(User.username == data.username, User.email == data.email)))
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username or email already registered")

    manager_id = None
    if data.manager_username:
        manager = db.scalar(select(User).where(User.username == data.manager_username))
        if not manager or manager.status != STATUS_APPROVED:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Manager user not found")
        manager_id = manager.id

    user = User(
        username=data.username,
        email=data.email,
        full_name=data.full_name,
        password_hash=hash_password(data.password),
        status=STATUS_PENDING,
        manager_id=manager_id,
    )
    db.add(user)
    db.flush()

    for admin_id in db.scalars(select(User.id).where(User.role == ROLE_ADMIN)):
        db.add(Notification(user_id=admin_id, kind="APPROVAL", message=f"New user '{user.username}' is waiting for approval"))
    db.commit()
    return {"message": "Registration submitted. An admin must approve your account before you can log in."}


@router.post("/login", response_model=TokenOut)
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    if not login_limiter.hit(_client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many sign-in attempts. Wait a minute and try again.")
    user = db.scalar(select(User).where(User.username == form.username))
    now = now_local()
    if user and user.login_blocked_until and user.login_blocked_until > now:
        minutes = max(1, -(-int((user.login_blocked_until - now).total_seconds()) // 60))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many wrong passwords for this account. Try again in {minutes} minute(s) or reset your password.",
        )
    if not verify_password(form.password, user.password_hash if user else None):
        if user:
            _record_failed_login(db, user, request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    if user.status == STATUS_PENDING:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your account is waiting for admin approval")
    if user.status == STATUS_REJECTED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your registration was rejected by the admin")
    if user.is_locked:
        reason = f" Reason: {user.lock_reason}" if user.lock_reason else ""
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Your account has been locked by an administrator.{reason}")
    if user.org_locked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, ORG_LOCKED_MESSAGE)
    if user.failed_logins or user.login_blocked_until:
        user.failed_logins, user.login_blocked_until = 0, None
        db.commit()
    return TokenOut(access_token=create_access_token(user.id, user.token_version))


def _record_failed_login(db: Session, user: User, request: Request):
    user.failed_logins += 1
    if user.failed_logins >= settings.login_max_failures:
        user.failed_logins = 0
        user.login_blocked_until = now_local() + timedelta(minutes=settings.login_lock_minutes)
        db.add(
            Notification(
                user_id=user.id,
                kind="SECURITY",
                message=f"Sign-in was blocked for {settings.login_lock_minutes} minutes after "
                f"{settings.login_max_failures} wrong passwords (from {_client_ip(request)}). "
                "If this wasn't you, change your password.",
            )
        )
    db.commit()


@router.post("/logout-all", response_model=TokenOut)
def logout_everywhere(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Revoke every token of the caller (all devices). Returns a fresh token for the current device."""
    user.token_version += 1
    db.commit()
    return TokenOut(access_token=create_access_token(user.id, user.token_version))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get("/accessible-users", response_model=list[AccessibleUser])
def list_accessible_users(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Users whose data the caller can view (self, their whole team, users who shared with them; everyone for admin)."""
    return [
        AccessibleUser(
            id=u.id,
            username=u.username,
            full_name=u.full_name,
            access=level,
            writable=can_write(level),
            avatar_updated_at=u.avatar_updated_at,
        )
        for u, level in accessible_users(db, user)
    ]


# ---------- forgot password ----------
FORGOT_MESSAGE = (
    "If that username exists, we've emailed its owner a temporary password and a reset link. "
    "Check your inbox. The link expires soon."
)
MAX_TEMP_PASSWORD_ATTEMPTS = 5
_TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.post("/forgot-password")
def forgot_password(data: ForgotPasswordIn, request: Request, db: Session = Depends(get_db)):
    """Email a temporary password and a one-time reset link.

    The response is the same whether or not the username exists, so it can't be used to discover accounts.
    At most RESET_MAX_PER_WINDOW resets are issued per RESET_WINDOW_DAYS. Past that, the owner is told by email.
    """
    if not public_form_limiter.hit(f"forgot:{_client_ip(request)}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Wait a minute and try again.")
    user = db.scalar(select(User).where(User.username == data.username.strip()))
    if not user or user.status != STATUS_APPROVED or user.is_locked or user.org_locked:
        return {"message": FORGOT_MESSAGE}

    now = now_local()
    window_start = now - timedelta(days=settings.reset_window_days)
    recent = db.scalars(
        select(PasswordReset.created_at)
        .where(PasswordReset.user_id == user.id, PasswordReset.created_at >= window_start)
        .order_by(PasswordReset.created_at)
    ).all()
    if len(recent) >= settings.reset_max_per_window:
        next_allowed = recent[0] + timedelta(days=settings.reset_window_days)
        send_email(
            user.email,
            "Expense Tracker: password reset limit reached",
            f"Hi {user.full_name},\n\nSomeone asked to reset the password for '{user.username}', but the limit of "
            f"{settings.reset_max_per_window} password resets in {settings.reset_window_days} days has been reached.\n"
            f"You can request another reset after {next_allowed:%d %b %Y %H:%M}, or ask an administrator to reset it.\n\n"
            "If this wasn't you, you can ignore this email. Your password has not changed.\n",
        )
        return {"message": FORGOT_MESSAGE}

    # Only the newest reset link stays valid
    db.execute(
        update(PasswordReset)
        .where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None), PasswordReset.expires_at > now)
        .values(expires_at=now)
    )
    token = secrets.token_urlsafe(32)
    temp_password = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(10))
    expires = now + timedelta(minutes=settings.reset_token_minutes)
    db.add(
        PasswordReset(
            user_id=user.id,
            token_hash=_token_hash(token),
            temp_password_hash=hash_password(temp_password),
            created_at=now,
            expires_at=expires,
            requested_ip=request.client.host if request.client else None,
        )
    )
    db.commit()

    left = settings.reset_max_per_window - len(recent) - 1
    link = f"{settings.frontend_url.rstrip('/')}/reset-password?token={token}"
    send_email(
        user.email,
        "Expense Tracker: reset your password",
        f"Hi {user.full_name},\n\n"
        f"We received a request to reset the password for '{user.username}'.\n\n"
        f"Temporary password: {temp_password}\n\n"
        f"Open this link and enter the temporary password to choose a new password:\n{link}\n\n"
        f"The link and temporary password expire at {expires:%d %b %Y %H:%M} and work only once.\n"
        f"You can request {left} more reset(s) in the next {settings.reset_window_days} days.\n\n"
        "If you didn't ask for this, ignore this email. Your current password still works.\n",
    )
    return {"message": FORGOT_MESSAGE}


def _active_reset(db: Session, token: str) -> PasswordReset:
    reset = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == _token_hash(token)))
    if (
        not reset
        or reset.used_at
        or reset.expires_at <= now_local()
        or reset.user.status != STATUS_APPROVED
        or reset.user.is_locked
        or reset.user.org_locked
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired. Request a new one.")
    return reset


@router.get("/reset-password/{token}")
def check_reset_token(token: str, db: Session = Depends(get_db)):
    reset = _active_reset(db, token)
    return {"username": reset.user.username, "expires_at": reset.expires_at}


@router.post("/reset-password")
def reset_password(data: ResetPasswordIn, db: Session = Depends(get_db)):
    reset = _active_reset(db, data.token)
    if not verify_password(data.temp_password, reset.temp_password_hash):
        reset.failed_attempts += 1
        if reset.failed_attempts >= MAX_TEMP_PASSWORD_ATTEMPTS:
            reset.expires_at = now_local()
            db.commit()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Too many wrong attempts. This link is now disabled.")
        db.commit()
        left = MAX_TEMP_PASSWORD_ATTEMPTS - reset.failed_attempts
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Temporary password is incorrect ({left} attempt(s) left)")
    if data.new_password == data.temp_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a new password, not the temporary one")

    user = reset.user
    user.password_hash = hash_password(data.new_password)
    # Sign out every device, and clear any sign-in block from wrong passwords
    user.token_version += 1
    user.failed_logins, user.login_blocked_until = 0, None
    reset.used_at = now_local()
    db.add(Notification(user_id=user.id, kind="SECURITY", message="Your password was reset using a forgot-password link."))
    db.commit()
    send_email(
        user.email,
        "Expense Tracker: your password was changed",
        f"Hi {user.full_name},\n\nThe password for '{user.username}' was just changed using a reset link.\n"
        "If this wasn't you, contact your administrator immediately.\n",
    )
    return {"message": "Password changed. You can now sign in with your new password."}
