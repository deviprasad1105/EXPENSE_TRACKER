"""The voucher engine: the only code that writes vouchers.

Rules it enforces for every voucher, manual or auto-posted:
- at least two lines; each line is a debit or a credit (not both, not zero), in whole paise
- total debits == total credits
- every ledger belongs to the same user
- nothing dated on or before the lock date
- numbered per voucher type within its financial year
A posted voucher is never edited: it is corrected by a reversal (mirror lines, same date) plus a new voucher.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from ..models import Account, AccountTransfer, Expense, Fund, SplitBill, SplitMember, SplitSettlement, User
from ..models import Subscription, SubscriptionCharge
from ..timeutil import now_local, today_local
from .chart import ACCOUNT_KIND_GROUP, CASH_BANK_GROUPS, SEED_LEDGERS, fy_of, fy_start
from .models import VOUCHER_POSTED, VOUCHER_REVERSED, AccLedger, AccSettings, Voucher, VoucherLine

PAYMENT, RECEIPT, CONTRA, JOURNAL, SALES, PURCHASE = "PAYMENT", "RECEIPT", "CONTRA", "JOURNAL", "SALES", "PURCHASE"
VOUCHER_TYPES = (PAYMENT, RECEIPT, CONTRA, JOURNAL, SALES, PURCHASE)
PAISA = Decimal("0.01")
ZERO = Decimal(0)


class AccountingError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class PeriodLocked(AccountingError):
    status_code = 409


@dataclass
class Line:
    ledger_id: int
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    gst_rate: Decimal | None = None


# ---------- books ----------
def get_books(db: Session, user_id: int) -> AccSettings | None:
    return db.scalar(select(AccSettings).where(AccSettings.user_id == user_id))


def is_locked(books: AccSettings | None, d: date) -> bool:
    return bool(books and books.lock_date and d <= books.lock_date)


def check_open(books: AccSettings, d: date, owner: User | None = None) -> None:
    if is_locked(books, d):
        whose = f"@{owner.username}'s books are" if owner else "The books are"
        raise PeriodLocked(
            f"{whose} locked up to {books.lock_date:%d %b %Y}, so nothing dated {d:%d %b %Y} can be posted, changed or "
            "deleted. Make the correction with a voucher in an open period."
        )


def earliest_transaction(db: Session, user_id: int) -> date | None:
    member_ids = select(SplitMember.id).where(SplitMember.user_id == user_id)
    candidates = [
        db.scalar(select(func.min(Expense.expense_date)).where(Expense.user_id == user_id)),
        db.scalar(select(func.min(Fund.fund_date)).where(Fund.user_id == user_id)),
        db.scalar(select(func.min(AccountTransfer.transfer_date)).where(AccountTransfer.user_id == user_id)),
        db.scalar(
            select(func.min(SubscriptionCharge.charge_date)).join(Subscription).where(Subscription.user_id == user_id)
        ),
        db.scalar(select(func.min(SplitBill.bill_date)).where(SplitBill.paid_by.in_(member_ids))),
        db.scalar(
            select(func.min(SplitSettlement.settled_on)).where(
                SplitSettlement.from_member.in_(member_ids) | SplitSettlement.to_member.in_(member_ids)
            )
        ),
    ]
    dates = [d for d in candidates if d is not None]
    return min(dates) if dates else None


def create_books(db: Session, user: User) -> AccSettings:
    """Set up a user's books: settings, the standard ledgers, and a ledger per bank account. No postings yet."""
    first = earliest_transaction(db, user.id) or today_local()
    books = AccSettings(user_id=user.id, books_start=fy_start(fy_of(first)), business_name=user.full_name)
    db.add(books)
    for code, name, group, is_dep in SEED_LEDGERS:
        db.add(AccLedger(user_id=user.id, code=code, name=name, group_code=group, is_depreciation=is_dep))
    db.flush()
    for account in db.scalars(select(Account).where(Account.user_id == user.id).order_by(Account.id)):
        sync_account_ledger(db, books, account)
    db.flush()
    return books


# ---------- ledgers the app posts to ----------
def system_ledger(db: Session, user_id: int, code: str) -> AccLedger:
    ledger = db.scalar(select(AccLedger).where(AccLedger.user_id == user_id, AccLedger.code == code))
    if ledger is None:
        # A seeded ledger can't be deleted, but recreate it rather than fail if it is somehow missing
        seed = next(s for s in SEED_LEDGERS if s[0] == code)
        ledger = AccLedger(user_id=user_id, code=code, name=unique_ledger_name(db, user_id, seed[1]), group_code=seed[2],
                           is_depreciation=seed[3])
        db.add(ledger)
        db.flush()
    return ledger


