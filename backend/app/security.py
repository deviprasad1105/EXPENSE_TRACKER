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
        return int(payload["sub"]), int(payload.get("ver", 0))
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
