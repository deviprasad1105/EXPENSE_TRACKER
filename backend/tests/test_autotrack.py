"""Auto-tracking: bank SMS / app notifications from the phone become expenses, money received, transfers and bills."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import AccountTransfer, BankMessage, Expense, Fund, Notification
from app.routers import autotrack as autotrack_router
from app.services.bank_sms import CH_ATM, CH_CARD, CH_CARD_BILL, CH_UPI, CREDIT, DEBIT, KIND_BILL, NotATransaction, Unreadable, parse_bank_message
from app.timeutil import today_local

from .conftest import headers

R = date(2026, 11, 18)


@pytest.fixture(autouse=True)
def _reset_limiter():
    autotrack_router.device_limiter.reset()


# ---------- reading messages ----------
@pytest.mark.parametrize("sender,text,expect", [
    ("AX-AXISBK", "INR 250.00 debited from A/c no. XX1234 on 17-11-26 at 14:02:11 UPI/P2M/631234567890/SWIGGY LIMITED. "
     "Not you? SMS BLOCKUPI to 919951860002 - Axis Bank",
     dict(direction=DEBIT, amount=Decimal("250.00"), channel=CH_UPI, last4="1234", party="SWIGGY LIMITED", ref="631234567890",
          txn_date=date(2026, 11, 17), bank="Axis Bank")),
    ("VM-SBIUPI-S", "Dear UPI user A/C X5678 debited by 120.0 on date 17Nov26 trf to RAMESH KUMAR Refno 631299887766. "
     "If not u? call 1800111109. -SBI",
     dict(direction=DEBIT, amount=Decimal("120.0"), channel=CH_UPI, last4="5678", party="RAMESH KUMAR", bank="SBI")),
    ("JD-HDFCBK-S", "Spent Rs.1299 On HDFC Bank Card 9876 At AMAZON PAY INDIA On 2026-11-16:20:11:09.Not You? Call 18002586161",
     dict(direction=DEBIT, amount=Decimal("1299"), channel=CH_CARD, last4="9876", party="AMAZON PAY INDIA", txn_date=date(2026, 11, 16))),
    ("AD-HSBCIN", "Your HSBC Credit Card ending 4321 has been used for INR 455.00 at ZOMATO on 15NOV26. Avl limit: INR 1,20,000.00",
     dict(direction=DEBIT, amount=Decimal("455.00"), channel=CH_CARD, last4="4321", party="ZOMATO", is_credit_card=True)),
    ("VK-SBIINB", "Dear Customer, Rs.5,000.00 withdrawn at ATM S1AN000123 from A/c X5678 on 14Nov26. Avl Bal Rs.12,345.67",
     dict(direction=DEBIT, amount=Decimal("5000.00"), channel=CH_ATM, balance=Decimal("12345.67"))),
    ("AX-AXISBK", "INR 85,000.00 credited to A/c no. XX1234 on 01-11-26 by NEFT from ACME TECHNOLOGIES PVT LTD SALARY NOV. "
     "Avl bal INR 1,02,345.00",
     dict(direction=CREDIT, amount=Decimal("85000.00"), party="Salary", balance=Decimal("102345.00"))),
    ("AD-SCAPIA", "Payment of Rs 553.84 received towards your Scapia Credit Card. Thank you!",
     dict(direction=CREDIT, amount=Decimal("553.84"), channel=CH_CARD_BILL, bank="Scapia", is_credit_card=True)),
    ("AX-AXISBK", "INR 3,324.51 debited from A/c no. XX1234 on 05-11-26 towards Metal One credit card bill payment. Avl bal INR 4,726.91",
     dict(direction=DEBIT, amount=Decimal("3324.51"), channel=CH_CARD_BILL, last4="1234", card_name="Metal One", is_credit_card=False)),
    ("VM-ICICIB", "ICICI Bank Acct XX111 debited for Rs 49.00 on 16-Nov-26; JIO PREPAID credited. UPI:631200001111.",
     dict(direction=DEBIT, amount=Decimal("49.00"), party="JIO PREPAID", ref="631200001111")),
    ("AD-KOTAKB", "Rs 1200 refund credited to your Kotak A/c X9999 from FLIPKART on 12/11/26 Ref 99887766",
     dict(direction=CREDIT, amount=Decimal("1200"), party="FLIPKART", ref="99887766")),
])
def test_reads_transactions(sender, text, expect):
    got = parse_bank_message(text, sender, R)
    for key, value in expect.items():
        assert getattr(got, key) == value, (key, getattr(got, key))


def test_reads_a_card_bill():
    got = parse_bank_message("Your HSBC Credit Card XX4321 statement: Total amount due INR 343.79, Minimum due INR 100.00. "
                             "Payment due date 05-Dec-2026.", "AD-HSBCIN", R)
    assert got.kind == KIND_BILL and got.amount == Decimal("343.79") and got.min_due == Decimal("100.00")
    assert got.due_date == date(2026, 12, 5) and got.last4 == "4321"


@pytest.mark.parametrize("text,error", [
    ("123456 is your OTP for txn of INR 500 at AMAZON. Do not share.", NotATransaction),
    ("Get a pre-approved personal loan up to Rs 5,00,000! Apply now", NotATransaction),
    ("Your UPI payment of Rs 200 to abc@ybl has failed.", NotATransaction),
    ("Balance in your A/c XX1234 is INR 4,726.91 as on 18-11-26.", NotATransaction),
    ("RAMESH has requested Rs 500 from you on UPI", NotATransaction),
    ("Rs 500 on your account XX1234. Contact branch.", Unreadable),
])
def test_skips_what_isnt_a_transaction(text, error):
    with pytest.raises(error):
        parse_bank_message(text, "AX-AXISBK", R)


# ---------- recording ----------
DAY = today_local() - timedelta(days=1)
DMY = DAY.strftime("%d-%m-%y")


@pytest.fixture
def asha(client, make_user):
    user = make_user("asha", expense=True, upi=True)
    h = headers(user)
    ids = {}
    for name, kind, last4 in (("Axis Bank", "SAVINGS", "1234"), ("HSBC credit Card", "CREDIT_CARD", "4321"),
                              ("Metal one Credit card", "CREDIT_CARD", "5555"), ("Cash", "CASH", None)):
        r = client.post("/api/accounts", json={"name": name, "kind": kind, "last4": last4}, headers=h)
        assert r.status_code == 201, r.text
        ids[name] = r.json()["id"]
    user.ids = ids
    return user


def _settings(client, user, mode="REVIEW", enabled=True, since=None):
    body = {"enabled": enabled, "mode": mode, "since": (since or today_local() - timedelta(days=30)).isoformat()}
    r = client.put("/api/autotrack/settings", json=body, headers=headers(user))
    assert r.status_code == 200, r.text
    return r.json()


def _send(client, user, *messages):
    msgs = [m if isinstance(m, dict) else {"sender": "AX-AXISBK", "body": m, "source": "SMS"} for m in messages]
    r = client.post("/api/autotrack/messages", json={"messages": msgs}, headers=headers(user))
    assert r.status_code == 201, r.text
    return r.json()


UPI = f"INR 250.00 debited from A/c no. XX1234 on {DMY} UPI/P2M/631234567890/SWIGGY LIMITED. Not you? SMS BLOCKUPI - Axis Bank"


def test_review_mode_waits_then_accepting_records_it(client, db, asha):
    _settings(client, asha)
    out = _send(client, asha, UPI)
    msg = out["results"][0]
    assert msg["status"] == "PENDING" and msg["suggestion"] == "EXPENSE"
    assert msg["account_id"] == asha.ids["Axis Bank"] and msg["category"] == "Food" and msg["name"] == "SWIGGY LIMITED"
    assert not db.scalars(select(Expense)).all()

    r = client.post(f"/api/autotrack/messages/{msg['id']}/accept", json={"category": "Eating out"}, headers=headers(asha))
    assert r.status_code == 200 and r.json()["status"] == "CREATED"
    e = db.get(Expense, r.json()["expense_id"])
    assert (e.amount, e.category, e.account_id, e.origin, e.expense_date) == (Decimal("250.00"), "Eating out", asha.ids["Axis Bank"], "SMS", DAY)
    # Sent again (a rescan): not saved twice
    again = _send(client, asha, UPI)
    assert again["counts"] == {"SEEN": 1} and again["results"] == []


def test_auto_mode_records_card_spend_on_the_card(client, db, asha):
    _settings(client, asha, mode="AUTO")
    out = _send(client, asha, {"sender": "AD-HSBCIN", "source": "SMS",
                               "body": f"Your HSBC Credit Card ending 4321 has been used for INR 455.00 at ZOMATO on {DAY:%d%b%y}."})
    msg = out["results"][0]
    assert msg["status"] == "CREATED", msg
    assert db.get(Expense, msg["expense_id"]).account_id == asha.ids["HSBC credit Card"]


def test_sms_and_notification_of_one_payment_count_once(client, asha):
    _settings(client, asha, mode="AUTO")
    sms = f"Rs 99.00 spent on card XX4321 at UBER on {DMY}."
    note = {"sender": "com.hsbc.app", "source": "NOTIFICATION", "body": f"HSBC: INR 99.00 used at UBER, card ending 4321, {DMY}"}
    out = _send(client, asha, {"sender": "AD-HSBCIN", "body": sms, "source": "SMS"}, note)
    assert sorted(out["counts"]) == ["CREATED", "DUPLICATE"]


def test_same_reference_counts_once(client, asha):
    _settings(client, asha, mode="AUTO")
    out = _send(client, asha, UPI, UPI.replace("SMS BLOCKUPI", "Call 1800"))
    assert out["counts"] == {"CREATED": 1, "DUPLICATE": 1}


def test_card_bill_paid_from_bank_is_one_transfer(client, db, asha):
    _settings(client, asha, mode="AUTO")
    out = _send(client, asha,
                f"INR 3,324.51 debited from A/c no. XX1234 on {DMY} towards Metal One credit card bill payment.",
                {"sender": "AD-ONECRD", "source": "SMS", "body": "Payment of Rs 3324.51 received towards your credit card XX5555. Thanks"})
    assert out["counts"] == {"CREATED": 1, "DUPLICATE": 1}
    t = db.scalars(select(AccountTransfer)).one()
    assert (t.from_account_id, t.to_account_id, t.amount) == (asha.ids["Axis Bank"], asha.ids["Metal one Credit card"], Decimal("3324.51"))
    assert not db.scalars(select(Expense)).all()


def test_atm_withdrawal_moves_money_to_cash(client, db, asha):
    _settings(client, asha, mode="AUTO")
    _send(client, asha, f"Rs.2,000.00 withdrawn at ATM S1AN0001 from A/c XX1234 on {DMY}. Avl Bal Rs.5,000")
    t = db.scalars(select(AccountTransfer)).one()
    assert (t.from_account_id, t.to_account_id) == (asha.ids["Axis Bank"], asha.ids["Cash"])


def test_unknown_account_waits_even_in_auto_mode(client, asha):
    _settings(client, asha, mode="AUTO")
    msg = _send(client, asha, f"INR 80.00 debited from A/c no. XX9999 on {DMY} UPI/P2M/631234560000/TEA STALL.")["results"][0]
    assert msg["status"] == "PENDING" and "Which account" in msg["reason"]


def test_possible_existing_record_waits_even_in_auto_mode(client, asha):
    """Records imported or typed in for the same day and amount: don't add it twice without asking."""
    _settings(client, asha, mode="AUTO")
    client.post("/api/expenses", json={"expense_date": DAY.isoformat(), "item_name": "Swiggy", "amount": "250"}, headers=headers(asha))
    msg = _send(client, asha, UPI)["results"][0]
    assert msg["status"] == "PENDING" and "may be the same" in msg["reason"]
    # Accept-all leaves it for the user to decide
    assert client.post("/api/autotrack/messages/accept-all", headers=headers(asha)).json() == {"recorded": 0, "left": 1}


