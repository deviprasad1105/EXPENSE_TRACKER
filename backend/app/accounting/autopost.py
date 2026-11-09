"""Auto-posting: keep the books in step with expenses, funds, transfers, subscription charges and split bills.

For each source transaction we work out the voucher it *should* have (`_plans`) and compare it with the voucher
it *has* (the active, unreversed auto-voucher for that source):
- same lines and narration          → nothing to do (so retries and repeated syncs never post twice)
- only the narration differs        → update the narration
- anything else                     → reverse the old voucher and post a new version
Nothing dated on or before a user's lock date is touched: a live change raises PeriodLocked (and the whole
change is rolled back); a bulk re-sync skips it and reports it.

How it runs: SQLAlchemy session events note every tracked row that is added, changed or deleted, and just before
the commit `_process_pending` syncs them. That covers every path (API routes, the subscription job, backup
restore) without each one having to remember to post.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import event, false, func, select
from sqlalchemy.orm import Session

from ..models import (
    Account,
    AccountTransfer,
    Expense,
    Fund,
    SplitBill,
    SplitMember,
    SplitSettlement,
    SplitShare,
    Subscription,
    SubscriptionCharge,
    User,
)
from .chart import EXPENSE_GROUPS, INCOME_GROUPS
from ..importer.models import ImportRecord
from .engine import (
    CONTRA,
    JOURNAL,
    PAYMENT,
    RECEIPT,
    ZERO,
    AccountingError,
    Line,
    account_ledger,
    check_open,
    get_books,
    imported_source,
    is_cash_or_bank,
    is_locked,
    payment_ledger,
    post_voucher,
    reverse_voucher,
    sync_account_ledger,
    system_ledger,
    touch_synced,
    unique_ledger_name,
)
from .models import (
    RULE_EXPENSE_CATEGORY,
    RULE_FUND_SOURCE,
    VOUCHER_POSTED,
    AccLedger,
    AccSettings,
    PostingRule,
    Voucher,
    VoucherLine,
)

EXPENSE, FUND, TRANSFER, SUB_CHARGE = "EXPENSE", "FUND", "TRANSFER", "SUB_CHARGE"
SPLIT_BILL, SPLIT_SETTLEMENT, ACCOUNT = "SPLIT_BILL", "SPLIT_SETTLEMENT", "ACCOUNT"
SOURCE_TYPES = (EXPENSE, FUND, TRANSFER, SUB_CHARGE, SPLIT_BILL, SPLIT_SETTLEMENT)
SOURCE_LABEL = {
    EXPENSE: "Expense", FUND: "Money received", TRANSFER: "Transfer", SUB_CHARGE: "Subscription charge",
    SPLIT_BILL: "Split bill", SPLIT_SETTLEMENT: "Split settlement",
}


@dataclass
class Plan:
    voucher_type: str
    voucher_date: date
    narration: str
    net: dict[int, Decimal]  # ledger id → debit (+) / credit (−)

    def lines(self) -> list[Line]:
        return [
            Line(lid, debit=amt if amt > 0 else ZERO, credit=-amt if amt < 0 else ZERO)
            for lid, amt in sorted(self.net.items())
            if amt != 0
        ]

    def signature(self):
        return self.voucher_type, self.voucher_date, tuple((l.ledger_id, l.debit, l.credit) for l in self.lines())


def _signature_of(v: Voucher):
    net: dict[int, Decimal] = defaultdict(Decimal)
    for l in v.lines:
        net[l.ledger_id] += Decimal(l.debit) - Decimal(l.credit)
    return Plan(v.voucher_type, v.voucher_date, "", dict(net)).signature()


def _net(*pairs) -> dict[int, Decimal]:
    out: dict[int, Decimal] = defaultdict(Decimal)
    for ledger, amount in pairs:
        out[ledger.id] += Decimal(amount)
    return dict(out)


# ---------- posting rules ----------
def rule_key(label: str | None) -> str:
    return (label or "").strip().casefold()[:100] or "general"


def default_rule_ledger(db: Session, user_id: int, kind: str, key: str) -> AccLedger:
    """A ledger with the same name in an expense (or income) group; otherwise Drawings (or Capital).

    Personal spending is the proprietor's drawings and personal money coming in is capital introduced, so by
    default neither touches the business's profit. Business categories are mapped to expense ledgers instead.
    """
    groups = EXPENSE_GROUPS if kind == RULE_EXPENSE_CATEGORY else INCOME_GROUPS
    match = db.scalar(
        select(AccLedger).where(
            AccLedger.user_id == user_id, AccLedger.group_code.in_(groups), func.lower(AccLedger.name) == key
        )
    )
    return match or system_ledger(db, user_id, "DRAWINGS" if kind == RULE_EXPENSE_CATEGORY else "CAPITAL")


def rule_ledger(db: Session, user_id: int, kind: str, label: str | None) -> AccLedger:
    key = rule_key(label)
    rule = db.scalar(
        select(PostingRule).where(PostingRule.user_id == user_id, PostingRule.kind == kind, PostingRule.rule_key == key)
    )
    ledger = db.get(AccLedger, rule.ledger_id) if rule else None
    if ledger is None:
        ledger = default_rule_ledger(db, user_id, kind, key)
        if rule:
            rule.ledger_id = ledger.id
        else:
            db.add(PostingRule(user_id=user_id, kind=kind, rule_key=key, label=(label or "General").strip()[:100] or "General",
                               ledger_id=ledger.id))
        db.flush()
    return ledger


def split_ledger(db: Session, user_id: int, group) -> AccLedger:
    ledger = db.scalar(select(AccLedger).where(AccLedger.user_id == user_id, AccLedger.split_group_id == group.id))
    if ledger is None:
        ledger = AccLedger(
            user_id=user_id, split_group_id=group.id, group_code="SUNDRY_DEBTORS",
            name=unique_ledger_name(db, user_id, f"Split – {group.name}"),
        )
        db.add(ledger)
        db.flush()
    return ledger


# ---------- what each source should post ----------
# Each returns {user id: function(books) -> Plan | None}. A missing source returns {} (its vouchers get reversed).
def _expense(db: Session, sid: int):
    e = db.get(Expense, sid)
    if e is None:
        return {}

    def plan(books: AccSettings):
        qty = f" × {e.quantity}" if e.quantity and e.quantity > 1 else ""
        narration = f"{e.item_name}{qty}" + (f" – {e.note}" if e.note else "")
        return Plan(PAYMENT, e.expense_date, narration, _net(
            (rule_ledger(db, e.user_id, RULE_EXPENSE_CATEGORY, e.category), e.amount),
            (payment_ledger(db, books, e.user_id, e.account_id), -e.amount),
        ))

    return {e.user_id: plan}


def _fund(db: Session, sid: int):
    f = db.get(Fund, sid)
    if f is None:
        return {}

    def plan(books: AccSettings):
        narration = (f.source or "Money received") + (f" – {f.note}" if f.note else "")
        return Plan(RECEIPT, f.fund_date, narration, _net(
            (payment_ledger(db, books, f.user_id, f.account_id), f.amount),
            (rule_ledger(db, f.user_id, RULE_FUND_SOURCE, f.source), -f.amount),
        ))

    return {f.user_id: plan}


def _transfer(db: Session, sid: int):
    t = db.get(AccountTransfer, sid)
    if t is None:
        return {}

    def plan(books: AccSettings):
        src, dst = db.get(Account, t.from_account_id), db.get(Account, t.to_account_id)
        if src is None or dst is None:
            return None
        narration = f"Transfer {src.name} → {dst.name}" + (f" – {t.note}" if t.note else "")
        to_ledger, from_ledger = account_ledger(db, books, dst), account_ledger(db, books, src)
        # Bank to bank is a contra; into an investment / money-with-others account a payment, out of one a receipt
        to_cash, from_cash = is_cash_or_bank(to_ledger), is_cash_or_bank(from_ledger)
        vtype = CONTRA if to_cash and from_cash else PAYMENT if from_cash else RECEIPT if to_cash else JOURNAL
        return Plan(vtype, t.transfer_date, narration, _net((to_ledger, t.amount), (from_ledger, -t.amount)))

    return {t.user_id: plan}


def _sub_charge(db: Session, sid: int):
    c = db.get(SubscriptionCharge, sid)
    if c is None:
        return {}
    sub = db.get(Subscription, c.subscription_id)
    if sub is None:
        return {}

    def plan(books: AccSettings):
        # Logged as an expense: that expense carries the posting, so the charge itself posts nothing
        if c.expense_id and db.get(Expense, c.expense_id) is not None:
            return None
        return Plan(PAYMENT, c.charge_date, f"{sub.name}: subscription charge", _net(
            (rule_ledger(db, sub.user_id, RULE_EXPENSE_CATEGORY, sub.category), c.amount),
            (payment_ledger(db, books, sub.user_id, None), -c.amount),
        ))

    return {sub.user_id: plan}


def _split_bill(db: Session, sid: int):
    """Each app user in the group: their share is spending; what they paid beyond it is owed to them."""
    b = db.get(SplitBill, sid)
    if b is None:
        return {}
    group = b.group
    shares: dict[int, Decimal] = defaultdict(Decimal)
    for s in db.scalars(select(SplitShare).where(SplitShare.bill_id == b.id)):
        shares[s.member_id] += Decimal(s.amount)
    plans = {}
    for m in db.scalars(select(SplitMember).where(SplitMember.group_id == group.id, SplitMember.user_id.is_not(None))):
        share, paid = shares.get(m.id, ZERO), Decimal(b.total_amount) if b.paid_by == m.id else ZERO
        if share == 0 and paid == 0:
            continue

        def plan(books: AccSettings, uid=m.user_id, share=share, paid=paid):
            pairs = [
                (rule_ledger(db, uid, RULE_EXPENSE_CATEGORY, f"Split bills: {group.name}"), share),
                (split_ledger(db, uid, group), paid - share),
            ]
            if paid:
                pairs.append((payment_ledger(db, books, uid, None), -paid))
            return Plan(PAYMENT if paid else JOURNAL, b.bill_date, f"{b.title} (split: {group.name})", _net(*pairs))

        plans[m.user_id] = plan
    return plans


def _split_settlement(db: Session, sid: int):
    s = db.get(SplitSettlement, sid)
    if s is None:
        return {}
    group, payer, payee = s.group, s.payer, s.payee
    narration = f"Split settlement: {payer.name} paid {payee.name} ({group.name})" + (f" – {s.note}" if s.note else "")
    plans = {}
    if payer.user_id:
        plans[payer.user_id] = lambda books, uid=payer.user_id: Plan(PAYMENT, s.settled_on, narration, _net(
            (split_ledger(db, uid, group), s.amount), (payment_ledger(db, books, uid, None), -s.amount),
        ))
    if payee.user_id:
        plans[payee.user_id] = lambda books, uid=payee.user_id: Plan(RECEIPT, s.settled_on, narration, _net(
            (payment_ledger(db, books, uid, None), s.amount), (split_ledger(db, uid, group), -s.amount),
        ))
    return plans


_PLANNERS = {
    EXPENSE: _expense, FUND: _fund, TRANSFER: _transfer, SUB_CHARGE: _sub_charge,
    SPLIT_BILL: _split_bill, SPLIT_SETTLEMENT: _split_settlement,
}


# ---------- syncing ----------
@dataclass
class SyncReport:
    posted: int = 0
    reposted: int = 0
    reversed: int = 0
    unchanged: int = 0
    locked: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"posted": self.posted, "reposted": self.reposted, "reversed": self.reversed,
                "unchanged": self.unchanged, "locked": self.locked}


def active_voucher(db: Session, user_id: int, stype: str, sid: int) -> Voucher | None:
    # Looked up by the source only (one or two vouchers), so every database uses ix_voucher_source; with user_id in
    # the WHERE, SQLite picks the (user_id, date) index and scans all of the user's vouchers each time.
    for v in db.scalars(
        select(Voucher).where(
            Voucher.source_type == stype, Voucher.source_id == sid,
            Voucher.status == VOUCHER_POSTED, Voucher.is_reversal == false(),
        )
    ):
        if v.user_id == user_id:
            return v
    return None


def sync_source(
    db: Session, stype: str, sid: int, *, strict: bool, only_user: int | None = None, report: SyncReport | None = None,
    keep_missing: bool = False,
) -> SyncReport:
    """Bring the vouchers of one source in line with it, for every user whose books it touches."""
    report = report or SyncReport()
    plans = _PLANNERS[stype](db, sid)
    if not plans and keep_missing:
        return report
    active_users = set(
        db.scalars(
            select(Voucher.user_id).where(
                Voucher.source_type == stype, Voucher.source_id == sid, Voucher.status == VOUCHER_POSTED,
                Voucher.is_reversal == false(),
            )
        )
    )
    for uid in sorted(set(plans) | active_users):
        if only_user is not None and uid != only_user:
            continue
        books = get_books(db, uid)
        if books is None:
            continue
        want = plans[uid](books) if uid in plans else None
        if (want is not None and want.voucher_date < books.books_start and stype in _IMPORT_COLUMN
                and imported_source(db, _IMPORT_COLUMN[stype], sid)):
            want = None  # imported history before the books: carried in the accounts' opening balances instead
        _apply(db, books, stype, sid, want, active_voucher(db, uid, stype, sid), strict, report)
    return report


def _apply(db, books, stype, sid, want: Plan | None, active: Voucher | None, strict: bool, report: SyncReport):
    if want is not None and not want.lines():
        want = None
    if want is None and active is None:
        return
    if want and active and want.signature() == _signature_of(active):
        if (active.narration or "") == want.narration[:500]:
            report.unchanged += 1
            return
        if not _allowed(db, books, [active.voucher_date], strict, stype, sid, report):
            return
        active.narration = want.narration[:500]
        report.unchanged += 1
        return
    dates = ([active.voucher_date] if active else []) + ([want.voucher_date] if want else [])
    if not _allowed(db, books, dates, strict, stype, sid, report):
        return
    label = SOURCE_LABEL[stype].lower()
    if active:
        reverse_voucher(
            db, books, active,
            narration=f"Reversal: the {label} was {'changed' if want else 'deleted'}"
            + (f" ({active.narration})" if active.narration else ""),
        )
    if want:
        # By source only, then the user in Python: see active_voucher (keeps every database on ix_voucher_source)
        versions = db.execute(
            select(Voucher.user_id, Voucher.source_version).where(Voucher.source_type == stype, Voucher.source_id == sid)
        )
        version = max((v or 0 for uid, v in versions if uid == books.user_id), default=0) + 1
        post_voucher(db, books, voucher_type=want.voucher_type, voucher_date=want.voucher_date, lines=want.lines(),
                     narration=want.narration, source=(stype, sid, version))
    if active and want:
        report.reposted += 1
    elif want:
        report.posted += 1
    else:
        report.reversed += 1


def _allowed(db, books, dates, strict, stype, sid, report) -> bool:
    locked = [d for d in dates if is_locked(books, d)]
    if not locked:
        return True
    if strict:
        check_open(books, locked[0], db.get(User, books.user_id))
    report.locked.append(f"{SOURCE_LABEL[stype]} #{sid} ({locked[0]:%d %b %Y})")
    return False


def sync_all(db: Session, books: AccSettings) -> SyncReport:
    """Post everything of this user that isn't posted yet (or changed since); reverse what no longer exists.

    Used when the books are created, after a posting rule changes, and from Settings → Re-sync. Safe to run any
    number of times. Locked periods are left as they are and listed in the report.
    """
    uid, report = books.user_id, SyncReport()
    for account in db.scalars(select(Account).where(Account.user_id == uid)):
        try:
            sync_account_ledger(db, books, account)
        except AccountingError as e:
            report.locked.append(f"Account {account.name}: {e.message}")
    member_groups = select(SplitMember.group_id).where(SplitMember.user_id == uid)
    sources = {
        EXPENSE: select(Expense.id).where(Expense.user_id == uid),
        FUND: select(Fund.id).where(Fund.user_id == uid),
        TRANSFER: select(AccountTransfer.id).where(AccountTransfer.user_id == uid),
        SUB_CHARGE: select(SubscriptionCharge.id).join(Subscription).where(Subscription.user_id == uid),
        SPLIT_BILL: select(SplitBill.id).where(SplitBill.group_id.in_(member_groups)),
        SPLIT_SETTLEMENT: select(SplitSettlement.id).where(SplitSettlement.group_id.in_(member_groups)),
    }
    for stype, stmt in sources.items():
        ids = set(db.scalars(stmt))
        # Plus sources that still have a voucher here but are gone (or no longer involve this user)
        ids |= set(
            db.scalars(
                select(Voucher.source_id).where(
                    Voucher.user_id == uid, Voucher.source_type == stype, Voucher.status == VOUCHER_POSTED,
                    Voucher.is_reversal == false(),
                )
            )
        )
        for sid in sorted(ids):
            # A deleted split group takes its (settled) bills with it: those vouchers stay as history
            sync_source(db, stype, sid, strict=False, only_user=uid, report=report,
                        keep_missing=stype in (SPLIT_BILL, SPLIT_SETTLEMENT))
    touch_synced(books)
    db.flush()
    return report


def sync_rule(db: Session, books: AccSettings, rule: PostingRule) -> SyncReport:
    """Re-post the sources a posting rule covers (open periods only), after the rule's ledger changed."""
    uid, report = books.user_id, SyncReport()
    if rule.kind == RULE_FUND_SOURCE:
        targets = [(FUND, f.id) for f in db.scalars(select(Fund).where(Fund.user_id == uid)) if rule_key(f.source) == rule.rule_key]
    else:
        targets = [(EXPENSE, e.id) for e in db.scalars(select(Expense).where(Expense.user_id == uid))
                   if rule_key(e.category) == rule.rule_key]
        targets += [
            (SUB_CHARGE, c.id)
            for c, cat in db.execute(
                select(SubscriptionCharge, Subscription.category).join(Subscription).where(Subscription.user_id == uid)
            )
            if rule_key(cat) == rule.rule_key
        ]
        if rule.rule_key.startswith("split bills: "):
            member_groups = select(SplitMember.group_id).where(SplitMember.user_id == uid)
            targets += [(SPLIT_BILL, b.id) for b in db.scalars(select(SplitBill).where(SplitBill.group_id.in_(member_groups)))
                        if rule_key(f"Split bills: {b.group.name}") == rule.rule_key]
    for stype, sid in targets:
        sync_source(db, stype, sid, strict=False, only_user=uid, report=report)
    db.flush()
    return report


