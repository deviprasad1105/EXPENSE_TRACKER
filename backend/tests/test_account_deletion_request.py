"""A user asks for their account to be deleted from the Profile page; any admin approves or rejects it."""

from sqlalchemy import select

from app.models import AccountDeletionRequest, DataBackup, Notification, User
from app.routers import admin as admin_router

from .conftest import PASSWORD, headers


def ask(client, user, password=PASSWORD, reason="Moving to another app"):
    return client.post("/api/profile/deletion-request", json={"password": password, "reason": reason}, headers=headers(user))


def test_request_notifies_every_admin_and_shows_on_the_profile(client, make_user, db):
    a1, a2 = make_user("boss", admin=True), make_user("boss2", admin=True)
    asha = make_user("asha")
    r = ask(client, asha)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "PENDING" and r.json()["username"] == "asha"

    for admin in (a1, a2):
        kinds = db.scalars(select(Notification.kind).where(Notification.user_id == admin.id)).all()
        assert "DELETION_REQUEST" in kinds
    profile = client.get("/api/profile", headers=headers(asha)).json()
    assert profile["pending_deletion_request"]["id"] == r.json()["id"]
    # The account keeps working while the request waits
    assert client.get("/api/auth/me", headers=headers(asha)).status_code == 200


def test_wrong_password_or_a_second_request_is_refused(client, make_user):
    make_user("boss", admin=True)
    asha = make_user("asha")
    assert ask(client, asha, password="wrong-password").status_code == 400
    assert ask(client, asha).status_code == 201
    assert ask(client, asha).status_code == 409


def test_user_can_cancel(client, make_user, db):
    make_user("boss", admin=True)
    asha = make_user("asha")
    rid = ask(client, asha).json()["id"]
    assert client.delete(f"/api/profile/deletion-request/{rid}", headers=headers(asha)).status_code == 204
    assert client.get("/api/profile", headers=headers(asha)).json()["pending_deletion_request"] is None


def test_admin_approves_the_account_is_deleted_backed_up_and_emailed(client, make_user, db, monkeypatch):
    sent = []
    monkeypatch.setattr(admin_router, "send_email", lambda to, subject, body: sent.append((to, subject, body)) or True)
    boss = make_user("boss", admin=True)
    asha = make_user("asha")
    asha_id = asha.id
    rid = ask(client, asha).json()["id"]

    r = client.post(f"/api/admin/deletion-requests/{rid}/approve", json={"comment": "Done, all the best"}, headers=headers(boss))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "APPROVED" and out["decided_by_username"] == "boss" and out["user_id"] is None
    assert out["admin_comment"] == "Done, all the best"
    db.expire_all()
    assert db.get(User, asha_id) is None
    backup = db.get(DataBackup, out["backup_id"])
    assert backup and backup.username == "asha" and "Requested by the user: Moving to another app" in (backup.reason or "")
    assert sent and sent[0][0] == "asha@example.com" and "Done, all the best" in sent[0][2]

    # The record stays in the admin's list
    listed = client.get("/api/admin/deletion-requests", headers=headers(boss)).json()
    assert [x["id"] for x in listed] == [rid]


def test_admin_rejects_and_the_user_is_told(client, make_user, db):
    boss = make_user("boss", admin=True)
    asha = make_user("asha")
    rid = ask(client, asha).json()["id"]
    r = client.post(f"/api/admin/deletion-requests/{rid}/reject", json={"comment": "Please settle your split bills first"}, headers=headers(boss))
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    note = db.scalar(select(Notification).where(Notification.user_id == asha.id, Notification.kind == "DELETION_DECISION"))
    assert note and "settle your split bills" in note.message
    # They can ask again later
    assert ask(client, asha).status_code == 201


def test_only_admins_can_decide_and_not_on_their_own_request(client, make_user):
    boss, boss2 = make_user("boss", admin=True), make_user("boss2", admin=True)
    asha = make_user("asha")
    rid = ask(client, asha).json()["id"]
    assert client.post(f"/api/admin/deletion-requests/{rid}/approve", json={}, headers=headers(asha)).status_code == 403
    assert client.get("/api/admin/deletion-requests", headers=headers(asha)).status_code == 403

    own = ask(client, boss).json()["id"]
    r = client.post(f"/api/admin/deletion-requests/{own}/approve", json={}, headers=headers(boss))
    assert r.status_code == 400 and "Another administrator" in r.json()["detail"]
    assert client.post(f"/api/admin/deletion-requests/{own}/approve", json={}, headers=headers(boss2)).status_code == 200


def test_the_only_admin_cannot_ask(client, make_user):
    boss = make_user("boss", admin=True)
    r = ask(client, boss)
    assert r.status_code == 400 and "no other administrator" in r.json()["detail"]


def test_deleting_from_the_users_tab_fulfils_a_pending_request(client, make_user, db):
    boss = make_user("boss", admin=True)
    asha = make_user("asha")
    rid = ask(client, asha).json()["id"]
    assert client.delete(f"/api/admin/users/{asha.id}", headers=headers(boss)).status_code == 200
    db.expire_all()
    req = db.get(AccountDeletionRequest, rid)
    assert req.status == "APPROVED" and req.user_id is None and req.backup_id is not None
