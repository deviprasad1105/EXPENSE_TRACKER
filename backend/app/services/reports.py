from datetime import date
from io import BytesIO

from openpyxl import Workbook
from openpyxl.chart import BarChart, PieChart, Reference
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from reportlab.lib.pagesizes import A4
from reportlab.platypus import Paragraph, Spacer

from ..models import Expense, User
from ..timeutil import now_local
from .export_safe import defuse_formulas, pdf_text
from .report_style import (
    H2, NOTE, SUBTITLE, TITLE, XL_BORDER, XL_FONT, XL_INR, XL_TOTAL_FILL, build_pdf as _build, content_width, grid,
    inr, rupees, tiles, xl_header, xl_title, xl_use_rubik,
)

PDF_TOP_ITEMS = 25  # the PDF lists the biggest items; every entry is still in the detailed list


def _when(d) -> str:
    """'2026-04-23' or a date → '23 Apr 2026'."""
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return d.strftime("%d %b %Y")


def _dmy(d) -> str:
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return d.strftime("%d-%m-%Y")


def _period(summary) -> str:
    return f"{_when(summary['start'])} to {_when(summary['end'])}"


# ---------- Excel ----------
def _table(ws, top: int, labels: list[str], aligns: str, rows: list[list], formats: dict | None = None) -> int:
    """Header + bordered body. formats: {column index: number format}. Returns the last row written."""
    xl_header(ws, top, labels, aligns)
    formats = formats or {}
    for r, values in enumerate(rows, start=top + 1):
        for c, v in enumerate(values):
            cell = ws.cell(row=r, column=c + 1, value=v)
            cell.border = XL_BORDER
            cell.alignment = Alignment(horizontal={"L": "left", "R": "right", "C": "center"}[aligns[c]], vertical="top")
            if c in formats:
                cell.number_format = formats[c]
    return top + len(rows)


