"""Email one-time codes: confirming the email at sign-up, and password / username changes."""

from datetime import timedelta

from app.models import EmailOtp, User

from .conftest import PASSWORD, headers

FORM = {"username": "newbie", "email": "newbie@example.com", "full_name": "New Bie", "password": "long-enough-1"}


def _register(client, **over):
    return client.post("/api/auth/register", json={**FORM, **over})


def _expire_codes(db):
    for o in db.query(EmailOtp).all():
        o.expires_at -= timedelta(hours=1)
        o.created_at -= timedelta(hours=1)
    db.commit()


def test_signup_needs_email_code_before_admin_can_approve(client, db, make_user, outbox):
    admin = make_user("boss", admin=True)
    res = _register(client)
    assert res.status_code == 201 and res.json()["expires_at"]
    assert outbox[-1][0] == "newbie@example.com"
    user = db.query(User).filter_by(username="newbie").one()
    assert user.email_verified is False

    # Not verified: can't sign in, and the admin can't approve (neither button nor PATCH)
    login = client.post("/api/auth/login", data={"username": "newbie", "password": "long-enough-1"})
    assert login.status_code == 403 and "email" in login.json()["detail"].lower()
    assert client.post(f"/api/admin/users/{user.id}/approve", headers=headers(admin)).status_code == 400
    assert client.patch(f"/api/admin/users/{user.id}", headers=headers(admin), json={"status": "APPROVED"}).status_code == 400

    bad = client.post("/api/auth/verify-email", json={"username": "newbie", "code": "000000" if outbox.last_code() != "000000" else "111111"})
    assert bad.status_code == 400 and "attempt" in bad.json()["detail"]
    ok = client.post("/api/auth/verify-email", json={"username": "newbie", "code": outbox.last_code()})
    assert ok.status_code == 200
    # The code works once
    assert client.post("/api/auth/verify-email", json={"username": "newbie", "code": outbox.last_code()}).status_code == 400

    login = client.post("/api/auth/login", data={"username": "newbie", "password": "long-enough-1"})
    assert login.status_code == 403 and "approval" in login.json()["detail"]
    assert client.post(f"/api/admin/users/{user.id}/approve", headers=headers(admin)).status_code == 200


def test_signup_code_expires_and_can_be_resent(client, db, outbox):
    _register(client)
    first = outbox.last_code()
    _expire_codes(db)
    res = client.post("/api/auth/verify-email", json={"username": "newbie", "code": first})
    assert res.status_code == 400 and "expired" in res.json()["detail"]

    assert client.post("/api/auth/resend-email-code", json={"username": "newbie"}).status_code == 200
    # Resending again straight away is refused
    assert client.post("/api/auth/resend-email-code", json={"username": "newbie"}).status_code == 429
    assert client.post("/api/auth/verify-email", json={"username": "newbie", "code": outbox.last_code()}).status_code == 200


def test_too_many_wrong_codes_disable_the_code(client, outbox):
    _register(client)
    good = outbox.last_code()
    wrong = "123456" if good != "123456" else "654321"
    for _ in range(5):
        client.post("/api/auth/verify-email", json={"username": "newbie", "code": wrong})
    assert client.post("/api/auth/verify-email", json={"username": "newbie", "code": good}).status_code == 400


def test_abandoned_signup_frees_username_after_code_expires(client, db, outbox):
    _register(client)
    assert _register(client, email="other@example.com").status_code == 409  # code still live
    _expire_codes(db)
    assert _register(client, email="other@example.com").status_code == 201
    assert db.query(User).filter_by(username="newbie").one().email == "other@example.com"


def test_admin_sets_code_validity(client, db, make_user, outbox):
    admin = make_user("boss", admin=True)
    assert client.get("/api/admin/settings/otp", headers=headers(admin)).json() == {"signup_minutes": 3, "account_minutes": 5}
    res = client.put("/api/admin/settings/otp", headers=headers(admin), json={"signup_minutes": 10, "account_minutes": 7})
    assert res.json() == {"signup_minutes": 10, "account_minutes": 7}
    assert client.put("/api/admin/settings/otp", headers=headers(admin), json={"signup_minutes": 0, "account_minutes": 7}).status_code == 422
    user = make_user("asha")
    assert client.get("/api/admin/settings/otp", headers=headers(user)).status_code == 403

    _register(client)
    otp = db.query(EmailOtp).one()
    assert round((otp.expires_at - otp.created_at).total_seconds()) == 600


def test_password_change_needs_emailed_code(client, make_user, outbox):
    user = make_user("asha")
    h = headers(user)
    body = {"current_password": PASSWORD, "new_password": "brand-new-password"}
    assert client.post("/api/profile/password", headers=h, json=body).status_code == 400
    res = client.post("/api/profile/otp", headers=h, json={"purpose": "PASSWORD"})
    assert res.status_code == 200 and outbox[-1][0] == "asha@example.com"
    # A wrong code is refused (and the right one still works after)
    assert client.post("/api/profile/password", headers=h, json={**body, "otp": "999999"}).status_code == 400
    assert client.post("/api/profile/password", headers=h, json={**body, "otp": outbox.last_code()}).status_code == 200


def test_username_change_and_request_need_emailed_code(client, db, make_user, outbox):
    user = make_user("asha")
    h = headers(user)
    body = {"new_username": "asha_new", "password": PASSWORD}
    assert client.post("/api/profile/username", headers=h, json=body).status_code == 400
    client.post("/api/profile/otp", headers=h, json={"purpose": "USERNAME"})
    assert client.post("/api/profile/username", headers=h, json={**body, "otp": outbox.last_code()}).status_code == 200

    # Now locked: asking the admin also needs a fresh code
    _expire_codes(db)
    req = {"new_username": "asha_again"}
    assert client.post("/api/profile/username-requests", headers=h, json=req).status_code == 400
    client.post("/api/profile/otp", headers=h, json={"purpose": "USERNAME"})
    assert client.post("/api/profile/username-requests", headers=h, json={**req, "otp": outbox.last_code()}).status_code == 201
