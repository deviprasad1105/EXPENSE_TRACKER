"""Phases 3-7: every report, checked against figures worked out by hand for a two-year sole proprietorship.

Year 1 (FY0): Capital ₹1,20,000 and HDFC ₹1,00,000 opening, opening stock ₹20,000. Credit purchases ₹50,000,
cash sales ₹80,000, rent ₹12,000 (business), food ₹3,000 (personal → Drawings). Closing stock ₹30,000.
  Gross profit 80,000 + 30,000 − 20,000 − 50,000 = 40,000; net profit 40,000 − 12,000 = 28,000.
Year 2 (FY1): GST sale to Acme (same state) ₹20,000 + 18% (CGST 1,800 + SGST 1,800); GST purchase from Supplier
(other state) ₹5,000 + 12% IGST 600; paid Supplier ₹20,000; bought a computer ₹30,000; depreciation ₹3,000;
salary ₹25,000 brought in (capital); food ₹2,000 (drawings). Closing stock ₹28,000.
  Gross profit 20,000 + 28,000 − 30,000 − 5,000 = 13,000; net profit 13,000 − 3,000 = 10,000.
"""

from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.accounting.chart import fy_of, fy_start
from app.accounting.exports import inr
from app.timeutil import today_local

from .conftest import headers

D = Decimal
API = "/api/accounting"
TODAY = today_local()
FY1 = fy_of(TODAY)
FY0 = FY1 - 1


def d0(n):
    return fy_start(FY0) + timedelta(days=n)


def d1(n):
    return min(fy_start(FY1) + timedelta(days=n), TODAY)


class Books:
    def __init__(self, client, user):
        self.client, self.user, self.h = client, user, headers(user)

    def get(self, path, **params):
        r = self.client.get(f"{API}{path}", params=params, headers=self.h)
        assert r.status_code == 200, r.text
        return r.json()

    def post(self, path, body, expect=201):
        r = self.client.post(f"{API}{path}", json=body, headers=self.h)
        assert r.status_code == expect, r.text
        return r.json()

    def put(self, path, body):
        r = self.client.put(f"{API}{path}", json=body, headers=self.h)
        assert r.status_code == 200, r.text
        return r.json()

    @property
    def ledger(self):
        return {g["name"]: g for g in self.get("/ledgers")}

    def voucher(self, vtype, day, *lines):
        g = self.ledger
        return self.post("/vouchers", {"voucher_type": vtype, "voucher_date": str(day), "narration": vtype.lower(),
                                       "lines": [{"ledger_id": g[n]["id"], "debit": str(dr), "credit": str(cr)} for n, dr, cr in lines]})


