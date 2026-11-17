"""An admin can delete anyone, even someone in split bill groups: they stay in those groups as a guest."""

from sqlalchemy import select

from app.models import SplitBill, SplitGroup, SplitMember, User
from app.timeutil import today_local

from .conftest import headers


def make_group(client, db, owner, *others, guests=()):
    gid = client.post(
        "/api/splits/groups", json={"name": "Goa", "members": [f"@{u.username}" for u in others] + list(guests)}, headers=headers(owner)
    ).json()["id"]
    members = {m.user_id or m.guest_name: m.id for m in db.scalars(select(SplitMember).where(SplitMember.group_id == gid))}
    return gid, members


def add_bill(client, gid, payer_member, member_ids, by, amount="900"):
    bill = {"title": "Dinner", "total_amount": amount, "bill_date": str(today_local()), "paid_by": payer_member,
            "shares": [{"member_id": m} for m in member_ids]}
    r = client.post(f"/api/splits/groups/{gid}/bills", json=bill, headers=headers(by))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def nets(client, gid, user):
    group = client.get(f"/api/splits/groups/{gid}", headers=headers(user)).json()
    return {m["member"]["id"]: float(m["net"]) for m in group["members"]}, group


def test_member_with_a_balance_becomes_a_guest_and_balances_stay(client, make_user, db):
    admin = make_user("boss", admin=True)
    asha, ravi, meena = make_user("asha", split=True), make_user("ravi"), make_user("meena")
    gid, m = make_group(client, db, asha, ravi, meena)
    add_bill(client, gid, m[ravi.id], [m[asha.id], m[ravi.id], m[meena.id]], ravi)  # Ravi paid, everyone owes him 300
    before, _ = nets(client, gid, asha)
    ravi_id = ravi.id

    r = client.delete(f"/api/admin/users/{ravi_id}", headers=headers(admin))
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(User, ravi_id) is None

    after, group = nets(client, gid, asha)
    assert after == before  # nobody's balance moved
    ravi_row = next(x["member"] for x in group["members"] if x["member"]["id"] == m[ravi_id])
    assert ravi_row["is_guest"] and ravi_row["user_id"] is None
    assert ravi_row["full_name"] == "Ravi (deleted account)"
    bill = group["bills"][0]
    assert bill["created_by"]["id"] == asha.id  # the bill Ravi recorded now counts as the owner's

    # Asha can still settle up with him
    pay = {"from_member_id": m[asha.id], "to_member_id": m[ravi_id], "amount": "300", "settled_on": str(today_local())}
    assert client.post(f"/api/splits/groups/{gid}/settlements", json=pay, headers=headers(asha)).status_code == 201


def test_deleting_the_owner_passes_the_group_to_another_member(client, make_user, db):
    admin = make_user("boss", admin=True)
    asha, ravi, meena = make_user("asha", split=True), make_user("ravi"), make_user("meena")
    gid, m = make_group(client, db, asha, ravi, meena)
    add_bill(client, gid, m[asha.id], [m[asha.id], m[ravi.id], m[meena.id]], asha)
    # Meena is a group admin: she is preferred over Ravi, who isn't
    db.get(SplitMember, m[meena.id]).is_admin = True
    db.commit()

    assert client.delete(f"/api/admin/users/{asha.id}", headers=headers(admin)).status_code == 200
    db.expire_all()
    group = db.get(SplitGroup, gid)
    assert group.owner_id == meena.id
    assert all(b.created_by == meena.id for b in db.scalars(select(SplitBill).where(SplitBill.group_id == gid)))
    r = client.get(f"/api/splits/groups/{gid}", headers=headers(meena))
    assert r.status_code == 200 and r.json()["me"]["is_owner"] is True


def test_group_with_nobody_else_on_the_app_is_deleted(client, make_user, db):
    admin = make_user("boss", admin=True)
    asha = make_user("asha", split=True)
    gid, m = make_group(client, db, asha, guests=["Kiran"])
    add_bill(client, gid, m[asha.id], [m[asha.id], m["Kiran"]], asha)

    assert client.delete(f"/api/admin/users/{asha.id}", headers=headers(admin)).status_code == 200
    db.expire_all()
    assert db.get(SplitGroup, gid) is None
    assert db.scalar(select(SplitMember.id).where(SplitMember.group_id == gid)) is None


def test_restoring_the_account_puts_them_back_in_the_group(client, make_user, db):
    admin = make_user("boss", admin=True)
    asha, ravi = make_user("asha", split=True), make_user("ravi")
    gid, m = make_group(client, db, asha, ravi)
    add_bill(client, gid, m[ravi.id], [m[asha.id], m[ravi.id]], ravi)

    backup = client.delete(f"/api/admin/users/{ravi.id}", headers=headers(admin)).json()
    assert client.post(f"/api/admin/backups/{backup['id']}/restore", headers=headers(admin)).status_code == 200
    db.expire_all()
    member = db.get(SplitMember, m[ravi.id])
    assert member.user_id == ravi.id and member.guest_name is None
    assert client.get(f"/api/splits/groups/{gid}", headers=headers(ravi)).status_code == 200


def test_org_admin_still_cannot_delete_a_member_in_split_groups(client, make_user, db):
    org_admin = make_user("orgboss")
    org_admin.role = "ORG_ADMIN"
    asha = make_user("asha", split=True)
    ravi = make_user("ravi")
    ravi.org_admin_id = org_admin.id
    db.commit()
    make_group(client, db, asha, ravi)

    r = client.delete(f"/api/org/members/{ravi.id}", headers=headers(org_admin))
    assert r.status_code == 400 and "split bill groups" in r.json()["detail"]
    db.expire_all()
    assert db.get(User, ravi.id) is not None
