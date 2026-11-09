"""Fixes from the end-to-end test of a full year's export (DEF-01 … DEF-10): wallet accounts, pass-through money,
"maybe" category suggestions and unusually large rows, accounts that would go below zero, long notes, choices
remembered between imports, rows never imported twice, the Undo count, the paged transfer list and the real row
count of a file over the limit."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.importer.service import suggest_kind, suggest_treatment
from app.models import Account, Expense
from app.timeutil import today_local

from .conftest import headers

D = Decimal
API = "/api/imports"
DAY = today_local() - timedelta(days=90)


def d(n: int) -> str:
    return (DAY + timedelta(days=n)).strftime("%d/%m/%Y")


def upload(client, user, content, name="records.csv"):
    res = client.post(f"{API}/extract", files={"file": (name, content.encode())}, headers=headers(user))
    assert res.status_code == 200, res.text
    return res.json()


def preview(client, user, extracted, options=None):
    t = extracted["tables"][extracted["table_index"]]
    res = client.post(f"{API}/preview", json={"rows": t["rows"], "mapping": extracted["guess"], "options": options or {}},
                      headers=headers(user))
    assert res.status_code == 200, res.text
    return res.json()


def run(client, user, extracted, options):
    prev = preview(client, user, extracted, options)
    batch = client.post(API, json={"filename": extracted["filename"], "file_type": "csv", "file_hash": extracted["file_hash"]},
                        headers=headers(user)).json()
    res = client.post(f"{API}/{batch['id']}/rows", json={"records": prev["records"], "options": options}, headers=headers(user))
    assert res.status_code == 200, res.text
    return prev, client.post(f"{API}/{batch['id']}/finish", headers=headers(user)).json()


# ---------- DEF-01: wallets ----------
def test_fastag_and_other_prepaid_accounts_are_wallets():
    assert suggest_kind("FasTag") == "WALLET"
    assert suggest_kind("Fastag ICICI") == "WALLET"
    assert suggest_kind("Paytm") == "WALLET"
    assert suggest_kind("Metal one Credit card") == "CREDIT_CARD"
    assert suggest_kind("SBI Bank") == "SAVINGS"


def test_an_account_that_would_go_below_zero_is_flagged(client, make_user):
    me = make_user("asha")
    text = ("Date,Type,Amount,Category,Account,To account,Notes\n"
            f"{d(0)},Transfer,100,,Axis Bank,FasTag,Fastag recharge\n"
            f"{d(1)},Expense,250,Toll,FasTag,,Express way\n"
            f"{d(1)},Expense,20,Toll,FasTag,,Karjan\n"
            f"{d(2)},Expense,500,Food,Card,,Dinner\n"
            f"{d(2)},Income,1000,Salary,Axis Bank,,Salary\n")
    ex = upload(client, me, text)
    opts = {"account_mode": "column", "create_accounts": {"axis bank": "SAVINGS", "fastag": "WALLET", "card": "CREDIT_CARD"},
            "category_map": {"toll": "Transport"}}
    prev = preview(client, me, ex, opts)
    # FasTag ends at 100 - 270 = -170; a credit card may go below zero; Axis Bank ends at +900
    assert prev["summary"]["account_warnings"] == [{"account": "FasTag", "kind": "WALLET", "balance": "-170.00"}]


# ---------- DEF-04: pass-through money ----------
def test_money_given_and_cash_received_are_suggested_as_pass_through():
    for name in ("Money given", "Cash received", "Money Return", "Reimbursement", "Lent to Ravi", "Borrowed"):
        assert suggest_treatment(name) == "new:RECEIVABLE:Money with others", name
    for name in ("Loan", "Salary", "Food"):
        assert suggest_treatment(name) is None, name


# ---------- DEF-05: "maybe" suggestions and large rows ----------
PHONE = ("Date,Type,Amount,Category,Notes\n"
         + "".join(f"{d(i)},Expense,{a},Mobile,Recharge\n" for i, a in enumerate((299, 359, 299, 319, 75)))
         + f"{d(6)},Expense,54647,Mobile,New iPhone\n"
         f"{d(7)},Expense,600,Fuel,Petrol\n"
         f"{d(8)},Expense,300,Gift Given,Cake\n")


def test_weak_suggestions_are_only_maybes_and_large_rows_are_flagged(client, make_user):
    me = make_user("asha")
    prev = preview(client, me, upload(client, me, PHONE))
    cats = {c["value"]: (c["suggested"], c["maybe"]) for c in prev["category_values"]}
    assert cats["Fuel"] == ("Transport", None)        # sure: applied by "Use the suggestions"
    assert cats["Mobile"] == (None, "Bills")          # recharges or a phone: the user decides
    assert cats["Gift Given"] == (None, "Shopping")
    flagged = [r for r in prev["records"] if r["to_check"]]
    assert [r["description"] for r in flagged] == ["New iPhone"]
    assert "check that this is the right category" in flagged[0]["warnings"][-1]
    assert prev["summary"]["to_check"] == 1


# ---------- DEF-06: long notes ----------
def test_a_long_note_is_kept_in_full(client, make_user, db):
    me = make_user("asha")
    long = "2 Fries (R), 1 Mcsaver Mcspicy chicken Meal, 1 Jain Meal, 1 Corn & Cheese Burger + chocolate shake, 1 Corn & cheese burger meal"
    ex = upload(client, me, f"Date,Type,Amount,Category,Notes\n{d(0)},Expense,2705,Food,\"{long}\"\n")
    _, done = run(client, me, ex, {})
    assert done["rows_imported"] == 1
    e = db.scalar(select(Expense))
    assert e.item_name == long[:100] and e.note == long


# ---------- DEF-03: choices remembered ----------
def test_choices_are_remembered_for_the_next_import(client, make_user):
    me = make_user("asha")
    mine = client.post("/api/accounts", json={"name": "Axis Spending", "kind": "SAVINGS", "opening_balance": "0"},
                       headers=headers(me)).json()
    first = ("Date,Type,Amount,Category,Account,Notes\n"
             f"{d(0)},Expense,600,Fuel,Axis Bk,Petrol\n"
             f"{d(1)},Expense,3000,Investment,Axis Bk,Midcap fund\n"
             f"{d(2)},Expense,250,Toll charges,FasTag,Toll\n")
    opts = {"account_mode": "column", "account_map": {"axis bk": mine["id"]}, "create_accounts": {"fastag": "WALLET"},
            "category_map": {"fuel": "Transport", "toll charges": "Tolls"},
            "category_treatment": {"investment": "new:INVESTMENT:Investments"}}
    _, done = run(client, me, upload(client, me, first), opts)
    assert done["rows_imported"] == 3

    nxt = ("Date,Type,Amount,Category,Account,Notes\n"
           f"{d(30)},Expense,700,Fuel,AXIS BK,Petrol\n"
           f"{d(31)},Expense,3000,Investment,Axis Bk,Midcap fund\n"
           f"{d(32)},Expense,90,Food,Axis Bk,Tea\n")
    prev = preview(client, me, upload(client, me, nxt, "next year.csv"), {"account_mode": "column"})
    accounts = {a["name"]: a["id"] for a in client.get("/api/accounts", headers=headers(me)).json()}
    assert prev["remembered"] == {
        "category_map": {"fuel": "Transport"},
        "category_treatment": {"investment": f"id:{accounts['Investments']}"},
        "account_map": {"axis bk": mine["id"]},
    }


# ---------- DEF-08: never imported twice ----------
def test_rows_imported_before_cannot_be_ticked_back_in(client, make_user, db):
    me = make_user("asha")
    text = f"Date,Type,Amount,Category,Notes\n{d(0)},Expense,40,Food,Tea\n{d(1)},Expense,60,Food,Coffee\n"
    _, done = run(client, me, upload(client, me, text), {})
    assert done["rows_imported"] == 2
    ex = upload(client, me, text, "again.csv")
    prev = preview(client, me, ex)
    for r in prev["records"]:
        r["include"] = True  # even when ticked by hand
    batch = client.post(API, json={"filename": "again.csv", "file_type": "csv", "file_hash": ex["file_hash"]}, headers=headers(me)).json()
    res = client.post(f"{API}/{batch['id']}/rows", json={"records": prev["records"], "options": {}}, headers=headers(me)).json()
    assert res["imported"] == 0 and res["skipped"] == 2
    detail = client.get(f"{API}/{batch['id']}", headers=headers(me)).json()
    assert {r["reason"] for r in detail["records"]} == {"Already imported from an earlier upload"}
    assert len(db.scalars(select(Expense)).all()) == 2


# ---------- DEF-07 / DEF-09: Undo ----------
def test_undo_counts_records_deleted_by_hand_separately(client, make_user, db):
    me = make_user("asha", accounting=True)
    assert client.get("/api/accounting/settings", headers=headers(me)).status_code == 200  # opens the books
    text = f"Date,Type,Amount,Category,Notes\n{d(0)},Expense,40,Food,Tea\n{d(1)},Expense,60,Food,Coffee\n"
    _, done = run(client, me, upload(client, me, text), {})
    tea = db.scalar(select(Expense).where(Expense.item_name == "Tea"))
    assert client.delete(f"/api/expenses/{tea.id}", headers=headers(me)).status_code in (200, 204)
    assert client.get(f"{API}/{done['id']}", headers=headers(me)).json()["books"] is True
    res = client.post(f"{API}/{done['id']}/undo", headers=headers(me)).json()
    assert (res["removed"], res["already_deleted"], res["status"]) == (1, 1, "UNDONE")


# ---------- DEF-02: paged transfer list ----------
def test_the_transfer_list_is_paged(client, make_user):
    me = make_user("asha")
    h = headers(me)
    a = client.post("/api/accounts", json={"name": "A", "kind": "SAVINGS", "opening_balance": "1000"}, headers=h).json()
    b = client.post("/api/accounts", json={"name": "B", "kind": "SAVINGS", "opening_balance": "0"}, headers=h).json()
    for i in range(5):
        t = {"from_account_id": a["id"], "to_account_id": b["id"], "amount": str(10 + i),
             "transfer_date": (today_local() - timedelta(days=i)).isoformat()}
        assert client.post("/api/accounts/transfers", json=t, headers=h).status_code == 201
    first = client.get("/api/accounts/transfers", params={"limit": 2}, headers=h)
    assert first.headers["x-total-count"] == "5" and [D(t["amount"]) for t in first.json()] == [D(10), D(11)]
    last = client.get("/api/accounts/transfers", params={"offset": 4, "limit": 2}, headers=h).json()
    assert [D(t["amount"]) for t in last] == [D(14)]
    assert len(client.get("/api/accounts/transfers", headers=h).json()) == 5  # default: up to 200, as before


# ---------- DEF-10: real row count ----------
def test_a_table_over_the_limit_reports_its_real_size(client, make_user, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "import_max_records", 10)
    me = make_user("asha")
    text = "Date,Type,Amount,Notes\n" + "".join(f"{d(i % 60)},Expense,{i + 1},Row {i}\n" for i in range(500))
    t = upload(client, me, text)["tables"][0]
    assert t["truncated"] is True and t["total_rows"] == 501 and len(t["rows"]) == 210


def test_a_file_past_the_read_cap_reports_its_real_size(client, make_user, monkeypatch):
    from app.config import settings
    from app.importer import extract

    monkeypatch.setattr(extract, "MAX_LINES", 300)  # reading stops here…
    monkeypatch.setattr(settings, "import_max_records", 10)  # …past the import limit (10 + 200 shown)
    me = make_user("asha")
    text = "Date,Type,Amount,Notes\n" + "".join(f"{d(i % 60)},Expense,{i + 1},Row {i}\n" for i in range(349))
    t = upload(client, me, text)["tables"][0]
    assert t["truncated"] is True and t["total_rows"] == 350


# ---------- Migration: older records move the books' start back, nothing is lost ----------
def test_records_older_than_the_books_move_their_start_back(client, make_user, db):
    from app.accounting.chart import fy_of, fy_start

    me = make_user("asha", accounting=True)
    start = date.fromisoformat(client.get("/api/accounting/settings", headers=headers(me)).json()["books_start"])
    old = start - timedelta(days=3 * 365)
    text = f"Date,Type,Amount,Category,Notes\n{old:%d/%m/%Y},Expense,40,Food,Tea\n{d(1)},Expense,60,Food,Coffee\n"
    ex = upload(client, me, text)
    prev, _ = run(client, me, ex, {})
    assert prev["books_start"] == str(start)
    assert prev["books_moves_to"] == str(fy_start(fy_of(old))) and prev["before_books"] == 1
    books = client.get("/api/accounting/settings", headers=headers(me)).json()
    assert books["books_start"] == str(fy_start(fy_of(old)))
