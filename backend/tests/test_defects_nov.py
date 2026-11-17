"""Fixes from the live end-to-end test of 16-Nov-2026 (DEF-13, 14, 17, 18, 21-28 and the 'days with no entry' note)."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models import Budget, Expense, SplitBill, SplitGroup, SplitMember, SplitSettlement, SplitShare
from app.services.aliases import payee_key
from app.services.subscription_import import parse_message
from app.timeutil import today_local

from .conftest import PASSWORD, headers
from .test_import_purge import API, d, preview, purge_all, run, upload

OPTS = {"account_mode": "column", "create_accounts": {"Wallet": "WALLET"}}


# ---------- DEF-14: a category you added can be removed while hidden ----------
def test_hidden_own_category_can_be_removed_but_a_hidden_default_cannot(client, make_user):
    me = make_user("asha")
    h = headers(me)
    assert client.post("/api/categories", json={"name": "Pets"}, headers=h).status_code == 201
    listed = client.patch("/api/categories/Pets", json={"is_hidden": True}, headers=h).json()
    pets = next(c for c in listed if c["name"] == "Pets")
    assert pets["is_hidden"] and pets["is_custom"]  # still "yours" while hidden
    res = client.delete("/api/categories/Pets", headers=h)
    assert res.status_code == 200, res.text
    assert all(c["name"] != "Pets" for c in res.json())
    # A default that is only hidden is not the user's to remove
    client.patch("/api/categories/Smoking", json={"is_hidden": True}, headers=h)
    assert client.delete("/api/categories/Smoking", headers=h).status_code == 404


# ---------- DEF-18: 'Your Netflix subscription of Rs 649 has been renewed' ----------
@pytest.mark.parametrize("text, merchant, amount", [
    ("Your Netflix subscription of Rs 649 has been renewed on 04-11-2026 using card XX1234.", "Netflix", "649"),
    ("Your Spotify Premium plan of Rs.119 has been renewed on 05/11/2026.", "Spotify Premium", "119"),
    ("Rs 499 debited towards NETFLIX via PhonePe on 04-10-2026", "Netflix", "499"),
])
def test_subscription_sms_named_before_the_word_subscription(text, merchant, amount):
    found = parse_message(text)
    assert found and found["merchant"] == merchant and found["amount"] == Decimal(amount)


# ---------- DEF-23: the same username / email in other letter case ----------
def test_username_and_email_are_unique_in_any_letter_case(client, make_user):
    make_user("Asha_K")  # asha_k@example.com
    form = {"full_name": "Asha", "password": PASSWORD}
    res = client.post("/api/auth/register", json={**form, "username": "asha_k", "email": "new@example.com"})
    assert res.status_code == 409
    res = client.post("/api/auth/register", json={**form, "username": "someone", "email": "ASHA_K@Example.com"})
    assert res.status_code == 409
    assert client.post("/api/auth/register", json={**form, "username": "someone", "email": "x@example.com"}).status_code == 201
    other = make_user("ravi")
    res = client.patch("/api/profile", json={"email": "X@EXAMPLE.COM"}, headers=headers(other))
    assert res.status_code == 409


# ---------- DEF-24: the time from the file is kept ----------
def test_rows_told_apart_by_time_keep_their_time_in_the_note(client, make_user, db):
    me = make_user("asha")
    text = ("Date,Time,Type,Amount,Category,Account,Notes\n"
            f"{d(0)},09:15,Expense,345,Food,Wallet,Coffee\n"
            f"{d(0)},18:40,Expense,345,Food,Wallet,Coffee\n")
    _, done = run(client, me, upload(client, me, text, "coffee.csv"), OPTS)
    assert done["rows_imported"] == 2
    notes = sorted(e.note for e in db.query(Expense).filter(Expense.user_id == me.id))
    assert notes == ["At 09:15", "At 18:40"]


# ---------- DEF-25: Purge & re-import over several imports ----------
def test_replacement_can_replace_every_import_its_rows_came_in_with(client, make_user, db):
    me = make_user("asha")
    h = headers(me)
    year = ("Date,Type,Amount,Category,Account,Notes\n"
            f"{d(0)},Expense,40,Food,Wallet,Tea\n"
            f"{d(1)},Expense,600,Food,Wallet,Groceries\n")
    month = "Date,Type,Amount,Category,Account,Notes\n" + f"{d(5)},Expense,90,Food,Wallet,Snacks\n"
    _, a = run(client, me, upload(client, me, year, "year.csv"), OPTS)
    _, b = run(client, me, upload(client, me, month, "month.csv"), OPTS)
    fixed = year.replace(",600,", ",60,") + f"{d(5)},Expense,90,Food,Wallet,Snacks\n"
    ex = upload(client, me, fixed, "fixed.csv")

    # Replacing only the first import: the row that came in with the second would be skipped, and that import is offered
    prev = preview(client, me, ex, OPTS, replaces_id=a["id"])
    assert prev["summary"]["imported_before"] == 1 and [e["id"] for e in prev["earlier_imports"] if e["can_replace"]] == [b["id"]]

    # Replacing both: the file can be imported, then both earlier imports are purged
    t = ex["tables"][ex["table_index"]]
    body = {"rows": t["rows"], "mapping": ex["guess"], "options": OPTS, "replaces_id": a["id"], "also_replaces": [b["id"]]}
    prev = client.post(f"{API}/preview", json=body, headers=h).json()
    assert prev["can_import"] and prev["replaces"]["id"] == a["id"] and [x["id"] for x in prev["also_replaces"]] == [b["id"]]
    res = client.post(API, json={"filename": "fixed.csv", "file_type": "csv", "file_hash": ex["file_hash"], "records": prev["records"],
                                 "options": OPTS, "replaces_id": a["id"], "also_replaces": [b["id"]]}, headers=h)
    assert res.status_code == 201, res.text
    new = res.json()
    assert client.post(f"{API}/{new['id']}/rows", json={"records": prev["records"], "options": OPTS}, headers=h).status_code == 200
    assert client.post(f"{API}/{new['id']}/finish", headers=h).status_code == 200
    for old in (a, b):
        assert purge_all(client, me, old["id"])["status"] == "REPLACED"
    amounts = sorted(e.amount for e in db.query(Expense).filter(Expense.user_id == me.id))
    assert amounts == [Decimal("40.00"), Decimal("60.00"), Decimal("90.00")]


# ---------- DEF-26: payee keys ignore dates and mandate codes ----------
@pytest.mark.parametrize("text, key", [
    ("NEFT CR-ACME TECHNOLOGIES PVT LTD-SALARY JAN 2025", "acme technologies pvt ltd salary"),
    ("NEFT CR-ACME TECHNOLOGIES PVT LTD-SALARY FEB 2025", "acme technologies pvt ltd salary"),
    ("ACH D- HDFC MF SIP-0000987654", "hdfc mf sip"),
    ("May Fair Restaurant", "may fair restaurant"),
    ("UPI/P2M/647975254177/THEIVANAI K /UPI/CANARA BANK", "theivanai k"),
])
def test_payee_key_is_the_same_every_month(text, key):
    assert payee_key(text) == key


# ---------- DEF-27: statement rows offered as moves, per payee ----------
def test_statement_cash_withdrawal_and_sip_are_offered_as_moves(client, make_user, db):
    me = make_user("asha")
    h = headers(me)
    bank = client.post("/api/accounts", json={"name": "Axis", "kind": "SAVINGS"}, headers=h).json()
    text = ("Date,Particulars,Debit,Credit\n"
            f"{d(0)},ATM WDL 0000123 ANDHERI EAST,5000,\n"
            f"{d(1)},ACH D- HDFC MF SIP-0000987654,10000,\n"
            f"{d(2)},UPI/DR/500000007919/SWIGGY/ICIC/swiggy.stores@icici/Payment,250,\n")
    ex = upload(client, me, text, "statement.csv")
    opts = {"account_mode": "fixed", "account_id": bank["id"], "category_map": {"": "Food"}}
    prev = preview(client, me, ex, opts)
    moves = {c["label"]: c["suggested_treatment"] for c in prev["category_values"] if c.get("payee")}
    assert moves == {"Atm Wdl Andheri East": "new:CASH:Cash", "Hdfc Mf Sip": "new:INVESTMENT:Investments"}
    keys = [c["value"] for c in prev["category_values"] if c.get("payee")]
    opts["category_treatment"] = {k: next(c["suggested_treatment"] for c in prev["category_values"] if c["value"] == k) for k in keys}
    prev = preview(client, me, ex, opts)
    kinds = sorted(r["effective_kind"] for r in prev["records"])
    assert kinds == ["EXPENSE", "TRANSFER", "TRANSFER"] and prev["can_import"]
    assert Decimal(prev["summary"]["expense_total"]) == Decimal("250")


# ---------- DEF-17: Money with others / Investments below zero are warned about ----------
def test_moves_that_leave_money_with_others_below_zero_are_warned(client, make_user):
    me = make_user("asha")
    text = ("Date,Type,Amount,Category,Account,Notes\n"
            f"{d(0)},Income,1500,Money received,Wallet,From friend\n")
    opts = {**OPTS, "category_treatment": {"money received": "new:RECEIVABLE:Money with others"}}
    prev = preview(client, me, upload(client, me, text, "f.csv"), opts)
    warned = {w["account"]: w["kind"] for w in prev["summary"]["account_warnings"]}
    assert warned.get("Money with others") == "RECEIVABLE"


# ---------- DEF-28: a budget isn't judged against months before it existed ----------
def test_past_month_overview_leaves_out_budgets_set_up_later(client, make_user, db):
    me = make_user("asha")
    h = headers(me)
    assert client.post("/api/budgets", json={"scope": "OVERALL", "target": "*", "monthly_limit": "5000"}, headers=h).status_code == 201
    today = today_local()
    old = date(today.year - 2, 1, 15)
    db.add(Expense(user_id=me.id, expense_date=old, item_name="Old", category="Food", quantity=1, amount=Decimal("9000"),
                   is_extra=False, created_by=me.id))
    db.commit()
    past = client.get("/api/dashboard/insights", params={"month": old.strftime("%Y-%m")}, headers=h).json()
    assert past["budgets"] == []
    now = client.get("/api/dashboard/insights", headers=h).json()
    assert [b["scope"] for b in now["budgets"]] == ["OVERALL"]


# ---------- DEF-13: what has no account moves the primary account, as in the books ----------
def test_primary_balance_counts_unassigned_records_and_split_bills_like_the_books(client, make_user, db):
    me = make_user("asha", accounting=True, split=True)
    friend = make_user("ravi")
    h = headers(me)
    assert client.get("/api/accounting/settings", headers=h).status_code == 200  # books on
    bank = client.post("/api/accounts", json={"name": "HDFC", "kind": "SAVINGS", "opening_balance": "10000", "is_primary": True},
                       headers=h).json()
    day = today_local()
    db.add(Expense(user_id=me.id, expense_date=day, item_name="Tea", category="Food", quantity=1, amount=Decimal("205"),
                   is_extra=False, account_id=None, created_by=me.id))
    group = SplitGroup(name="Trip", owner_id=me.id)
    db.add(group)
    db.flush()
    mine, his = SplitMember(group_id=group.id, user_id=me.id), SplitMember(group_id=group.id, user_id=friend.id)
    db.add_all([mine, his])
    db.flush()
    bill = SplitBill(group_id=group.id, title="Hotel", total_amount=Decimal("6300"), bill_date=day, paid_by=mine.id, created_by=me.id)
    db.add(bill)
    db.flush()
    db.add_all([SplitShare(bill_id=bill.id, member_id=mine.id, share_type="EQUAL", amount=Decimal("3150")),
                SplitShare(bill_id=bill.id, member_id=his.id, share_type="EQUAL", amount=Decimal("3150"))])
    db.add(SplitSettlement(group_id=group.id, from_member=his.id, to_member=mine.id, amount=Decimal("2000"), settled_on=day,
                           created_by=friend.id))
    db.commit()
    acc = next(a for a in client.get("/api/accounts", headers=h).json() if a["id"] == bank["id"])
    assert Decimal(acc["balance"]) == Decimal("10000") - 205 - 6300 + 2000
    ledger = next(l for l in client.get("/api/accounting/ledgers", headers=h).json() if l["name"] == "HDFC")
    assert Decimal(str(ledger["balance"])) == Decimal(acc["balance"])


# ---------- 'Days with no entry' counts from the first record when older ones were imported ----------
def test_missed_days_count_from_the_first_imported_record(client, make_user, db):
    me = make_user("asha")
    first = date(today_local().year - 3, 3, 1)
    for n in (0, 2):
        db.add(Expense(user_id=me.id, expense_date=first + timedelta(days=n), item_name="Tea", category="Food", quantity=1,
                       amount=Decimal("10"), is_extra=False, created_by=me.id))
    db.commit()
    res = client.get("/api/dashboard", params={"start": first.isoformat(), "end": (first + timedelta(days=2)).isoformat()},
                     headers=headers(me))
    assert res.status_code == 200, res.text
    assert res.json()["period"]["missed_days"] == [(first + timedelta(days=1)).isoformat()]


def test_below_zero_forecast_leaves_out_the_import_being_replaced(client, make_user):
    me = make_user("asha")
    text = ("Date,Type,Amount,Category,Account,Notes\n"
            f"{d(0)},Income,1500,Money received,Wallet,From friend\n")
    opts = {**OPTS, "category_treatment": {"money received": "new:RECEIVABLE:Money with others"}}
    _, first = run(client, me, upload(client, me, text, "f.csv"), opts)
    again = upload(client, me, text.replace("1500", "1600"), "g.csv")
    prev = preview(client, me, again, opts, replaces_id=first["id"])
    warned = {w["account"]: w["balance"] for w in prev["summary"]["account_warnings"]}
    assert warned["Money with others"] == "-1600.00"  # not -3100: the first import's -1500 is removed


@pytest.mark.parametrize("text, move", [
    ("UPI/CR/500000150462/PRIYA SHARMA/SBIN/priya.sharma@oksbi/Dinner share", None),
    ("ACH D- HDFC MF SIP-0000987654", "new:INVESTMENT:Investments"),
    ("ATM WDL 0000123 ANDHERI EAST", "new:CASH:Cash"),
    ("POS 4321 SHOPPERS STOP", None),
])
def test_statement_descriptions_only_suggest_unmistakable_moves(text, move):
    from app.importer.service import suggest_description_treatment
    assert suggest_description_treatment(text) == move
