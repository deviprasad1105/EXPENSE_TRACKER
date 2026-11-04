"""Phase 1: the voucher engine, chart of accounts, period lock and access rules."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.accounting.chart import fy_of, fy_start
from app.accounting.models import AccLedger, Voucher
from app.timeutil import today_local

from .conftest import headers

D = Decimal
API = "/api/accounting"


def accountant(make_user, name="asha", **kw):
    return make_user(name, accounting=True, **kw)


def ledgers(client, user, **params):
    return {g["name"]: g for g in client.get(f"{API}/ledgers", params=params, headers=headers(user)).json()}


def voucher(client, user, vtype, lines, day=None, **params):
    body = {"voucher_type": vtype, "voucher_date": str(day or today_local()), "narration": "test",
            "lines": [{"ledger_id": lid, "debit": str(dr), "credit": str(cr)} for lid, dr, cr in lines]}
    return client.post(f"{API}/vouchers", json=body, params=params, headers=headers(user))


# ---------- access ----------
def test_accounting_needs_the_feature_and_expenses(client, make_user):
    assert client.get(f"{API}/settings", headers=headers(make_user("plain"))).status_code == 403
    no_expense = make_user("noexp", accounting=True, expense=False)
    assert client.get(f"{API}/settings", headers=headers(no_expense)).status_code == 403
    assert client.get(f"{API}/settings", headers=headers(accountant(make_user))).status_code == 200
    assert client.get(f"{API}/settings", headers=headers(make_user("boss", admin=True))).status_code == 200


def test_manager_reads_but_cannot_post(client, make_user):
    boss = make_user("boss", accounting=True)
    me = accountant(make_user, manager=boss)
    cash = ledgers(client, me)["Cash"]["id"]
    capital = ledgers(client, me)["Capital Account"]["id"]
    assert client.get(f"{API}/ledgers", params={"user_id": me.id}, headers=headers(boss)).status_code == 200
    r = voucher(client, boss, "RECEIPT", [(cash, 100, 0), (capital, 0, 100)], user_id=me.id)
    assert r.status_code == 403


def test_admin_can_post_in_someone_elses_books(client, make_user):
    admin, me = make_user("root", admin=True), accountant(make_user)
    g = ledgers(client, me)
    r = voucher(client, admin, "RECEIPT", [(g["Cash"]["id"], 100, 0), (g["Capital Account"]["id"], 0, 100)], user_id=me.id)
    assert r.status_code == 201


# ---------- books ----------
def test_books_are_seeded_with_tally_ledgers_and_bank_accounts(client, make_user):
    me = accountant(make_user)
    client.post("/api/accounts", json={"name": "HDFC Salary", "kind": "SALARY", "opening_balance": "2500"}, headers=headers(me))
    client.post("/api/accounts", json={"name": "Amex", "kind": "CREDIT_CARD", "opening_balance": "-800"}, headers=headers(me))
    g = ledgers(client, me)
    for name in ("Capital Account", "Drawings", "Cash", "Suspense", "Output CGST", "Input IGST", "TDS Payable", "Depreciation"):
        assert name in g
    assert g["HDFC Salary"]["group_code"] == "BANK_ACCOUNTS"
    assert (D(g["HDFC Salary"]["opening_balance"]), g["HDFC Salary"]["opening_side"]) == (D(2500), "DR")
    assert g["Amex"]["group_code"] == "CURRENT_LIABILITIES" and g["Amex"]["opening_side"] == "CR"


def test_first_financial_year_comes_from_the_earliest_transaction(client, make_user):
    me = accountant(make_user)
    old = date(today_local().year - 2, 2, 10)
    client.post("/api/expenses", json={"expense_date": str(old), "item_name": "Tea", "amount": 10}, headers=headers(me))
    s = client.get(f"{API}/settings", headers=headers(me)).json()
    assert s["books_start"] == str(fy_start(fy_of(old)))
    assert s["years"][-1]["start"] == s["books_start"]


# ---------- the double-entry rules ----------
def test_unbalanced_voucher_is_rejected(client, make_user):
    me = accountant(make_user)
    g = ledgers(client, me)
    r = voucher(client, me, "JOURNAL", [(g["Rent"]["id"], 100, 0), (g["Capital Account"]["id"], 0, 99)])
    assert r.status_code == 400 and "must be equal" in r.json()["detail"]


@pytest.mark.parametrize("dr, cr, code", [(D("10.005"), 0, 422), (10, 10, 400), (0, 0, 400)])
def test_bad_lines_are_rejected(client, make_user, dr, cr, code):
    me = accountant(make_user)
    g = ledgers(client, me)
    r = voucher(client, me, "JOURNAL", [(g["Rent"]["id"], dr, cr), (g["Capital Account"]["id"], 0, 10)])
    assert r.status_code == code


def test_cannot_post_to_another_users_ledger(client, make_user):
    me, other = accountant(make_user), accountant(make_user, "ravi")
    theirs = ledgers(client, other)["Cash"]["id"]
    r = voucher(client, me, "JOURNAL", [(ledgers(client, me)["Rent"]["id"], 50, 0), (theirs, 0, 50)])
    assert r.status_code == 400


def test_voucher_type_rules(client, make_user):
    me = accountant(make_user)
    g = ledgers(client, me)
    rent, cash, capital = g["Rent"]["id"], g["Cash"]["id"], g["Capital Account"]["id"]
    assert voucher(client, me, "PAYMENT", [(rent, 50, 0), (capital, 0, 50)]).status_code == 400
    assert voucher(client, me, "RECEIPT", [(rent, 50, 0), (capital, 0, 50)]).status_code == 400
    assert voucher(client, me, "CONTRA", [(cash, 50, 0), (capital, 0, 50)]).status_code == 400
    assert voucher(client, me, "PAYMENT", [(rent, 50, 0), (cash, 0, 50)]).status_code == 201


def test_future_dated_voucher_is_rejected(client, make_user):
    me = accountant(make_user)
    g = ledgers(client, me)
    r = voucher(client, me, "JOURNAL", [(g["Rent"]["id"], 5, 0), (g["Capital Account"]["id"], 0, 5)],
                day=today_local() + timedelta(days=1))
    assert r.status_code == 400


def test_numbering_is_per_type_and_restarts_each_financial_year(client, make_user, db):
    me = accountant(make_user)
    g = ledgers(client, me)
    lines = [(g["Rent"]["id"], 5, 0), (g["Capital Account"]["id"], 0, 5)]
    this_fy = fy_start(fy_of(today_local()))
    last_fy_day = this_fy - timedelta(days=1)
    a = voucher(client, me, "JOURNAL", lines, day=this_fy).json()
    b = voucher(client, me, "JOURNAL", lines, day=this_fy).json()
    c = voucher(client, me, "JOURNAL", lines, day=last_fy_day).json()
    assert (a["voucher_no"], b["voucher_no"], c["voucher_no"]) == (1, 2, 1)
    assert c["fy"] == a["fy"] - 1


def test_reversal_mirrors_the_voucher_and_nets_to_zero(client, make_user):
    me = accountant(make_user)
    g = ledgers(client, me)
    v = voucher(client, me, "JOURNAL", [(g["Rent"]["id"], 700, 0), (g["Capital Account"]["id"], 0, 700)]).json()
    rev = client.post(f"{API}/vouchers/{v['id']}/reverse", json={}, headers=headers(me))
    assert rev.status_code == 201
    rev = rev.json()
    assert rev["is_reversal"] and rev["reversal_of_id"] == v["id"]
    assert [(l["debit"], l["credit"]) for l in rev["lines"]] == [(l["credit"], l["debit"]) for l in v["lines"]]
    assert D(ledgers(client, me)["Rent"]["balance"]) == 0
    original = client.get(f"{API}/vouchers/{v['id']}", headers=headers(me)).json()
    assert original["status"] == "REVERSED" and original["reversed_by_id"] == rev["id"]
    # Neither can be reversed again
    assert client.post(f"{API}/vouchers/{v['id']}/reverse", json={}, headers=headers(me)).status_code == 400
    assert client.post(f"{API}/vouchers/{rev['id']}/reverse", json={}, headers=headers(me)).status_code == 400


# ---------- period lock ----------
def test_locked_period_refuses_posting_and_reversal(client, make_user):
    me = accountant(make_user)
    g = ledgers(client, me)
    lines = [(g["Rent"]["id"], 5, 0), (g["Capital Account"]["id"], 0, 5)]
    day = today_local() - timedelta(days=10)
    v = voucher(client, me, "JOURNAL", lines, day=day).json()
    r = client.put(f"{API}/settings", json={"lock_date": str(day)}, headers=headers(me))
    assert r.status_code == 200 and r.json()["locked_by"] == "asha"

    assert voucher(client, me, "JOURNAL", lines, day=day).status_code == 409
    assert client.post(f"{API}/vouchers/{v['id']}/reverse", json={}, headers=headers(me)).status_code == 409
    # Even an admin
    admin = make_user("root", admin=True)
    assert voucher(client, admin, "JOURNAL", lines, day=day, user_id=me.id).status_code == 409
    # An open period still works
    assert voucher(client, me, "JOURNAL", lines, day=day + timedelta(days=1)).status_code == 201


def test_lock_date_cannot_be_in_the_future(client, make_user):
    me = accountant(make_user)
    r = client.put(f"{API}/settings", json={"lock_date": str(today_local() + timedelta(days=1))}, headers=headers(me))
    assert r.status_code == 400


def test_opening_balances_freeze_once_locked(client, make_user):
    me = accountant(make_user)
    h = headers(me)
    acc = client.post("/api/accounts", json={"name": "SBI", "opening_balance": 100}, headers=h).json()
    client.put(f"{API}/settings", json={"lock_date": str(today_local() - timedelta(days=1))}, headers=h)
    r = client.put(f"/api/accounts/{acc['id']}", json={"name": "SBI", "opening_balance": 200}, headers=h)
    assert r.status_code == 409
    assert client.get("/api/accounts", headers=h).json()[0]["opening_balance"] in ("100.00", "100")


# ---------- ledgers ----------
def test_ledger_rules(client, make_user):
    me = accountant(make_user)
    h = headers(me)
    ok = client.post(f"{API}/ledgers", json={"name": "Office Laptop", "group_code": "FIXED_ASSETS", "opening_balance": "45000"}, headers=h)
    assert ok.status_code == 201 and ok.json()["opening_side"] == "DR"
    assert client.post(f"{API}/ledgers", json={"name": "office laptop", "group_code": "FIXED_ASSETS"}, headers=h).status_code == 409
    nominal = client.post(f"{API}/ledgers", json={"name": "Misc", "group_code": "INDIRECT_EXPENSES", "opening_balance": "5"}, headers=h)
    assert nominal.status_code == 400
    assert client.post(f"{API}/ledgers", json={"name": "X", "group_code": "NOPE"}, headers=h).status_code == 400

    g = ledgers(client, me)
    assert client.delete(f"{API}/ledgers/{g['Drawings']['id']}", headers=h).status_code == 400
    voucher(client, me, "JOURNAL", [(g["Rent"]["id"], 5, 0), (g["Capital Account"]["id"], 0, 5)])
    assert client.delete(f"{API}/ledgers/{g['Rent']['id']}", headers=h).status_code == 400
    assert client.delete(f"{API}/ledgers/{ok.json()['id']}", headers=h).status_code == 204


def test_books_are_set_up_once(client, make_user, db):
    me = accountant(make_user)
    for _ in range(3):
        client.get(f"{API}/settings", headers=headers(me))
    assert len(db.scalars(select(AccLedger).where(AccLedger.code == "CAPITAL", AccLedger.user_id == me.id)).all()) == 1
    assert db.scalar(select(Voucher.id)) is None
