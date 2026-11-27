"""One look for every PDF and Excel export: the Rubik font, the colours, money formats and the table layout.

PDF tables span the full width of the page, every cell is wrapped text (long UPI descriptions wrap instead of
running into the next column) and each header cell is aligned like its column (amounts right, text left).
Rubik (app/fonts, SIL Open Font License) is embedded in the PDFs; Excel files name it and fall back to the
viewer's default font where Rubik isn't installed.
"""

from decimal import Decimal
from pathlib import Path

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Table, TableStyle

from ..timeutil import now_local
from .export_safe import pdf_text

# ---------- colours (the app's indigo) ----------
INK = "1E1B4B"
HEAD = "312E81"
MUTED = "6B7280"
GRID = "E2E4EE"
ZEBRA = "F6F7FB"
TOTAL = "EBEDF9"


def _c(hex_):
    return colors.HexColor("#" + hex_)


# ---------- fonts ----------
FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
REGULAR, MEDIUM, BOLD = "Rubik", "Rubik-Medium", "Rubik-Bold"
XL_FONT = "Rubik"


def _register() -> tuple[str, str, str]:
    try:
        pdfmetrics.getFont(REGULAR)
        return REGULAR, MEDIUM, BOLD
    except KeyError:
        pass
    try:
        for name, file in ((REGULAR, "Rubik-Regular.ttf"), (MEDIUM, "Rubik-Medium.ttf"), (BOLD, "Rubik-Bold.ttf")):
            pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file)))
        pdfmetrics.registerFontFamily(REGULAR, normal=REGULAR, bold=BOLD, italic=REGULAR, boldItalic=BOLD)
        return REGULAR, MEDIUM, BOLD
    except Exception:  # fonts missing from the deployment: still produce the report
        return "Helvetica", "Helvetica-Bold", "Helvetica-Bold"


F_REG, F_MED, F_BOLD = _register()


# ---------- money ----------
# Indian grouping in Excel: crores and lakhs need their own sections
XL_INR = r"[>=10000000]##\,##\,##\,##0.00;[>=100000]##\,##\,##0.00;##,##0.00"


def inr(value) -> str:
    """12345678.5 → '1,23,45,678.50'."""
    d = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    sign, whole, frac = ("-" if d < 0 else ""), *f"{abs(d):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return sign + ",".join(parts + [tail]) + "." + frac


def rupees(value) -> str:
    v = inr(value)
    return ("-₹" + v[1:]) if v.startswith("-") else "₹" + v


# ---------- PDF ----------
def _style(name, size=8.5, font=None, color=INK, align=TA_LEFT, leading=None, **kw):
    return ParagraphStyle(name, fontName=font or F_REG, fontSize=size, leading=leading or size * 1.3,
                          textColor=_c(color), alignment=align, **kw)


TITLE = _style("title", 18, F_BOLD, leading=22, spaceAfter=2)
SUBTITLE = _style("subtitle", 9.5, color=MUTED, leading=13)
H2 = _style("h2", 11.5, F_MED, leading=15, spaceBefore=12, spaceAfter=5)
NOTE = _style("note", 8, color=MUTED, leading=10.5, spaceBefore=4)
_ALIGN = {"L": TA_LEFT, "R": TA_RIGHT, "C": TA_CENTER}
_CELL = {(a, b): _style(f"cell{a}{b}", 8.5, F_BOLD if b else F_REG, align=_ALIGN[a]) for a in "LRC" for b in (0, 1)}
_HEAD = {a: _style(f"head{a}", 8.5, F_MED, color="FFFFFF", align=_ALIGN[a]) for a in "LRC"}


def para(text, style=SUBTITLE) -> Paragraph:
    return Paragraph(pdf_text(text), style)


