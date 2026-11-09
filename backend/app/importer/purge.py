"""Purge a date range: remove every expense, money received and transfer dated between two dates, whether typed in
by hand or imported, so the period can be imported again cleanly.

- Checked as a whole first: refused when part of the range is in a locked accounting period, or a record in it is
  reconciled with a bank statement (the books would no longer agree), so a purge never stops halfway on those.
- A backup of everything it removes is saved before anything is deleted (an administrator can restore it from
  Admin → Backups).
- Removed in chunks (a removal also reverses its voucher in the books), 200 records per request: the page calls
  again with the backup's id while `remaining` > 0. Only the records in that backup are removed.
- Import history stays right: the rows of an import whose record was purged are marked as removed, so the file can
  be imported again, and an import with nothing left is marked as purged.
Split bills and settlements (shared with other people), subscriptions, budgets, accounts and opening balances stay.
"""

from collections import Counter
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..accounting.engine import get_books
from ..models import AccountTransfer, Expense, Fund, User
from ..services.backup import create_backup, load_backup, row_to_dict
from ..timeutil import now_local
from .models import (
    COMPLETED, COMPLETED_WITH_ERRORS, PARTIALLY_UNDONE, PURGED, REC_IMPORTED, REC_KEPT, REC_UNDONE, ImportBatch,
    ImportRecord,
)
from .service import UNDO_CHUNK, _reconciled

KIND_PURGE = "PURGE"
_MAX_DAYS = 366 * 30
# Backup table → model, date column, ImportRecord link, voucher source type
_KINDS = (
    ("expenses", Expense, Expense.expense_date, ImportRecord.expense_id, "EXPENSE"),
    ("funds", Fund, Fund.fund_date, ImportRecord.fund_id, "FUND"),
    ("account_transfers", AccountTransfer, AccountTransfer.transfer_date, ImportRecord.transfer_id, "TRANSFER"),
)


def _bad(message: str, code=status.HTTP_400_BAD_REQUEST):
    raise HTTPException(code, message)


def _range(start: date, end: date) -> None:
    if start > end:
        _bad("The From date must be on or before the To date")
    if (end - start).days > _MAX_DAYS:
        _bad("Choose a range of at most 30 years")


def preview(db: Session, user: User, start: date, end: date) -> dict:
    """What a purge of the range would remove, and why it can't be done (empty `blocks` = it can)."""
    _range(start, end)
    out: dict = {"start": start, "end": end, "blocks": []}
    imported_ids: dict[str, set] = {}
    total_records = 0
    for name, model, col, link, _ in _KINDS:
        where = (model.user_id == user.id, col >= start, col <= end)
        count, total = db.execute(select(func.count(model.id), func.coalesce(func.sum(model.amount), 0)).where(*where)).one()
        out[name] = {"count": count, "total": str(Decimal(total).quantize(Decimal("0.01")))}
        total_records += count
        imported_ids[name] = set(db.scalars(
            select(link).where(ImportRecord.user_id == user.id, ImportRecord.status.in_((REC_IMPORTED, REC_KEPT)),
                               link.is_not(None), link.in_(select(model.id).where(*where)))))
    out["records"] = total_records
    out["imported"] = sum(len(v) for v in imported_ids.values())
    out["typed"] = total_records - out["imported"]
    # The imports whose records are in the range
    batches = Counter()
    for name, model, col, link, _ in _KINDS:
        for (bid,) in db.execute(select(ImportRecord.batch_id).where(
                ImportRecord.user_id == user.id, ImportRecord.status.in_((REC_IMPORTED, REC_KEPT)),
                link.in_(select(model.id).where(model.user_id == user.id, col >= start, col <= end)))):
            batches[bid] += 1
    out["imports"] = [{"id": b.id, "filename": b.filename, "created_at": b.created_at, "rows": n}
                      for bid, n in batches.most_common() if (b := db.get(ImportBatch, bid))]
    books = get_books(db, user.id)
    if books and books.lock_date and start <= books.lock_date and total_records:
        out["blocks"].append(f"Your accounting books are locked up to {books.lock_date:%d %b %Y}: records on or before "
                             "it can't be removed. Choose a From date after it, or unlock the books first.")
    if books and total_records:
        reconciled = _reconciled(db, user.id)
        n = 0
        for name, model, col, _, source in _KINDS:
            ids = set(db.scalars(select(model.id).where(model.user_id == user.id, col >= start, col <= end)))
            n += sum(1 for t, i in reconciled if t == source and i in ids)
        if n:
            out["blocks"].append(f"{n} record(s) in this range are reconciled with a bank statement. Unmatch them in "
                                 "Bank Reconciliation first.")
    return out


