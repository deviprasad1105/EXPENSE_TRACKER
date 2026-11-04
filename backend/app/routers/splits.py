"""Split bills: groups of people sharing bills, who owes whom, and settling up.

Rules (all enforced here):
- Creating a group, and adding guests, needs split bills in your plan (granted by an admin). Anyone added to a
  group can use that group (add bills, record payments, leave) without it.
- Members are app users, added as '@username', or guests: people who aren't on the app, added by name only to
  track what they owe. Guests can't sign in and get no notifications.
- The group creator is its owner: always a group admin, cannot be removed, and the only one who can delete
  the group (and only once every balance is settled).
- Group admins add and remove members and can make other members admin.
- Any member can add a bill or a settlement, but only whoever created it can change or delete it.
  If that person has left the group, the owner takes over their bills.
- Every change notifies the other members.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from ..database import get_db
from ..deps import FEATURE_SPLIT, get_current_user, has_feature, require_feature
from ..models import (
    SHARE_EQUAL,
    STATUS_APPROVED,
    Notification,
    SplitBill,
    SplitGroup,
    SplitMember,
    SplitSettlement,
    SplitShare,
    User,
)
from ..schemas import (
    SplitBillIn,
    SplitGroupCreate,
    SplitGroupIn,
    SplitMemberAdd,
    SplitMemberRemove,
    SplitMemberUpdate,
    SplitSettlementIn,
    UserBrief,
)
from ..services.splits import (
    BillChange,
    add_participant,
    allocate,
    is_settled,
    member_totals,
    pair_debts,
    remove_participant,
)
from ..timeutil import now_local, today_local

router = APIRouter(prefix="/api/splits", tags=["split bills"])
# Only creating groups and adding guests need the plan; everything else only needs group membership
split_user = require_feature(FEATURE_SPLIT)

KIND_SPLIT = "SPLIT"


# ---------- helpers ----------
def _brief(u: User) -> dict:
    return UserBrief.model_validate(u).model_dump()


def _person(m: SplitMember) -> dict:
    """A member as the UI shows them. `id` is the member id; `user_id` is None for guests."""
    u = m.user
    return {
        "id": m.id,
        "user_id": m.user_id,
        "username": u.username if u else None,
        "full_name": m.name,
        "avatar_updated_at": u.avatar_updated_at if u else None,
        "is_guest": m.is_guest,
        "added_by": m.added_by,
        "active": m.active,
    }


def _label(m: SplitMember) -> str:
    return f"{m.name} (@{m.user.username})" if m.user else f"{m.name} (guest)"


def _money(v) -> str:
    return f"₹{Decimal(v):,.2f}"


def _member_of(group: SplitGroup, user_id: int) -> SplitMember | None:
    """The user's active membership."""
    return next((m for m in group.members if m.user_id == user_id and m.active), None)


def _by_id(group: SplitGroup, member_id: int) -> SplitMember | None:
    return next((m for m in group.members if m.id == member_id), None)


def _load_group(db: Session, group_id: int, user: User) -> tuple[SplitGroup, SplitMember]:
    group = db.get(SplitGroup, group_id)
    me = group and _member_of(group, user.id)
    if not me:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    return group, me


def _require_admin(me: SplitMember):
    if not me.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only group admins can do this")


def _can_edit_bill(group: SplitGroup, bill: SplitBill, user: User) -> bool:
    if bill.created_by == user.id:
        return True
    # Bills of someone who left the group are taken over by the owner
    return group.owner_id == user.id and _member_of(group, bill.created_by) is None


def _notify(db: Session, group: SplitGroup, actor: User, text: str, extra_user_ids: tuple[int, ...] = ()):
    """Tell every active app-user member (and anyone in `extra_user_ids`) except the actor. Guests get nothing."""
    message = f"[{group.name}] {actor.full_name}: {text}"
    if len(message) > 500:
        message = message[:497] + "..."
    users = {m.user_id for m in group.members if m.user_id and m.active} | set(extra_user_ids)
    for uid in users:
        if uid != actor.id:
            db.add(Notification(user_id=uid, kind=KIND_SPLIT, message=message))


