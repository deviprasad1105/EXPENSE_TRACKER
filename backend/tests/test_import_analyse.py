"""Auto-analyse: rows that need action are worked out from the rest of the file."""

from datetime import timedelta

from app.importer.parse import guess_mapping
from app.timeutil import today_local

from .conftest import headers

API = "/api/imports"
T = today_local()
D = [(T - timedelta(days=30 - i)) for i in range(6)]
dmy = lambda d: d.strftime("%d/%m/%Y")  # noqa: E731


def preview(client, user, rows):
    res = client.post(f"{API}/preview", json={"rows": rows, "mapping": guess_mapping(rows).model_dump()}, headers=headers(user))
    assert res.status_code == 200, res.text
    return res.json()


def analyse(client, user, records, row_nos):
    res = client.post(f"{API}/analyse", json={"records": records, "row_nos": row_nos}, headers=headers(user))
    assert res.status_code == 200, res.text
    return {x["row_no"]: x for x in res.json()}


def test_amount_and_type_from_the_running_balance(client, make_user):
    me = make_user("asha")
    rows = [["Date", "Narration", "Withdrawal", "Deposit", "Balance"],
            [dmy(D[0]), "SWIGGY", "450.00", "", "9,550.00"],
            [dmy(D[1]), "MYSTERY", "100.00", "50.00", "10,050.00"],  # both columns filled: unreadable
            [dmy(D[2]), "UBER", "230.00", "", "9,820.00"]]
    prev = preview(client, me, rows)
    bad = [r for r in prev["records"] if not r["can_import"]]
    assert [r["row_no"] for r in bad] == [3]
    fix = analyse(client, me, prev["records"], [3])[3]
    assert fix["solved"] and fix["patch"]["kind"] == "INCOME" and fix["patch"]["amount"] == "500.00"
    assert "balance went from ₹9,550.00" in fix["reasons"][0]
    # Applied, the row is ready
    records = [{**r, **fix["patch"]} if r["row_no"] == 3 else r for r in prev["records"]]
    res = client.post(f"{API}/check", json={"records": records}, headers=headers(me)).json()
    assert next(r for r in res["records"] if r["row_no"] == 3)["status"] == "READY"


def test_dates_worked_out(client, make_user):
    me = make_user("asha")
    rows = [["Date", "Item", "Amount"],
            [dmy(D[0]), "Tea", "20"],
            ["", "Coffee", "40"],                      # missing: from the rows around it
            [dmy(D[0]), "Bus", "15"]]
    prev = preview(client, me, rows)
    fix = analyse(client, me, prev["records"], [3])[3]
    assert fix["solved"] and fix["patch"]["date"] == D[0].isoformat() and "both dated" in fix["reasons"][0]


def test_type_from_the_description_and_negative_amounts(client, make_user):
    me = make_user("asha")
    records = [
        {"row_no": 1, "kind": None, "date": D[0].isoformat(), "description": "NEFT CR ACME SALARY", "amount": "5000"},
        {"row_no": 2, "kind": "EXPENSE", "date": D[0].isoformat(), "description": "Amazon", "amount": "-300"},
        {"row_no": 3, "kind": None, "date": D[0].isoformat(), "description": "Something", "amount": None},
    ]
    out = analyse(client, me, records, [1, 2, 3])
    assert out[1]["patch"]["kind"] == "INCOME" and out[1]["solved"]
    assert (out[2]["patch"]["kind"], out[2]["patch"]["amount"]) == ("INCOME", "300")
    assert not out[3]["solved"] and out[3]["message"]


def test_coverage_says_how_far_records_go(client, make_user):
    me = make_user("asha")
    assert client.get(f"{API}/coverage", headers=headers(me)).json()["last_record_date"] is None
    content = f"Date,Item,Category,Amount\n{dmy(D[0])},Tea,Food,20\n".encode()
    d = client.post(f"{API}/extract", files={"file": ("a.csv", content)}, headers=headers(me)).json()
    t = d["tables"][0]
    prev = client.post(f"{API}/preview", json={"rows": t["rows"], "mapping": d["guess"]}, headers=headers(me)).json()
    b = client.post(API, json={"filename": "a.csv", "file_type": "csv", "file_hash": d["file_hash"], "records": prev["records"]},
                    headers=headers(me)).json()
    client.post(f"{API}/{b['id']}/rows", json={"records": prev["records"]}, headers=headers(me))
    client.post(f"{API}/{b['id']}/finish", headers=headers(me))
    c = client.get(f"{API}/coverage", headers=headers(me)).json()
    assert c["last_record_date"] == D[0].isoformat() and c["last_imported_date"] == D[0].isoformat()
    assert c["days_behind"] == (T - D[0]).days