def test_skipped_unreadable_and_before_start(client, db, asha):
    _settings(client, asha, since=DAY)
    out = _send(client, asha,
                "123456 is your OTP for txn of INR 500. Do not share.",
                "Rs 500 on your account XX1234. Contact branch.",
                "INR 70.00 debited from A/c no. XX1234 on 01-01-20 UPI/P2M/631234569999/OLD SHOP.")
    statuses = {r["body"][:6]: (r["status"], r["reason"]) for r in out["results"]}
    assert statuses["123456"][0] == "IGNORED"
    assert statuses["Rs 500"][0] == "FAILED"
    # The message's date is far from when it arrived, so the arrival day counts: today is after the start date
    assert statuses["INR 70"][0] == "PENDING"

    failed = next(r for r in out["results"] if r["status"] == "FAILED")
    r = client.post(f"/api/autotrack/messages/{failed['id']}/accept", headers=headers(asha), json={
        "suggestion": "EXPENSE", "name": "Branch charge", "amount": "500", "txn_date": DAY.isoformat(), "account_id": asha.ids["Axis Bank"]})
    assert r.status_code == 200 and r.json()["status"] == "CREATED"

    old = {"sender": "AX-AXISBK", "source": "SMS", "received_at": (datetime.combine(DAY, datetime.min.time()) - timedelta(days=3)).isoformat(),
           "body": "INR 75.00 debited from A/c no. XX1234 UPI/P2M/631234568888/BEFORE START."}
    msg = _send(client, asha, old)["results"][0]
    assert msg["status"] == "IGNORED" and "start date" in msg["reason"]


