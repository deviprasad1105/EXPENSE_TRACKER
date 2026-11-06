"""UPI tracker: turn a bank / UPI app message about a UPI payment into an expense or money received.

No bank or UPI app lets another app read a user's payments directly, so the user's phone forwards the alert it already
gets (an SMS-forwarding app, or an iOS Shortcuts automation) to their private link, or they paste it on the UPI tracker
page. The message is read for the amount, paid or received, the other party (UPI ID and name), the UPI reference and
the date. The same reference is recorded once, so forwarding a message twice does no harm.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..accounting.engine import get_books, is_locked
from ..deps import FEATURE_UPI, has_feature
from ..models import (
    UPI_CREATED, UPI_DUPLICATE, UPI_FAILED, UPI_IGNORED, Account, Expense, Fund, UpiEvent, UpiId, User,
)
from ..services.accounts import resolve_account
from ..services.budgets import check_budgets
from ..timeutil import now_local, today_local
from .aliases import UPI_PATH_RE, VPA_RE, find_alias, name_record, payee_key, user_aliases

DEBIT = "DEBIT"
CREDIT = "CREDIT"
MAX_TEXT = 1000

_AMOUNT = re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_AMOUNT_VERB = re.compile(r"(?:debited|credited|sent|paid|received)\s+(?:by|for|with|of)?\s*([\d,]+(?:\.\d{1,2})?)\b", re.I)
_DEBIT = re.compile(r"\b(debited|debit|sent|paid|spent|withdrawn|transferred to|payment of)\b", re.I)
_CREDIT = re.compile(r"\b(credited|credit|received|deposited|added to)\b", re.I)
_REF = re.compile(
    r"(?:ref(?:erence)?\.?\s*(?:no|num|number|id)?\.?|refno|rrn|utr|upi\s*(?:ref|txn|transaction)?\s*(?:no|id)?\.?|txn\s*(?:id|no)?\.?)"
    r"[\s:#.-]*(\d{8,16})", re.I)
_LAST4 = re.compile(r"(?:a/?c|acct|account|card)\s*(?:no\.?)?\s*[*xX.\s]*(\d{3,4})\b", re.I)
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_DATE_NUM = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b")
_DATE_MON = re.compile(r"\b(\d{1,2})[-\s]?([A-Za-z]{3})[a-z]*[-\s,]*(\d{2,4})\b")
_NAME_END = r"(?=\s+(?:on|ref|refno|upi|via|using|at|dated|avl|bal|not|if)\b|\s*[.;:(\n]|\s*$)"
_TO = re.compile(r"\b(?:to|trf to|transfer to|paid to)\s+(?:vpa\s+)?([A-Za-z][\w .&'@-]{1,60}?)" + _NAME_END, re.I)
_FROM = re.compile(r"\b(?:from|transfer from|received from)\s+(?:vpa\s+)?([A-Za-z][\w .&'@-]{1,60}?)" + _NAME_END, re.I)
_SEMI_CREDITED = re.compile(r";\s*([A-Za-z][\w .&'-]{1,60}?)\s+credited", re.I)
# "from HDFC Bank A/c", "to your a/c": the user's own bank, not the other party
_NOT_A_PARTY = re.compile(r"^(?:your|a/?c|ac|acct|account|card|bank|\w+ bank\b|(?:rs|inr)\b)", re.I)


@dataclass
class ParsedUpi:
    direction: str
    amount: Decimal
    vpa: str | None
    name: str | None
    ref: str | None
    txn_date: date
    last4: str | None

    @property
    def counterparty(self) -> str:
        return (self.name or self.vpa or "UPI payment")[:120]

    @property
    def description(self) -> str:
        """What the record is called before any alias: the name, with the UPI ID when there is one."""
        word = "UPI to" if self.direction == DEBIT else "UPI from"
        parts = [word, self.name or "", f"({self.vpa})" if self.vpa and self.vpa != self.name else ""]
        return " ".join(p for p in parts if p)[:200]


def _amount(text: str) -> Decimal | None:
    for rx in (_AMOUNT, _AMOUNT_VERB):
        for m in rx.finditer(text):
            try:
                value = Decimal(m.group(1).replace(",", ""))
            except InvalidOperation:
                continue
            if value > 0:
                return value
    return None


def _date(text: str) -> date | None:
    for m in _DATE_NUM.finditer(text):
        d, mo, y = (int(x) for x in m.groups())
        try:
            return date(y + 2000 if y < 100 else y, mo, d)
        except ValueError:
            continue
    for m in _DATE_MON.finditer(text):
        mo = _MONTHS.get(m.group(2).lower())
        if not mo:
            continue
        y = int(m.group(3))
        try:
            return date(y + 2000 if y < 100 else y, mo, int(m.group(1)))
        except ValueError:
            continue
    return None


def _clean_name(name: str | None) -> str | None:
    if not name:
        return None
    name = re.sub(r"\s+", " ", name).strip(" .-'")
    if not name or _NOT_A_PARTY.match(name) or VPA_RE.fullmatch(name):
        return None
    return name[:80]


def parse_message(text: str, own_vpas: set[str] | None = None) -> ParsedUpi:
    """Read a UPI payment alert. Raises ValueError with a reason the user can act on."""
    own_vpas = own_vpas or set()
    flat = " ".join(text.split())
    low = flat.lower()
    if not flat:
        raise ValueError("The message is empty")
    vpas = [v.lower() for v in VPA_RE.findall(flat) if v.lower() not in own_vpas]
    if "upi" not in low and not vpas and re.search(r"\b(card|pos|atm)\b", low):
        # Some banks' UPI alerts don't say "UPI", but a card or cash withdrawal alert isn't a UPI payment
        raise ValueError("Not a UPI payment message (a card or ATM transaction)")
    if re.search(r"\botp\b", low) or ("request" in low and not re.search(r"debited|credited", low)):
        raise ValueError("A one-time password or a payment request, not a payment")
    debit, credit = _DEBIT.search(flat), _CREDIT.search(flat)
    if not debit and not credit:
        raise ValueError("Couldn't tell whether money was paid or received")
    # Both can appear ("A/c debited …; RAMESH credited"): the first one says what happened to the user's account
    direction = DEBIT if debit and (not credit or debit.start() < credit.start()) else CREDIT
    amount = _amount(flat)
    if amount is None:
        raise ValueError("Couldn't find the amount")
    if amount > Decimal("9999999999.99"):
        raise ValueError("The amount is too large")

    name = None
    m = UPI_PATH_RE.search(text)
    if m:
        name = _clean_name(m.group(1))
    if not name and direction == DEBIT:
        m = _SEMI_CREDITED.search(flat)
        name = _clean_name(m.group(1)) if m else None
    if not name:
        for m in (_TO if direction == DEBIT else _FROM).finditer(flat):
            name = _clean_name(m.group(1))
            if name:
                break
    ref = _REF.search(flat)
    ref = ref.group(1) if ref else (re.search(r"\b(\d{12})\b", flat) or [None, None])[1]
    last4 = _LAST4.search(flat)
    d = _date(flat) or today_local()
    if d > today_local() or d < today_local() - timedelta(days=366):
        d = today_local()
    return ParsedUpi(direction, amount, vpas[0] if vpas else None, name, ref, d, last4.group(1) if last4 else None)


# ---------- forwarding token ----------
def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(user: User) -> str:
    token = secrets.token_urlsafe(32)
    user.upi_token_hash = hash_token(token)
    user.upi_token_hint = token[-4:]
    return token


def user_for_token(db: Session, token: str) -> User | None:
    if not token or len(token) > 200:
        return None
    return db.scalar(select(User).where(User.upi_token_hash == hash_token(token)))


# ---------- recording ----------
def _pick_upi_id(ids: list[UpiId], accounts: dict[int, Account], parsed: ParsedUpi, text: str) -> UpiId | None:
    low = text.lower()
    for u in ids:
        if u.vpa in low:
            return u
    if parsed.last4:
        for u in ids:
            a = accounts.get(u.account_id)
            if a and a.last4 and a.last4.endswith(parsed.last4):
                return u
    return ids[0] if len(ids) == 1 else None


def _event(db: Session, user: User, status: str, reason: str | None, text: str, parsed: ParsedUpi | None = None, **kw) -> UpiEvent:
    ev = UpiEvent(user_id=user.id, status=status, reason=reason and reason[:300], raw_text=text[:MAX_TEXT], **kw)
    if parsed:
        ev.direction, ev.amount, ev.ref, ev.txn_date = parsed.direction, parsed.amount, parsed.ref, parsed.txn_date
        ev.counterparty = parsed.counterparty
        ev.payee_key = payee_key(parsed.vpa or parsed.description)
    db.add(ev)
    db.flush()
    return ev


def record_message(db: Session, user: User, text: str) -> UpiEvent:
    """Record one forwarded message for `user`. Always returns the event saved (the caller commits)."""
    text = (text or "").strip()
    if not has_feature(user, FEATURE_UPI):
        return _event(db, user, UPI_IGNORED, "UPI tracker is not part of your plan", text)
    if not user.upi_enabled:
        return _event(db, user, UPI_IGNORED, "UPI tracking is turned off in Settings", text)
    ids = list(db.scalars(select(UpiId).where(UpiId.user_id == user.id, UpiId.is_active.is_(True)).order_by(UpiId.id)))
    if not ids:
        return _event(db, user, UPI_IGNORED, "Add your UPI ID in Settings → UPI tracker first", text)
    try:
        parsed = parse_message(text, {u.vpa for u in ids})
    except ValueError as e:
        return _event(db, user, UPI_FAILED, str(e), text)

    if parsed.ref:
        dup = db.scalar(select(UpiEvent.id).where(UpiEvent.user_id == user.id, UpiEvent.ref == parsed.ref,
                                                  UpiEvent.status == UPI_CREATED, UpiEvent.direction == parsed.direction))
    else:
        # No reference: the same amount to the same party within two minutes is the same message sent twice
        dup = db.scalar(select(UpiEvent.id).where(
            UpiEvent.user_id == user.id, UpiEvent.status == UPI_CREATED, UpiEvent.direction == parsed.direction,
            UpiEvent.amount == parsed.amount, UpiEvent.counterparty == parsed.counterparty,
            UpiEvent.received_at >= now_local() - timedelta(minutes=2),
        ))
    if dup:
        return _event(db, user, UPI_DUPLICATE, "Already recorded (same UPI reference)", text, parsed)
    if is_locked(get_books(db, user.id), parsed.txn_date):
        return _event(db, user, UPI_FAILED, f"Your books are locked for {parsed.txn_date:%d %b %Y}", text, parsed)

    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.user_id == user.id))}
    upi = _pick_upi_id(ids, accounts, parsed, text)
    account_id = upi.account_id if upi and upi.account_id in accounts and accounts[upi.account_id].is_active else None
    account_id = account_id or resolve_account(db, user.id, None)
    key = payee_key(parsed.vpa or parsed.description)
    alias = find_alias(user_aliases(db, user.id), key, parsed.description)
    note = " · ".join(x for x in (f"UPI ref {parsed.ref}" if parsed.ref else None, f"via {upi.vpa}" if upi else None) if x) or None
    label = parsed.name or parsed.vpa or "UPI payment"

    if parsed.direction == DEBIT:
        row = Expense(user_id=user.id, expense_date=parsed.txn_date, item_name=label[:100], category="General", quantity=1,
                      amount=parsed.amount, note=note, is_extra=False, account_id=account_id, created_by=user.id)
    else:
        row = Fund(user_id=user.id, fund_date=parsed.txn_date, amount=parsed.amount, source=label[:100], note=note,
                   account_id=account_id)
    row.origin, row.payee_key, row.raw_name = "UPI", key, parsed.description
    if alias:
        name_record(row, alias)
    db.add(row)
    db.flush()
    ev = _event(db, user, UPI_CREATED, None if alias else "No alias yet for this payee", text, parsed, upi_id=upi.id if upi else None,
                expense_id=row.id if parsed.direction == DEBIT else None, fund_id=row.id if parsed.direction == CREDIT else None)
    if parsed.direction == DEBIT:
        check_budgets(db, user, parsed.txn_date, today_local())
    return ev
