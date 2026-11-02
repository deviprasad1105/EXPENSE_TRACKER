from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import Expense, Notification, Subscription, SubscriptionCharge
from app.services.subscription_import import frequency_from_gaps, parse_message, suggestions_from_sms
from app.services.subscriptions import (
    add_months,
    monthly_cost,
    next_charge_on_or_after,
    process_user,
    refresh_next_charge,
)
from app.timeutil import today_local

from .conftest import headers

D = Decimal


def sub(**kw):
    values = dict(name="Netflix", amount=D("649"), frequency="MONTHLY", interval_count=1, interval_unit="MONTH",
                  start_date=date(2026, 1, 31), end_date=None)
    values.update(kw)
    return Subscription(**values)


# ---------- schedule maths ----------
def test_month_end_keeps_anchor_day():
    assert add_months(date(2026, 1, 31), 1, 31) == date(2026, 2, 28)
    assert add_months(date(2026, 1, 31), 2, 31) == date(2026, 3, 31)
    assert add_months(date(2028, 1, 31), 1, 31) == date(2028, 2, 29)


def test_next_charge_monthly_from_31st():
    s = sub()
    assert next_charge_on_or_after(s, date(2026, 2, 1)) == date(2026, 2, 28)
    assert next_charge_on_or_after(s, date(2026, 3, 1)) == date(2026, 3, 31)
    assert next_charge_on_or_after(s, date(2025, 12, 1)) == date(2026, 1, 31)


def test_quarterly_yearly_weekly_custom():
    assert next_charge_on_or_after(sub(frequency="QUARTERLY", start_date=date(2026, 1, 15)), date(2026, 2, 1)) == date(2026, 4, 15)
    assert next_charge_on_or_after(sub(frequency="YEARLY", start_date=date(2024, 2, 29)), date(2025, 1, 1)) == date(2025, 2, 28)
    assert next_charge_on_or_after(sub(frequency="WEEKLY", start_date=date(2026, 1, 1)), date(2026, 1, 9)) == date(2026, 1, 15)
    custom = sub(frequency="CUSTOM", interval_count=45, interval_unit="DAY", start_date=date(2026, 1, 1))
    assert next_charge_on_or_after(custom, date(2026, 1, 2)) == date(2026, 2, 15)


def test_end_date_stops_charges():
    s = sub(start_date=date(2026, 1, 10), end_date=date(2026, 3, 1))
    assert next_charge_on_or_after(s, date(2026, 2, 11)) is None


def test_monthly_cost():
    assert monthly_cost(sub(amount=D("1200"), frequency="YEARLY")) == D("100.00")
    assert monthly_cost(sub(amount=D("300"), frequency="QUARTERLY")) == D("100.00")
    assert monthly_cost(sub(amount=D("100"), frequency="MONTHLY")) == D("100.00")


# ---------- charges and reminders ----------
def test_due_charge_logged_once_and_reminder_once(db, make_user):
    user = make_user("asha", subscription=True)
    today = today_local()
    s = sub(user_id=user.id, start_date=today + timedelta(days=1))
    refresh_next_charge(s, today)
    db.add(s)
    db.commit()

    # The day before: one reminder, no charge
    assert process_user(db, user, today, email=False) == (0, 1)
    assert process_user(db, user, today, email=False) == (0, 0)
    # On the day: one charge, logged as an expense, and the next date moves on
    tomorrow = today + timedelta(days=1)
    assert process_user(db, user, tomorrow, email=False)[0] == 1
    assert process_user(db, user, tomorrow, email=False)[0] == 0
    db.commit()
    assert db.scalar(select(SubscriptionCharge.expense_id)) is not None
    assert db.scalars(select(Expense)).one().amount == D("649")
    assert s.next_charge_date > tomorrow
    kinds = db.scalars(select(Notification.kind)).all()
    assert kinds.count("SUBSCRIPTION_DUE") == 1 and kinds.count("SUBSCRIPTION_CHARGED") == 1


def test_paused_subscription_is_skipped(db, make_user):
    user = make_user("asha", subscription=True)
    today = today_local()
    s = sub(user_id=user.id, start_date=today, is_active=False)
    refresh_next_charge(s, today)
    db.add(s)
    db.commit()
    assert process_user(db, user, today, email=False) == (0, 0)


# ---------- API ----------
def payload(**kw):
    body = {"name": "Spotify", "amount": 119, "frequency": "MONTHLY", "start_date": str(today_local() + timedelta(days=5))}
    body.update(kw)
    return body


def test_needs_plan(client, make_user):
    user = make_user("asha")
    assert client.get("/api/subscriptions", headers=headers(user)).status_code == 403
    admin = make_user("boss", admin=True)
    assert client.patch(f"/api/admin/users/{user.id}", headers=headers(admin), json={"feature_subscription": True}).status_code == 200
    assert client.get("/api/subscriptions", headers=headers(user)).status_code == 200


