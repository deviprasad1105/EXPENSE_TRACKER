"""Bank account balances and choosing which account a transaction belongs to."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from ..models import Account, AccountTransfer, Expense, Fund


def primary_account_id(db: Session, user_id: int) -> int | None:
    return db.scalar(
        select(Account.id).where(Account.user_id == user_id, Account.is_primary == true(), Account.is_active == true())
    )


def resolve_account(db: Session, user_id: int, account_id: int | None) -> int | None:
    """The account a transaction goes to: the one given (checked to be the user's) or else the primary account."""
    if account_id is None:
        return primary_account_id(db, user_id)
    account = db.get(Account, account_id)
    if not account or account.user_id != user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid account")
    return account.id


def _sums(db: Session, column, amount, user_col, user_id: int, where=()) -> dict[int, Decimal]:
    rows = db.execute(
        select(column, func.sum(amount)).where(user_col == user_id, column.is_not(None), *where).group_by(column)
    ).all()
    return {k: Decimal(v or 0) for k, v in rows}


def balances(db: Session, user_id: int, month_start: date) -> dict[int, dict[str, Decimal]]:
    """Per account: received, spent, transferred in / out, balance, and spent since month_start."""
    received = _sums(db, Fund.account_id, Fund.amount, Fund.user_id, user_id)
    spent = _sums(db, Expense.account_id, Expense.amount, Expense.user_id, user_id)
    month = _sums(db, Expense.account_id, Expense.amount, Expense.user_id, user_id, (Expense.expense_date >= month_start,))
    t_in = _sums(db, AccountTransfer.to_account_id, AccountTransfer.amount, AccountTransfer.user_id, user_id)
    t_out = _sums(db, AccountTransfer.from_account_id, AccountTransfer.amount, AccountTransfer.user_id, user_id)
    out: dict[int, dict[str, Decimal]] = defaultdict(dict)
    for account in db.scalars(select(Account).where(Account.user_id == user_id)):
        a = account.id
        r, s, i, o = received.get(a, Decimal(0)), spent.get(a, Decimal(0)), t_in.get(a, Decimal(0)), t_out.get(a, Decimal(0))
        out[a] = {
            "received": r, "spent": s, "transferred_in": i, "transferred_out": o,
            "balance": Decimal(account.opening_balance) + r - s + i - o,
            "spent_this_month": month.get(a, Decimal(0)),
        }
    return out