@pytest.fixture
def books(client, make_user):
    me = make_user("asha", accounting=True)
    h = headers(me)
    client.post("/api/accounts", json={"name": "HDFC", "opening_balance": "100000"}, headers=h)
    for item, cat, amt in (("Shop rent", "Rent", 12000), ("Groceries", "Food", 3000)):
        client.post("/api/expenses", json={"expense_date": str(d0(10)), "item_name": item, "category": cat, "amount": amt}, headers=h)
    b = Books(client, me)
    assert b.get("/settings")["books_start"] == str(fy_start(FY0))
    cap = b.ledger["Capital Account"]
    b.put(f"/ledgers/{cap['id']}", {"name": "Capital Account", "group_code": "CAPITAL", "opening_balance": "120000", "opening_side": "CR"})
    b.post("/ledgers", {"name": "Supplier", "group_code": "SUNDRY_CREDITORS", "state": "Karnataka"})
    b.post("/ledgers", {"name": "Acme", "group_code": "SUNDRY_DEBTORS", "state": "Maharashtra"})
    b.put("/settings", {"state": "Maharashtra", "business_name": "Asha Traders"})
    b.put("/stock", {"fy": FY0, "opening_value": "20000", "closing_value": "30000"})
    b.put("/stock", {"fy": FY1, "closing_value": "28000"})
    # Year 1
    b.voucher("JOURNAL", d0(20), ("Purchases", 50000, 0), ("Supplier", 0, 50000))
    b.voucher("RECEIPT", d0(30), ("HDFC", 80000, 0), ("Sales", 0, 80000))
    # Year 2
    g = b.ledger
    b.post("/invoices", {"kind": "SALES", "invoice_date": str(d1(2)), "invoice_no": "A-001", "party_ledger_id": g["Acme"]["id"],
                         "place_of_supply": "Maharashtra", "items": [{"ledger_id": g["Sales"]["id"], "taxable_value": "20000", "gst_rate": "18"}]})
    b.post("/invoices", {"kind": "PURCHASE", "invoice_date": str(d1(3)), "invoice_no": "S-77", "party_ledger_id": g["Supplier"]["id"],
                         "place_of_supply": "Karnataka", "items": [{"ledger_id": g["Purchases"]["id"], "taxable_value": "5000", "gst_rate": "12"}]})
    b.voucher("PAYMENT", d1(5), ("Supplier", 20000, 0), ("HDFC", 0, 20000))
    b.voucher("PAYMENT", d1(7), ("Computers & Equipment", 30000, 0), ("HDFC", 0, 30000))
    b.voucher("JOURNAL", d1(9), ("Depreciation", 3000, 0), ("Computers & Equipment", 0, 3000))
    hdfc_account = client.get("/api/accounts", headers=h).json()[0]["id"]
    client.post("/api/funds", json={"fund_date": str(d1(12)), "amount": "25000", "source": "Salary", "account_id": hdfc_account}, headers=h)
    client.post("/api/expenses", json={"expense_date": str(d1(14)), "item_name": "Dinner", "category": "Food", "amount": 2000,
                                       "account_id": hdfc_account}, headers=h)
    return b


def amounts(section_list):
    return {i["name"]: D(i["amount"]) for s in section_list for i in s["items"]}


# ---------- Journal & Ledger ----------
def test_journal_lists_every_voucher_of_the_period(books):
    j = books.get("/reports/journal", fy=FY1)
    assert len(j["entries"]) == 7
    assert D(j["total_debit"]) == D(j["total_credit"])
    sale = next(e for e in j["entries"] if e["voucher_type"] == "SALES")
    assert {l["ledger"]: (D(l["debit"]), D(l["credit"])) for l in sale["lines"]} == {
        "Acme": (D(23600), 0), "Sales": (0, D(20000)), "Output CGST": (0, D(1800)), "Output SGST": (0, D(1800)),
    }


def test_ledger_has_opening_running_and_closing_balance(books):
    hdfc = books.ledger["HDFC"]["id"]
    r = books.get("/reports/ledger", ledger_id=hdfc, fy=FY1)
    assert (D(r["opening"]), r["opening_side"]) == (D(165000), "Dr")
    assert [e["particulars"] for e in r["entries"]] == ["By Supplier", "By Computers & Equipment", "To Capital Account", "By Drawings"]
    assert (D(r["closing"]), r["closing_side"]) == (D(138000), "Dr")
    assert D(r["entries"][-1]["balance"]) == D(138000)


def test_nominal_ledger_starts_the_year_at_zero(books):
    r = books.get("/reports/ledger", ledger_id=books.ledger["Sales"]["id"], fy=FY1)
    assert D(r["opening"]) == 0 and (D(r["closing"]), r["closing_side"]) == (D(20000), "Cr")


# ---------- Trial Balance ----------
def test_trial_balance_balances_with_tally_lines(books):
    tb = books.get("/reports/trial-balance", fy=FY1)
    t = tb["totals"]
    assert tb["balanced"]
    assert D(t["closing_debit"]) == D(t["closing_credit"]) == D(232200)
    rows = {r["name"]: D(r["closing"]) for r in tb["rows"]}
    assert rows["Opening Stock"] == D(30000)
    assert rows["Profit & Loss A/c (earlier years)"] == D(-28000)
    assert rows["Drawings"] == D(5000)
    assert "Difference in opening balances" not in rows


def test_trial_balance_of_the_first_year_balances_too(books):
    tb = books.get("/reports/trial-balance", fy=FY0)
    assert tb["balanced"]
    assert {r["name"]: D(r["closing"]) for r in tb["rows"]}["Opening Stock"] == D(20000)


