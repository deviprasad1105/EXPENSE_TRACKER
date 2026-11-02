"""Find subscriptions the user already pays for, so they don't have to type them in.

GPay, PhonePe and Paytm don't offer an API for reading someone's UPI AutoPay mandates, so the two sources are:

- Bank / UPI SMS (or email alerts) the user pastes in: mandate set-up messages ("UPI AutoPay mandate created for
  Netflix, Rs 649, Monthly") and repeated debits to the same merchant.
- The user's own expense history: the same item at a regular interval for a similar amount.

Both only return suggestions; nothing is saved until the user picks which ones to add.
"""

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    FREQ_CUSTOM,
    FREQ_HALF_YEARLY,
    FREQ_MONTHLY,
    FREQ_QUARTERLY,
    FREQ_WEEKLY,
    FREQ_YEARLY,
    SUB_SOURCE_DETECTED,
    SUB_SOURCE_SMS,
    Expense,
    Subscription,
)
from .subscriptions import STEP

MAX_TEXT = 50_000

# UPI handle (the part after @) → app
UPI_HANDLES = {
    "GPAY": ("okaxis", "okhdfcbank", "okicici", "oksbi"),
    "PHONEPE": ("ybl", "ibl", "axl"),
    "PAYTM": ("paytm", "ptys", "ptyes", "pthdfc", "ptaxis", "ptsbi"),
    "AMAZONPAY": ("apl", "yapl", "rapl"),
    "BHIM": ("upi",),
}
APP_WORDS = [
    ("GPAY", r"google\s*pay|\bgpay\b"),
    ("PHONEPE", r"phone\s*pe"),
    ("PAYTM", r"paytm"),
    ("AMAZONPAY", r"amazon\s*pay"),
    ("CRED", r"\bcred\b"),
    ("BHIM", r"\bbhim\b"),
]
RECURRING_WORDS = re.compile(
    r"mandate|auto\s*-?\s*pay|recurring|subscription|standing\s+instruction|\bsi\b|e-?mandate|\bnach\b|renew",
    re.I,
)
DEBIT_WORDS = re.compile(r"debited|deducted|paid|spent|charged|sent|payment\s+of|txn|transaction", re.I)
FREQ_WORDS = [
    (FREQ_WEEKLY, r"weekly|every\s+week"),
    (FREQ_QUARTERLY, r"quarterly|every\s+3\s+months"),
    (FREQ_HALF_YEARLY, r"half[\s-]*yearly|semi[\s-]*annual|every\s+6\s+months"),
    (FREQ_YEARLY, r"yearly|annual(?:ly)?|every\s+year|per\s+annum"),
    (FREQ_MONTHLY, r"monthly|every\s+month|per\s+month|\bpm\b"),
]
AMOUNT = re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)", re.I)
# Words that end a merchant name in "... towards NETFLIX via PhonePe ..."
_STOP = (
    r"via|on|of|has|have|is|was|been|frequency|for|with|from|using|upi|ref|scheduled|successfully|rs|inr|by|will|"
    r"a/c|ac|account|avl|available|bal|balance|and|at|the|start|starting|valid|till|until|umn|mandate|id|txn"
)
MERCHANT = re.compile(
    r"\b(?:towards|to|for|of|at|by)\s+"
    r"(?:(?:your|the|a)\s+)?(?:(?:upi\s+)?(?:auto\s*-?\s*pay|mandate|e-?mandate|subscription|si)\s+(?:of|to|for|towards)?\s*)?"
    rf"([A-Za-z][A-Za-z0-9&'.\- ]{{1,40}}?)(?=\s+(?:{_STOP})\b|\s*[.,;:()\n]|\s*-\s|\s*$)",
    re.I,
)
NOT_MERCHANT = re.compile(
    r"^(?:rs|inr|your|you|a/?c|ac|account|upi|mandate|auto ?pay|autopay|amount|the|this|payment|recurring|"
    r"transaction|txn|card|bank|debit|credit|customer|dear|frequency|monthly|yearly|weekly|quarterly|rs\.?)\b",
    re.I,
)
REF = re.compile(r"(?:umn|upi\s*ref(?:\s*no)?|ref(?:erence)?\s*(?:no|id)?|mandate\s*(?:id|ref)|txn\s*id)[\s.:#-]*([A-Za-z0-9@.\-]{6,40})", re.I)
VPA = re.compile(r"\b[\w.\-]{2,}@([a-z]{2,})\b", re.I)

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
DATE_PATTERNS = [
    # 05-10-2026, 05/10/26
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b"), "dmy"),
    # 2026-10-05
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    # 05-Oct-26, 5 Oct 2026, 05Oct26
    (re.compile(r"\b(\d{1,2})[\s-]?([A-Za-z]{3})[a-z]*[\s,-]*'?(\d{2,4})\b"), "dMy"),
]


