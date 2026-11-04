"""Ledger balances, straight from posted voucher lines. Every report builds on these.

Sign convention: debit balances are positive, credit balances negative.
- Balance Sheet ledgers carry their balance forward forever: opening balance + every line up to the date.
- Trading and P&L ledgers (income and expenses) start every financial year at zero; what they added up to in
  earlier years is that year's profit, which belongs to the proprietor's capital (see `retained_profit`).
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .chart import BS, GROUPS, fy_of, fy_start
from .models import AccLedger, StockValue, Voucher, VoucherLine

ZERO = Decimal(0)


def ledgers_of(db: Session, user_id: int) -> list[AccLedger]:
    return list(db.scalars(select(AccLedger).where(AccLedger.user_id == user_id).order_by(AccLedger.name)))


def is_nominal(ledger: AccLedger) -> bool:
    return GROUPS[ledger.group_code].section != BS


def movements(db: Session, user_id: int, start: date | None, end: date) -> dict[int, tuple[Decimal, Decimal]]:
    """Total debits and credits per ledger for vouchers dated start..end (start None = from the beginning)."""
    stmt = (
        select(VoucherLine.ledger_id, func.sum(VoucherLine.debit), func.sum(VoucherLine.credit))
        .join(Voucher, Voucher.id == VoucherLine.voucher_id)
        .where(Voucher.user_id == user_id, Voucher.voucher_date <= end)
        .group_by(VoucherLine.ledger_id)
    )
    if start is not None:
        stmt = stmt.where(Voucher.voucher_date >= start)
    return {lid: (Decimal(dr or 0), Decimal(cr or 0)) for lid, dr, cr in db.execute(stmt)}


def balances_as_of(db: Session, user_id: int, as_of: date, ledgers: list[AccLedger] | None = None) -> dict[int, Decimal]:
    """Closing balance of every ledger at the end of `as_of`."""
    ledgers = ledgers if ledgers is not None else ledgers_of(db, user_id)
    to_date = movements(db, user_id, None, as_of)
    this_year = movements(db, user_id, fy_start(fy_of(as_of)), as_of)
    out = {}
    for g in ledgers:
        if is_nominal(g):
            dr, cr = this_year.get(g.id, (ZERO, ZERO))
            out[g.id] = dr - cr
        else:
            dr, cr = to_date.get(g.id, (ZERO, ZERO))
            out[g.id] = Decimal(g.opening_balance) + dr - cr
    return out


def opening_balances(db: Session, user_id: int, start: date, ledgers: list[AccLedger] | None = None) -> dict[int, Decimal]:
    """Balance of every ledger just before `start` (nominal ledgers: only since the start of that year)."""
    ledgers = ledgers if ledgers is not None else ledgers_of(db, user_id)
    if start == fy_start(fy_of(start)):
        before = movements(db, user_id, None, start - timedelta(days=1))
        return {g.id: ZERO if is_nominal(g) else Decimal(g.opening_balance) + before.get(g.id, (ZERO, ZERO))[0]
                - before.get(g.id, (ZERO, ZERO))[1] for g in ledgers}
    return balances_as_of(db, user_id, start - timedelta(days=1), ledgers)


# ---------- stock and profit carried forward ----------
def stock_for(db: Session, user_id: int, fy: int, first_fy: int) -> tuple[Decimal, Decimal | None]:
    """(opening, closing) stock of a year. Opening = last year's closing (entered by hand only for the first year).
    Closing is None until it has been entered."""
    rows = {s.fy: s for s in db.scalars(select(StockValue).where(StockValue.user_id == user_id, StockValue.fy <= fy))}
    this = rows.get(fy)
    if fy <= first_fy:
        opening = Decimal(this.opening_value) if this and this.opening_value is not None else ZERO
    else:
        prev = rows.get(fy - 1)
        opening = Decimal(prev.closing_value) if prev and prev.closing_value is not None else ZERO
    closing = Decimal(this.closing_value) if this and this.closing_value is not None else None
    return opening, closing


def first_opening_stock(db: Session, user_id: int, first_fy: int) -> Decimal:
    return stock_for(db, user_id, first_fy, first_fy)[0]


def year_profit(db: Session, user_id: int, fy: int, end: date, first_fy: int, ledgers: list[AccLedger]) -> Decimal:
    """Net profit of a financial year up to `end`: income − expenses + closing stock − opening stock."""
    moves = movements(db, user_id, fy_start(fy), end)
    nominal = {g.id for g in ledgers if is_nominal(g)}
    dr_minus_cr = sum((dr - cr for lid, (dr, cr) in moves.items() if lid in nominal), ZERO)
    opening, closing = stock_for(db, user_id, fy, first_fy)
    return -dr_minus_cr + (closing or ZERO) - opening


def retained_profit(db: Session, user_id: int, fy: int, first_fy: int, ledgers: list[AccLedger]) -> Decimal:
    """Profit (or loss) of every earlier financial year, carried into the proprietor's capital."""
    total = ZERO
    for y in range(first_fy, fy):
        total += year_profit(db, user_id, y, date(y + 1, 3, 31), first_fy, ledgers)
    return total


def opening_difference(db: Session, user_id: int, first_fy: int, ledgers: list[AccLedger]) -> Decimal:
    """Opening balances that don't balance (Tally's "Difference in opening balances"). Debit-positive.

    The first year's opening stock counts as an opening debit balance: it is an asset the business started with.
    """
    return sum((Decimal(g.opening_balance) for g in ledgers), ZERO) + first_opening_stock(db, user_id, first_fy)


def split_dr_cr(amount: Decimal) -> tuple[Decimal, Decimal]:
    return (amount, ZERO) if amount >= 0 else (ZERO, -amount)


def ledger_usage(db: Session, user_id: int) -> set[int]:
    stmt = select(VoucherLine.ledger_id).join(Voucher).where(Voucher.user_id == user_id).distinct()
    return set(db.scalars(stmt))


def group_totals(per_ledger: dict[int, Decimal], ledgers: list[AccLedger]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for g in ledgers:
        totals[g.group_code] += per_ledger.get(g.id, ZERO)
    return totals
