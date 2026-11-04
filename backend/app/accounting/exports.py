"""Excel and PDF exports of the accounting reports, Tally-style.

Each report is first turned into a `Doc` (title, period, columns, rows); one writer per format renders any Doc.
Two-sided statements (Trading, P&L, Balance Sheet) use the horizontal layout: Particulars | Amount | Particulars |
Amount. Money uses Indian digit grouping (12,34,567.89). User text is escaped / kept as text (see export_safe).
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from io import BytesIO
from itertools import zip_longest

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..services.export_safe import defuse_formulas, pdf_text
from ..timeutil import now_local
from .chart import fy_label

ZERO = Decimal(0)
# Indian grouping in Excel: crores and lakhs need their own sections
XL_INR = r"[>=10000000]##\,##\,##\,##0.00;[>=100000]##\,##\,##0.00;##,##0.00"
TYPE_ABBR = {"PAYMENT": "Pymt", "RECEIPT": "Rcpt", "CONTRA": "Ctra", "JOURNAL": "Jrnl", "SALES": "Sale", "PURCHASE": "Purc"}


def inr(value) -> str:
    """12345678.5 → '1,23,45,678.50'."""
    d = Decimal(value or 0).quantize(Decimal("0.01"))
    sign, whole, frac = ("-" if d < 0 else ""), *f"{abs(d):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return sign + ",".join(parts + [tail]) + "." + frac


@dataclass
class Col:
    label: str
    money: bool = False
    width: float = 30  # mm in the PDF; Excel widths are derived from it


@dataclass
class Row:
    cells: list
    bold: bool = False
    indent: int = 0
    shade: bool = False


@dataclass
class Doc:
    title: str
    subtitle: str
    business: str
    columns: list[Col]
    rows: list[Row] = field(default_factory=list)
    landscape: bool = False
    note: str | None = None


def _money_or_blank(v):
    return None if v is None or v == "" or (isinstance(v, Decimal) and v == 0) else v


# ---------- report → Doc ----------
def journal_doc(r: dict, business: str) -> Doc:
    doc = Doc("Journal Register", r["period"], business, [
        Col("Date", width=22), Col("Voucher", width=22), Col("Particulars", width=78), Col("Debit", True, 28),
        Col("Credit", True, 28)], landscape=False)
    for e in r["entries"]:
        tag = " (reversal)" if e["is_reversal"] else (" (reversed)" if e["status"] == "REVERSED" else "")
        first = True
        for l in e["lines"]:
            dr, cr = Decimal(l["debit"]), Decimal(l["credit"])
            name = (l["ledger"] + " Dr") if dr else ("    To " + l["ledger"])
            doc.rows.append(Row([e["date"].strftime("%d-%m-%Y") if first else "",
                                 f"{TYPE_ABBR[e['voucher_type']]} {e['voucher_no']}{tag}" if first else "",
                                 name, _money_or_blank(dr), _money_or_blank(cr)]))
            first = False
        if e["narration"]:
            doc.rows.append(Row(["", "", f"({e['narration']})", None, None], indent=1))
    doc.rows.append(Row(["", "", "Total", r["total_debit"], r["total_credit"]], bold=True, shade=True))
    return doc


def ledger_doc(r: dict, business: str) -> Doc:
    doc = Doc(f"Ledger: {r['ledger']['name']}", f"{r['ledger']['group']} · {r['period']}", business, [
        Col("Date", width=20), Col("Particulars", width=60), Col("Vch", width=18), Col("Debit", True, 26),
        Col("Credit", True, 26), Col("Balance", True, 28), Col("", width=8)], landscape=False)
    doc.rows.append(Row(["", "Opening Balance", "", None, None, r["opening"], r["opening_side"]], bold=True))
    for e in r["entries"]:
        text = e["particulars"] + (f" — {e['narration']}" if e["narration"] else "")
        doc.rows.append(Row([e["date"].strftime("%d-%m-%Y"), text, f"{TYPE_ABBR[e['voucher_type']]} {e['voucher_no']}",
                             _money_or_blank(e["debit"]), _money_or_blank(e["credit"]), e["balance"], e["balance_side"]]))
    doc.rows.append(Row(["", "Total", "", r["total_debit"], r["total_credit"], None, ""], bold=True, shade=True))
    doc.rows.append(Row(["", "Closing Balance", "", None, None, r["closing"], r["closing_side"]], bold=True))
    return doc


def trial_balance_doc(r: dict, business: str, grouped: bool) -> Doc:
    doc = Doc("Trial Balance", r["period"], business, [
        Col("Particulars", width=78), Col("Opening Dr", True, 28), Col("Opening Cr", True, 28), Col("Debit", True, 28),
        Col("Credit", True, 28), Col("Closing Dr", True, 28), Col("Closing Cr", True, 28)], landscape=True)

    def cells(name, x):
        o, c = x["opening"], x["closing"]
        return [name, _money_or_blank(o if o > 0 else ZERO), _money_or_blank(-o if o < 0 else ZERO),
                _money_or_blank(x["debit"]), _money_or_blank(x["credit"]),
                _money_or_blank(c if c > 0 else ZERO), _money_or_blank(-c if c < 0 else ZERO)]

    if grouped:
        for g in r["groups"]:
            doc.rows.append(Row(cells(g["name"], g), bold=True))
            if len(g["ledgers"]) > 1 or g["ledgers"][0]["name"] != g["name"]:
                for l in g["ledgers"]:
                    doc.rows.append(Row(cells(l["name"], l), indent=1))
    else:
        for l in r["rows"]:
            doc.rows.append(Row(cells(l["name"], l)))
    t = r["totals"]
    doc.rows.append(Row(["Grand Total", t["opening_debit"], t["opening_credit"], t["debit"], t["credit"],
                         t["closing_debit"], t["closing_credit"]], bold=True, shade=True))
    return doc


def _t_doc(title: str, subtitle: str, business: str, left_title: str, right_title: str,
           left: list[tuple], right: list[tuple], total_left, total_right, note=None) -> Doc:
    """Horizontal T-account: each side is a list of (text, amount, bold, indent)."""
    doc = Doc(title, subtitle, business, [Col(left_title, width=82), Col("Amount", True, 32),
                                          Col(right_title, width=82), Col("Amount", True, 32)], landscape=True, note=note)
    for l, rr in zip_longest(left, right, fillvalue=("", None, False, 0)):
        doc.rows.append(Row([("    " * l[3]) + l[0], l[1], ("    " * rr[3]) + rr[0], rr[1]], bold=l[2] and rr[2]))
    doc.rows.append(Row(["Total", total_left, "Total", total_right], bold=True, shade=True))
    return doc


def _side(sections, title_prefix="") -> list[tuple]:
    out = []
    for s in sections:
        if not s["items"]:
            continue
        if len(s["items"]) == 1 and s["items"][0]["name"] == s["title"]:
            out.append((s["title"], s["total"], True, 0))
            continue
        out.append((title_prefix + s["title"], s["total"], True, 0))
        out += [(i["name"], i["amount"], False, 1) for i in s["items"]]
    return out


def trading_doc(r: dict, business: str) -> Doc:
    t = r["trading"]
    left, right = _side(t["debit"]), _side(t["credit"])
    gp = t["gross_profit"]
    if gp > 0:
        left.append(("Gross Profit c/o", gp, True, 0))
    elif gp < 0:
        right.append(("Gross Loss c/o", -gp, True, 0))
    note = "Closing stock for this year hasn't been entered." if r["closing_stock_missing"] else None
    return _t_doc("Trading Account", r["period"], business, "Particulars (Dr)", "Particulars (Cr)", left, right,
                  t["total"], t["total"], note)


def profit_loss_doc(r: dict, business: str) -> Doc:
    pl = r["profit_loss"]
    gp, net = pl["gross_profit"], pl["net_profit"]
    left = ([("Gross Loss b/f", -gp, True, 0)] if gp < 0 else []) + _side(pl["debit"])
    right = ([("Gross Profit b/f", gp, True, 0)] if gp > 0 else []) + _side(pl["credit"])
    if net > 0:
        left.append(("Net Profit", net, True, 0))
    elif net < 0:
        right.append(("Net Loss", -net, True, 0))
    note = "Drawings (personal spending) are deducted from capital in the Balance Sheet, not charged here."
    return _t_doc("Profit & Loss Account", r["period"], business, "Particulars (Dr)", "Particulars (Cr)", left, right,
                  pl["total"], pl["total"], note)


def balance_sheet_doc(r: dict, business: str) -> Doc:
    note = "Closing stock for this year hasn't been entered." if r["closing_stock_missing"] else None
    return _t_doc("Balance Sheet", r["period"], business, "Liabilities", "Assets", _side(r["liabilities"]),
                  _side(r["assets"]), r["total_liabilities"], r["total_assets"], note)


def cash_flow_doc(r: dict, business: str) -> Doc:
    doc = Doc("Cash Flow Statement (Indirect Method)", r["period"], business,
              [Col("Particulars", width=130), Col("Amount", True, 35), Col("Total", True, 35)])
    for s in r["sections"]:
        doc.rows.append(Row([s["title"], None, None], bold=True))
        for i in s["items"]:
            doc.rows.append(Row([i["name"], i["amount"], None], indent=1))
        doc.rows.append(Row([f"Net cash from {s['title'].split('from ')[-1]}", None, s["total"]], bold=True, shade=True))
    doc.rows += [
        Row(["Net increase / (decrease) in cash and cash equivalents", None, r["net_change"]], bold=True),
        Row(["Cash and cash equivalents at the beginning", None, r["opening_cash"]]),
        Row(["Cash and cash equivalents at the end", None, r["closing_cash"]], bold=True, shade=True),
    ]
    if r["closing_stock_missing"]:
        doc.note = "Closing stock for this year hasn't been entered."
    return doc


def brs_doc(r: dict, business: str) -> Doc:
    doc = Doc(f"Bank Reconciliation: {r['ledger']['name']}", r["period"], business,
              [Col("Date", width=22), Col("Particulars", width=110), Col("Amount", True, 32), Col("Total", True, 32)])
    doc.rows.append(Row(["", "Balance as per books", None, r["books_balance"]], bold=True))
    for s in r["sections"]:
        if not s["items"]:
            continue
        doc.rows.append(Row(["", s["title"], None, None], bold=True))
        for i in s["items"]:
            doc.rows.append(Row([i["date"].strftime("%d-%m-%Y"), i["text"], i["amount"], None], indent=1))
        doc.rows.append(Row(["", "", None, s["total"]], shade=True))
    doc.rows.append(Row(["", "Balance as per bank statement", None, r["bank_balance"]], bold=True, shade=True))
    if r.get("statement_balance") is not None:
        doc.rows.append(Row(["", "Closing balance on the imported statement", None, r["statement_balance"]]))
    return doc


# ---------- writers ----------
def to_xlsx(doc: Doc) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = doc.title[:31].replace(":", " ").replace("/", " ")
    width = len(doc.columns)
    ws.cell(row=1, column=1, value=doc.business).font = Font(bold=True, size=13)
    ws.cell(row=2, column=1, value=doc.title).font = Font(bold=True, size=12)
    ws.cell(row=3, column=1, value=doc.subtitle).font = Font(italic=True, color="555555")
    for r in (1, 2, 3):
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=width)
    thin = Side(style="thin", color="BFBFBF")
    head = 5
    for c, col in enumerate(doc.columns, start=1):
        cell = ws.cell(row=head, column=c, value=col.label)
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F4E79")
        cell.alignment = Alignment(horizontal="right" if col.money else "left")
        ws.column_dimensions[get_column_letter(c)].width = max(10, col.width * 0.55)
    for i, row in enumerate(doc.rows, start=head + 1):
        for c, (col, value) in enumerate(zip(doc.columns, row.cells), start=1):
            if isinstance(value, date):
                value = value.strftime("%d-%m-%Y")
            cell = ws.cell(row=i, column=c, value=float(value) if col.money and isinstance(value, Decimal) else value)
            if col.money:
                cell.number_format = XL_INR
            elif row.indent and c in (1, 2, 3):
                cell.alignment = Alignment(indent=row.indent * 2)
            if row.bold:
                cell.font = Font(bold=True)
            if row.shade:
                cell.fill = PatternFill("solid", fgColor="EEF2F7")
                cell.border = Border(top=thin)
    if doc.note:
        ws.cell(row=head + len(doc.rows) + 2, column=1, value=doc.note).font = Font(italic=True, color="AA5500")
    ws.cell(row=head + len(doc.rows) + 3, column=1, value=f"Generated {now_local():%d %b %Y %H:%M}").font = Font(color="888888")
    ws.freeze_panes = ws.cell(row=head + 1, column=1)
    defuse_formulas(wb)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pdf(doc: Doc) -> bytes:
    buf = BytesIO()
    size = landscape(A4) if doc.landscape else A4
    pdf = SimpleDocTemplate(buf, pagesize=size, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=doc.title)
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=8, leading=10)
    bold = small.clone("smallbold", fontName="Helvetica-Bold")
    story = [
        Paragraph(pdf_text(doc.business), styles["Title"]),
        Paragraph(f"<b>{pdf_text(doc.title)}</b>", styles["Heading3"]),
        Paragraph(pdf_text(doc.subtitle), styles["Normal"]),
        Spacer(1, 6),
    ]
    available = size[0] - 24 * mm
    widths = [c.width for c in doc.columns]
    widths = [w * available / sum(widths) for w in widths]
    data = [[c.label for c in doc.columns]]
    styling = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
    ]
    for c, col in enumerate(doc.columns):
        if col.money:
            styling.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    for i, row in enumerate(doc.rows, start=1):
        cells = []
        for col, value in zip(doc.columns, row.cells):
            if col.money:
                cells.append("" if value is None else inr(value))
            else:
                text = value.strftime("%d-%m-%Y") if isinstance(value, date) else ("" if value is None else str(value))
                indent = "&nbsp;" * 4 * row.indent if col is doc.columns[0] or col is doc.columns[min(2, len(doc.columns) - 1)] else ""
                cells.append(Paragraph(indent + pdf_text(text), bold if row.bold else small))
        data.append(cells)
        if row.bold:
            styling.append(("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"))
        if row.shade:
            styling.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#EEF2F7")))
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle(styling))
    story.append(table)
    if doc.note:
        story += [Spacer(1, 6), Paragraph(f"<i>{pdf_text(doc.note)}</i>", small)]
    story += [Spacer(1, 4), Paragraph(f"Generated {now_local():%d %b %Y %H:%M}", small)]
    pdf.build(story)
    return buf.getvalue()


def filename(kind: str, end: date | None = None, fy: int | None = None) -> str:
    suffix = f"FY{fy_label(fy)}" if fy is not None else (end.isoformat() if end else "")
    return f"{kind}_{suffix}".strip("_")
