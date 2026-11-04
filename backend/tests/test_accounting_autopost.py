"""Phase 2: existing transactions post themselves into the books, exactly once, and follow every change."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.accounting.autopost import sync_all
from app.accounting.engine import get_books
from app.accounting.models import Voucher
from app.models import SplitMember, Subscription, SubscriptionCharge
from app.services.subscriptions import process_user, refresh_next_charge
from app.timeutil import today_local

from .conftest import headers

D = Decimal
API = "/api/accounting"
TODAY = today_local()


def accountant(make_user, name="asha", **kw):
    return make_user(name, accounting=True, **kw)


def open_books(client, user):
    assert client.get(f"{API}/settings", headers=headers(user)).status_code == 200


def lines_of(client, user, **filters):
    """[(type, {ledger name: signed amount})] of the active (unreversed) vouchers, oldest first."""
    page = client.get(f"{API}/vouchers", params={"hide_reversed": True, "size": 200, **filters}, headers=headers(user)).json()
    out = []
    for v in reversed(page["items"]):
        out.append((v["voucher_type"], {l["ledger_name"]: D(l["debit"]) - D(l["credit"]) for l in v["lines"]}))
    return out


def balance(client, user, name):
    g = {g["name"]: g for g in client.get(f"{API}/ledgers", headers=headers(user)).json()}[name]
    return D(g["balance"]) * (1 if g["balance_side"] == "DR" else -1)


def expense(client, user, amount, category="Food", day=None, **kw):
    body = {"expense_date": str(day or TODAY), "item_name": "Lunch", "category": category, "amount": str(amount), **kw}
    return client.post("/api/expenses", json=body, headers=headers(user))


def count_vouchers(db):
    db.expire_all()
    return db.scalar(select(func.count(Voucher.id)))


# ---------- expenses ----------
def test_personal_expense_posts_to_drawings_and_business_category_to_its_ledger(client, make_user):
    me = accountant(make_user)
    client.post("/api/accounts", json={"name": "HDFC", "opening_balance": 5000}, headers=headers(me))
    open_books(client, me)
    expense(client, me, 250, "Food")
    expense(client, me, 9000, "Rent")
    assert lines_of(client, me) == [
        ("PAYMENT", {"Drawings": D(250), "HDFC": D(-250)}),
        ("PAYMENT", {"Rent": D(9000), "HDFC": D(-9000)}),
    ]
    assert balance(client, me, "HDFC") == D(5000 - 250 - 9000)


def test_expense_without_any_account_goes_to_suspense(client, make_user):
    me = accountant(make_user)
    open_books(client, me)
    expense(client, me, 40)
    assert lines_of(client, me) == [("PAYMENT", {"Drawings": D(40), "Suspense": D(-40)})]


def test_existing_history_is_backfilled_once(client, make_user, db):
    me = accountant(make_user)
    expense(client, me, 10, day=TODAY - timedelta(days=40))
    expense(client, me, 20)
    client.post("/api/funds", json={"fund_date": str(TODAY), "amount": "1000", "source": "Salary"}, headers=headers(me))
    open_books(client, me)
    assert count_vouchers(db) == 3
    # Re-syncing (from Settings, or a retried request) never posts twice
    r = client.post(f"{API}/sync", headers=headers(me)).json()
    assert r["posted"] == 0 and r["unchanged"] == 3
    assert count_vouchers(db) == 3


def test_editing_an_expense_reverses_and_reposts(client, make_user, db):
    me = accountant(make_user)
    open_books(client, me)
    e = expense(client, me, 100).json()
    assert count_vouchers(db) == 1
    client.put(f"/api/expenses/{e['id']}", json={"amount": "120"}, headers=headers(me))
    # original + its reversal + the new version
    assert count_vouchers(db) == 3
    assert lines_of(client, me) == [("PAYMENT", {"Drawings": D(120), "Suspense": D(-120)})]
    assert balance(client, me, "Drawings") == D(120)
    # Saving it unchanged posts nothing
    client.put(f"/api/expenses/{e['id']}", json={"amount": "120"}, headers=headers(me))
    assert count_vouchers(db) == 3


def test_note_change_updates_narration_without_reposting(client, make_user, db):
    me = accountant(make_user)
    open_books(client, me)
    e = expense(client, me, 100).json()
    client.put(f"/api/expenses/{e['id']}", json={"note": "with client"}, headers=headers(me))
    assert count_vouchers(db) == 1
    v = client.get(f"{API}/vouchers", headers=headers(me)).json()["items"][0]
    assert v["narration"] == "Lunch – with client"


def test_deleting_an_expense_reverses_its_voucher(client, make_user, db):
    me = accountant(make_user)
    open_books(client, me)
    e = expense(client, me, 100).json()
    client.delete(f"/api/expenses/{e['id']}", headers=headers(me))
    assert lines_of(client, me) == []
    assert count_vouchers(db) == 2
    assert balance(client, me, "Drawings") == 0


# ---------- funds and transfers ----------
def test_fund_is_a_receipt_into_capital_and_transfer_is_contra(client, make_user):
    me = accountant(make_user)
    h = headers(me)
    a = client.post("/api/accounts", json={"name": "Salary A/c"}, headers=h).json()
    b = client.post("/api/accounts", json={"name": "Wallet", "kind": "WALLET"}, headers=h).json()
    open_books(client, me)
    client.post("/api/funds", json={"fund_date": str(TODAY), "amount": "50000", "source": "Salary", "account_id": a["id"]}, headers=h)
    client.post("/api/accounts/transfers", json={"from_account_id": a["id"], "to_account_id": b["id"], "amount": "2000",
                                                 "transfer_date": str(TODAY)}, headers=h)
    assert lines_of(client, me) == [
        ("RECEIPT", {"Salary A/c": D(50000), "Capital Account": D(-50000)}),
        ("CONTRA", {"Wallet": D(2000), "Salary A/c": D(-2000)}),
    ]


def test_account_with_entries_cannot_be_deleted(client, make_user):
    me = accountant(make_user)
    h = headers(me)
    a = client.post("/api/accounts", json={"name": "SBI"}, headers=h).json()
    open_books(client, me)
    expense(client, me, 10, account_id=a["id"])
    r = client.delete(f"/api/accounts/{a['id']}", headers=h)
    assert r.status_code == 400 and "Deactivate" in r.json()["detail"]
    assert len(client.get("/api/accounts", headers=h).json()) == 1


# ---------- subscriptions ----------
def _subscription(db, user, auto_log=True):
    s = Subscription(user_id=user.id, name="Netflix", amount=D("649"), frequency="MONTHLY", start_date=TODAY,
                     category="Entertainment", auto_log_expense=auto_log)
    refresh_next_charge(s, TODAY)
    db.add(s)
    db.commit()
    return s


def test_subscription_charge_logged_as_expense_posts_once(client, make_user, db):
    me = accountant(make_user, subscription=True)
    open_books(client, me)
    _subscription(db, me)
    process_user(db, me, TODAY, email=False)
    db.commit()
    assert lines_of(client, me) == [("PAYMENT", {"Drawings": D(649), "Suspense": D(-649)})]
    assert lines_of(client, me)[0] and count_vouchers(db) == 1


def test_charge_not_logged_as_expense_posts_itself_and_takes_over_when_expense_deleted(client, make_user, db):
    me = accountant(make_user, subscription=True)
    open_books(client, me)
    _subscription(db, me, auto_log=False)
    process_user(db, me, TODAY, email=False)
    db.commit()
    assert lines_of(client, me) == [("PAYMENT", {"Drawings": D(649), "Suspense": D(-649)})]

    other = accountant(make_user, "ravi", subscription=True)
    open_books(client, other)
    _subscription(db, other)
    process_user(db, other, TODAY, email=False)
    db.commit()
    expense_id = db.scalar(select(SubscriptionCharge.expense_id).join(Subscription).where(Subscription.user_id == other.id))
    client.delete(f"/api/expenses/{expense_id}", headers=headers(other))
    # The money still left the account: the charge now carries the posting
    assert lines_of(client, other) == [("PAYMENT", {"Drawings": D(649), "Suspense": D(-649)})]


def test_charge_in_a_locked_period_is_skipped(client, make_user, db):
    me = accountant(make_user, subscription=True)
    open_books(client, me)
    client.put(f"{API}/settings", json={"lock_date": str(TODAY)}, headers=headers(me))
    _subscription(db, me)
    process_user(db, me, TODAY, email=False)
    db.commit()
    assert db.scalar(select(SubscriptionCharge.id)) is None
    assert count_vouchers(db) == 0


# ---------- split bills ----------
def _group(client, db, owner, *others):
    gid = client.post("/api/splits/groups", json={"name": "Goa", "members": [f"@{o.username}" for o in others]},
                      headers=headers(owner)).json()["id"]
    members = {m.user_id: m.id for m in db.scalars(select(SplitMember).where(SplitMember.group_id == gid))}
    return gid, members


def test_split_bill_and_settlement_post_in_both_users_books(client, make_user, db):
    a, b = accountant(make_user, "asha", split=True), accountant(make_user, "ravi")
    open_books(client, a)
    open_books(client, b)
    gid, m = _group(client, db, a, b)
    bill = {"title": "Dinner", "total_amount": "1000", "bill_date": str(TODAY), "paid_by": m[a.id],
            "shares": [{"member_id": m[a.id]}, {"member_id": m[b.id]}]}
    assert client.post(f"/api/splits/groups/{gid}/bills", json=bill, headers=headers(a)).status_code == 201

    assert lines_of(client, a) == [("PAYMENT", {"Drawings": D(500), "Split – Goa": D(500), "Suspense": D(-1000)})]
    assert lines_of(client, b) == [("JOURNAL", {"Drawings": D(500), "Split – Goa": D(-500)})]

    pay = {"from_member_id": m[b.id], "to_member_id": m[a.id], "amount": "500", "settled_on": str(TODAY)}
    assert client.post(f"/api/splits/groups/{gid}/settlements", json=pay, headers=headers(b)).status_code == 201
    assert balance(client, a, "Split – Goa") == 0
    assert balance(client, b, "Split – Goa") == 0
    assert balance(client, a, "Suspense") == D(-500)


def test_split_bill_edit_is_refused_when_another_members_books_are_locked(client, make_user, db):
    a, b = accountant(make_user, "asha", split=True), accountant(make_user, "ravi")
    open_books(client, a)
    open_books(client, b)
    gid, m = _group(client, db, a, b)
    day = TODAY - timedelta(days=5)
    bill = {"title": "Dinner", "total_amount": "1000", "bill_date": str(day), "paid_by": m[a.id],
            "shares": [{"member_id": m[a.id]}, {"member_id": m[b.id]}]}
    bill_id = client.post(f"/api/splits/groups/{gid}/bills", json=bill, headers=headers(a)).json()["id"]
    client.put(f"{API}/settings", json={"lock_date": str(day)}, headers=headers(b))

    r = client.put(f"/api/splits/bills/{bill_id}", json={**bill, "total_amount": "1200"}, headers=headers(a))
    assert r.status_code == 409 and "@ravi" in r.json()["detail"]
    assert balance(client, a, "Split – Goa") == D(500)


# ---------- locked periods ----------
def test_locked_expense_cannot_be_edited_deleted_or_added(client, make_user, admin_and_me=None):
    me = accountant(make_user)
    admin = make_user("root", admin=True)
    open_books(client, me)
    day = TODAY - timedelta(days=1)
    e = expense(client, me, 100, day=day).json()
    client.put(f"{API}/settings", json={"lock_date": str(day)}, headers=headers(me))

    assert client.put(f"/api/expenses/{e['id']}", json={"amount": "150"}, headers=headers(admin), params={"user_id": me.id}).status_code == 409
    assert client.put(f"/api/expenses/{e['id']}", json={"note": "x"}, headers=headers(me)).status_code == 409
    assert client.delete(f"/api/expenses/{e['id']}", headers=headers(admin)).status_code == 409
    assert expense(client, me, 5, day=day).status_code == 409
    assert balance(client, me, "Drawings") == D(100)
    # Moving it out of the locked period is an edit of the locked voucher too
    assert client.put(f"/api/expenses/{e['id']}", json={"expense_date": str(TODAY)}, headers=headers(me)).status_code == 409


# ---------- posting rules ----------
def test_changing_a_rule_reposts_open_periods_only(client, make_user, db):
    me = accountant(make_user)
    open_books(client, me)
    old_day = TODAY - timedelta(days=3)
    expense(client, me, 100, "Travel", day=old_day)
    expense(client, me, 60, "Travel")
    client.put(f"{API}/settings", json={"lock_date": str(old_day)}, headers=headers(me))

    rules = {r["label"]: r for r in client.get(f"{API}/rules", headers=headers(me)).json()}
    assert rules["Travel"]["ledger_name"] == "Drawings" and rules["Travel"]["usage"] == 2
    travel = {g["name"]: g["id"] for g in client.get(f"{API}/ledgers", headers=headers(me)).json()}["Travelling & Conveyance"]
    r = client.put(f"{API}/rules/{rules['Travel']['id']}", json={"ledger_id": travel}, headers=headers(me)).json()
    assert r["sync"]["reposted"] == 1 and len(r["sync"]["locked"]) == 1
    assert balance(client, me, "Travelling & Conveyance") == D(60)
    assert balance(client, me, "Drawings") == D(100)


def test_rule_cannot_point_at_a_bank_ledger(client, make_user):
    me = accountant(make_user)
    open_books(client, me)
    expense(client, me, 10)
    rule = client.get(f"{API}/rules", headers=headers(me)).json()[0]
    cash = {g["name"]: g["id"] for g in client.get(f"{API}/ledgers", headers=headers(me)).json()}["Cash"]
    assert client.put(f"{API}/rules/{rule['id']}", json={"ledger_id": cash}, headers=headers(me)).status_code == 400


# ---------- users without books ----------
def test_users_without_accounting_are_untouched(client, make_user, db):
    plain = make_user("plain")
    assert expense(client, plain, 10).status_code == 201
    assert get_books(db, plain.id) is None
    assert count_vouchers(db) == 0


def test_sync_all_twice_gives_identical_books(client, make_user, db):
    me = accountant(make_user)
    for i in range(5):
        expense(client, me, 10 + i, category=["Food", "Rent"][i % 2], day=TODAY - timedelta(days=i))
    open_books(client, me)
    before = count_vouchers(db)
    books = get_books(db, me.id)
    assert sync_all(db, books).posted == 0
    db.commit()
    assert count_vouchers(db) == before == 5
