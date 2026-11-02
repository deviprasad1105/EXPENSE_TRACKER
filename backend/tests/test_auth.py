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


def test_password_change_revokes_old_tokens(client, make_user):
    user = make_user("asha")
    old = headers(user)
    res = client.post("/api/profile/password", headers=old,
                      json={"current_password": PASSWORD, "new_password": "brand-new-password"})
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
