"""Migrating a whole history from another app (Money Manager style export): transfers between accounts, opening
balances, accounts created by the import, notes as descriptions, bulk category choices, and categories that are not
spending or income (investments, money paid for others). The account balances must come out exactly as in the file."""

import csv
import io
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.importer.parse import guess_mapping, parse_records
from app.models import Account, AccountTransfer, Expense, Fund
from app.timeutil import today_local

from .conftest import headers

D = Decimal
API = "/api/imports"
DAY = today_local() - timedelta(days=200)


def d(n: int) -> str:
    return (DAY + timedelta(days=n)).strftime("%d-%b-%y")


# The layout of the Money Manager export: TYPE "(+) Income" / "(-) Expense" / "(*) Transfer", transfers as
# "From->To" in ACCOUNT, opening balances as "Balance cf" rows, and what the money was for in NOTES
MM = (
    "Date,TYPE,AMOUNT,CATEGORY,ACCOUNT,NOTES \r\n"
    f"{d(0)},(+) Income,302.05,Balance cf ,Axis Bank,\"Balance cf\n \"\r\n"
    f"{d(0)},(-) Expense,179,Entertainment,.Credit Card,Amazon prime \r\n"
    f"{d(0)},(+) Income,15940.75,Balance cf ,SBI Bank,Balance cf  \r\n"
    f"{d(0)},(+) Income,1150,Balance cf ,Cash,Balance cf  \r\n"
    f"{d(1)},(+) Income,22260,Salary,Axis Bank,Salary \r\n"
    f"{d(1)},(-) Expense,20,Food,Cash,lemon Juice \r\n"
    f"{d(2)},(-) Expense,530,Fuel,Axis Bank,Car Petrol \r\n"
    f"{d(3)},(*) Transfer,3000,  -  ,Axis Bank->SBI Bank,Internet (Transfer to SBI) \r\n"
    f"{d(3)},(-) Expense,6095,Internet ,SBI Bank,Internet \r\n"
    f"{d(4)},(-) Expense,3000,Investment,Axis Bank,Axis Midcap Fund (Mutual fund) \r\n"
    f"{d(5)},(-) Expense,100,Paid on Behalf of Someone ,Cash,Vaibhav \r\n"
    f"{d(6)},(+) Income,100,Reimbursement,Axis Bank,received money from Vaibhav paid in cash for him \r\n"
    f"{d(7)},(*) Transfer,1358.02,  -  ,Axis Bank->.Credit Card,credit card Payment via Cred \r\n"
    f"{d(8)},(*) Transfer,1000,  -  ,Axis Bank->Cash,Cash withdrawal \r\n"
    f"{d(9)},(*) Transfer,5016,  -  ,Axis Bank->SBI Bank, \r\n"
    f"{d(9)},(*) Transfer,5016,  -  ,SBI Bank->Axis Bank, \r\n"
    f"{d(10)},(+) Income,9988.12,Mutual funds Withdrawal,Axis Bank,Mutual funds Withdrawal \r\n"
    f"{d(11)},(+) Income,160,Dividend,SBI Bank,Infosys Dividend \r\n"
    f"{d(12)},(-) Expense,1200,Food,Axis Bank,\"Snacks payment on behalf of Shantanu.\nAte french fries. \"\r\n"
    f"{d(13)},(*) Transfer,1200,  -  ,Axis Bank->SBI Bank,don't know yet 😅 \r\n"
)


def true_balances(text: str) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(Decimal)
    for r in csv.DictReader(io.StringIO(text)):
        kind, amount, acc = r["TYPE"].strip(), D(r["AMOUNT"]), r["ACCOUNT"].strip()
        if kind.startswith("(*)"):
            a, b = [x.strip() for x in acc.split("->")]
            out[a] -= amount
            out[b] += amount
        else:
            out[acc] += amount if kind.startswith("(+)") else -amount
    return out


def upload(client, user, content, name="Money Manager.csv"):
    res = client.post(f"{API}/extract", files={"file": (name, content.encode())}, headers=headers(user))
    assert res.status_code == 200, res.text
    return res.json()


