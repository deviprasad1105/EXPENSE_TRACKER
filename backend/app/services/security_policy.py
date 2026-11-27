"""App-wide sign-in rules the admin turns on and off (Admin -> Settings -> Sign-in security), and the sign-in log
that only admins see (Admin -> Sign-ins).

- Email code at sign-in: after the password (and the authenticator code, for users who turned that on), a 6-digit
  code is emailed and must be entered before the session starts.
- Passcode: every user chooses a 4-6 digit passcode, asked whenever the app or site is opened on a device where they
  are already signed in (and after it has been in the background for a while).
"""

import re
from datetime import timedelta

from fastapi import Request
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..config import settings
from ..models import LOGIN_FAILED, LOGIN_OK, AppSetting, LoginEvent, User
from ..timeutil import now_local

KEY_LOGIN_EMAIL_OTP = "login_email_otp"
KEY_PASSCODE = "passcode_required"
# Sign-in log rows older than this are removed when the user next signs in
LOGIN_EVENT_DAYS = 180
# Wrong passcodes in a row before the user is signed out everywhere and must use their password
PASSCODE_MAX_FAILURES = 5


def _flag(db: Session, key: str) -> bool:
    row = db.get(AppSetting, key)
    return bool(row and row.value == "1")


def _set_flag(db: Session, key: str, on: bool, actor: str) -> None:
    now, value = now_local(), "1" if on else "0"
    row = db.get(AppSetting, key)
    if row:
        row.value, row.updated_at, row.updated_by = value, now, actor
    else:
        db.add(AppSetting(key=key, value=value, updated_at=now, updated_by=actor))


def login_email_otp(db: Session) -> bool:
    return _flag(db, KEY_LOGIN_EMAIL_OTP)


def passcode_required(db: Session) -> bool:
    return _flag(db, KEY_PASSCODE)


def policy(db: Session) -> dict:
    return {"login_email_otp": login_email_otp(db), "passcode_required": passcode_required(db)}


def set_policy(db: Session, *, login_email_otp: bool, passcode_required: bool, actor: str) -> None:
    _set_flag(db, KEY_LOGIN_EMAIL_OTP, login_email_otp, actor)
    _set_flag(db, KEY_PASSCODE, passcode_required, actor)


def email_can_be_sent() -> bool:
    """In production codes must really reach people: without SMTP they would only be written to the outbox folder."""
    return bool(settings.smtp_host) or not settings.is_production


def mask_email(email: str) -> str:
    """d*******d@gmail.com: enough to recognise the address on the sign-in page without showing it to anyone."""
    name, _, domain = email.partition("@")
    if len(name) <= 2:
        return f"{name[:1]}*@{domain}"
    return f"{name[0]}{'*' * min(len(name) - 2, 8)}{name[-1]}@{domain}"


# ---------- sign-in log ----------
def client_ip(request: Request | None) -> str | None:
    """The visitor's address. Behind Vercel's proxy it is the first X-Forwarded-For entry (Vercel sets that header
    itself, so a visitor can't fake it there); run directly, it is the connection's address."""
    if request is None:
        return None
    for header in ("x-real-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()[:45]
    return request.client.host[:45] if request.client else None


_ANDROID = re.compile(r"Android\s+([\d.]+)(?:;\s*([^;)]+?))?(?:\s+Build/[^;)]*)?[;)]")
_IOS = re.compile(r"(iPhone|iPad|iPod).*?OS\s+([\d_]+)")
_WINDOWS = {"10.0": "10/11", "6.3": "8.1", "6.2": "8", "6.1": "7"}
_BROWSERS = (
    ("Edge", re.compile(r"Edg(?:A|iOS)?/(\d+)")),
    ("Opera", re.compile(r"(?:OPR|Opera)/(\d+)")),
    ("Samsung Internet", re.compile(r"SamsungBrowser/(\d+)")),
    ("Firefox", re.compile(r"(?:Firefox|FxiOS)/(\d+)")),
    ("Chrome", re.compile(r"(?:Chrome|CriOS)/(\d+)")),
    ("Safari", re.compile(r"Version/(\d+).*Safari/")),
)
# Model codes phones report, and the maker they belong to
_MAKERS = (
    ("Samsung", re.compile(r"^(SM-|GT-|SAMSUNG)", re.I)),
    ("Xiaomi", re.compile(r"^(Redmi|POCO|Mi |M2\d|2\d{3}|Xiaomi)", re.I)),
    ("OnePlus", re.compile(r"^(ONEPLUS|CPH25|CPH24|NE2|LE2|KB2|IN20)", re.I)),
    ("Oppo", re.compile(r"^(CPH|OPPO|PH[A-Z]M)", re.I)),
    ("Vivo", re.compile(r"^(vivo|V2\d{3}|I2\d{3})", re.I)),
    ("Realme", re.compile(r"^(RMX|realme)", re.I)),
    ("Google", re.compile(r"^Pixel", re.I)),
    ("Motorola", re.compile(r"^(moto|motorola|XT\d)", re.I)),
    ("Nothing", re.compile(r"^(A0\d{2}|Nothing)", re.I)),
)


def describe(user_agent: str | None) -> dict:
    """{device, os, browser} read from a user agent, e.g. Samsung SM-S918B / Android 14 / Chrome 131."""
    ua = user_agent or ""
    os_name = browser = device = None
    if m := _ANDROID.search(ua):
        os_name = f"Android {m.group(1)}"
        model = (m.group(2) or "").strip()
        if model and model.upper() not in ("K", "U", "WV", "MOBILE"):
            maker = next((name for name, rx in _MAKERS if rx.search(model)), None)
            device = f"{maker} {model}" if maker and not model.lower().startswith(maker.lower()) else model
        else:
            device = "Android phone"
    elif m := _IOS.search(ua):
        device, os_name = m.group(1), f"iOS {m.group(2).replace('_', '.')}"
    elif m := re.search(r"Windows NT ([\d.]+)", ua):
        device, os_name = "Windows PC", f"Windows {_WINDOWS.get(m.group(1), m.group(1))}"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        device, os_name = "Mac", "macOS"
    elif "CrOS" in ua:
        device, os_name = "Chromebook", "ChromeOS"
    elif "Linux" in ua:
        device, os_name = "Linux PC", "Linux"
    for name, rx in _BROWSERS:
        if m := rx.search(ua):
            browser = f"{name} {m.group(1)}"
            break
    if "; wv)" in ua and browser and browser.startswith("Chrome"):
        browser = "Android WebView"
    return {"device": device and device[:120], "os": os_name and os_name[:40], "browser": browser and browser[:40]}


def record_login(db: Session, user: User, request: Request | None, *, ok: bool, detail: str | None = None) -> None:
    """Add a row to the sign-in log (the caller commits). Never shown to the user, only to admins."""
    headers = request.headers if request is not None else {}
    ua = headers.get("user-agent")
    client = headers.get("x-client") or ""  # "android-app/1.6" from the app (frontend/src/api.js)
    is_app = client.startswith("android-app")
    now = now_local()
    db.add(LoginEvent(
        user_id=user.id, created_at=now, result=LOGIN_OK if ok else LOGIN_FAILED, detail=detail and detail[:60],
        ip=client_ip(request), platform="APP" if is_app else "WEB",
        app_version=client.partition("/")[2][:20] or None if is_app else None,
        user_agent=ua and ua[:400], **describe(ua),
    ))
    if ok:
        db.execute(delete(LoginEvent).where(LoginEvent.user_id == user.id,
                                            LoginEvent.created_at < now - timedelta(days=LOGIN_EVENT_DAYS)))
