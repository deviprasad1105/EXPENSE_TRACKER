from io import BytesIO

from openpyxl import Workbook
from openpyxl.chart import BarChart, PieChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..models import Expense, User
from ..timeutil import now_local

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MONEY = "#,##0.00"


def _header(ws, row: int, values: list[str]):
    for col, value in enumerate(values, start=1):
        cell = ws.cell(row=row, column=col, value=value)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
        cell.border = BORDER


def _autosize(ws):
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max(width + 2, 10), 50)


def build_excel(user: User, expenses: list[Expense], summary: dict, bal: dict) -> bytes:
    wb = Workbook()

    # --- Summary sheet ---
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Expense Report"
    ws["A1"].font = Font(bold=True, size=16)
    info = [
        ("User", f"{user.full_name} ({user.username})"),
        ("Period", f"{summary['start']} to {summary['end']}"),
        ("Generated", now_local().strftime("%Y-%m-%d %H:%M")),
        ("Total spent in period", summary["total"]),
        ("Number of entries", summary["count"]),
        ("Average per day", summary["average_per_day"]),
        ("Days with no entry", len(summary["missed_days"])),
        ("Total funds (all time)", bal["total_funds"]),
        ("Total expenses (all time)", bal["total_expenses"]),
        ("Available balance", bal["balance"]),
    ]
    for i, (label, value) in enumerate(info, start=3):
        ws.cell(row=i, column=1, value=label).font = Font(bold=True)
        cell = ws.cell(row=i, column=2, value=value)
        if isinstance(value, float):
            cell.number_format = MONEY

    row = len(info) + 5
    ws.cell(row=row - 1, column=1, value="By category").font = Font(bold=True, size=12)
    _header(ws, row, ["Category", "Amount", "% of total"])
    cat_start = row + 1
    for r, c in enumerate(summary["by_category"], start=cat_start):
        ws.cell(row=r, column=1, value=c["category"]).border = BORDER
        ws.cell(row=r, column=2, value=c["amount"]).number_format = MONEY
        pct = ws.cell(row=r, column=3, value=(c["amount"] / summary["total"]) if summary["total"] else 0)
        pct.number_format = "0.0%"
    cat_end = cat_start + len(summary["by_category"]) - 1

    if summary["by_category"]:
        pie = PieChart()
        pie.title = "Spending by category"
        pie.add_data(Reference(ws, min_col=2, min_row=cat_start - 1, max_row=cat_end), titles_from_data=True)
        pie.set_categories(Reference(ws, min_col=1, min_row=cat_start, max_row=cat_end))
        pie.height, pie.width = 8, 12
        ws.add_chart(pie, "E3")

    row = cat_end + 3
    ws.cell(row=row - 1, column=1, value="By item").font = Font(bold=True, size=12)
    _header(ws, row, ["Item", "Times", "Quantity", "Amount"])
    for r, it in enumerate(summary["by_item"], start=row + 1):
        ws.cell(row=r, column=1, value=it["item"])
        ws.cell(row=r, column=2, value=it["count"])
        ws.cell(row=r, column=3, value=it["quantity"])
        ws.cell(row=r, column=4, value=it["amount"]).number_format = MONEY
    _autosize(ws)

    # --- Totals per period (daily / weekly / monthly / quarterly / yearly) ---
    periods = summary["trend"]
    granularity = summary.get("granularity", "daily").capitalize()
    wd = wb.create_sheet(f"{granularity} Totals")
    _header(wd, 1, ["Period", "Starts", "Expenses", "Funds added"])
    for r, p in enumerate(periods, start=2):
        wd.cell(row=r, column=1, value=p["label"])
        wd.cell(row=r, column=2, value=p["period"])
        wd.cell(row=r, column=3, value=p["expenses"]).number_format = MONEY
        wd.cell(row=r, column=4, value=p["funds"]).number_format = MONEY
    if periods:
        bar = BarChart()
        bar.title = f"{granularity} spending"
        bar.add_data(Reference(wd, min_col=3, min_row=1, max_row=len(periods) + 1), titles_from_data=True)
        bar.set_categories(Reference(wd, min_col=1, min_row=2, max_row=len(periods) + 1))
        bar.height, bar.width = 9, 22
        wd.add_chart(bar, "F2")
    _autosize(wd)

    # --- Detailed list sheet ---
    wl = wb.create_sheet("Detailed List")
    _header(wl, 1, ["Date", "Item", "Category", "Qty", "Amount", "Extra item", "Note", "Created at"])
    for r, e in enumerate(expenses, start=2):
        values = [
            e.expense_date.isoformat(),
            e.item_name,
            e.category,
            e.quantity,
            float(e.amount),
            "Yes" if e.is_extra else "No",
            e.note or "",
            e.created_at.strftime("%Y-%m-%d %H:%M"),
        ]
        for c, v in enumerate(values, start=1):
            cell = wl.cell(row=r, column=c, value=v)
            cell.border = BORDER
        wl.cell(row=r, column=5).number_format = MONEY
    total_row = len(expenses) + 2
    wl.cell(row=total_row, column=4, value="TOTAL").font = Font(bold=True)
    tot = wl.cell(row=total_row, column=5, value=f"=SUM(E2:E{max(total_row - 1, 2)})")
    tot.font = Font(bold=True)
    tot.number_format = MONEY
    wl.freeze_panes = "A2"
    wl.auto_filter.ref = f"A1:H{max(total_row - 1, 1)}"
    _autosize(wl)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_pdf(user: User, expenses: list[Expense], summary: dict, bal: dict) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm)
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=8, leading=10)

    def table(data, widths, money_cols=()):
        t = Table(data, colWidths=widths, repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BFBFBF")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F6FA")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
        for c in money_cols:
            style.append(("ALIGN", (c, 1), (c, -1), "RIGHT"))
        t.setStyle(TableStyle(style))
        return t

    money = lambda v: f"{v:,.2f}"  # noqa: E731
    story = [
        Paragraph("Expense Report", styles["Title"]),
        Paragraph(
            f"<b>User:</b> {user.full_name} ({user.username}) &nbsp;&nbsp; <b>Period:</b> {summary['start']} to {summary['end']}"
            f" &nbsp;&nbsp; <b>Generated:</b> {now_local().strftime('%Y-%m-%d %H:%M')}",
            styles["Normal"],
        ),
        Spacer(1, 8),
        Paragraph("Summary", styles["Heading2"]),
        table(
            [
                ["Total spent", "Entries", "Avg / day", "Days missed", "Total funds", "Available balance"],
                [
                    money(summary["total"]),
                    str(summary["count"]),
                    money(summary["average_per_day"]),
                    str(len(summary["missed_days"])),
                    money(bal["total_funds"]),
                    money(bal["balance"]),
                ],
            ],
            [30 * mm] * 6,
        ),
        Spacer(1, 8),
    ]

    granularity = summary.get("granularity", "daily")
    if summary.get("trend") and granularity != "daily":
        story += [
            Paragraph(f"{granularity.capitalize()} totals", styles["Heading2"]),
            table(
                [["Period", "Starts", "Expenses", "Funds added"]]
                + [[p["label"], p["period"], money(p["expenses"]), money(p["funds"])] for p in summary["trend"]],
                [45 * mm, 35 * mm, 40 * mm, 40 * mm],
                money_cols=(2, 3),
            ),
            Spacer(1, 8),
        ]

    if summary["by_category"]:
        story += [
            Paragraph("By category", styles["Heading2"]),
            table(
                [["Category", "Amount", "% of total"]]
                + [
                    [c["category"], money(c["amount"]), f"{(c['amount'] / summary['total'] * 100) if summary['total'] else 0:.1f}%"]
                    for c in summary["by_category"]
                ],
                [70 * mm, 40 * mm, 30 * mm],
                money_cols=(1, 2),
            ),
            Spacer(1, 8),
        ]
    if summary["by_item"]:
        story += [
            Paragraph("By item", styles["Heading2"]),
            table(
                [["Item", "Times", "Quantity", "Amount"]]
                + [[i["item"], str(i["count"]), str(i["quantity"]), money(i["amount"])] for i in summary["by_item"]],
                [70 * mm, 25 * mm, 25 * mm, 40 * mm],
                money_cols=(3,),
            ),
            Spacer(1, 8),
        ]

    story.append(Paragraph("Detailed list", styles["Heading2"]))
    rows = [["Date", "Item", "Category", "Qty", "Amount", "Note"]]
    for e in expenses:
        name = e.item_name + (" (extra)" if e.is_extra else "")
        rows.append([e.expense_date.isoformat(), Paragraph(name, small), e.category, str(e.quantity), money(float(e.amount)), Paragraph(e.note or "", small)])
    rows.append(["", "", "", "TOTAL", money(summary["total"]), ""])
    detail = table(rows, [22 * mm, 45 * mm, 28 * mm, 12 * mm, 25 * mm, 48 * mm], money_cols=(4,))
    detail.setStyle(TableStyle([("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    story.append(detail)

    doc.build(story)
    return buf.getvalue()
