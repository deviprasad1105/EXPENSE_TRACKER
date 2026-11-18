"""Two-step sign-in with an authenticator app."""

import time

import pytest

from app.config import settings
from app.routers import mfa as mfa_router
from app.services import mfa

from .conftest import PASSWORD, headers


@pytest.fixture(autouse=True)
def _no_email(monkeypatch):
    sent = []
    monkeypatch.setattr("app.routers.mfa.send_email", lambda to, subject, body: sent.append((to, subject)) or True)
    monkeypatch.setattr("app.routers.admin.send_email", lambda to, subject, body: sent.append((to, subject)) or True)
    mfa_router.mfa_limiter.reset()
    return sent


def _login(client, name="asha", password=PASSWORD):
    return client.post("/api/auth/login", data={"username": name, "password": password})


def _turn_on(client, user):
    """Set up two-step sign-in like the Settings page does. Returns (secret, recovery codes)."""
    setup = client.post("/api/profile/mfa/setup", json={"password": PASSWORD}, headers=headers(user))
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/Expense%20Tracker%3Aasha?secret=" + secret)
    on = client.post("/api/profile/mfa/enable", json={"code": mfa.code_for(secret)}, headers=headers(user))
    assert on.status_code == 200, on.text
    return secret, on.json()["recovery_codes"]


def _next_code(secret):
    """A code for the next time step (the current one is used up by the call before)."""
    return mfa.code_for(secret, time.time() + mfa.PERIOD)


def test_totp_matches_rfc_6238():
    # RFC 6238 test key "12345678901234567890", SHA-1, at 59 s: 94287082 (8 digits), so 287082 for 6
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert mfa.code_for(secret, 59) == "287082"
    assert mfa.code_for(secret, 1111111109) == "081804"


def test_off_by_default_and_password_alone_signs_in(client, make_user):
    user = make_user("asha")
    assert client.get("/api/profile/mfa", headers=headers(user)).json() == {"enabled": False, "enabled_at": None, "recovery_codes_left": 0}
    res = _login(client)
    assert res.status_code == 200 and res.json()["access_token"] and not res.json()["mfa_required"]


def test_turn_on_then_sign_in_needs_the_code(client, make_user, _no_email):
    user = make_user("asha")
    secret, codes = _turn_on(client, user)
    assert len(codes) == 10 and len(set(codes)) == 10
    assert client.get("/api/auth/me", headers=headers(user)).json()["mfa_enabled"] is True
    assert ("asha@example.com", "Expense Tracker: two-step sign-in is on") in _no_email

    first = _login(client)
    body = first.json()
    assert first.status_code == 200 and body["mfa_required"] and body["access_token"] is None
    # The ticket is not a session
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['mfa_token']}"}).status_code == 401

    wrong = client.post("/api/auth/login/mfa", json={"mfa_token": body["mfa_token"], "code": "000000" if _next_code(secret) != "000000" else "111111"})
    assert wrong.status_code == 401
    ok = client.post("/api/auth/login/mfa", json={"mfa_token": body["mfa_token"], "code": _next_code(secret)})
    assert ok.status_code == 200, ok.text
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"}).status_code == 200


def test_a_code_works_only_once(client, make_user):
    user = make_user("asha")
    secret, _ = _turn_on(client, user)
    code = _next_code(secret)
    ticket = _login(client).json()["mfa_token"]
    assert client.post("/api/auth/login/mfa", json={"mfa_token": ticket, "code": code}).status_code == 200
    ticket = _login(client).json()["mfa_token"]
    assert client.post("/api/auth/login/mfa", json={"mfa_token": ticket, "code": code}).status_code == 401


def test_recovery_code_signs_in_once(client, make_user, db):
    user = make_user("asha")
    _, codes = _turn_on(client, user)
    ticket = _login(client).json()["mfa_token"]
    assert client.post("/api/auth/login/mfa", json={"mfa_token": ticket, "code": codes[0].upper()}).status_code == 200
    ticket = _login(client).json()["mfa_token"]
    assert client.post("/api/auth/login/mfa", json={"mfa_token": ticket, "code": codes[0]}).status_code == 401
    assert client.get("/api/profile/mfa", headers=headers(user)).json()["recovery_codes_left"] == 9


