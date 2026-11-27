"""Admin sign-in rules (email code at sign-in, app passcode) and the sign-in log only admins see."""

from app.models import LoginEvent, User
from app.routers import passcode as passcode_router

from .conftest import PASSWORD, headers

PHONE_UA = ("Mozilla/5.0 (Linux; Android 14; SM-S918B Build/UP1A.231005.007; wv) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Version/4.0 Chrome/131.0.6778.135 Mobile Safari/537.36")
PC_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def _login(client, username, password=PASSWORD, **hdrs):
    return client.post("/api/auth/login", data={"username": username, "password": password}, headers=hdrs)


def _policy(client, admin, **flags):
    body = {"login_email_otp": False, "passcode_required": False, **flags}
    r = client.put("/api/admin/settings/security", json=body, headers=headers(admin))
    assert r.status_code == 200, r.text
    return r.json()


def test_only_admins_change_the_rules(client, make_user):
    user = make_user("asha")
    assert client.put("/api/admin/settings/security", json={"login_email_otp": True, "passcode_required": True},
                      headers=headers(user)).status_code == 403
    assert client.get("/api/auth/security-policy").json() == {"login_email_otp": False, "passcode_required": False}


def test_email_code_at_sign_in(client, make_user, outbox):
    admin, user = make_user("boss", admin=True), make_user("asha")
    _policy(client, admin, login_email_otp=True)
    res = _login(client, "asha").json()
    assert res["email_code_required"] and not res["access_token"] and res["email_hint"].endswith("@example.com")
    assert outbox[-1][0] == "asha@example.com" and "sign in" in outbox[-1][1]

    wrong = "000000" if outbox.last_code() != "000000" else "111111"
    bad = client.post("/api/auth/login/email-code", json={"email_token": res["email_token"], "code": wrong})
    assert bad.status_code == 400
    ok = client.post("/api/auth/login/email-code", json={"email_token": res["email_token"], "code": outbox.last_code()})
    assert ok.status_code == 200 and ok.json()["access_token"]

    # Turned off again: the password is enough
    _policy(client, admin, login_email_otp=False)
    assert _login(client, "asha").json()["access_token"]


def test_email_code_needs_mail_in_production(client, make_user, monkeypatch):
    from app.config import settings
    admin = make_user("boss", admin=True)
    monkeypatch.setattr(settings, "environment", "production")
    r = client.put("/api/admin/settings/security", json={"login_email_otp": True, "passcode_required": False}, headers=headers(admin))
    assert r.status_code == 400 and "SMTP" in r.json()["detail"]


def test_passcode(client, db, make_user):
    passcode_router.passcode_limiter.reset()
    admin, user = make_user("boss", admin=True), make_user("asha")
    _policy(client, admin, passcode_required=True)
    assert client.get("/api/profile/passcode", headers=headers(user)).json() == {"required": True, "set": False}
    assert client.put("/api/profile/passcode", json={"passcode": "1234"}, headers=headers(user)).status_code == 400
    assert client.put("/api/profile/passcode", json={"passcode": "12a4"}, headers=headers(user)).status_code == 422
    assert client.put("/api/profile/passcode", json={"passcode": "4826"}, headers=headers(user)).status_code == 200
    assert client.get("/api/auth/me", headers=headers(user)).json()["passcode_set"] is True
    # Changing it needs the password
    assert client.put("/api/profile/passcode", json={"passcode": "7391"}, headers=headers(user)).status_code == 400
    assert client.put("/api/profile/passcode", json={"passcode": "7391", "password": PASSWORD}, headers=headers(user)).status_code == 200

    assert client.post("/api/profile/passcode/verify", json={"passcode": "7391"}, headers=headers(user)).status_code == 200
    for _ in range(4):
        assert client.post("/api/profile/passcode/verify", json={"passcode": "0000"}, headers=headers(user)).status_code == 400
    # The fifth wrong one signs them out everywhere
    old = headers(user)
    assert client.post("/api/profile/passcode/verify", json={"passcode": "0000"}, headers=old).status_code == 401
    assert client.get("/api/auth/me", headers=old).status_code == 401


def test_sign_in_log_and_devices(client, db, make_user):
    admin, user = make_user("boss", admin=True), make_user("asha")
    assert _login(client, "asha", **{"User-Agent": PHONE_UA, "X-Client": "android-app/1.6", "X-Forwarded-For": "49.36.1.2, 10.0.0.1"}).status_code == 200
    assert _login(client, "asha", "wrong-password", **{"User-Agent": PC_UA}).status_code == 401
    assert _login(client, "asha", **{"User-Agent": PC_UA}).status_code == 200

    log = client.get("/api/admin/logins", params={"user_id": user.id}, headers=headers(admin)).json()
    assert [e["result"] for e in log] == ["SUCCESS", "FAILED", "SUCCESS"]
    phone = log[-1]
    assert (phone["ip"], phone["platform"], phone["app_version"], phone["os"]) == ("49.36.1.2", "APP", "1.6", "Android 14")
    assert phone["device"] == "Samsung SM-S918B" and log[1]["detail"] == "Wrong password"
    assert log[0]["device"] == "Windows PC" and log[0]["browser"] == "Chrome 131"

    devices = client.get(f"/api/admin/users/{user.id}/devices", headers=headers(admin)).json()
    assert len(devices) == 2 and {d["platform"] for d in devices} == {"APP", "WEB"}

    users = {u["username"]: u for u in client.get("/api/admin/users", headers=headers(admin)).json()}
    assert users["asha"]["last_login_device"] == "Windows PC · Chrome 131"

    # Users can't see it, and it goes when the account is deleted
    assert client.get("/api/admin/logins", headers=headers(user)).status_code == 403
    assert client.delete(f"/api/admin/users/{user.id}", headers=headers(admin)).status_code == 200
    assert not db.query(LoginEvent).filter_by(user_id=user.id).count()
