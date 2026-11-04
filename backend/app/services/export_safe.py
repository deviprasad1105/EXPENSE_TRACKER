"""Make user-entered text safe to put into Excel and PDF exports.

- Excel: openpyxl stores any string starting with "=" as a formula, so an item named "=HYPERLINK(...)" would
  run for whoever opens the file. `defuse_formulas` turns every formula cell back into plain text, except the
  plain `=SUM(A1:A9)` totals the exports write themselves.
- PDF: reportlab's Paragraph parses a small HTML-like markup, so "<" or "&" in a note breaks the export (or
  injects markup). `pdf_text` escapes it.
"""

import re
from xml.sax.saxutils import escape

_OWN_FORMULA = re.compile(r"^=SUM\([A-Z]{1,3}\d+:[A-Z]{1,3}\d+\)$")


def defuse_formulas(workbook) -> None:
    for ws in workbook.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == "f" and not _OWN_FORMULA.match(str(cell.value)):
                    cell.data_type = "s"


def pdf_text(value) -> str:
    return escape("" if value is None else str(value))
