"""Payments that look like buying an investment wait on the Investments page until added to a holding or skipped."""

from datetime import timedelta
from decimal import Decimal

from app.services.investments import looks_like_investment
from app.timeutil import today_local

from .conftest import headers

TODAY = today_local()
D = lambda n: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731


def _expense(client, h, name, category="General", amount="5000", days_ago=1, **extra):
    r = client.post("/api/expenses", json={"expense_date": D(days_ago), "item_name": name, "category": category,
                                           "amount": amount, **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _pending(client, h, **params):
    r = client.get("/api/investments/pending", params=params, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _settle(client, h, source, sid, **body):
    return client.post(f"/api/investments/pending/{source}/{sid}", json=body, headers=h)


def test_what_counts_as_buying_an_investment():
    for yes in ("SIP HSBC Small Cap", "Mutual funds", "Investment", "Money transfer to groww account", "ACH D- BSESTARMF",
                "NSE Clearing Ltd", "Zerodha", "CoinDCX", "Digital gold", "Stocks", "IPO application"):
        assert looks_like_investment(yes), yes
    for no in ("Dinner share", "Groceries", "Nuvama charges", "Annual Maintenance fees", "Zerodha brokerage", "Gold bangle"):
        assert not looks_like_investment(no), no


def test_only_investment_payments_after_the_latest_valuation_are_waiting(client, make_user):
    h = headers(make_user("asha", expense=True))
    sip = _expense(client, h, "SIP HSBC Small Cap", "Mutual funds", days_ago=2)
    _expense(client, h, "Groceries", "Groceries")
    _expense(client, h, "Nuvama charges", "Annual Maintenance fees")
    old = _expense(client, h, "Groww", "Investment", days_ago=20)
    assert [p["id"] for p in _pending(client, h)["items"]] == [sip, old]

    # A holding valued 10 days ago already includes what was bought before then
    client.post("/api/investments", json={"kind": "MUTUAL_FUND", "name": "HSBC", "valued_on": D(10)}, headers=h)
    page = _pending(client, h)
    assert page["since"] == D(9) and [p["id"] for p in page["items"]] == [sip]
    assert page["items"][0]["label"] == "SIP HSBC Small Cap (Mutual funds)" and Decimal(page["items"][0]["amount"]) == 5000

    # Just-saved check: only the ids asked about
    assert _pending(client, h, expense_ids=str(sip))["items"][0]["id"] == sip
    assert _pending(client, h, expense_ids="")["items"] == []


def test_add_to_an_existing_holding_and_its_parent(client, make_user):
    h = headers(make_user("asha", expense=True))
    groww = client.post("/api/investments", json={"kind": "STOCK", "name": "Groww", "invested": "1000", "current_value": "1500",
                                                  "valued_on": D(30)}, headers=h).json()
    bse = client.post("/api/investments", json={"kind": "STOCK", "name": "BSE", "units": "2", "invested": "600",
                                                "parent_id": groww["id"]}, headers=h).json()
    eid = _expense(client, h, "Groww", "Investment", amount="900")

    r = _settle(client, h, "expense", eid, action="ADD", holding_id=bse["id"], units="3")
    assert r.status_code == 200, r.text
    assert Decimal(r.json()["holding"]["units"]) == 5 and Decimal(r.json()["holding"]["invested"]) == 1500
    page = client.get("/api/investments", headers=h).json()
    parent = next(x for x in page["holdings"] if x["id"] == groww["id"])
    # The broker's total includes the new purchase; the value goes up by what was paid until it is updated
    assert Decimal(parent["invested"]) == 1900 and Decimal(parent["current_value"]) == 2400
    assert Decimal(page["invested"]) == 1900

    # Asked once: no longer waiting, and can't be added again
    assert _pending(client, h)["items"] == []
    assert _settle(client, h, "expense", eid, action="SKIP").status_code == 409


def test_new_holding_skip_and_transfers_to_an_investment_account(client, make_user):
    h = headers(make_user("asha", expense=True))
    bank = client.post("/api/accounts", json={"name": "Axis", "kind": "SAVINGS", "opening_balance": "50000"}, headers=h).json()["id"]
    broker = client.post("/api/accounts", json={"name": "Investments", "kind": "INVESTMENT"}, headers=h).json()["id"]
    t = client.post("/api/accounts/transfers", json={"from_account_id": bank, "to_account_id": broker, "amount": "2500",
                                                     "transfer_date": D(1), "note": "Sent to Zerodha"}, headers=h).json()["id"]
    back = client.post("/api/accounts/transfers", json={"from_account_id": broker, "to_account_id": bank, "amount": "100",
                                                        "transfer_date": D(1)}, headers=h)
    assert back.status_code == 201
    eid = _expense(client, h, "SIP", "Mutual funds", amount="1000")
    items = _pending(client, h)["items"]
    assert {(p["source"], p["id"]) for p in items} == {("TRANSFER", t), ("EXPENSE", eid)}
    assert next(p for p in items if p["source"] == "TRANSFER")["label"] == "Sent to Zerodha"
    assert _pending(client, h, transfer_ids=str(t))["items"][0]["account"] == "Axis"

    r = _settle(client, h, "transfer", t, action="NEW", holding={"kind": "STOCK", "name": "Tata Motors", "platform": "Zerodha",
                                                                "units": "10"})
    assert r.status_code == 200, r.text
    new = r.json()["holding"]
    assert Decimal(new["invested"]) == 2500 and Decimal(new["current_value"]) == 2500 and new["valued_on"] == D(1)
    assert Decimal(new["units"]) == 10 and Decimal(new["avg_cost"]) == 250

    assert _settle(client, h, "expense", eid, action="SKIP").json()["holding"] is None
    assert _pending(client, h)["items"] == []
    assert Decimal(client.get("/api/investments", headers=h).json()["invested"]) == 2500


def test_bad_requests_and_other_peoples_payments(client, make_user):
    h = headers(make_user("asha", expense=True))
    eid = _expense(client, h, "SIP", "Mutual funds")
    assert _settle(client, h, "expense", eid, action="ADD").status_code == 400
    assert _settle(client, h, "expense", eid, action="NEW").status_code == 400
    assert _settle(client, h, "nothing", eid, action="SKIP").status_code == 404
    assert _settle(client, h, "expense", 99999, action="SKIP").status_code == 404
    assert client.get("/api/investments/pending", params={"expense_ids": "x"}, headers=h).status_code == 400

    other = headers(make_user("ravi", expense=True))
    assert _pending(client, other)["items"] == []
    assert _settle(client, other, "expense", eid, action="SKIP").status_code in (403, 404)
    mine = client.post("/api/investments", json={"kind": "STOCK", "name": "Mine"}, headers=other).json()
    assert _settle(client, h, "expense", eid, action="ADD", holding_id=mine["id"]).status_code == 400
    assert len(_pending(client, h)["items"]) == 1
