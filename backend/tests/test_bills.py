"""Bills you pay yourself: due dates, marking paid (new expense or one already recorded), skip / undo, reminders."""

from datetime import date, timedelta

from sqlalchemy import select

from app.models import Bill, BillPayment, Expense, Notification
from app.services.bills import send_reminders
from app.timeutil import today_local

from .conftest import headers


def _user(make_user, name="asha"):
    return make_user(name, subscription=True, expense=True)


def _add(client, user, **kw):
    body = {"name": "Electricity", "biller": "MSEDCL", "kind": "ELECTRICITY", "due_date": str(today_local() + timedelta(days=10))}
    body.update(kw)
    r = client.post("/api/bills", json=body, headers=headers(user))
    assert r.status_code == 201, r.text
    return r.json()


def test_needs_the_subscriptions_feature(client, make_user):
    u = make_user("nofeature", expense=True)
    assert client.get("/api/bills", headers=headers(u)).status_code == 403


def test_add_and_status(client, make_user):
    u = _user(make_user)
    today = today_local()
    _add(client, u, name="Rent", biller="Landlord", kind="RENT", amount="15000", due_date=str(today - timedelta(days=2)))
    _add(client, u, name="Airtel postpaid", kind="MOBILE", amount="599", due_date=str(today + timedelta(days=2)))
    _add(client, u)
    bills = client.get("/api/bills", headers=headers(u)).json()
    # Overdue first, then due soon, then later ones
    assert [(b["name"], b["status"]) for b in bills] == [("Rent", "OVERDUE"), ("Airtel postpaid", "DUE_SOON"), ("Electricity", "UPCOMING")]
    assert bills[0]["days_until"] == -2


def test_pay_logs_an_expense_and_moves_to_next_month(client, db, make_user):
    u = _user(make_user)
    due = date(2026, 1, 31)
    b = _add(client, u, amount="599", due_date=str(due))
    r = client.post(f"/api/bills/{b['id']}/pay", json={"amount": "612.50", "paid_on": "2026-01-29"}, headers=headers(u))
    assert r.status_code == 200, r.text
    assert r.json()["next_due_date"] == "2026-02-28"  # day of the month kept, short month
    assert r.json()["last_amount"] == "612.50"
    e = db.scalar(select(Expense).where(Expense.user_id == u.id))
    assert e.item_name == "Electricity" and str(e.amount) == "612.50" and e.category == "Bills"

    # Undo: the bill is due again and the expense it made is gone
    pay = client.get(f"/api/bills/{b['id']}/payments", headers=headers(u)).json()[0]
    r = client.delete(f"/api/bills/{b['id']}/payments/{pay['id']}", headers=headers(u))
    assert r.status_code == 200 and r.json()["next_due_date"] == "2026-01-31"
    db.expire_all()
    assert db.scalar(select(Expense).where(Expense.user_id == u.id)) is None


def test_payment_already_recorded_is_spotted_and_linked(client, db, make_user):
    u = _user(make_user)
    today = today_local()
    b = _add(client, u, amount="1800", due_date=str(today - timedelta(days=1)))
    # Auto-tracking already recorded the payment
    r = client.post("/api/expenses", json={"expense_date": str(today - timedelta(days=3)), "item_name": "MSEDCL bill",
                                           "amount": "1795"}, headers=headers(u))
    assert r.status_code == 201, r.text
    client.post("/api/expenses", json={"expense_date": str(today), "item_name": "Groceries", "amount": "1800"}, headers=headers(u))
    bill = client.get("/api/bills", headers=headers(u)).json()[0]
    assert bill["match"]["item_name"] == "MSEDCL bill"

    r = client.post(f"/api/bills/{b['id']}/pay", json={"expense_id": bill["match"]["expense_id"]}, headers=headers(u))
    assert r.status_code == 200, r.text
    db.expire_all()
    # No second expense, and the same expense can't pay another bill
    assert len(db.scalars(select(Expense).where(Expense.user_id == u.id)).all()) == 2
    other = _add(client, u, name="MSEDCL shop", due_date=str(today))
    assert client.post(f"/api/bills/{other['id']}/pay", json={"expense_id": bill["match"]["expense_id"]},
                       headers=headers(u)).status_code == 409


def test_variable_amount_needs_the_amount(client, make_user):
    u = _user(make_user)
    b = _add(client, u)  # no usual amount
    assert client.post(f"/api/bills/{b['id']}/pay", json={}, headers=headers(u)).status_code == 400
    assert client.post(f"/api/bills/{b['id']}/pay", json={"amount": "900", "log_expense": False},
                       headers=headers(u)).status_code == 200


def test_skip_and_last_emi(client, make_user):
    u = _user(make_user)
    b = _add(client, u, name="Car EMI", kind="EMI", amount="9000", due_date="2026-01-05", end_date="2026-02-05")
    r = client.post(f"/api/bills/{b['id']}/skip", headers=headers(u))
    assert r.json()["next_due_date"] == "2026-02-05"
    r = client.post(f"/api/bills/{b['id']}/pay", json={"log_expense": False}, headers=headers(u))
    assert r.json()["next_due_date"] is None and r.json()["status"] == "PAUSED"
    assert r.json()["is_active"] is False


def test_reminders_once_each(client, db, make_user):
    u = _user(make_user)
    today = today_local()
    soon = _add(client, u, name="Airtel postpaid", biller="Airtel", amount="599", due_date=str(today + timedelta(days=2)))
    late = _add(client, u, name="Rent", biller="Landlord", amount="15000", due_date=str(today - timedelta(days=1)))
    assert send_reminders(db, u, today, email=False) == 2
    assert send_reminders(db, u, today, email=False) == 0
    db.commit()
    kinds = {n.kind: n.message for n in db.scalars(select(Notification).where(Notification.user_id == u.id))}
    assert "due in 2 days" in kinds["BILL_DUE"] and "Airtel postpaid" in kinds["BILL_DUE"]
    assert "isn't marked paid" in kinds["BILL_OVERDUE"]
    assert {soon["id"], late["id"]} == {b.id for b in db.scalars(select(Bill))}


def test_other_users_bill_is_private(client, make_user):
    u, other = _user(make_user), _user(make_user, "ravi")
    b = _add(client, u)
    assert client.post(f"/api/bills/{b['id']}/pay", json={"amount": "1"}, headers=headers(other)).status_code in (403, 404)
    assert client.get("/api/bills", headers=headers(other)).json() == []


def test_delete_keeps_the_expense(client, db, make_user):
    u = _user(make_user)
    b = _add(client, u, amount="500")
    client.post(f"/api/bills/{b['id']}/pay", json={}, headers=headers(u))
    assert client.delete(f"/api/bills/{b['id']}", headers=headers(u)).status_code == 204
    db.expire_all()
    assert db.scalar(select(BillPayment)) is None
    assert db.scalar(select(Expense).where(Expense.user_id == u.id)) is not None
