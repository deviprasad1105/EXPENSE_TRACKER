"""Bank reconciliation: import a statement (CSV or Excel), match it to the bank ledger, and reconcile.

Import works with any bank's layout: the user picks the header row and which column holds the date, description,
withdrawal, deposit (or one signed amount) and balance. Bank-specific presets can be added later as mappings.
Importing the same file twice adds nothing (each line has a fingerprint).

Auto-match: a statement deposit matches a debit to the bank ledger (and a withdrawal a credit) of exactly the
same amount within ±N days, but only when that pairing is unambiguous on both sides. Everything else is matched
by hand, or turned into a voucher (bank charges, interest).
"""

import csv
import hashlib
import io
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException, status
from openpyxl import load_workbook
from pydantic import BaseModel, Field
from sqlalchemy import false, select
from sqlalchemy.orm import Session

from ..timeutil import now_local
from .balances import balances_as_of
from .chart import fy_label, fy_of
from .engine import is_cash_or_bank
from .models import VOUCHER_POSTED, AccLedger, AccSettings, BankImport, BankStatementLine, Voucher, VoucherLine

MAX_BYTES = 5_000_000
MAX_ROWS = 20_000
DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d-%b-%Y", "%d %b %Y", "%d-%b-%y",
                "%d %b %y", "%Y-%m-%d", "%d %B %Y", "%b %d, %Y")
ZERO = Decimal(0)


class ColumnMap(BaseModel):
    header_row: int = Field(ge=0, description="0-based index of the header row in the preview")
    date_col: int = Field(ge=0)
    description_col: int | None = Field(default=None, ge=0)
    reference_col: int | None = Field(default=None, ge=0)
    withdrawal_col: int | None = Field(default=None, ge=0)
    deposit_col: int | None = Field(default=None, ge=0)
    # Alternative to withdrawal/deposit columns: one amount, negative (or "Dr") = withdrawal
    amount_col: int | None = Field(default=None, ge=0)
    balance_col: int | None = Field(default=None, ge=0)


# ---------- reading files ----------
def read_rows(content: bytes, filename: str) -> list[list]:
    if len(content) > MAX_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The statement file is too large (5 MB at most)")
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        try:
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That Excel file couldn't be read")
        ws = wb.worksheets[0]
        rows = [list(r) for _, r in zip(range(MAX_ROWS), ws.iter_rows(values_only=True))]
    elif name.endswith((".csv", ".txt")):
        text = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [r for _, r in zip(range(MAX_ROWS), csv.reader(io.StringIO(text), dialect))]
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload a .csv or .xlsx bank statement")
    return [[c if c is not None else "" for c in r] for r in rows if any(str(c or "").strip() for c in r)]


def guess_mapping(rows: list[list]) -> dict:
    """Best guess at the header row and columns, from common Indian bank statement headings."""
    for i, row in enumerate(rows[:40]):
        cells = [str(c).strip().lower() for c in row]
        if not any("date" in c for c in cells):
            continue

        def find(*words, avoid=()):
            for j, c in enumerate(cells):
                if any(w in c for w in words) and not any(a in c for a in avoid):
                    return j
            return None

        guess = {
            "header_row": i,
            "date_col": find("txn date", "transaction date", "tran date", "date", avoid=("value",)),
            "description_col": find("narration", "description", "particular", "remarks", "details"),
            "reference_col": find("chq", "cheque", "ref"),
            "withdrawal_col": find("withdrawal", "debit", "dr"),
            "deposit_col": find("deposit", "credit", "cr"),
            "balance_col": find("balance"),
            "amount_col": None,
        }
        if guess["withdrawal_col"] is None and guess["deposit_col"] is None:
            guess["amount_col"] = find("amount")
        return guess
    return {"header_row": 0, "date_col": 0, "description_col": 1, "reference_col": None, "withdrawal_col": None,
            "deposit_col": None, "amount_col": 2, "balance_col": None}


def _date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _money(value) -> tuple[Decimal | None, str | None]:
    """'1,23,456.00 Dr' → (123456.00, 'DR'). Blank → (None, None)."""
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)).quantize(Decimal("0.01")), None
    text = str(value).strip().upper()
    if not text:
        return None, None
    side = "DR" if text.endswith("DR") else "CR" if text.endswith("CR") else None
    cleaned = re.sub(r"[^0-9.\-()]", "", text)
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        return Decimal(cleaned).quantize(Decimal("0.01")), side
    except InvalidOperation:
        return None, None


