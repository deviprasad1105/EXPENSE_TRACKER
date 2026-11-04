from app.models import ROLE_ORG_ADMIN, Notification, User

from .conftest import PASSWORD, headers


def make_org(db, make_user):
    boss = make_user("boss")
    boss.role = ROLE_ORG_ADMIN
    member = make_user("member")
    member.org_admin_id = boss.id
    db.commit()
    return boss, member


def test_org_admin_views_members_read_only(client, db, make_user):
    boss, member = make_org(db, make_user)
    outsider = make_user("outsider")
    assert client.get("/api/expenses", params={"user_id": member.id}, headers=headers(boss)).status_code == 200
    res = client.post("/api/expenses", params={"user_id": member.id}, headers=headers(boss),
                      json={"expense_date": "2026-01-01", "item_name": "Tea", "amount": 10})
    assert res.status_code == 403
    assert client.get("/api/expenses", params={"user_id": outsider.id}, headers=headers(boss)).status_code == 403
    access = {a["id"]: a["access"] for a in client.get("/api/auth/accessible-users", headers=headers(boss)).json()}
    assert access == {boss.id: "OWNER", member.id: "ORG"}
    # The member is not told who can see them
    me = client.get("/api/auth/me", headers=headers(member)).json()
    assert "org_admin_id" not in me
    assert [a["id"] for a in client.get("/api/auth/accessible-users", headers=headers(member)).json()] == [member.id]


def test_org_admin_adds_locks_and_removes_only_own_members(client, db, make_user):
    boss, member = make_org(db, make_user)
    outsider = make_user("outsider")
    res = client.post("/api/org/members", headers=headers(boss),
                      json={"username": "newbie", "email": "newbie@example.com", "full_name": "Newbie", "password": PASSWORD})
    assert res.status_code == 201 and res.json()["status"] == "APPROVED" and res.json()["org_admin_id"] == boss.id
    assert {m["username"] for m in client.get("/api/org/members", headers=headers(boss)).json()} == {"member", "newbie"}

    assert client.post(f"/api/org/members/{outsider.id}/lock", headers=headers(boss), json={}).status_code == 404
    assert client.post(f"/api/org/members/{member.id}/lock", headers=headers(boss), json={}).status_code == 200
    assert client.get("/api/auth/me", headers=headers(member)).status_code == 401
    assert client.post(f"/api/org/members/{member.id}/unlock", headers=headers(boss)).status_code == 200

    assert client.delete(f"/api/org/members/{outsider.id}", headers=headers(boss)).status_code == 404
    assert client.delete(f"/api/org/members/{member.id}", headers=headers(boss)).status_code == 200
    assert client.get("/api/org/members", headers=headers(outsider)).status_code == 403


def test_locking_org_admin_locks_members(client, db, make_user):
    admin = make_user("root", admin=True)
    boss, member = make_org(db, make_user)
    member_headers = headers(member)
    assert client.post(f"/api/admin/users/{boss.id}/lock", headers=headers(admin), json={}).status_code == 200
    assert client.get("/api/auth/me", headers=member_headers).status_code == 401
    assert client.post("/api/auth/login", data={"username": "member", "password": PASSWORD}).status_code == 403
    client.post(f"/api/admin/users/{boss.id}/unlock", headers=headers(admin))
    assert client.get("/api/auth/me", headers=member_headers).status_code == 200


def test_admin_cannot_have_manager(client, db, make_user):
    admin = make_user("root", admin=True)
    other_admin = make_user("root2", admin=True)
    user = make_user("someone")
    res = client.patch(f"/api/admin/users/{other_admin.id}", headers=headers(admin), json={"manager_id": user.id})
    assert res.status_code == 400
    # Promoting a user to admin drops their manager
    worker = make_user("worker", manager=user)
    client.patch(f"/api/admin/users/{worker.id}", headers=headers(admin), json={"role": "ADMIN"})
    db.expire_all()
    assert db.get(User, worker.id).manager_id is None


def test_removing_access_sends_no_notification(client, db, make_user):
    admin = make_user("root", admin=True)
    user = make_user("someone", split=True)
    client.patch(f"/api/admin/users/{user.id}", headers=headers(admin), json={"feature_split": False})
    assert db.query(Notification).filter_by(user_id=user.id).count() == 0
    client.patch(f"/api/admin/users/{user.id}", headers=headers(admin), json={"feature_subscription": True})
    assert db.query(Notification).filter_by(user_id=user.id).count() == 1
