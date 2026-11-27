"""An entry can be edited or deleted at any time, and what keeps a copy of its amount (an investment it was added
to, a bill it paid) follows the change."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import BillPayment, Expense, InvestmentLink
from app.timeutil import now_local, today_local

from .conftest import headers

TODAY = today_local()


def _expense(client, h, name="Lunch", category="Food", amount="500", day=None):
    r = client.post("/api/expenses", json={"expense_date": str(day or TODAY), "item_name": name, "category": category,
                                           "amount": amount}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _age(db, eid, days=30):
    row = db.get(Expense, eid)
    row.created_at = now_local() - timedelta(days=days)
    db.commit()


def test_an_old_expense_can_be_edited_and_deleted_by_its_owner(client, db, make_user):
    u = make_user("asha", expense=True)
    h = headers(u)
    bank = client.post("/api/accounts", json={"name": "Axis", "kind": "SAVINGS", "opening_balance": "10000"}, headers=h).json()["id"]
    r = client.post("/api/expenses", json={"expense_date": str(TODAY - timedelta(days=40)), "item_name": "Titan",
                                           "category": "Shopping", "amount": "1500", "account_id": bank}, headers=h)
    eid = r.json()["id"]
    _age(db, eid, 40)
    assert client.get("/api/expenses", headers=h).json()[0]["editable"] is True

    r = client.put(f"/api/expenses/{eid}", json={"category": "Investment", "amount": "1600"}, headers=h)
    assert r.status_code == 200, r.text
    assert (r.json()["category"], Decimal(r.json()["amount"])) == ("Investment", 1600)
    # The account's balance follows the new amount
    acc = next(a for a in client.get("/api/accounts", headers=h).json() if a["id"] == bank)
    assert Decimal(acc["balance"]) == Decimal("8400")

    # Someone else still can't
    other = headers(make_user("ravi", expense=True))
    assert client.put(f"/api/expenses/{eid}", json={"amount": "1"}, headers=other).status_code in (403, 404)

    assert client.delete(f"/api/expenses/{eid}", headers=h).status_code == 204
    acc = next(a for a in client.get("/api/accounts", headers=h).json() if a["id"] == bank)
    assert Decimal(acc["balance"]) == Decimal("10000")


def test_an_investment_follows_the_payment_added_to_it(client, db, make_user):
    h = headers(make_user("asha", expense=True))
    gold = client.post("/api/investments", json={"kind": "GOLD", "name": "Gold", "invested": "11070", "current_value": "11719.75",
                                                 "units": "0.7494", "valued_on": str(TODAY - timedelta(days=30))}, headers=h).json()
    eid = _expense(client, h, "Digital gold", "Investment", amount="1500", day=TODAY - timedelta(days=5))
    r = client.post(f"/api/investments/pending/expense/{eid}", json={"action": "ADD", "holding_id": gold["id"], "units": "0.1002"},
                    headers=h)
    assert r.status_code == 200, r.text
    _age(db, eid)

    def holding():
        return next(x for x in client.get("/api/investments", headers=h).json()["holdings"] if x["id"] == gold["id"])

    assert (Decimal(holding()["invested"]), Decimal(holding()["current_value"])) == (Decimal("12570"), Decimal("13219.75"))

    # Paid 1,450, not 1,500: the holding goes down by 50
    assert client.put(f"/api/expenses/{eid}", json={"amount": "1450"}, headers=h).status_code == 200
    assert (Decimal(holding()["invested"]), Decimal(holding()["current_value"])) == (Decimal("12520"), Decimal("13169.75"))
    # Changing something else leaves it alone
    assert client.put(f"/api/expenses/{eid}", json={"note": "Titan"}, headers=h).status_code == 200
    assert Decimal(holding()["invested"]) == Decimal("12520")

    # Deleted: what it added comes out again (the grams are left for you to correct)
    assert client.delete(f"/api/expenses/{eid}", headers=h).status_code == 204
    assert (Decimal(holding()["invested"]), Decimal(holding()["current_value"])) == (Decimal("11070"), Decimal("11719.75"))
    db.expire_all()
    assert db.scalar(select(InvestmentLink)) is None


def test_deleting_a_transfer_into_an_investment_account_takes_it_out(client, db, make_user):
    h = headers(make_user("asha", expense=True))
    bank = client.post("/api/accounts", json={"name": "Axis", "kind": "SAVINGS", "opening_balance": "50000"}, headers=h).json()["id"]
    broker = client.post("/api/accounts", json={"name": "Investments", "kind": "INVESTMENT"}, headers=h).json()["id"]
    t = client.post("/api/accounts/transfers", json={"from_account_id": bank, "to_account_id": broker, "amount": "4000",
                                                     "transfer_date": str(TODAY - timedelta(days=1)), "note": "SIP"},
                    headers=h).json()["id"]
    r = client.post(f"/api/investments/pending/transfer/{t}",
                    json={"action": "NEW", "holding": {"kind": "MUTUAL_FUND", "name": "HSBC Small Cap"}}, headers=h)
    hid = r.json()["holding"]["id"]
    assert Decimal(next(x for x in client.get("/api/investments", headers=h).json()["holdings"] if x["id"] == hid)["invested"]) == 4000
    # Deleted: the holding no longer counts it
    assert client.delete(f"/api/accounts/transfers/{t}", headers=h).status_code == 204
    hold = next(x for x in client.get("/api/investments", headers=h).json()["holdings"] if x["id"] == hid)
    assert Decimal(hold["invested"]) == 0


def test_a_bill_payment_follows_its_expense(client, db, make_user):
    u = make_user("asha", subscription=True, expense=True)
    h = headers(u)
    b = client.post("/api/bills", json={"name": "Electricity", "biller": "MSEDCL", "kind": "ELECTRICITY", "amount": "599",
                                        "due_date": "2026-01-31"}, headers=h).json()
    assert client.post(f"/api/bills/{b['id']}/pay", json={"amount": "612.50", "paid_on": "2026-01-29"}, headers=h).status_code == 200
    e = db.scalar(select(Expense).where(Expense.user_id == u.id))
    _age(db, e.id)
    assert client.put(f"/api/expenses/{e.id}", json={"amount": "620"}, headers=h).status_code == 200
    db.expire_all()
    assert db.scalar(select(BillPayment.amount)) == Decimal("620")
    assert client.get("/api/bills", headers=h).json()[0]["last_amount"] == "620.00"


def test_money_received_long_ago_can_be_deleted(client, db, make_user):
    h = headers(make_user("asha", expense=True))
    f = client.post("/api/funds", json={"fund_date": str(date(2025, 1, 5)), "amount": "100", "source": "Mamta"}, headers=h).json()
    assert f["editable"]
    assert client.delete(f"/api/funds/{f['id']}", headers=h).status_code == 204