def unique_ledger_name(db: Session, user_id: int, name: str, exclude_id: int | None = None) -> str:
    """`name`, or `name (2)`, `name (3)`… if another ledger of this user already has it."""
    name = name[:90]
    taken = {
        n.casefold()
        for n in db.scalars(select(AccLedger.name).where(AccLedger.user_id == user_id, AccLedger.id != (exclude_id or 0)))
    }
    candidate, i = name, 2
    while candidate.casefold() in taken:
        candidate, i = f"{name} ({i})", i + 1
    return candidate


def account_ledger(db: Session, books: AccSettings, account: Account) -> AccLedger:
    ledger = db.scalar(select(AccLedger).where(AccLedger.account_id == account.id))
    return ledger or sync_account_ledger(db, books, account)


def sync_account_ledger(db: Session, books: AccSettings, account: Account) -> AccLedger:
    """Keep the ledger of a bank account in step with it: name, group (from the kind), opening balance."""
    ledger = db.scalar(select(AccLedger).where(AccLedger.account_id == account.id))
    group = ACCOUNT_KIND_GROUP.get(account.kind, "BANK_ACCOUNTS")
    opening = Decimal(account.opening_balance or 0)
    if ledger is None:
        ledger = AccLedger(
            user_id=account.user_id, account_id=account.id, group_code=group, opening_balance=opening,
            name=unique_ledger_name(db, account.user_id, account.name), is_active=account.is_active,
        )
        db.add(ledger)
        db.flush()
        return ledger
    if (ledger.group_code != group or ledger.opening_balance != opening) and books.lock_date:
        raise PeriodLocked(
            "The books are locked, so this account's opening balance and type can't change any more. "
            "Make the correction with a journal voucher in an open period."
        )
    ledger.group_code, ledger.opening_balance, ledger.is_active = group, opening, account.is_active
    if ledger.name.casefold() != account.name.casefold():
        ledger.name = unique_ledger_name(db, account.user_id, account.name, exclude_id=ledger.id)
    return ledger


def payment_ledger(db: Session, books: AccSettings, user_id: int, account_id: int | None) -> AccLedger:
    """Where money was paid from / received into: the account given, else the primary account, else Suspense."""
    account = db.get(Account, account_id) if account_id else None
    if account is None or account.user_id != user_id:
        account = db.scalar(
            select(Account).where(Account.user_id == user_id, Account.is_primary == true(), Account.is_active == true())
        )
    return account_ledger(db, books, account) if account else system_ledger(db, user_id, "SUSPENSE")


def is_cash_or_bank(ledger: AccLedger) -> bool:
    return ledger.group_code in CASH_BANK_GROUPS or ledger.account_id is not None


# ---------- posting ----------
def _amount(value, what: str) -> Decimal:
    d = Decimal(str(value if value is not None else 0))
    if d < 0:
        raise AccountingError(f"{what} can't be negative")
    if d != d.quantize(PAISA):
        raise AccountingError(f"{what} has more than 2 decimal places")
    return d.quantize(PAISA)