def test_crud_toggle_and_summary(client, make_user):
    user = make_user("asha", subscription=True)
    h = headers(user)
    a = client.post("/api/subscriptions", headers=h, json=payload()).json()
    b = client.post("/api/subscriptions", headers=h, json=payload(name="Gym", amount=1200, frequency="QUARTERLY")).json()
    assert a["days_until"] == 5 and a["monthly_cost"] == "119.00"

    summary = client.get("/api/subscriptions/summary", headers=h).json()
    assert summary["active_count"] == 2 and summary["monthly_total"] == 519.0

    res = client.post("/api/subscriptions/toggle", headers=h, json={"ids": [a["id"], b["id"]], "is_active": False})
    assert all(not s["is_active"] for s in res.json())
    summary = client.get("/api/subscriptions/summary", headers=h).json()
    assert summary["active_count"] == 0 and summary["paused_monthly"] == 519.0

    assert client.delete(f"/api/subscriptions/{a['id']}", headers=h).status_code == 204
    assert len(client.get("/api/subscriptions", headers=h).json()) == 1


def test_end_before_start_rejected(client, make_user):
    user = make_user("asha", subscription=True)
    res = client.post("/api/subscriptions", headers=headers(user),
                      json=payload(start_date="2026-05-01", end_date="2026-04-01"))
    assert res.status_code == 400


def test_manager_sees_but_cannot_toggle(client, make_user):
    boss = make_user("boss", subscription=True)
    owner = make_user("owner", subscription=True, manager=boss)
    sid = client.post("/api/subscriptions", headers=headers(owner), json=payload()).json()["id"]
    res = client.get("/api/subscriptions", params={"user_id": owner.id}, headers=headers(boss))
    assert res.status_code == 200 and [s["id"] for s in res.json()] == [sid]
    assert client.post("/api/subscriptions/toggle", headers=headers(boss), json={"ids": [sid], "is_active": False}).status_code == 403


# ---------- import ----------
SMS = """UPI AutoPay mandate created successfully for NETFLIX of Rs 649.00, frequency Monthly, via PhonePe. -HDFC Bank

Rs.149.00 debited from A/c XX1234 on 05-08-26 towards UPI Mandate YOUTUBE PREMIUM. UPI Ref 628811223344
Rs.149.00 debited from A/c XX1234 on 05-09-26 towards UPI Mandate YOUTUBE PREMIUM. UPI Ref 628811223355
Rs.149.00 debited from A/c XX1234 on 05-10-26 towards UPI Mandate YOUTUBE PREMIUM. UPI Ref 628811223366

Sent Rs.250.00 from Kotak Bank AC X1234 to swiggy@ybl on 01-10-26. UPI Ref 1234567890

Rs 500 credited to your account from Rahul"""


def test_parse_mandate_message():
    p = parse_message("Your mandate for Rs.199.00 towards SPOTIFY INDIA has been set up via Google Pay. Frequency: MONTHLY")
    assert p["merchant"] == "Spotify India" and p["amount"] == D("199.00")
    assert p["frequency"] == "MONTHLY" and p["payment_method"] == "GPAY"


def test_sms_suggestions(db, make_user):
    user = make_user("asha")
    found = {s["name"]: s for s in suggestions_from_sms(db, user.id, SMS, date(2026, 10, 30))}
    assert set(found) == {"Netflix", "Youtube Premium"}
    assert found["Netflix"]["payment_method"] == "PHONEPE"
    yt = found["Youtube Premium"]
    assert yt["frequency"] == "MONTHLY" and yt["start_date"] == date(2026, 8, 5) and yt["confidence"] == "high"


def test_frequency_from_gaps():
    assert frequency_from_gaps([date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1)])[0] == "QUARTERLY"
    assert frequency_from_gaps([date(2026, 1, 1), date(2026, 1, 3), date(2026, 3, 1)]) is None


def test_detect_from_expenses(client, make_user):
    user = make_user("asha", subscription=True, expense=True)
    h = headers(user)
    for month in (6, 7, 8, 9):
        client.post("/api/expenses", headers=h, json={"expense_date": f"2026-{month:02d}-03", "item_name": "Rent", "amount": 12000})
    for day in range(1, 8):
        client.post("/api/expenses", headers=h, json={"expense_date": f"2026-09-{day:02d}", "item_name": "Tea", "amount": 10})
    found = client.get("/api/subscriptions/import/detect", headers=h).json()
    assert [s["name"] for s in found] == ["Rent"]
    assert found[0]["frequency"] == "MONTHLY" and found[0]["auto_log_expense"] is False
