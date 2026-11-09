"""Check, import and undo past records.

Every row is checked before anything is saved: a date, an amount, a type, a category for spending, an account when
the file's account column is used, and the accounting lock. Duplicates are looked for in two places:
- rows imported before (same type, date, amount and description, and the same occurrence of it in its file), so
  uploading the same file again adds nothing;
- records already in the app on the same date for the same amount (typed in by hand, or from another file), which
  the user reviews: they are left out unless ticked.

Imported records are ordinary expenses and money received: they show on every page and post to the books. Their
original date is kept; the edit window starts on the day of the import; no budget alert is sent for them.
A split row becomes an expense for the user's share, and a split bill (paid by the user) for the part paid for
the others, who then owe it. A split someone else paid ("Paid by" column) becomes an expense for the user's share
(from no account: no money left the user's accounts) and a split bill paid by that person, which the user owes.
A transfer row ("Axis Bank->SBI Bank", or a "To account" column) becomes a transfer between the two accounts; an
opening-balance row ("Balance cf", "Opening balance") is added to the account's opening balance.
Accounts named in the file are matched by name or last 4 digits, or created by the import.
A category can be marked as not spending or income (investments, money paid for others and paid back): its rows
become transfers to or from an account such as "Investments" or "Money with others".
"""

import hashlib
import re
from collections import Counter, defaultdict
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..accounting.chart import fy_of, fy_start
from ..accounting.engine import get_books
from ..accounting.models import AccLedger, BankStatementLine, Voucher, VoucherLine
from ..config import settings
from ..deps import FEATURE_SPLIT, has_feature
from ..models import (
    ACCOUNT_KINDS, Account, AccountTransfer, Expense, Fund, SplitBill, SplitGroup, SplitMember, SplitShare, User,
)
from ..routers.expenses import user_categories
from ..services.aliases import find_alias, label_new, payee_key, suggest_alias, user_aliases
from ..services.accounts import balances
from ..services.splits import split_evenly
from ..timeutil import now_local, today_local
from .models import (
    COMPLETED, COMPLETED_WITH_ERRORS, FAILED, IN_PROGRESS, KIND_EXPENSE, KIND_INCOME, KIND_OPENING, KIND_SPLIT,
    KIND_TRANSFER, PARTIALLY_UNDONE,
    REC_FAILED, REC_IMPORTED, REC_KEPT, REC_SKIPPED, REC_UNDONE, UNDONE, ImportBatch, ImportRecord,
    CHOICE_ACCOUNT, CHOICE_CATEGORY, CHOICE_TREATMENT, ImportChoice,
)
from .parse import CENT, MAX_AMOUNT, is_me
from .schemas import ImportOptions, RecordIn

READY = "READY"
INVALID = "INVALID"
LOCKED = "LOCKED"
NEEDS_CATEGORY = "NEEDS_CATEGORY"
NEEDS_ACCOUNT = "NEEDS_ACCOUNT"
DUPLICATE = "DUPLICATE"
IMPORTED_BEFORE = "IMPORTED_BEFORE"
# A row imported before can't be imported again; a possible duplicate (same date and amount as a record already in
# the app) is left out unless the user ticks it
IMPORTABLE = (READY, DUPLICATE)
SPLIT_GROUP_NAME = "Imported split bills"
OTHERS = "Others"
_IN_CHUNK = 900  # Oracle allows at most 1000 values in an IN list
UNDO_CHUNK = 200  # records undone per request


def _bad(message: str, code=status.HTTP_400_BAD_REQUEST):
    raise HTTPException(code, message)


