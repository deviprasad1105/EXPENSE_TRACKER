"""Auto-tracking: turn the bank messages the phone app sends into expenses, money received and transfers.

For each message (see bank_sms.py for reading it) this works out:
- whether it was already recorded: the same message sent twice, the same reference number, the SMS and the bank
  app's notification about one payment, a card bill payment seen from both the bank and the card side, or a record
  with the same date and amount added by hand or imported;
- which of the user's accounts it is about: by the last digits of the account or card, else by the bank's name;
- what to record: an expense, money received, or a transfer (a card bill paid from the bank, cash from an ATM);
- its name and category: the user's transaction aliases first, else a guess from the merchant's name.

In REVIEW mode everything waits for the user to accept it. In AUTO mode a transaction is recorded straight away when
every part is known and nothing suggests it is already recorded; anything uncertain still waits for review.
"""

import hashlib
import re
import secrets
from datetime import date, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..accounting.engine import get_books, is_locked
from ..models import (
    MSG_BILL, MSG_CREATED, MSG_DISMISSED, MSG_DUPLICATE, MSG_FAILED, MSG_IGNORED, MSG_PENDING, UPI_CREATED, Account,
    AccountTransfer, BankMessage, Expense, Fund, Notification, UpiEvent, User,
)
from ..timeutil import now_local, today_local
from .accounts import resolve_account
from .aliases import label_new
from .bank_sms import (
    CH_ATM, CH_CARD_BILL, CH_UPI, CREDIT, DEBIT, KIND_BILL, NotATransaction, ParsedBank, Unreadable, guess_category,
    parse_bank_message,
)
from .budgets import check_budgets
from .cards import SRC_SMS, save_bill

MODE_REVIEW, MODE_AUTO = "REVIEW", "AUTO"
SUG_EXPENSE, SUG_INCOME, SUG_TRANSFER = "EXPENSE", "INCOME", "TRANSFER"
MAX_BODY = 1000
CARD_KIND = "CREDIT_CARD"


# ---------- the phone's token ----------
def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_device_token(user: User) -> str:
    token = secrets.token_urlsafe(32)
    user.autotrack_token_hash = hash_token(token)
    user.autotrack_token_hint = token[-4:]
    return token


def user_for_device_token(db: Session, token: str | None) -> User | None:
    if not token or len(token) > 200:
        return None
    return db.scalar(select(User).where(User.autotrack_token_hash == hash_token(token)))


def default_since(db: Session, user_id: int) -> date:
    """Start after what is already in the records: the latest expense or money received (or 90 days back)."""
    latest = max(
        (d for d in (
            db.scalar(select(func.max(Expense.expense_date)).where(Expense.user_id == user_id)),
            db.scalar(select(func.max(Fund.fund_date)).where(Fund.user_id == user_id)),
        ) if d),
        default=None,
    )
    floor = today_local() - timedelta(days=90)
    return min(max(latest, floor), today_local()) if latest else floor


# ---------- accounts ----------
def _words(text: str | None) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split() if len(w) > 1 and w not in {"bank", "card", "credit", "the", "of", "and", "upi", "payment"}}


def match_account(accounts: list[Account], last4: str | None, bank: str | None, *, card: bool | None = None,
                  name: str | None = None) -> Account | None:
    """The one account the message is about, or None when it isn't clear.

    By the last digits first (the user enters them in Settings → Bank accounts), then by the card's or bank's name.
    `card` True / False keeps only credit cards / only other accounts when that narrows it down."""
    active = [a for a in accounts if a.is_active]

    def narrow(found: list[Account]) -> Account | None:
        if card is not None:
            kind = [a for a in found if (a.kind == CARD_KIND) == card]
            found = kind or found
        return found[0] if len(found) == 1 else None

    if last4:
        found = [a for a in active if a.last4 and (a.last4.endswith(last4) or last4.endswith(a.last4))]
        hit = narrow(found)
        if hit:
            return hit
    # By name: an account whose own last digits are on file and differ is another account at the same bank
    named = [a for a in active if not (last4 and a.last4)]
    for hint in (name, bank):
        words = _words(hint)
        if not words:
            continue
        found = [a for a in named if words <= _words(f"{a.name} {a.bank or ''}")]
        hit = narrow(found)
        if hit:
            return hit
    return None


# ---------- recording ----------
def _received(value: datetime | None) -> datetime:
    now = now_local()
    return now if value is None or value > now + timedelta(minutes=5) else value


def _body_hash(user_id: int, sender: str | None, body: str, received: datetime) -> str:
    flat = " ".join(body.split()).lower()
    return hashlib.sha256(f"{user_id}|{(sender or '').upper()}|{flat}|{received.date()}".encode()).hexdigest()