def preview(client, user, extracted, options):
    t = extracted["tables"][extracted["table_index"]]
    res = client.post(f"{API}/preview", json={"rows": t["rows"], "mapping": extracted["guess"], "options": options},
                      headers=headers(user))
    assert res.status_code == 200, res.text
    return res.json()


def run(client, user, extracted, options):
    prev = preview(client, user, extracted, options)
    batch = client.post(API, json={"filename": extracted["filename"], "file_type": "csv", "file_hash": extracted["file_hash"]},
                        headers=headers(user)).json()
    created = []
    for i in range(0, len(prev["records"]), 4):  # small chunks: accounts created in one chunk are reused in the next
        res = client.post(f"{API}/{batch['id']}/rows", json={"records": prev["records"][i:i + 4], "options": options},
                          headers=headers(user))
        assert res.status_code == 200, res.text
        created += res.json()["accounts_created"]
    done = client.post(f"{API}/{batch['id']}/finish", headers=headers(user)).json()
    return prev, done, created


def all_new(prev, *, skip=()):
    """What the page's "Create all as new" buttons send: every unknown category and account created."""
    cats = {c["value"].lower(): c["value"].strip() for c in prev["category_values"]
            if c["expense_rows"] and not c["known"] and c["value"].lower() not in skip}
    accounts = {a["value"].lower(): a["suggested_kind"] for a in prev["account_values"] if a["ref"] is None}
    return cats, accounts


# ---------- reading the layout ----------
def test_money_manager_layout_is_understood():
    from app.importer.extract import extract

    rows = extract(MM.encode(), "mm.csv")[0].rows  # as the upload reads it
    m = guess_mapping(rows)
    # No description column: the NOTES column is used as the description
    assert (m.date, m.type, m.amount, m.category, m.account, m.description, m.note) == (0, 1, 2, 3, 4, 5, None)
    recs, _ = parse_records(rows, m, today_local())
    by = {r["row_no"]: r for r in recs}
    assert (by[2]["kind"], by[2]["amount"], by[2]["account"]) == ("OPENING", "302.05", "Axis Bank")
    assert (by[3]["kind"], by[3]["description"]) == ("EXPENSE", "Amazon prime")
    assert (by[9]["kind"], by[9]["account"], by[9]["to_account"]) == ("TRANSFER", "Axis Bank", "SBI Bank")
    assert by[20]["description"] == "Snacks payment on behalf of Shantanu. Ate french fries."
    assert [r["kind"] for r in recs].count("TRANSFER") == 6 and [r["kind"] for r in recs].count("OPENING") == 3


def test_an_unknown_type_is_never_guessed():
    rows = [["Date", "TYPE", "AMOUNT", "ACCOUNT"], [d(1), "(?) Adjustment", "50", "Cash"]] + [
        [d(1), "(-) Expense", "20", "Cash"], [d(2), "(+) Income", "90", "Cash"], [d(3), "(-) Expense", "10", "Cash"]]
    recs, _ = parse_records(rows, guess_mapping(rows), today_local())
    assert recs[0]["kind"] is None and "Unknown type" in recs[0]["parse_errors"][0]


def test_separate_from_and_to_columns_make_a_transfer():
    rows = [["Date", "Amount", "From account", "To account", "Note"], [d(1), "500", "HDFC", "Paytm Wallet", "Top up"]]
    m = guess_mapping(rows)
    assert (m.account, m.to_account) == (2, 3)
    recs, _ = parse_records(rows, m, today_local())
    assert (recs[0]["kind"], recs[0]["account"], recs[0]["to_account"]) == ("TRANSFER", "HDFC", "Paytm Wallet")