# ---------- Trading, P&L, Balance Sheet ----------
def test_trading_and_profit_and_loss(books):
    t = books.get("/reports/trading", fy=FY1)
    assert D(t["trading"]["gross_profit"]) == D(13000)
    assert D(t["trading"]["total"]) == D(48000)
    assert D(t["net_profit"]) == D(10000)
    pl = t["profit_loss"]
    assert amounts(pl["debit"]) == {"Depreciation": D(3000)}
    # Personal spending is drawings: it never reaches the P&L
    assert "Drawings" not in amounts(pl["debit"])
    assert D(books.get("/reports/trading", fy=FY0)["net_profit"]) == D(28000)


def test_balance_sheet_tallies_and_carries_profit_into_capital(books):
    bs = books.get("/reports/balance-sheet")
    assert bs["balanced"] and D(bs["total_assets"]) == D(bs["total_liabilities"]) == D(216600)
    capital = next(s for s in bs["liabilities"] if s["title"] == "Capital Account")
    assert {i["name"]: D(i["amount"]) for i in capital["items"]} == {
        "Capital Account": D(145000), "Add: Profit of earlier years": D(28000), "Add: Net Profit for the year": D(10000),
        "Less: Drawings": D(-5000),
    }
    assert D(capital["total"]) == D(178000)
    assets = amounts(bs["assets"])
    assert assets["Closing Stock"] == D(28000) and assets["HDFC"] == D(138000) and assets["Computers & Equipment"] == D(27000)


def test_balance_sheet_at_the_end_of_the_first_year(books):
    bs = books.get("/reports/balance-sheet", as_of=str(date(FY0 + 1, 3, 31)))
    assert bs["balanced"]
    assert D(bs["net_profit"]) == D(28000)


# ---------- Cash Flow ----------
def test_cash_flow_indirect_method_reconciles(books):
    cf = books.get("/reports/cash-flow", fy=FY1)
    assert cf["reconciled"]
    operating, investing, financing = cf["sections"]
    assert D(operating["total"]) == D(-20000)
    assert D(investing["total"]) == D(-30000)
    assert D(financing["total"]) == D(23000)
    assert (D(cf["opening_cash"]), D(cf["closing_cash"]), D(cf["net_change"])) == (D(165000), D(138000), D(-27000))
    items = {i["name"]: D(i["amount"]) for i in operating["items"]}
    assert items["Net profit for the period"] == D(10000) and items["Add: Depreciation"] == D(3000)
    assert {i["name"]: D(i["amount"]) for i in financing["items"]} == {
        "Capital introduced / (withdrawn)": D(25000), "Drawings": D(-2000),
    }


# ---------- exports ----------
@pytest.mark.parametrize("path", ["/reports/journal", "/reports/trial-balance", "/reports/trading", "/reports/profit-loss",
                                  "/reports/balance-sheet", "/reports/cash-flow"])
def test_every_report_exports_to_excel_and_pdf(books, path):
    for fmt, magic in (("xlsx", b"PK"), ("pdf", b"%PDF")):
        r = books.client.get(f"{API}{path}", params={"format": fmt}, headers=books.h)
        assert r.status_code == 200 and r.content.startswith(magic), (path, fmt, r.text[:200])
        assert "attachment" in r.headers["content-disposition"]


def test_ledger_export_and_indian_number_format(books):
    r = books.client.get(f"{API}/reports/ledger", params={"ledger_id": books.ledger["HDFC"]["id"], "format": "xlsx"}, headers=books.h)
    ws = load_workbook(BytesIO(r.content)).active
    assert ws["A1"].value == "Asha Traders"
    assert inr(D("12345678.5")) == "1,23,45,678.50" and inr(D("999")) == "999.00" and inr(D("-100000")) == "-1,00,000.00"


# ---------- periods ----------
def test_report_period_must_sit_inside_one_financial_year(books):
    r = books.client.get(f"{API}/reports/trial-balance", params={"start": str(d0(5)), "end": str(d1(5))}, headers=books.h)
    assert r.status_code == 400 and "one financial year" in r.json()["detail"]