def test_bill_due_notifies_and_is_listed(client, db, asha):
    _settings(client, asha)
    due = today_local() + timedelta(days=10)
    body = f"Your HSBC Credit Card XX4321 statement: Total amount due INR 343.79, Minimum due INR 100.00. Payment due date {due:%d-%b-%Y}."
    out = _send(client, asha, {"sender": "AD-HSBCIN", "source": "SMS", "body": body},
                {"sender": "com.hsbc.app", "source": "NOTIFICATION", "body": "HSBC card: " + body})
    assert out["counts"] == {"BILL": 1, "DUPLICATE": 1}
    bills = client.get("/api/autotrack/bills", headers=headers(asha)).json()
    assert len(bills) == 1 and bills[0]["account_id"] == asha.ids["HSBC credit Card"] and bills[0]["due_date"] == due.isoformat()
    assert db.scalar(select(Notification).where(Notification.user_id == asha.id, Notification.kind == "BILL_DUE"))


def test_turned_off_records_nothing(client, db, asha):
    _settings(client, asha, enabled=False)
    msg = _send(client, asha, UPI)["results"][0]
    assert msg["status"] == "IGNORED" and "turned off" in msg["reason"]


def test_phone_token_sends_in_the_background(client, db, asha):
    _settings(client, asha, mode="AUTO")
    token = client.post("/api/autotrack/device-token", headers=headers(asha)).json()["token"]
    ms = int(datetime.now().timestamp() * 1000)
    r = client.post("/api/autotrack/device/messages", headers={"X-Device-Token": token},
                    json={"messages": [{"sender": "AX-AXISBK", "body": UPI, "source": "SMS", "received_ms": ms}]})
    assert r.status_code == 200 and r.json()["counts"] == {"CREATED": 1}
    assert client.get("/api/autotrack/device/ping", headers={"X-Device-Token": token}).json()["username"] == "asha"
    assert client.post("/api/autotrack/device/messages", headers={"X-Device-Token": "nope"},
                       json={"messages": [{"body": UPI}]}).status_code == 401
    # Unlinking stops the phone
    client.delete("/api/autotrack/device-token", headers=headers(asha))
    assert client.get("/api/autotrack/device/ping", headers={"X-Device-Token": token}).status_code == 401