def _possible_existing(db: Session, user_id: int, parsed: ParsedBank, suggestion: str) -> str | None:
    """A record with the same date and amount already exists (added by hand or imported): say which."""
    if suggestion == SUG_EXPENSE:
        row = db.scalar(select(Expense).where(Expense.user_id == user_id, Expense.expense_date == parsed.txn_date,
                                              Expense.amount == parsed.amount).limit(1))
        return row and f"An expense of {row.amount} on {row.expense_date:%d %b} ('{row.item_name}') may be the same"
    if suggestion == SUG_INCOME:
        row = db.scalar(select(Fund).where(Fund.user_id == user_id, Fund.fund_date == parsed.txn_date,
                                           Fund.amount == parsed.amount).limit(1))
        return row and f"Money received of {row.amount} on {row.fund_date:%d %b} ('{row.source}') may be the same"
    row = db.scalar(select(AccountTransfer).where(
        AccountTransfer.user_id == user_id, AccountTransfer.amount == parsed.amount,
        AccountTransfer.transfer_date.between(parsed.txn_date - timedelta(days=5), parsed.txn_date + timedelta(days=5))).limit(1))
    return row and f"A transfer of {row.amount} on {row.transfer_date:%d %b} may be the same"


def _duplicate_of(db: Session, user: User, parsed: ParsedBank, received: datetime) -> str | None:
    live = (MSG_CREATED, MSG_PENDING)
    if parsed.ref:
        hit = db.scalar(select(BankMessage.id).where(
            BankMessage.user_id == user.id, BankMessage.ref == parsed.ref, BankMessage.direction == parsed.direction,
            BankMessage.status.in_(live)))
        if hit:
            return "Already recorded (same reference number)"
        if db.scalar(select(UpiEvent.id).where(UpiEvent.user_id == user.id, UpiEvent.ref == parsed.ref,
                                               UpiEvent.status == UPI_CREATED)):
            return "Already recorded by the UPI tracker"
    # The SMS and the bank app's notification about one payment arrive minutes apart
    near = db.scalars(select(BankMessage).where(
        BankMessage.user_id == user.id, BankMessage.status.in_(live), BankMessage.amount == parsed.amount,
        BankMessage.direction == parsed.direction, BankMessage.txn_date == parsed.txn_date,
        BankMessage.received_at.between(received - timedelta(minutes=10), received + timedelta(minutes=10)))).all()
    for other in near:
        if not (other.last4 and parsed.last4) or other.last4 == parsed.last4:
            return f"Same transaction as an earlier message ({other.source.lower()})"
    return None


def _card_bill_pair(db: Session, user: User, parsed: ParsedBank) -> BankMessage | None:
    """The other side of a card bill payment: the bank's debit for a card's 'payment received', or the reverse."""
    other_dir = DEBIT if parsed.direction == CREDIT else CREDIT
    return db.scalar(select(BankMessage).where(
        BankMessage.user_id == user.id, BankMessage.channel == CH_CARD_BILL, BankMessage.direction == other_dir,
        BankMessage.amount == parsed.amount, BankMessage.status.in_((MSG_CREATED, MSG_PENDING)),
        BankMessage.txn_date.between(parsed.txn_date - timedelta(days=5), parsed.txn_date + timedelta(days=5)),
    ).order_by(BankMessage.id.desc()).limit(1))


# Accounts a UPI payment can come from
BANK_KINDS = ("SAVINGS", "CURRENT", "SALARY")


def _suggest(db: Session, user: User, msg: BankMessage, parsed: ParsedBank, accounts: list[Account]) -> None:
    """Fill in what to record: the kind of record, its accounts, name and category."""
    own = match_account(accounts, parsed.last4, parsed.bank, card=parsed.is_credit_card)
    msg.name = parsed.label
    if parsed.channel == CH_CARD_BILL:
        msg.suggestion = SUG_TRANSFER
        if parsed.direction == DEBIT:  # paid from the bank, to the card
            msg.account_id = own.id if own else None
            card = match_account(accounts, None, None, card=True, name=parsed.card_name)
            msg.to_account_id = card.id if card else None
        else:  # the card says the payment arrived
            msg.to_account_id = own.id if own else None
        msg.name = f"Card bill{' – ' + parsed.card_name if parsed.card_name else ''}"[:100]
        return
    if own is None and parsed.channel == CH_UPI and not parsed.last4 and not parsed.is_credit_card:
        # UPI apps often don't say which account paid: with only one bank account, it's that one
        banks = [a for a in accounts if a.is_active and a.kind in BANK_KINDS]
        own = banks[0] if len(banks) == 1 else None
    if parsed.channel == CH_ATM:
        cash = [a for a in accounts if a.is_active and a.kind == "CASH"]
        if len(cash) == 1:
            msg.suggestion, msg.account_id, msg.to_account_id, msg.name = SUG_TRANSFER, own and own.id, cash[0].id, "ATM withdrawal"
            return
    msg.account_id = own.id if own else None
    if parsed.direction == DEBIT:
        msg.suggestion = SUG_EXPENSE
        msg.category = "Cash withdrawal" if parsed.channel == CH_ATM else guess_category(parsed.party or parsed.bank)
    else:
        msg.suggestion = SUG_INCOME
        msg.category = None


