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
the others, who then owe it.
"""

import hashlib
from collections import Counter, defaultdict
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..accounting.engine import get_books
from ..accounting.models import BankStatementLine, Voucher, VoucherLine
from ..config import settings
from ..deps import FEATURE_SPLIT, has_feature
from ..models import Account, Expense, Fund, SplitBill, SplitGroup, SplitMember, SplitShare, User
from ..routers.expenses import user_categories
from ..services.splits import split_evenly
from ..timeutil import now_local, today_local
from .models import (
    COMPLETED, COMPLETED_WITH_ERRORS, FAILED, IN_PROGRESS, KIND_EXPENSE, KIND_INCOME, KIND_SPLIT, PARTIALLY_UNDONE,
    REC_FAILED, REC_IMPORTED, REC_KEPT, REC_SKIPPED, REC_UNDONE, UNDONE, ImportBatch, ImportRecord,
)
from .parse import CENT, MAX_AMOUNT
from .schemas import ImportOptions, RecordIn

READY = "READY"
INVALID = "INVALID"
LOCKED = "LOCKED"
NEEDS_CATEGORY = "NEEDS_CATEGORY"
NEEDS_ACCOUNT = "NEEDS_ACCOUNT"
DUPLICATE = "DUPLICATE"
IMPORTED_BEFORE = "IMPORTED_BEFORE"
IMPORTABLE = (READY, DUPLICATE, IMPORTED_BEFORE)
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
    return f"{r.kind}|{r.date.isoformat()}|{Decimal(r.amount).quantize(CENT)}|{_key(r.description)}"


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
    mine_exp, mine_fund = set(), set()
    if batch_id:
        for e, f in db.execute(select(ImportRecord.expense_id, ImportRecord.fund_id).where(ImportRecord.batch_id == batch_id)):
            mine_exp.add(e)
            mine_fund.add(f)
    out: dict[tuple, list[str]] = defaultdict(list)
    for e in db.execute(select(Expense.id, Expense.expense_date, Expense.amount, Expense.item_name).where(
            Expense.user_id == user_id, Expense.expense_date >= lo, Expense.expense_date <= hi)):
        if e.id not in mine_exp:
            out[(KIND_EXPENSE, e.expense_date, Decimal(e.amount))].append(e.item_name)
    for f in db.execute(select(Fund.id, Fund.fund_date, Fund.amount, Fund.source).where(
            Fund.user_id == user_id, Fund.fund_date >= lo, Fund.fund_date <= hi)):
        if f.id not in mine_fund:
            out[(KIND_INCOME, f.fund_date, Decimal(f.amount))].append(f.source)
    return out


def _match_account(raw: str, accounts: list[Account]) -> int | None:
    key = _key(raw)
    if not key:
        return None
    digits = "".join(c for c in raw if c.isdigit())
    for a in accounts:
        if key == _key(a.name):
            return a.id
    for a in accounts:
        if a.last4 and digits.endswith(a.last4):
            return a.id
    for a in accounts:
        if _key(a.name) in key or (a.bank and key == _key(a.bank)):
            return a.id
    return None


def _member_groups(db: Session, user_id: int) -> list[SplitGroup]:
    ids = select(SplitMember.group_id).where(SplitMember.user_id == user_id, SplitMember.left_at.is_(None))
    return list(db.scalars(select(SplitGroup).where(SplitGroup.id.in_(ids)).order_by(SplitGroup.name)))


def check(db: Session, user: User, records: list[RecordIn], opts: ImportOptions, batch_id: int | None = None,
          fresh: bool = False) -> dict:
    """Check every record. `fresh` (a first preview): each record's `include` becomes its default."""
    if len(records) > settings.import_max_records:
        _bad(f"This file has {len(records)} records; up to {settings.import_max_records:,} can be imported at once. Split the file.")
    today = today_local()
    books = get_books(db, user.id)
    lock = books.lock_date if books else None
    accounts = list(db.scalars(select(Account).where(Account.user_id == user.id).order_by(Account.name)))
    account_ids = {a.id for a in accounts}
    primary = next((a.id for a in accounts if a.is_primary and a.is_active), None)
    if opts.account_mode == "fixed" and opts.account_id not in account_ids:
        _bad("Choose one of your accounts for this upload")
    for v in opts.account_map.values():
        if v is not None and v not in account_ids:
            _bad("An account chosen for the file's account names isn't one of yours")
    known = {c.casefold(): c for c in user_categories(db, user.id)}
    cat_map = {_key(k): v.strip() for k, v in opts.category_map.items()}
    acc_map = {_key(k): v for k, v in opts.account_map.items()}
    split_ok = has_feature(user, FEATURE_SPLIT)
    fps = fingerprints(records)
    imported_before = _imported_fingerprints(db, user.id, fps, batch_id)
    existing = _existing(db, user.id, records, batch_id)

    out, cat_values, acc_values = [], Counter(), Counter()
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
            problems.append(parse_msg("type", "debit and a credit", default="Choose expense, income or split"))
        if r.amount is None:
            problems.append(parse_msg("amount", "decimal", default="Missing amount"))
        elif r.amount <= 0:
            problems.append("Amount must be more than zero")
        elif r.amount > MAX_AMOUNT:
            problems.append("Amount is too large")
        elif r.amount != r.amount.quantize(CENT):
            problems.append("Amount has more than 2 decimal places")
        if r.kind == KIND_SPLIT:
            if not split_ok:
                problems.append("Split bills aren't on your plan: import this row as an expense of your share")
            if r.my_share is None:
                problems.append("A split bill needs your share")
            elif r.my_share < 0 or r.my_share != r.my_share.quantize(CENT):
                problems.append("Your share must be zero or more, with at most 2 decimals")
            elif r.amount is not None and r.my_share > r.amount:
                problems.append("Your share is more than the amount paid")
        description = " ".join(r.description.split())
        if len(description) > 100:
            warnings.append("Description shortened to 100 characters")
        if problems:
            state = INVALID
        elif lock and r.date <= lock:
            state = LOCKED
            problems.append(f"Your books are locked up to {lock:%d %b %Y}: records on or before it can't be added")
        elif fp in imported_before:
            # Shown as such even before its category is chosen; a category is only needed to import it again
            state = IMPORTED_BEFORE
            warnings.append("Already imported from an earlier upload")
        mapping_problems = []

        category = None
        if r.kind in (KIND_EXPENSE, KIND_SPLIT):
            raw = (r.category or "").strip()
            cat_values[raw] += 1
            k = _key(raw)
            if k in cat_map and cat_map[k]:
                category = cat_map[k][:50]
            elif k and k in known:
                category = known[k]
            elif state in (READY, IMPORTED_BEFORE):
                mapping_problems.append((NEEDS_CATEGORY, f"Choose a category for “{raw}”" if raw else "Choose a category for rows without one"))

        account_id = None
        if opts.account_mode == "primary":
            account_id = primary
        elif opts.account_mode == "fixed":
            account_id = opts.account_id
        elif opts.account_mode == "column":
            raw = (r.account or "").strip()
            acc_values[raw] += 1
            k = _key(raw)
            if k in acc_map:
                account_id = acc_map[k]
            else:
                account_id = _match_account(raw, accounts)
                if account_id is None and state in (READY, IMPORTED_BEFORE):
                    mapping_problems.append((NEEDS_ACCOUNT, f"Choose the account for “{raw}”" if raw else "Choose the account for rows without one"))

        can_map = not mapping_problems
        if mapping_problems and state == READY:
            state = mapping_problems[0][0]
            problems += [m for _, m in mapping_problems]
        elif mapping_problems:  # imported before: to import it again, a category / account is still needed
            warnings += [m for _, m in mapping_problems]
        if state == READY and r.kind in (KIND_EXPENSE, KIND_INCOME, KIND_SPLIT):
            amount = r.my_share if r.kind == KIND_SPLIT else r.amount
            names = existing.get((KIND_INCOME if r.kind == KIND_INCOME else KIND_EXPENSE, r.date, amount))
            if names:
                state = DUPLICATE
                warnings.append(f"Possible duplicate: “{names.pop(0)}” for ₹{amount:,.2f} on {r.date:%d %b %Y} is already in the app")

        can = state in IMPORTABLE and can_map
        default = can and state == READY and not r.parse_warnings
        out.append({
            **r.model_dump(), "status": state, "problems": problems, "warnings": warnings, "can_import": can,
            "default_include": default, "category_final": category, "account_id": account_id, "fingerprint": fp,
            "include": default if fresh else r.include,
        })

    counts = Counter(o["status"] for o in out)
    chosen = [o for o in out if o["can_import"] and o["include"]]
    return {
        "records": out,
        "summary": {
            "total": len(out), "ready": counts[READY], "invalid": counts[INVALID], "locked": counts[LOCKED],
            "needs_category": counts[NEEDS_CATEGORY], "needs_account": counts[NEEDS_ACCOUNT],
            "duplicates": counts[DUPLICATE], "imported_before": counts[IMPORTED_BEFORE],
            "chosen": len(chosen),
            "expense_total": str(sum((Decimal(o["amount"]) for o in chosen if o["kind"] != KIND_INCOME), Decimal(0))),
            "income_total": str(sum((Decimal(o["amount"]) for o in chosen if o["kind"] == KIND_INCOME), Decimal(0))),
        },
        "categories": sorted(known.values(), key=str.casefold),
        "category_values": [
            {"value": v, "rows": n, "mapped_to": cat_map.get(_key(v)) or known.get(_key(v)), "known": _key(v) in known}
            for v, n in sorted(cat_values.items(), key=lambda x: (-x[1], x[0]))
        ],
        "account_values": [
            {"value": v, "rows": n, "account_id": acc_map[_key(v)] if _key(v) in acc_map else _match_account(v, accounts),
             "mapped": _key(v) in acc_map}
            for v, n in sorted(acc_values.items(), key=lambda x: (-x[1], x[0]))
        ],
        "accounts": [{"id": a.id, "name": a.name, "last4": a.last4, "is_primary": a.is_primary} for a in accounts if a.is_active],
        "split_groups": [{"id": g.id, "name": g.name} for g in _member_groups(db, user.id)],
        "split_enabled": split_ok,
        "lock_date": lock,
        "max_records": settings.import_max_records,
    }


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


