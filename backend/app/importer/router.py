"""Import past records: /api/imports.

1. POST /extract (the file, and its password if it is locked: a 423 asks for one) → its tables and a guess of the columns. Nothing is saved.
2. POST /preview (a table + the columns) → the records, each checked; POST /check after the user edits them.
3. POST "" starts an import with the whole file, which is checked first: any problem in the file blocks it (the
   user fixes the file and uploads it again). POST /{id}/rows then sends the records in chunks (the page sends 200;
   at most 500, each saved as a whole, so a big file never hits the server's time limit); POST /{id}/finish closes
   it once every row is in. An import that stops before it finishes is taken back with POST /{id}/purge.
4. GET "" (history), GET /{id} (its rows, what Undo and Purge & re-import would do), POST /{id}/undo (200 records
   per call: call again while `remaining` > 0).
5. Purge & re-import: preview / check / start with `replaces_id` (the earlier import's records don't count as
   duplicates); once the corrected file is in, POST /{old id}/purge removes the earlier import entirely.

Each user imports into their own account only.
"""

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Expense, Fund, User
from ..timeutil import today_local
from . import purge as range_purge
from . import service
from .extract import extract, file_hash, file_type, gunzip
from .analyse import analyse
from .models import REC_IMPORTED, ImportBatch, ImportRecord
from .parse import Mapping, guess_mapping, parse_records
from .reconcile import reconcile, statement_summary
from .schemas import (
    AnalyseIn, BatchDetail, BatchIn, BatchOut, CheckIn, CheckOut, ChunkIn, ExtractOut, ImportRecordOut, PreviewIn,
    PurgeRangeIn, RecordIn, TableOut,
)

router = APIRouter(prefix="/api/imports", tags=["imports"])


def _batch(db: Session, user: User, batch_id: int) -> ImportBatch:
    batch = db.get(ImportBatch, batch_id)
    if batch is None or batch.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    return batch


