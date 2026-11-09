"""Import past records: /api/imports.

1. POST /extract (the file) → its tables and a guess of the columns. Nothing is saved.
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

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..timeutil import today_local
from . import purge as range_purge
from . import service
from .extract import extract, file_hash, file_type, gunzip
from .models import ImportBatch
from .parse import Mapping, guess_mapping, parse_records
from .schemas import (
    BatchDetail, BatchIn, BatchOut, CheckIn, CheckOut, ChunkIn, ExtractOut, ImportRecordOut, PreviewIn, PurgeRangeIn,
    RecordIn, TableOut,
)

router = APIRouter(prefix="/api/imports", tags=["imports"])


def _batch(db: Session, user: User, batch_id: int) -> ImportBatch:
    batch = db.get(ImportBatch, batch_id)
    if batch is None or batch.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    return batch


@router.post("/extract", response_model=ExtractOut)
async def extract_file(file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    content = await file.read(settings.import_max_bytes + 1)
    name = (file.filename or "upload").strip()[:200]
    if name.lower().endswith(".gz"):  # compressed by the page (the server takes at most 4.5 MB per request)
        name = name[:-3] or "upload"
        content = gunzip(content, settings.import_max_bytes)
    tables = extract(content, name)
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


@router.post("/preview", response_model=CheckOut)
def preview(data: PreviewIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        rows, ignored = parse_records(data.rows, data.mapping, today_local())
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    records = [RecordIn(**r) for r in rows]
    replaces = _batch(db, user, data.replaces_id) if data.replaces_id else None
    result = service.check(db, user, records, data.options, fresh=True, replaces=replaces)
    return {**result, "ignored_rows": ignored}


@router.post("/check", response_model=CheckOut)
def check(data: CheckIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    replaces = _batch(db, user, data.replaces_id) if data.replaces_id else None
    return service.check(db, user, data.records, data.options, replaces=replaces)


# ---------- purge a date range (Import Records → Purge) ----------
@router.get("/purge/preview")
def purge_preview(start: date, end: date, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """What purging the range would remove: expenses, money received and transfers (typed or imported)."""
    return range_purge.preview(db, user, start, end)


@router.post("/purge")
def purge_range(data: PurgeRangeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Remove every record in the range, 200 per call (backed up first): call again with `backup_id` while
    `remaining` > 0."""
    return range_purge.purge(db, user, data.start, data.end, data.backup_id)


@router.post("", response_model=BatchOut, status_code=201)
def start(data: BatchIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.start(db, user, data.filename, data.file_type, data.file_hash, data.records, data.options,
                         data.replaces_id)


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