def _key(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def fingerprint_base(r: RecordIn) -> str | None:
    if not (r.kind and r.date and r.amount):
        return None
    base = f"{r.kind}|{r.date.isoformat()}|{Decimal(r.amount).quantize(CENT)}|{_key(r.description)}"
    if r.kind in (KIND_TRANSFER, KIND_OPENING):
        base += f"|{_akey(r.account)}>{_akey(r.to_account)}"
    return base


def _short(base: str) -> str:
    return hashlib.sha256(base.encode()).hexdigest()[:16]


def fingerprints(records: list[RecordIn]) -> list[str | None]:
    """One per record. Identical rows in a file get #1, #2…, so re-uploading the file matches them one to one."""
    seen: Counter = Counter()
    out = []
    for r in records:
        base = fingerprint_base(r)
        if base is None:
            out.append(None)
            continue
        seen[base] += 1
        # Kept when it still describes the row (the user may have edited the date or amount since)
        out.append(r.fingerprint if r.fingerprint and r.fingerprint.startswith(_short(base)) else
                   _short(base) + hashlib.sha256(f"{base}#{seen[base]}".encode()).hexdigest()[:48])
    return out


def _in_chunks(values):
    values = list(values)
    for i in range(0, len(values), _IN_CHUNK):
        yield values[i:i + _IN_CHUNK]


def _imported_fingerprints(db: Session, user_id: int, fps, exclude_batch: int | None) -> set[str]:
    found: set[str] = set()
    for part in _in_chunks({f for f in fps if f}):
        stmt = select(ImportRecord.fingerprint).where(
            ImportRecord.user_id == user_id, ImportRecord.status.in_((REC_IMPORTED, REC_KEPT)), ImportRecord.fingerprint.in_(part)
        )
        if exclude_batch:
            stmt = stmt.where(ImportRecord.batch_id != exclude_batch)
        found.update(db.scalars(stmt))
    return found


def _existing(db: Session, user_id: int, records: list[RecordIn], batch_id: int | None):
    """Records already in the app on the dates of the file: (kind, date, amount) → names, for duplicate checks.
    Records created by this same import (earlier chunks) don't count."""
    dates = [r.date for r in records if r.date]
    if not dates:
        return defaultdict(list)
    lo, hi = min(dates), max(dates)
    mine_exp, mine_fund, mine_tr = set(), set(), set()
    if batch_id:
        for e, f, t in db.execute(select(ImportRecord.expense_id, ImportRecord.fund_id, ImportRecord.transfer_id)
                                  .where(ImportRecord.batch_id == batch_id)):
            mine_exp.add(e)
            mine_fund.add(f)
            mine_tr.add(t)
    out: dict[tuple, list[str]] = defaultdict(list)
    for e in db.execute(select(Expense.id, Expense.expense_date, Expense.amount, Expense.item_name).where(
            Expense.user_id == user_id, Expense.expense_date >= lo, Expense.expense_date <= hi)):
        if e.id not in mine_exp:
            out[(KIND_EXPENSE, e.expense_date, Decimal(e.amount))].append(e.item_name)
    for f in db.execute(select(Fund.id, Fund.fund_date, Fund.amount, Fund.source).where(
            Fund.user_id == user_id, Fund.fund_date >= lo, Fund.fund_date <= hi)):
        if f.id not in mine_fund:
            out[(KIND_INCOME, f.fund_date, Decimal(f.amount))].append(f.source)
    for t in db.execute(select(AccountTransfer.id, AccountTransfer.transfer_date, AccountTransfer.amount,
                               AccountTransfer.from_account_id, AccountTransfer.to_account_id, AccountTransfer.note)
                        .where(AccountTransfer.user_id == user_id, AccountTransfer.transfer_date >= lo,
                               AccountTransfer.transfer_date <= hi)):
        if t.id not in mine_tr:
            out[(KIND_TRANSFER, t.transfer_date, Decimal(t.amount), t.from_account_id, t.to_account_id)].append(t.note or "a transfer")
    return out


def _akey(text: str | None) -> str:
    """An account name compared loosely: ".Credit Card", "credit card" and "CREDIT-CARD" are the same account."""
    return re.sub(r"[^0-9a-z]+", "", (text or "").casefold())


def clean_account_name(raw: str) -> str:
    return re.sub(r"^[^\w]+", "", raw or "").strip()[:60] or (raw or "").strip()[:60]


def suggest_kind(name: str) -> str:
    n = (name or "").casefold()
    for pattern, kind in _KIND_HINTS:
        if re.search(pattern, n):
            return kind
    return "SAVINGS"


_KIND_HINTS = (
    (r"credit|card", "CREDIT_CARD"),
    (r"\bcash\b|petty", "CASH"),
    (r"wallet|paytm|phonepe|gpay|google pay|amazon pay|upi|fas\s*tag|toll|prepaid|freecharge|mobikwik|sodexo|pluxee", "WALLET"),
    (r"invest|mutual|groww|zerodha|demat|broker|\bmf\b|stock|share|\bppf\b|\bpf\b|\bfd\b|deposit", "INVESTMENT"),
    (r"lent|loan to|receivable|friends|others", "RECEIVABLE"),
)
# Suggested category for a category that isn't in the app, when one of these defaults exists. "Sure" suggestions
# are applied by "Use the suggestions"; the others are only shown as "maybe" for the user to confirm (a "Mobile"
# category may hold recharges or a new phone; gifts aren't really shopping)
_CATEGORY_HINTS = (
    (r"fuel|petrol|diesel|\bcng\b|cab\b|taxi|\bauto\b|uber|\bola\b|metro|\bbus\b|train|transport|parking|fastag|toll|travel",
     "Transport", True),
    (r"grocer|vegetable|kirana|supermarket|mart\b", "Groceries", True),
    (r"food|restaurant|dining|snack|cafe|zomato|swiggy|lunch|dinner|breakfast|tiffin|eat", "Food", True),
    (r"electric|water\b|gas\b|recharge|internet|broadband|\bdth\b|cable|bill|rent|utilit", "Bills", True),
    (r"doctor|medic|health|pharma|hospital|clinic|dental|gym|fitness", "Health", True),
    (r"movie|entertain|netflix|prime|ott|game|music|concert|party", "Entertainment", True),
    (r"cloth|apparel|shoe|shopping|amazon|flipkart|myntra", "Shopping", True),
    (r"cigar|smok|tobacco", "Smoking", True),
    (r"mobile|phone", "Bills", False),
    (r"beauty|cosmetic|electronic|gadget|gift", "Shopping", False),
)
# A row this much bigger than the usual one of its category is flagged for the user to check its category
_LARGE_ROW = Decimal(10_000)
_LARGE_FACTOR = 20
# Categories that are usually not spending or income: suggested as transfers to an account of this kind
_TREATMENT_HINTS = (
    (r"invest|mutual|\bsip\b|\bshares?\b|stock|f\s*&\s*o|trading|demat|\bpf\b|\bppf\b|\bfd\b|fixed deposit|\bipo\b|"
     r"brokerage|bal tnf", "INVESTMENT", "Investments"),
    (r"reimburse|on behalf|money return|returned|\blent\b|\blend|borrow|money received|money given|cash received|"
     r"paid back|transfer money and received cash|ticket money", "RECEIVABLE", "Money with others"),
)


def suggest_category(raw: str, known: dict[str, str], sure: bool = True) -> str | None:
    """The suggested app category: a sure one (sure=True), or a "maybe" for the user to confirm (sure=False)."""
    n = (raw or "").casefold()
    for pattern, category, strong in _CATEGORY_HINTS:
        if re.search(pattern, n):
            if strong != sure:
                return None
            return known.get(category.casefold())
    return None


def suggest_treatment(raw: str) -> str | None:
    n = (raw or "").casefold()
    for pattern, kind, name in _TREATMENT_HINTS:
        if re.search(pattern, n):
            return f"new:{kind}:{name}"
    return None


class _Accounts:
    """Resolves account names from a file to the user's accounts (or to accounts the import will create).

    A reference is "id:<n>", "new:<KIND>:<name>", "none" (no account) or None (not resolved: the user must choose)."""

    def __init__(self, accounts: list[Account], opts: ImportOptions):
        self.accounts = accounts
        self.by_key = {_akey(a.name): a for a in accounts}
        self.ids = {a.id for a in accounts}
        self.map = {_akey(k): v for k, v in opts.account_map.items()}
        self.create = {_akey(k): v for k, v in opts.create_accounts.items()}
        for v in self.map.values():
            if v is not None and v not in self.ids:
                _bad("An account chosen for the file's account names isn't one of yours")
        for v in self.create.values():
            if v not in ACCOUNT_KINDS:
                _bad("Choose a valid type for the new account")
        self.seen: Counter = Counter()

    def resolve(self, raw: str | None, count: bool = True) -> str | None:
        raw = (raw or "").strip()
        if count:
            self.seen[raw] += 1
        k = _akey(raw)
        if k in self.map:
            v = self.map[k]
            return f"id:{v}" if v is not None else "none"
        if k and k in self.by_key:
            return f"id:{self.by_key[k].id}"
        digits = "".join(c for c in raw if c.isdigit())
        if len(digits) >= 4:
            hits = [a for a in self.accounts if a.last4 and digits.endswith(a.last4)]
            if len(hits) == 1:
                return f"id:{hits[0].id}"
        if k and k in self.create:
            return f"new:{self.create[k]}:{clean_account_name(raw)}"
        return None

    def treatment(self, ref: str | None) -> str | None:
        """A category's "move it to this account" choice, validated."""
        if not ref:
            return None
        if ref.startswith("id:"):
            if not ref[3:].isdigit() or int(ref[3:]) not in self.ids:
                _bad("An account chosen for a category isn't one of yours")
            return ref
        parts = ref.split(":", 2)
        if len(parts) != 3 or parts[0] != "new" or parts[1] not in ACCOUNT_KINDS or not parts[2].strip():
            _bad("Invalid account for a category")
        existing = self.by_key.get(_akey(parts[2]))
        return f"id:{existing.id}" if existing else f"new:{parts[1]}:{parts[2].strip()[:60]}"

    def label(self, ref: str | None) -> str | None:
        if ref is None:
            return None
        if ref == "none":
            return "No account"
        if ref.startswith("id:"):
            a = next((a for a in self.accounts if a.id == int(ref[3:])), None)
            return a.name if a else None
        return f"{ref.split(':', 2)[2]} (new)"

    def values(self) -> list[dict]:
        out = []
        for v, n in sorted(self.seen.items(), key=lambda x: (-x[1], x[0])):
            ref = self.resolve(v, count=False)
            k = _akey(v)
            out.append({
                "value": v, "rows": n, "account_id": int(ref[3:]) if ref and ref.startswith("id:") else None,
                "ref": ref, "mapped": k in self.map, "create_kind": self.create.get(k),
                "suggested_kind": suggest_kind(v), "new_name": clean_account_name(v), "matched_name": self.label(ref)
                if ref and ref.startswith("id:") else None,
                "matched_inactive": bool(ref and ref.startswith("id:") and not next(
                    (a.is_active for a in self.accounts if a.id == int(ref[3:])), True)),
            })
        return out


def _member_groups(db: Session, user_id: int) -> list[SplitGroup]:
    ids = select(SplitMember.group_id).where(SplitMember.user_id == user_id, SplitMember.left_at.is_(None))
    return list(db.scalars(select(SplitGroup).where(SplitGroup.id.in_(ids)).order_by(SplitGroup.name)))


def _ref_id(ref: str | None) -> int | None:
    return int(ref[3:]) if ref and ref.startswith("id:") else None


def check(db: Session, user: User, records: list[RecordIn], opts: ImportOptions, batch_id: int | None = None,
          fresh: bool = False) -> dict:
    """Check every record. `fresh` (a first preview): each record's `include` becomes its default."""
    if len(records) > settings.import_max_records:
        _bad(f"This file has {len(records)} records; up to {settings.import_max_records:,} can be imported at once. Split the file.")
    today = today_local()
    books = get_books(db, user.id)
    lock = books.lock_date if books else None
    accounts = list(db.scalars(select(Account).where(Account.user_id == user.id).order_by(Account.name)))
    acc = _Accounts(accounts, opts)
    primary = next((a.id for a in accounts if a.is_primary and a.is_active), None)
    if opts.account_mode == "fixed" and opts.account_id not in acc.ids:
        _bad("Choose one of your accounts for this upload")
    known = {c.casefold(): c for c in user_categories(db, user.id)}
    cat_map = {_key(k): v.strip() for k, v in opts.category_map.items()}
    treat = {_key(k): acc.treatment(v) for k, v in opts.category_treatment.items() if v}
    split_ok = has_feature(user, FEATURE_SPLIT)
    fps = fingerprints(records)
    imported_before = _imported_fingerprints(db, user.id, fps, batch_id)
    existing = _existing(db, user.id, records, batch_id)
    my_names = {_key(user.full_name), _key(user.username)} - {""}
    aliases = user_aliases(db, user.id)
    payees: dict[str, dict] = {}

    def payer_of(r: RecordIn) -> str | None:
        """Who paid a split row: None = the user."""
        if r.kind != KIND_SPLIT or not r.paid_by or is_me(r.paid_by) or _key(r.paid_by) in my_names:
            return None
        return " ".join(r.paid_by.split())[:100]

    def row_account(r: RecordIn) -> str | None:
        if opts.account_mode == "column":
            return acc.resolve(r.account)
        if opts.account_mode == "fixed":
            return f"id:{opts.account_id}"
        if opts.account_mode == "none":
            return "none"
        return f"id:{primary}" if primary else "none"

    amounts: dict[str, list] = defaultdict(list)
    for r in records:
        if r.kind in (KIND_EXPENSE, KIND_SPLIT) and r.amount and r.amount > 0:
            amounts[_key(r.category)].append(r.amount)
    typical = {k: sorted(v)[len(v) // 2] for k, v in amounts.items() if len(v) >= 4}

    out = []
    cat_stats: dict[str, Counter] = defaultdict(Counter)
    first_row: dict[str, int] = {}
    for r, fp in zip(records, fps):
        problems, warnings = [], list(r.parse_warnings)
        base = fingerprint_base(r)
        if base and base in first_row:
            warnings.append(f"Same as row {first_row[base]} of this file (both are imported unless you untick one)")
        elif base:
            first_row[base] = r.row_no
        state = READY

        def parse_msg(*words, default):
            return next((e for e in r.parse_errors if any(w in e.lower() for w in words)), default)

        if r.date is None:
            problems.append(parse_msg("date", default="Missing date"))
        elif r.date > today:
            problems.append("Date is in the future")
        if r.kind is None and r.amount is not None:
            problems.append(parse_msg("type", "debit and a credit", default="Choose expense, income, transfer or split"))
        if r.amount is None:
            problems.append(parse_msg("amount", "decimal", default="Missing amount"))
        elif r.amount == 0 or (r.amount < 0 and r.kind != KIND_OPENING):
            problems.append("Amount must be more than zero")
        elif abs(r.amount) > MAX_AMOUNT:
            problems.append("Amount is too large")
        elif r.amount != r.amount.quantize(CENT):
            problems.append("Amount has more than 2 decimal places")
        if r.kind == KIND_SPLIT:
            if not split_ok:
                problems.append("Split bills aren't on your plan: import this row as an expense of your share")
            if r.my_share is None:
                problems.append(f"{payer_of(r)} paid: enter your share of it" if payer_of(r) else "A split bill needs your share")
            elif r.my_share < 0 or r.my_share != r.my_share.quantize(CENT):
                problems.append("Your share must be zero or more, with at most 2 decimals")
            elif r.amount is not None and r.my_share > r.amount:
                problems.append("Your share is more than the amount paid")
            elif payer_of(r) and r.my_share == 0:
                problems.append(f"{payer_of(r)} paid and your share is zero: there is nothing to import")
        description = " ".join(r.description.split())
        if len(description) > 100:
            warnings.append("Description shortened to 100 characters")

        # Where the money is: one account, or two for a transfer (also a category treated as a transfer)
        raw_cat = (r.category or "").strip()
        ck = _key(raw_cat)
        treated = treat.get(ck) if r.kind in (KIND_EXPENSE, KIND_INCOME) else None
        account_ref = to_ref = None
        mapping_problems = []
        if r.kind == KIND_TRANSFER:
            if not (r.account and r.to_account):
                problems.append(parse_msg("transfer", default="A transfer needs the account it came from and the one it went to"))
            else:
                account_ref, to_ref = acc.resolve(r.account), acc.resolve(r.to_account)
                for raw, ref in ((r.account, account_ref), (r.to_account, to_ref)):
                    if ref is None or ref == "none":
                        mapping_problems.append((NEEDS_ACCOUNT, f"Choose or create the account “{raw}”"))
                if account_ref and account_ref == to_ref and account_ref != "none":
                    problems.append("Both sides of the transfer are the same account")
        elif r.kind == KIND_OPENING:
            account_ref = acc.resolve(r.account) if r.account else row_account(r)
            if account_ref is None or account_ref == "none":
                mapping_problems.append((NEEDS_ACCOUNT, f"Choose or create the account “{r.account or ''}”"))
        elif payer_of(r):
            account_ref = "none"  # someone else paid: no money left the user's accounts
        elif r.kind in (KIND_EXPENSE, KIND_INCOME, KIND_SPLIT):
            account_ref = row_account(r)
            if account_ref is None:
                raw = (r.account or "").strip()
                mapping_problems.append((NEEDS_ACCOUNT, f"Choose or create the account “{raw}”" if raw else "Choose the account for rows without one"))
            if treated:
                if account_ref == "none":
                    mapping_problems.append((NEEDS_ACCOUNT, "Choose the account the money moved from or to"))
                account_ref, to_ref = (account_ref, treated) if r.kind == KIND_EXPENSE else (treated, account_ref)

        if problems:
            state = INVALID
        elif lock and r.kind == KIND_OPENING:
            state = LOCKED
            problems.append(f"Your books are locked (up to {lock:%d %b %Y}), so opening balances can't change")
        elif lock and r.date <= lock:
            state = LOCKED
            problems.append(f"Your books are locked up to {lock:%d %b %Y}: records on or before it can't be added")
        elif fp in imported_before:
            # Shown as such even before its category is chosen; a category is only needed to import it again
            state = IMPORTED_BEFORE
            warnings.append("Already imported from an earlier upload")

        # The payee (UPI ID or name in the description) and the alias that will name it
        key = alias = None
        if r.kind in (KIND_EXPENSE, KIND_INCOME, KIND_SPLIT) and not treated and description:
            key = payee_key(description)
            alias = find_alias(aliases, key, description)
            if key and state not in (INVALID,):
                p = payees.setdefault(key, {
                    "payee_key": key, "description": description[:100], "sent_count": 0, "sent_total": Decimal(0),
                    "received_count": 0, "received_total": Decimal(0), "alias_id": alias and alias.id,
                    "alias": alias and alias.alias, "alias_category": alias and alias.category,
                    "suggestion": alias.alias if alias else suggest_alias(key, description),
                })
                side = "received" if r.kind == KIND_INCOME else "sent"
                p[f"{side}_count"] += 1
                p[f"{side}_total"] += abs(r.amount or 0)
        # A row without a category of its own takes its alias's category, so it needs no mapping
        alias_category = alias.category if alias and alias.category and not raw_cat else None

        category = None
        if r.kind in (KIND_EXPENSE, KIND_INCOME, KIND_SPLIT) and not (alias_category and r.kind != KIND_INCOME):
            stats = cat_stats[raw_cat]
            stats["rows"] += 1
            stats["income_rows" if r.kind == KIND_INCOME else "expense_rows"] += 1
        if r.kind in (KIND_EXPENSE, KIND_SPLIT) and not treated:
            if alias_category:
                category = alias_category[:50]
            elif ck in cat_map and cat_map[ck]:
                category = cat_map[ck][:50]
            elif ck and ck in known:
                category = known[ck]
            elif state in (READY, IMPORTED_BEFORE):
                mapping_problems.append((NEEDS_CATEGORY, f"Choose a category for “{raw_cat}”" if raw_cat else "Choose a category for rows without one"))
        elif r.kind == KIND_INCOME and not treated:
            # Money received is named by its source: the file's category, else (a bank statement) its description
            category = ((cat_map.get(ck) or raw_cat) if raw_cat else (description or cat_map.get(ck) or "Income"))[:100]

        can_map = not mapping_problems
        if mapping_problems and state == READY:
            state = mapping_problems[0][0]
            problems += [m for _, m in mapping_problems]
        elif mapping_problems:  # imported before: to import it again, a category / account is still needed
            warnings += [m for _, m in mapping_problems]
        if state == READY:
            dup = None
            if r.kind == KIND_TRANSFER or treated:
                if _ref_id(account_ref) and _ref_id(to_ref):
                    dup = existing.get((KIND_TRANSFER, r.date, r.amount, _ref_id(account_ref), _ref_id(to_ref)))
            elif r.kind in (KIND_EXPENSE, KIND_INCOME, KIND_SPLIT):
                amount = r.my_share if r.kind == KIND_SPLIT else r.amount
                dup = existing.get((KIND_INCOME if r.kind == KIND_INCOME else KIND_EXPENSE, r.date, amount))
            if dup:
                state = DUPLICATE
                warnings.append(f"Possible duplicate: “{dup.pop(0)}” for ₹{abs(r.amount):,.2f} on {r.date:%d %b %Y} is already in the app")
        if payer_of(r) and state in (READY, DUPLICATE, IMPORTED_BEFORE):
            warnings.append(f"Paid by {payer_of(r)}: you owe them your share")
        if treated and state in (READY, DUPLICATE, IMPORTED_BEFORE):
            warnings.append(f"Not spending or income: moved {'to' if r.kind == KIND_EXPENSE else 'from'} {acc.label(treated)}")

        to_check = bool(r.kind in (KIND_EXPENSE, KIND_SPLIT) and not treated and r.amount and ck in typical
                        and r.amount >= max(_LARGE_ROW, typical[ck] * _LARGE_FACTOR) and state != IMPORTED_BEFORE)
        if to_check:
            warnings.append(f"Much bigger than the other “{raw_cat}” rows (usually about ₹{typical[ck]:,.0f}): "
                            "check that this is the right category")
        can = state in IMPORTABLE and can_map
        default = can and state == READY and not r.parse_warnings
        out.append({
            **r.model_dump(), "paid_by": payer_of(r), "status": state, "problems": problems, "warnings": warnings, "can_import": can,
            "default_include": default, "category_final": category, "fingerprint": fp,
            "include": default if fresh else r.include,
            "effective_kind": KIND_TRANSFER if treated else r.kind,
            "account_ref": account_ref, "to_account_ref": to_ref,
            "account_id": _ref_id(account_ref), "to_account_id": _ref_id(to_ref),
            "account_label": acc.label(account_ref), "to_account_label": acc.label(to_ref), "to_check": to_check,
            "payee_key": key, "alias_id": alias and alias.id, "alias_name": alias and alias.alias,
        })

    counts = Counter(o["status"] for o in out)
    chosen = [o for o in out if o["can_import"] and o["include"]]
    category_values = [
        {"value": v, "rows": s["rows"], "expense_rows": s["expense_rows"], "income_rows": s["income_rows"],
         "mapped_to": cat_map.get(_key(v)) or known.get(_key(v)), "known": _key(v) in known,
         "treatment": treat.get(_key(v)), "suggested": suggest_category(v, known),
         "maybe": suggest_category(v, known, sure=False), "suggested_treatment": suggest_treatment(v)}
        for v, s in sorted(cat_stats.items(), key=lambda x: (-x[1]["rows"], x[0]))
    ]
    account_values = acc.values()
    kinds = Counter(o["effective_kind"] for o in chosen)
    # Records older than the books move the books' start back to their financial year, so nothing migrated is lost
    earliest = min((o["date"] for o in chosen if o["date"]), default=None)
    books_moves_to = (
        fy_start(fy_of(earliest)) if books and earliest and earliest < books.books_start else None
    )
    return {
        "records": out,
        "summary": {
            "total": len(out), "ready": counts[READY], "invalid": counts[INVALID], "locked": counts[LOCKED],
            "needs_category": counts[NEEDS_CATEGORY], "needs_account": counts[NEEDS_ACCOUNT],
            "duplicates": counts[DUPLICATE], "imported_before": counts[IMPORTED_BEFORE],
            "chosen": len(chosen), "transfers": kinds[KIND_TRANSFER], "openings": kinds[KIND_OPENING],
            "expense_total": str(sum((Decimal(o["amount"]) for o in chosen if o["effective_kind"] in (KIND_EXPENSE, KIND_SPLIT)), Decimal(0))),
            "income_total": str(sum((Decimal(o["amount"]) for o in chosen if o["effective_kind"] == KIND_INCOME), Decimal(0))),
            "transfer_total": str(sum((Decimal(o["amount"]) for o in chosen if o["effective_kind"] == KIND_TRANSFER), Decimal(0))),
            "to_check": sum(1 for o in out if o["to_check"]),
            "account_warnings": _below_zero(db, user.id, accounts, chosen),
        },
        "categories": sorted(known.values(), key=str.casefold),
        "category_values": category_values,
        "account_values": account_values,
        "remembered": _remembered(db, user.id, accounts, category_values, account_values),
        "payees": sorted(
            ({**p, "sent_total": str(p["sent_total"]), "received_total": str(p["received_total"])} for p in payees.values()),
            key=lambda p: (-(p["sent_count"] + p["received_count"]), p["payee_key"]),
        ),
        "accounts": [{"id": a.id, "name": a.name, "last4": a.last4, "kind": a.kind, "is_primary": a.is_primary}
                     for a in accounts if a.is_active],
        "account_kinds": list(ACCOUNT_KINDS),
        "split_groups": [{"id": g.id, "name": g.name} for g in _member_groups(db, user.id)],
        "split_enabled": split_ok,
        "lock_date": lock,
        "books_start": books.books_start if books else None,
        "books_moves_to": books_moves_to,
        "before_books": sum(1 for o in chosen if books_moves_to and o["date"] and o["date"] < books.books_start),
        "max_records": settings.import_max_records,
    }


# Accounts that can't normally go below zero (a card or a loan can)
_NO_OVERDRAFT = {"SAVINGS", "CURRENT", "SALARY", "CASH", "WALLET"}


def _below_zero(db: Session, user_id: int, accounts: list[Account], chosen: list[dict]) -> list[dict]:
    """Accounts (bank, cash, wallet) that would end below zero once the chosen rows are imported: usually rows
    missing from the file (a wallet's top-ups, a bank's own statement), which the user is told to import too."""
    change: dict[str, Decimal] = defaultdict(Decimal)
    for o in chosen:
        amount = Decimal(o["amount"])
        src, dst = o["account_ref"], o["to_account_ref"]
        if o["effective_kind"] == KIND_TRANSFER:
            change[src] -= amount
            change[dst] += amount
        elif o["kind"] == KIND_OPENING or o["effective_kind"] == KIND_INCOME:
            change[src] += amount
        elif o["effective_kind"] in (KIND_EXPENSE, KIND_SPLIT):
            change[src] -= Decimal(o["my_share"]) if o["kind"] == KIND_SPLIT and o["my_share"] is not None else amount
    by_id = {a.id: a for a in accounts}
    now = balances(db, user_id, today_local().replace(day=1)) if any(r and r.startswith("id:") for r in change) else {}
    out = []
    for ref, delta in change.items():
        if not ref or ref == "none":
            continue
        if ref.startswith("id:"):
            account = by_id.get(int(ref[3:]))
            if account is None:
                continue
            kind, name, start = account.kind, account.name, now.get(account.id, {}).get("balance", Decimal(0))
        else:
            _, kind, name = ref.split(":", 2)
            start = Decimal(0)
        end = start + delta
        if kind in _NO_OVERDRAFT and end < 0:
            out.append({"account": name, "kind": kind, "balance": str(end.quantize(CENT))})
    return sorted(out, key=lambda x: x["account"].casefold())


def _remembered(db: Session, user_id: int, accounts: list[Account], category_values: list[dict],
                account_values: list[dict]) -> dict:
    """The choices made in the user's earlier imports for the categories and accounts of this file, keyed like the
    page's options (the value in lower case). Accounts no longer active are left out."""
    saved = {(c.kind, c.source_key): c.value for c in db.scalars(select(ImportChoice).where(ImportChoice.user_id == user_id))}
    if not saved:
        return {}
    active = {a.id for a in accounts if a.is_active}

    def ok_ref(ref: str) -> bool:
        if ref.startswith("id:"):
            return ref[3:].isdigit() and int(ref[3:]) in active
        return ref == "none" or ref.startswith("new:")

    out = {"category_map": {}, "category_treatment": {}, "account_map": {}}
    for c in category_values:
        v, k = c["value"], _key(c["value"])
        if (CHOICE_CATEGORY, k) in saved and c["expense_rows"] and not c["known"]:
            out["category_map"][v.lower()] = saved[(CHOICE_CATEGORY, k)]
        if (CHOICE_TREATMENT, k) in saved and ok_ref(saved[(CHOICE_TREATMENT, k)]) and saved[(CHOICE_TREATMENT, k)] != "none":
            out["category_treatment"][v.lower()] = saved[(CHOICE_TREATMENT, k)]
    for a in account_values:
        ref = saved.get((CHOICE_ACCOUNT, _akey(a["value"])))
        if ref and ok_ref(ref) and not ref.startswith("new:") and a["ref"] is None:
            out["account_map"][a["value"].lower()] = None if ref == "none" else int(ref[3:])
    return {k: v for k, v in out.items() if v}


def _remember(db: Session, user: User, opts: ImportOptions) -> None:
    """Keep this import's choices for the next one (an account created by the import is kept as that account)."""
    accounts = {_akey(a.name): a for a in db.scalars(select(Account).where(Account.user_id == user.id))}
    want: dict[tuple[str, str], str] = {}
    for k, v in opts.category_map.items():
        if v and v.strip() and _key(k):
            want[(CHOICE_CATEGORY, _key(k))] = v.strip()[:50]
    for k, v in opts.category_treatment.items():
        if _key(k):
            ref = v or "none"
            if ref.startswith("new:") and len(ref.split(":", 2)) == 3 and _akey(ref.split(":", 2)[2]) in accounts:
                ref = f"id:{accounts[_akey(ref.split(':', 2)[2])].id}"
            want[(CHOICE_TREATMENT, _key(k))] = ref[:100]
    for k, v in opts.account_map.items():
        if _akey(k):
            want[(CHOICE_ACCOUNT, _akey(k))] = "none" if v is None else f"id:{v}"
    for k in opts.create_accounts:
        a = accounts.get(_akey(clean_account_name(k)))
        if a is not None and _akey(k):
            want[(CHOICE_ACCOUNT, _akey(k))] = f"id:{a.id}"
    if not want:
        return
    have = {(c.kind, c.source_key): c for c in db.scalars(select(ImportChoice).where(ImportChoice.user_id == user.id))}
    for (kind, key), value in want.items():
        key = key[:100]
        row = have.get((kind, key))
        if row is None:
            db.add(ImportChoice(user_id=user.id, kind=kind, source_key=key, value=value))
        elif row.value != value:
            row.value, row.updated_at = value, now_local()


# ---------- importing ----------
def _split_group(db: Session, user: User, group_id: int | None) -> tuple[SplitGroup, SplitMember]:
    if group_id:
        group = db.get(SplitGroup, group_id)
        me = next((m for m in group.members if m.user_id == user.id and m.active), None) if group else None
        if me is None:
            _bad("Choose a split group you are a member of")
        return group, me
    group = db.scalar(select(SplitGroup).where(SplitGroup.owner_id == user.id, SplitGroup.name == SPLIT_GROUP_NAME))
    if group is None:
        group = SplitGroup(name=SPLIT_GROUP_NAME, description="Split bills imported from past records", owner_id=user.id)
        group.members.append(SplitMember(user_id=user.id, is_admin=True, added_by=user.id))
        db.add(group)
        db.flush()
    me = next(m for m in group.members if m.user_id == user.id)
    if not me.active:
        me.left_at = None
    return group, me


def _participant(db: Session, group: SplitGroup, name: str, user: User) -> SplitMember:
    key = _key(name)
    for m in group.members:
        if m.user_id != user.id and (_key(m.guest_name) == key or (m.user and key in (_key(m.user.full_name), _key(m.user.username)))):
            if not m.active:
                m.left_at = None
            return m
    member = SplitMember(guest_name=name[:100], added_by=user.id)
    group.members.append(member)
    db.flush()
    return member


def _account(db: Session, user: User, ref: str | None, ctx: dict) -> Account | None:
    """The account of a reference, creating a "new:KIND:name" one the first time it is needed."""
    if ref is None or ref == "none":
        return None
    cache = ctx.setdefault("accounts", {})
    if ref in cache:
        return cache[ref]
    if ref.startswith("id:"):
        account = db.get(Account, int(ref[3:]))
        if account is None or account.user_id != user.id:
            _bad("An account in this import no longer exists")
        account.is_active = True  # e.g. set aside by the Undo of an earlier import
    else:
        _, kind, name = ref.split(":", 2)
        account = next((a for a in db.scalars(select(Account).where(Account.user_id == user.id)) if _akey(a.name) == _akey(name)), None)
        if account is None:
            account = Account(user_id=user.id, name=name[:60], kind=kind, opening_balance=Decimal(0), is_primary=False)
            db.add(account)
            db.flush()
            ctx.setdefault("created_accounts", []).append(account.name)
            ctx.setdefault("created_account_ids", []).append(account.id)
        else:
            account.is_active = True
    cache[ref] = account
    return account


def _create(db: Session, user: User, r: dict, ctx: dict) -> dict:
    d = r["date"]
    full = " ".join((r["description"] or "").split())
    description = full[:100]
    amount = Decimal(r["amount"])
    note = r["note"]
    if len(full) > 100:  # the description holds 100 characters: the whole text goes in the note
        note = " – ".join(x for x in (full, note) if x)[:500]
    if r["kind"] == KIND_OPENING:
        account = _account(db, user, r["account_ref"], ctx)
        account.opening_balance = Decimal(account.opening_balance or 0) + amount
        db.flush()
        return {"account_id": account.id}
    if r["effective_kind"] == KIND_TRANSFER:
        src, dst = _account(db, user, r["account_ref"], ctx), _account(db, user, r["to_account_ref"], ctx)
        text = (note if len(full) > 100 else " – ".join(x for x in (description, note) if x)) or None
        transfer = AccountTransfer(user_id=user.id, from_account_id=src.id, to_account_id=dst.id, amount=amount,
                                   transfer_date=d, note=text and text[:200])
        db.add(transfer)
        db.flush()
        return {"transfer_id": transfer.id}
    r = {**r, "account_id": _account(db, user, r["account_ref"], ctx).id if r["account_ref"] not in (None, "none") else None}
    if r["kind"] == KIND_INCOME:
        source = (r.get("category_final") or r["category"] or "").strip()[:100] or "Income"
        # The description is the name when there was no category (the rest of a long one goes in the note);
        # otherwise the description goes in the note, in full
        shown = (full if len(full) > 100 else None) if source == description[:100] else full
        fund_note = " – ".join(x for x in (shown, r["note"]) if x)[:500] or None
        fund = Fund(user_id=user.id, fund_date=d, amount=amount, source=source, note=fund_note, account_id=r["account_id"])
        label_new(db, fund, aliases=_aliases(db, user, ctx), text=description or source)
        db.add(fund)
        db.flush()
        return {"fund_id": fund.id}
    item = description or r["category_final"] or "Imported expense"
    links = {}
    if r["kind"] == KIND_SPLIT and r.get("paid_by"):
        share = Decimal(r["my_share"])
        if "group" not in ctx:
            ctx["group"] = _split_group(db, user, ctx["group_id"])
        group, me = ctx["group"]
        payer = _participant(db, group, r["paid_by"], user)
        bill = SplitBill(group_id=group.id, title=item, total_amount=share, bill_date=d, paid_by=payer.id, created_by=user.id,
                         note=f"Imported: {r['paid_by']} paid ₹{amount:,.2f}, your share ₹{share:,.2f}"[:500])
        bill.shares.append(SplitShare(member_id=me.id, share_type="EQUAL", share_value=None, amount=share))
        db.add(bill)
        db.flush()
        links["split_bill_id"] = bill.id
        amount = share
        note = " – ".join(x for x in (note, f"Your share of ₹{Decimal(r['amount']):,.2f} paid by {r['paid_by']}") if x)[:500]
    elif r["kind"] == KIND_SPLIT:
        share = Decimal(r["my_share"])
        others = amount - share
        if others > 0:
            if "group" not in ctx:
                ctx["group"] = _split_group(db, user, ctx["group_id"])
            group, me = ctx["group"]
            people = [_participant(db, group, n, user) for n in (r["split_with"] or [OTHERS])]
            people = list({p.id: p for p in people}.values())
            bill = SplitBill(group_id=group.id, title=item, total_amount=others, bill_date=d, paid_by=me.id, created_by=user.id,
                             note=f"Imported: paid ₹{amount:,.2f}, your share ₹{share:,.2f}"[:500])
            for p, part in zip(people, split_evenly(others, len(people))):
                bill.shares.append(SplitShare(member_id=p.id, share_type="EQUAL", share_value=None, amount=part))
            db.add(bill)
            db.flush()
            links["split_bill_id"] = bill.id
        if share == 0:
            return links
        amount = share
        note = " – ".join(x for x in (note, f"Your share of ₹{Decimal(r['amount']):,.2f} split bill") if x)[:500]
    expense = Expense(user_id=user.id, expense_date=d, item_name=item, category=r["category_final"], quantity=r["quantity"],
                      amount=amount, note=note, is_extra=False, account_id=r["account_id"], created_by=user.id)
    # The category was mapped during the review, so the alias only renames
    label_new(db, expense, set_category=False, aliases=_aliases(db, user, ctx))
    db.add(expense)
    db.flush()
    links["expense_id"] = expense.id
    return links


def _aliases(db: Session, user, ctx: dict):
    if "aliases" not in ctx:
        ctx["aliases"] = user_aliases(db, user.id)
    return ctx["aliases"]


def _record(batch: ImportBatch, r: dict, status_: str, reason: str | None, **links) -> ImportRecord:
    return ImportRecord(
        batch_id=batch.id, user_id=batch.user_id, row_no=r["row_no"], kind=r["kind"], status=status_,
        reason=(reason or None) and reason[:500], record_date=r["date"], description=(r["description"] or "")[:100] or None,
        amount=r["amount"] if r["amount"] is None or abs(Decimal(r["amount"])) <= MAX_AMOUNT else None,
        fingerprint=r["fingerprint"] if status_ == REC_IMPORTED else None, **links,
    )


def import_chunk(db: Session, user: User, batch: ImportBatch, records: list[RecordIn], opts: ImportOptions) -> dict:
    """Import one chunk of rows (all of it or none of it). Rows that can't be imported are recorded with the reason."""
    if batch.status != IN_PROGRESS:
        _bad("This import is already finished")
    if len(records) + batch.rows_total > settings.import_max_records:
        _bad(f"Up to {settings.import_max_records:,} records can be imported at once")
    checked = check(db, user, records, opts, batch_id=batch.id)["records"]
    ctx = {"group_id": opts.split_group_id}
    counts = Counter()
    exp_total = inc_total = Decimal(0)
    try:
        for r in checked:
            if r["can_import"] and r["include"]:
                links = _create(db, user, r, ctx)
                db.add(_record(batch, r, REC_IMPORTED, "; ".join(r["warnings"]) or None, **links))
                counts[REC_IMPORTED] += 1
                if r["effective_kind"] == KIND_INCOME:
                    inc_total += Decimal(r["amount"])
                elif r["effective_kind"] in (KIND_EXPENSE, KIND_SPLIT):
                    exp_total += Decimal(r["amount"])
            elif r["can_import"]:
                db.add(_record(batch, r, REC_SKIPPED, "; ".join(r["warnings"]) or "Left out by you"))
                counts[REC_SKIPPED] += 1
            elif r["status"] == IMPORTED_BEFORE:  # never imported twice
                db.add(_record(batch, r, REC_SKIPPED, "Already imported from an earlier upload"))
                counts[REC_SKIPPED] += 1
            else:
                db.add(_record(batch, r, REC_FAILED, "; ".join(r["problems"])))
                counts[REC_FAILED] += 1
        _add_counts(batch, counts, exp_total, inc_total)
        _remember(db, user, opts)
        if ctx.get("created_account_ids"):
            ids = [x for x in (batch.created_accounts or "").split(",") if x] + [str(i) for i in ctx["created_account_ids"]]
            while len(",".join(ids)) > 500:  # whole ids only (the rest simply stay after an Undo)
                ids.pop()
            batch.created_accounts = ",".join(ids)
        db.commit()  # posts the new records to the books (accounting hooks) — or fails as a whole
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:  # e.g. the books were locked between the check and now
        db.rollback()
        message = getattr(exc, "message", None) or "These rows couldn't be saved"
        batch = db.get(ImportBatch, batch.id)
        counts = Counter({REC_FAILED: len(checked)})
        for r in checked:
            db.add(_record(batch, r, REC_FAILED, message))
        _add_counts(batch, counts, Decimal(0), Decimal(0))
        db.commit()
    return {"imported": counts[REC_IMPORTED], "skipped": counts[REC_SKIPPED], "failed": counts[REC_FAILED],
            "accounts_created": ctx.get("created_accounts", [])}


def _add_counts(batch: ImportBatch, counts: Counter, exp_total: Decimal, inc_total: Decimal):
    batch.rows_total += sum(counts.values())
    batch.rows_imported += counts[REC_IMPORTED]
    batch.rows_skipped += counts[REC_SKIPPED]
    batch.rows_failed += counts[REC_FAILED]
    batch.expense_total = Decimal(batch.expense_total or 0) + exp_total
    batch.income_total = Decimal(batch.income_total or 0) + inc_total


def finish(db: Session, batch: ImportBatch) -> ImportBatch:
    if batch.status == IN_PROGRESS:
        if batch.rows_imported == 0:
            batch.status = FAILED
        elif batch.rows_failed:
            batch.status = COMPLETED_WITH_ERRORS
        else:
            batch.status = COMPLETED
        batch.finished_at = now_local()
        db.commit()
    return batch


# ---------- undo ----------
def _reconciled(db: Session, user_id: int) -> set[tuple[str, int]]:
    """Sources (EXPENSE 12, FUND 3…) whose voucher is matched to a bank statement line."""
    rows = db.execute(
        select(Voucher.source_type, Voucher.source_id)
        .join(VoucherLine, VoucherLine.voucher_id == Voucher.id)
        .join(BankStatementLine, BankStatementLine.matched_line_id == VoucherLine.id)
        .where(Voucher.user_id == user_id, Voucher.source_type.is_not(None))
    )
    return {(t, i) for t, i in rows}


def _undo_plan(db: Session, batch: ImportBatch, records: list[ImportRecord] | None = None) -> list[tuple[ImportRecord, list, str | None]]:
    """Per imported record (of `records`, default all): the objects Undo would delete, or why it has to stay."""
    books = get_books(db, batch.user_id)
    lock = books.lock_date if books else None
    reconciled = _reconciled(db, batch.user_id) if books else set()
    plan = []
    for rec in batch.records if records is None else records:
        if rec.status not in (REC_IMPORTED, REC_KEPT):
            continue
        objs = []
        expense = db.get(Expense, rec.expense_id) if rec.expense_id else None
        fund = db.get(Fund, rec.fund_id) if rec.fund_id else None
        bill = db.get(SplitBill, rec.split_bill_id) if rec.split_bill_id else None
        transfer = db.get(AccountTransfer, rec.transfer_id) if rec.transfer_id else None
        opening = db.get(Account, rec.account_id) if rec.kind == KIND_OPENING and rec.account_id else None
        reason = None
        if expense and expense.updated_at or bill and bill.updated_at:
            reason = "Edited after the import"
        elif opening and lock:
            reason = f"Your books are locked (up to {lock:%d %b %Y}), so opening balances can't change"
        elif lock and rec.record_date and rec.record_date <= lock:
            reason = f"In a locked accounting period (locked up to {lock:%d %b %Y})"
        elif (expense and ("EXPENSE", expense.id) in reconciled or fund and ("FUND", fund.id) in reconciled
              or bill and ("SPLIT_BILL", bill.id) in reconciled or transfer and ("TRANSFER", transfer.id) in reconciled):
            reason = "Reconciled with a bank statement"
        objs = [o for o in (expense, fund, bill, transfer) if o is not None]
        if opening is not None:
            objs.append(("opening", opening, Decimal(rec.amount or 0)))
        plan.append((rec, objs, reason))
    return plan


def undo_preview(db: Session, batch: ImportBatch) -> tuple[int, list[dict]]:
    removes, keeps = 0, Counter()
    for _, _, reason in _undo_plan(db, batch):
        if reason:
            keeps[reason] += 1
        else:
            removes += 1
    return removes, [{"reason": k, "rows": n} for k, n in keeps.items()]


def undo(db: Session, batch: ImportBatch, limit: int | None = None) -> dict:
    """Undo up to `limit` records per call (each removal also reverses its voucher, so a big import is undone over
    several requests, each well within the server's time limit). Call again while `remaining` > 0.
    Once nothing imported is left, a further Undo retries the records kept before (e.g. edited back, or unlocked)."""
    if batch.status == IN_PROGRESS:
        _bad("This import is still running")
    limit = limit or UNDO_CHUNK
    imported = [r for r in batch.records if r.status == REC_IMPORTED]
    todo = imported[:limit] if imported else [r for r in batch.records if r.status == REC_KEPT][:limit]
    removed = kept = gone = 0
    for rec, objs, reason in _undo_plan(db, batch, todo):
        if reason:
            rec.status, rec.reason = REC_KEPT, reason
            kept += 1
            continue
        for o in objs:
            if isinstance(o, tuple):  # an opening balance: take the imported amount off the account again
                _, account, amount = o
                account.opening_balance = Decimal(account.opening_balance or 0) - amount
            else:
                db.delete(o)
        rec.status = REC_UNDONE
        rec.reason = "Removed by Undo" if objs else "Already deleted before Undo"
        if objs:
            removed += 1
        else:
            gone += 1
    batch.rows_undone = sum(1 for r in batch.records if r.status == REC_UNDONE)
    remaining = sum(1 for r in batch.records if r.status == REC_IMPORTED)
    still = remaining + sum(1 for r in batch.records if r.status == REC_KEPT)
    batch.status = UNDONE if not still else PARTIALLY_UNDONE
    batch.undone_at = now_local()
    db.commit()  # reverses the vouchers of what was removed
    accounts = _drop_created_accounts(db, batch) if batch.status == UNDONE else {"removed": [], "deactivated": []}
    return {"removed": removed, "kept": kept, "already_deleted": gone, "remaining": remaining, "status": batch.status,
            "accounts_removed": accounts["removed"], "accounts_deactivated": accounts["deactivated"]}


def _drop_created_accounts(db: Session, batch: ImportBatch) -> dict:
    """The accounts this import created, once fully undone: removed when nothing uses them (no expense, money received
    or transfer, a zero opening balance, not primary). One whose ledger has entries in the books (the import's,
    reversed by the Undo) is deactivated instead, as an account with entries can't be deleted. Others stay."""
    out = {"removed": [], "deactivated": []}
    ids = [int(x) for x in (batch.created_accounts or "").split(",") if x.strip().isdigit()]
    kept = []
    for account_id in ids:
        account = db.get(Account, account_id)
        if account is None or account.user_id != batch.user_id:
            continue
        used = (account.is_primary or Decimal(account.opening_balance or 0) != 0
                or db.scalar(select(Expense.id).where(Expense.account_id == account.id).limit(1))
                or db.scalar(select(Fund.id).where(Fund.account_id == account.id).limit(1))
                or db.scalar(select(AccountTransfer.id).where(or_(AccountTransfer.from_account_id == account.id,
                                                                   AccountTransfer.to_account_id == account.id)).limit(1)))
        if used:
            kept.append(str(account.id))
            continue
        ledger = db.scalar(select(AccLedger).where(AccLedger.account_id == account.id))
        if ledger is not None and db.scalar(select(VoucherLine.id).where(VoucherLine.ledger_id == ledger.id).limit(1)):
            account.is_active = False
            out["deactivated"].append(account.name)
        else:
            db.delete(account)
            out["removed"].append(account.name)
    batch.created_accounts = ",".join(kept) or None
    db.commit()
    return out


def already_imported(db: Session, user_id: int, file_hash: str) -> dict | None:
    batch = db.scalar(
        select(ImportBatch).where(ImportBatch.user_id == user_id, ImportBatch.file_hash == file_hash,
                                  ImportBatch.status.in_((COMPLETED, COMPLETED_WITH_ERRORS, PARTIALLY_UNDONE)))
        .order_by(ImportBatch.id.desc())
    )
    if batch is None:
        return None
    return {"batch_id": batch.id, "date": batch.created_at, "rows": batch.rows_imported}