@router.post("/extract", response_model=ExtractOut)
async def extract_file(file: UploadFile = File(...), password: str | None = Form(None, max_length=256), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    content = await file.read(settings.import_max_bytes + 1)
    name = (file.filename or "upload").strip()[:200]
    if name.lower().endswith(".gz"):  # compressed by the page (the server takes at most 4.5 MB per request)
        name = name[:-3] or "upload"
        content = gunzip(content, settings.import_max_bytes)
    tables = extract(content, name, password or None)  # 423 when the file needs a password (not stored or logged)
    limit = settings.import_max_records + 200  # room for headings and blank lines
    out = [TableOut(name=t.name, source=t.source, rows=t.rows[:limit], truncated=len(t.rows) > limit,
                    total_rows=max(len(t.rows), t.total or 0))
           for t in tables]
    # Start on the table most likely to hold the records: the most rows that have a date and an amount
    def score(t: TableOut) -> int:
        g = guess_mapping(t.rows)
        if g.date is None:
            return 0
        return sum(1 for r in t.rows[g.header_row + 1:] if g.date < len(r) and r[g.date]) + (5 if t.source != "text" else 0)
    index = max(range(len(out)), key=lambda i: score(out[i]))
    digest = file_hash(content)
    return ExtractOut(
        filename=name, file_type=file_type(name), file_hash=digest, tables=out, table_index=index,
        guess=guess_mapping(out[index].rows), already_imported=service.already_imported(db, user.id, digest),
    )


@router.post("/guess", response_model=Mapping)
def guess(data: PreviewIn, user: User = Depends(get_current_user)):
    return guess_mapping(data.rows)


@router.post("/analyse")
def analyse_rows(data: AnalyseIn, user: User = Depends(get_current_user)):
    """Work out the rows that need action from the rest of the file: a fix and its reasons for each (nothing saved)."""
    return analyse(data.records, data.row_nos, today_local())


@router.get("/coverage")
def coverage(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """How far the user's records go: the latest record, the latest imported one, and the days since."""
    last_expense = db.scalar(select(func.max(Expense.expense_date)).where(Expense.user_id == user.id))
    last_fund = db.scalar(select(func.max(Fund.fund_date)).where(Fund.user_id == user.id))
    last_imported = db.scalar(select(func.max(ImportRecord.record_date))
                              .where(ImportRecord.user_id == user.id, ImportRecord.status == REC_IMPORTED))
    last = max((d for d in (last_expense, last_fund) if d), default=None)
    today = today_local()
    return {
        "last_record_date": last, "last_imported_date": last_imported,
        "days_behind": (today - last).days if isinstance(last, date) else None,
    }


@router.post("/preview", response_model=CheckOut)
def preview(data: PreviewIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        rows, ignored = parse_records(data.rows, data.mapping, today_local())
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    records = [RecordIn(**r) for r in rows]
    replaces = _replaces(db, user, data.replaces_id, data.also_replaces)
    result = service.check(db, user, records, data.options, fresh=True, replaces=replaces)
    summary = statement_summary(data.rows, data.mapping, data.other_tables)
    return _with_statement({**result, "ignored_rows": ignored}, records, summary)


@router.post("/check", response_model=CheckOut)
def check(data: CheckIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    replaces = _replaces(db, user, data.replaces_id, data.also_replaces)
    result = service.check(db, user, data.records, data.options, replaces=replaces)
    return _with_statement(result, data.records, data.statement) if data.statement is not None else result


def _replaces(db: Session, user: User, replaces_id: int | None, also: list[int]) -> list:
    """The imports a Purge & re-import replaces: the chosen one first, then the others (only with a chosen one)."""
    if not replaces_id:
        return []
    ids = [replaces_id] + [i for i in dict.fromkeys(also) if i != replaces_id]
    return [_batch(db, user, i) for i in ids]


# ---------- purge a date range (Import Records → Purge) ----------
@router.get("/purge/preview")
def purge_preview(start: date, end: date, account_id: int | None = None, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """What purging the range would remove: expenses, money received and transfers (typed or imported), of every
    account or only `account_id`'s (a transfer to or from it counts)."""
    return range_purge.preview(db, user, start, end, account_id)


@router.post("/purge")
def purge_range(data: PurgeRangeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Remove every record in the range, 200 per call (backed up first): call again with `backup_id` while
    `remaining` > 0."""
    return range_purge.purge(db, user, data.start, data.end, data.backup_id, data.account_id)


def _with_statement(result: dict, records: list[RecordIn], summary: dict) -> dict:
    """The statement check, with each row it points at noted on the row itself."""
    verdict = reconcile(records, summary)
    by_row = {}
    for issue in verdict["row_issues"]:
        by_row.setdefault(issue["row_no"], []).append(issue["message"])
    for r in result["records"]:
        r["warnings"] = r["warnings"] + by_row.get(r["row_no"], [])
    return {**result, "statement": summary, "statement_check": verdict}


@router.post("", response_model=BatchOut, status_code=201)
def start(data: BatchIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.start(db, user, data.filename, data.file_type, data.file_hash, data.records, data.options,
                         data.replaces_id, data.also_replaces)


@router.post("/{batch_id}/rows")
def add_rows(batch_id: int, data: ChunkIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.import_chunk(db, user, _batch(db, user, batch_id), data.records, data.options)


@router.post("/{batch_id}/finish", response_model=BatchOut)
def finish(batch_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.finish(db, _batch(db, user, batch_id))


@router.get("", response_model=list[BatchOut])
def history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(ImportBatch).where(ImportBatch.user_id == user.id).order_by(ImportBatch.id.desc())).all()


@router.get("/{batch_id}", response_model=BatchDetail)
def detail(batch_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    batch = _batch(db, user, batch_id)
    removes, keeps = service.undo_preview(db, batch)
    return BatchDetail(
        **BatchOut.model_validate(batch).model_dump(),
        records=[ImportRecordOut.model_validate(r) for r in batch.records], undo_removes=removes, undo_keeps=keeps,
        books=service.get_books(db, user.id) is not None, **service.purge_preview(db, batch),
    )


@router.post("/{batch_id}/undo")
def undo(batch_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.undo(db, _batch(db, user, batch_id))


@router.post("/{batch_id}/purge")
def purge(batch_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Remove an import entirely (200 records per call): one replaced by Purge & re-import, or one that stopped."""
    return service.purge(db, _batch(db, user, batch_id))