def test_stock_rules(books):
    r = books.client.put(f"{API}/stock", json={"fy": FY1, "opening_value": "1"}, headers=books.h)
    assert r.status_code == 400
    years = {y["fy"]: y for y in books.get("/stock")}
    assert years[FY1]["opening"] == "30000.00" and years[FY1]["closing"] == "28000.00"
    books.put("/settings", {"lock_date": str(date(FY0 + 1, 3, 31))})
    r = books.client.put(f"{API}/stock", json={"fy": FY0, "closing_value": "31000"}, headers=books.h)
    assert r.status_code == 409


# ---------- GST ----------
def test_gst_rounding_and_split(client, make_user):
    me = make_user("ravi", accounting=True)
    b = Books(client, me)
    b.put("/settings", {"state": "Karnataka"})
    b.post("/ledgers", {"name": "Cust", "group_code": "SUNDRY_DEBTORS"})
    g = b.ledger
    body = {"kind": "SALES", "invoice_date": str(TODAY), "invoice_no": "1", "party_ledger_id": g["Cust"]["id"],
            "place_of_supply": "Karnataka", "items": [{"ledger_id": g["Sales"]["id"], "taxable_value": "999.99", "gst_rate": "5"}]}
    p = b.post("/invoices/preview", body, expect=200)
    assert p["taxes"] == {"CGST": "25.00", "SGST": "25.00"} and p["total"] == "1049.99"
    p = b.post("/invoices/preview", {**body, "place_of_supply": "Kerala"}, expect=200)
    assert p["taxes"] == {"IGST": "50.00"} and not p["intra_state"]
    b.post("/invoices", body)
    assert client.post(f"{API}/invoices", json=body, headers=b.h).status_code == 409  # same number again
    bad = {**body, "invoice_no": "2", "items": [{"ledger_id": g["Rent"]["id"], "taxable_value": "10", "gst_rate": "18"}]}
    assert client.post(f"{API}/invoices", json=bad, headers=b.h).status_code == 400
    assert client.post(f"{API}/invoices/preview", json={**body, "items": [{**body["items"][0], "gst_rate": "7"}]},
                       headers=b.h).status_code == 400


def test_gst_needs_the_business_state(client, make_user):
    me = make_user("ravi", accounting=True)
    b = Books(client, me)
    b.post("/ledgers", {"name": "Cust", "group_code": "SUNDRY_DEBTORS"})
    g = b.ledger
    r = client.post(f"{API}/invoices", headers=b.h, json={
        "kind": "SALES", "invoice_date": str(TODAY), "invoice_no": "1", "party_ledger_id": g["Cust"]["id"],
        "place_of_supply": "Karnataka", "items": [{"ledger_id": g["Sales"]["id"], "taxable_value": "100", "gst_rate": "5"}]})
    assert r.status_code == 400 and "state" in r.json()["detail"]


# ---------- Bank reconciliation ----------
def _statement():
    rows = [
        ("Txn Date", "Narration", "Chq No", "Withdrawal Amt", "Deposit Amt", "Closing Balance"),
        (d1(6), "NEFT SUPPLIER", "", "20,000.00", "", "1,45,000.00"),
        (d1(8), "COMPUTER STORE", "", "30,000.00", "", "1,15,000.00"),
        (d1(12), "SALARY CREDIT", "", "", "25,000.00", "1,40,000.00"),
        (d1(15), "SMS CHARGES INCL GST", "", "118.00", "", "1,39,882.00"),
        (d1(16), "INTEREST CREDIT", "", "", "50.00", "1,39,932.00"),
    ]
    text = "\n".join(",".join(f'"{c:%d/%m/%Y}"' if isinstance(c, date) else f'"{c}"' for c in r) for r in rows)
    return ("hdfc.csv", text.encode(), "text/csv")


def _import(books, hdfc):
    preview = books.client.post(f"{API}/bank/preview", files={"file": _statement()}, headers=books.h).json()
    guess = preview["guess"]
    assert (guess["header_row"], guess["date_col"], guess["withdrawal_col"], guess["deposit_col"], guess["balance_col"]) == (0, 0, 3, 4, 5)
    import json
    r = books.client.post(f"{API}/bank/import", data={"ledger_id": str(hdfc), "mapping": json.dumps(guess)},
                          files={"file": _statement()}, headers=books.h)
    assert r.status_code == 200, r.text
    return r.json()