def test_wrong_codes_block_the_account(client, make_user, db):
    user = make_user("asha")
    secret, _ = _turn_on(client, user)
    ticket = _login(client).json()["mfa_token"]
    for _ in range(settings.login_max_failures):
        assert client.post("/api/auth/login/mfa", json={"mfa_token": ticket, "code": "12345x"}).status_code == 401
    res = client.post("/api/auth/login/mfa", json={"mfa_token": ticket, "code": _next_code(secret)})
    assert res.status_code == 429
    db.refresh(user)
    assert user.login_blocked_until is not None


def test_ticket_stops_working_after_sign_out_everywhere(client, make_user):
    user = make_user("asha")
    secret, _ = _turn_on(client, user)
    ticket = _login(client).json()["mfa_token"]
    client.post("/api/auth/logout-all", headers=headers(user))
    assert client.post("/api/auth/login/mfa", json={"mfa_token": ticket, "code": _next_code(secret)}).status_code == 401


def test_setup_needs_the_password_and_a_right_code(client, make_user):
    user = make_user("asha")
    assert client.post("/api/profile/mfa/setup", json={"password": "wrong"}, headers=headers(user)).status_code == 400
    assert client.post("/api/profile/mfa/enable", json={"code": "123456"}, headers=headers(user)).status_code == 400
    secret = client.post("/api/profile/mfa/setup", json={"password": PASSWORD}, headers=headers(user)).json()["secret"]
    bad = "000000" if mfa.code_for(secret) != "000000" else "111111"
    assert client.post("/api/profile/mfa/enable", json={"code": bad}, headers=headers(user)).status_code == 400
    # Still off: the password alone signs in
    assert _login(client).json()["access_token"]


def test_secret_is_stored_encrypted(client, make_user, db):
    user = make_user("asha")
    secret, codes = _turn_on(client, user)
    db.refresh(user)
    assert secret not in user.mfa_secret and mfa.unseal(user.mfa_secret) == secret
    assert all(c not in user.mfa_recovery_hashes for c in codes)


def test_turn_off_needs_password_and_code(client, make_user):
    user = make_user("asha")
    secret, _ = _turn_on(client, user)
    h = headers(user)
    assert client.post("/api/profile/mfa/disable", json={"password": "wrong", "code": _next_code(secret)}, headers=h).status_code == 400
    assert client.post("/api/profile/mfa/disable", json={"password": PASSWORD, "code": "000001"}, headers=h).status_code == 400
    off = client.post("/api/profile/mfa/disable", json={"password": PASSWORD, "code": _next_code(secret)}, headers=h)
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert _login(client).json()["access_token"]


def test_new_recovery_codes_replace_the_old_ones(client, make_user):
    user = make_user("asha")
    secret, old = _turn_on(client, user)
    res = client.post("/api/profile/mfa/recovery-codes", json={"password": PASSWORD, "code": _next_code(secret)}, headers=headers(user))
    assert res.status_code == 200
    new = res.json()["recovery_codes"]
    assert not set(new) & set(old)
    ticket = _login(client).json()["mfa_token"]
    assert client.post("/api/auth/login/mfa", json={"mfa_token": ticket, "code": old[0]}).status_code == 401
    assert client.post("/api/auth/login/mfa", json={"mfa_token": ticket, "code": new[0]}).status_code == 200


def test_admin_can_reset_it(client, make_user, _no_email):
    admin = make_user("boss", admin=True)
    user = make_user("asha")
    _turn_on(client, user)
    old_session = headers(user)
    asha = make_user("other")
    assert client.post(f"/api/admin/users/{user.id}/mfa/reset", json={}, headers=headers(asha)).status_code == 403
    res = client.post(f"/api/admin/users/{user.id}/mfa/reset", json={"reason": "Lost phone"}, headers=headers(admin))
    assert res.status_code == 200 and res.json()["mfa_enabled"] is False
    assert client.get("/api/auth/me", headers=old_session).status_code == 401
    assert _login(client).json()["access_token"]
    assert ("asha@example.com", "Expense Tracker: two-step sign-in was turned off") in _no_email
