"""Split bill maths: dividing a bill between participants, redistributing shares, and group balances.

A bill is split in three kinds of shares:
  AMOUNT  a fixed amount
  PERCENT a fixed percentage of the total
  EQUAL   an equal part of whatever the fixed shares leave over
Amounts are rounded to paise so that the shares always add up to exactly the bill total.
Everyone is identified by their split_members id, so app users and guests are treated the same.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from fastapi import HTTPException, status

from ..models import SHARE_AMOUNT, SHARE_EQUAL, SHARE_PERCENT, SplitBill, SplitGroup, SplitShare

CENT = Decimal("0.01")
HUNDRED = Decimal(100)
# Balances below this are treated as settled
TOLERANCE = Decimal("0.009")
PERCENT_SLACK = Decimal("0.000001")


def _bad(msg: str):
    raise HTTPException(status.HTTP_400_BAD_REQUEST, msg)


def allocate(total: Decimal, shares: list[tuple[str, Decimal | None]]) -> list[Decimal]:
    """What each (share_type, share_value) owes of `total`. Raises 400 if the shares don't fit the total."""
    if not shares:
        _bad("A bill needs at least one participant")
    raw: list[Decimal | None] = []
    fixed = Decimal(0)
    percent = Decimal(0)
    for kind, value in shares:
        if kind == SHARE_EQUAL:
            raw.append(None)
            continue
        if value is None or value < 0:
            _bad("Fixed amounts and percentages cannot be negative")
        if kind == SHARE_PERCENT:
            percent += value
            amount = total * value / HUNDRED
        elif kind == SHARE_AMOUNT:
            amount = value
        else:
            _bad(f"Unknown share type {kind}")
        raw.append(amount)
        fixed += amount
    if percent > HUNDRED:
        _bad(f"Percentages add up to {percent.normalize()}%, more than 100%")
    # Percentages are stored to 4 decimals, so allow the tiny rounding that causes on large bills
    slack = TOLERANCE + total * PERCENT_SLACK * sum(1 for k, _ in shares if k == SHARE_PERCENT)
    remainder = total - fixed
    equal_count = sum(1 for r in raw if r is None)
    if remainder < -slack:
        _bad(f"Fixed shares add up to {fixed.quantize(CENT)}, more than the bill total {total}")
    if equal_count == 0 and abs(remainder) > slack:
        _bad(
            f"Shares add up to {fixed.quantize(CENT)} but the bill is {total}. "
            f"Give {remainder.quantize(CENT)} to someone, or let someone pay an equal share of the rest"
        )
    if equal_count:
        each = max(remainder, Decimal(0)) / equal_count
        exact = [each if r is None else r for r in raw]
        if remainder < 0:  # within slack: trim the fixed shares to fit
            exact = [e * total / fixed for e in exact]
    else:
        exact = [r * total / fixed for r in raw] if fixed else [Decimal(0)] * len(raw)

    # Largest-remainder rounding to paise, so the rounded shares add up to the total exactly
    floors = [e.quantize(CENT, rounding=ROUND_FLOOR) for e in exact]
    leftover = int(((total - sum(floors)) / CENT).to_integral_value())
    order = sorted(range(len(exact)), key=lambda i: exact[i] - floors[i], reverse=True)
    for i in order[: max(leftover, 0)]:
        floors[i] += CENT
    return floors


def split_evenly(amount: Decimal, n: int) -> list[Decimal]:
    """`amount` in n parts that differ by at most one paisa and add up exactly."""
    base = (amount / n).quantize(CENT, rounding=ROUND_FLOOR)
    extra = int(((amount - base * n) / CENT).to_integral_value())
    return [base + (CENT if i < extra else 0) for i in range(n)]


def recalculate(bill: SplitBill) -> None:
    amounts = allocate(bill.total_amount, [(s.share_type, s.share_value) for s in bill.shares])
    for share, amount in zip(bill.shares, amounts):
        share.amount = amount


