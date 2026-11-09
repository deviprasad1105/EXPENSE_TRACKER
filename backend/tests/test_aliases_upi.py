"""Transaction aliases and the UPI tracker."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.models import Expense, Fund
from app.routers import upi as upi_router
from app.services.aliases import payee_key
from app.services.upi import CREDIT, DEBIT, parse_message
from app.timeutil import today_local

from .conftest import headers


@pytest.fixture(autouse=True)
def _reset_limiter():
    upi_router.hook_limiter.reset()


# ---------- payee keys ----------
@pytest.mark.parametrize("text,key", [
    ("UPI/DR/412345678901/RAMESH TEA STALL/YBL/ramesh@ybl/Payment", "ramesh@ybl"),
    ("UPI-RAMESH TEA STALL-412345678901", "ramesh tea stall"),
    ("UPI/P2M/647975254177/THEIVANAI K /UPI/CANARA BANK", "theivanai k"),
    ("Tea", "tea"),
    ("NEFT CR 0098123 ACME PVT LTD", "acme pvt ltd"),
    ("123456", None),
])
def test_payee_key(text, key):
    assert payee_key(text) == key


# ---------- message parsing ----------
D = today_local() - timedelta(days=1)
DMY = D.strftime("%d-%m-%y")
DMON = D.strftime("%d%b%y")


@pytest.mark.parametrize("text,direction,amount,vpa,name,ref", [
    (f"Rs.250.00 debited from a/c **1234 on {DMY} to VPA ramesh@ybl(UPI Ref No 412345678901).", DEBIT, "250.00", "ramesh@ybl", None,
     "412345678901"),
    (f"Sent Rs.120.00\nFrom HDFC Bank A/C *1234\nTo RAMESH TEA STALL\nOn {D:%d/%m/%y}\nRef 412345678902\nNot You? Call 18002586161",
     DEBIT, "120.00", None, "RAMESH TEA STALL", "412345678902"),
    (f"Dear UPI user A/C X1234 debited by 75.0 on date {DMON} trf to SURESH KIRANA Refno 412345678903. If not u? call 1800111109. -SBI",
     DEBIT, "75.0", None, "SURESH KIRANA", "412345678903"),
    (f"ICICI Bank Acct XX123 debited for Rs 499.00 on {D:%d-%b-%y}; NETFLIX credited. UPI:412345678904. Call 18002662 for dispute.",
     DEBIT, "499.00", None, "NETFLIX", "412345678904"),
    (f"Dear SBI User, your A/c X1234-credited by Rs.500 on {DMON} transfer from ANIL KUMAR Ref No 412345678905 -SBI",
     CREDIT, "500", None, "ANIL KUMAR", "412345678905"),
    (f"INR 60.00 debited\nA/c no. XX1234\n{DMY}, 10:15:22\nUPI/P2M/412345678906/CHAI POINT\nNot you? SMS BLOCKUPI", DEBIT, "60.00", None,
     "CHAI POINT", "412345678906"),
])
def test_parse_bank_messages(text, direction, amount, vpa, name, ref):
    p = parse_message(text, {"me@okaxis"})
    assert (p.direction, p.amount, p.vpa, p.name, p.ref) == (direction, Decimal(amount), vpa, name, ref)
    assert p.txn_date == D


@pytest.mark.parametrize("text", [
    "Your OTP for UPI transaction is 123456. Do not share.",
    "Rs.100 debited from a/c **1234 at AMAZON on card",  # a card payment, not UPI
    "ramesh@ybl has requested money from you on UPI. Pay Rs 200 in the app",
])
def test_parse_rejects_non_payments(text):
    with pytest.raises(ValueError):
        parse_message(text)


# ---------- aliases ----------
def _expense(db, user, name, amount=10, days_ago=0, **kw):
    e = Expense(user_id=user.id, expense_date=today_local() - timedelta(days=days_ago), item_name=name, amount=Decimal(amount), **kw)
    db.add(e)
    db.commit()
    return e


def test_groups_alias_old_records_and_future_ones(client, db, make_user):
    u = make_user("alice")
    h = headers(u)
    e1 = _expense(db, u, "UPI/DR/412345678901/RAMESH TEA/YBL/ramesh@ybl/Pay", 15, 2)
    e2 = _expense(db, u, "UPI/DR/412345678999/RAMESH TEA/YBL/ramesh@ybl/Pay", 20, 1)
    _expense(db, u, "Petrol", 500)

    groups = client.get("/api/aliases/groups", headers=h).json()
    tea = next(g for g in groups if g["match_key"] == "ramesh@ybl")
    assert tea["expense_count"] == 2 and Decimal(tea["expense_total"]) == 35
    assert tea["suggestion"] == "Ramesh Tea"

    preview = client.get("/api/aliases/matches", params={"match_key": "ramesh@ybl"}, headers=h).json()
    assert {r["id"] for r in preview} == {e1.id, e2.id}

    # Rename only one of the old records
    r = client.post("/api/aliases", json={"match_key": "ramesh@ybl", "alias": "Tea", "category": "Food", "expense_ids": [e1.id]}, headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["renamed"] == 1 and body["alias"]["records"] == 1 and body["alias"]["unnamed"] == 1
    db.expire_all()
    assert db.get(Expense, e1.id).item_name == "Tea" and db.get(Expense, e1.id).raw_name.startswith("UPI/DR")
    assert db.get(Expense, e1.id).category == "General"  # old records keep their category
    assert db.get(Expense, e2.id).item_name.startswith("UPI/DR")
    # The group still shows the old record not renamed, with its alias
    tea = next(g for g in client.get("/api/aliases/groups", headers=h).json() if g["match_key"] == "ramesh@ybl")
    assert tea["alias"] == "Tea" and tea["expense_count"] == 1

    alias_id = body["alias"]["id"]
    assert client.post(f"/api/aliases/{alias_id}/apply", json={"expense_ids": [e2.id]}, headers=h).json()["renamed"] == 1

    # A new expense to the same UPI ID is named at once, with the alias's category
    new = client.post("/api/expenses", json={"expense_date": str(today_local()), "item_name": "UPI/DR/4111/X/YBL/ramesh@ybl/p",
                                              "amount": 12}, headers=h).json()
    assert new["item_name"] == "Tea" and new["alias_id"] == alias_id and new["category"] == "Food"

    # Renaming the alias renames every record it names
    r = client.put(f"/api/aliases/{alias_id}", json={"alias": "Morning tea"}, headers=h).json()
    assert r["renamed"] == 3
    db.expire_all()
    assert db.get(Expense, e2.id).item_name == "Morning tea"

    # Deleting it with restore puts the original descriptions back
    assert client.delete(f"/api/aliases/{alias_id}", params={"restore": True}, headers=h).json()["renamed"] == 3
    db.expire_all()
    assert db.get(Expense, e1.id).item_name.startswith("UPI/DR") and db.get(Expense, e1.id).alias_id is None


def test_typed_key_matches_words_and_funds(client, db, make_user):
    u = make_user("bob")
    h = headers(u)
    f = Fund(user_id=u.id, fund_date=today_local(), amount=Decimal(1000), source="NEFT CR 0098123 ACME PVT LTD SALARY")
    db.add(f)
    db.commit()
    r = client.post("/api/aliases", json={"match_key": "Acme", "alias": "Salary", "fund_ids": [f.id]}, headers=h).json()
    assert r["renamed"] == 1
    db.expire_all()
    assert db.get(Fund, f.id).source == "Salary"
    new = client.post("/api/funds", json={"fund_date": str(today_local()), "amount": 5, "source": "IMPS ACME PVT LTD 99"}, headers=h).json()
    assert new["source"] == "Salary"


def test_editing_an_aliased_name_by_hand_detaches_it(client, db, make_user):
    u = make_user("cara")
    h = headers(u)
    e = _expense(db, u, "Chai@1")
    a = client.post("/api/aliases", json={"match_key": "chai", "alias": "Tea", "expense_ids": [e.id]}, headers=h).json()
    client.put(f"/api/expenses/{e.id}", json={"item_name": "Coffee"}, headers=h)
    db.expire_all()
    assert db.get(Expense, e.id).alias_id is None
    assert client.get("/api/aliases", headers=h).json()[0]["records"] == 0
    assert a["alias"]["id"]


def test_viewer_cannot_change_aliases(client, db, make_user):
    owner = make_user("dan")
    viewer = make_user("eve", manager=None)
    r = client.post("/api/aliases", params={"user_id": owner.id}, json={"match_key": "tea", "alias": "Tea"}, headers=headers(viewer))
    assert r.status_code == 403


# ---------- UPI tracker ----------
MSG = f"Rs.250.00 debited from a/c **1234 on {DMY} to VPA ramesh@ybl(UPI Ref No 412345678901)."


def _setup_upi(client, user):
    h = headers(user)
    assert client.post("/api/upi/ids", json={"vpa": "Alice@OKAXIS"}, headers=h).status_code == 201
    assert client.patch("/api/upi/settings", json={"enabled": True}, headers=h).json()["enabled"] is True
    return client.post("/api/upi/token", headers=h).json()["hook_path"]


def test_upi_needs_the_plan(client, make_user):
    u = make_user("frank")
    assert client.get("/api/upi/settings", headers=headers(u)).status_code == 403
    u2 = make_user("gina", upi=True, expense=False)
    assert client.get("/api/upi/settings", headers=headers(u2)).status_code == 403


def test_admin_grants_upi(client, make_user):
    admin = make_user("root", admin=True)
    u = make_user("hank")
    r = client.patch(f"/api/admin/users/{u.id}", json={"feature_upi": True}, headers=headers(admin))
    assert r.status_code == 200 and r.json()["feature_upi"] is True
    assert client.get("/api/upi/settings", headers=headers(u)).status_code == 200


def test_upi_hook_records_payment_with_alias_once(client, db, make_user):
    u = make_user("ivy", upi=True)
    h = headers(u)
    client.post("/api/aliases", json={"match_key": "ramesh@ybl", "alias": "Tea", "category": "Food"}, headers=h)
    hook = _setup_upi(client, u)

    r = client.post(hook, json={"text": MSG})
    assert r.status_code == 200 and r.json()["status"] == "CREATED"
    exp = db.query(Expense).filter_by(user_id=u.id).one()
    assert (exp.item_name, exp.category, exp.amount, exp.origin, exp.expense_date) == ("Tea", "Food", Decimal("250.00"), "UPI", D)
    assert exp.alias_id is not None

    # Forwarded twice: recorded once
    assert client.post(hook, content=MSG, headers={"content-type": "text/plain"}).json()["status"] == "DUPLICATE"
    assert db.query(Expense).filter_by(user_id=u.id).count() == 1

    act = client.get("/api/upi/activity", headers=h).json()
    assert act["days"][0]["count"] == 1 and Decimal(act["days"][0]["paid"]) == 250
    assert act["events"][1]["name"] == "Tea"


def test_upi_payee_without_alias_is_recorded_and_flagged(client, db, make_user):
    u = make_user("jay", upi=True)
    _setup_upi(client, u)
    msg = f"Dear SBI User, your A/c X1234-credited by Rs.500 on {DMON} transfer from ANIL KUMAR Ref No 412345678905 -SBI"
    ev = client.post("/api/upi/messages", json={"text": msg}, headers=headers(u)).json()
    assert ev["status"] == "CREATED" and ev["reason"] == "No alias yet for this payee" and ev["fund_id"]
    f = db.get(Fund, ev["fund_id"])
    assert (f.source, f.raw_name, f.origin) == ("ANIL KUMAR", "UPI from ANIL KUMAR", "UPI")
    # It appears among the groups waiting for an alias
    assert any(g["match_key"] == "anil kumar" for g in client.get("/api/aliases/groups", headers=headers(u)).json())


def test_upi_switched_off_or_bad_link(client, db, make_user):
    u = make_user("kim", upi=True)
    hook = _setup_upi(client, u)
    client.patch("/api/upi/settings", json={"enabled": False}, headers=headers(u))
    assert client.post(hook, json={"text": MSG}).json()["status"] == "IGNORED"
    assert db.query(Expense).count() == 0
    assert client.post("/api/upi/hook/not-a-real-token-xxxxxxxx", json={"text": MSG}).status_code == 404
    # A new link replaces the old one
    client.post("/api/upi/token", headers=headers(u))
    assert client.post(hook, json={"text": MSG}).status_code == 404


def test_upi_cannot_be_enabled_without_an_id(client, make_user):
    u = make_user("lee", upi=True)
    assert client.patch("/api/upi/settings", json={"enabled": True}, headers=headers(u)).status_code == 400


def test_alias_skips_records_in_a_locked_accounting_period(client, db, make_user):
    from app.accounting.models import Voucher

    u = make_user("mona", accounting=True)
    h = headers(u)
    assert client.get("/api/accounting/settings", headers=h).status_code == 200  # opens the books
    old = client.post("/api/expenses", json={"expense_date": str(today_local() - timedelta(days=5)), "item_name": "chai stall", "amount": 10},
                      headers=h).json()
    new = client.post("/api/expenses", json={"expense_date": str(today_local()), "item_name": "chai stall", "amount": 12}, headers=h).json()
    r = client.put("/api/accounting/settings", json={"lock_date": str(today_local() - timedelta(days=1))}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/api/aliases", json={"match_key": "chai stall", "alias": "Tea", "expense_ids": [old["id"], new["id"]]}, headers=h)
    assert r.status_code == 201, r.text
    assert (r.json()["renamed"], r.json()["skipped_locked"]) == (1, 1)
    db.expire_all()
    assert db.get(Expense, old["id"]).item_name == "chai stall" and db.get(Expense, new["id"]).item_name == "Tea"
    # The open-period voucher's narration follows the alias
    assert any(v.narration.startswith("Tea") for v in db.query(Voucher).all())


# ---------- overview: an earlier month ----------
def test_overview_for_an_earlier_month(client, db, make_user):
    u = make_user("nina")
    h = headers(u)
    first = today_local().replace(day=1)
    prev = (first - timedelta(days=1)).replace(day=1)
    before = (prev - timedelta(days=1)).replace(day=1)
    _expense(db, u, "Rent", 1000, (today_local() - prev).days)
    _expense(db, u, "Rent", 800, (today_local() - before).days)
    r = client.get("/api/dashboard/insights", params={"month": f"{prev:%Y-%m}"}, headers=h).json()
    assert r["month"] == prev.isoformat() and r["is_current"] is False and r["first_month"] == before.isoformat()
    t = r["sections"]["total"]
    # The whole month against the whole month before
    assert (t["this_month"], t["last_month_to_date"], t["vs_last_month"]) == (1000, 800, 25.0)
    assert r["insights"][0]["text"].startswith(f"In {prev:%B} you spent ₹1,000")
    assert client.get("/api/dashboard/insights", headers=h).json()["is_current"] is True
    nxt = (first + timedelta(days=32)).replace(day=1)
    assert client.get("/api/dashboard/insights", params={"month": f"{nxt:%Y-%m}"}, headers=h).status_code == 400


def test_money_received_can_be_filtered(client, db, make_user):
    u = make_user("omar")
    h = headers(u)
    for days, src in ((40, "Salary ACME"), (2, "Refund"), (1, "Salary ACME")):
        db.add(Fund(user_id=u.id, fund_date=today_local() - timedelta(days=days), amount=Decimal(100), source=src))
    db.commit()
    start = str(today_local() - timedelta(days=10))
    assert len(client.get("/api/funds", params={"start": start}, headers=h).json()) == 2
    assert len(client.get("/api/funds", params={"search": "acme"}, headers=h).json()) == 2
    assert len(client.get("/api/funds", params={"start": start, "search": "acme"}, headers=h).json()) == 1


# ---------- categories ----------
def test_custom_and_hidden_categories(client, db, make_user):
    u = make_user("pia")
    h = headers(u)
    assert "Smoking" in client.get("/api/expenses/categories", headers=h).json()
    r = client.patch("/api/categories/smoking", json={"is_hidden": True}, headers=h)
    assert r.status_code == 200 and next(c for c in r.json() if c["name"] == "Smoking")["is_hidden"]
    assert "Smoking" not in client.get("/api/expenses/categories", headers=h).json()
    assert client.post("/api/categories", json={"name": "Pets"}, headers=h).status_code == 201
    assert client.post("/api/categories", json={"name": "pets"}, headers=h).status_code == 400
    assert "Pets" in client.get("/api/expenses/categories", headers=h).json()
    # Adding a hidden one shows it again
    client.post("/api/categories", json={"name": "Smoking"}, headers=h)
    assert "Smoking" in client.get("/api/expenses/categories", headers=h).json()
    assert client.delete("/api/categories/Pets", headers=h).status_code == 200
    assert "Pets" not in client.get("/api/expenses/categories", headers=h).json()
    assert client.delete("/api/categories/Food", headers=h).status_code == 404


def test_alias_can_give_old_records_its_category(client, db, make_user):
    u = make_user("quin")
    h = headers(u)
    e = _expense(db, u, "UPI/P2M/1234/SUNIL TEA/UPI/SBI", 10)
    r = client.post("/api/aliases", json={"match_key": "sunil tea", "alias": "Tea", "category": "Food",
                                          "apply_all_existing": True, "set_category_existing": True}, headers=h).json()
    assert r["renamed"] == 1
    db.expire_all()
    assert (db.get(Expense, e.id).item_name, db.get(Expense, e.id).category) == ("Tea", "Food")


# ---------- statement import uses aliases ----------
STATEMENT = (
    "Date,Particulars,Debit,Credit\r\n"
    f"{D:%d-%m-%Y},UPI/P2M/111111111111/SUNIL TEA /UPI/CANARA BANK,20.00,\r\n"
    f"{D:%d-%m-%Y},UPI/P2M/222222222222/SUNIL TEA /UPI/CANARA BANK,30.00,\r\n"
    f"{D:%d-%m-%Y},UPI/P2M/333333333333/VVM AND SONS /UPI/CITY UNION BANK,185.00,\r\n"
    f"{D:%d-%m-%Y},NEFT CR ACME PVT LTD SALARY,,5000.00\r\n"
)


def test_statement_rows_take_alias_name_and_category(client, db, make_user):
    u = make_user("ravi")
    h = headers(u)
    client.post("/api/aliases", json={"match_key": "sunil tea", "alias": "Tea", "category": "Food"}, headers=h)
    d = client.post("/api/imports/extract", files={"file": ("s.csv", STATEMENT.encode())}, headers=h).json()
    t = d["tables"][d["table_index"]]
    prev = client.post("/api/imports/preview", json={"rows": t["rows"], "mapping": d["guess"]}, headers=h).json()
    by = {r["description"][:20]: r for r in prev["records"]}
    tea = by["UPI/P2M/111111111111"]
    # The aliased payee needs no category mapping; the other one still does
    assert (tea["status"], tea["category_final"], tea["alias_name"]) == ("READY", "Food", "Tea")
    assert by["UPI/P2M/333333333333"]["status"] == "NEEDS_CATEGORY"
    payees = {p["payee_key"]: p for p in prev["payees"]}
    assert payees["sunil tea"]["alias"] == "Tea" and payees["sunil tea"]["sent_count"] == 2
    assert payees["vvm and sons"]["alias"] is None and payees["vvm and sons"]["suggestion"] == "Vvm And Sons"
    assert payees["acme pvt ltd salary"]["received_count"] == 1
    assert next(c for c in prev["category_values"] if c["value"] == "")["expense_rows"] == 1

    # Name the other payees in the review, then import: everything is named
    client.post("/api/aliases", json={"match_key": "vvm and sons", "alias": "Groceries shop", "category": "Groceries"}, headers=h)
    client.post("/api/aliases", json={"match_key": "acme", "alias": "Salary"}, headers=h)
    check = client.post("/api/imports/check", json={"records": prev["records"]}, headers=h).json()
    assert check["summary"]["needs_category"] == 0
    recs = [{**r, "include": r["can_import"]} for r in check["records"]]
    batch = client.post("/api/imports", json={"filename": "s.csv", "file_type": "csv", "file_hash": d["file_hash"], "records": recs}, headers=h).json()
    client.post(f"/api/imports/{batch['id']}/rows", json={"records": recs}, headers=h)
    exp = sorted((e.item_name, e.category, e.amount) for e in db.query(Expense).filter_by(user_id=u.id))
    assert exp == [("Groceries shop", "Groceries", Decimal("185.00")), ("Tea", "Food", Decimal("20.00")), ("Tea", "Food", Decimal("30.00"))]
    f = db.query(Fund).filter_by(user_id=u.id).one()
    assert (f.source, f.raw_name) == ("Salary", "NEFT CR ACME PVT LTD SALARY")


def test_suggestion_ignores_reference_codes():
    from app.services.aliases import suggest_alias
    assert suggest_alias("creditcard xx", "CreditCard Payment XX 2365 Ref#7ET36ST93V0QR7") == "Creditcard Xx"
    assert suggest_alias("theivanai k", "UPI/P2M/647975254177/THEIVANAI K /UPI/CANARA BANK") == "Theivanai K"
