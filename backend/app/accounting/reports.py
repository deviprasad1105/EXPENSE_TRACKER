"""The accounting reports, all computed from posted voucher lines (never from expenses or funds directly).

Signed amounts are debit-positive throughout; each report turns them into Dr/Cr columns or the natural side of
a T-account. Periods are within one financial year (April–March), like Tally.

Why the books always balance:
  every voucher balances, so Σ(all ledger balances) = Σ(opening balances).
  Trial Balance adds three lines Tally also shows: this year's opening stock (Dr), the profit of earlier years
  ("Profit & Loss A/c", Cr) and any "Difference in opening balances". With those, Dr == Cr exactly.
  Balance Sheet: Assets − Liabilities = the opening difference, so that line closes it too.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..timeutil import today_local
from .balances import (
    ZERO,
    balances_as_of,
    is_nominal,
    ledgers_of,
    movements,
    opening_balances,
    opening_difference,
    retained_profit,
    stock_for,
    stock_missing,
    year_profit,
)
from .chart import ASSET, BS, CASH, EXPENSE, FINANCING, GROUPS, INCOME, INVESTING, LIABILITY, OPERATING, PL, TRADING
from .chart import fy_end, fy_label, fy_of, fy_start, root_of
from .models import AccLedger, AccSettings, Voucher, VoucherLine


@dataclass
class Period:
    fy: int
    start: date
    end: date

    @property
    def label(self) -> str:
        return f"{self.start:%d %b %Y} to {self.end:%d %b %Y} (FY {fy_label(self.fy)})"


def period(books: AccSettings, fy: int | None = None, start: date | None = None, end: date | None = None) -> Period:
    """A reporting period inside one financial year. Defaults: the current year, from 1 April to today."""
    today = today_local()
    if fy is None:
        fy = fy_of(start or end or today)
    first = fy_of(books.books_start)
    if fy < first - 50 or fy > fy_of(today) + 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That financial year is out of range")
    start = start or fy_start(fy)
    end = end or min(fy_end(fy), max(today, start))
    if start > end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Start date must be before end date")
    if fy_of(start) != fy or fy_of(end) != fy:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"A report covers one financial year: pick dates between {fy_start(fy):%d %b %Y} and {fy_end(fy):%d %b %Y}",
        )
    return Period(fy, start, end)


def dr_cr(amount: Decimal) -> dict:
    return {"debit": amount if amount > 0 else ZERO, "credit": -amount if amount < 0 else ZERO}


def _first_fy(books: AccSettings) -> int:
    return fy_of(books.books_start)


# ---------- Journal (day book) ----------
def journal(db: Session, books: AccSettings, p: Period, voucher_type: str | None = None) -> dict:
    stmt = (
        select(Voucher)
        .where(Voucher.user_id == books.user_id, Voucher.voucher_date >= p.start, Voucher.voucher_date <= p.end)
        .options(selectinload(Voucher.lines))
        .order_by(Voucher.voucher_date, Voucher.voucher_type, Voucher.voucher_no)
    )
    if voucher_type:
        stmt = stmt.where(Voucher.voucher_type == voucher_type)
    names = {g.id: g.name for g in ledgers_of(db, books.user_id)}
    entries, total = [], ZERO
    for v in db.scalars(stmt):
        amount = sum((Decimal(l.debit) for l in v.lines), ZERO)
        total += amount
        entries.append({
            "id": v.id, "date": v.voucher_date, "voucher_type": v.voucher_type, "voucher_no": v.voucher_no,
            "narration": v.narration, "status": v.status, "is_reversal": v.is_reversal, "source_type": v.source_type,
            "amount": amount,
            "lines": [{"ledger_id": l.ledger_id, "ledger": names.get(l.ledger_id, "?"), "debit": l.debit, "credit": l.credit}
                      for l in v.lines],
        })
    return {"period": p.label, "start": p.start, "end": p.end, "entries": entries, "total_debit": total, "total_credit": total}


# ---------- General Ledger ----------
def ledger_report(db: Session, books: AccSettings, ledger: AccLedger, p: Period) -> dict:
    ledgers = ledgers_of(db, books.user_id)
    names = {g.id: g.name for g in ledgers}
    opening = opening_balances(db, books.user_id, p.start, [ledger])[ledger.id]
    rows = db.execute(
        select(Voucher, VoucherLine)
        .join(VoucherLine, VoucherLine.voucher_id == Voucher.id)
        .where(Voucher.user_id == books.user_id, VoucherLine.ledger_id == ledger.id,
               Voucher.voucher_date >= p.start, Voucher.voucher_date <= p.end)
        .options(selectinload(Voucher.lines))
        .order_by(Voucher.voucher_date, Voucher.id, VoucherLine.line_no)
    ).all()
    running, entries, total_dr, total_cr = opening, [], ZERO, ZERO
    for v, line in rows:
        # "Particulars": the other side of the entry (Tally shows the contra ledger, or "as per details")
        others = [names.get(l.ledger_id, "?") for l in v.lines if l.ledger_id != ledger.id
                  and (Decimal(l.debit) > 0) != (Decimal(line.debit) > 0)]
        dr, cr = Decimal(line.debit), Decimal(line.credit)
        running += dr - cr
        total_dr, total_cr = total_dr + dr, total_cr + cr
        entries.append({
            "voucher_id": v.id, "date": v.voucher_date, "voucher_type": v.voucher_type, "voucher_no": v.voucher_no,
            "particulars": ("To " if dr else "By ") + (others[0] if len(others) == 1 else "(as per details)"),
            "others": others, "narration": v.narration, "debit": dr, "credit": cr, "balance": abs(running),
            "balance_side": "Dr" if running >= 0 else "Cr", "is_reversal": v.is_reversal, "status": v.status,
        })
    return {
        "period": p.label, "start": p.start, "end": p.end,
        "ledger": {"id": ledger.id, "name": ledger.name, "group": GROUPS[ledger.group_code].name},
        "opening": abs(opening), "opening_side": "Dr" if opening >= 0 else "Cr",
        "entries": entries, "total_debit": total_dr, "total_credit": total_cr,
        "closing": abs(running), "closing_side": "Dr" if running >= 0 else "Cr",
    }


# ---------- Trial Balance ----------
def trial_balance(db: Session, books: AccSettings, p: Period) -> dict:
    uid, first = books.user_id, _first_fy(books)
    ledgers = ledgers_of(db, uid)
    opening = opening_balances(db, uid, p.start, ledgers)
    moves = movements(db, uid, p.start, p.end)
    closing = {g.id: opening[g.id] + moves.get(g.id, (ZERO, ZERO))[0] - moves.get(g.id, (ZERO, ZERO))[1] for g in ledgers}

    rows = []
    for g in ledgers:
        dr, cr = moves.get(g.id, (ZERO, ZERO))
        if not (opening[g.id] or dr or cr or closing[g.id]):
            continue
        rows.append({"ledger_id": g.id, "name": g.name, "group_code": g.group_code, "group": GROUPS[g.group_code].name,
                     "opening": opening[g.id], "debit": dr, "credit": cr, "closing": closing[g.id]})

    # Lines that make it balance (each the same at the start and the end of the period)
    stock_open, _ = stock_for(db, uid, p.fy, first)
    carried = -retained_profit(db, uid, p.fy, first, ledgers)  # debit-positive: a profit is a credit
    difference = -opening_difference(db, uid, first, ledgers)
    specials = [("Opening Stock", "STOCK", stock_open), ("Profit & Loss A/c (earlier years)", "PROFIT", carried),
                ("Difference in opening balances", "DIFFERENCE", difference)]
    for name, code, amount in specials:
        if amount:
            rows.append({"ledger_id": None, "name": name, "group_code": code, "group": name,
                         "opening": amount, "debit": ZERO, "credit": ZERO, "closing": amount})

    order = {c: g.order for c, g in GROUPS.items()}
    rows.sort(key=lambda r: (order.get(r["group_code"], 99), r["name"].casefold()))
    totals = {
        "opening_debit": sum((r["opening"] for r in rows if r["opening"] > 0), ZERO),
        "opening_credit": sum((-r["opening"] for r in rows if r["opening"] < 0), ZERO),
        "debit": sum((r["debit"] for r in rows), ZERO),
        "credit": sum((r["credit"] for r in rows), ZERO),
        "closing_debit": sum((r["closing"] for r in rows if r["closing"] > 0), ZERO),
        "closing_credit": sum((-r["closing"] for r in rows if r["closing"] < 0), ZERO),
    }
    groups = _group_rollup(rows)
    return {"period": p.label, "start": p.start, "end": p.end, "rows": rows, "groups": groups, "totals": totals,
            "balanced": totals["closing_debit"] == totals["closing_credit"] and totals["debit"] == totals["credit"]}


def _group_rollup(rows: list[dict]) -> list[dict]:
    """Rows summed into their primary group (Tally's grouped Trial Balance), with the ledgers underneath."""
    by_root: dict[str, dict] = {}
    for r in rows:
        if r["group_code"] in GROUPS:
            root = root_of(r["group_code"])
            key, name, order = root.code, root.name, root.order
        else:
            key, name, order = r["group_code"], r["name"], 99
        g = by_root.setdefault(key, {"code": key, "name": name, "order": order, "opening": ZERO, "debit": ZERO,
                                     "credit": ZERO, "closing": ZERO, "ledgers": []})
        for f in ("opening", "debit", "credit", "closing"):
            g[f] += r[f]
        g["ledgers"].append(r)
    return sorted(by_root.values(), key=lambda g: g["order"])


# ---------- Trading and Profit & Loss ----------
def _side_items(ledgers, bal, groups, sign) -> list[dict]:
    items = [{"ledger_id": g.id, "name": g.name, "group": GROUPS[g.group_code].name, "amount": sign * bal.get(g.id, ZERO)}
             for g in ledgers if g.group_code in groups and bal.get(g.id, ZERO)]
    return sorted(items, key=lambda i: (i["group"], i["name"].casefold()))


def trading_and_pl(db: Session, books: AccSettings, p: Period) -> dict:
    """Trading Account (gross profit) and Profit & Loss Account (net profit) for the year up to p.end.

    Drawings sit in the Capital Account group, so personal spending never reaches either account.
    """
    uid, first = books.user_id, _first_fy(books)
    ledgers = ledgers_of(db, uid)
    moves = movements(db, uid, fy_start(p.fy), p.end)
    bal = {g.id: moves.get(g.id, (ZERO, ZERO))[0] - moves.get(g.id, (ZERO, ZERO))[1] for g in ledgers if is_nominal(g)}
    stock_open, stock_close = stock_for(db, uid, p.fy, first)

    def groups_of(nature, section):
        return {c for c, g in GROUPS.items() if g.nature == nature and g.section == section}

    purchases = _side_items(ledgers, bal, {"PURCHASE"}, 1)
    direct_exp = _side_items(ledgers, bal, {"DIRECT_EXPENSES"}, 1)
    sales = _side_items(ledgers, bal, {"SALES"}, -1)
    direct_inc = _side_items(ledgers, bal, {"DIRECT_INCOMES"}, -1)
    t_dr = stock_open + sum((i["amount"] for i in purchases + direct_exp), ZERO)
    t_cr = (stock_close or ZERO) + sum((i["amount"] for i in sales + direct_inc), ZERO)
    gross = t_cr - t_dr

    indirect_exp = _side_items(ledgers, bal, groups_of(EXPENSE, PL), 1)
    indirect_inc = _side_items(ledgers, bal, groups_of(INCOME, PL), -1)
    pl_dr = (-gross if gross < 0 else ZERO) + sum((i["amount"] for i in indirect_exp), ZERO)
    pl_cr = (gross if gross > 0 else ZERO) + sum((i["amount"] for i in indirect_inc), ZERO)
    net = pl_cr - pl_dr
    trading_total = max(t_dr, t_cr)
    pl_total = max(pl_dr, pl_cr)

    def sec(title, items):
        return {"title": title, "items": items, "total": sum((i["amount"] for i in items), ZERO)}

    return {
        "period": p.label, "start": fy_start(p.fy), "end": p.end, "fy": p.fy,
        "closing_stock_missing": stock_missing(db, uid, p.fy, p.end, first, ledgers),
        "trading": {
            "debit": [sec("Opening Stock", [{"name": "Opening Stock", "amount": stock_open}] if stock_open else []),
                      sec("Purchase Accounts", purchases), sec("Direct Expenses", direct_exp)],
            "credit": [sec("Sales Accounts", sales), sec("Direct Incomes", direct_inc),
                       sec("Closing Stock", [{"name": "Closing Stock", "amount": stock_close}] if stock_close else [])],
            "gross_profit": gross, "total": trading_total,
        },
        "profit_loss": {
            "debit": [sec("Indirect Expenses", indirect_exp)], "credit": [sec("Indirect Incomes", indirect_inc)],
            "gross_profit": gross, "net_profit": net, "total": pl_total,
        },
        "net_profit": net,
    }


# ---------- Balance Sheet ----------
def balance_sheet(db: Session, books: AccSettings, as_of: date) -> dict:
    """Sole proprietor, horizontal format: Liabilities | Assets as at a date."""
    uid, fy, first = books.user_id, fy_of(as_of), _first_fy(books)
    ledgers = ledgers_of(db, uid)
    bal = balances_as_of(db, uid, as_of, ledgers)
    net = year_profit(db, uid, fy, as_of, first, ledgers)
    carried = retained_profit(db, uid, fy, first, ledgers)
    _, closing_stock = stock_for(db, uid, fy, first)
    difference = opening_difference(db, uid, first, ledgers)

    def items(group_codes, sign):
        return [{"ledger_id": g.id, "name": g.name, "code": g.code, "group": GROUPS[g.group_code].name,
                 "amount": sign * bal[g.id]}
                for g in sorted(ledgers, key=lambda g: (GROUPS[g.group_code].order, g.name.casefold()))
                if g.group_code in group_codes and bal.get(g.id)]

    def tree(root_code):
        return {c for c in GROUPS if root_of(c).code == root_code}

    # Capital: capital ledgers (credit), less drawings, plus profits
    capital_items = [i for i in items(tree("CAPITAL"), -1)]
    drawings = [i for i in capital_items if i["code"] == "DRAWINGS" or i["name"].casefold().startswith("drawing")]
    capital_rest = [i for i in capital_items if i not in drawings]
    capital_lines = capital_rest + (
        [{"name": "Add: Profit of earlier years" if carried >= 0 else "Less: Loss of earlier years", "amount": carried}] if carried else []
    ) + [{"name": "Add: Net Profit for the year" if net >= 0 else "Less: Net Loss for the year", "amount": net}] + [
        {**d, "name": f"Less: {d['name']}"} for d in drawings
    ]

    def sec(title, lines):
        return {"title": title, "items": lines, "total": sum((i["amount"] for i in lines), ZERO)}

    liabilities = [sec("Capital Account", capital_lines)]
    for root in ("LOANS", "CURRENT_LIABILITIES", "SUSPENSE"):
        lines = items(tree(root), -1)
        if lines:
            liabilities.append(sec(GROUPS[root].name, lines))
    assets = []
    for root in ("FIXED_ASSETS", "INVESTMENTS", "CURRENT_ASSETS"):
        lines = items(tree(root), 1)
        if root == "CURRENT_ASSETS" and closing_stock:
            lines = [{"name": "Closing Stock", "amount": closing_stock}] + lines
        if lines:
            assets.append(sec(GROUPS[root].name, lines))
    if difference > 0:
        liabilities.append(sec("Difference in opening balances", [{"name": "Difference in opening balances", "amount": difference}]))
    elif difference < 0:
        assets.append(sec("Difference in opening balances", [{"name": "Difference in opening balances", "amount": -difference}]))

    total_l = sum((s["total"] for s in liabilities), ZERO)
    total_a = sum((s["total"] for s in assets), ZERO)
    return {
        "as_of": as_of, "fy": fy, "period": f"As at {as_of:%d %b %Y} (FY {fy_label(fy)})",
        "liabilities": liabilities, "assets": assets, "total_liabilities": total_l, "total_assets": total_a,
        "balanced": total_l == total_a, "net_profit": net,
        "closing_stock_missing": stock_missing(db, uid, fy, as_of, first, ledgers),
    }


# ---------- Cash Flow Statement (indirect method, AS-3) ----------
def cash_flow(db: Session, books: AccSettings, p: Period) -> dict:
    """Net profit, adjusted for non-cash items and working capital, then investing and financing flows.

    Every ledger outside cash and bank is classified by its group, so the statement always reconciles:
    opening cash + net change = closing cash.
    """
    uid, first = books.user_id, _first_fy(books)
    ledgers = ledgers_of(db, uid)
    start_bal = opening_balances(db, uid, fy_start(p.fy), ledgers)
    end_bal = balances_as_of(db, uid, p.end, ledgers)
    moves = movements(db, uid, fy_start(p.fy), p.end)
    net = year_profit(db, uid, p.fy, p.end, first, ledgers)
    stock_open, stock_close = stock_for(db, uid, p.fy, first)

    depreciation = sum((moves.get(g.id, (ZERO, ZERO))[0] - moves.get(g.id, (ZERO, ZERO))[1]
                        for g in ledgers if g.is_depreciation), ZERO)
    changes: dict[str, dict[str, Decimal]] = {OPERATING: defaultdict(Decimal), INVESTING: defaultdict(Decimal),
                                              FINANCING: defaultdict(Decimal)}
    opening_cash = closing_cash = ZERO
    for g in ledgers:
        if is_nominal(g):
            continue
        cf = GROUPS[g.group_code].cash_flow
        delta = end_bal[g.id] - start_bal[g.id]
        if cf == CASH:
            opening_cash += start_bal[g.id]
            closing_cash += end_bal[g.id]
        elif delta:
            label = _cash_flow_label(g)
            changes[cf][label] -= delta  # an asset going up uses cash; a liability going up brings it in
    stock_change = (stock_close or ZERO) - stock_open
    if stock_change:
        changes[OPERATING]["(Increase) / decrease in stock"] -= stock_change
    if depreciation:
        # Depreciation is credited to the asset, so the asset ledgers fell by it without any cash moving
        changes[INVESTING]["Purchase of fixed assets (net)"] -= depreciation

    def section(key, title, extra=()):
        lines = list(extra) + [{"name": k, "amount": v} for k, v in sorted(changes[key].items()) if v]
        return {"title": title, "items": lines, "total": sum((l["amount"] for l in lines), ZERO)}

    operating = section(OPERATING, "Cash flow from operating activities", [
        {"name": "Net profit for the period", "amount": net},
        *([{"name": "Add: Depreciation", "amount": depreciation}] if depreciation else []),
    ])
    investing = section(INVESTING, "Cash flow from investing activities")
    financing = section(FINANCING, "Cash flow from financing activities")
    net_change = operating["total"] + investing["total"] + financing["total"]
    return {
        "period": f"{fy_start(p.fy):%d %b %Y} to {p.end:%d %b %Y} (FY {fy_label(p.fy)})", "start": fy_start(p.fy), "end": p.end,
        "sections": [operating, investing, financing], "net_change": net_change,
        "opening_cash": opening_cash, "closing_cash": closing_cash,
        "reconciled": opening_cash + net_change == closing_cash,
        "closing_stock_missing": stock_missing(db, uid, p.fy, p.end, first, ledgers),
    }


def _cash_flow_label(g: AccLedger) -> str:
    group = GROUPS[g.group_code]
    root = root_of(g.group_code)
    if root.code == "CAPITAL":
        return "Drawings" if (g.code == "DRAWINGS" or g.name.casefold().startswith("drawing")) else "Capital introduced / (withdrawn)"
    if root.code == "LOANS":
        return f"{group.name}: borrowed / (repaid)"
    if group.cash_flow == INVESTING:
        return "Purchase of fixed assets (net)" if root.code == "FIXED_ASSETS" else "Investments made / (sold)"
    if group.nature == ASSET or root.nature == ASSET:
        return f"(Increase) / decrease in {group.name}"
    return f"Increase / (decrease) in {group.name}"
