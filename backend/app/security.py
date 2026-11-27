from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import settings

ALGORITHM = "HS256"
# Checked against when the username doesn't exist, so a wrong username takes as long as a wrong password
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    return bcrypt.checkpw(password.encode(), (password_hash or _DUMMY_HASH).encode()) and password_hash is not None


def create_access_token(user_id: int, token_version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "ver": token_version, "iat": now, "exp": now + timedelta(minutes=settings.jwt_expire_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> tuple[int, int] | None:
    """(user id, token version) of a valid token. A token whose version no longer matches the user's is revoked."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        # A sign-in ticket (two-step code, new password after a temporary one) is never a session
        if payload.get("typ"):
            return None
        return int(payload["sub"]), int(payload.get("ver", 0))
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


# Sign-in tickets: proof of one step of signing in, sent back with the next step
MFA_TICKET = "mfa"            # the password (or temporary password) was right; the authenticator code comes next
EMAIL_CODE_TICKET = "mail"     # the earlier steps passed; the code emailed for this sign-in comes next (admin setting)
NEW_PASSWORD_TICKET = "newpw"  # signed in with a temporary password: a new password must be chosen next


def create_ticket(kind: str, user_id: int, token_version: int, minutes: int, **extra) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "ver": token_version, "typ": kind, "iat": now, "exp": now + timedelta(minutes=minutes), **extra}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_ticket(token: str, kind: str) -> dict | None:
    """The ticket's claims ("sub" and "ver" as ints), or None if it is invalid, expired or of another kind."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        if payload.get("typ") != kind:
            return None
        payload["sub"], payload["ver"] = int(payload["sub"]), int(payload.get("ver", 0))
        return payload
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