def post_voucher(
    db: Session,
    books: AccSettings,
    *,
    voucher_type: str,
    voucher_date: date,
    lines: list[Line],
    narration: str | None = None,
    created_by: int | None = None,
    source: tuple[str, int, int] | None = None,
    party_ledger_id: int | None = None,
    invoice_no: str | None = None,
    place_of_supply: str | None = None,
    reversal_of: Voucher | None = None,
    manual: bool = False,
) -> Voucher:
    """Validate and post one voucher. `manual` adds Tally's per-type rules and refuses inactive ledgers."""
    if voucher_type not in VOUCHER_TYPES:
        raise AccountingError(f"Unknown voucher type {voucher_type}")
    check_open(books, voucher_date)
    if len(lines) < 2:
        raise AccountingError("A voucher needs at least two lines")
    clean: list[Line] = []
    for i, line in enumerate(lines, start=1):
        dr, cr = _amount(line.debit, f"Line {i} debit"), _amount(line.credit, f"Line {i} credit")
        if (dr > 0) == (cr > 0):
            raise AccountingError(f"Line {i} must have either a debit or a credit amount")
        clean.append(Line(line.ledger_id, dr, cr, line.gst_rate))
    total_dr, total_cr = sum(l.debit for l in clean), sum(l.credit for l in clean)
    if total_dr != total_cr:
        raise AccountingError(f"Debits (₹{total_dr:,.2f}) and credits (₹{total_cr:,.2f}) must be equal")

    ids = {l.ledger_id for l in clean}
    ledgers = {g.id: g for g in db.scalars(select(AccLedger).where(AccLedger.id.in_(ids)))}
    if len(ledgers) != len(ids) or any(g.user_id != books.user_id for g in ledgers.values()):
        raise AccountingError("Every ledger must be one of your own ledgers")
    if manual:
        inactive = [g.name for g in ledgers.values() if not g.is_active]
        if inactive:
            raise AccountingError(f"Inactive ledger: {', '.join(inactive)}")
        _check_type_rules(voucher_type, clean, ledgers)

    # One posting at a time per user (Oracle: row lock; SQLite has a single writer anyway): keeps numbers unique
    db.execute(select(AccSettings.id).where(AccSettings.id == books.id).with_for_update())
    fy = fy_of(voucher_date)
    number = (
        db.scalar(
            select(func.max(Voucher.voucher_no)).where(
                Voucher.user_id == books.user_id, Voucher.voucher_type == voucher_type, Voucher.fy == fy
            )
        )
        or 0
    ) + 1
    voucher = Voucher(
        user_id=books.user_id, voucher_type=voucher_type, voucher_no=number, fy=fy, voucher_date=voucher_date,
        narration=(narration or None) and narration[:500], created_by=created_by, party_ledger_id=party_ledger_id,
        invoice_no=invoice_no, place_of_supply=place_of_supply,
        is_reversal=reversal_of is not None, reversal_of_id=reversal_of.id if reversal_of else None,
    )
    if source:
        stype, sid, version = source
        voucher.source_type, voucher.source_id, voucher.source_version = stype, sid, version
        voucher.source_key = f"{stype}:{sid}:u{books.user_id}:v{version}"
    voucher.lines = [
        VoucherLine(ledger_id=l.ledger_id, debit=l.debit, credit=l.credit, gst_rate=l.gst_rate, line_no=i)
        for i, l in enumerate(clean, start=1)
    ]
    db.add(voucher)
    if voucher_date < books.books_start:
        books.books_start = fy_start(fy)
    db.flush()
    return voucher


def _check_type_rules(voucher_type: str, lines: list[Line], ledgers: dict[int, AccLedger]) -> None:
    cash = {i for i, g in ledgers.items() if is_cash_or_bank(g)}
    if voucher_type == PAYMENT and not any(l.credit > 0 and l.ledger_id in cash for l in lines):
        raise AccountingError("A Payment voucher must credit a cash or bank ledger (where the money was paid from)")
    if voucher_type == RECEIPT and not any(l.debit > 0 and l.ledger_id in cash for l in lines):
        raise AccountingError("A Receipt voucher must debit a cash or bank ledger (where the money was received)")
    if voucher_type == CONTRA and not all(l.ledger_id in cash for l in lines):
        raise AccountingError("A Contra voucher can only use cash and bank ledgers")


def reverse_voucher(
    db: Session, books: AccSettings, voucher: Voucher, created_by: int | None = None, narration: str | None = None
) -> Voucher:
    """Post the mirror image of `voucher` on the same date, so the pair nets to zero, and mark it reversed."""
    if voucher.is_reversal:
        raise AccountingError("A reversal can't itself be reversed. Post a new voucher instead.")
    if voucher.status != VOUCHER_POSTED:
        raise AccountingError("This voucher has already been reversed")
    check_open(books, voucher.voucher_date)
    reversal = post_voucher(
        db, books,
        voucher_type=voucher.voucher_type,
        voucher_date=voucher.voucher_date,
        lines=[Line(l.ledger_id, debit=l.credit, credit=l.debit) for l in voucher.lines],
        narration=narration or f"Reversal of {voucher.voucher_type.title()} #{voucher.voucher_no}"
        + (f": {voucher.narration}" if voucher.narration else ""),
        created_by=created_by,
        reversal_of=voucher,
    )
    voucher.status = VOUCHER_REVERSED
    db.flush()
    return reversal


def touch_synced(books: AccSettings) -> None:
    books.synced_at = now_local()