def _create(db: Session, user: User, r: dict, ctx: dict) -> dict:
    d = r["date"]
    description = " ".join((r["description"] or "").split())[:100]
    amount = Decimal(r["amount"])
    note = r["note"]
    if r["kind"] == KIND_INCOME:
        source = (r["category"] or "").strip()[:100] or "Income"
        fund_note = " – ".join(x for x in (description, note) if x)[:500] or None
        fund = Fund(user_id=user.id, fund_date=d, amount=amount, source=source, note=fund_note, account_id=r["account_id"])
        db.add(fund)
        db.flush()
        return {"fund_id": fund.id}
    item = description or r["category_final"] or "Imported expense"
    links = {}
    if r["kind"] == KIND_SPLIT:
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
    db.add(expense)
    db.flush()
    links["expense_id"] = expense.id
    return links


def _record(batch: ImportBatch, r: dict, status_: str, reason: str | None, **links) -> ImportRecord:
    return ImportRecord(
        batch_id=batch.id, user_id=batch.user_id, row_no=r["row_no"], kind=r["kind"], status=status_,
        reason=(reason or None) and reason[:500], record_date=r["date"], description=(r["description"] or "")[:100] or None,
        amount=r["amount"] if r["amount"] is None or Decimal(r["amount"]) <= MAX_AMOUNT else None,
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
                if r["kind"] == KIND_INCOME:
                    inc_total += Decimal(r["amount"])
                else:
                    exp_total += Decimal(r["amount"])
            elif r["can_import"]:
                db.add(_record(batch, r, REC_SKIPPED, "; ".join(r["warnings"]) or "Left out by you"))
                counts[REC_SKIPPED] += 1
            else:
                db.add(_record(batch, r, REC_FAILED, "; ".join(r["problems"])))
                counts[REC_FAILED] += 1
        _add_counts(batch, counts, exp_total, inc_total)
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
    return {"imported": counts[REC_IMPORTED], "skipped": counts[REC_SKIPPED], "failed": counts[REC_FAILED]}


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
        reason = None
        if expense and expense.updated_at or bill and bill.updated_at:
            reason = "Edited after the import"
        elif lock and rec.record_date and rec.record_date <= lock:
            reason = f"In a locked accounting period (locked up to {lock:%d %b %Y})"
        elif (expense and ("EXPENSE", expense.id) in reconciled or fund and ("FUND", fund.id) in reconciled
              or bill and ("SPLIT_BILL", bill.id) in reconciled):
            reason = "Reconciled with a bank statement"
        objs = [o for o in (expense, fund, bill) if o is not None]
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
    removed = kept = 0
    for rec, objs, reason in _undo_plan(db, batch, todo):
        if reason:
            rec.status, rec.reason = REC_KEPT, reason
            kept += 1
            continue
        for o in objs:
            db.delete(o)
        rec.status = REC_UNDONE
        rec.reason = "Removed by Undo" if objs else "Already deleted before Undo"
        removed += 1
    batch.rows_undone = sum(1 for r in batch.records if r.status == REC_UNDONE)
    remaining = sum(1 for r in batch.records if r.status == REC_IMPORTED)
    still = remaining + sum(1 for r in batch.records if r.status == REC_KEPT)
    batch.status = UNDONE if not still else PARTIALLY_UNDONE
    batch.undone_at = now_local()
    db.commit()  # reverses the vouchers of what was removed
    return {"removed": removed, "kept": kept, "remaining": remaining, "status": batch.status}


def already_imported(db: Session, user_id: int, file_hash: str) -> dict | None:
    batch = db.scalar(
        select(ImportBatch).where(ImportBatch.user_id == user_id, ImportBatch.file_hash == file_hash,
                                  ImportBatch.status.in_((COMPLETED, COMPLETED_WITH_ERRORS, PARTIALLY_UNDONE)))
        .order_by(ImportBatch.id.desc())
    )
    if batch is None:
        return None
    return {"batch_id": batch.id, "date": batch.created_at, "rows": batch.rows_imported}