def _amounts(bill: SplitBill) -> dict[int, Decimal]:
    return {s.member_id: Decimal(s.amount) for s in bill.shares}


@dataclass
class BillChange:
    """How a bill changed when someone was added or removed: the per-person amounts before and after."""

    bill: SplitBill
    before: dict[int, Decimal]
    after: dict[int, Decimal]
    moved: Decimal  # amount the removed person no longer pays / the added person now pays
    warning: str | None = None


def remove_participant(bill: SplitBill, member_id: int) -> BillChange:
    """Take `member_id` out of the bill and spread their amount over the people left.

    If anyone pays an equal share, the freed amount simply joins the equal part. Otherwise it is added to every
    remaining participant in equal parts (as extra percentage or extra fixed amount), so nothing is left unpaid.
    """
    before = _amounts(bill)
    share = next(s for s in bill.shares if s.member_id == member_id)
    freed = Decimal(share.amount)
    bill.shares.remove(share)
    rest = bill.shares
    if not any(s.share_type == SHARE_EQUAL for s in rest):
        for s, part in zip(rest, split_evenly(freed, len(rest))):
            if s.share_type == SHARE_PERCENT:
                s.share_value = (Decimal(s.share_value) + part / bill.total_amount * HUNDRED).quantize(Decimal("0.0001"))
            else:
                s.share_value = Decimal(s.share_value) + part
    recalculate(bill)
    return BillChange(bill, before, _amounts(bill), freed)


def add_participant(bill: SplitBill, member_id: int) -> BillChange:
    """Add `member_id` with an equal share. Fixed amounts and percentages of the others are kept as they are."""
    before = _amounts(bill)
    had_equal = any(s.share_type == SHARE_EQUAL for s in bill.shares)
    bill.shares.append(SplitShare(member_id=member_id, share_type=SHARE_EQUAL, share_value=None, amount=0))
    recalculate(bill)
    after = _amounts(bill)
    warning = None
    if not had_equal:
        warning = "Everyone else pays a fixed amount or percentage, so nothing was left to share. Edit the bill to give them a share."
    return BillChange(bill, before, after, after[member_id], warning)


# ---------- balances ----------
def pair_debts(group: SplitGroup) -> dict[tuple[int, int], Decimal]:
    """Net debts as {(debtor, creditor): amount > 0}, after settlements."""
    owe: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    for bill in group.bills:
        for s in bill.shares:
            if s.member_id != bill.paid_by:
                owe[(s.member_id, bill.paid_by)] += Decimal(s.amount)
    for st in group.settlements:
        owe[(st.from_member, st.to_member)] -= Decimal(st.amount)
    net: dict[tuple[int, int], Decimal] = {}
    for a, b in {tuple(sorted(k)) for k in owe}:
        diff = owe.get((a, b), Decimal(0)) - owe.get((b, a), Decimal(0))
        if diff > TOLERANCE:
            net[(a, b)] = diff
        elif diff < -TOLERANCE:
            net[(b, a)] = -diff
    return net


def member_totals(group: SplitGroup) -> dict[int, dict[str, Decimal]]:
    """Per member id: bills paid, own share of bills, settlements sent and received, and the net position.

    net > 0 means the others owe this person money; net < 0 means they owe the others.
    """
    t: dict[int, dict[str, Decimal]] = defaultdict(
        lambda: {"paid": Decimal(0), "share": Decimal(0), "sent": Decimal(0), "received": Decimal(0)}
    )
    for bill in group.bills:
        t[bill.paid_by]["paid"] += Decimal(bill.total_amount)
        for s in bill.shares:
            t[s.member_id]["share"] += Decimal(s.amount)
    for st in group.settlements:
        t[st.from_member]["sent"] += Decimal(st.amount)
        t[st.to_member]["received"] += Decimal(st.amount)
    for v in t.values():
        v["net"] = v["paid"] - v["share"] + v["sent"] - v["received"]
    return t


def is_settled(group: SplitGroup) -> bool:
    return not pair_debts(group)