def parse_lines(rows: list[list], m: ColumnMap) -> tuple[list[dict], int]:
    """Statement lines under the header row. Rows without a date or an amount (totals, page headers) are skipped."""
    width = max((len(r) for r in rows), default=0)
    for col in (m.date_col, m.description_col, m.reference_col, m.withdrawal_col, m.deposit_col, m.amount_col, m.balance_col):
        if col is not None and col >= width:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "A chosen column doesn't exist in this file")
    if m.amount_col is None and m.withdrawal_col is None and m.deposit_col is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose the withdrawal and deposit columns, or one amount column")

    def cell(row, col):
        return row[col] if col is not None and col < len(row) else ""

    out, skipped = [], 0
    for row in rows[m.header_row + 1:]:
        d = _date(cell(row, m.date_col))
        if d is None:
            skipped += 1
            continue
        withdrawal = deposit = ZERO
        if m.amount_col is not None:
            amount, side = _money(cell(row, m.amount_col))
            if amount is None:
                skipped += 1
                continue
            if side == "DR" or (side is None and amount < 0):
                withdrawal = abs(amount)
            else:
                deposit = abs(amount)
        else:
            w, _ = _money(cell(row, m.withdrawal_col))
            dep, _ = _money(cell(row, m.deposit_col))
            withdrawal, deposit = abs(w or ZERO), abs(dep or ZERO)
        if not withdrawal and not deposit:
            skipped += 1
            continue
        bal, side = _money(cell(row, m.balance_col)) if m.balance_col is not None else (None, None)
        if bal is not None and side == "DR":
            bal = -abs(bal)
        out.append({
            "txn_date": d, "withdrawal": withdrawal, "deposit": deposit, "balance": bal,
            "description": str(cell(row, m.description_col)).strip()[:300] or None,
            "reference": str(cell(row, m.reference_col)).strip()[:60] or None,
        })
    return out, skipped


# ---------- importing ----------
def bank_ledger(db: Session, books: AccSettings, ledger_id: int) -> AccLedger:
    ledger = db.get(AccLedger, ledger_id)
    if ledger is None or ledger.user_id != books.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ledger not found")
    if not is_cash_or_bank(ledger):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a bank (or card / wallet) ledger to reconcile")
    return ledger


def import_statement(db: Session, books: AccSettings, ledger: AccLedger, filename: str, lines: list[dict], skipped: int) -> dict:
    existing = set(db.scalars(select(BankStatementLine.fingerprint).where(BankStatementLine.ledger_id == ledger.id)))
    batch = BankImport(user_id=books.user_id, ledger_id=ledger.id, filename=filename[:200])
    db.add(batch)
    db.flush()
    seen: Counter = Counter()
    added = duplicates = 0
    for line in lines:
        key = f"{line['txn_date']}|{line['description']}|{line['reference']}|{line['withdrawal']}|{line['deposit']}|{line['balance']}"
        seen[key] += 1
        fingerprint = hashlib.sha256(f"{key}|{seen[key]}".encode()).hexdigest()
        if fingerprint in existing:
            duplicates += 1
            continue
        existing.add(fingerprint)
        db.add(BankStatementLine(user_id=books.user_id, ledger_id=ledger.id, import_id=batch.id, fingerprint=fingerprint, **line))
        added += 1
    batch.line_count, batch.skipped_count = added, skipped + duplicates
    db.flush()
    matched = auto_match(db, ledger)
    return {"import_id": batch.id, "added": added, "duplicates": duplicates, "skipped": skipped, "auto_matched": matched}


# ---------- matching ----------
def _book_lines(db: Session, ledger: AccLedger, upto: date | None = None):
    """(line, voucher) on the bank ledger from live vouchers: a reversed voucher and its reversal never hit the bank."""
    stmt = (
        select(VoucherLine, Voucher)
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .where(VoucherLine.ledger_id == ledger.id, Voucher.status == VOUCHER_POSTED, Voucher.is_reversal == false())
        .order_by(Voucher.voucher_date, Voucher.id)
    )
    if upto:
        stmt = stmt.where(Voucher.voucher_date <= upto)
    return db.execute(stmt).all()


