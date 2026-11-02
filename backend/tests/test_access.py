from app.deps import subordinate_ids, would_create_cycle

from .conftest import headers


def test_manager_hierarchy_any_depth(db, make_user):
    a = make_user("a")
    b = make_user("b", manager=a)
    c = make_user("c", manager=b)
    d = make_user("d", manager=c)
    other = make_user("other")
    assert subordinate_ids(db, a.id) == {b.id, c.id, d.id}
    assert subordinate_ids(db, c.id) == {d.id}
    assert subordinate_ids(db, other.id) == set()
    assert would_create_cycle(db, a.id, d.id)
    assert not would_create_cycle(db, d.id, other.id)


def test_manager_reads_but_cannot_write(client, make_user):
    boss = make_user("boss")
    worker = make_user("worker", manager=boss)
    res = client.get("/api/expenses", params={"user_id": worker.id}, headers=headers(boss))
    assert res.status_code == 200
    res = client.post("/api/expenses", params={"user_id": worker.id}, headers=headers(boss),
                      json={"expense_date": "2026-01-01", "item_name": "Tea", "amount": 10})
    assert res.status_code == 403


def test_stranger_cannot_read(client, make_user):
    a = make_user("a")
    b = make_user("b")
    assert client.get("/api/expenses", params={"user_id": a.id}, headers=headers(b)).status_code == 403


def test_search_wildcards_are_literal(client, make_user):
    a = make_user("a")
    for name in ("100% juice", "Tea"):
        client.post("/api/expenses", headers=headers(a), json={"expense_date": "2026-01-01", "item_name": name, "amount": 10})
    names = [e["item_name"] for e in client.get("/api/expenses", params={"search": "%"}, headers=headers(a)).json()]
    assert names == ["100% juice"]
