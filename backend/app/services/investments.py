"""Payments that look like buying an investment (a SIP, shares, crypto, gold, money sent to a broker), waiting to be
added to the Investments page, and adding them to a holding.

A payment is an expense whose category, name or note says so, or a transfer into an Investment-type account. It
waits until the user adds it to a holding (an existing one, or a new one) or says it isn't an investment; either is
remembered in `investment_links`, so it is asked about once.

Only payments after the holdings' latest valuation date are asked about (at most 60 days back): anything earlier is
already in the values the user typed in.
"""

import re
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Account, AccountTransfer, Expense, Holding, InvestmentLink

SRC_EXPENSE, SRC_TRANSFER = "EXPENSE", "TRANSFER"
LOOKBACK_DAYS = 60

# Words in a category, item, note or bank payee that mean buying an investment ("share" alone isn't: a dinner share)
_INVEST = re.compile(
    r"\bsip\b|mutual\s*funds?|\bmf\b|\binvest|\bstocks?\b|\bequity\b|\betfs?\b|\bipo\b|demat|\bbroker\b|"
    r"groww|zerodha|nuvama|upstox|angel\s*one|kuvera|paytm\s*money|coin\s*dcx|wazirx|crypto|bitcoin|"
    r"digital\s*gold|safegold|augmont|smallcase|nse\s*clearing|indian\s*clearing|\biccl\b|bse\s*star|bsestarmf|"
    r"\bcams\b|kfin",
    re.I,
)
# ...but fees and charges at a broker aren't a purchase
_NOT_A_PURCHASE = re.compile(r"charges?\b|\bfees?\b|\bamc\b|maintenance|brokerage|\bgst\b|penalt|\btax\b", re.I)


def looks_like_investment(*texts: str | None) -> bool:
    text = " ".join(t for t in texts if t)
    return bool(_INVEST.search(text)) and not _NOT_A_PURCHASE.search(text)


def since_date(db: Session, user_id: int, today: date) -> date:
    """The first day asked about: the day after the latest valuation of a holding, but at most 60 days back."""
    latest = db.scalar(select(func.max(Holding.valued_on)).where(Holding.user_id == user_id, Holding.is_active.is_(True)))
    earliest = today - timedelta(days=LOOKBACK_DAYS)
    return max(earliest, latest + timedelta(days=1)) if latest else earliest


def _linked(db: Session, user_id: int, source: str) -> set[int]:
    return set(db.scalars(select(InvestmentLink.source_id).where(InvestmentLink.user_id == user_id,
                                                                  InvestmentLink.source == source)))


def pending(db: Session, user_id: int, today: date, expense_ids: list[int] | None = None,
            transfer_ids: list[int] | None = None) -> tuple[date, list[dict]]:
    """Payments waiting to be added, newest first. With ids, only those (e.g. what was just saved)."""
    since = since_date(db, user_id, today)
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.user_id == user_id))}
    out: list[dict] = []

    if transfer_ids is None or expense_ids is not None:
        q = select(Expense).where(Expense.user_id == user_id, Expense.expense_date >= since)
        if expense_ids is not None:
            q = q.where(Expense.id.in_(expense_ids or [0]))
        done = _linked(db, user_id, SRC_EXPENSE)
        for e in db.scalars(q):
            if e.id in done or not looks_like_investment(e.category, e.item_name, e.note, e.raw_name):
                continue
            acc = accounts.get(e.account_id)
            if acc and acc.kind == "INVESTMENT":  # paid out of the investment account itself: not new money in
                continue
            out.append({"source": SRC_EXPENSE, "id": e.id, "date": e.expense_date, "amount": e.amount,
                        "label": e.item_name if e.item_name == e.category else f"{e.item_name} ({e.category})",
                        "account": acc.name if acc else None})

    if expense_ids is None or transfer_ids is not None:
        invest = {a.id for a in accounts.values() if a.kind == "INVESTMENT"}
        if invest:
            q = select(AccountTransfer).where(AccountTransfer.user_id == user_id, AccountTransfer.transfer_date >= since,
                                              AccountTransfer.to_account_id.in_(invest),
                                              AccountTransfer.from_account_id.not_in(invest))
            if transfer_ids is not None:
                q = q.where(AccountTransfer.id.in_(transfer_ids or [0]))
            done = _linked(db, user_id, SRC_TRANSFER)
            for t in db.scalars(q):
                if t.id in done:
                    continue
                out.append({"source": SRC_TRANSFER, "id": t.id, "date": t.transfer_date, "amount": t.amount,
                            "label": t.note or f"Moved to {accounts[t.to_account_id].name}",
                            "account": accounts[t.from_account_id].name})

    out.sort(key=lambda p: (p["date"], p["id"]), reverse=True)
    return since, out


def find_payment(db: Session, source: str, source_id: int):
    """(user id, date, amount) of an expense or transfer, or None."""
    row = db.get(Expense if source == SRC_EXPENSE else AccountTransfer, source_id)
    if row is None:
        return None
    return row.user_id, (row.expense_date if source == SRC_EXPENSE else row.transfer_date), row.amount


def add_money(holding: Holding, amount: Decimal, units: Decimal | None = None) -> None:
    """Money put into a holding: more invested, more units if known, and (if it has a value) that much more value."""
    holding.invested = (holding.invested or Decimal(0)) + amount
    if units:
        holding.units = (holding.units or Decimal(0)) + units
    if holding.current_value is not None:
        holding.current_value += amount


def buy(db: Session, holding: Holding, amount: Decimal, units: Decimal | None = None) -> None:
    """Add a purchase to a holding, and to the line it is part of (whose figures include it)."""
    add_money(holding, amount, units)
    if holding.parent_id:
        parent = db.get(Holding, holding.parent_id)
        if parent:
            add_money(parent, amount)
