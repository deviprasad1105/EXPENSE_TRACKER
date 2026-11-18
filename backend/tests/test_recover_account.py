"""After an account is deleted, whoever can read its email restores it or erases its backup, confirming with a code."""

from datetime import timedelta

from sqlalchemy import select

from app.models import AccountDeletionRequest, DataBackup, Expense, Notification, RecoveryOtp, User
from .conftest import PASSWORD, headers

EMAIL = "asha@example.com"


def _deleted(client, db, make_user, with_expense=True):
    """Make @asha (with one expense) and have the admin delete her. Returns (admin, asha's old id)."""
    boss = make_user("boss", admin=True)
    asha = make_user("asha")
    if with_expense:
        r = client.post("/api/expenses", json={"expense_date": "2026-10-01", "item_name": "Tea", "amount": "20"}, headers=headers(asha))
        assert r.status_code == 201, r.text
    asha_id = asha.id
    assert client.delete(f"/api/admin/users/{asha_id}", headers=headers(boss)).status_code == 200
    db.expire_all()
    return boss, asha_id


def _send(client, action, email=EMAIL):
    return client.post("/api/auth/recover/send-code", json={"email": email, "action": action})


def _confirm(client, action, code, email=EMAIL):
    return client.post("/api/auth/recover/confirm", json={"email": email, "action": action, "code": code})


def test_backup_remembers_the_email(client, db, make_user):
    _, asha_id = _deleted(client, db, make_user)
    backup = db.scalar(select(DataBackup).where(DataBackup.user_id == asha_id))
    assert backup.email == EMAIL


def test_restore_with_the_emailed_code(client, db, make_user, outbox):
    boss, asha_id = _deleted(client, db, make_user)
    r = _send(client, "RESTORE", email="  ASHA@Example.com ")
    assert r.status_code == 200 and r.json()["expires_in_seconds"] > 0
    assert outbox[-1][0] == EMAIL and "@asha" in outbox[-1][2]

    wrong = "000000" if outbox.last_code() != "000000" else "111111"
    assert _confirm(client, "RESTORE", wrong).status_code == 400
    # A RESTORE code doesn't erase anything
    assert _confirm(client, "PURGE", outbox.last_code()).status_code == 400

    r = _confirm(client, "RESTORE", outbox.last_code())
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "asha"
    db.expire_all()
    assert db.get(User, asha_id) is not None
    assert db.scalar(select(Expense).where(Expense.user_id == asha_id)).item_name == "Tea"
    # Same password as before, and the admins are told
    login = client.post("/api/auth/login", data={"username": "asha", "password": PASSWORD})
    assert login.status_code == 200
    assert db.scalar(select(Notification).where(Notification.user_id == boss.id, Notification.message.like("%restored their deleted%")))
    backup = db.scalar(select(DataBackup).where(DataBackup.user_id == asha_id))
    assert backup.restored_at and backup.restored_by == "@asha (self)"

    # The code worked once, and there's nothing left to restore
    assert _confirm(client, "RESTORE", outbox.last_code()).status_code == 400


def test_purge_erases_every_backup_for_good(client, db, make_user, outbox):
    boss, asha_id = _deleted(client, db, make_user)
    _send(client, "PURGE")
    assert "cannot be undone" in outbox[-1][2]
    r = _confirm(client, "PURGE", outbox.last_code())
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.scalars(select(DataBackup).where(DataBackup.user_id == asha_id)).all() == []
    assert db.get(User, asha_id) is None
    # Nothing left: no new code is sent, and the admin's Backups tab no longer has it
    before = len(outbox)
    assert _send(client, "RESTORE").status_code == 200
    assert len(outbox) == before
    assert all(b["user_id"] != asha_id for b in client.get("/api/admin/backups", headers=headers(boss)).json())


def test_purge_clears_the_link_from_the_deletion_request(client, db, make_user, outbox):
    boss, asha = make_user("boss", admin=True), make_user("asha")
    rid = client.post("/api/profile/deletion-request", json={"password": PASSWORD}, headers=headers(asha)).json()["id"]
    assert client.post(f"/api/admin/deletion-requests/{rid}/approve", json={}, headers=headers(boss)).status_code == 200
    _send(client, "PURGE")
    assert _confirm(client, "PURGE", outbox.last_code()).status_code == 200
    db.expire_all()
    assert db.get(AccountDeletionRequest, rid).backup_id is None


def test_unknown_email_gets_the_same_answer_and_no_email(client, db, make_user, outbox):
    _deleted(client, db, make_user)
    known = _send(client, "RESTORE").json()["message"]
    sent = len(outbox)
    unknown = _send(client, "RESTORE", email="nobody@example.com")
    assert unknown.status_code == 200 and unknown.json()["message"] == known.replace(EMAIL, "nobody@example.com")
    assert len(outbox) == sent
    assert _confirm(client, "RESTORE", "123456", email="nobody@example.com").status_code == 400


def test_a_live_account_cannot_be_restored_over_or_erased(client, db, make_user, outbox):
    make_user("boss", admin=True)
    make_user("asha")
    _send(client, "RESTORE")
    assert outbox == []


def test_restore_refused_when_the_username_was_taken_again(client, db, make_user, outbox):
    _deleted(client, db, make_user)
    _send(client, "RESTORE")
    code = outbox.last_code()
    make_user("asha")  # someone signed up again as @asha
    r = _confirm(client, "RESTORE", code)
    assert r.status_code == 409 and "asha" in r.json()["detail"]
    # Nothing was saved, so the code isn't used up
    db.expire_all()
    assert db.scalar(select(RecoveryOtp).where(RecoveryOtp.email == EMAIL)).used_at is None


def test_code_expires_and_resend_waits(client, db, make_user, outbox):
    _deleted(client, db, make_user)
    _send(client, "RESTORE")
    first = outbox.last_code()
    # Asking again at once doesn't send another email
    _send(client, "RESTORE")
    assert len(outbox) == 1
    for o in db.scalars(select(RecoveryOtp)):
        o.expires_at -= timedelta(hours=1)
        o.created_at -= timedelta(hours=1)
    db.commit()
    r = _confirm(client, "RESTORE", first)
    assert r.status_code == 400 and "expired" in r.json()["detail"]
    _send(client, "RESTORE")
    assert len(outbox) == 2
    assert _confirm(client, "RESTORE", outbox.last_code()).status_code == 200


def test_restore_still_works_for_admins(client, db, make_user):
    """The admin's Backups → Restore still records the admin as the restorer."""
    boss, asha_id = _deleted(client, db, make_user, with_expense=False)
    backup = db.scalar(select(DataBackup).where(DataBackup.user_id == asha_id))
    assert client.post(f"/api/admin/backups/{backup.id}/restore", headers=headers(boss)).status_code == 200
    db.expire_all()
    assert db.get(DataBackup, backup.id).restored_by == "boss"


def test_older_backups_get_their_email_at_startup(client, db, make_user):
    """Backups made before the email column existed: startup reads the email from the backup itself."""
    from app.migrate import _data_fixups

    _, asha_id = _deleted(client, db, make_user, with_expense=False)
    backup = db.scalar(select(DataBackup).where(DataBackup.user_id == asha_id))
    backup.email = None
    db.commit()
    _data_fixups(db.get_bind())
    db.expire_all()
    assert db.get(DataBackup, backup.id).email == EMAIL