# ---------- the review ----------
def test_review_lists_accounts_to_create_and_suggests_categories(client, make_user):
    me = make_user("asha")
    ex = upload(client, me, MM)
    prev = preview(client, me, ex, {"account_mode": "column"})
    accounts = {a["value"]: a for a in prev["account_values"]}
    assert {k: (a["ref"], a["suggested_kind"], a["new_name"]) for k, a in accounts.items()} == {
        "Axis Bank": (None, "SAVINGS", "Axis Bank"), "SBI Bank": (None, "SAVINGS", "SBI Bank"),
        "Cash": (None, "CASH", "Cash"), ".Credit Card": (None, "CREDIT_CARD", "Credit Card")}
    statuses = {r["row_no"]: r["status"] for r in prev["records"]}
    assert statuses[9] == "NEEDS_ACCOUNT" and statuses[2] == "NEEDS_ACCOUNT"
    cats = {c["value"]: c for c in prev["category_values"]}
    assert cats["Fuel"]["suggested"] == "Transport" and cats["Internet"]["suggested"] == "Bills"
    assert cats["Investment"]["suggested_treatment"] == "new:INVESTMENT:Investments"
    assert cats["Reimbursement"]["suggested_treatment"] == "new:RECEIVABLE:Money with others"
    assert cats["Paid on Behalf of Someone"]["suggested_treatment"] == "new:RECEIVABLE:Money with others"


def test_accounts_match_only_exactly_or_by_last_4_digits(client, make_user):
    me = make_user("asha")
    client.post("/api/accounts", json={"name": "HDFC Salary", "last4": "4321"}, headers=headers(me))
    rows = [["Date", "Item", "Category", "Amount", "Account"], [d(1), "Tea", "Food", "20", "HDFC"],
            [d(1), "Bus", "Transport", "30", "HDFC XX4321"], [d(1), "Book", "Shopping", "300", "hdfc-salary"]]
    res = client.post(f"{API}/preview", json={"rows": rows, "mapping": guess_mapping(rows).model_dump(),
                                             "options": {"account_mode": "column"}}, headers=headers(me)).json()
    assert [r["account_label"] for r in res["records"]] == [None, "HDFC Salary", "HDFC Salary"]


# ---------- the whole migration ----------
def test_whole_history_imports_with_exact_balances(client, make_user, db):
    me = make_user("asha")
    ex = upload(client, me, MM)
    opts = {"account_mode": "column"}
    first = preview(client, me, ex, opts)
    cats, new_accounts = all_new(first, skip=("investment", "paid on behalf of someone"))
    opts.update(category_map=cats, create_accounts=new_accounts, category_treatment={
        "investment": "new:INVESTMENT:Investments", "mutual funds withdrawal": "new:INVESTMENT:Investments",
        "paid on behalf of someone": "new:RECEIVABLE:Money with others", "reimbursement": "new:RECEIVABLE:Money with others",
    })
    prev, done, created = run(client, me, ex, opts)
    assert prev["summary"]["invalid"] == 0 and prev["summary"]["needs_account"] == 0 and prev["summary"]["needs_category"] == 0
    assert (prev["summary"]["transfers"], prev["summary"]["openings"]) == (10, 3)  # 6 transfers + 4 treated rows
    assert done["status"] == "COMPLETED" and done["rows_imported"] == 20
    assert sorted(created) == ["Axis Bank", "Cash", "Credit Card", "Investments", "Money with others", "SBI Bank"]

    balances = {a["name"]: D(a["balance"]) for a in client.get("/api/accounts", headers=headers(me)).json()}
    expected = true_balances(MM)
    # The file's own accounts end exactly where the file says…
    for name in ("Axis Bank", "SBI Bank", "Cash"):
        assert balances[name] == expected[name], name
    assert balances["Credit Card"] == expected[".Credit Card"]
    # …and the money that wasn't spending sits in its own accounts
    assert balances["Investments"] == D("3000") - D("9988.12")
    assert balances["Money with others"] == D("100") - D("100")
    kinds = {a["name"]: a["kind"] for a in client.get("/api/accounts", headers=headers(me)).json()}
    assert (kinds["Credit Card"], kinds["Cash"], kinds["Investments"], kinds["Money with others"]) == (
        "CREDIT_CARD", "CASH", "INVESTMENT", "RECEIVABLE")

    # Spending and income leave out transfers, opening balances and the treated categories
    assert D(done["expense_total"]) == D("179") + D("20") + D("530") + D("6095") + D("1200")
    assert D(done["income_total"]) == D("22260") + D("160")
    db.expire_all()
    assert db.scalar(select(func.count(AccountTransfer.id))) == 10
    amazon = db.scalar(select(Expense).where(Expense.amount == D("179")))
    assert (amazon.item_name, amazon.category) == ("Amazon prime", "Entertainment")

    # Uploading it again adds nothing
    again = preview(client, me, upload(client, me, MM, "again.csv"), opts)
    assert again["summary"]["chosen"] == 0 and again["summary"]["imported_before"] == 20


