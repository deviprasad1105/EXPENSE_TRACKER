"""Bank account balances and choosing which account a transaction belongs to."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from ..models import (
    Account, AccountTransfer, Expense, Fund, SplitBill, SplitMember, SplitSettlement, Subscription, SubscriptionCharge,
)


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


def _total(db: Session, stmt) -> Decimal:
    return Decimal(db.scalar(stmt) or 0)


def _primary_extra(db: Session, user_id: int, month_start: date) -> dict[str, Decimal]:
    """Money that belongs to no account but moves the primary one, exactly as the accounting books post it
    (engine.payment_ledger): expenses and money received with no account, split bills the user paid, split
    settlements they paid or received, and subscription charges not logged as an expense."""
    member_ids = select(SplitMember.id).where(SplitMember.user_id == user_id)
    spent = _total(db, select(func.sum(Expense.amount)).where(Expense.user_id == user_id, Expense.account_id.is_(None)))
    month = _total(db, select(func.sum(Expense.amount)).where(
        Expense.user_id == user_id, Expense.account_id.is_(None), Expense.expense_date >= month_start))
    received = _total(db, select(func.sum(Fund.amount)).where(Fund.user_id == user_id, Fund.account_id.is_(None)))
    split_paid = _total(db, select(func.sum(SplitBill.total_amount)).where(SplitBill.paid_by.in_(member_ids)))
    settled_out = _total(db, select(func.sum(SplitSettlement.amount)).where(SplitSettlement.from_member.in_(member_ids)))
    settled_in = _total(db, select(func.sum(SplitSettlement.amount)).where(SplitSettlement.to_member.in_(member_ids)))
    charges = _total(db, select(func.sum(SubscriptionCharge.amount)).join(Subscription).where(
        Subscription.user_id == user_id, SubscriptionCharge.expense_id.is_(None)))
    return {"received": received + settled_in, "spent": spent + split_paid + settled_out + charges, "spent_this_month": month}


def balances(db: Session, user_id: int, month_start: date) -> dict[int, dict[str, Decimal]]:
    """Per account: received, spent, transferred in / out, balance, and spent since month_start."""
    received = _sums(db, Fund.account_id, Fund.amount, Fund.user_id, user_id)
    spent = _sums(db, Expense.account_id, Expense.amount, Expense.user_id, user_id)
    month = _sums(db, Expense.account_id, Expense.amount, Expense.user_id, user_id, (Expense.expense_date >= month_start,))
    t_in = _sums(db, AccountTransfer.to_account_id, AccountTransfer.amount, AccountTransfer.user_id, user_id)
    t_out = _sums(db, AccountTransfer.from_account_id, AccountTransfer.amount, AccountTransfer.user_id, user_id)
    out: dict[int, dict[str, Decimal]] = defaultdict(dict)
    primary = primary_account_id(db, user_id)
    for account in db.scalars(select(Account).where(Account.user_id == user_id)):
        a = account.id
        r, s, i, o = received.get(a, Decimal(0)), spent.get(a, Decimal(0)), t_in.get(a, Decimal(0)), t_out.get(a, Decimal(0))
        m = month.get(a, Decimal(0))
        if a == primary:  # what has no account is the primary account's, as in the books
            extra = _primary_extra(db, user_id, month_start)
            r, s, m = r + extra["received"], s + extra["spent"], m + extra["spent_this_month"]
        out[a] = {
            "received": r, "spent": s, "transferred_in": i, "transferred_out": o,
            "balance": Decimal(account.opening_balance) + r - s + i - o,
            "spent_this_month": m,
        }
    return out
