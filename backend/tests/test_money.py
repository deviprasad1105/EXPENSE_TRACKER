"""Bank accounts and transfers, budget alerts, and the month-by-month insights."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import Expense, Notification
from app.services.insights import build
from app.timeutil import today_local

from .conftest import headers


def _expense(client, user, amount, category="Food", item="Lunch", **extra):
    body = {"expense_date": today_local().isoformat(), "item_name": item, "category": category, "amount": amount, **extra}
    return client.post("/api/expenses", json=body, headers=headers(user))


# ---------- accounts ----------
def test_first_account_is_primary_and_used_by_default(client, db, make_user):
    me = make_user("asha")
    h = headers(me)
    salary = client.post("/api/accounts", json={"name": "Salary", "kind": "SALARY", "opening_balance": 1000}, headers=h).json()
    spend = client.post("/api/accounts", json={"name": "Spending", "last4": "1234"}, headers=h).json()
    assert salary["is_primary"] and not spend["is_primary"]

    _expense(client, me, 100)
    assert db.scalar(select(Expense.account_id)) == salary["id"]

    client.post(f"/api/accounts/{spend['id']}/primary", headers=h)
    _expense(client, me, 50)
    accounts = {a["name"]: a for a in client.get("/api/accounts", headers=h).json()}
    assert accounts["Spending"]["is_primary"] and not accounts["Salary"]["is_primary"]
    assert Decimal(accounts["Spending"]["balance"]) == Decimal(-50)
    assert Decimal(accounts["Salary"]["balance"]) == Decimal(900)


def test_transfer_moves_money_between_own_accounts(client, make_user):
    me, other = make_user("asha"), make_user("ravi")
    h = headers(me)
    a = client.post("/api/accounts", json={"name": "Salary", "opening_balance": 5000}, headers=h).json()
    b = client.post("/api/accounts", json={"name": "Spending"}, headers=h).json()
    theirs = client.post("/api/accounts", json={"name": "Theirs"}, headers=headers(other)).json()

    t = {"from_account_id": a["id"], "to_account_id": b["id"], "amount": 2000, "transfer_date": today_local().isoformat()}
    assert client.post("/api/accounts/transfers", json=t, headers=h).status_code == 201
    _expense(client, me, 300, account_id=b["id"])
    bal = {x["name"]: Decimal(x["balance"]) for x in client.get("/api/accounts", headers=h).json()}
    assert bal == {"Salary": Decimal(3000), "Spending": Decimal(1700)}

    # Someone else's account can't be used
    assert client.post("/api/accounts/transfers", json={**t, "to_account_id": theirs["id"]}, headers=h).status_code == 400
    assert _expense(client, me, 10, account_id=theirs["id"]).status_code == 400


def test_deleting_account_keeps_expenses(client, db, make_user):
    me = make_user("asha")
    h = headers(me)
    a = client.post("/api/accounts", json={"name": "Old card"}, headers=h).json()
    _expense(client, me, 99)
    assert client.delete(f"/api/accounts/{a['id']}", headers=h).status_code == 204
    db.expire_all()
    e = db.scalar(select(Expense))
    assert e is not None and e.account_id is None


# ---------- budgets ----------
def test_budget_alert_at_90_percent_then_over(client, db, make_user):
    me = make_user("asha")
    h = headers(me)
    b = client.post("/api/budgets", json={"scope": "CATEGORY", "target": "food", "monthly_limit": 1000}, headers=h).json()
    assert b["level"] == "OK" and b["alert_percent"] == 90

    _expense(client, me, 500)
    assert not db.scalars(select(Notification).where(Notification.kind == "BUDGET_ALERT")).all()
    _expense(client, me, 420)  # 92%
    _expense(client, me, 10)  # still WARN: no second alert
    alerts = db.scalars(select(Notification).where(Notification.kind == "BUDGET_ALERT")).all()
    assert len(alerts) == 1 and "92%" in alerts[0].message

    _expense(client, me, 100)  # 103%
    alerts = db.scalars(select(Notification).where(Notification.kind == "BUDGET_ALERT")).all()
    assert len(alerts) == 2 and "exceeded" in alerts[1].message
    status = client.get("/api/budgets", headers=h).json()[0]
    assert status["level"] == "OVER" and status["percent"] == 103.0

    # Other categories don't count
    _expense(client, me, 5000, category="Travel")
    assert Decimal(client.get("/api/budgets", headers=h).json()[0]["spent"]) == Decimal(1030)


def test_overall_budget_and_duplicates(client, make_user):
    me = make_user("asha")
    h = headers(me)
    assert client.post("/api/budgets", json={"scope": "OVERALL", "monthly_limit": 100}, headers=h).status_code == 201
    assert client.post("/api/budgets", json={"scope": "OVERALL", "monthly_limit": 200}, headers=h).status_code == 400
    assert client.post("/api/budgets", json={"scope": "ITEM", "monthly_limit": 200}, headers=h).status_code == 400
    _expense(client, me, 95, category="Travel")
    assert client.get("/api/budgets", headers=h).json()[0]["level"] == "WARN"


def test_viewer_cannot_change_budgets(client, make_user):
    boss = make_user("boss")
    me = make_user("asha", manager=boss)
    b = client.post("/api/budgets", json={"monthly_limit": 100}, headers=headers(me)).json()
    assert client.get("/api/budgets", params={"user_id": me.id}, headers=headers(boss)).status_code == 200
    assert client.delete(f"/api/budgets/{b['id']}", headers=headers(boss)).status_code == 403


# ---------- insights ----------
def test_insights_compare_months(db, make_user):
    me = make_user("asha")
    today = date(2026, 10, 15)
    rows = [
        (date(2026, 10, 3), "Food", 600), (date(2026, 10, 10), "Travel", 100),
        (date(2026, 9, 5), "Food", 300), (date(2026, 9, 25), "Food", 900), (date(2026, 9, 6), "Travel", 400),
        (date(2026, 5, 1), "Food", 1200),  # within the six months before October
        (date(2026, 3, 1), "Food", 9999),  # too old
    ]
    for d, cat, amt in rows:
        db.add(Expense(user_id=me.id, expense_date=d, item_name=cat, category=cat, amount=Decimal(amt)))
    db.commit()

    r = build(db, me.id, today)
    assert [m["label"] for m in r["months"]] == ["Apr 26", "May 26", "Jun 26", "Jul 26", "Aug 26", "Sep 26", "Oct 26"]
    t = r["sections"]["total"]
    assert t["this_month"] == 700 and t["last_month"] == 1600
    assert t["last_month_to_date"] == 700  # 5 and 6 Sep, not the 25th
    assert t["vs_last_month"] == 0.0
    assert t["avg_6m"] == round((1600 + 1200) / 6, 2)
    food = next(c for c in r["categories"] if c["category"] == "Food")
    assert food["this_month"] == 600 and food["last_month"] == 1200 and food["change_pct"] == -50.0
    assert any("Food is down 50%" in i["text"] for i in r["insights"])