def _missing(msg: BankMessage) -> str | None:
    if msg.suggestion == SUG_TRANSFER:
        if not msg.account_id or not msg.to_account_id:
            return "Choose both accounts for this transfer"
        if msg.account_id == msg.to_account_id:
            return "The transfer's two accounts are the same"
    elif not msg.account_id:
        return "Which account? Add its last 4 digits in Settings → Bank accounts so it's found next time"
    return None


def record_message(db: Session, user: User, sender: str | None, body: str, received_at: datetime | None,
                   source: str) -> tuple[BankMessage, bool]:
    """Save one message and act on it (the caller commits). Returns (message, is_new): a message already received
    (same sender, text and day) isn't saved again."""
    body = (body or "").strip()[:MAX_BODY]
    received = _received(received_at)
    digest = _body_hash(user.id, sender, body, received)
    seen = db.scalar(select(BankMessage).where(BankMessage.user_id == user.id, BankMessage.body_hash == digest).limit(1))
    if seen:
        return seen, False
    msg = BankMessage(user_id=user.id, received_at=received, source=source, sender=(sender or "")[:80] or None,
                      body=body, body_hash=digest, status=MSG_IGNORED)
    db.add(msg)
    if not user.autotrack_enabled:
        msg.reason = "Auto-tracking is turned off"
        db.flush()
        return msg, True
    try:
        parsed = parse_bank_message(body, sender, received.date())
    except NotATransaction as e:
        msg.reason = str(e)
        db.flush()
        return msg, True
    except Unreadable as e:
        msg.status, msg.reason = MSG_FAILED, f"{e}. Add it by hand if it is a transaction."
        db.flush()
        return msg, True

    for attr in ("kind", "direction", "channel", "amount", "bank", "last4", "ref", "balance", "txn_date", "due_date", "min_due"):
        setattr(msg, attr, getattr(parsed, attr))
    msg.party = parsed.party and parsed.party[:120]
    accounts = list(db.scalars(select(Account).where(Account.user_id == user.id)))

    if parsed.kind == KIND_BILL:
        return _record_bill(db, user, msg, parsed, accounts), True
    if user.autotrack_since and parsed.txn_date < user.autotrack_since:
        msg.reason = f"Before your start date ({user.autotrack_since:%d %b %Y}): already in your records"
        db.flush()
        return msg, True
    dup = _duplicate_of(db, user, parsed, received)
    if dup:
        msg.status, msg.reason = MSG_DUPLICATE, dup
        db.flush()
        return msg, True

    _suggest(db, user, msg, parsed, accounts)
    if parsed.channel == CH_CARD_BILL:
        pair = _card_bill_pair(db, user, parsed)
        if pair:
            # One payment, two messages: complete the first one with this side's account and keep it
            if parsed.direction == CREDIT and msg.to_account_id and not pair.to_account_id and pair.status == MSG_PENDING:
                pair.to_account_id = msg.to_account_id
            elif parsed.direction == DEBIT and msg.account_id and not pair.account_id and pair.status == MSG_PENDING:
                pair.account_id = msg.account_id
            msg.status, msg.reason = MSG_DUPLICATE, "The same card bill payment as an earlier message"
            db.flush()
            return msg, True

    msg.status = MSG_PENDING
    msg.reason = _missing(msg) or _possible_existing(db, user.id, parsed, msg.suggestion)
    if not msg.reason and user.autotrack_mode == MODE_AUTO:
        try:
            create_record(db, user, msg)
        except HTTPException as e:
            msg.status, msg.reason = MSG_PENDING, str(e.detail)
    db.flush()
    return msg, True