def test_needs_the_feature(client, make_user):
    bob = make_user("bob", expense=True)
    assert client.get("/api/autotrack/settings", headers=headers(bob)).status_code == 403


def test_settings_default_start_after_existing_records(client, db, asha):
    client.post("/api/funds", json={"fund_date": DAY.isoformat(), "amount": "10", "source": "Gift"}, headers=headers(asha))
    r = client.put("/api/autotrack/settings", json={"enabled": True, "mode": "REVIEW"}, headers=headers(asha))
    assert r.json()["since"] == DAY.isoformat()


def test_dismiss_and_income(client, db, asha):
    _settings(client, asha)
    msg = _send(client, asha, f"INR 85,000.00 credited to A/c no. XX1234 on {DMY} by NEFT from ACME LTD SALARY.")["results"][0]
    assert msg["suggestion"] == "INCOME" and msg["name"] == "Salary"
    r = client.post(f"/api/autotrack/messages/{msg['id']}/accept", json={}, headers=headers(asha))
    assert db.get(Fund, r.json()["fund_id"]).amount == Decimal("85000.00")
    other = _send(client, asha, UPI)["results"][0]
    assert client.post(f"/api/autotrack/messages/{other['id']}/dismiss", headers=headers(asha)).json()["status"] == "DISMISSED"
    assert client.post(f"/api/autotrack/messages/{other['id']}/accept", json={}, headers=headers(asha)).status_code == 400
    assert db.scalars(select(BankMessage).where(BankMessage.status == "PENDING")).all() == []


