"""Importing past records: reading every file format, guessing columns, checking rows, importing, history and undo."""

import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from app.importer.parse import Mapping, guess_mapping, parse_date, parse_money, parse_records
from app.models import Expense, Fund, Notification, SplitBill
from app.timeutil import today_local

from .conftest import headers

D = Decimal
API = "/api/imports"
TODAY = today_local()
DAY1 = TODAY - timedelta(days=60)
DAY2 = TODAY - timedelta(days=45)


def dmy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def upload(client, user, name, content):
    return client.post(f"{API}/extract", files={"file": (name, content)}, headers=headers(user))


def run_import(client, user, extracted, *, options=None, table=None, mapping=None, edit=None, include=None):
    """extract → preview → (edit) → start → rows → finish, like the page does."""
    t = extracted["tables"][extracted["table_index"] if table is None else table]
    body = {"rows": t["rows"], "mapping": mapping or extracted["guess"], "options": options or {}}
    prev = client.post(f"{API}/preview", json=body, headers=headers(user))
    assert prev.status_code == 200, prev.text
    records = prev.json()["records"]
    for r in records:
        r["include"] = r["default_include"] if include is None else include(r)
        if edit:
            edit(r)
    batch = client.post(API, json={"filename": extracted["filename"], "file_type": extracted["file_type"],
                                   "file_hash": extracted["file_hash"]}, headers=headers(user)).json()
    for i in range(0, len(records), 2):  # tiny chunks: the chunked path is what matters
        res = client.post(f"{API}/{batch['id']}/rows", json={"records": records[i:i + 2], "options": options or {}},
                          headers=headers(user))
        assert res.status_code == 200, res.text
    done = client.post(f"{API}/{batch['id']}/finish", headers=headers(user)).json()
    return prev.json(), done


CSV = (
    "Date,Description,Category,Amount,Type\n"
    f"{dmy(DAY1)},Swiggy dinner,Food,\"₹1,250.50\",Expense\n"
    f"{dmy(DAY1)},Metro card,Transport,500,Expense\n"
    f"{dmy(DAY2)},November salary,Salary,\"75,000\",Income\n"
    f"{dmy(DAY2)},Petrol,Fuel,2000,Expense\n"
)


# ---------- parsing ----------
@pytest.mark.parametrize("text, order, expected", [
    ("05/11/2026", "DMY", date(2026, 11, 5)),
    ("05/11/2026", "MDY", date(2026, 5, 11)),
    ("2026-11-05", "DMY", date(2026, 11, 5)),
    ("2026-11-05 14:30:00", "DMY", date(2026, 11, 5)),
    ("05-Nov-2026", "DMY", date(2026, 11, 5)),
    ("5 November 2026", "DMY", date(2026, 11, 5)),
    ("Nov 5, 2026", "DMY", date(2026, 11, 5)),
    ("5th Nov 26", "DMY", date(2026, 11, 5)),
    ("05.11.26", "DMY", date(2026, 11, 5)),
    ("46331", "DMY", date(2026, 11, 5)),  # Excel serial number
    ("31/02/2026", "DMY", None),
    ("not a date", "DMY", None),
])
def test_dates_in_many_layouts(text, order, expected):
    assert parse_date(text, order) == expected


@pytest.mark.parametrize("text, amount, side", [
    ("₹1,23,456.50", D("123456.50"), None),
    ("Rs. 500/-", D("500"), None),
    ("INR 2,000", D("2000"), None),
    ("(200.00)", D("-200.00"), None),
    ("-45", D("-45"), None),
    ("45-", D("-45"), None),
    ("300.00 Dr", D("300.00"), "DR"),
    ("1,000 CR", D("1000"), "CR"),
    (1250.5, D("1250.5"), None),
])
def test_amounts_in_many_layouts(text, amount, side):
    value, s, err = parse_money(text)
    assert (value, s, err) == (amount, side, None)


def test_bad_amounts_are_reported():
    assert parse_money("abc")[2] and parse_money("12.345")[2] and parse_money("") == (None, None, None)