def _find_user(db: Session, username: str) -> User:
    u = db.scalar(select(User).where(User.username == username, User.status == STATUS_APPROVED))
    if not u:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No user @{username}. To track someone who isn't on the app, add their name without the @",
        )
    return u


def _add_person(db: Session, group: SplitGroup, entry: str, actor: User) -> SplitMember:
    """Add '@username' (an app user) or a plain name (a guest) to the group, or bring back someone who left."""
    entry = entry.strip()
    if entry.startswith("@"):
        u = _find_user(db, entry[1:].strip())
        existing = next((m for m in group.members if m.user_id == u.id), None)
    else:
        if not has_feature(actor, FEATURE_SPLIT):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Adding people who aren't on the app is part of the split bills plan. Add app users as @username",
            )
        if not entry:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter a name")
        existing = next(
            (m for m in group.members if m.is_guest and m.guest_name.casefold() == entry.casefold()), None
        )
        u = None
    if existing:
        if existing.active:
            raise HTTPException(status.HTTP_409_CONFLICT, f"{_label(existing)} is already in this group")
        existing.left_at, existing.is_admin, existing.added_by = None, False, actor.id
        return existing
    member = SplitMember(user=u, guest_name=None if u else entry, added_by=actor.id)
    group.members.append(member)
    return member


def _bill_out(group: SplitGroup, bill: SplitBill, user: User) -> dict:
    return {
        "id": bill.id,
        "title": bill.title,
        "total_amount": bill.total_amount,
        "bill_date": bill.bill_date,
        "note": bill.note,
        "paid_by": _person(bill.payer),
        "created_by": _brief(bill.creator),
        "created_at": bill.created_at,
        "updated_at": bill.updated_at,
        "can_edit": _can_edit_bill(group, bill, user),
        "shares": [
            {"member": _person(s.member), "share_type": s.share_type, "share_value": s.share_value, "amount": s.amount}
            for s in bill.shares
        ],
    }


def _change_out(group: SplitGroup, change: BillChange, member_id: int) -> dict:
    """A bill's before/after amounts for the add/remove member summaries."""
    rows = []
    for mid in set(change.before) | set(change.after):
        rows.append(
            {
                "member": _person(_by_id(group, mid)),
                "before": change.before.get(mid),
                "after": change.after.get(mid),
                "is_target": mid == member_id,
            }
        )
    rows.sort(key=lambda r: (not r["is_target"], r["member"]["full_name"]))
    return {
        "bill_id": change.bill.id,
        "title": change.bill.title,
        "total_amount": change.bill.total_amount,
        "moved": change.moved,
        "warning": change.warning,
        "rows": rows,
    }