def _record_bill(db: Session, user: User, msg: BankMessage, parsed: ParsedBank, accounts: list[Account]) -> BankMessage:
    card = match_account(accounts, parsed.last4, parsed.bank, card=True)
    msg.account_id = card.id if card else None
    msg.name = (card.name if card else f"{parsed.bank or 'Card'} bill")[:100]
    same = db.scalar(select(BankMessage.id).where(
        BankMessage.user_id == user.id, BankMessage.status == MSG_BILL, BankMessage.amount == parsed.amount,
        BankMessage.due_date == parsed.due_date, or_(BankMessage.bank == parsed.bank, BankMessage.last4 == parsed.last4)))
    if same:
        msg.status, msg.reason = MSG_DUPLICATE, "Same bill as an earlier message"
    else:
        msg.status = MSG_BILL
        due = f" is due on {parsed.due_date:%d %b %Y}" if parsed.due_date else " is due"
        minimum = f" (minimum {parsed.min_due})" if parsed.min_due else ""
        db.add(Notification(user_id=user.id, kind="BILL_DUE", message=f"{msg.name}: {parsed.amount}{minimum}{due}"))
    # The card's bill on the Credit Cards page (replaces an estimate for the same cycle; a typed-in one stays)
    if card and parsed.due_date:
        save_bill(db, card, parsed.due_date, parsed.amount, SRC_SMS, parsed.min_due)
    db.flush()
    return msg


def create_record(db: Session, user: User, msg: BankMessage) -> None:
    """Make the expense, money received or transfer the message describes (its fields as the user left them)."""
    missing = _missing(msg)
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, missing)
    if not msg.amount or msg.amount <= 0 or not msg.txn_date:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter the amount and the date")
    if is_locked(get_books(db, user.id), msg.txn_date):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Your books are locked for {msg.txn_date:%d %b %Y}")
    for aid in (msg.account_id, msg.to_account_id):
        if aid:
            resolve_account(db, user.id, aid)
    source = msg.bank or msg.sender or "bank message"
    note = " · ".join(x for x in (f"Auto-tracked from {source}", f"ref {msg.ref}" if msg.ref else None) if x)[:200]
    name = (msg.name or "Bank transaction")[:100]
    if msg.suggestion == SUG_TRANSFER:
        row = AccountTransfer(user_id=user.id, from_account_id=msg.account_id, to_account_id=msg.to_account_id,
                              amount=msg.amount, transfer_date=msg.txn_date, note=f"{name} · {note}"[:200])
        db.add(row)
        db.flush()
        msg.transfer_id = row.id
    elif msg.suggestion == SUG_EXPENSE:
        row = Expense(user_id=user.id, expense_date=msg.txn_date, item_name=name, category=(msg.category or "General")[:50],
                      quantity=1, amount=msg.amount, note=note, is_extra=False, account_id=msg.account_id, created_by=user.id)
        row.origin = "SMS"
        label_new(db, row, set_category=not msg.category or msg.category == "General")
        db.add(row)
        db.flush()
        msg.expense_id, msg.name, msg.category = row.id, row.item_name, row.category
        check_budgets(db, user, row.expense_date, today_local())
    else:
        row = Fund(user_id=user.id, fund_date=msg.txn_date, amount=msg.amount, source=name, note=note, account_id=msg.account_id)
        row.origin = "SMS"
        label_new(db, row)
        db.add(row)
        db.flush()
        msg.fund_id, msg.name = row.id, row.source
    msg.status, msg.reason = MSG_CREATED, None


def accept(db: Session, user: User, msg: BankMessage, changes: dict) -> None:
    """Record a waiting (or unreadable) message, with any fields the user changed."""
    if msg.status not in (MSG_PENDING, MSG_FAILED):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This message has already been dealt with")
    for key in ("suggestion", "name", "category", "account_id", "to_account_id", "amount", "txn_date"):
        if key in changes and changes[key] is not None:
            setattr(msg, key, changes[key])
    if msg.suggestion not in (SUG_EXPENSE, SUG_INCOME, SUG_TRANSFER):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose expense, money received or transfer")
    if msg.suggestion != SUG_TRANSFER:
        msg.to_account_id = None
    create_record(db, user, msg)


def dismiss(msg: BankMessage) -> None:
    if msg.status not in (MSG_PENDING, MSG_FAILED, MSG_BILL):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This message has already been dealt with")
    msg.status, msg.reason = MSG_DISMISSED, "You chose not to record it"


def ready_to_accept(msg: BankMessage) -> bool:
    """Accept-all takes only complete suggestions that don't look like something already recorded."""
    return msg.status == MSG_PENDING and _missing(msg) is None and not (msg.reason and "may be the same" in msg.reason)


__all__ = [
    "MODE_AUTO", "MODE_REVIEW", "SUG_EXPENSE", "SUG_INCOME", "SUG_TRANSFER", "accept", "create_record", "default_since",
    "dismiss", "match_account", "new_device_token", "ready_to_accept", "record_message", "user_for_device_token",
]
