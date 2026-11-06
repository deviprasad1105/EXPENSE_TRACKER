"""Import past records: /api/imports.

1. POST /extract (the file) → its tables and a guess of the columns. Nothing is saved.
2. POST /preview (a table + the columns) → the records, each checked; POST /check after the user edits them.
3. POST "" starts an import; POST /{id}/rows sends the records in chunks (the page sends 200; at most 500, each
   saved as a whole, so a big file never hits the server's time limit); POST /{id}/finish closes it.
4. GET "" (history), GET /{id} (its rows and what Undo would do), POST /{id}/undo (200 records per call: call
   again while `remaining` > 0).

Each user imports into their own account only.
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..timeutil import today_local
from . import service
from .extract import extract, file_hash, file_type, gunzip
from .models import ImportBatch
from .parse import Mapping, guess_mapping, parse_records
from .schemas import (
    BatchDetail, BatchIn, BatchOut, CheckIn, CheckOut, ChunkIn, ExtractOut, ImportRecordOut, PreviewIn, RecordIn, TableOut,
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
    out = [TableOut(name=t.name, source=t.source, rows=t.rows[:limit], truncated=len(t.rows) > limit) for t in tables]
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
    result = service.check(db, user, records, data.options, fresh=True)
    return {**result, "ignored_rows": ignored}


@router.post("/check", response_model=CheckOut)
def check(data: CheckIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.check(db, user, data.records, data.options)


@router.post("", response_model=BatchOut, status_code=201)
def start(data: BatchIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    batch = ImportBatch(user_id=user.id, filename=data.filename.strip(), file_type=data.file_type[:10] or "manual",
                        file_hash=data.file_hash)
    db.add(batch)
    db.commit()
    return batch


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
    )


@router.post("/{batch_id}/undo")
def undo(batch_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.undo(db, _batch(db, user, batch_id))
