"""Two-step sign-in with an authenticator app (Google Authenticator, Microsoft Authenticator, Authy, 1Password…).

The app and the server share a secret key. Every 30 seconds both turn it and the current time into the same 6-digit
code (TOTP, RFC 6238), so the code proves the person signing in has the phone, without SMS or email.

- The secret is stored encrypted with a key derived from MFA_KEY (or JWT_SECRET when that is empty), so a copy of the
  database alone can't produce codes. Changing that key turns two-step sign-in off for everyone who had it.
- A code works once: the last time step used is kept, so a code seen over someone's shoulder can't be reused.
- Recovery codes (for a lost phone) are stored as hashes and each works once.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings
from ..models import User

ISSUER = "Expense Tracker"
DIGITS = 6
PERIOD = 30
# Codes from one step either side are accepted: the phone's clock may be a little off
WINDOW = 1
RECOVERY_CODES = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


# ---------- the secret, encrypted at rest ----------
def _fernet() -> Fernet:
    key = (settings.mfa_key or settings.jwt_secret).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"expense-tracker-mfa:" + key).digest()))


def new_secret() -> str:
    """A random 160-bit key in base32, the form authenticator apps take."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def seal(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def unseal(sealed: str | None) -> str | None:
    if not sealed:
        return None
    try:
        return _fernet().decrypt(sealed.encode()).decode()
    except InvalidToken:
        return None


def otpauth_uri(secret: str, account: str) -> str:
    """What the QR code holds: authenticator apps add the account from it."""
    label = quote(f"{ISSUER}:{account}")
    query = urlencode({"secret": secret, "issuer": ISSUER, "algorithm": "SHA1", "digits": DIGITS, "period": PERIOD})
    return f"otpauth://totp/{label}?{query}"


# ---------- codes ----------
def _code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // PERIOD)


def code_for(secret: str, now: float | None = None) -> str:
    """The code the authenticator app shows right now (used by tests)."""
    return _code_at(secret, current_step(now))


def clean_code(code: str | None) -> str:
    return "".join((code or "").split()).replace("-", "").lower()


def matching_step(secret: str, code: str | None, after_step: int | None = None, now: float | None = None) -> int | None:
    """The time step the code belongs to, or None. Steps at or before `after_step` were already used."""
    code = clean_code(code)
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for s in range(step - WINDOW, step + WINDOW + 1):
        if after_step is not None and s <= after_step:
            continue
        if hmac.compare_digest(_code_at(secret, s), code):
            return s
    return None


# ---------- recovery codes ----------
def _hash_recovery(user_id: int, code: str) -> str:
    return hmac.new((settings.mfa_key or settings.jwt_secret).encode(), f"{user_id}:{clean_code(code)}".encode(), hashlib.sha256).hexdigest()


def new_recovery_codes(user: User) -> list[str]:
    """Make a fresh set (older ones stop working). Returns the codes to show once; only hashes are kept."""
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    user.mfa_recovery_hashes = ",".join(_hash_recovery(user.id, c) for c in codes)
    return codes


def recovery_left(user: User) -> int:
    return len([h for h in (user.mfa_recovery_hashes or "").split(",") if h])


def use_recovery_code(user: User, code: str | None) -> bool:
    """True (and the code is used up) when it is one of the user's unused recovery codes."""
    if len(clean_code(code)) != 10:
        return False
    target = _hash_recovery(user.id, code or "")
    hashes = [h for h in (user.mfa_recovery_hashes or "").split(",") if h]
    for h in hashes:
        if hmac.compare_digest(h, target):
            hashes.remove(h)
            user.mfa_recovery_hashes = ",".join(hashes)
            return True
    return False


# ---------- checking a code at sign-in or for a change ----------
def verify(user: User, code: str | None, *, allow_recovery: bool = True) -> str | None:
    """Check a code from the authenticator app (or a recovery code). Returns "TOTP" / "RECOVERY", or None.
    The caller commits: the used step / recovery code is saved then."""
    secret = unseal(user.mfa_secret)
    if secret:
        step = matching_step(secret, code, user.mfa_last_step)
        if step is not None:
            user.mfa_last_step = step
            return "TOTP"
    if allow_recovery and use_recovery_code(user, code):
        return "RECOVERY"
    return None


def turn_off(user: User) -> None:
    user.mfa_enabled = False
    user.mfa_secret = user.mfa_pending_secret = user.mfa_recovery_hashes = None
    user.mfa_enabled_at = user.mfa_last_step = None
