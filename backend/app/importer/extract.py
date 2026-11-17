"""Read an uploaded file into tables of text cells, whatever its format.

- CSV / TXT: the delimiter is detected (comma, semicolon, tab, pipe).
- Excel: every sheet of an .xlsx / .xlsm (openpyxl) or old .xls (xlrd) is a table. Dates stay dates.
- PDF: tables are taken from each page (pdfplumber); a table continuing on the next page is joined to it. A PDF
  without ruled tables falls back to its text, split into columns where there are gaps of two or more spaces.
  A scanned PDF (only images, no text) can't be read.
- Word .docx: its tables, else its paragraphs split the same way as PDF text.
- Password-protected files (PDF, and Excel / Word encrypted with a password to open) are read with the password the
  user gives; without it (or with a wrong one) a 423 asks the page to prompt for it. The password is never stored.
- Old Word .doc can't be read without Word or LibreOffice, which a server function doesn't have: the user is asked
  to save it as .docx or PDF.

Cells come back as strings ('' for blanks); dates as YYYY-MM-DD. Nothing here decides what a column means.
"""

import csv
import hashlib
import io
import re
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime

from fastapi import HTTPException, status

from ..config import settings

MAX_LINES = 20_000  # rows read from a file, before anything is filtered out
MAX_PDF_PAGES = 100
MAX_CELL = 300
TEXT_EXT = (".csv", ".txt", ".tsv")
EXCEL_EXT = (".xlsx", ".xlsm")
SUPPORTED = TEXT_EXT + EXCEL_EXT + (".xls", ".pdf", ".docx", ".doc")

# A date or an amount inside a line of text (used to keep transaction lines and drop headings / page furniture)
_DATE_TOKEN = re.compile(
    r"\b(\d{1,4}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{1,2}[\s\-](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-,]*\d{2,4}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+\d{2,4})\b",
    re.I,
)
_MONEY_TOKEN = re.compile(r"[-+(]?\s*(?:₹|rs\.?|inr)?\s*\d[\d,]*(?:\.\d{1,2})\b|\b\d[\d,]{2,}\b", re.I)
_SUMMARY_TEXT = re.compile(r"opening\s*bal|closing\s*bal|\btotal\b|\b(dr|cr|debit|credit)s?\.?\s*count|"
                           r"\b(debits|credits|withdrawals|deposits)\b|brought\s*forward|carried\s*forward", re.I)
_LINE_SPLIT = re.compile(r"\t|\s{2,}|\s*\|\s*")
# "05/11/2026 Swiggy order 350.00" (single spaces): date first, amount last
_LINE_PARTS = re.compile(r"^\s*(?P<date>" + _DATE_TOKEN.pattern[2:-2] + r")\s+[-–:]?\s*(?P<desc>.*?)\s*[-–:]?\s+"
                         r"(?P<amount>[-+]?\s*(?:₹|rs\.?|inr)?\s*\d[\d,]*(?:\.\d{1,2})?(?:\s*(?:dr|cr))?)\s*$", re.I)


@dataclass
class Table:
    name: str
    source: str  # "sheet", "table" or "text"
    rows: list[list[str]] = field(default_factory=list)
    # Rows in the file's table when there were more than MAX_LINES (only those are read)
    total: int | None = None


def bad(message: str):
    raise HTTPException(status.HTTP_400_BAD_REQUEST, message)


PASSWORD_HEADER = "X-Password-Required"


def need_password(wrong: bool):
    """423 tells the page to ask for the file's password (and send the file again with it)."""
    raise HTTPException(
        status.HTTP_423_LOCKED,
        "That password isn't right. Try again." if wrong else "This file is password-protected. Enter its password to open it.",
        headers={PASSWORD_HEADER: "wrong" if wrong else "required"},
    )


