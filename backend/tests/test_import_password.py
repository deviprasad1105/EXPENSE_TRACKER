"""Password-protected uploads: a locked file asks for its password (423), a wrong one asks again, the right one reads."""

import io

import docx
import msoffcrypto
import pytest
from openpyxl import Workbook
from reportlab.lib import pdfencrypt
from reportlab.pdfgen import canvas

from .conftest import headers

LINE = "05/11/2026  Swiggy order  350.00"


def _pdf(password: str | None) -> bytes:
    buf = io.BytesIO()
    enc = pdfencrypt.StandardEncryption(password, canPrint=1) if password is not None else None
    c = canvas.Canvas(buf, encrypt=enc)
    c.drawString(50, 750, "Date  Description  Amount")
    c.drawString(50, 730, LINE)
    c.save()
    return buf.getvalue()


def _lock(plain: bytes, password: str) -> bytes:
    from msoffcrypto.format.ooxml import OOXMLFile

    out = io.BytesIO()
    OOXMLFile(io.BytesIO(plain)).encrypt(password, out)
    return out.getvalue()


def _xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["Date", "Description", "Amount"])
    ws.append(["2026-11-05", "Swiggy order", 350])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _docx() -> bytes:
    d = docx.Document()
    t = d.add_table(rows=2, cols=3)
    for i, v in enumerate(["Date", "Description", "Amount"]):
        t.cell(0, i).text = v
    for i, v in enumerate(["05/11/2026", "Swiggy order", "350.00"]):
        t.cell(1, i).text = v
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _post(client, h, name, content, password=None):
    data = {"password": password} if password is not None else {}
    return client.post("/api/imports/extract", files={"file": (name, content)}, data=data, headers=h)


@pytest.mark.parametrize("name,content", [
    ("statement.pdf", _pdf("secret")),
    ("book.xlsx", _lock(_xlsx(), "secret")),
    ("note.docx", _lock(_docx(), "secret")),
])
def test_locked_file_asks_for_password(client, make_user, name, content):
    h = headers(make_user("ravi"))
    r = _post(client, h, name, content)
    assert r.status_code == 423 and r.headers["X-Password-Required"] == "required"
    r = _post(client, h, name, content, "wrong")
    assert r.status_code == 423 and r.headers["X-Password-Required"] == "wrong"
    r = _post(client, h, name, content, "secret")
    assert r.status_code == 200, r.text
    rows = [c for t in r.json()["tables"] for row in t["rows"] for c in row]
    assert any("Swiggy" in c for c in rows)


@pytest.mark.parametrize("name,content", [
    ("plain.pdf", _pdf(None)),
    ("owner-only.pdf", _pdf("")),  # encrypted, but opens without a password (common for bank statements)
    ("book.xlsx", _xlsx()),
    ("note.docx", _docx()),
])
def test_unlocked_file_reads_without_asking(client, make_user, name, content):
    r = _post(client, headers(make_user("ravi")), name, content)
    assert r.status_code == 200, r.text