def release_stale_matches(db: Session, ledger: AccLedger) -> None:
    """Unmatch statement lines whose voucher has since been reversed (its source was edited or deleted)."""
    live = {l.id for l, _ in _book_lines(db, ledger)}
    for s in db.scalars(select(BankStatementLine).where(BankStatementLine.ledger_id == ledger.id,
                                                        BankStatementLine.matched_line_id.is_not(None))):
        if s.matched_line_id not in live:
            s.matched_line_id, s.match_kind, s.matched_at = None, None, None


def auto_match(db: Session, ledger: AccLedger, window_days: int = 3) -> int:
    release_stale_matches(db, ledger)
    statement = list(db.scalars(select(BankStatementLine).where(BankStatementLine.ledger_id == ledger.id,
                                                                BankStatementLine.matched_line_id.is_(None))))
    taken = set(db.scalars(select(BankStatementLine.matched_line_id).where(BankStatementLine.matched_line_id.is_not(None))))
    books = [(l, v) for l, v in _book_lines(db, ledger) if l.id not in taken]
    by_key = defaultdict(list)
    for l, v in books:
        # Money into the bank is a debit to its ledger; money out is a credit
        by_key[("IN", Decimal(l.debit)) if l.debit > 0 else ("OUT", Decimal(l.credit))].append((l, v))
    window = timedelta(days=window_days)
    candidates = {}
    for s in statement:
        key = ("IN", Decimal(s.deposit)) if s.deposit > 0 else ("OUT", Decimal(s.withdrawal))
        candidates[s.id] = [l for l, v in by_key.get(key, []) if abs(v.voucher_date - s.txn_date) <= window]
    claims = Counter(l.id for ls in candidates.values() for l in ls)
    matched = 0
    for s in statement:
        options = candidates[s.id]
        if len(options) == 1 and claims[options[0].id] == 1:
            s.matched_line_id, s.match_kind, s.matched_at = options[0].id, "AUTO", now_local()
            matched += 1
    db.flush()
    return matched