# ---------- session hooks ----------
_PENDING = "acc_pending"
# The ImportRecord column that links a source to the import that created it
_IMPORT_COLUMN = {
    EXPENSE: ImportRecord.expense_id, FUND: ImportRecord.fund_id, TRANSFER: ImportRecord.transfer_id,
    SPLIT_BILL: ImportRecord.split_bill_id,
}


def _mark_accounts(session: Session, obj, include_old: bool) -> None:
    """An expense, money received or transfer changed: its accounts' ledgers may carry imported history in their
    opening balance, so they are brought up to date too (also the account it was moved away from)."""
    from sqlalchemy import inspect as sa_inspect

    cols = ("from_account_id", "to_account_id") if isinstance(obj, AccountTransfer) else ("account_id",)
    state = sa_inspect(obj)
    for col in cols:
        ids = {getattr(obj, col, None)}
        if include_old:
            ids |= set(state.attrs[col].history.deleted or ())
        for account_id in ids - {None}:
            _mark(session, (ACCOUNT, account_id))
_TRACKED = {
    Expense: EXPENSE, Fund: FUND, AccountTransfer: TRANSFER, SubscriptionCharge: SUB_CHARGE,
    SplitBill: SPLIT_BILL, SplitSettlement: SPLIT_SETTLEMENT,
}


