"""Records that keep their own copy of an expense's or a transfer's amount follow it when it changes, whenever it was
saved (an entry can be edited at any time):

- an investment it was added to (Investments → "Waiting to be added" → Add…): the holding's invested amount and value
  (and its parent line's) change by the difference; if the payment is deleted, they go down by what it added
- a bill it paid (Bills → Mark paid) and a subscription charge it records: their amount becomes the new one

Balances, budgets, reports and the dashboard are worked out from the entries themselves, and the books follow them
through accounting/autopost.py, so they need nothing here.

How it runs: a SQLAlchemy session event, just before each flush, so every path (API routes, imports, restores) is
covered without each one having to remember.
"""

from decimal import Decimal

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from ..models import AccountTransfer, BillPayment, Expense, Holding, InvestmentLink, SubscriptionCharge
from .investments import SRC_EXPENSE, SRC_TRANSFER, buy


def _source(obj) -> str | None:
    if isinstance(obj, Expense):
        return SRC_EXPENSE
    if isinstance(obj, AccountTransfer):
        return SRC_TRANSFER
    return None


def _link(session: Session, source: str, source_id: int) -> InvestmentLink | None:
    return session.scalar(select(InvestmentLink).where(InvestmentLink.source == source,
                                                       InvestmentLink.source_id == source_id))


def _move_holding(session: Session, link: InvestmentLink, by: Decimal) -> None:
    holding = session.get(Holding, link.holding_id) if link.holding_id else None
    if holding is not None and by:
        buy(session, holding, by)  # a negative amount takes money out (and out of its parent line)


def _amount_changed(session: Session, obj, source: str, new: Decimal) -> None:
    link = _link(session, source, obj.id)
    if link is not None and link.amount != new:
        _move_holding(session, link, new - link.amount)
        link.amount = new
    if source == SRC_EXPENSE:
        for row in session.scalars(select(BillPayment).where(BillPayment.expense_id == obj.id)):
            row.amount = new
        for row in session.scalars(select(SubscriptionCharge).where(SubscriptionCharge.expense_id == obj.id)):
            row.amount = new


def _removed(session: Session, obj, source: str) -> None:
    link = _link(session, source, obj.id)
    if link is not None:
        _move_holding(session, link, -link.amount)
        session.delete(link)


@event.listens_for(Session, "before_flush")
def _follow(session: Session, flush_context, instances) -> None:
    with session.no_autoflush:
        for obj in list(session.dirty):
            source = _source(obj)
            if source is None or obj.id is None:
                continue
            history = inspect(obj).attrs.amount.history
            if history.deleted and obj.amount is not None:
                _amount_changed(session, obj, source, Decimal(str(obj.amount)))
        for obj in list(session.deleted):
            source = _source(obj)
            if source is not None and obj.id is not None:
                _removed(session, obj, source)