def _year(y: str) -> int:
    n = int(y)
    return n + 2000 if n < 100 else n


def find_date(text: str) -> date | None:
    for pattern, kind in DATE_PATTERNS:
        for m in pattern.finditer(text):
            try:
                if kind == "dmy":
                    return date(_year(m[3]), int(m[2]), int(m[1]))
                if kind == "ymd":
                    return date(int(m[1]), int(m[2]), int(m[3]))
                month = _MONTHS.get(m[2][:3].lower())
                if month:
                    return date(_year(m[3]), month, int(m[1]))
            except ValueError:
                continue
    return None


def find_amount(text: str) -> Decimal | None:
    for m in AMOUNT.finditer(text):
        try:
            value = Decimal(m[1].replace(",", ""))
        except InvalidOperation:
            continue
        if value > 0:
            return value
    return None


def find_app(text: str) -> str | None:
    for app, pattern in APP_WORDS:
        if re.search(pattern, text, re.I):
            return app
    for m in VPA.finditer(text):
        handle = m[1].lower()
        for app, handles in UPI_HANDLES.items():
            if handle in handles:
                return app
    return "UPI" if re.search(r"\bupi\b", text, re.I) else None


def find_merchant(text: str) -> str | None:
    for m in MERCHANT.finditer(text):
        name = re.sub(r"\s+", " ", m[1]).strip(" .-'")
        if len(name) >= 2 and not NOT_MERCHANT.match(name) and not re.fullmatch(r"[xX*\d]+", name):
            return name.title() if name.isupper() or name.islower() else name
    # "Sent Rs 250 to swiggy@ybl": name the merchant after its UPI ID
    for m in VPA.finditer(text):
        local = m[0].split("@")[0]
        if not re.fullmatch(r"[\d.\-_]+", local):
            return re.sub(r"[._\-]+", " ", local).strip().title()
    return None


def find_frequency(text: str) -> str | None:
    for freq, pattern in FREQ_WORDS:
        if re.search(pattern, text, re.I):
            return freq
    return None


def split_messages(text: str) -> list[str]:
    """One message per blank-line-separated block; a block of single lines that each look like an SMS is split too."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text[:MAX_TEXT]) if b.strip()]
    messages = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) > 1 and all(AMOUNT.search(ln) for ln in lines):
            messages.extend(lines)
        else:
            messages.append(" ".join(lines))
    return messages


def parse_message(text: str) -> dict | None:
    """What one bank/UPI message says about a payment, or None if it isn't about a payment to someone."""
    amount = find_amount(text)
    merchant = find_merchant(text)
    if not amount or not merchant:
        return None
    recurring = bool(RECURRING_WORDS.search(text))
    if not recurring and not DEBIT_WORDS.search(text):
        return None
    ref = REF.search(text)
    return {
        "merchant": merchant,
        "amount": amount,
        "date": find_date(text),
        "frequency": find_frequency(text),
        "payment_method": find_app(text),
        "payment_ref": ref[1] if ref else None,
        "recurring": recurring,
        "text": text[:300],
    }


def frequency_from_gaps(dates: list[date]) -> tuple[str, int, str] | None:
    """(frequency, interval_count, interval_unit) when the dates are evenly spaced, else None."""
    days = sorted(set(dates))
    if len(days) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    mid = median(gaps)
    for freq, low, high, count, unit in (
        (FREQ_WEEKLY, 6, 8, 1, "WEEK"),
        (FREQ_MONTHLY, 26, 35, 1, "MONTH"),
        (FREQ_QUARTERLY, 84, 98, 3, "MONTH"),
        (FREQ_HALF_YEARLY, 174, 190, 6, "MONTH"),
        (FREQ_YEARLY, 350, 380, 1, "YEAR"),
    ):
        if low <= mid <= high and all(low <= g <= high for g in gaps):
            return freq, count, unit
    if mid >= 10 and all(abs(g - mid) <= max(2, mid * 0.1) for g in gaps):
        return FREQ_CUSTOM, int(round(mid)), "DAY"
    return None


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _tracked_keys(db: Session, user_id: int) -> set[str]:
    rows = db.execute(select(Subscription.name, Subscription.merchant).where(Subscription.user_id == user_id)).all()
    return {_key(v) for row in rows for v in row if v}