def _mark(session: Session, key: tuple) -> None:
    session.info.setdefault(_PENDING, set()).add(key)


def _key_for(obj):
    if isinstance(obj, SplitShare):
        return (SPLIT_BILL, obj.bill_id) if obj.bill_id else None
    if isinstance(obj, Account):
        return (ACCOUNT, obj.id)
    stype = _TRACKED.get(type(obj))
    return (stype, obj.id) if stype and obj.id else None


@event.listens_for(Session, "before_flush")
def _before_flush(session: Session, flush_context, instances):
    """Deletions are noted before they run: a database cascade may null the links we need to follow."""
    for obj in list(session.deleted):
        if isinstance(obj, Account):
            _guard_account_delete(session, obj)
            continue
        key = _key_for(obj)
        if key:
            _mark(session, key)
        if isinstance(obj, (Expense, Fund, AccountTransfer)):
            _mark_accounts(session, obj, include_old=True)
        if isinstance(obj, Expense):
            # A subscription charge logged as this expense now has to carry the posting itself
            for cid in session.scalars(select(SubscriptionCharge.id).where(SubscriptionCharge.expense_id == obj.id)):
                _mark(session, (SUB_CHARGE, cid))


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, flush_context):
    for obj in session.new:
        key = _key_for(obj)
        if key:
            _mark(session, key)
    for obj in session.dirty:
        if session.is_modified(obj, include_collections=False):
            key = _key_for(obj)
            if key:
                _mark(session, key)
            if isinstance(obj, (Expense, Fund, AccountTransfer)):
                _mark_accounts(session, obj, include_old=True)


