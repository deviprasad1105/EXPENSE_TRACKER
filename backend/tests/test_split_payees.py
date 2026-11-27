"""A UPI payee shared by a split group: paying it asks "Split this?" with the group's members."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.models import BankMessage, Expense, SplitBill
from app.routers import autotrack as autotrack_router
from app.timeutil import today_local

from .conftest import headers

DMY = f"{today_local():%d-%m-%y}"


def dmart(amount="900.00", ref="412345678901"):
    return f"Rs.{amount} debited from a/c **1234 on {DMY} to VPA dmart.blr@icici(UPI Ref No {ref})."


@pytest.fixture(autouse=True)
def _reset_limiter():
    autotrack_router.device_limiter.reset()


@pytest.fixture
def flat(client, make_user):
    """Asha (auto-tracking on) in the group 'Flat 4B' with Ravi (app user) and Kiran (guest)."""
    asha = make_user("asha", expense=True, upi=True, split=True)
    ravi = make_user("ravi")
    r = client.post("/api/splits/groups", json={"name": "Flat 4B", "members": ["@ravi", "Kiran"]}, headers=headers(asha))
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    members = {m["member"]["full_name"]: m["member"]["id"] for m in client.get(f"/api/splits/groups/{gid}", headers=headers(asha)).json()["members"]}
    body = {"enabled": True, "mode": "REVIEW", "since": (today_local() - timedelta(days=30)).isoformat()}
    assert client.put("/api/autotrack/settings", json=body, headers=headers(asha)).status_code == 200
    return asha, ravi, gid, members


def _send(client, user, body):
    r = client.post("/api/autotrack/messages", json={"messages": [{"sender": "AX-ICICIB", "body": body, "source": "SMS"}]}, headers=headers(user))
    assert r.status_code == 201, r.text
    return r.json()["results"]


def _payee(client, user, gid, **over):
    body = {"label": "DMart groceries", "vpa": "DMart.Blr@icici", **over}
    r = client.post(f"/api/splits/groups/{gid}/payees", json=body, headers=headers(user))
    assert r.status_code == 201, r.text
    return r.json()


def test_payment_to_group_payee_asks_and_splits(client, db, flat):
    asha, ravi, gid, members = flat
    _payee(client, asha, gid)
    res = _send(client, asha, dmart())[0]
    assert res["split_status"] == "ASK" and res["split_label"] == "DMart groceries · Flat 4B"

    prompts = client.get("/api/splits/prompts", headers=headers(asha)).json()
    assert len(prompts) == 1 and prompts[0]["label"] == "DMart groceries"
    assert set(prompts[0]["default_member_ids"]) == set(members.values())
    # Ravi doesn't see Asha's payment
    assert client.get("/api/splits/prompts", headers=headers(ravi)).json() == []

    # Custom amounts: Asha 500, Ravi 400, Kiran left out
    shares = [{"member_id": members["Asha"], "amount": "500"}, {"member_id": members["Ravi"], "amount": "400"}]
    r = client.post(f"/api/splits/prompts/{res['id']}", json={"action": "SPLIT", "shares": shares}, headers=headers(asha))
    assert r.status_code == 200, r.text
    bill = db.get(SplitBill, r.json()["bill_id"])
    assert bill.total_amount == Decimal("900.00") and {s.member_id: s.amount for s in bill.shares} == {
        members["Asha"]: Decimal("500.00"), members["Ravi"]: Decimal("400.00")}
    assert client.get("/api/splits/prompts", headers=headers(asha)).json() == []
    # Answered once only
    assert client.post(f"/api/splits/prompts/{res['id']}", json={"action": "SKIP"}, headers=headers(asha)).status_code == 404


def test_amounts_must_add_up(client, flat):
    asha, _, gid, members = flat
    _payee(client, asha, gid)
    res = _send(client, asha, dmart())[0]
    shares = [{"member_id": members["Asha"], "amount": "100"}, {"member_id": members["Ravi"], "amount": "100"}]
    r = client.post(f"/api/splits/prompts/{res['id']}", json={"action": "SPLIT", "shares": shares}, headers=headers(asha))
    assert r.status_code == 400 and "add up" in r.json()["detail"]


def test_only_me_and_cancel(client, db, flat):
    asha, _, gid, members = flat
    _payee(client, asha, gid)
    first = _send(client, asha, dmart("300.00", "412345678902"))[0]
    second = _send(client, asha, dmart("120.00", "412345678903"))[0]
    r = client.post(f"/api/splits/prompts/{first['id']}", json={"action": "SELF"}, headers=headers(asha))
    bill = db.get(SplitBill, r.json()["bill_id"])
    assert [s.member_id for s in bill.shares] == [members["Asha"]]
    assert client.post(f"/api/splits/prompts/{second['id']}", json={"action": "SKIP"}, headers=headers(asha)).json() == {"status": "SKIPPED"}
    assert db.query(SplitBill).count() == 1


def test_other_payments_are_not_asked_about(client, flat):
    asha, _, gid, _ = flat
    _payee(client, asha, gid)
    other = "Rs.80.00 debited from a/c **1234 on {} to VPA chai.stall@ybl(UPI Ref No 412345678909).".format(DMY)
    assert _send(client, asha, other)[0]["split_status"] is None
    # Money coming in from the same UPI ID (a refund) isn't asked about either
    refund = f"Rs.900.00 credited to a/c **1234 on {DMY} from VPA dmart.blr@icici (UPI Ref No 412345678910)."
    assert _send(client, asha, refund)[0]["split_status"] is None


def test_name_word_matches_when_no_upi_id(client, flat):
    asha, _, gid, _ = flat
    _payee(client, asha, gid, vpa=None, name_match="DMART")
    sms = f"INR 450.00 debited from A/c no. XX1234 on {DMY} UPI/P2M/631234567777/AVENUE SUPERMARTS DMART. - ICICI"
    assert _send(client, asha, sms)[0]["split_status"] == "ASK"


def test_one_upi_id_per_group_and_leaving_stops_it(client, flat, make_user):
    asha, _, gid, _ = flat
    p = _payee(client, asha, gid)
    r = client.post("/api/splits/groups", json={"name": "Trip", "members": []}, headers=headers(asha))
    dup = client.post(f"/api/splits/groups/{r.json()['id']}/payees", json={"label": "x", "vpa": "dmart.blr@icici"}, headers=headers(asha))
    assert dup.status_code == 409
    # Deleting it: nothing is asked any more
    assert client.delete(f"/api/splits/payees/{p['id']}", headers=headers(asha)).status_code == 204
    assert _send(client, asha, dmart())[0]["split_status"] is None


def test_the_expense_itself_is_unchanged(client, db, flat):
    asha, _, gid, members = flat
    _payee(client, asha, gid)
    res = _send(client, asha, dmart())[0]
    client.post(f"/api/splits/prompts/{res['id']}", json={"action": "SPLIT", "shares": [{"member_id": m} for m in members.values()]},
                headers=headers(asha))
    msg = db.get(BankMessage, res["id"])
    db.refresh(msg)
    assert msg.status == "PENDING" and msg.split_status == "SPLIT"
    assert db.query(Expense).count() == 0
