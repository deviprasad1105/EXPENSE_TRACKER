"""Input limits on money, the report date-range cap, and safe Excel/PDF exports."""

from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.timeutil import today_local

from .conftest import headers


def _expense(client, user, amount, **extra):
    body = {"expense_date": today_local().isoformat(), "item_name": "Lunch", "category": "Food", "amount": amount, **extra}
    return client.post("/api/expenses", json=body, headers=headers(user))


# ---------- money limits ----------
@pytest.mark.parametrize("amount", ["100.555", "12345678901.00", "0"])
def test_expense_amount_outside_numeric_12_2_is_rejected(client, make_user, amount):
    assert _expense(client, make_user("asha"), amount).status_code == 422


def test_expense_amount_at_the_limit_is_accepted(client, make_user):
    assert _expense(client, make_user("asha"), "9999999999.99").status_code == 201


@pytest.mark.parametrize(
    "url, body",
    [
        ("/api/funds", {"fund_date": "2026-01-01", "amount": "1.001"}),
        ("/api/accounts", {"name": "Cash", "opening_balance": "1.001"}),
        ("/api/budgets", {"monthly_limit": "1.001"}),
        ("/api/daily-items", {"name": "Tea", "default_amount": "1.001"}),
    ],
)
def test_other_money_inputs_reject_a_third_decimal(client, make_user, url, body):
    assert client.post(url, json=body, headers=headers(make_user("asha"))).status_code == 422


# ---------- date-range cap ----------
@pytest.mark.parametrize("url", ["/api/dashboard", "/api/reports/export"])
def test_huge_date_range_is_refused(client, make_user, url):
    r = client.get(url, params={"start": "0001-01-01", "end": "9999-12-31"}, headers=headers(make_user("asha")))
    assert r.status_code == 400
    assert "too long" in r.json()["detail"]


def test_reversed_date_range_is_refused(client, make_user):
    r = client.get("/api/dashboard", params={"start": "2026-02-01", "end": "2026-01-01"}, headers=headers(make_user("asha")))
    assert r.status_code == 400


# ---------- safe exports ----------
def test_excel_export_stores_formula_like_text_as_text(client, make_user):
    me = make_user("asha")
    _expense(client, me, 10, item_name='=HYPERLINK("http://evil.example","x")', note="=1+1")
    r = client.get("/api/reports/export", params={"format": "xlsx"}, headers=headers(me))
    assert r.status_code == 200

    sheet = load_workbook(BytesIO(r.content))["Detailed List"]
    item, note = sheet.cell(row=2, column=2), sheet.cell(row=2, column=7)
    assert item.data_type == "s" and item.value.startswith("=HYPERLINK")
    assert note.data_type == "s"
    # The export's own total stays a working formula
    total = next(c for c in sheet["E"] if isinstance(c.value, str) and c.value.startswith("=SUM("))
    assert total.data_type == "f"


def test_pdf_export_survives_markup_characters(client, make_user):
    me = make_user("asha")
    _expense(client, me, 10, item_name="Tea <b & co", note="<img src='x'> & more")
    r = client.get("/api/reports/export", params={"format": "pdf"}, headers=headers(me))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
