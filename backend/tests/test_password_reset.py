"""Forgot password: an emailed temporary password signs in once, then a new password must be chosen."""

import re
import time
from datetime import timedelta

import pytest

from app.models import PasswordReset
from app.services import mfa

from .conftest import PASSWORD, headers

NEW = "brand-new-pass-1"


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr("app.routers.auth.send_email", lambda to, subject, body: sent.append((to, subject, body)) or True)
    monkeypatch.setattr("app.routers.mfa.send_email", lambda to, subject, body: True)
    return sent


def _forgot(client, mails, name="asha"):
    assert client.post("/api/auth/forgot-password", json={"username": name}).status_code == 200
    body = mails[-1][2]
    return re.search(r"Temporary password: (\S+)", body).group(1)


def _login(client, password, name="asha"):
    return client.post("/api/auth/login", data={"username": name, "password": password})


def test_email_has_a_temporary_password_and_no_link(client, make_user, mails):
    make_user("asha")
    temp = _forgot(client, mails)
    to, subject, body = mails[-1]
    assert to == "asha@example.com" and len(temp) == 10
    assert "http" not in body and "reset-password" not in body


def test_temporary_password_signs_in_then_a_new_password_is_required(client, make_user, mails):
    user = make_user("asha")
    temp = _forgot(client, mails)
    res = _login(client, temp).json()
    assert res["password_change_required"] and res["access_token"] is None
    # The ticket is not a session
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {res['password_token']}"}).status_code == 401

    same = client.post("/api/auth/login/new-password", json={"password_token": res["password_token"], "new_password": temp})
    assert same.status_code == 400
    ok = client.post("/api/auth/login/new-password", json={"password_token": res["password_token"], "new_password": NEW})
    assert ok.status_code == 200, ok.text
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"}).status_code == 200
    # Old sessions end; the temporary and the old password stop working; the new one works
    assert client.get("/api/auth/me", headers=headers(user)).status_code == 401
    assert _login(client, temp).status_code == 401
    assert _login(client, PASSWORD).status_code == 401
    assert _login(client, NEW).json()["access_token"]
    assert mails[-1][1] == "Expense Tracker: your password was changed"


def test_old_password_still_works_and_expires_the_temporary_one(client, make_user, mails):
    make_user("asha")
    temp = _forgot(client, mails)
    assert _login(client, PASSWORD).json()["access_token"]
    assert _login(client, temp).status_code == 401


def test_temporary_password_works_for_one_hour(client, make_user, mails, db):
    make_user("asha")
    temp = _forgot(client, mails)
    reset = db.query(PasswordReset).one()
    assert timedelta(minutes=59) < reset.expires_at - reset.created_at <= timedelta(minutes=60)
    reset.expires_at = reset.created_at  # an hour later
    db.commit()
    assert _login(client, temp).status_code == 401


def test_only_the_newest_temporary_password_works(client, make_user, mails, db):
    make_user("asha")
    first = _forgot(client, mails)
    second = _forgot(client, mails)
    assert _login(client, first).status_code == 401
    assert _login(client, second).json()["password_change_required"]


def test_ticket_stops_working_when_the_temporary_password_expires(client, make_user, mails):
    make_user("asha")
    temp = _forgot(client, mails)
    ticket = _login(client, temp).json()["password_token"]
    assert _login(client, PASSWORD).json()["access_token"]  # remembered it after all
    res = client.post("/api/auth/login/new-password", json={"password_token": ticket, "new_password": NEW})
    assert res.status_code == 401


def test_with_two_step_sign_in_the_code_comes_before_the_new_password(client, make_user, mails):
    user = make_user("asha")
    secret = client.post("/api/profile/mfa/setup", json={"password": PASSWORD}, headers=headers(user)).json()["secret"]
    assert client.post("/api/profile/mfa/enable", json={"code": mfa.code_for(secret)}, headers=headers(user)).status_code == 200
    temp = _forgot(client, mails)
    first = _login(client, temp).json()
    assert first["mfa_required"] and not first["password_change_required"]
    code = mfa.code_for(secret, time.time() + mfa.PERIOD)
    second = client.post("/api/auth/login/mfa", json={"mfa_token": first["mfa_token"], "code": code}).json()
    assert second["password_change_required"] and second["access_token"] is None
    ok = client.post("/api/auth/login/new-password", json={"password_token": second["password_token"], "new_password": NEW})
    assert ok.status_code == 200 and ok.json()["access_token"]


def test_wrong_passwords_count_towards_the_block(client, make_user, mails):
    from app.config import settings
    make_user("asha")
    temp = _forgot(client, mails)
    for _ in range(settings.login_max_failures):
        assert _login(client, "nope-nope").status_code == 401
    assert _login(client, temp).status_code == 429


# ---------- changing the password from Profile ----------
def test_profile_change_needs_the_app_code_when_two_step_is_on(client, make_user, mails):
    user = make_user("asha")
    h = headers(user)
    secret = client.post("/api/profile/mfa/setup", json={"password": PASSWORD}, headers=h).json()["secret"]
    client.post("/api/profile/mfa/enable", json={"code": mfa.code_for(secret)}, headers=h)
    body = {"current_password": PASSWORD, "new_password": NEW}
    code = mfa.code_for(secret, time.time() + mfa.PERIOD)
    wrong = "000000" if code != "000000" else "111111"
    assert client.post("/api/profile/password", json={**body, "otp": wrong}, headers=h).status_code == 400
    res = client.post("/api/profile/password", json={**body, "otp": code}, headers=h)
    assert res.status_code == 200, res.text
    assert _login(client, NEW).json()["mfa_required"]