# ---------- UPI app notifications (PhonePe, Google Pay, Paytm) ----------
@pytest.mark.parametrize("text, direction, amount, party, last4", [
    ("Payment Successful: Payment of ₹10 to Ravi Kumar was successful", DEBIT, "10", "Ravi Kumar", None),
    ("Payment Successful: Your payment of ₹1 to Sri Lakshmi Stores is successful", DEBIT, "1", "Sri Lakshmi Stores", None),
    ("PhonePe: Payment to Swiggy of ₹230 successful", DEBIT, "230", "Swiggy", None),
    ("Payment Complete: Your ₹10 payment to RAVI KUMAR has been completed", DEBIT, "10", "RAVI KUMAR", None),
    ("₹1 Payment Successful: to Sri Lakshmi Stores", DEBIT, "1", "Sri Lakshmi Stores", None),
    ("Money sent!: ₹ 10 sent to Ravi Kumar successfully", DEBIT, "10", "Ravi Kumar", None),
    ("₹10 paid to RAVI KUMAR: Paid via HDFC Bank - 1234", DEBIT, "10", "RAVI KUMAR", "1234"),
    ("Paid ₹45 to Chai Point: From Bank of Baroda ••4321", DEBIT, "45", "Chai Point", "4321"),
    ("Ravi Kumar sent you ₹500", CREDIT, "500", "Ravi Kumar", None),
    ("Payment received: Received ₹500 from Ravi Kumar", CREDIT, "500", "Ravi Kumar", None),
])
def test_reads_upi_app_notifications(text, direction, amount, party, last4):
    got = parse_bank_message(text, "PhonePe", R)
    assert (got.direction, got.amount, got.party, got.last4, got.channel) == (direction, Decimal(amount), party, last4, CH_UPI)


def test_upi_app_payment_uses_the_only_bank_account(client, db, asha):
    _settings(client, asha, mode="AUTO")
    note = {"sender": "PhonePe", "source": "NOTIFICATION", "body": "Payment Successful: Payment of ₹10 to Ravi Kumar was successful"}
    msg = _send(client, asha, note)["results"][0]
    assert msg["status"] == "CREATED" and msg["account_id"] == asha.ids["Axis Bank"] and msg["name"] == "Ravi Kumar"
    e = db.get(Expense, msg["expense_id"])
    assert (e.amount, e.account_id) == (Decimal("10"), asha.ids["Axis Bank"])


def test_upi_app_payment_asks_which_account_when_there_are_several(client, asha):
    client.post("/api/accounts", json={"name": "SBI", "kind": "SAVINGS"}, headers=headers(asha))
    _settings(client, asha, mode="AUTO")
    note = {"sender": "PhonePe", "source": "NOTIFICATION", "body": "PhonePe: Payment to Swiggy of ₹230 successful"}
    msg = _send(client, asha, note)["results"][0]
    assert msg["status"] == "PENDING" and "Which account" in msg["reason"]


@pytest.mark.parametrize("text", [
    "Get ₹100 cashback on your next payment",
    "PhonePe: Pay your electricity bill of ₹540 now",
    "Recharge now! Plans starting at ₹199 for your next payment",
    "Your payment of ₹99 is pending. We will update you shortly",
])
def test_skips_upi_app_offers_and_pending_payments(text):
    with pytest.raises(NotATransaction):
        parse_bank_message(text, "PhonePe", R)


def test_one_bad_message_doesnt_fail_the_batch(client, db, asha, monkeypatch):
    """A message that crashes the reader is kept as "couldn't read"; the rest of the batch is still recorded."""
    _settings(client, asha)
    from app.services import autotrack as svc
    real = svc.parse_bank_message

    def flaky(body, sender, day):
        if "BOOM" in body:
            raise ValueError("unexpected layout")
        return real(body, sender, day)

    monkeypatch.setattr(svc, "parse_bank_message", flaky)
    token = client.post("/api/autotrack/device-token", headers=headers(asha)).json()["token"]
    r = client.post("/api/autotrack/device/messages", headers={"X-Device-Token": token}, json={"messages": [
        {"sender": "AX-AXISBK", "body": "INR 99.00 debited BOOM from A/c XX1234", "source": "SMS"},
        {"sender": "AX-AXISBK", "body": UPI, "source": "SMS"},
    ]})
    assert r.status_code == 200, r.text
    assert sorted(m["status"] for m in r.json()["results"]) == ["FAILED", "PENDING"]