@event.listens_for(Session, "before_commit")
def _process_pending(session: Session):
    for _ in range(20):
        session.flush()
        pending = session.info.pop(_PENDING, None)
        if not pending:
            return
        for stype, sid in sorted(pending, key=lambda k: (k[0] != ACCOUNT, k[0], k[1])):
            if stype == ACCOUNT:
                account = session.get(Account, sid)
                books = get_books(session, account.user_id) if account else None
                if books:
                    sync_account_ledger(session, books, account)
            else:
                sync_source(session, stype, sid, strict=True)
    raise RuntimeError("Accounting auto-posting did not settle")


@event.listens_for(Session, "after_rollback")
def _forget_pending(session: Session):
    session.info.pop(_PENDING, None)


def _guard_account_delete(session: Session, account: Account) -> None:
    """An account whose ledger has entries can't be deleted (as in Tally): deactivate it instead."""
    ledger = session.scalar(select(AccLedger).where(AccLedger.account_id == account.id))
    if ledger is None:
        return
    used = session.scalar(select(VoucherLine.id).where(VoucherLine.ledger_id == ledger.id).limit(1))
    if used:
        raise AccountingError(
            f"'{account.name}' has entries in your accounting books, so it can't be deleted. Deactivate it instead."
        )
    session.delete(ledger)


def charge_day_locked(db: Session, user_id: int, day: date) -> bool:
    """Used by the subscription job: a charge in a locked period is skipped, not posted."""
    return is_locked(get_books(db, user_id), day)