def purge(db: Session, user: User, start: date, end: date, backup_id: int | None = None) -> dict:
    """Remove up to 200 records of the range. The first call checks the whole range and saves the backup; the next
    calls pass that backup's id and remove the rest of what it holds."""
    from ..models import DataBackup

    if backup_id is None:
        info = preview(db, user, start, end)
        if info["blocks"]:
            _bad("Nothing was removed. " + " ".join(info["blocks"]))
        if not info["records"]:
            _bad("There are no records in this date range")
        tables = {}
        for name, model, col, _, _ in _KINDS:
            tables[name] = [row_to_dict(o) for o in db.scalars(
                select(model).where(model.user_id == user.id, col >= start, col <= end))]
        backup = create_backup(
            db, KIND_PURGE, user, user, tables,
            summary=(f"Purged {start:%d %b %Y} to {end:%d %b %Y}: {len(tables['expenses'])} expenses, "
                     f"{len(tables['funds'])} money received, {len(tables['account_transfers'])} transfers"),
            reason="Purged by the user from Import Records",
            extra={"start": start.isoformat(), "end": end.isoformat()},
        )
        db.commit()
    else:
        backup = db.get(DataBackup, backup_id)
        if backup is None or backup.user_id != user.id or backup.kind != KIND_PURGE:
            _bad("Purge not found", status.HTTP_404_NOT_FOUND)
    doc = load_backup(backup)
    start, end = date.fromisoformat(doc["start"]), date.fromisoformat(doc["end"])

    removed = 0
    touched: set[int] = set()
    for name, model, _, link, _ in _KINDS:
        live = [o for r in doc["tables"].get(name, []) if (o := db.get(model, r["id"])) is not None]
        for obj in live[:max(0, UNDO_CHUNK - removed)]:
            for rec in db.scalars(select(ImportRecord).where(link == obj.id, ImportRecord.status.in_((REC_IMPORTED, REC_KEPT)))):
                rec.status, rec.reason = REC_UNDONE, f"Purged ({start:%d %b %Y} to {end:%d %b %Y})"
                touched.add(rec.batch_id)
            db.delete(obj)
            removed += 1
    db.flush()
    for bid in touched:
        batch = db.get(ImportBatch, bid)
        left = db.scalar(select(func.count(ImportRecord.id)).where(
            ImportRecord.batch_id == bid, ImportRecord.status.in_((REC_IMPORTED, REC_KEPT))))
        batch.rows_undone = db.scalar(select(func.count(ImportRecord.id)).where(
            ImportRecord.batch_id == bid, ImportRecord.status == REC_UNDONE))
        batch.undone_at = now_local()
        if batch.status in (COMPLETED, COMPLETED_WITH_ERRORS, PARTIALLY_UNDONE):
            batch.status = PURGED if not left else PARTIALLY_UNDONE
    db.commit()  # reverses the vouchers of what was removed
    remaining = sum(1 for name, model, _, _, _ in _KINDS for r in doc["tables"].get(name, []) if db.get(model, r["id"]) is not None)
    return {"backup_id": backup.id, "removed": removed, "remaining": remaining, "start": start, "end": end}
