"""Credit Cards page: bills and due dates per card, paid status from payments, and reminders near the due date."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import Account, CardBill, Notification
from app.services import cards as svc
from app.timeutil import today_local

from .conftest import headers

TODAY = today_local()


def _setup(client, make_user, **features):
    user = make_user("asha", expense=True, **features)
    h = headers(user)
    bank = client.post("/api/accounts", json={"name": "Axis Bank", "kind": "SAVINGS", "opening_balance": "50000"}, headers=h).json()["id"]
    card = client.post("/api/accounts", json={"name": "HSBC credit Card", "kind": "CREDIT_CARD", "last4": "4321"}, headers=h).json()["id"]
    r = client.post("/api/expenses", json={"expense_date": (TODAY - timedelta(days=3)).isoformat(), "item_name": "Shoes",
                                           "amount": "1000", "account_id": card}, headers=h)
    assert r.status_code == 201, r.text
    return user, h, bank, card


def _cards(client, h):
    r = client.get("/api/cards", headers=h)
    assert r.status_code == 200, r.text
    return {c["name"]: c for c in r.json()}


def test_card_owes_and_estimated_bill_from_due_day(client, make_user):
    user, h, bank, card = _setup(client, make_user)
    c = _cards(client, h)["HSBC credit Card"]
    assert Decimal(c["owed"]) == 1000 and c["bill"] is None

    due = TODAY + timedelta(days=2)
    r = client.put(f"/api/cards/{card}/settings", headers=h, json={"credit_limit": "50000", "due_day": due.day, "statement_day": None,
                                                                     "remind_days": 3, "remind_on": True})
    assert r.status_code == 200, r.text
    c = r.json()
    assert Decimal(c["available"]) == 49000 and c["used_percent"] == 2.0
    assert c["bill"]["source"] == "ESTIMATE" and c["bill"]["due_date"] == due.isoformat()
    assert Decimal(c["bill"]["total_due"]) == 1000 and c["bill"]["status"] == "DUE" and c["bill"]["days_left"] == 2
    # The bank isn't a card
    assert set(_cards(client, h)) == {"HSBC credit Card"}


def test_reminders_before_on_the_day_and_overdue_each_once(client, db, make_user):
    user, h, bank, card = _setup(client, make_user)
    due = TODAY + timedelta(days=2)
    client.put(f"/api/cards/{card}/settings", headers=h, json={"due_day": due.day, "remind_days": 3, "remind_on": True})

    def notes():
        return [n.message for n in db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.kind == "CARD_DUE"))]

    assert svc.run_for_user(db, user, TODAY, email=False) == 1
    assert "due in 2 days" in notes()[0]
    assert svc.run_for_user(db, user, TODAY + timedelta(days=1), email=False) == 0  # "before" goes once
    assert svc.run_for_user(db, user, due, email=False) == 1
    assert "due today" in notes()[-1]
    assert svc.run_for_user(db, user, due + timedelta(days=1), email=False) == 1
    assert "isn't paid yet" in notes()[-1]
    assert svc.run_for_user(db, user, due + timedelta(days=2), email=False) == 0


def test_payment_into_the_card_marks_the_bill_paid_and_stops_reminders(client, db, make_user):
    user, h, bank, card = _setup(client, make_user)
    due = TODAY + timedelta(days=1)
    client.post(f"/api/cards/{card}/bills", headers=h, json={"due_date": due.isoformat(), "total_due": "1000", "min_due": "100",
                                                              "statement_date": (TODAY - timedelta(days=10)).isoformat()})
    r = client.post("/api/accounts/transfers", headers=h, json={"from_account_id": bank, "to_account_id": card, "amount": "400",
                                                                 "transfer_date": TODAY.isoformat()})
    assert r.status_code == 201, r.text
    bill = _cards(client, h)["HSBC credit Card"]["bill"]
    assert bill["status"] == "DUE" and Decimal(bill["paid_amount"]) == 400
    assert svc.run_for_user(db, user, TODAY, email=False) == 1
    assert "bill of 600" in db.scalars(select(Notification.message).where(Notification.kind == "CARD_DUE")).first()

    client.post("/api/accounts/transfers", headers=h, json={"from_account_id": bank, "to_account_id": card, "amount": "600",
                                                             "transfer_date": TODAY.isoformat()})
    bill = _cards(client, h)["HSBC credit Card"]["bill"]
    assert bill["status"] == "PAID" and bill["paid"] is True
    assert svc.run_for_user(db, user, due, email=False) == 0


def test_typed_in_bill_replaces_estimate_and_isnt_overwritten(client, db, make_user):
    user, h, bank, card = _setup(client, make_user)
    due = TODAY + timedelta(days=5)
    client.put(f"/api/cards/{card}/settings", headers=h, json={"due_day": due.day, "remind_days": 3, "remind_on": True})
    actual_due = TODAY + timedelta(days=6)
    r = client.post(f"/api/cards/{card}/bills", headers=h, json={"due_date": actual_due.isoformat(), "total_due": "1234.50", "min_due": "60"})
    assert r.status_code == 201 and r.json()["source"] == "MANUAL"
    bills = db.scalars(select(CardBill).where(CardBill.account_id == card)).all()
    assert len(bills) == 1 and bills[0].due_date == actual_due
    # A statement message for the same cycle doesn't change what the user typed
    svc.save_bill(db, db.get(Account, card), actual_due, Decimal("999"), svc.SRC_SMS)
    db.commit()
    db.expire_all()
    assert db.get(CardBill, bills[0].id).total_due == Decimal("1234.50")
    assert client.post(f"/api/cards/{card}/bills", headers=h, json={"due_date": due.isoformat(), "total_due": "10", "min_due": "20"}).status_code == 400


def test_statement_sms_becomes_the_cards_bill(client, db, make_user):
    user, h, bank, card = _setup(client, make_user, upi=True)
    client.put("/api/autotrack/settings", headers=h, json={"enabled": True, "mode": "REVIEW", "since": (TODAY - timedelta(days=30)).isoformat()})
    due = TODAY + timedelta(days=12)
    body = f"Your HSBC Credit Card XX4321 statement: Total amount due INR 1,000.00, Minimum due INR 50.00. Payment due date {due:%d-%b-%Y}."
    out = client.post("/api/autotrack/messages", headers=h, json={"messages": [{"sender": "AD-HSBCIN", "body": body, "source": "SMS"}]}).json()
    assert out["counts"] == {"BILL": 1}
    bill = _cards(client, h)["HSBC credit Card"]["bill"]
    assert bill["source"] == "SMS" and bill["due_date"] == due.isoformat() and Decimal(bill["min_due"]) == 50


def test_mark_paid_unpaid_and_delete(client, db, make_user):
    user, h, bank, card = _setup(client, make_user)
    bill = client.post(f"/api/cards/{card}/bills", headers=h, json={"due_date": (TODAY + timedelta(days=3)).isoformat(), "total_due": "1000"}).json()
    assert client.post(f"/api/cards/bills/{bill['id']}/paid", headers=h, json={"paid": True}).json()["status"] == "PAID"
    assert client.post(f"/api/cards/bills/{bill['id']}/paid", headers=h, json={"paid": False}).json()["status"] == "DUE"
    assert len(client.get(f"/api/cards/{card}/bills", headers=h).json()) == 1
    assert client.delete(f"/api/cards/bills/{bill['id']}", headers=h).status_code == 204
    assert client.get(f"/api/cards/{card}/bills", headers=h).json() == []


def test_paid_past_bill_rolls_to_next_estimate(client, db, make_user):
    user, h, bank, card = _setup(client, make_user)
    past = TODAY - timedelta(days=5)
    client.put(f"/api/cards/{card}/settings", headers=h, json={"due_day": past.day, "remind_days": 3, "remind_on": True})
    old = client.post(f"/api/cards/{card}/bills", headers=h, json={"due_date": past.isoformat(), "total_due": "200"}).json()
    client.post(f"/api/cards/bills/{old['id']}/paid", headers=h, json={"paid": True})
    bill = _cards(client, h)["HSBC credit Card"]["bill"]
    assert bill["source"] == "ESTIMATE" and date.fromisoformat(bill["due_date"]) > TODAY and Decimal(bill["total_due"]) == 1000


def test_deleting_the_card_removes_its_bills_and_others_cant_change_them(client, db, make_user):
    user, h, bank, card = _setup(client, make_user)
    bill = client.post(f"/api/cards/{card}/bills", headers=h, json={"due_date": (TODAY + timedelta(days=3)).isoformat(), "total_due": "1000"}).json()
    stranger = make_user("bob", expense=True)
    assert client.post(f"/api/cards/bills/{bill['id']}/paid", headers=headers(stranger), json={"paid": True}).status_code == 403
    assert client.put(f"/api/cards/{card}/settings", headers=headers(stranger), json={"remind_days": 1, "remind_on": True}).status_code == 403
    assert client.delete(f"/api/accounts/{card}", headers=h).status_code == 204
    assert db.scalars(select(CardBill)).all() == []


def test_overdue_bill_shows_before_next_months_estimate(client, db, make_user):
    """Due day set first (an estimate for next month appears), then last month's bill typed in, part paid: the overdue
    bill is what the page shows and reminds about, not next month's estimate."""
    user, h, bank, card = _setup(client, make_user)
    past = TODAY - timedelta(days=1)
    client.put(f"/api/cards/{card}/settings", headers=h, json={"due_day": past.day, "remind_days": 3, "remind_on": True})
    client.post(f"/api/cards/{card}/bills", headers=h, json={"due_date": past.isoformat(), "total_due": "1000"})
    client.post("/api/accounts/transfers", headers=h, json={"from_account_id": bank, "to_account_id": card, "amount": "300",
                                                             "transfer_date": TODAY.isoformat()})
    bill = _cards(client, h)["HSBC credit Card"]["bill"]
    assert bill["due_date"] == past.isoformat() and bill["status"] == "OVERDUE" and Decimal(bill["paid_amount"]) == 300
    assert svc.run_for_user(db, user, TODAY, email=False) == 1
    assert "isn't paid yet" in db.scalars(select(Notification.message).where(Notification.kind == "CARD_DUE")).first()
