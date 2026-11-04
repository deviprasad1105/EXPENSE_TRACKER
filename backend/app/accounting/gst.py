"""GST invoices: a Sales or Purchase voucher built from taxable values and GST rates.

- Place of supply in the business's own state: CGST + SGST, each half the rate (intra-state supply).
- Any other state: IGST at the full rate (inter-state supply).
Each tax is computed on each line and rounded to the paisa (half up), so CGST and SGST are always equal.
Sales post output tax (a liability); purchases post input tax (credit the business can claim).
Return filing (GSTR-1 / 3B) is out of scope.
"""

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .chart import CASH_BANK_GROUPS, EXPENSE_GROUPS, INCOME_GROUPS, STATES, fy_of
from .engine import PURCHASE, SALES, Line, post_voucher, system_ledger
from .models import AccLedger, AccSettings, Voucher

GST_RATES = (Decimal("0"), Decimal("0.25"), Decimal("3"), Decimal("5"), Decimal("12"), Decimal("18"), Decimal("28"))
PAISA = Decimal("0.01")


class InvoiceItemIn(BaseModel):
    ledger_id: int
    taxable_value: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    gst_rate: Decimal = Field(ge=0, le=28, max_digits=5, decimal_places=2)


class InvoiceIn(BaseModel):
    kind: str = Field(pattern="^(SALES|PURCHASE)$")
    invoice_date: date
    invoice_no: str = Field(min_length=1, max_length=40)
    party_ledger_id: int
    place_of_supply: str = Field(max_length=40)
    narration: str | None = Field(default=None, max_length=500)
    items: list[InvoiceItemIn] = Field(min_length=1, max_length=100)


def _half_up(d: Decimal) -> Decimal:
    return d.quantize(PAISA, rounding=ROUND_HALF_UP)


def compute(items: list[InvoiceItemIn], intra_state: bool) -> dict:
    """Tax per component and invoice total, without touching the database (used for the live preview too)."""
    taxes: dict[str, Decimal] = defaultdict(Decimal)
    taxable = Decimal(0)
    for item in items:
        if item.gst_rate not in GST_RATES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"GST rate {item.gst_rate}% isn't a valid rate")
        taxable += item.taxable_value
        if intra_state:
            half = _half_up(item.taxable_value * item.gst_rate / 200)
            taxes["CGST"] += half
            taxes["SGST"] += half
        else:
            taxes["IGST"] += _half_up(item.taxable_value * item.gst_rate / 100)
    total_tax = sum(taxes.values(), Decimal(0))
    return {"taxable": taxable, "taxes": dict(taxes), "total_tax": total_tax, "total": taxable + total_tax}


def post_invoice(db: Session, books: AccSettings, data: InvoiceIn, created_by: int) -> Voucher:
    if not books.state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Set your business's state in Accounting settings first")
    if data.place_of_supply not in STATES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose the place of supply from the list")
    party = db.get(AccLedger, data.party_ledger_id)
    if party is None or party.user_id != books.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Party ledger not found")
    if party.group_code not in {"SUNDRY_DEBTORS", "SUNDRY_CREDITORS"} | CASH_BANK_GROUPS and party.account_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The party must be a debtor, a creditor, or a cash / bank ledger")
    allowed = INCOME_GROUPS if data.kind == SALES else EXPENSE_GROUPS | {"FIXED_ASSETS"}
    for item in data.items:
        g = db.get(AccLedger, item.ledger_id)
        if g is None or g.user_id != books.user_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ledger not found")
        if g.group_code not in allowed:
            what = "an income (sales) ledger" if data.kind == SALES else "a purchase, expense or fixed asset ledger"
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{g.name}' can't be on this invoice: choose {what}")
    duplicate = db.scalar(select(Voucher.id).where(
        Voucher.user_id == books.user_id, Voucher.voucher_type == data.kind, Voucher.fy == fy_of(data.invoice_date),
        func.lower(Voucher.invoice_no) == data.invoice_no.strip().lower(), Voucher.status == "POSTED",
    ))
    if duplicate and data.kind == SALES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Sales invoice {data.invoice_no} already exists this financial year")

    intra = data.place_of_supply == books.state
    result = compute(data.items, intra)
    prefix = "OUT_" if data.kind == SALES else "IN_"
    sales = data.kind == SALES
    lines: list[Line] = []
    for item in data.items:
        amt = item.taxable_value
        lines.append(Line(item.ledger_id, credit=amt, gst_rate=item.gst_rate) if sales else
                     Line(item.ledger_id, debit=amt, gst_rate=item.gst_rate))
    for comp, amount in result["taxes"].items():
        if amount:
            ledger = system_ledger(db, books.user_id, prefix + comp)
            lines.append(Line(ledger.id, credit=amount) if sales else Line(ledger.id, debit=amount))
    total = result["total"]
    lines.insert(0, Line(party.id, debit=total) if sales else Line(party.id, credit=total))
    narration = data.narration or f"{'Sales' if sales else 'Purchase'} invoice {data.invoice_no} – {party.name}"
    return post_voucher(
        db, books, voucher_type=data.kind, voucher_date=data.invoice_date, lines=lines, narration=narration,
        created_by=created_by, party_ledger_id=party.id, invoice_no=data.invoice_no.strip(),
        place_of_supply=data.place_of_supply, manual=True,
    )