def test_phone_batch_with_odd_fields_is_not_refused(client, asha):
    """Long sender names, empty bodies or an unknown source used to make the server refuse (422) the whole batch,
    and the phone then threw all of it away."""
    _settings(client, asha)
    token = client.post("/api/autotrack/device-token", headers=headers(asha)).json()["token"]
    r = client.post("/api/autotrack/device/messages", headers={"X-Device-Token": token}, json={"messages": [
        {"sender": "A very long bank app name " * 5, "body": UPI, "source": "NOTIFICATION"},
        {"sender": "AX-AXISBK", "body": "", "source": "SMS"},
        {"sender": "AX-AXISBK", "body": UPI.replace("250.00", "251.00").replace("631234567890", "631234567891"), "source": "RCS"},
    ]})
    assert r.status_code == 200, r.text
    assert r.json()["counts"].get("EMPTY") == 1 and len(r.json()["results"]) == 2


# ---------- formats from more banks ----------
@pytest.mark.parametrize("sender,text,expect", [
    ("VM-UNIONB-S", "A/c *1234 Debited for Rs:400.00 on 20-11-2026 13:22:11 by Mob Bk ref no 431234567801 Avl Bal Rs:1,200.00.",
     dict(direction=DEBIT, amount=Decimal("400.00"), balance=Decimal("1200.00"))),
    ("VM-BOBTXN-S", "Rs.1000.00 Dr. from A/C XXXXXX1234 and Cr. to swiggy@icici. Ref:431234567898. AvlBal:Rs5000.00",
     dict(direction=DEBIT, amount=Decimal("1000.00"), party="swiggy@icici")),
    ("VM-CANBNK-S", "An amount of INR 600.00 has been DEBITED to your account XXX1234 on 20/11/2026 towards UPI/431234567899/ZEPTO. "
     "Total Avail.bal INR 7,000.00. - Canara Bank", dict(direction=DEBIT, party="ZEPTO", balance=Decimal("7000.00"))),
    ("AX-ICICIB-S", "INR 3,499.00 spent using ICICI Bank Card XX9876 on 20-Nov-26 on FLIPKART. Avl Limit: INR 1,20,000.00.",
     dict(direction=DEBIT, amount=Decimal("3499.00"), party="FLIPKART", channel=CH_CARD)),
    ("JM-AXISBK-S", "Spent\nCard no. XX4321\nINR 2350\n20-11-26 19:10:01\nDMART\nAvl Lmt INR 85000\nSMS BLOCK 4321 to 919951860002",
     dict(direction=DEBIT, amount=Decimal("2350"), party="DMART", last4="4321")),
    ("AD-SBIINB-S", "Your A/C XXXXX1234 Credited INR 50,000.00 on 20/11/26 -Deposit by transfer from ACME PVT LTD. Avl Bal INR 62,345.67",
     dict(direction=CREDIT, party="ACME PVT LTD")),
    ("BP-PNBSMS-S", "A/c XX1234 debited INR 300.00 Dt 20-11-26 thru UPI:431234567897.Bal INR 9,876.00 Not u?Fwd this SMS to 9264092640 to block UPI",
     dict(direction=DEBIT, party=None)),
    ("VM-INDBNK-S", "Your a/c no. XXXXX1234 is debited for Rs.250.00 on 20-11-2026 and credited to a/c no. XXXXX5678 (UPI Ref no 431234567802)",
     dict(direction=DEBIT, party="A/c ••5678")),
])
def test_reads_more_bank_formats(sender, text, expect):
    got = parse_bank_message(text, sender, R)
    for key, value in expect.items():
        assert getattr(got, key) == value, (key, getattr(got, key))