def match(db: Session, ledger: AccLedger, statement_line_id: int, voucher_line_id: int) -> None:
    s = db.get(BankStatementLine, statement_line_id)
    if s is None or s.ledger_id != ledger.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Statement line not found")
    if s.matched_line_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "That statement line is already matched")
    live = {l.id: (l, v) for l, v in _book_lines(db, ledger)}
    if voucher_line_id not in live:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose an entry in this bank ledger that hasn't been reversed")
    if db.scalar(select(BankStatementLine.id).where(BankStatementLine.matched_line_id == voucher_line_id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "That book entry is already matched to another statement line")
    line, _ = live[voucher_line_id]
    if (s.deposit > 0) != (line.debit > 0) or (s.deposit or s.withdrawal) != (line.debit or line.credit):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The amount and direction must be the same on both sides")
    s.matched_line_id, s.match_kind, s.matched_at = voucher_line_id, "MANUAL", now_local()


def unmatch(db: Session, ledger: AccLedger, statement_line_id: int) -> None:
    s = db.get(BankStatementLine, statement_line_id)
    if s is None or s.ledger_id != ledger.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Statement line not found")
    s.matched_line_id, s.match_kind, s.matched_at = None, None, None


def workspace(db: Session, ledger: AccLedger, start: date | None, end: date | None) -> dict:
    """Statement lines and book entries side by side, for the matching screen."""
    release_stale_matches(db, ledger)
    stmt = select(BankStatementLine).where(BankStatementLine.ledger_id == ledger.id)
    if start:
        stmt = stmt.where(BankStatementLine.txn_date >= start)
    if end:
        stmt = stmt.where(BankStatementLine.txn_date <= end)
    statement = list(db.scalars(stmt.order_by(BankStatementLine.txn_date, BankStatementLine.id)))
    matched_ids = set(db.scalars(select(BankStatementLine.matched_line_id).where(
        BankStatementLine.ledger_id == ledger.id, BankStatementLine.matched_line_id.is_not(None))))
    book = []
    for l, v in _book_lines(db, ledger, upto=end):
        if start and v.voucher_date < start - timedelta(days=31):
            continue
        book.append({"line_id": l.id, "voucher_id": v.id, "date": v.voucher_date, "voucher_type": v.voucher_type,
                     "voucher_no": v.voucher_no, "narration": v.narration, "debit": l.debit, "credit": l.credit,
                     "matched": l.id in matched_ids})
    return {
        "ledger": {"id": ledger.id, "name": ledger.name},
        "statement": [{"id": s.id, "date": s.txn_date, "description": s.description, "reference": s.reference,
                       "withdrawal": s.withdrawal, "deposit": s.deposit, "balance": s.balance,
                       "matched_line_id": s.matched_line_id, "match_kind": s.match_kind} for s in statement],
        "book": book,
        "imports": [{"id": b.id, "filename": b.filename, "imported_at": b.imported_at, "line_count": b.line_count,
                     "skipped_count": b.skipped_count}
                    for b in db.scalars(select(BankImport).where(BankImport.ledger_id == ledger.id).order_by(BankImport.id.desc()))],
    }


# ---------- the reconciliation statement ----------
def reconciliation(db: Session, books: AccSettings, ledger: AccLedger, as_of: date) -> dict:
    """Balance as per books, adjusted by what only one side has seen, gives the balance as per the bank."""
    release_stale_matches(db, ledger)
    book_balance = balances_as_of(db, books.user_id, as_of, [ledger])[ledger.id]
    statement = list(db.scalars(select(BankStatementLine).where(BankStatementLine.ledger_id == ledger.id,
                                                                BankStatementLine.txn_date <= as_of)
                                .order_by(BankStatementLine.txn_date, BankStatementLine.id)))
    first_statement = statement[0].txn_date if statement else None
    cleared = {s.matched_line_id for s in statement if s.matched_line_id}

    not_presented, not_credited = [], []
    for l, v in _book_lines(db, ledger, upto=as_of):
        # Entries from before the first imported statement can't be checked against it
        if first_statement is None or v.voucher_date < first_statement - timedelta(days=7) or l.id in cleared:
            continue
        item = {"date": v.voucher_date, "text": f"{v.voucher_type.title()} #{v.voucher_no}" + (f" – {v.narration}" if v.narration else ""),
                "amount": Decimal(l.credit) or Decimal(l.debit), "voucher_id": v.id}
        (not_presented if l.credit > 0 else not_credited).append(item)
    bank_only_in = [{"date": s.txn_date, "text": s.description or "Credit by bank", "amount": Decimal(s.deposit), "statement_line_id": s.id}
                    for s in statement if not s.matched_line_id and s.deposit > 0]
    bank_only_out = [{"date": s.txn_date, "text": s.description or "Debit by bank", "amount": Decimal(s.withdrawal), "statement_line_id": s.id}
                     for s in statement if not s.matched_line_id and s.withdrawal > 0]

    def sec(title, items, sign):
        total = sum((i["amount"] for i in items), ZERO)
        return {"title": title, "items": items, "total": sign * total}

    sections = [
        sec("Add: Cheques issued / payments not yet debited by the bank", not_presented, 1),
        sec("Less: Deposits not yet credited by the bank", not_credited, -1),
        sec("Add: Credited by the bank but not in the books", bank_only_in, 1),
        sec("Less: Debited by the bank but not in the books", bank_only_out, -1),
    ]
    bank_balance = book_balance + sum((s["total"] for s in sections), ZERO)
    with_balance = [s for s in statement if s.balance is not None]
    statement_balance = Decimal(with_balance[-1].balance) if with_balance else None
    return {
        "ledger": {"id": ledger.id, "name": ledger.name}, "as_of": as_of,
        "period": f"As at {as_of:%d %b %Y} (FY {fy_label(fy_of(as_of))})",
        "books_balance": book_balance, "sections": sections, "bank_balance": bank_balance,
        "statement_balance": statement_balance,
        "difference": (bank_balance - statement_balance) if statement_balance is not None else None,
        "first_statement_date": first_statement,
        "unmatched_statement_lines": len(bank_only_in) + len(bank_only_out),
    }
