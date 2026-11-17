"""The statement check: the records read must match the statement's own totals, balances and running balance."""

from datetime import timedelta

from app.importer.parse import guess_mapping
from app.timeutil import today_local

from .conftest import headers

API = "/api/imports"
D = [(today_local() - timedelta(days=30 - i)).strftime("%d/%m/%Y") for i in range(5)]


def statement(rows=None, *, newest_first=False, summary="inline"):
    body = rows or [
        [D[0], "UPI/P2M/1111/SWIGGY/UPI", "450.00", "", "9,550.00"],
        [D[1], "NEFT CR ACME SALARY", "", "65,000.00", "74,550.00"],
        [D[2], "UPI/P2M/2222/UBER/UPI", "230.00", "", "74,320.00"],
        [D[3], "ATM WDL", "2,000.00", "", "72,320.00"],
    ]
    if newest_first:
        body = list(reversed(body))
    head = [["ACME BANK - Statement of account"], ["Opening Balance : 10,000.00"],
            ["Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]]
    if summary == "inline":
        tail = [["", "TRANSACTION TOTAL", "2,680.00", "65,000.00", ""], ["", "Closing Balance", "", "", "72,320.00"]]
    elif summary == "block":  # labels on one row, values on the next (common in Indian bank PDFs)
        tail = [["Opening Balance", "Dr Count", "Cr Count", "Debits", "Credits", "Closing Bal"],
                ["10,000.00", "3", "1", "2,680.00", "65,000.00", "72,320.00"]]
    else:
        tail = []
    return head + body + tail


def preview(client, user, rows, other_tables=()):
    res = client.post(f"{API}/preview", json={"rows": rows, "mapping": guess_mapping(rows).model_dump(),
                                              "other_tables": list(other_tables)}, headers=headers(user))
    assert res.status_code == 200, res.text
    return res.json()


def test_a_statement_that_adds_up_is_verified(client, make_user):
    me = make_user("asha")
    rows = statement()
    assert guess_mapping(rows).balance == 4
    prev = preview(client, me, rows)
    c = prev["statement_check"]
    assert c["ok"] and c["available"], c
    assert {x["key"]: x["ok"] for x in c["checks"]} == {"debits": True, "credits": True, "closing": True}
    assert (c["balance_order"], c["balances_verified"]) == ("oldest first", 4)  # the first one from the opening balance
    assert len(prev["records"]) == 4 and all(r["status"] == "READY" for r in prev["records"])


def test_a_misread_amount_is_caught_at_its_row(client, make_user):
    rows = statement()
    rows[5][2] = "23.00"  # the Uber row read as 23 instead of 230
    c = preview(client, make_user("asha"), rows)["statement_check"]
    assert not c["ok"]
    debits = next(x for x in c["checks"] if x["key"] == "debits")
    assert (debits["statement"], debits["read"], debits["ok"]) == ("₹2,680.00", "₹2,473.00", False)
    assert c["row_issues"][0]["row_no"] == 6


def test_a_missing_row_is_caught(client, make_user):
    me = make_user("asha")
    rows = statement()
    del rows[4]  # the salary row is lost
    prev = preview(client, me, rows)
    c = prev["statement_check"]
    assert not c["ok"]
    assert not next(x for x in c["checks"] if x["key"] == "credits")["ok"]
    assert c["row_issue_count"] >= 1
    # The row next to the gap says so
    flagged = [r for r in prev["records"] if any("misread or is missing" in w for w in r["warnings"])]
    assert flagged


def test_newest_first_statements_and_summary_blocks(client, make_user):
    me = make_user("asha")
    c = preview(client, me, statement(newest_first=True, summary="block"))["statement_check"]
    assert c["ok"], c
    assert c["balance_order"] == "newest first"
    assert {x["key"] for x in c["checks"]} == {"debits", "credits", "debit_count", "credit_count", "closing"}


def test_summary_in_another_table_of_the_file(client, make_user):
    rows = statement(summary=None)
    other = [[["Opening Balance", "Debits", "Credits", "Closing Bal"], ["10,000.00", "2,680.00", "65,000.00", "72,320.00"]]]
    c = preview(client, make_user("asha"), rows, other)["statement_check"]
    assert c["ok"] and len(c["checks"]) == 3


def test_a_file_with_nothing_to_check_against(client, make_user):
    rows = [["Date", "Item", "Amount"], [D[0], "Tea", "20"]]
    c = preview(client, make_user("asha"), rows)["statement_check"]
    assert c["available"] is False and c["checks"] == []


def test_fixing_the_row_makes_the_check_pass_again(client, make_user):
    me = make_user("asha")
    rows = statement()
    rows[5][2] = "23.00"
    prev = preview(client, me, rows)
    assert not prev["statement_check"]["ok"]
    records = prev["records"]
    uber = next(r for r in records if "UBER" in r["description"])
    uber["amount"] = "230.00"
    res = client.post(f"{API}/check", json={"records": records, "statement": prev["statement"]}, headers=headers(me)).json()
    assert res["statement_check"]["ok"], res["statement_check"]
