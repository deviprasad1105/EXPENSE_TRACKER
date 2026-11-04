"""Regression: editing a split bill that keeps the same people used to fail with a unique-key error."""

from sqlalchemy import select

from app.models import SplitMember
from app.timeutil import today_local

from .conftest import headers


def test_edit_bill_keeping_the_same_people(client, make_user, db):
    a, b = make_user("asha", split=True), make_user("ravi")
    gid = client.post("/api/splits/groups", json={"name": "Goa", "members": ["@ravi"]}, headers=headers(a)).json()["id"]
    m = {x.user_id: x.id for x in db.scalars(select(SplitMember).where(SplitMember.group_id == gid))}
    bill = {"title": "Dinner", "total_amount": "1000", "bill_date": str(today_local()), "paid_by": m[a.id],
            "shares": [{"member_id": m[a.id]}, {"member_id": m[b.id]}]}
    bid = client.post(f"/api/splits/groups/{gid}/bills", json=bill, headers=headers(a)).json()["id"]
    r = client.put(f"/api/splits/bills/{bid}", json={**bill, "total_amount": "1200"}, headers=headers(a))
    assert r.status_code == 200
    assert sorted(s["amount"] for s in r.json()["shares"]) in (["600.00", "600.00"], ["600", "600"], [600, 600])