def test_columns_are_guessed_from_different_headings():
    bank = [["Txn Date", "Value Date", "Narration", "Chq./Ref.No.", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"],
            ["05/11/26", "05/11/26", "UPI-SWIGGY", "123", "350.00", "", "9,650.00"]]
    g = guess_mapping(bank)
    assert (g.header_row, g.date, g.description, g.debit, g.credit, g.amount) == (0, 0, 2, 4, 5, None)
    hdfc = [["HDFC BANK"], ["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"],
            ["01/08/26", "SALARY", "", "01/08/26", "", "65000", "90000"]]
    g = guess_mapping(hdfc)
    assert (g.header_row, g.date, g.description, g.debit, g.credit, g.amount, g.category) == (1, 0, 1, 4, 5, None, None)

    app_export = [["My expenses"], [], ["Date", "Item", "Category", "Cost", "Payment mode", "Notes"], ["2026-11-05", "Tea", "Food", "20", "UPI", ""]]
    g = guess_mapping([r for r in app_export if r])
    assert (g.header_row, g.date, g.description, g.category, g.amount, g.account, g.note) == (1, 0, 1, 2, 3, 4, 5)

    split = [["Date", "Description", "Total paid", "My share", "Split with"], ["05/11/2026", "Dinner", "1500", "500", "Ravi, Priya"]]
    g = guess_mapping(split)
    assert (g.amount, g.my_share, g.split_with) == (2, 3, 4)

    no_header = [["05/11/2026", "Tea", "20"], ["06/11/2026", "Lunch with the team", "350"]]
    g = guess_mapping(no_header)
    assert (g.header_row, g.date, g.description, g.amount) == (-1, 0, 1, 2)


def test_payment_type_column_is_not_taken_for_income_or_expense():
    rows = [["Date", "Item", "Amount", "Payment Type"], ["05/11/2026", "Tea", "20", "UPI"], ["05/11/2026", "Bus", "30", "Cash"]]
    assert guess_mapping(rows).type is None


def test_month_first_dates_are_detected():
    rows = [["Date", "Item", "Amount"], ["11/25/2026", "Tea", "20"], ["11/05/2026", "Bus", "30"]]
    assert guess_mapping(rows).date_order == "MDY"


def test_signed_amounts_and_debit_credit_columns_decide_the_type():
    signed = [["Date", "Details", "Amount"], [dmy(DAY1), "Coffee", "-120"], [dmy(DAY1), "Refund", "300"]]
    m = guess_mapping(signed)
    assert m.default_type == "SIGN"
    recs, _ = parse_records(signed, m, TODAY)
    assert [(r["kind"], r["amount"]) for r in recs] == [("EXPENSE", "120"), ("INCOME", "300")]

    dc = [["Date", "Narration", "Debit", "Credit"], [dmy(DAY1), "ATM", "2,000.00", ""], [dmy(DAY1), "NEFT IN", "", "5,000.00"],
          [dmy(DAY1), "Opening balance", "", "1,000.00"], [dmy(DAY1), "Closing balance", "", "4,000.00"]]
    recs, _ = parse_records(dc, guess_mapping(dc), TODAY)
    assert [(r["kind"], r["amount"]) for r in recs] == [
        ("EXPENSE", "2000.00"), ("INCOME", "5000.00"), ("OPENING", "1000.00"), ("INCOME", "4000.00")]
    assert recs[2]["parse_errors"] == ["An opening balance needs its account"]  # no account column in this file
    assert recs[3]["parse_warnings"]  # a closing balance is flagged and left out


def test_row_problems_are_explained():
    rows = [["Date", "Item", "Amount"], ["", "No date", "10"], ["32/13/2026", "Bad date", "10"], [dmy(DAY1), "No amount", ""],
            [dmy(TODAY + timedelta(days=3)), "Future", "10"], ["", "", ""]]
    recs, ignored = parse_records(rows, guess_mapping(rows), TODAY)
    assert ignored == 1
    assert recs[2]["kind"] == "EXPENSE"  # typing the amount in the review is then enough
    assert [r["parse_errors"][0] for r in recs] == [
        "Missing date", "Unrecognised date “32/13/2026”", "Missing amount", "Date is in the future"]


# ---------- reading files ----------
def test_csv_upload_is_read_and_guessed(client, make_user):
    me = make_user("asha")
    res = upload(client, me, "old.csv", CSV.encode())
    assert res.status_code == 200
    d = res.json()
    assert d["file_type"] == "csv" and len(d["tables"][0]["rows"]) == 5
    assert d["guess"]["date"] == 0 and d["guess"]["type"] == 4


def test_excel_every_sheet_is_a_table_and_dates_stay_dates(client, make_user):
    wb = Workbook()
    ws = wb.active
    ws.title = "Notes"
    ws.append(["Just some notes"])
    ws2 = wb.create_sheet("2025")
    ws2.append(["Date", "Item", "Amount"])
    ws2.append([DAY1, "Tea", 20.0])
    ws2.append([DAY2, "Groceries", 0.1 + 0.2])
    buf = io.BytesIO()
    wb.save(buf)
    d = upload(client, make_user("asha"), "past.xlsx", buf.getvalue()).json()
    assert [t["name"] for t in d["tables"]] == ["Notes", "2025"] and d["table_index"] == 1
    assert d["tables"][1]["rows"][1] == [DAY1.isoformat(), "Tea", "20"]
    assert d["tables"][1]["rows"][2][2] == "0.3"


def test_word_table_is_read(client, make_user):
    import docx

    doc = docx.Document()
    doc.add_paragraph("Household expenses")
    t = doc.add_table(rows=3, cols=3)
    for r, values in enumerate([["Date", "Item", "Amount"], [dmy(DAY1), "Milk", "60"], [dmy(DAY2), "Rent", "15,000"]]):
        for c, v in enumerate(values):
            t.cell(r, c).text = v
    buf = io.BytesIO()
    doc.save(buf)
    d = upload(client, make_user("asha"), "expenses.docx", buf.getvalue()).json()
    assert d["tables"][d["table_index"]]["rows"] == [["Date", "Item", "Amount"], [dmy(DAY1), "Milk", "60"], [dmy(DAY2), "Rent", "15,000"]]


def test_word_paragraphs_become_rows(client, make_user):
    import docx

    doc = docx.Document()
    doc.add_paragraph("My spending")
    doc.add_paragraph(f"{dmy(DAY1)} Auto rickshaw to office 120")
    doc.add_paragraph(f"{dmy(DAY2)} - Electricity bill - 1,450.00")
    buf = io.BytesIO()
    doc.save(buf)
    d = upload(client, make_user("asha"), "notes.docx", buf.getvalue()).json()
    rows = d["tables"][d["table_index"]]["rows"]
    assert rows == [[dmy(DAY1), "Auto rickshaw to office", "120"], [dmy(DAY2), "Electricity bill", "1,450.00"]]


def _pdf(draw) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4).build(draw())
    return buf.getvalue()


