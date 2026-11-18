from app.config import settings

from .conftest import PASSWORD, headers


def login(client, username, password):
    return client.post("/api/auth/login", data={"username": username, "password": password})


def test_login_ok(client, make_user):
    make_user("asha")
    res = login(client, "asha", PASSWORD)
    assert res.status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {res.json()['access_token']}"}).status_code == 200


def test_account_blocked_after_repeated_wrong_passwords(client, make_user, db):
    user = make_user("asha")
    for _ in range(settings.login_max_failures):
        assert login(client, "asha", "wrong-password").status_code == 401
    # Even the right password is refused while blocked
    res = login(client, "asha", PASSWORD)
    assert res.status_code == 429
    assert "Too many wrong passwords" in res.json()["detail"]
    db.refresh(user)
    assert user.login_blocked_until is not None


def test_successful_login_resets_failure_count(client, make_user, db):
    user = make_user("asha")
    for _ in range(settings.login_max_failures - 1):
        login(client, "asha", "wrong-password")
    assert login(client, "asha", PASSWORD).status_code == 200
    db.refresh(user)
    assert user.failed_logins == 0


def test_ip_rate_limit(client, make_user):
    for _ in range(settings.login_ip_max_attempts):
        login(client, "nobody", "x")
    assert login(client, "nobody", "x").status_code == 429


def test_password_change_revokes_old_tokens(client, make_user, outbox):
    user = make_user("asha")
    old = headers(user)
    assert client.post("/api/profile/otp", headers=old, json={"purpose": "PASSWORD"}).status_code == 200
    res = client.post("/api/profile/password", headers=old,
                      json={"current_password": PASSWORD, "new_password": "brand-new-password", "otp": outbox.last_code()})
    assert res.status_code == 200
    assert client.get("/api/auth/me", headers=old).status_code == 401
    new = {"Authorization": f"Bearer {res.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=new).status_code == 200


def test_logout_everywhere(client, make_user):
    user = make_user("asha")
    old = headers(user)
    res = client.post("/api/auth/logout-all", headers=old)
    assert res.status_code == 200
    assert client.get("/api/auth/me", headers=old).status_code == 401


def test_admin_lock_revokes_session(client, make_user):
    admin = make_user("boss", admin=True)
    user = make_user("asha")
    token = headers(user)
    assert client.post(f"/api/admin/users/{user.id}/lock", headers=headers(admin), json={"reason": "test"}).status_code == 200
    assert client.get("/api/auth/me", headers=token).status_code == 401


def test_short_password_rejected(client):
    res = client.post("/api/auth/register", json={
        "username": "newbie", "email": "n@example.com", "full_name": "New", "password": "short"})
    assert res.status_code == 422


def test_security_headers(client):
    res = client.get("/api/health")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"


def test_login_with_email_any_case(client, make_user):
    make_user("asha")
    res = login(client, "  Asha@Example.COM ", PASSWORD)
    assert res.status_code == 200
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {res.json()['access_token']}"})
    assert me.json()["username"] == "asha"


def test_login_with_unknown_email_or_wrong_password(client, make_user):
    make_user("asha")
    assert login(client, "nobody@example.com", PASSWORD).status_code == 401
    res = login(client, "asha@example.com", "wrong-password")
    assert res.status_code == 401 and res.json()["detail"] == "Invalid username/email or password"


def test_wrong_passwords_by_email_count_towards_the_block(client, make_user, db):
    user = make_user("asha")
    for _ in range(settings.login_max_failures):
        assert login(client, "asha@example.com", "wrong-password").status_code == 401
    assert login(client, "asha", PASSWORD).status_code == 429
    db.refresh(user)
    assert user.login_blocked_until is not None


def test_forgot_password_by_username_or_email(client, make_user, monkeypatch):
    sent = []
    monkeypatch.setattr("app.routers.auth.send_email", lambda to, subject, body: sent.append(to) or True)
    make_user("asha")
    unknown = client.post("/api/auth/forgot-password", json={"username": "nobody@example.com"})
    assert unknown.status_code == 200 and sent == []
    by_email = client.post("/api/auth/forgot-password", json={"username": "ASHA@example.com"})
    # Same answer either way, so it can't be used to find out which emails have accounts
    assert by_email.json() == unknown.json()
    assert sent == ["asha@example.com"]
    assert client.post("/api/auth/forgot-password", json={"username": "asha"}).status_code == 200
    assert sent == ["asha@example.com", "asha@example.com"]