def test_undo_takes_everything_back_including_opening_balances(client, make_user, db):
    me = make_user("asha")
    ex = upload(client, me, MM)
    opts = {"account_mode": "column"}
    cats, new_accounts = all_new(preview(client, me, ex, opts))
    opts.update(category_map=cats, create_accounts=new_accounts)
    _, done, _ = run(client, me, ex, opts)
    res = client.post(f"{API}/{done['id']}/undo", headers=headers(me)).json()
    assert res["status"] == "UNDONE" and res["removed"] == 20
    db.expire_all()
    assert db.scalar(select(func.count(Expense.id))) == 0 and db.scalar(select(func.count(Fund.id))) == 0
    assert db.scalar(select(func.count(AccountTransfer.id))) == 0
    # The accounts the import created stay (other records may use them), back at a zero balance
    assert all(D(a["balance"]) == 0 for a in client.get("/api/accounts", headers=headers(me)).json())
    assert db.scalar(select(func.count(Account.id))) == 4


def test_transfers_and_openings_post_to_the_books(client, make_user):
    me = make_user("asha", accounting=True)
    assert client.get("/api/accounting/settings", headers=headers(me)).status_code == 200
    ex = upload(client, me, MM)
    opts = {"account_mode": "column"}
    cats, new_accounts = all_new(preview(client, me, ex, opts), skip=("investment",))
    opts.update(category_map=cats, create_accounts=new_accounts,
                category_treatment={"investment": "new:INVESTMENT:Investments"})
    _, done, _ = run(client, me, ex, opts)
    assert done["status"] == "COMPLETED"
    ledgers = {g["name"]: g for g in client.get("/api/accounting/ledgers", headers=headers(me)).json()}
    assert ledgers["Investments"]["group_code"] == "INVESTMENTS"
    assert (D(ledgers["SBI Bank"]["opening_balance"]), ledgers["SBI Bank"]["opening_side"]) == (D("15940.75"), "DR")
    vouchers = client.get("/api/accounting/vouchers", params={"hide_reversed": True, "size": 100}, headers=headers(me)).json()["items"]
    by_narration = {v["narration"]: v["voucher_type"] for v in vouchers}
    assert by_narration["Transfer Axis Bank → Investments – Axis Midcap Fund (Mutual fund)"] == "PAYMENT"
    assert by_narration["Transfer Axis Bank → SBI Bank – Internet (Transfer to SBI)"] == "CONTRA"
    fy = DAY.year if DAY.month >= 4 else DAY.year - 1
    tb = client.get("/api/accounting/reports/trial-balance", params={"fy": fy}, headers=headers(me))
    assert tb.status_code == 200 and tb.json()["balanced"] is True
    bs = client.get("/api/accounting/reports/balance-sheet", params={"fy": fy}, headers=headers(me)).json()
    assert bs["balanced"] is True


def test_opening_balances_are_refused_once_the_books_are_locked(client, make_user):
    me = make_user("asha", accounting=True)
    client.get("/api/accounting/settings", headers=headers(me))
    client.put("/api/accounting/settings", json={"lock_date": str(DAY - timedelta(days=30))}, headers=headers(me))
    ex = upload(client, me, MM)
    opts = {"account_mode": "column"}
    cats, new_accounts = all_new(preview(client, me, ex, opts))
    opts.update(category_map=cats, create_accounts=new_accounts)
    prev = preview(client, me, ex, opts)
    openings = [r for r in prev["records"] if r["kind"] == "OPENING"]
    assert openings and all(r["status"] == "LOCKED" for r in openings)
    assert all(r["status"] == "READY" for r in prev["records"] if r["kind"] == "TRANSFER" and r["row_no"] not in (17,))