# ---------- groups ----------
@router.get("/groups")
def list_groups(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    groups = db.scalars(
        select(SplitGroup)
        .join(SplitMember)
        .where(SplitMember.user_id == user.id, SplitMember.left_at.is_(None))
        .order_by(SplitGroup.created_at.desc())
    ).all()
    out = []
    for g in groups:
        me = _member_of(g, user.id)
        mine = member_totals(g).get(me.id, {"net": Decimal(0)})
        out.append(
            {
                "id": g.id,
                "name": g.name,
                "description": g.description,
                "owner": _brief(g.owner),
                "member_count": sum(1 for m in g.members if m.active),
                "guest_count": sum(1 for m in g.members if m.active and m.is_guest),
                "bill_count": len(g.bills),
                "total_spent": sum((b.total_amount for b in g.bills), Decimal(0)),
                "my_net": mine["net"],
                "is_admin": me.is_admin,
                "is_owner": g.owner_id == user.id,
                "settled": is_settled(g),
                "created_at": g.created_at,
            }
        )
    return out


@router.post("/groups", status_code=201)
def create_group(data: SplitGroupCreate, user: User = Depends(split_user), db: Session = Depends(get_db)):
    group = SplitGroup(name=data.name.strip(), description=data.description, owner_id=user.id)
    group.members.append(SplitMember(user=user, is_admin=True, added_by=user.id))
    for entry in dict.fromkeys(e.strip() for e in data.members if e.strip()):
        if entry != f"@{user.username}":
            _add_person(db, group, entry, user)
    db.add(group)
    db.flush()
    _notify(db, group, user, "added you to the split group")
    db.commit()
    return {"id": group.id}


@router.get("/groups/{group_id}")
def get_group(group_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group, me = _load_group(db, group_id, user)
    totals = member_totals(group)

    def totals_of(mid):
        return totals.get(mid, {"paid": 0, "share": 0, "sent": 0, "received": 0, "net": 0})

    # Everyone still in the group, then people who left but still appear in bills or payments
    shown = [m for m in group.members if m.active or m.id in totals]
    shown.sort(key=lambda m: (not m.active, m.user_id != group.owner_id, m.is_guest, m.name.casefold()))
    members = [
        {
            "member": _person(m),
            "is_admin": m.is_admin,
            "is_owner": m.user_id == group.owner_id,
            "joined_at": m.joined_at,
            "active": m.active,
            **totals_of(m.id),
        }
        for m in shown
    ]
    people = {m.id: m for m in group.members}
    debts = [
        {"from": _person(people[a]), "to": _person(people[b]), "amount": amt}
        for (a, b), amt in sorted(pair_debts(group).items(), key=lambda kv: -kv[1])
    ]
    bills = sorted(group.bills, key=lambda b: (b.bill_date, b.id), reverse=True)
    settlements = sorted(group.settlements, key=lambda s: (s.settled_on, s.id), reverse=True)
    return {
        "id": group.id,
        "name": group.name,
        "description": group.description,
        "owner": _brief(group.owner),
        "created_at": group.created_at,
        "me": {
            "member_id": me.id,
            "is_admin": me.is_admin,
            "is_owner": group.owner_id == user.id,
            "can_add_guests": has_feature(user, FEATURE_SPLIT),
        },
        "settled": not debts,
        "members": members,
        "debts": debts,
        "bills": [_bill_out(group, b, user) for b in bills],
        "settlements": [
            {
                "id": s.id,
                "from": _person(s.payer),
                "to": _person(s.payee),
                "amount": s.amount,
                "settled_on": s.settled_on,
                "note": s.note,
                "created_by": s.created_by,
                "can_edit": s.created_by == user.id,
                "created_at": s.created_at,
            }
            for s in settlements
        ],
    }


@router.patch("/groups/{group_id}")
def rename_group(group_id: int, data: SplitGroupIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group, me = _load_group(db, group_id, user)
    _require_admin(me)
    old = group.name
    group.name, group.description = data.name.strip(), data.description
    _notify(db, group, user, f"renamed the group from '{old}'" if old != group.name else "updated the group details")
    db.commit()
    return {"id": group.id}


@router.delete("/groups/{group_id}", status_code=204)
def delete_group(group_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group, _ = _load_group(db, group_id, user)
    if group.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person who created the group can delete it")
    debts = pair_debts(group)
    if debts:
        open_total = sum(debts.values())
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Settle up first: {len(debts)} balance(s) totalling {_money(open_total)} are still open in this group",
        )
    _notify(db, group, user, "deleted the group. All balances were settled.")
    db.delete(group)
    db.commit()


# ---------- members ----------
@router.post("/groups/{group_id}/members")
def add_member(group_id: int, data: SplitMemberAdd, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group, me = _load_group(db, group_id, user)
    _require_admin(me)
    new = _add_person(db, group, data.name, user)
    db.flush()

    changes = []
    for bill in group.bills:
        if bill.id in data.include_bill_ids and not any(s.member_id == new.id for s in bill.shares):
            changes.append(add_participant(bill, new.id))
            bill.updated_at = now_local()
    db.flush()
    summary = [_change_out(group, c, new.id) for c in changes]
    if changes:
        what = ", ".join(f"'{c.bill.title}' ({_money(c.moved)})" for c in changes)
        _notify(db, group, user, f"added {_label(new)} to the group and to the bills {what}. Shares were recalculated.")
    else:
        _notify(db, group, user, f"added {_label(new)} to the group")
    db.commit()
    return {"member": _person(new), "bills": summary}


@router.post("/groups/{group_id}/members/{member_id}/remove")
def remove_member(
    group_id: int, member_id: int, data: SplitMemberRemove,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Remove a member or guest (group admins) or leave the group (anyone, for themselves).

    `exclude_bill_ids` are the bills they are taken out of; their amount there is shared by everyone else on the
    bill. With `dry_run` nothing is saved, so the UI can show the summary before confirming.
    """
    group, me = _load_group(db, group_id, user)
    target = _by_id(group, member_id)
    if not target or not target.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That person is not in this group")
    self = target.id == me.id
    if not self:
        _require_admin(me)
    if target.user_id == group.owner_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The group owner cannot be removed")

    changes: list[BillChange] = []
    for bill in group.bills:
        if bill.id not in data.exclude_bill_ids or not any(s.member_id == member_id for s in bill.shares):
            continue
        if bill.paid_by == member_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{target.name} paid '{bill.title}', so they stay on it")
        if len(bill.shares) == 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{target.name} is the only one on '{bill.title}'")
        changes.append(remove_participant(bill, member_id))
        bill.updated_at = now_local()
    target.left_at, target.is_admin = now_local(), False
    db.flush()

    summary = [_change_out(group, c, member_id) for c in changes]
    result = {
        "member": _person(target),
        "bills": summary,
        "moved_total": sum((c.moved for c in changes), Decimal(0)),
        # What they still owe (<0) or are owed (>0) from bills they stay on
        "remaining_net": member_totals(group).get(member_id, {}).get("net", Decimal(0)),
    }
    if data.dry_run:
        db.rollback()
        return result

    what = ""
    if changes:
        what = " Their share is now split between the others: " + ", ".join(f"{_money(c.moved)} on '{c.bill.title}'" for c in changes) + "."
    if self:
        _notify(db, group, user, f"left the group.{what}")
    else:
        extra = (target.user_id,) if target.user_id else ()
        _notify(db, group, user, f"removed {_label(target)} from the group.{what}", extra_user_ids=extra)
    db.commit()
    return result


@router.patch("/groups/{group_id}/members/{member_id}")
def update_member(
    group_id: int, member_id: int, data: SplitMemberUpdate,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    group, me = _load_group(db, group_id, user)
    _require_admin(me)
    target = _by_id(group, member_id)
    if not target or not target.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That person is not in this group")
    if target.is_guest:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Guests aren't on the app, so they can't be admins")
    if target.user_id == group.owner_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The group owner is always an admin")
    target.is_admin = data.is_admin
    verb = "made" if data.is_admin else "removed"
    _notify(db, group, user, f"{verb} {target.name} {'a group admin' if data.is_admin else 'as group admin'}")
    db.commit()
    return {"member_id": member_id, "is_admin": target.is_admin}


# ---------- bills ----------
def _apply_bill(group: SplitGroup, bill: SplitBill, data: SplitBillIn):
    # On an edit, people who already left can stay on the bill; nobody new from outside the group
    allowed = {m.id for m in group.members if m.active} | {s.member_id for s in bill.shares}
    if bill.id:
        allowed.add(bill.paid_by)
    ids = [s.member_id for s in data.shares]
    if len(set(ids)) != len(ids):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Each person can appear only once on a bill")
    if not set(ids) <= allowed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Everyone on the bill must be a member of the group")
    if data.paid_by not in allowed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The person who paid must be a member of the group")
    for s in data.shares:
        if s.share_type != SHARE_EQUAL and s.share_value is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter an amount or percentage for every fixed share")
    amounts = allocate(data.total_amount, [(s.share_type, s.share_value) for s in data.shares])

    bill.title, bill.total_amount, bill.bill_date = data.title.strip(), data.total_amount, data.bill_date
    bill.paid_by, bill.note = data.paid_by, data.note
    bill.shares.clear()
    session = object_session(bill)
    if session is not None and bill.id is not None:
        # Delete the old shares before adding new ones for the same people: (bill, member) is unique
        session.flush()
    for s, amount in zip(data.shares, amounts):
        value = None if s.share_type == SHARE_EQUAL else s.share_value
        bill.shares.append(SplitShare(member_id=s.member_id, share_type=s.share_type, share_value=value, amount=amount))


def _check_date(data: SplitBillIn):
    if data.bill_date > today_local():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The bill date cannot be in the future")


@router.post("/groups/{group_id}/bills", status_code=201)
def add_bill(group_id: int, data: SplitBillIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group, me = _load_group(db, group_id, user)
    _check_date(data)
    bill = SplitBill(group_id=group.id, created_by=user.id, title="", total_amount=0, bill_date=data.bill_date, paid_by=me.id)
    _apply_bill(group, bill, data)
    group.bills.append(bill)
    db.flush()
    payer = _by_id(group, bill.paid_by)
    _notify(db, group, user, f"added the bill '{bill.title}' of {_money(bill.total_amount)}, paid by {payer.name}")
    db.commit()
    db.refresh(bill)
    return _bill_out(group, bill, user)


def _load_bill(db: Session, bill_id: int, user: User) -> tuple[SplitGroup, SplitBill]:
    bill = db.get(SplitBill, bill_id)
    if not bill:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bill not found")
    group, _ = _load_group(db, bill.group_id, user)
    if not _can_edit_bill(group, bill, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Only {bill.creator.full_name}, who added this bill, can change it")
    return group, bill


@router.put("/bills/{bill_id}")
def update_bill(bill_id: int, data: SplitBillIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group, bill = _load_bill(db, bill_id, user)
    _check_date(data)
    old_total, old_title = bill.total_amount, bill.title
    _apply_bill(group, bill, data)
    bill.updated_at = now_local()
    db.flush()
    change = f" (total {_money(old_total)} → {_money(bill.total_amount)})" if old_total != bill.total_amount else ""
    _notify(db, group, user, f"changed the bill '{old_title}'{change}. Check your share.")
    db.commit()
    db.refresh(bill)
    return _bill_out(group, bill, user)


@router.delete("/bills/{bill_id}", status_code=204)
def delete_bill(bill_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group, bill = _load_bill(db, bill_id, user)
    _notify(db, group, user, f"deleted the bill '{bill.title}' of {_money(bill.total_amount)}")
    group.bills.remove(bill)
    db.commit()


# ---------- settlements ----------
@router.post("/groups/{group_id}/settlements", status_code=201)
def add_settlement(
    group_id: int, data: SplitSettlementIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    group, me = _load_group(db, group_id, user)
    payer, payee = _by_id(group, data.from_member_id), _by_id(group, data.to_member_id)
    # The other person may have left the group but still have a balance here
    involved = {m.id for m in group.members if m.active} | set(member_totals(group))
    if not payer or not payee or not {payer.id, payee.id} <= involved:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Both people must be part of this group")
    if payer.id == payee.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose two different people")
    # Guests can't record their own payments, so whoever added them (or a group admin) does it for them
    for_guest = any(m.is_guest and (me.is_admin or m.added_by == user.id) for m in (payer, payee))
    if me.id not in (payer.id, payee.id) and not for_guest:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "You can only record a payment that you made or received, or one for a guest you added",
        )
    if data.settled_on > today_local():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The payment date cannot be in the future")
    st = SplitSettlement(
        group_id=group.id, from_member=payer.id, to_member=payee.id, amount=data.amount,
        settled_on=data.settled_on, note=data.note, created_by=user.id,
    )
    group.settlements.append(st)
    _notify(
        db, group, user, f"recorded that {payer.name} paid {payee.name} {_money(data.amount)}",
        extra_user_ids=tuple(m.user_id for m in (payer, payee) if m.user_id),
    )
    db.commit()
    return {"id": st.id}


@router.delete("/settlements/{settlement_id}", status_code=204)
def delete_settlement(settlement_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    st = db.get(SplitSettlement, settlement_id)
    if not st:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    group, _ = _load_group(db, st.group_id, user)
    if st.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person who recorded this payment can delete it")
    extra = tuple(m.user_id for m in (st.payer, st.payee) if m.user_id)
    _notify(db, group, user, f"deleted a recorded payment of {_money(st.amount)}", extra_user_ids=extra)
    group.settlements.remove(st)
    db.commit()