def test_pdf_table_is_read(client, make_user):
    from reportlab.platypus import Table, TableStyle

    data = [["Date", "Description", "Amount"], [dmy(DAY1), "Pharmacy", "845.00"], [dmy(DAY2), "Internet bill", "999.00"]]
    pdf = _pdf(lambda: [Table(data, style=TableStyle([("GRID", (0, 0), (-1, -1), 0.5, "black")]))])
    d = upload(client, make_user("asha"), "statement.pdf", pdf).json()
    assert d["tables"][d["table_index"]]["rows"] == data


def test_pdf_text_lines_are_split_into_columns(client, make_user):
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Preformatted

    text = f"Expense diary\n\nDate        Item              Amount\n{dmy(DAY1)}  Vegetables        240.00\n{dmy(DAY2)}  Movie tickets     600.00\n"
    pdf = _pdf(lambda: [Preformatted(text, getSampleStyleSheet()["Code"])])
    d = upload(client, make_user("asha"), "diary.pdf", pdf).json()
    rows = d["tables"][d["table_index"]]["rows"]
    assert [dmy(DAY1), "Vegetables", "240.00"] in rows and [dmy(DAY2), "Movie tickets", "600.00"] in rows


@pytest.mark.parametrize("name, content, words", [
    ("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100, "save it as .docx"),
    ("photo.jpg", b"\xff\xd8\xff", "isn't supported"),
    ("empty.csv", b"", "empty"),
    ("broken.xlsx", b"not a zip", "couldn't be opened"),
    ("broken.pdf", b"%PDF-1.4 garbage", "couldn't be opened"),
])
def test_unreadable_files_get_a_clear_message(client, make_user, name, content, words):
    res = upload(client, make_user("asha"), name, content)
    assert res.status_code == 400 and words in res.json()["detail"]


def test_scanned_pdf_without_text_is_refused(client, make_user):
    from reportlab.platypus import Spacer

    res = upload(client, make_user("asha"), "scan.pdf", _pdf(lambda: [Spacer(10, 10)]))
    assert res.status_code == 400 and "scanned" in res.json()["detail"]


def test_file_size_limit(client, make_user, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "import_max_bytes", 100)
    res = upload(client, make_user("asha"), "big.csv", CSV.encode() * 10)
    assert res.status_code == 400 and "too large" in res.json()["detail"]


def test_record_limit(client, make_user, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "import_max_records", 2)
    me = make_user("asha")
    d = upload(client, me, "old.csv", CSV.encode()).json()
    res = client.post(f"{API}/preview", json={"rows": d["tables"][0]["rows"], "mapping": d["guess"]}, headers=headers(me))
    assert res.status_code == 400 and "up to 2" in res.json()["detail"]


# ---------- checking ----------
def test_unknown_categories_must_be_mapped_and_known_ones_match_any_case(client, make_user):
    me = make_user("asha")
    d = upload(client, me, "old.csv", CSV.replace("Food", "food").encode()).json()
    rows = d["tables"][0]["rows"]
    prev = client.post(f"{API}/preview", json={"rows": rows, "mapping": d["guess"]}, headers=headers(me)).json()
    by = {r["description"]: r for r in prev["records"]}
    assert by["Swiggy dinner"]["status"] == "READY" and by["Swiggy dinner"]["category_final"] == "Food"
    assert by["Petrol"]["status"] == "NEEDS_CATEGORY" and "Fuel" in by["Petrol"]["problems"][0]
    assert by["November salary"]["status"] == "READY"  # income has a source, not a spending category
    fuel = next(c for c in prev["category_values"] if c["value"] == "Fuel")
    assert (fuel["rows"], fuel["mapped_to"], fuel["known"], fuel["suggested"]) == (1, None, False, "Transport")

    # Map to an existing category, or create a new one
    for target in ("Transport", "Vehicle fuel"):
        prev = client.post(f"{API}/preview", json={"rows": rows, "mapping": d["guess"], "options": {"category_map": {"fuel": target}}},
                           headers=headers(me)).json()
        petrol = next(r for r in prev["records"] if r["description"] == "Petrol")
        assert petrol["status"] == "READY" and petrol["category_final"] == target


def test_import_creates_expenses_and_income_with_their_dates(client, make_user, db):
    me = make_user("asha")
    client.post("/api/budgets", json={"scope": "OVERALL", "monthly_limit": "100"}, headers=headers(me))
    d = upload(client, me, "old.csv", CSV.encode()).json()
    prev, done = run_import(client, me, d, options={"category_map": {"fuel": "Transport"}})
    assert prev["summary"]["ready"] == 4
    assert done["status"] == "COMPLETED" and done["rows_imported"] == 4
    assert D(done["expense_total"]) == D("3750.50") and D(done["income_total"]) == D("75000")

    expenses = client.get("/api/expenses", params={"start": str(DAY1), "end": str(DAY2)}, headers=headers(me)).json()
    assert sorted((e["expense_date"], e["item_name"], D(e["amount"])) for e in expenses) == [
        (str(DAY1), "Metro card", D("500")), (str(DAY1), "Swiggy dinner", D("1250.50")), (str(DAY2), "Petrol", D("2000"))]
    # The edit window runs from the import, not from the old date
    assert all(e["editable"] and not e["locked"] for e in expenses)
    funds = client.get("/api/funds", headers=headers(me)).json()
    assert [(f["fund_date"], f["source"], D(f["amount"])) for f in funds] == [(str(DAY2), "Salary", D("75000"))]
    assert db.scalar(select(func.count(Notification.id)).where(Notification.kind == "BUDGET_ALERT")) == 0


def test_same_file_again_adds_nothing(client, make_user, db):
    me = make_user("asha")
    content = (CSV + f"{dmy(DAY1)},Metro card,Transport,500,Expense\n").encode()  # the same row twice: both are real
    d = upload(client, me, "old.csv", content).json()
    run_import(client, me, d, options={"category_map": {"fuel": "Transport"}})
    assert db.scalar(select(func.count(Expense.id))) == 4

    again = upload(client, me, "old (1).csv", content).json()
    assert again["already_imported"]["rows"] == 5
    first = client.post(f"{API}/preview", json={"rows": again["tables"][0]["rows"], "mapping": again["guess"]}, headers=headers(me)).json()
    assert first["records"][4]["warnings"][0].startswith("Same as row 3 of this file")
    prev, done = run_import(client, me, again, options={"category_map": {"fuel": "Transport"}})
    assert prev["summary"]["imported_before"] == 5 and prev["summary"]["chosen"] == 0
    assert done["rows_imported"] == 0 and done["rows_skipped"] == 5 and done["status"] == "FAILED"
    db.expire_all()
    assert db.scalar(select(func.count(Expense.id))) == 4


def test_records_already_in_the_app_are_flagged_and_left_out_unless_ticked(client, make_user, db):
    me = make_user("asha")
    client.post("/api/expenses", json={"expense_date": str(DAY1), "item_name": "Dinner (typed by hand)", "category": "Food",
                                       "amount": "1250.50"}, headers=headers(me))
    d = upload(client, me, "old.csv", CSV.encode()).json()
    prev, done = run_import(client, me, d, options={"category_map": {"fuel": "Transport"}})
    dup = next(r for r in prev["records"] if r["description"] == "Swiggy dinner")
    assert dup["status"] == "DUPLICATE" and not dup["default_include"] and "Dinner (typed by hand)" in dup["warnings"][0]
    assert done["rows_imported"] == 3 and done["rows_skipped"] == 1

    # Ticked on purpose: imported anyway
    d = upload(client, me, "other.csv", CSV.encode()).json()
    _, done = run_import(client, me, d, options={"category_map": {"fuel": "Transport"}},
                         include=lambda r: r["description"] == "Swiggy dinner")
    assert done["rows_imported"] == 1


def test_invalid_rows_are_recorded_with_their_reason(client, make_user):
    me = make_user("asha")
    content = ("Date,Item,Category,Amount\n"
               f"{dmy(DAY1)},Tea,Food,20\n"
               f"bad,Coffee,Food,30\n"
               f"{dmy(DAY1)},Juice,Food,\n").encode()
    d = upload(client, me, "mixed.csv", content).json()
    _, done = run_import(client, me, d)
    assert (done["status"], done["rows_imported"], done["rows_failed"]) == ("COMPLETED_WITH_ERRORS", 1, 2)
    detail = client.get(f"{API}/{done['id']}", headers=headers(me)).json()
    assert [(r["row_no"], r["status"], r["reason"]) for r in detail["records"]] == [
        (2, "IMPORTED", None), (3, "FAILED", "Unrecognised date “bad”"), (4, "FAILED", "Missing amount")]


def test_rows_corrected_in_the_review_are_imported(client, make_user):
    me = make_user("asha")
    d = upload(client, me, "mixed.csv", f"Date,Item,Category,Amount\nbad,Coffee,Food,30\n".encode()).json()

    def fix(r):
        r["date"] = str(DAY1)
    _, done = run_import(client, me, d, edit=fix, include=lambda r: True)
    assert done["rows_imported"] == 1


def test_records_typed_by_hand_without_a_file(client, make_user):
    me = make_user("asha")
    records = [{"row_no": 1, "kind": "EXPENSE", "date": str(DAY1), "description": "Gym fee", "category": "Health", "amount": "1500"}]
    chk = client.post(f"{API}/check", json={"records": records}, headers=headers(me)).json()
    assert chk["records"][0]["status"] == "READY"
    batch = client.post(API, json={"filename": "Typed by hand", "file_type": "manual"}, headers=headers(me)).json()
    assert client.post(f"{API}/{batch['id']}/rows", json={"records": records}, headers=headers(me)).json()["imported"] == 1


# ---------- accounts ----------
def test_accounts_fixed_or_from_the_file(client, make_user):
    me = make_user("asha")
    hdfc = client.post("/api/accounts", json={"name": "HDFC Salary", "last4": "1234", "is_primary": True}, headers=headers(me)).json()
    card = client.post("/api/accounts", json={"name": "ICICI Card", "kind": "CREDIT_CARD"}, headers=headers(me)).json()
    content = ("Date,Item,Category,Amount,Paid with\n"
               f"{dmy(DAY1)},Tea,Food,20,ICICI Card\n"
               f"{dmy(DAY1)},Book,Shopping,300,HDFC XX1234\n"
               f"{dmy(DAY1)},Bus,Transport,15,Cash\n").encode()
    d = upload(client, me, "acc.csv", content).json()
    assert d["guess"]["account"] == 4
    prev = client.post(f"{API}/preview", json={"rows": d["tables"][0]["rows"], "mapping": d["guess"],
                                               "options": {"account_mode": "column"}}, headers=headers(me)).json()
    assert [(r["account_id"], r["status"]) for r in prev["records"]] == [
        (card["id"], "READY"), (hdfc["id"], "READY"), (None, "NEEDS_ACCOUNT")]

    # "Cash" left without an account on purpose
    _, done = run_import(client, me, d, options={"account_mode": "column", "account_map": {"cash": None}})
    assert done["rows_imported"] == 3
    got = {e["item_name"]: e["account_id"] for e in client.get("/api/expenses", headers=headers(me)).json()}
    assert got == {"Tea": card["id"], "Book": hdfc["id"], "Bus": None}

    d = upload(client, me, "acc2.csv", content.replace(b"Tea,Food,20", b"Coffee,Food,45")).json()
    _, done = run_import(client, me, d, options={"account_mode": "fixed", "account_id": card["id"]})
    coffee = next(e for e in client.get("/api/expenses", headers=headers(me)).json() if e["item_name"] == "Coffee")
    assert coffee["account_id"] == card["id"]


def test_someone_elses_account_is_refused(client, make_user):
    me, other = make_user("asha"), make_user("ravi")
    theirs = client.post("/api/accounts", json={"name": "Theirs"}, headers=headers(other)).json()
    records = [{"row_no": 1, "kind": "EXPENSE", "date": str(DAY1), "description": "x", "category": "Food", "amount": "1"}]
    res = client.post(f"{API}/check", json={"records": records, "options": {"account_mode": "fixed", "account_id": theirs["id"]}},
                      headers=headers(me))
    assert res.status_code == 400


# ---------- split bills ----------
SPLIT_CSV = ("Date,Description,Category,Total paid,My share,Split with\n"
             f"{dmy(DAY1)},Goa hotel,Travel,9000,3000,\"Ravi, Priya\"\n"
             f"{dmy(DAY1)},Cab,Transport,600,,Ravi\n"
             f"{dmy(DAY1)},Gift for Amit,Gifts,1000,0,\n")


def test_split_rows_become_my_share_and_a_bill_for_the_others(client, make_user, db):
    me = make_user("asha", split=True)
    d = upload(client, me, "splits.csv", SPLIT_CSV.encode()).json()
    prev, done = run_import(client, me, d, options={"category_map": {"travel": "Travel", "gifts": "Gifts"}})
    assert [(r["kind"], r["my_share"]) for r in prev["records"]] == [("SPLIT", "3000"), ("SPLIT", "300.00"), ("SPLIT", "0")]
    assert done["rows_imported"] == 3

    mine = {e["item_name"]: D(e["amount"]) for e in client.get("/api/expenses", headers=headers(me)).json()}
    assert mine == {"Goa hotel": D("3000"), "Cab": D("300.00")}
    groups = client.get("/api/splits/groups", headers=headers(me)).json()
    assert [g["name"] for g in groups] == ["Imported split bills"]
    group = client.get(f"/api/splits/groups/{groups[0]['id']}", headers=headers(me)).json()
    bills = {b["title"]: b for b in group["bills"]}
    assert D(bills["Goa hotel"]["total_amount"]) == D("6000")
    shares = lambda b: sorted((s["member"]["full_name"], D(str(s["amount"]))) for s in bills[b]["shares"])
    assert shares("Goa hotel") == [("Priya", D("3000")), ("Ravi", D("3000"))]
    assert shares("Cab") == [("Ravi", D("300"))]
    assert shares("Gift for Amit") == [("Others", D("1000"))]
    assert all(b["paid_by"]["user_id"] == me.id for b in bills.values())
    # The others now owe 6000 + 300 + 1000
    assert D(str(groups[0]["my_net"])) == D("7300")


def test_split_rows_need_the_split_feature(client, make_user):
    me = make_user("asha", split=False)
    d = upload(client, me, "splits.csv", SPLIT_CSV.encode()).json()
    prev = client.post(f"{API}/preview", json={"rows": d["tables"][0]["rows"], "mapping": d["guess"]}, headers=headers(me)).json()
    assert all(r["status"] == "INVALID" and "plan" in r["problems"][0] for r in prev["records"])


# ---------- accounting ----------
def test_imports_post_to_the_books_and_locked_periods_are_refused(client, make_user, db):
    me = make_user("asha", accounting=True)
    client.post("/api/accounts", json={"name": "HDFC", "is_primary": True}, headers=headers(me))
    assert client.get("/api/accounting/settings", headers=headers(me)).status_code == 200
    client.put("/api/accounting/settings", json={"lock_date": str(DAY1)}, headers=headers(me))
    d = upload(client, me, "old.csv", CSV.encode()).json()
    prev, done = run_import(client, me, d, options={"category_map": {"fuel": "Transport"}})
    statuses = {r["description"]: r["status"] for r in prev["records"]}
    assert statuses == {"Swiggy dinner": "LOCKED", "Metro card": "LOCKED", "November salary": "READY", "Petrol": "READY"}
    assert (done["rows_imported"], done["rows_failed"]) == (2, 2)
    vouchers = client.get("/api/accounting/vouchers", params={"hide_reversed": True}, headers=headers(me)).json()["items"]
    assert sorted(v["source_type"] for v in vouchers if v["source_type"]) == ["EXPENSE", "FUND"]


# ---------- history and undo ----------
def test_history_and_undo_keep_edited_records(client, make_user, db):
    me = make_user("asha")
    d = upload(client, me, "old.csv", CSV.encode()).json()
    _, done = run_import(client, me, d, options={"category_map": {"fuel": "Transport"}})
    hist = client.get(API, headers=headers(me)).json()
    assert [(b["filename"], b["rows_imported"], b["status"]) for b in hist] == [("old.csv", 4, "COMPLETED")]

    metro = next(e for e in client.get("/api/expenses", headers=headers(me)).json() if e["item_name"] == "Metro card")
    client.put(f"/api/expenses/{metro['id']}", json={"amount": "550"}, headers=headers(me))
    detail = client.get(f"{API}/{done['id']}", headers=headers(me)).json()
    assert detail["undo_removes"] == 3 and detail["undo_keeps"] == [{"reason": "Edited after the import", "rows": 1}]

    res = client.post(f"{API}/{done['id']}/undo", headers=headers(me)).json()
    assert res == {"removed": 3, "kept": 1, "remaining": 0, "status": "PARTIALLY_UNDONE",
                   "accounts_removed": [], "accounts_deactivated": []}
    db.expire_all()
    assert [e.item_name for e in db.scalars(select(Expense))] == ["Metro card"]
    assert db.scalar(select(func.count(Fund.id))) == 0
    # Undone rows don't count as "imported before": the file can be imported again
    again = upload(client, me, "old.csv", CSV.encode()).json()
    prev = client.post(f"{API}/preview", json={"rows": again["tables"][0]["rows"], "mapping": again["guess"],
                                               "options": {"category_map": {"fuel": "Transport"}}}, headers=headers(me)).json()
    assert {r["description"]: r["status"] for r in prev["records"]}["Swiggy dinner"] == "READY"
    assert {r["description"]: r["status"] for r in prev["records"]}["Metro card"] == "IMPORTED_BEFORE"


def test_undo_removes_split_bills_and_reverses_the_books(client, make_user, db):
    me = make_user("asha", split=True, accounting=True)
    client.post("/api/accounts", json={"name": "HDFC", "is_primary": True}, headers=headers(me))
    client.get("/api/accounting/settings", headers=headers(me))
    d = upload(client, me, "splits.csv", SPLIT_CSV.encode()).json()
    _, done = run_import(client, me, d, options={"category_map": {"travel": "Travel", "gifts": "Gifts"}})
    assert db.scalar(select(func.count(SplitBill.id))) == 3
    assert client.post(f"{API}/{done['id']}/undo", headers=headers(me)).json()["status"] == "UNDONE"
    db.expire_all()
    assert db.scalar(select(func.count(SplitBill.id))) == 0 and db.scalar(select(func.count(Expense.id))) == 0
    active = client.get("/api/accounting/vouchers", params={"hide_reversed": True}, headers=headers(me)).json()["items"]
    assert not [v for v in active if v["source_type"]]


def test_undo_keeps_records_in_a_locked_period(client, make_user, db):
    me = make_user("asha", accounting=True)
    client.get("/api/accounting/settings", headers=headers(me))
    d = upload(client, me, "old.csv", CSV.encode()).json()
    _, done = run_import(client, me, d, options={"category_map": {"fuel": "Transport"}})
    client.put("/api/accounting/settings", json={"lock_date": str(DAY1)}, headers=headers(me))
    res = client.post(f"{API}/{done['id']}/undo", headers=headers(me)).json()
    assert res == {"removed": 2, "kept": 2, "remaining": 0, "status": "PARTIALLY_UNDONE",
                   "accounts_removed": [], "accounts_deactivated": []}


def test_imports_are_private(client, make_user):
    me, other = make_user("asha"), make_user("ravi")
    d = upload(client, me, "old.csv", CSV.encode()).json()
    _, done = run_import(client, me, d, options={"category_map": {"fuel": "Transport"}})
    assert client.get(f"{API}/{done['id']}", headers=headers(other)).status_code == 404
    assert client.post(f"{API}/{done['id']}/undo", headers=headers(other)).status_code == 404
    assert client.get(API, headers=headers(other)).json() == []


def test_mapping_default_is_valid():
    assert Mapping().header_row == 0


def test_rows_imported_before_show_as_such_even_without_a_category(client, make_user):
    me = make_user("asha")
    content = f"Date,Narration,Withdrawal,Deposit\n{dmy(DAY1)},UPI-SWIGGY,450,\n{dmy(DAY1)},SALARY,,65000\n".encode()
    d = upload(client, me, "bank.csv", content).json()
    _, done = run_import(client, me, d, options={"category_map": {"": "Food"}})
    assert done["rows_imported"] == 2
    again = upload(client, me, "bank.csv", content).json()
    prev = client.post(f"{API}/preview", json={"rows": again["tables"][0]["rows"], "mapping": again["guess"]}, headers=headers(me)).json()
    assert [(r["status"], r["can_import"]) for r in prev["records"]] == [("IMPORTED_BEFORE", False), ("IMPORTED_BEFORE", True)]
    assert "Choose a category" in prev["records"][0]["warnings"][-1]


def test_big_imports_are_undone_over_several_requests(client, make_user, db, monkeypatch):
    from app.importer import service

    monkeypatch.setattr(service, "UNDO_CHUNK", 2)
    me = make_user("asha")
    d = upload(client, me, "old.csv", CSV.encode()).json()
    _, done = run_import(client, me, d, options={"category_map": {"fuel": "Transport"}})
    results = [client.post(f"{API}/{done['id']}/undo", headers=headers(me)).json() for _ in range(2)]
    assert [(r["removed"], r["remaining"], r["status"]) for r in results] == [(2, 2, "PARTIALLY_UNDONE"), (2, 0, "UNDONE")]
    db.expire_all()
    assert db.scalar(select(func.count(Expense.id))) == 0 and db.scalar(select(func.count(Fund.id))) == 0


def test_admin_can_delete_a_user_with_import_history(client, make_user, db):
    from app.importer.models import ImportBatch, ImportRecord

    admin, me = make_user("boss", admin=True), make_user("asha")
    d = upload(client, me, "old.csv", CSV.encode()).json()
    run_import(client, me, d, options={"category_map": {"fuel": "Transport"}})
    assert client.delete(f"/api/admin/users/{me.id}", headers=headers(admin)).status_code == 200
    db.expire_all()
    assert db.scalar(select(func.count(ImportBatch.id))) == 0 and db.scalar(select(func.count(ImportRecord.id))) == 0


def test_a_split_someone_else_paid_is_my_share_owed_to_them(client, make_user, db):
    me = make_user("asha", split=True)
    text = ("Date,Description,Category,Total paid,My share,Paid by,Split with\n"
            f"{dmy(DAY1)},Dinner,Food,1200,400,Ravi,\n"
            f"{dmy(DAY1)},Movie,Entertainment,600,300,Me,Priya\n"
            f"{dmy(DAY1)},Cab,Transport,500,250,asha,Priya\n"
            f"{dmy(DAY1)},Lunch,Food,900,,Ravi,\n")
    d = upload(client, me, "paid by.csv", text.encode()).json()
    assert d["guess"]["paid_by"] == 5
    prev, done = run_import(client, me, d, options={"category_map": {"food": "Food", "entertainment": "Entertainment", "transport": "Transport"}})
    rows = {r["description"]: r for r in prev["records"]}
    assert (rows["Dinner"]["kind"], rows["Dinner"]["paid_by"], rows["Dinner"]["account_ref"]) == ("SPLIT", "Ravi", "none")
    assert "you owe them" in " ".join(rows["Dinner"]["warnings"])
    assert rows["Movie"]["paid_by"] is None and rows["Cab"]["paid_by"] is None  # "Me" and the user's own name
    assert rows["Lunch"]["status"] == "INVALID" and "Ravi paid" in rows["Lunch"]["problems"][0]
    assert done["rows_imported"] == 3

    dinner = db.scalar(select(Expense).where(Expense.item_name == "Dinner"))
    assert dinner.amount == D("400") and dinner.account_id is None and "paid by Ravi" in dinner.note
    groups = client.get("/api/splits/groups", headers=headers(me)).json()
    group = client.get(f"/api/splits/groups/{groups[0]['id']}", headers=headers(me)).json()
    bills = {b["title"]: b for b in group["bills"]}
    assert bills["Dinner"]["paid_by"]["full_name"] == "Ravi" and D(str(bills["Dinner"]["total_amount"])) == D("400")
    assert [(s["member"]["user_id"], D(str(s["amount"]))) for s in bills["Dinner"]["shares"]] == [(me.id, D("400"))]
    # I owe Ravi 400; Priya owes me 300 + 250
    assert D(str(groups[0]["my_net"])) == D("150")


def test_a_compressed_upload_reads_like_the_file(client, make_user):
    import gzip

    me = make_user("asha")
    plain = upload(client, me, "records.csv", CSV.encode()).json()
    packed = upload(client, me, "records.csv.gz", gzip.compress(CSV.encode())).json()
    assert packed["filename"] == "records.csv" and packed["file_type"] == "csv"
    assert packed["tables"] == plain["tables"] and packed["file_hash"] == plain["file_hash"]
    bad = upload(client, me, "records.csv.gz", b"not gzip")
    assert bad.status_code == 400


def test_a_compressed_upload_is_held_to_the_size_limit(client, make_user, monkeypatch):
    import gzip

    from app.config import settings

    monkeypatch.setattr(settings, "import_max_bytes", 1000)
    res = upload(client, make_user("asha"), "big.csv.gz", gzip.compress(CSV.encode() * 50))
    assert res.status_code == 413
