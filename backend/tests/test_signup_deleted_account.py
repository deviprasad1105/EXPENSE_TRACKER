"""Signing up again with the email of a deleted account: once the email code is entered, the person is offered to
bring the old account back (the new sign-up is cancelled) or to erase its data (the new sign-up goes on)."""

from sqlalchemy import select

from app.models import DataBackup, Expense, Notification, User
from .conftest import PASSWORD, headers
from .test_recover_account import EMAIL, _deleted


def _sign_up(client, outbox, username="asha2", email=EMAIL):
    r = client.post("/api/auth/register", json={"username": username, "email": email, "full_name": "Asha",
                                                 "password": "long-enough-1"})
    assert r.status_code == 201, r.text
    r = client.post("/api/auth/verify-email", json={"username": username, "code": outbox.last_code()})
    assert r.status_code == 200, r.text
    return r.json()


def _choose(client, ticket, action):
    return client.post("/api/auth/recover/after-signup", json={"ticket": ticket, "action": action})


def test_no_offer_without_a_deleted_account(client, outbox):
    assert _sign_up(client, outbox, email="new@example.com")["deleted_account"] is None


def test_restore_cancels_the_new_sign_up(client, db, make_user, outbox):
    boss, asha_id = _deleted(client, db, make_user)
    offer = _sign_up(client, outbox, username="asha")["deleted_account"]  # even with the old username
    assert offer["username"] == "asha" and offer["ticket"]

    r = _choose(client, offer["ticket"], "RESTORE")
    assert r.status_code == 200, r.text
    assert "cancelled" in r.json()["message"]
    db.expire_all()
    users = db.scalars(select(User).where(User.email == EMAIL)).all()
    assert [u.id for u in users] == [asha_id]
    assert db.scalar(select(Expense).where(Expense.user_id == asha_id)).item_name == "Tea"
    assert client.post("/api/auth/login", data={"username": "asha", "password": PASSWORD}).status_code == 200
    # The admins aren't left with an approval request for the cancelled sign-up
    assert not db.scalar(select(Notification).where(Notification.user_id == boss.id, Notification.kind == "APPROVAL"))
    # The ticket works once
    assert _choose(client, offer["ticket"], "RESTORE").status_code == 401


def test_erase_keeps_the_new_sign_up(client, db, make_user, outbox):
    boss, asha_id = _deleted(client, db, make_user)
    offer = _sign_up(client, outbox)["deleted_account"]
    r = _choose(client, offer["ticket"], "PURGE")
    assert r.status_code == 200, r.text
    db.expire_all()
    assert not db.scalars(select(DataBackup).where(DataBackup.user_id == asha_id)).all()
    new = db.scalar(select(User).where(User.username == "asha2"))
    assert new.status == "PENDING" and new.email_verified
    assert db.scalar(select(Notification).where(Notification.user_id == boss.id, Notification.kind == "APPROVAL"))
    assert _choose(client, offer["ticket"], "PURGE").status_code == 400


def test_restore_refused_when_the_old_username_is_taken(client, db, make_user, outbox):
    _, asha_id = _deleted(client, db, make_user)
    other = make_user("asha")  # someone else has the username now
    other.email = "other@example.com"
    db.commit()
    offer = _sign_up(client, outbox)["deleted_account"]
    assert _choose(client, offer["ticket"], "RESTORE").status_code == 409
    db.expire_all()
    # Nothing changed: the new sign-up is still there, the backup too
    assert db.scalar(select(User).where(User.username == "asha2"))
    assert db.scalar(select(DataBackup).where(DataBackup.user_id == asha_id))


def test_ticket_is_checked(client, db, make_user, outbox):
    boss, _ = _deleted(client, db, make_user)
    offer = _sign_up(client, outbox)["deleted_account"]
    assert _choose(client, "not-a-ticket", "RESTORE").status_code == 401
    # Once the new account is approved, the ticket no longer works
    new = db.scalar(select(User).where(User.username == "asha2"))
    assert client.post(f"/api/admin/users/{new.id}/approve", headers=headers(boss)).status_code == 200
    assert _choose(client, offer["ticket"], "RESTORE").status_code == 401
