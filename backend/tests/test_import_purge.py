"""Whole-file imports: Purge & re-import (a corrected file replaces an earlier import entirely), taking back an import
that stopped before it finished, and the checks that keep a file from coming in half or twice."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import Account, Expense, Fund
from app.timeutil import today_local

from .conftest import headers

API = "/api/imports"
DAY = today_local() - timedelta(days=40)


def d(n: int) -> str:
    return (DAY + timedelta(days=n)).strftime("%d/%m/%Y")


V1 = ("Date,Type,Amount,Category,Account,Notes\n"
      f"{d(0)},Expense,40,Food,Wallet,Tea\n"
      f"{d(1)},Expense,600,Food,Wallet,Groceries\n"  # wrong in this version: it was 60
      f"{d(2)},Income,5000,Salary,Wallet,Pay\n")
V2 = V1.replace(",600,", ",60,") + f"{d(3)},Expense,25,Food,Wallet,Biscuits\n"


def upload(client, user, text, name):
    res = client.post(f"{API}/extract", files={"file": (name, text.encode())}, headers=headers(user))
    assert res.status_code == 200, res.text
    return res.json()


def preview(client, user, ex, options, replaces_id=None):
    t = ex["tables"][ex["table_index"]]
    res = client.post(f"{API}/preview", json={"rows": t["rows"], "mapping": ex["guess"], "options": options,
                                              "replaces_id": replaces_id}, headers=headers(user))
    assert res.status_code == 200, res.text
    return res.json()


def start(client, user, ex, prev, options, replaces_id=None):
    return client.post(API, json={"filename": ex["filename"], "file_type": "csv", "file_hash": ex["file_hash"],
                                  "records": prev["records"], "options": options, "replaces_id": replaces_id},
                       headers=headers(user))


def run(client, user, ex, options, replaces_id=None):
    prev = preview(client, user, ex, options, replaces_id)
    res = start(client, user, ex, prev, options, replaces_id)
    assert res.status_code == 201, res.text
    batch = res.json()
    for i in range(0, len(prev["records"]), 2):
        r = client.post(f"{API}/{batch['id']}/rows", json={"records": prev["records"][i:i + 2], "options": options},
                        headers=headers(user))
        assert r.status_code == 200, r.text
    done = client.post(f"{API}/{batch['id']}/finish", headers=headers(user))
    assert done.status_code == 200, done.text
    return prev, done.json()


def purge_all(client, user, batch_id):
    while True:
        res = client.post(f"{API}/{batch_id}/purge", headers=headers(user))
        assert res.status_code == 200, res.text
        if not res.json()["remaining"]:
            return res.json()


OPTS = {"account_mode": "column", "create_accounts": {"Wallet": "WALLET"}}


def test_purge_and_reimport_replaces_the_earlier_import_entirely(client, make_user, db):
    me = make_user("asha", accounting=True)
    assert client.get("/api/accounting/settings", headers=headers(me)).status_code == 200  # books on
    _, first = run(client, me, upload(client, me, V1, "money.csv"), OPTS)
    assert first["rows_imported"] == 3
    tea = db.scalar(select(Expense).where(Expense.item_name == "Tea"))
    assert client.put(f"/api/expenses/{tea.id}", json={"category": "Snacks"}, headers=headers(me)).status_code == 200
    detail = client.get(f"{API}/{first['id']}", headers=headers(me)).json()
    assert (detail["purge_removes"], detail["purge_edited"], detail["purge_blocks"]) == (3, 1, [])
    assert detail["undo_removes"] == 2  # Undo would keep the edited one: a purge doesn't

    # The corrected file, uploaded on its own, is blocked: its rows were imported before
    fixed = upload(client, me, V2, "money (fixed).csv")
    plain = preview(client, me, fixed, OPTS)
    assert plain["blocked"] and plain["earlier_imports"][0]["id"] == first["id"]
    # As a replacement of that import, nothing of it counts against the new file
    prev = preview(client, me, fixed, OPTS, replaces_id=first["id"])
    assert not prev["blocked"] and prev["can_import"]
    assert prev["replaces"]["id"] == first["id"] and prev["replaces"]["edited"] == 1 and prev["replaces"]["blocks"] == []
    _, second = run(client, me, fixed, OPTS, replaces_id=first["id"])
    assert second["rows_imported"] == 4 and second["replaces_id"] == first["id"]
    out = purge_all(client, me, first["id"])
    assert out["status"] == "REPLACED" and out["removed"] == 3

    db.expire_all()
    items = sorted((e.item_name, e.category, e.amount) for e in db.scalars(select(Expense)))
    assert items == [("Biscuits", "Food", Decimal("25.00")), ("Groceries", "Food", Decimal("60.00")), ("Tea", "Food", Decimal("40.00"))]
    assert [f.amount for f in db.scalars(select(Fund))] == [Decimal("5000.00")]
    wallet = db.scalar(select(Account).where(Account.name == "Wallet"))
    assert wallet.is_active  # created by the first import, used by the second: kept
    old = client.get(f"{API}/{first['id']}", headers=headers(me)).json()
    assert old["status"] == "REPLACED" and old["replaced_by_id"] == second["id"]
    assert {r["reason"] for r in old["records"]} == {"Replaced by “money (fixed).csv”"}
    tb = client.get("/api/accounting/reports/trial-balance", headers=headers(me)).json()
    assert tb["balanced"]
    # Replaced already: not again
    assert start(client, me, fixed, prev, OPTS, replaces_id=first["id"]).status_code == 400


def test_purge_is_refused_up_front_when_the_books_are_locked(client, make_user):
    me = make_user("asha", accounting=True)
    client.get("/api/accounting/settings", headers=headers(me))
    _, first = run(client, me, upload(client, me, V1, "money.csv"), OPTS)
    client.put("/api/accounting/settings", json={"lock_date": str(DAY + timedelta(days=1))}, headers=headers(me))
    fixed = upload(client, me, V2, "money (fixed).csv")
    prev = preview(client, me, fixed, OPTS, replaces_id=first["id"])
    assert prev["replaces"]["blocks"] == [{"reason": f"In a locked accounting period (locked up to {DAY + timedelta(days=1):%d %b %Y})", "rows": 2}]
    res = start(client, me, fixed, prev, OPTS, replaces_id=first["id"])
    assert res.status_code == 400 and "can't be purged" in res.json()["detail"]
    assert client.get(API, headers=headers(me)).json()[0]["id"] == first["id"]  # nothing started


def test_an_import_that_stops_is_taken_back(client, make_user, db):
    me = make_user("asha")
    ex = upload(client, me, V2, "money.csv")
    prev = preview(client, me, ex, OPTS)
    batch = start(client, me, ex, prev, OPTS).json()
    assert batch["rows_expected"] == 4
    assert client.post(f"{API}/{batch['id']}/rows", json={"records": prev["records"][:2], "options": OPTS},
                       headers=headers(me)).status_code == 200
    # The page lost its connection here: finishing is refused, the import is taken back
    res = client.post(f"{API}/{batch['id']}/finish", headers=headers(me))
    assert res.status_code == 409 and "incomplete (2 of 4 rows)" in res.json()["detail"]
    out = purge_all(client, me, batch["id"])
    assert out["status"] == "ROLLED_BACK" and out["removed"] == 2 and out["accounts_removed"] == ["Wallet"]
    db.expire_all()
    assert db.scalars(select(Expense)).all() == [] and db.scalar(select(Account).where(Account.name == "Wallet")) is None
    # The file can then be imported again, whole
    _, done = run(client, me, upload(client, me, V2, "money.csv"), OPTS)
    assert done["rows_imported"] == 4 and done["status"] == "COMPLETED"


def test_a_finished_import_is_only_purged_through_a_replacement(client, make_user):
    me = make_user("asha")
    _, first = run(client, me, upload(client, me, V1, "money.csv"), OPTS)
    res = client.post(f"{API}/{first['id']}/purge", headers=headers(me))
    assert res.status_code == 400
    other = make_user("ravi")
    assert client.post(f"{API}/{first['id']}/purge", headers=headers(other)).status_code == 404
    ex = upload(client, other, V2, "money.csv")
    assert preview(client, other, ex, OPTS)["blocked"] is False  # someone else's imports never count
    t = ex["tables"][0]
    res = client.post(f"{API}/preview", json={"rows": t["rows"], "mapping": ex["guess"], "replaces_id": first["id"]},
                      headers=headers(other))
    assert res.status_code == 404


# ---------- Purge a date range (Import Records → Purge) ----------
def purge_range(client, user, start, end):
    first = client.post(f"{API}/purge", json={"start": str(start), "end": str(end)}, headers=headers(user))
    assert first.status_code == 200, first.text
    out = first.json()
    while out["remaining"]:
        res = client.post(f"{API}/purge", json={"start": str(start), "end": str(end), "backup_id": out["backup_id"]},
                          headers=headers(user))
        assert res.status_code == 200, res.text
        out = {**res.json(), "removed": out["removed"] + res.json()["removed"]}
    return out


def test_purge_a_date_range_removes_typed_and_imported_records(client, make_user, db, monkeypatch):
    monkeypatch.setattr("app.importer.purge.UNDO_CHUNK", 2)  # several calls, as for a big range
    me = make_user("asha", accounting=True)
    admin = make_user("boss", admin=True)
    client.get("/api/accounting/settings", headers=headers(me))
    _, first = run(client, me, upload(client, me, V2, "money.csv"), OPTS)  # DAY .. DAY+3
    h = headers(me)
    client.post("/api/expenses", json={"expense_date": str(DAY + timedelta(days=1)), "item_name": "Typed lunch", "amount": "99"}, headers=h)
    client.post("/api/expenses", json={"expense_date": str(DAY + timedelta(days=10)), "item_name": "Outside", "amount": "7"}, headers=h)

    start, end = DAY, DAY + timedelta(days=3)
    pv = client.get(f"{API}/purge/preview", params={"start": str(start), "end": str(end)}, headers=h).json()
    assert (pv["expenses"]["count"], pv["funds"]["count"], pv["records"], pv["imported"], pv["typed"]) == (4, 1, 5, 4, 1)
    assert pv["expenses"]["total"] == "224.00" and pv["blocks"] == []
    assert pv["imports"] == [{**pv["imports"][0], "id": first["id"], "filename": "money.csv", "rows": 4}]

    out = purge_range(client, me, start, end)
    assert out["removed"] == 5 and out["remaining"] == 0
    db.expire_all()
    assert [e.item_name for e in db.scalars(select(Expense))] == ["Outside"]
    assert db.scalars(select(Fund)).all() == []
    batch = client.get(f"{API}/{first['id']}", headers=h).json()
    assert batch["status"] == "PURGED" and {r["reason"] for r in batch["records"]} == {f"Purged ({start:%d %b %Y} to {end:%d %b %Y})"}
    assert client.get("/api/accounting/reports/trial-balance", headers=h).json()["balanced"]

    # The period can be imported again, whole
    _, again = run(client, me, upload(client, me, V2, "money.csv"), OPTS)
    assert again["rows_imported"] == 4
    # The purge was backed up: an administrator can restore it
    backups = client.get("/api/admin/backups", headers=headers(admin)).json()
    mine = next(b for b in backups if b["kind"] == "PURGE")
    assert mine["row_count"] == 5 and "Purged" in mine["summary"]


def test_purge_is_refused_in_a_locked_period_and_checks_its_input(client, make_user, db):
    me = make_user("asha", accounting=True)
    client.get("/api/accounting/settings", headers=headers(me))
    run(client, me, upload(client, me, V2, "money.csv"), OPTS)
    client.put("/api/accounting/settings", json={"lock_date": str(DAY + timedelta(days=1))}, headers=headers(me))
    h = headers(me)
    pv = client.get(f"{API}/purge/preview", params={"start": str(DAY), "end": str(DAY + timedelta(days=5))}, headers=h).json()
    assert "locked" in pv["blocks"][0]
    res = client.post(f"{API}/purge", json={"start": str(DAY), "end": str(DAY + timedelta(days=5))}, headers=h)
    assert res.status_code == 400 and res.json()["detail"].startswith("Nothing was removed")
    assert len(db.scalars(select(Expense)).all()) == 3
    # After the locked date: fine
    assert purge_range(client, me, DAY + timedelta(days=2), DAY + timedelta(days=5))["removed"] == 2
    bad = client.post(f"{API}/purge", json={"start": str(DAY), "end": str(DAY - timedelta(days=1))}, headers=h)
    assert bad.status_code == 400
    empty = client.post(f"{API}/purge", json={"start": "2001-01-01", "end": "2001-01-02"}, headers=h)
    assert empty.status_code == 400 and "no records" in empty.json()["detail"]
    other = make_user("ravi")
    first = client.get("/api/admin/backups", headers=headers(make_user("boss", admin=True))).json()[0]
    res = client.post(f"{API}/purge", json={"start": str(DAY), "end": str(DAY), "backup_id": first["id"]}, headers=headers(other))
    assert res.status_code == 404