def test_bank_statement_import_auto_match_and_reconcile(books):
    if d1(16) == TODAY and (TODAY - fy_start(FY1)).days < 16:
        pytest.skip("needs a few weeks of the financial year")
    hdfc = books.ledger["HDFC"]["id"]
    first = _import(books, hdfc)
    assert first["added"] == 5 and first["auto_matched"] == 3
    assert _import(books, hdfc)["added"] == 0  # the same file again adds nothing

    brs = books.get("/reports/bank-reconciliation", ledger_id=hdfc)
    assert D(brs["books_balance"]) == D(138000)
    by_title = {s["title"].split(":")[0]: s for s in brs["sections"]}
    assert [i["amount"] for i in brs["sections"][0]["items"]] == ["2000.00"]  # the dinner hasn't reached the bank
    assert D(brs["bank_balance"]) == D(139932) == D(brs["statement_balance"])
    assert D(brs["difference"]) == 0

    # Book the bank charges from the statement line; they're matched at once
    ws = books.get("/bank/workspace", ledger_id=hdfc)
    charge = next(s for s in ws["statement"] if s["description"].startswith("SMS"))
    books.post("/bank/voucher", {"ledger_id": hdfc, "statement_line_id": charge["id"],
                                 "other_ledger_id": books.ledger["Bank Charges"]["id"]})
    brs = books.get("/reports/bank-reconciliation", ledger_id=hdfc)
    assert D(brs["books_balance"]) == D(137882) and D(brs["difference"]) == 0
    assert brs["unmatched_statement_lines"] == 1


def test_manual_match_checks_amount_and_direction(books):
    hdfc = books.ledger["HDFC"]["id"]
    _import(books, hdfc)
    ws = books.get("/bank/workspace", ledger_id=hdfc)
    interest = next(s for s in ws["statement"] if s["description"] == "INTEREST CREDIT")
    dinner = next(b for b in ws["book"] if D(b["credit"]) == D(2000))
    r = books.client.post(f"{API}/bank/match", json={"ledger_id": hdfc, "statement_line_id": interest["id"],
                                                    "voucher_line_id": dinner["line_id"]}, headers=books.h)
    assert r.status_code == 400
    matched = next(s for s in ws["statement"] if s["matched_line_id"])
    assert books.post("/bank/unmatch", {"ledger_id": hdfc, "statement_line_id": matched["id"]}, expect=200)["ok"]
    r = books.client.post(f"{API}/bank/match", json={"ledger_id": hdfc, "statement_line_id": matched["id"],
                                                    "voucher_line_id": matched["matched_line_id"]}, headers=books.h)
    assert r.status_code == 200


def test_reversed_voucher_loses_its_bank_match(books, client):
    hdfc = books.ledger["HDFC"]["id"]
    _import(books, hdfc)
    # The salary fund was matched; editing it reverses its voucher, so the match is released
    fund = client.get("/api/funds", headers=books.h).json()[0]
    client.delete(f"/api/funds/{fund['id']}", headers=books.h)
    ws = books.get("/bank/workspace", ledger_id=hdfc)
    salary = next(s for s in ws["statement"] if s["description"] == "SALARY CREDIT")
    assert salary["matched_line_id"] is None


def test_closing_stock_warning_only_when_the_business_holds_stock(client, make_user, books):
    # The fixture business entered this year's closing stock
    assert books.get("/reports/trading", fy=FY1)["closing_stock_missing"] is False
    # A service business (no stock, no purchases) is never warned
    me = make_user("ravi", accounting=True)
    client.post("/api/expenses", json={"expense_date": str(TODAY), "item_name": "Rent", "category": "Rent", "amount": 100}, headers=headers(me))
    service = Books(client, me)
    assert service.get("/reports/trading")["closing_stock_missing"] is False
    assert service.get("/reports/balance-sheet")["closing_stock_missing"] is False
    # Once it buys goods, the missing closing stock is flagged
    service.voucher("PAYMENT", TODAY, ("Purchases", 500, 0), ("Cash", 0, 500))
    assert service.get("/reports/trading")["closing_stock_missing"] is True
