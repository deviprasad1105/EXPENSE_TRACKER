"""Investments page: holdings with whatever details are known, totals over known figures only."""

from decimal import Decimal

from sqlalchemy import select

from app.models import Holding
from app.services.backup import snapshot_user_data

from .conftest import headers


def _add(client, h, **body):
    r = client.post("/api/investments", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _page(client, h):
    r = client.get("/api/investments", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_unknown_details_stay_empty_and_known_ones_are_worked_out(client, make_user):
    h = headers(make_user("asha", expense=True))
    full = _add(client, h, kind="CRYPTO", name="Ethereum", platform="CoinDCX", units="0.02861", invested="5902", current_value="6751")
    assert Decimal(full["avg_cost"]) == Decimal("206291.51") and Decimal(full["price"]) == Decimal("235966.45")
    assert Decimal(full["gain"]) == 849 and full["gain_percent"] == 14.38

    bare = _add(client, h, kind="STOCK", name="Groww shares", current_value="1000")
    assert bare["units"] is None and bare["invested"] is None
    assert bare["avg_cost"] is None and bare["price"] is None and bare["gain"] is None and bare["gain_percent"] is None

    no_value = _add(client, h, kind="MUTUAL_FUND", name="HSBC Small Cap", invested="500")
    assert no_value["gain"] is None

    page = _page(client, h)
    # Every known figure is added up; the gain only over holdings where both are known
    assert Decimal(page["invested"]) == Decimal("6402") and Decimal(page["current_value"]) == Decimal("7751")
    assert Decimal(page["gain"]) == 849 and page["gain_percent"] == 14.38
    assert page["unvalued"] == 1
    groups = {g["kind"]: g for g in page["groups"]}
    assert groups["STOCK"]["gain"] is None and Decimal(groups["CRYPTO"]["current_value"]) == 6751
    # No Investment-type account: nothing to compare with
    assert page["accounts_balance"] is None and page["accounts"] == []


def test_sold_holdings_are_listed_but_not_counted_and_edits_and_deletes_work(client, make_user):
    h = headers(make_user("asha", expense=True))
    a = _add(client, h, kind="MUTUAL_FUND", name="Tata Small Cap", invested="100", current_value="120")
    b = _add(client, h, kind="GOLD", name="Digital gold", units="0.7494", invested="11070", current_value="11719.75")

    body = {**{k: a[k] for k in ("kind", "name", "platform", "symbol", "units", "invested", "current_value", "valued_on", "note")},
            "is_active": False}
    r = client.put(f"/api/investments/{a['id']}", json=body, headers=h)
    assert r.status_code == 200 and r.json()["is_active"] is False
    page = _page(client, h)
    assert len(page["holdings"]) == 2 and page["holdings"][-1]["name"] == "Tata Small Cap"
    assert Decimal(page["current_value"]) == Decimal("11719.75") and [g["kind"] for g in page["groups"]] == ["GOLD"]

    assert client.delete(f"/api/investments/{b['id']}", headers=h).status_code == 204
    assert [x["name"] for x in _page(client, h)["holdings"]] == ["Tata Small Cap"]


def test_compares_with_investment_accounts(client, make_user):
    h = headers(make_user("asha", expense=True))
    r = client.post("/api/accounts", json={"name": "Investments", "kind": "INVESTMENT", "opening_balance": "1000"}, headers=h)
    assert r.status_code == 201, r.text
    _add(client, h, kind="STOCK", name="Shares", current_value="1500")
    page = _page(client, h)
    assert Decimal(page["accounts_balance"]) == 1000 and page["accounts"] == ["Investments"]


def test_bad_input_and_other_peoples_holdings(client, make_user):
    h = headers(make_user("asha", expense=True))
    assert client.post("/api/investments", json={"kind": "HOUSE", "name": "x"}, headers=h).status_code == 422
    assert client.post("/api/investments", json={"kind": "STOCK", "name": "x", "units": "0"}, headers=h).status_code == 422
    assert client.post("/api/investments", json={"kind": "STOCK", "name": ""}, headers=h).status_code == 422
    mine = _add(client, h, kind="STOCK", name="Mine", current_value="10")

    other = headers(make_user("ravi", expense=True))
    assert _page(client, other)["holdings"] == []
    assert client.delete(f"/api/investments/{mine['id']}", headers=other).status_code in (403, 404)
    assert client.put(f"/api/investments/{mine['id']}", json={"kind": "STOCK", "name": "Taken"}, headers=other).status_code in (403, 404)
    assert _page(client, h)["holdings"][0]["name"] == "Mine"


def test_feature_off_and_backup(client, make_user, db):
    off = make_user("nofeature", expense=False)
    assert client.get("/api/investments", headers=headers(off)).status_code == 403

    user = make_user("asha", expense=True)
    _add(client, headers(user), kind="ETF", name="LIC Gold ETF FoF", invested="12999")
    assert [r["name"] for r in snapshot_user_data(db, user.id)["holdings"]] == ["LIC Gold ETF FoF"]
    assert db.scalar(select(Holding.user_id)) == user.id