_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _decrypt_office(content: bytes, password: str | None) -> bytes:
    """An Excel / Word file locked with a password to open is an encrypted container: unlock it, or ask for the
    password. Files that aren't encrypted come back unchanged."""
    if not content.startswith(_OLE_MAGIC):
        return content  # .xlsx / .docx without encryption are plain zip files
    import msoffcrypto
    from msoffcrypto.exceptions import DecryptionError, InvalidKeyError

    try:
        office = msoffcrypto.OfficeFile(io.BytesIO(content))
        if not office.is_encrypted():
            return content
    except Exception:
        return content  # not something msoffcrypto knows: let the reader report it
    if not password:
        need_password(False)
    out = io.BytesIO()
    try:
        office.load_key(password=password, verify_password=True)
        office.decrypt(out)
    except (InvalidKeyError, DecryptionError):
        need_password(True)
    except Exception:
        bad("This password-protected file couldn't be unlocked. Open it, remove the password, save it and upload it again.")
    return out.getvalue()


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def file_type(filename: str) -> str:
    name = filename.lower().strip()
    ext = name[name.rfind("."):] if "." in name else ""
    if ext not in SUPPORTED:
        bad("This file type isn't supported. Upload Excel (.xlsx, .xls), CSV, PDF or Word (.docx).")
    return ext.lstrip(".")


def cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if not (value.hour or value.minute) else value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        # Excel stores 0.1 + 0.2 as 0.30000000000000004: round off the float noise, keep real paise
        value = round(value, 6)
        return str(int(value)) if value == int(value) else repr(value)
    return re.sub(r"\s+", " ", str(value)).strip()[:MAX_CELL]


def _clean(rows) -> list[list[str]]:
    out = []
    for r in rows:
        cells = [cell_text(c) for c in r]
        while cells and not cells[-1]:
            cells.pop()
        if any(cells):
            out.append(cells)
        if len(out) >= MAX_LINES:
            break
    return out


# ---------- formats ----------
def _text(content: bytes) -> list[Table]:
    text = None
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = content.decode(enc)
            if enc == "utf-16" and "\x00" in text:
                continue
            break
        except UnicodeDecodeError:
            continue
    if text is None or "\x00" in text:
        bad("This file doesn't look like text. Save it as CSV (comma separated) and upload it again.")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = _clean(csv.reader(io.StringIO(text), dialect))
    total = sum(1 for _ in csv.reader(io.StringIO(text), dialect)) if len(rows) >= MAX_LINES else None
    return [Table("CSV", "sheet", rows, total)]


def _xlsx(content: bytes) -> list[Table]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        bad("This Excel file couldn't be opened. Check it isn't damaged.")
    tables = []
    for ws in wb.worksheets:
        rows = _clean(r for _, r in zip(range(MAX_LINES), ws.iter_rows(values_only=True)))
        if rows:
            tables.append(Table(ws.title, "sheet", rows, ws.max_row if len(rows) >= MAX_LINES and ws.max_row else None))
    return tables


def _xls(content: bytes) -> list[Table]:
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=content)
    except Exception:
        bad("This .xls file couldn't be opened. Open it in Excel and save it as .xlsx, then upload that.")
    tables = []
    for sheet in book.sheets():
        rows = []
        for i in range(min(sheet.nrows, MAX_LINES)):
            row = []
            for c in sheet.row(i):
                if c.ctype == xlrd.XL_CELL_DATE:
                    try:
                        row.append(xlrd.xldate.xldate_as_datetime(c.value, book.datemode))
                        continue
                    except Exception:
                        pass
                row.append(c.value)
            rows.append(row)
        rows = _clean(rows)
        if rows:
            tables.append(Table(sheet.name, "sheet", rows, sheet.nrows if sheet.nrows > MAX_LINES else None))
    return tables


def text_rows(lines: list[str]) -> list[list[str]]:
    """Lines of a document → rows: split at tabs, pipes or runs of 2+ spaces. A line that doesn't split but reads
    '<date> <description> <amount>' becomes those three cells. Lines with neither a date nor an amount are dropped,
    except a heading line that names a date column."""
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        cells = [c.strip() for c in _LINE_SPLIT.split(line) if c.strip()]
        has_date, has_money = bool(_DATE_TOKEN.search(line)), bool(_MONEY_TOKEN.search(line))
        if len(cells) >= 2 and (has_date and has_money):
            rows.append(cells)
        elif has_date and has_money and (m := _LINE_PARTS.match(line)):
            rows.append([m["date"], m["desc"], m["amount"]])
        elif len(cells) >= 3 and re.search(r"\bdate\b", line, re.I) and not has_money:
            rows.append(cells)  # a heading row: kept so its column names can be used
        elif _SUMMARY_TEXT.search(line) and (has_money or re.search(r"\d", line)):
            rows.append(cells)  # a statement's totals / balances: used to check the records, never imported
    return rows[:MAX_LINES]


