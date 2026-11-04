"""Accounting reports (JSON, or ?format=xlsx|pdf), stock values, GST invoices and bank reconciliation."""

import json
from datetime import date
from decimal import Decimal

from fastapi import Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..timeutil import now_local, today_local, validate_range
from . import bank, exports, reports
from .chart import fy_end, fy_of, fy_start
from .engine import PAYMENT, RECEIPT, PeriodLocked, Line, post_voucher
from .gst import InvoiceIn, compute, post_invoice
from .models import AccSettings, BankImport, BankStatementLine, StockValue
from .router import _ledger, open_books, router, vouchers_out

FORMAT = Query("json", pattern="^(json|xlsx|pdf)$")
MEDIA = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "pdf": "application/pdf"}


def _plain(obj):
    """Exact JSON: amounts as strings with two decimals, dates as ISO strings."""
    if isinstance(obj, Decimal):
        return f"{obj:.2f}"
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def _business(db: Session, books: AccSettings) -> str:
    user = db.get(User, books.user_id)
    name = books.business_name or user.full_name
    return name + (f" · GSTIN {books.gstin}" if books.gstin else "")


def _respond(data: dict, fmt: str, make_doc, name: str):
    if fmt == "json":
        return JSONResponse(_plain(data))
    doc = make_doc()
    content = exports.to_xlsx(doc) if fmt == "xlsx" else exports.to_pdf(doc)
    return Response(content, media_type=MEDIA[fmt], headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'})


def _period(books, fy, start, end):
    if start and end:
        validate_range(start, end)
    return reports.period(books, fy, start, end)


# ---------- reports ----------
@router.get("/reports/journal")
def journal_report(
    user_id: int | None = None, fy: int | None = None, start: date | None = None, end: date | None = None,
    voucher_type: str | None = Query(None, pattern="^(PAYMENT|RECEIPT|CONTRA|JOURNAL|SALES|PURCHASE)$"),
    format: str = FORMAT, viewer: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _, books = open_books(db, viewer, user_id)
    p = _period(books, fy, start, end)
    r = reports.journal(db, books, p, voucher_type)
    return _respond(r, format, lambda: exports.journal_doc(r, _business(db, books)), f"journal_{p.start}_{p.end}")


@router.get("/reports/ledger")
def ledger_report(
    ledger_id: int, user_id: int | None = None, fy: int | None = None, start: date | None = None,
    end: date | None = None, format: str = FORMAT, viewer: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _, books = open_books(db, viewer, user_id)
    ledger = _ledger(db, books, ledger_id)
    p = _period(books, fy, start, end)
    r = reports.ledger_report(db, books, ledger, p)
    safe = "".join(c if c.isalnum() else "_" for c in ledger.name)[:40]
    return _respond(r, format, lambda: exports.ledger_doc(r, _business(db, books)), f"ledger_{safe}_{p.start}_{p.end}")


@router.get("/reports/trial-balance")
def trial_balance_report(
    user_id: int | None = None, fy: int | None = None, start: date | None = None, end: date | None = None,
    view: str = Query("grouped", pattern="^(grouped|ledgers)$"), format: str = FORMAT,
    viewer: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _, books = open_books(db, viewer, user_id)
    p = _period(books, fy, start, end)
    r = reports.trial_balance(db, books, p)
    return _respond(r, format, lambda: exports.trial_balance_doc(r, _business(db, books), view == "grouped"),
                    f"trial_balance_{p.start}_{p.end}")


def _final_accounts(db, books, fy, end):
    p = reports.period(books, fy, None, end)
    return reports.trading_and_pl(db, books, p)


@router.get("/reports/trading")
def trading_report(
    user_id: int | None = None, fy: int | None = None, end: date | None = None, format: str = FORMAT,
    viewer: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _, books = open_books(db, viewer, user_id)
    r = _final_accounts(db, books, fy, end)
    return _respond(r, format, lambda: exports.trading_doc(r, _business(db, books)), exports.filename("trading_account", fy=r["fy"]))


@router.get("/reports/profit-loss")
def profit_loss_report(
    user_id: int | None = None, fy: int | None = None, end: date | None = None, format: str = FORMAT,
    viewer: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _, books = open_books(db, viewer, user_id)
    r = _final_accounts(db, books, fy, end)
    return _respond(r, format, lambda: exports.profit_loss_doc(r, _business(db, books)), exports.filename("profit_and_loss", fy=r["fy"]))


@router.get("/reports/balance-sheet")
def balance_sheet_report(
    user_id: int | None = None, as_of: date | None = None, format: str = FORMAT,
    viewer: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _, books = open_books(db, viewer, user_id)
    as_of = as_of or today_local()
    reports.period(books, fy_of(as_of), None, as_of)  # range checks
    r = reports.balance_sheet(db, books, as_of)
    return _respond(r, format, lambda: exports.balance_sheet_doc(r, _business(db, books)), exports.filename("balance_sheet", end=as_of))


@router.get("/reports/cash-flow")
def cash_flow_report(
    user_id: int | None = None, fy: int | None = None, end: date | None = None, format: str = FORMAT,
    viewer: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _, books = open_books(db, viewer, user_id)
    p = reports.period(books, fy, None, end)
    r = reports.cash_flow(db, books, p)
    return _respond(r, format, lambda: exports.cash_flow_doc(r, _business(db, books)), exports.filename("cash_flow", fy=p.fy))


@router.get("/reports/bank-reconciliation")
def bank_reconciliation_report(
    ledger_id: int, user_id: int | None = None, as_of: date | None = None, format: str = FORMAT,
    viewer: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _, books = open_books(db, viewer, user_id)
    ledger = bank.bank_ledger(db, books, ledger_id)
    r = bank.reconciliation(db, books, ledger, as_of or today_local())
    db.commit()
    return _respond(r, format, lambda: exports.brs_doc(r, _business(db, books)), exports.filename("bank_reconciliation", end=r["as_of"]))


# ---------- stock ----------
class StockIn(BaseModel):
    fy: int
    opening_value: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    closing_value: Decimal | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    clear_closing: bool = False


@router.get("/stock")
def get_stock(fy: int | None = None, user_id: int | None = None, viewer: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    _, books = open_books(db, viewer, user_id)
    first = fy_of(books.books_start)
    years = []
    for y in range(max(fy_of(today_local()), first), first - 1, -1):
        opening, closing = reports.stock_for(db, books.user_id, y, first)
        years.append({"fy": y, "label": f"{y}-{str(y + 1)[-2:]}", "opening": f"{opening:.2f}",
                      "closing": None if closing is None else f"{closing:.2f}", "opening_editable": y == first,
                      "locked": bool(books.lock_date and books.lock_date >= fy_end(y))})
    return years


@router.put("/stock")
def set_stock(data: StockIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Closing stock of a year (any year); opening stock only for the first year (later years carry forward)."""
    _, books = open_books(db, viewer, user_id, write=True)
    first = fy_of(books.books_start)
    if data.fy < first or data.fy > fy_of(today_local()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That financial year isn't in your books")
    if data.opening_value is not None and data.fy != first:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only the first year's opening stock is entered; later years open with the previous closing stock")
    row = db.scalar(select(StockValue).where(StockValue.user_id == books.user_id, StockValue.fy == data.fy))
    if row is None:
        row = StockValue(user_id=books.user_id, fy=data.fy)
        db.add(row)
    lock = books.lock_date
    if data.opening_value is not None and Decimal(row.opening_value or 0) != data.opening_value:
        if lock:
            raise PeriodLocked("The books are locked, so the first year's opening stock can't change")
        row.opening_value = data.opening_value
    new_closing = None if data.clear_closing else data.closing_value
    if (data.clear_closing or data.closing_value is not None) and row.closing_value != new_closing:
        if lock and lock >= fy_end(data.fy):
            raise PeriodLocked(f"FY {data.fy}-{str(data.fy + 1)[-2:]} is locked, so its closing stock can't change")
        row.closing_value = new_closing
    row.updated_at = now_local()
    db.commit()
    return get_stock(None, user_id, viewer, db)


# ---------- GST invoices ----------
@router.post("/invoices/preview")
def invoice_preview(data: InvoiceIn, user_id: int | None = None, viewer: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    _, books = open_books(db, viewer, user_id)
    intra = bool(books.state) and data.place_of_supply == books.state
    return _plain({**compute(data.items, intra), "intra_state": intra})


@router.post("/invoices", status_code=201)
def create_invoice(data: InvoiceIn, user_id: int | None = None, viewer: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    _, books = open_books(db, viewer, user_id, write=True)
    if data.invoice_date > today_local():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "An invoice can't be dated in the future")
    v = post_invoice(db, books, data, viewer.id)
    db.commit()
    return vouchers_out(db, books.user_id, [v])[0]


# ---------- bank reconciliation ----------
@router.post("/bank/preview")
async def bank_preview(file: UploadFile = File(...), user_id: int | None = None, viewer: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    open_books(db, viewer, user_id, write=True)
    rows = bank.read_rows(await file.read(), file.filename or "")
    return _plain({"rows": [[str(c) if not isinstance(c, (date, Decimal)) else c for c in r] for r in rows[:40]],
                   "total_rows": len(rows), "guess": bank.guess_mapping(rows)})


@router.post("/bank/import")
async def bank_import(
    ledger_id: int = Form(...), mapping: str = Form(...), file: UploadFile = File(...), user_id: int | None = None,
    viewer: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    _, books = open_books(db, viewer, user_id, write=True)
    ledger = bank.bank_ledger(db, books, ledger_id)
    try:
        cols = bank.ColumnMap(**json.loads(mapping))
    except (ValueError, ValidationError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The column mapping isn't valid")
    rows = bank.read_rows(await file.read(), file.filename or "")
    lines, skipped = bank.parse_lines(rows, cols)
    if not lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No statement lines found: check the header row and the date column")
    result = bank.import_statement(db, books, ledger, file.filename or "statement", lines, skipped)
    db.commit()
    return result


@router.get("/bank/workspace")
def bank_workspace(ledger_id: int, user_id: int | None = None, start: date | None = None, end: date | None = None,
                   viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _, books = open_books(db, viewer, user_id)
    ledger = bank.bank_ledger(db, books, ledger_id)
    result = bank.workspace(db, ledger, start, end)
    db.commit()
    return _plain(result)


class MatchIn(BaseModel):
    ledger_id: int
    statement_line_id: int | None = None
    voucher_line_id: int | None = None
    window_days: int = Field(default=3, ge=0, le=15)


@router.post("/bank/auto-match")
def bank_auto_match(data: MatchIn, user_id: int | None = None, viewer: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    _, books = open_books(db, viewer, user_id, write=True)
    matched = bank.auto_match(db, bank.bank_ledger(db, books, data.ledger_id), data.window_days)
    db.commit()
    return {"auto_matched": matched}


@router.post("/bank/match")
def bank_match(data: MatchIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _, books = open_books(db, viewer, user_id, write=True)
    if not data.statement_line_id or not data.voucher_line_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a statement line and a book entry")
    bank.match(db, bank.bank_ledger(db, books, data.ledger_id), data.statement_line_id, data.voucher_line_id)
    db.commit()
    return {"ok": True}


@router.post("/bank/unmatch")
def bank_unmatch(data: MatchIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _, books = open_books(db, viewer, user_id, write=True)
    if not data.statement_line_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a statement line")
    bank.unmatch(db, bank.bank_ledger(db, books, data.ledger_id), data.statement_line_id)
    db.commit()
    return {"ok": True}


class StatementVoucherIn(BaseModel):
    ledger_id: int  # the bank ledger
    statement_line_id: int
    other_ledger_id: int  # e.g. Bank Charges, Interest Received
    narration: str | None = Field(default=None, max_length=500)


@router.post("/bank/voucher", status_code=201)
def bank_voucher(data: StatementVoucherIn, user_id: int | None = None, viewer: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Book a statement line the books don't have yet (bank charges, interest), and match it."""
    _, books = open_books(db, viewer, user_id, write=True)
    ledger = bank.bank_ledger(db, books, data.ledger_id)
    s = db.get(BankStatementLine, data.statement_line_id)
    if s is None or s.ledger_id != ledger.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Statement line not found")
    if s.matched_line_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "That statement line is already matched")
    other = _ledger(db, books, data.other_ledger_id)
    amount = Decimal(s.deposit or s.withdrawal)
    if s.deposit:
        vtype, lines = RECEIPT, [Line(ledger.id, debit=amount), Line(other.id, credit=amount)]
    else:
        vtype, lines = PAYMENT, [Line(other.id, debit=amount), Line(ledger.id, credit=amount)]
    v = post_voucher(db, books, voucher_type=vtype, voucher_date=s.txn_date, lines=lines, created_by=viewer.id,
                     narration=data.narration or s.description or "As per bank statement", manual=True)
    s.matched_line_id = next(l.id for l in v.lines if l.ledger_id == ledger.id)
    s.match_kind, s.matched_at = "MANUAL", now_local()
    db.commit()
    return vouchers_out(db, books.user_id, [v])[0]


@router.delete("/bank/imports/{import_id}", status_code=204)
def delete_import(import_id: int, user_id: int | None = None, viewer: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Remove an imported statement (its lines and matches). Vouchers created from it stay."""
    _, books = open_books(db, viewer, user_id, write=True)
    batch = db.get(BankImport, import_id)
    if batch is None or batch.user_id != books.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    db.execute(delete(BankStatementLine).where(BankStatementLine.import_id == batch.id))
    db.delete(batch)
    db.commit()
