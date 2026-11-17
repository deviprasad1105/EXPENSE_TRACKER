"""Auto-analyse a row that needs action: work its missing or wrong values out from the rest of the file.

What can be worked out, strongest evidence first:
- the amount and the type from the running balance (the balance moved from the older row's to this one's);
- the type from the description ("SALARY", "REFUND", "NEFT CR"… is money in; anything else money out);
- a date written in another order (05/13/2026 is 13 May), a future date with day and month swapped, or a missing
  date from the rows around it;
- a negative amount (a refund: money in), more than two decimals (rounded), a split bill without a share (an
  equal share with the people named, else the whole amount as an expense), a transfer missing an account (an
  expense or money in for the account it names).

Each fix comes with the reason, so the user sees why before accepting it. A row nothing can be worked out for is
reported as such.
"""

import re
from datetime import date
from decimal import Decimal

from .models import KIND_EXPENSE, KIND_INCOME, KIND_OPENING, KIND_SPLIT, KIND_TRANSFER
from .parse import CENT, parse_date
from .schemas import RecordIn

_INCOME_WORDS = re.compile(
    r"\b(salary|refund|cashback|cash back|reversal|reversed|interest|dividend|credited|credit|received|deposit|"
    r"neft\s*cr|imps\s*cr|upi\s*cr|by\s+transfer|inward)\b|\bcr\b",
    re.I,
)
_RAW_DATE = re.compile(r"“(.+?)”")


def _fmt(v: Decimal) -> str:
    return f"₹{v:,.2f}"


def _order(records: list[RecordIn]) -> int:
    """+1 when the file is oldest first, -1 when newest first, judged by the rows whose balance follows."""
    fwd = back = 0
    for a, b in zip(records, records[1:]):
        if a.balance is None or b.balance is None:
            continue
        for r, sign in ((b, 1), (a, -1)):
            if r.amount is None or r.kind not in (KIND_EXPENSE, KIND_INCOME, KIND_SPLIT):
                continue
            delta = r.amount if r.kind == KIND_INCOME else -r.amount
            if sign == 1 and b.balance - a.balance == delta:
                fwd += 1
            if sign == -1 and a.balance - b.balance == delta:
                back += 1
    return -1 if back > fwd else 1


def _older_balance(records: list[RecordIn], i: int, order: int) -> tuple[Decimal | None, int | None]:
    """The balance just before row i happened, and the row it's on (only the adjacent row counts)."""
    j = i - order
    if 0 <= j < len(records) and records[j].balance is not None:
        return records[j].balance, records[j].row_no
    return None, None


def _neighbour_date(records: list[RecordIn], i: int) -> tuple[date | None, str]:
    before = next((r for r in reversed(records[:i]) if r.date), None)
    after = next((r for r in records[i + 1:] if r.date), None)
    if before and after and before.date == after.date:
        return before.date, f"the rows around it (rows {before.row_no} and {after.row_no}) are both dated {before.date:%d %b %Y}"
    if before:
        return before.date, f"taken from row {before.row_no}, the row before it"
    if after:
        return after.date, f"taken from row {after.row_no}, the row after it"
    return None, ""


def analyse_one(records: list[RecordIn], i: int, today: date, order: int) -> dict:
    r = records[i]
    patch: dict = {}
    reasons: list[str] = []
    kind, amount = r.kind, r.amount

    # ----- date -----
    if r.date is None:
        raw = next((m.group(1) for e in r.parse_errors if (m := _RAW_DATE.search(e)) and "date" in e.lower()), None)
        found = None
        if raw:
            for o in ("DMY", "MDY", "YMD"):
                d = parse_date(raw, o)
                if d and d <= today:
                    found = d
                    reasons.append(f"Date “{raw}” read as {d:%d %b %Y} (written {o[0]}-{o[1]}-{o[2]})")
                    break
        if not found:
            found, why = _neighbour_date(records, i)
            if found:
                reasons.append(f"Date {found:%d %b %Y}: {why}")
        if found:
            patch["date"] = found.isoformat()
    elif r.date > today and r.date.day <= 12:
        try:
            swapped = r.date.replace(day=r.date.month, month=r.date.day)
        except ValueError:
            swapped = None
        if swapped and swapped <= today:
            patch["date"] = swapped.isoformat()
            reasons.append(f"Date was in the future: day and month swapped to {swapped:%d %b %Y}")

    # ----- amount and type -----
    if kind != KIND_OPENING and (amount is None or kind is None):
        older, older_row = _older_balance(records, i, order)
        if r.balance is not None and older is not None and r.balance != older:
            delta = r.balance - older
            kind, amount = (KIND_INCOME, delta) if delta > 0 else (KIND_EXPENSE, -delta)
            reasons.append(f"{'Money in' if kind == KIND_INCOME else 'Money out'} of {_fmt(amount)}: the balance went from "
                           f"{_fmt(older)} (row {older_row}) to {_fmt(r.balance)}")
        elif amount is not None and kind is None:
            kind = KIND_INCOME if _INCOME_WORDS.search(r.description or "") else KIND_EXPENSE
            reasons.append(("Money in" if kind == KIND_INCOME else "Money out")
                           + (": the description says so" if kind == KIND_INCOME else ": nothing in the description says it was received"))
    if amount is not None and amount < 0 and kind != KIND_OPENING:
        amount = -amount
        if kind == KIND_EXPENSE:
            kind = KIND_INCOME
            reasons.append(f"A negative expense is money back (a refund): {_fmt(amount)} in")
        else:
            reasons.append("Amount made positive")
    if amount is not None and amount != amount.quantize(CENT):
        amount = amount.quantize(CENT)
        reasons.append(f"Amount rounded to {_fmt(amount)}")

    # ----- splits and transfers -----
    if kind == KIND_SPLIT and r.my_share is None and amount:
        if r.split_with:
            share = (amount / (len(r.split_with) + 1)).quantize(CENT)
            patch["my_share"] = str(share)
            reasons.append(f"Your share: an equal part with {', '.join(r.split_with)} ({_fmt(share)})")
        else:
            kind = KIND_EXPENSE
            reasons.append("No one to split with: imported as your own expense")
    if kind == KIND_TRANSFER and not (r.account and r.to_account):
        if r.account or r.to_account:
            kind = KIND_EXPENSE if r.account else KIND_INCOME
            if not r.account:
                patch["account"] = r.to_account
            patch["to_account"] = None
            reasons.append("Only one account is named: imported as " + ("money out of it" if kind == KIND_EXPENSE else "money into it"))

    if kind != r.kind:
        patch["kind"] = kind
    if amount != r.amount:
        patch["amount"] = str(amount) if amount is not None else None
    solved = bool(reasons) and (patch.get("date") or r.date) is not None and amount is not None and amount != 0 \
        and kind is not None
    if reasons:
        patch["parse_errors"] = []
        patch["parse_warnings"] = [w for w in r.parse_warnings if not w.startswith("Auto-analysed")] + \
            [f"Auto-analysed: {x}" for x in reasons]
    return {"row_no": r.row_no, "patch": patch, "reasons": reasons, "solved": solved,
            "message": None if reasons else "Nothing in the file tells what this row should be: fix it by hand or leave it out"}


def analyse(records: list[RecordIn], row_nos: list[int], today: date) -> list[dict]:
    order = _order(records)
    index = {r.row_no: i for i, r in enumerate(records)}
    return [analyse_one(records, index[n], today, order) for n in row_nos if n in index]