def grid(columns: list[tuple[str, str, float]], rows: list[list], width: float, *, bold=(), shade=(), indent=None,
         zebra=True, divide_after=()) -> Table:
    """A full-width table. columns: (label, 'L'|'R'|'C', relative width). divide_after: column indexes with a rule after. Cells are strings (escaped here),
    None (blank) or ready-made flowables. bold/shade: row indexes (0 = first body row). indent: {row: levels}."""
    total = sum(c[2] for c in columns)
    widths = [c[2] * width / total for c in columns]
    bold, shade, indent = set(bold), set(shade), indent or {}
    data = [[Paragraph(pdf_text(label), _HEAD[align]) for label, align, _ in columns]]
    for r, row in enumerate(rows):
        out = []
        for c, value in enumerate(row):
            if isinstance(value, Flowable):
                out.append(value)
                continue
            raw = "" if value is None else str(value)
            text = raw.lstrip(" ")
            style = _CELL[(columns[c][1], int(r in bold))]
            # Indent levels: the row's own, plus 4 leading spaces per level (T-accounts, "To ..." lines).
            # A real paragraph indent, so wrapped lines stay indented too.
            level = (indent.get(r, 0) if columns[c][1] == "L" and text else 0) + (len(raw) - len(text)) // 4
            if level:
                style = ParagraphStyle(f"{style.name}_i{level}", parent=style, leftIndent=9 * level)
            out.append(Paragraph(pdf_text(text), style))
        data.append(out)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _c(HEAD)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, _c(GRID)),
        ("BOX", (0, 0), (-1, -1), 0.4, _c(GRID)),
    ]
    for c in divide_after:  # e.g. between the two sides of a T-account
        style.append(("LINEAFTER", (c, 0), (c, -1), 0.8, _c(HEAD)))
    if zebra:
        style.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _c(ZEBRA)]))
    for r in shade:
        style += [("BACKGROUND", (0, r + 1), (-1, r + 1), _c(TOTAL)), ("LINEABOVE", (0, r + 1), (-1, r + 1), 0.8, _c(HEAD))]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle(style))
    return t


def tiles(items: list[tuple[str, str]], width: float, per_row: int = 3) -> Table:
    """Summary figures as boxes: label above, value below."""
    label = _style("tile_label", 8, color=MUTED)
    value = _style("tile_value", 13, F_MED, leading=17)
    cells = [[Paragraph(pdf_text(k), label), Paragraph(pdf_text(v), value)] for k, v in items]
    while len(cells) % per_row:
        cells.append("")
    data = [cells[i:i + per_row] for i in range(0, len(cells), per_row)]
    t = Table(data, colWidths=[width / per_row] * per_row, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.4, _c(GRID)),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, _c(GRID)),
        ("BACKGROUND", (0, 0), (-1, -1), _c(ZEBRA)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
    ]))
    return t


def _numbered_canvas(footer: str):
    class NumberedCanvas(rl_canvas.Canvas):
        """Draws 'footer · Page x of y' on every page once the page count is known."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            for state in self._pages:
                self.__dict__.update(state)
                w, _ = self._pagesize
                self.setFont(F_REG, 7.5)
                self.setFillColor(_c(MUTED))
                self.setStrokeColor(_c(GRID))
                self.line(14 * mm, 10 * mm, w - 14 * mm, 10 * mm)
                self.drawString(14 * mm, 6.5 * mm, footer)
                self.drawRightString(w - 14 * mm, 6.5 * mm, f"Page {self._pageNumber} of {len(self._pages)}")
                super().showPage()
            super().save()

    return NumberedCanvas


def build_pdf(buf, story: list, pagesize, title: str) -> None:
    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm,
                            bottomMargin=16 * mm, title=title, author="Expense Tracker")
    footer = f"Expense Tracker · {title} · generated {now_local():%d %b %Y, %H:%M}"
    doc.build(story, canvasmaker=_numbered_canvas(footer))


def content_width(pagesize) -> float:
    return pagesize[0] - 28 * mm


# ---------- Excel ----------
XL_THIN = Side(style="thin", color="D5D8E5")
XL_BORDER = Border(left=XL_THIN, right=XL_THIN, top=XL_THIN, bottom=XL_THIN)
XL_HEAD_FILL = PatternFill("solid", fgColor=HEAD)
XL_TOTAL_FILL = PatternFill("solid", fgColor=TOTAL)


def xl_header(ws, row: int, labels: list[str], aligns: str, col: int = 1) -> None:
    """Header cells aligned like their columns: aligns is one letter per column, L / R / C."""
    for i, (label, a) in enumerate(zip(labels, aligns)):
        cell = ws.cell(row=row, column=col + i, value=label)
        cell.fill = XL_HEAD_FILL
        cell.font = Font(name=XL_FONT, bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal={"L": "left", "R": "right", "C": "center"}[a], vertical="center")
        cell.border = XL_BORDER


def xl_title(ws, title: str, subtitle: str) -> None:
    ws["A1"] = title
    ws["A1"].font = Font(name=XL_FONT, bold=True, size=15, color=INK)
    ws["A2"] = subtitle
    ws["A2"].font = Font(name=XL_FONT, color=MUTED)


def xl_use_rubik(wb) -> None:
    """Rubik on every cell, keeping each cell's weight, size and colour."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                f = cell.font
                if f.name != XL_FONT:
                    cell.font = Font(name=XL_FONT, size=f.size, bold=f.bold, italic=f.italic, color=f.color,
                                     underline=f.underline)