def _merge_tables(tables: list[list[list[str]]]) -> list[list[list[str]]]:
    """Join a table that continues on the next page (same number of columns), dropping its repeated heading row."""
    merged: list[list[list[str]]] = []
    for t in tables:
        if merged and len(t[0]) == len(merged[-1][0]):
            prev = merged[-1]
            prev.extend(t[1:] if t[0] == prev[0] else t)
        else:
            merged.append([list(r) for r in t])
    return merged


def _pdf(content: bytes, password: str | None = None) -> list[Table]:
    import pdfplumber
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    try:
        # Many bank statements are encrypted with only an owner password: they open with an empty one
        pdf = pdfplumber.open(io.BytesIO(content), password=password or "")
    except PDFPasswordIncorrect:
        need_password(bool(password))
    except Exception as exc:
        if isinstance(getattr(exc, "__cause__", None), PDFPasswordIncorrect) or "PDFPasswordIncorrect" in repr(exc):
            need_password(bool(password))
        bad("This PDF couldn't be opened. Check it isn't damaged.")
    with pdf:
        if len(pdf.pages) > MAX_PDF_PAGES:
            bad(f"This PDF has {len(pdf.pages)} pages; up to {MAX_PDF_PAGES} can be read. Split it, or export the data as Excel or CSV.")
        found, lines = [], []
        for page in pdf.pages:
            for t in page.extract_tables() or []:
                rows = [[cell_text(c) for c in r] for r in t if any((c or "").strip() for c in r)]
                if len(rows) >= 2:
                    found.append(rows)
            try:
                text = page.extract_text(layout=True) or ""
            except Exception:
                text = page.extract_text() or ""
            lines.extend(text.splitlines())
    if not "".join(lines).strip():
        bad("This PDF has no text (it looks like a scanned image), so it can't be read. Export the records as "
            "Excel or CSV, or add them by hand below.")
    tables = [Table(f"Table {i + 1}", "table", _clean(t)) for i, t in enumerate(_merge_tables(found))]
    text = text_rows(lines)
    if text:
        tables.append(Table("Text of the document", "text", text))
    return [t for t in tables if t.rows]


def _docx(content: bytes) -> list[Table]:
    import docx

    try:
        document = docx.Document(io.BytesIO(content))
    except Exception:
        bad("This Word file couldn't be opened. Check it is a .docx and isn't damaged.")
    found = []
    for t in document.tables:
        rows = []
        for r in t.rows:
            cells, last = [], None
            for c in r.cells:
                # A merged cell repeats in python-docx: keep it once
                if c._tc is last:
                    continue
                last = c._tc
                cells.append(c.text)
            rows.append(cells)
        rows = _clean(rows)
        if len(rows) >= 2:
            found.append(rows)
    tables = [Table(f"Table {i + 1}", "table", t) for i, t in enumerate(_merge_tables(found))]
    text = text_rows([p.text for p in document.paragraphs])
    if text:
        tables.append(Table("Text of the document", "text", text))
    return tables


def gunzip(content: bytes, max_bytes: int) -> bytes:
    """A file the page compressed before uploading; refused when it is bigger than `max_bytes` unpacked."""
    try:
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out = d.decompress(content, max_bytes + 1)
    except zlib.error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The upload was damaged on the way. Try again.")
    if len(out) > max_bytes or d.unconsumed_tail:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"The file is over {max_bytes / 1_000_000:g} MB")
    return out


def extract(content: bytes, filename: str, password: str | None = None) -> list[Table]:
    if not content:
        bad("The file is empty.")
    if len(content) > settings.import_max_bytes:
        bad(f"The file is too large: {settings.import_max_bytes // 1_000_000} MB at most.")
    ext = "." + file_type(filename)
    if ext == ".doc":
        bad("Old Word .doc files can't be read here. Open the file in Word and save it as .docx (or PDF), then upload that.")
    if ext in EXCEL_EXT + (".xls", ".docx"):
        content = _decrypt_office(content, password)
    if ext in TEXT_EXT:
        tables = _text(content)
    elif ext in EXCEL_EXT:
        tables = _xlsx(content)
    elif ext == ".xls":
        tables = _xls(content)
    elif ext == ".pdf":
        tables = _pdf(content, password)
    else:
        tables = _docx(content)
    tables = [t for t in tables if t.rows]
    if not tables:
        bad("No table or list of records was found in this file. Add the records by hand below, or upload Excel or CSV.")
    return tables
