"""Income categories: money received has a category (Salary, Dividend…) as an expense does, its own list in
Settings → Categories, an alias can give it one, and old rows get one at startup."""

from datetime import timedelta

from sqlalchemy import select

from app.migrate import _data_fixups
from app.models import Fund
from app.timeutil import now_local, today_local

from .conftest import headers


def _fund(client, user, **kw):
    body = {"fund_date": str(today_local()), "amount": "100", "source": "Somebody"}
    body.update(kw)
    r = client.post("/api/funds", json=body, headers=headers(user))
    assert r.status_code == 201, r.text
    return r.json()


def test_category_picked_or_money_received(client, make_user):
    u = make_user("asha")
    assert _fund(client, u, source="NextGen Clearing", category="Salary")["category"] == "Salary"
    assert _fund(client, u, source="Mamta")["category"] == "Money received"
    cats = client.get("/api/funds/categories", headers=headers(u)).json()
    assert {"Salary", "Dividend", "Refunds", "Money received"} <= set(cats)
    # The filter finds them by category
    rows = client.get("/api/funds?category=Salary", headers=headers(u)).json()
    assert [r["source"] for r in rows] == ["NextGen Clearing"]


def test_income_list_is_separate_from_expense_list(client, make_user):
    u = make_user("asha", expense=True)
    h = headers(u)
    assert client.post("/api/categories?kind=income", json={"name": "Ticket Money"}, headers=h).status_code == 201
    # The same name can be in both lists
    assert client.post("/api/categories", json={"name": "Investment"}, headers=h).status_code == 201
    r = client.post("/api/categories?kind=income", json={"name": "Investment"}, headers=h)
    assert r.status_code == 201, r.text
    income = {c["name"]: c for c in r.json()}
    assert income["Ticket Money"]["is_custom"] and income["Investment"]["is_custom"]
    assert "Food" not in income
    expense = {c["name"] for c in client.get("/api/categories", headers=h).json()}
    assert "Ticket Money" not in expense and "Investment" in expense
    assert "Ticket Money" in client.get("/api/funds/categories", headers=h).json()
    # Hide a default income category; records keep it
    _fund(client, u, category="Cashback")
    client.patch("/api/categories/Cashback?kind=income", json={"is_hidden": True}, headers=h)
    assert "Cashback" not in client.get("/api/funds/categories", headers=h).json()
    assert client.get("/api/funds", headers=h).json()[0]["category"] == "Cashback"
    assert client.delete("/api/categories/Ticket Money?kind=income", headers=h).status_code == 200
    assert "Ticket Money" not in client.get("/api/funds/categories", headers=h).json()


def test_alias_gives_money_received_its_income_category(client, make_user):
    u = make_user("asha", expense=True)
    h = headers(u)
    r = client.post("/api/aliases", json={"match_key": "bharat mehta", "alias": "Dad", "category": "Food",
                                          "income_category": "Rental"}, headers=h)
    assert r.status_code == 201, r.text
    f = _fund(client, u, source="NEFT BHARAT MEHTA")
    assert (f["source"], f["category"]) == ("Dad", "Rental")
    # A category picked by hand stays
    assert _fund(client, u, source="NEFT BHARAT MEHTA", category="Money received")["category"] == "Money received"
    # An expense to the same payee gets the expense category, not the income one
    e = client.post("/api/expenses", json={"expense_date": str(today_local()), "item_name": "UPI BHARAT MEHTA", "amount": "5"}, headers=h).json()
    assert e["category"] == "Food"
    # Changing the alias's income category moves the money received that had the old one
    alias_id = r.json()["alias"]["id"]
    client.put(f"/api/aliases/{alias_id}", json={"income_category": "Rent from builder"}, headers=h)
    cats = sorted(x["category"] for x in client.get("/api/funds", headers=h).json())
    assert cats == ["Money received", "Rent from builder"]


def test_edit_category_at_any_time(client, db, make_user):
    u = make_user("asha")
    h = headers(u)
    f = _fund(client, u, source="Patel Har")
    assert f["editable"]
    r = client.put(f"/api/funds/{f['id']}", json={"category": "Ticket Money", "note": "His movie ticket"}, headers=h)
    assert r.status_code == 200, r.text
    assert (r.json()["category"], r.json()["note"]) == ("Ticket Money", "His movie ticket")
    row = db.get(Fund, f["id"])
    row.created_at = now_local() - timedelta(days=10)
    db.commit()
    # Saved long ago: the owner can still change it
    assert client.get("/api/funds", headers=h).json()[0]["editable"] is True
    r = client.put(f"/api/funds/{f['id']}", json={"category": "Salary"}, headers=h)
    assert r.status_code == 200 and r.json()["category"] == "Salary"
    # Someone else can't change it
    other = make_user("ravi")
    assert client.put(f"/api/funds/{f['id']}", json={"category": "Salary"}, headers=headers(other)).status_code in (403, 404)


def test_old_rows_get_a_category_at_startup(db, make_user):
    u = make_user("asha")
    db.add_all([Fund(user_id=u.id, fund_date=today_local(), amount=1, source="Dividend"),
                Fund(user_id=u.id, fund_date=today_local(), amount=2, source="Mamta", origin="SMS")])
    db.commit()
    for f in db.scalars(select(Fund)):
        f.category = None  # as before the column existed
    db.commit()
    _data_fixups(db.get_bind())
    db.expire_all()
    assert {f.source: f.category for f in db.scalars(select(Fund))} == {"Dividend": "Dividend", "Mamta": "Money received"}


def test_upgrading_an_old_database_files_old_rows_by_source(tmp_path):
    """The live database has funds without the column: adding it must not fill old rows with a default first."""
    from sqlalchemy import create_engine, text

    from app.database import Base
    from app.migrate import sync_schema

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        # (SQLite doesn't enforce the user foreign key here, so no user row is needed)
        conn.execute(text("ALTER TABLE funds DROP COLUMN category"))
        conn.execute(text("ALTER TABLE transaction_aliases DROP COLUMN income_category"))
        conn.execute(text("DROP TABLE income_categories"))
        for source, origin in (("Dividend", None), ("Security Deposit Received back", None), ("Mamta", "SMS"), ("Patel Har", "UPI")):
            conn.execute(text("INSERT INTO funds (user_id, fund_date, amount, source, origin, created_at) "
                              "VALUES (1, '2026-11-01', 1, :s, :o, '2026-11-01 10:00:00')"), {"s": source, "o": origin})
    sync_schema(engine)
    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT source, category FROM funds")).all())
        assert conn.execute(text("SELECT COUNT(*) FROM income_categories")).scalar() == 0
    assert rows == {"Dividend": "Dividend", "Security Deposit Received back": "Security Deposit Received back",
                    "Mamta": "Money received", "Patel Har": "Money received"}
    engine.dispose()