def _suggestion(name, amount, frequency, start, source, tracked, *, count=1, unit="MONTH", merchant=None,
                payment_method=None, payment_ref=None, evidence=None, auto_log=True, confidence="medium"):
    return {
        "name": name[:100],
        "merchant": (merchant or name)[:100],
        "amount": amount,
        "frequency": frequency or FREQ_MONTHLY,
        "interval_count": count,
        "interval_unit": unit,
        "start_date": start,
        "payment_method": payment_method,
        "payment_ref": payment_ref,
        "source": source,
        "auto_log_expense": auto_log,
        "confidence": confidence,
        "already_tracked": _key(name) in tracked or _key(merchant or "") in tracked,
        "evidence": evidence or [],
    }


def suggestions_from_sms(db: Session, user_id: int, text: str, today: date) -> list[dict]:
    """Subscriptions found in pasted bank / UPI messages, one per merchant."""
    by_merchant: dict[str, list[dict]] = defaultdict(list)
    for message in split_messages(text):
        parsed = parse_message(message)
        if parsed:
            by_merchant[_key(parsed["merchant"])].append(parsed)

    tracked = _tracked_keys(db, user_id)
    result = []
    for items in by_merchant.values():
        dated = sorted((p for p in items if p["date"]), key=lambda p: p["date"])
        latest = dated[-1] if dated else items[-1]
        explicit = next((p["frequency"] for p in items if p["frequency"]), None)
        inferred = frequency_from_gaps([p["date"] for p in dated])
        recurring = any(p["recurring"] for p in items)
        # A one-off debit with no mandate/AutoPay wording isn't a subscription
        if not recurring and not inferred:
            continue
        count, unit = 1, "MONTH"
        frequency = explicit
        if not frequency and inferred:
            frequency, count, unit = inferred
        elif frequency:
            count, unit = STEP[frequency]
        start = dated[0]["date"] if dated else today
        result.append(
            _suggestion(
                latest["merchant"], latest["amount"], frequency, start, SUB_SOURCE_SMS, tracked,
                count=count, unit=unit,
                payment_method=next((p["payment_method"] for p in reversed(items) if p["payment_method"]), None),
                payment_ref=next((p["payment_ref"] for p in reversed(items) if p["payment_ref"]), None),
                evidence=[p["text"] for p in items][:5],
                confidence="high" if recurring and (explicit or inferred) else "medium",
            )
        )
    return sorted(result, key=lambda s: (s["already_tracked"], s["name"].lower()))


def suggestions_from_expenses(db: Session, user_id: int, today: date) -> list[dict]:
    """Items in the expense history paid at a regular interval for a similar amount (rent, gym, recharge...)."""
    since = today - timedelta(days=400)
    rows = db.execute(
        select(func.lower(Expense.item_name), Expense.item_name, Expense.expense_date, Expense.amount, Expense.category)
        .where(Expense.user_id == user_id, Expense.expense_date >= since)
        .order_by(Expense.expense_date)
    ).all()
    groups: dict[str, list] = defaultdict(list)
    for key, name, day, amount, category in rows:
        groups[key].append((name, day, amount, category))

    tracked = _tracked_keys(db, user_id)
    result = []
    for entries in groups.values():
        days = sorted({d for _, d, _, _ in entries})
        found = frequency_from_gaps(days)
        if not found:
            continue
        frequency, count, unit = found
        # Weekly and monthly need 3 payments to be convincing; longer cycles 2
        if len(days) < (3 if frequency in (FREQ_WEEKLY, FREQ_MONTHLY, FREQ_CUSTOM) else 2):
            continue
        amounts = [Decimal(a) for _, _, a, _ in entries]
        if min(amounts) <= 0 or max(amounts) / min(amounts) > Decimal("1.25"):
            continue
        name, _, last_amount, category = entries[-1]
        suggestion = _suggestion(
            name, Decimal(last_amount), frequency, days[0], SUB_SOURCE_DETECTED, tracked, count=count, unit=unit,
            evidence=[f"{d:%d %b %Y}: ₹{a}" for _, d, a, _ in entries[-6:]],
            # They already log it by hand, so don't log it a second time
            auto_log=False,
            confidence="high" if len(days) >= 4 else "medium",
        )
        suggestion["category"] = category
        result.append(suggestion)
    return sorted(result, key=lambda s: (s["already_tracked"], s["name"].lower()))