# ---------- many at once ----------
def _unknown_account(n, amount, last4="7777"):
    return (f"INR {amount}.00 debited from A/c no. XX{last4} on {DMY} UPI/P2M/63123456{n:04d}/SHOP {n}. - Axis Bank")


def test_bulk_record_with_one_account_and_it_is_remembered(client, db, asha):
    _settings(client, asha)
    out = _send(client, asha, *[_unknown_account(n, 100 + n) for n in range(5)])
    ids = [m["id"] for m in out["results"]]
    assert all(m["account_id"] is None and m["reason"].startswith("Which account?") for m in out["results"])
    # Record the first two into Cash: the digits 7777 are remembered, so the other three get the account too
    r = client.post("/api/autotrack/messages/bulk", json={"ids": ids[:2], "action": "record", "account_id": asha.ids["Cash"],
                                                          "category": "Shopping"}, headers=headers(asha))
    assert r.status_code == 200 and r.json()["done"] == 2, r.text
    rest = [db.get(BankMessage, i) for i in ids[2:]]
    assert all(m.account_id == asha.ids["Cash"] and m.reason is None for m in rest)
    assert {e.category for e in db.scalars(select(Expense))} == {"Shopping"}
    # Select all → record: the rest go in with their own (now known) account
    r = client.post("/api/autotrack/messages/bulk", json={"ids": ids, "action": "record"}, headers=headers(asha))
    assert r.json()["done"] == 3 and r.json()["skipped"] == 2
    assert len(db.scalars(select(Expense)).all()) == 5


def test_bulk_dismiss_and_delete_only_touch_own_messages(client, db, asha, make_user):
    _settings(client, asha)
    ids = [m["id"] for m in _send(client, asha, *[_unknown_account(n, 50 + n) for n in range(3)])["results"]]
    other = make_user("ravi", expense=True, upi=True)
    r = client.post("/api/autotrack/messages/bulk", json={"ids": ids, "action": "delete"}, headers=headers(other))
    assert r.json()["done"] == 0 and len(db.scalars(select(BankMessage)).all()) == 3
    r = client.post("/api/autotrack/messages/bulk", json={"ids": ids[:2], "action": "dismiss"}, headers=headers(asha))
    assert r.json()["done"] == 2
    r = client.post("/api/autotrack/messages/bulk", json={"ids": ids, "action": "delete"}, headers=headers(asha))
    assert r.json()["done"] == 3 and not db.scalars(select(BankMessage)).all()


# ---------- payments with no message ----------
def _with_balance(n, amount, balance, minutes, direction="debited"):
    when = datetime.combine(DAY, datetime.min.time()) + timedelta(hours=9, minutes=minutes)
    return {"sender": "AX-AXISBK", "source": "SMS", "received_at": when.isoformat(),
            "body": f"INR {amount}.00 {direction} from A/c no. XX1234 on {DMY} UPI/P2M/6312999{n:05d}/SHOP {n}. Avl bal INR {balance}.00"}


def test_balance_check_finds_a_payment_with_no_message(client, db, asha):
    _settings(client, asha)
    # 10,000 → paid 100 → 9,900. Then a 500 payment sent no SMS. Then paid 200 → balance 9,200 (not 9,700)
    _send(client, asha, _with_balance(1, 100, 9900, 0), _with_balance(2, 200, 9200, 30))
    gaps = client.get("/api/autotrack/gaps", headers=headers(asha)).json()
    assert len(gaps) == 1 and gaps[0]["direction"] == "DEBIT" and Decimal(gaps[0]["amount"]) == Decimal("500.00")
    assert gaps[0]["last4"] == "1234"
    r = client.post(f"/api/autotrack/gaps/{gaps[0]['key']}/review", headers=headers(asha))
    assert r.status_code == 200 and r.json()["status"] == "PENDING" and r.json()["source"] == "BALANCE"
    assert r.json()["account_id"] == asha.ids["Axis Bank"] and r.json()["suggestion"] == "EXPENSE"
    # Now explained: no gap left
    assert client.get("/api/autotrack/gaps", headers=headers(asha)).json() == []


def test_balance_check_is_quiet_when_everything_adds_up(client, asha):
    _settings(client, asha)
    _send(client, asha, _with_balance(1, 100, 9900, 0), _with_balance(2, 200, 9700, 30))
    assert client.get("/api/autotrack/gaps", headers=headers(asha)).json() == []