def _widths(ws, widths: list[float]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def build_excel(user: User, expenses: list[Expense], summary: dict, bal: dict) -> bytes:
    wb = Workbook()
    who = f"{user.full_name} ({user.username})"

    # --- Summary sheet ---
    ws = wb.active
    ws.title = "Summary"
    xl_title(ws, "Expense Report", f"{who} · {_period(summary)} · generated {now_local():%d %b %Y, %H:%M}")
    info = [
        ("Total spent in period", summary["total"], XL_INR),
        ("Number of entries", summary["count"], "0"),
        ("Average per day", summary["average_per_day"], XL_INR),
        ("Days with no entry", len(summary["missed_days"]), "0"),
        ("Total funds (all time)", bal["total_funds"], XL_INR),
        ("Total expenses (all time)", bal["total_expenses"], XL_INR),
        ("Available balance", bal["balance"], XL_INR),
    ]
    xl_header(ws, 4, ["Figure", "Value"], "LR")
    for i, (label, value, fmt) in enumerate(info, start=5):
        a = ws.cell(row=i, column=1, value=label)
        b = ws.cell(row=i, column=2, value=value)
        a.border = b.border = XL_BORDER
        a.font = Font(name=XL_FONT, bold=True)
        b.number_format = fmt
    row = 5 + len(info) + 2

    ws.cell(row=row, column=1, value="By category").font = Font(name=XL_FONT, bold=True, size=12)
    total = summary["total"] or 0
    cats = [[c["category"], c["amount"], (c["amount"] / total) if total else 0] for c in summary["by_category"]]
    cat_end = _table(ws, row + 1, ["Category", "Amount (₹)", "% of total"], "LRR", cats, {1: XL_INR, 2: "0.0%"})
    if cats:
        pie = PieChart()
        pie.title = "Spending by category"
        pie.add_data(Reference(ws, min_col=2, min_row=row + 1, max_row=cat_end), titles_from_data=True)
        pie.set_categories(Reference(ws, min_col=1, min_row=row + 2, max_row=cat_end))
        pie.height, pie.width = 8, 12
        ws.add_chart(pie, "E4")
    row = cat_end + 2

    ws.cell(row=row, column=1, value="By item").font = Font(name=XL_FONT, bold=True, size=12)
    items = [[i["item"], i["count"], i["quantity"], i["amount"]] for i in summary["by_item"]]
    _table(ws, row + 1, ["Item", "Times", "Quantity", "Amount (₹)"], "LRRR", items, {3: XL_INR})
    _widths(ws, [48, 16, 12, 16])
    ws.freeze_panes = "A5"

    # --- Totals per period (daily / weekly / monthly / quarterly / yearly) ---
    periods = summary["trend"]
    granularity = summary.get("granularity", "daily").capitalize()
    wd = wb.create_sheet(f"{granularity} Totals")
    last = _table(wd, 1, ["Period", "Starts", "Expenses (₹)", "Funds added (₹)"], "LLRR",
                  [[p["label"], p["period"], p["expenses"], p["funds"]] for p in periods], {2: XL_INR, 3: XL_INR})
    if periods:
        bar = BarChart()
        bar.title = f"{granularity} spending"
        bar.add_data(Reference(wd, min_col=3, min_row=1, max_row=last), titles_from_data=True)
        bar.set_categories(Reference(wd, min_col=1, min_row=2, max_row=last))
        bar.height, bar.width = 9, 22
        wd.add_chart(bar, "F2")
    _widths(wd, [18, 14, 16, 16])
    wd.freeze_panes = "A2"

    # --- Detailed list sheet ---
    wl = wb.create_sheet("Detailed List")
    rows = [[e.expense_date, e.item_name, e.category, e.quantity, float(e.amount), "Yes" if e.is_extra else "No",
             e.note or "", e.created_at.strftime("%d-%m-%Y %H:%M")] for e in expenses]
    _table(wl, 1, ["Date", "Item", "Category", "Qty", "Amount (₹)", "Extra item", "Note", "Created at"], "LLLRRCLL",
           rows, {0: "dd-mm-yyyy", 4: XL_INR})
    total_row = len(expenses) + 2
    label = wl.cell(row=total_row, column=4, value="TOTAL")
    tot = wl.cell(row=total_row, column=5, value=f"=SUM(E2:E{max(total_row - 1, 2)})")
    for c in range(1, 9):
        wl.cell(row=total_row, column=c).fill = XL_TOTAL_FILL
        wl.cell(row=total_row, column=c).border = XL_BORDER
    label.font = tot.font = Font(name=XL_FONT, bold=True)
    label.alignment = tot.alignment = Alignment(horizontal="right")
    tot.number_format = XL_INR
    wl.freeze_panes = "A2"
    wl.auto_filter.ref = f"A1:H{max(total_row - 1, 1)}"
    _widths(wl, [12, 48, 16, 7, 14, 10, 32, 17])

    xl_use_rubik(wb)
    buf = BytesIO()
    defuse_formulas(wb)
    wb.save(buf)
    return buf.getvalue()


# ---------- PDF ----------
def build_pdf(user: User, expenses: list[Expense], summary: dict, bal: dict) -> bytes:
    buf = BytesIO()
    width = content_width(A4)
    story = [
        Paragraph("Expense Report", TITLE),
        Paragraph(f"{pdf_text(user.full_name)} ({pdf_text(user.username)}) · {_period(summary)}", SUBTITLE),
        Spacer(1, 10),
        tiles([
            ("Total spent", rupees(summary["total"])),
            ("Entries", str(summary["count"])),
            ("Average per day", rupees(summary["average_per_day"])),
            ("Days with no entry", str(len(summary["missed_days"]))),
            ("Total funds (all time)", rupees(bal["total_funds"])),
            ("Available balance", rupees(bal["balance"])),
        ], width),
    ]

    granularity = summary.get("granularity", "daily")
    if summary.get("trend") and granularity != "daily":
        trend = summary["trend"]
        story += [
            Paragraph(f"{granularity.capitalize()} totals", H2),
            grid([("Period", "L", 3), ("Starts", "L", 2), ("Expenses (₹)", "R", 2.5), ("Funds added (₹)", "R", 2.5)],
                 [[p["label"], _dmy(p["period"]), inr(p["expenses"]), inr(p["funds"])] for p in trend]
                 + [["Total", "", inr(sum(p["expenses"] for p in trend)), inr(sum(p["funds"] for p in trend))]],
                 width, bold=[len(trend)], shade=[len(trend)]),
        ]

    total = summary["total"] or 0
    if summary["by_category"]:
        cats = summary["by_category"]
        story += [
            Paragraph("By category", H2),
            grid([("Category", "L", 6), ("Amount (₹)", "R", 2.5), ("% of total", "R", 1.5)],
                 [[c["category"], inr(c["amount"]), f"{(c['amount'] / total * 100) if total else 0:.1f}%"] for c in cats]
                 + [["Total", inr(total), "100.0%" if total else "0.0%"]],
                 width, bold=[len(cats)], shade=[len(cats)]),
        ]

    if summary["by_item"]:
        items = sorted(summary["by_item"], key=lambda i: i["amount"], reverse=True)
        shown = items[:PDF_TOP_ITEMS]
        story += [
            Paragraph("Top items" if len(items) > len(shown) else "By item", H2),
            grid([("Item", "L", 6.2), ("Times", "R", 1), ("Quantity", "R", 1.2), ("Amount (₹)", "R", 1.8)],
                 [[i["item"], str(i["count"]), str(i["quantity"]), inr(i["amount"])] for i in shown], width),
        ]
        if len(items) > len(shown):
            story.append(Paragraph(
                f"The {len(shown)} biggest of {len(items)} items. Every entry is in the detailed list below and in the Excel workbook.",
                NOTE))

    story.append(Paragraph("Detailed list", H2))
    rows = [[e.expense_date.strftime("%d-%m-%Y"), e.item_name + (" (extra)" if e.is_extra else ""), e.category,
             str(e.quantity), inr(float(e.amount)), e.note or ""] for e in expenses]
    rows.append(["", "", "", "Total", inr(total), ""])
    story.append(grid([("Date", "L", 1.7), ("Item", "L", 5), ("Category", "L", 1.9), ("Qty", "R", 0.8),
                       ("Amount (₹)", "R", 1.8), ("Note", "L", 2.8)], rows, width,
                      bold=[len(rows) - 1], shade=[len(rows) - 1]))

    _build(buf, story, A4, "Expense Report")
    return buf.getvalue()
