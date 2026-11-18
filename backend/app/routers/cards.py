"""Credit Cards page: every card with what it owes, its bill and due date, and its reminder settings."""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, resolve_target
from ..models import Account, CardBill, User
from ..schemas import CardBillIn, CardBillOut, CardMarkPaidIn, CardOut, CardSettingsIn
from ..services import cards as svc
from ..services.accounts import balances
from ..timeutil import today_local

router = APIRouter(prefix="/api/cards", tags=["cards"])


def _bill_out(db: Session, bill: CardBill | None) -> CardBillOut | None:
    if bill is None:
        return None
    state = svc.bill_state(db, bill, today_local())
    return CardBillOut(
        id=bill.id, account_id=bill.account_id, due_date=bill.due_date, statement_date=bill.statement_date,
        total_due=bill.total_due, min_due=bill.min_due, source=bill.source, paid_at=bill.paid_at, **state,
    )


def _card(db: Session, viewer: User, account_id: int, write: bool = False) -> Account:
    card = db.get(Account, account_id)
    if not card or card.kind != svc.CARD:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credit card not found")
    resolve_target(db, viewer, card.user_id, write=write)
    return card


def _card_out(db: Session, card: Account, bal: dict, bill: CardBill | None) -> CardOut:
    b = bal.get(card.id, {})
    owes = svc.owed(b.get("balance", 0))
    limit = Decimal(card.credit_limit) if card.credit_limit else None
    return CardOut(
        id=card.id, name=card.name, bank=card.bank, last4=card.last4, is_active=card.is_active, owed=owes,
        spent_this_month=b.get("spent_this_month", Decimal(0)), credit_limit=limit,
        available=max(Decimal(0), limit - owes) if limit is not None else None,
        used_percent=round(float(owes / limit * 100), 1) if limit else None,
        statement_day=card.statement_day, due_day=card.due_day, remind_days=card.remind_days, remind_on=card.remind_on,
        bill=_bill_out(db, bill),
    )


@router.get("", response_model=list[CardOut])
def list_cards(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Active cards first, the soonest unpaid bill first."""
    target = resolve_target(db, viewer, user_id)
    today = today_local()
    bal = balances(db, target.user.id, today.replace(day=1))
    out = []
    for card in svc.cards_of(db, target.user.id):
        owes = svc.owed(bal.get(card.id, {}).get("balance", 0))
        bill = svc.current_bill(db, card, today, owes) if card.is_active else None
        out.append(_card_out(db, card, bal, bill))
    db.commit()  # an estimate may have been made or refreshed

    def order(c: CardOut):
        unpaid = c.bill and not c.bill.paid
        return (not c.is_active, not unpaid, c.bill.due_date if unpaid else today, c.name.lower())

    return sorted(out, key=order)


@router.put("/{account_id}/settings", response_model=CardOut)
def update_settings(account_id: int, data: CardSettingsIn, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    card = _card(db, viewer, account_id, write=True)
    if data.due_day != card.due_day:
        # A different due day: the estimate for the old date no longer applies
        for bill in db.scalars(select(CardBill).where(CardBill.account_id == card.id, CardBill.source == svc.SRC_ESTIMATE,
                                                      CardBill.due_date >= today_local(), CardBill.paid_at.is_(None))):
            db.delete(bill)
        db.flush()
    for key, value in data.model_dump().items():
        setattr(card, key, value)
    today = today_local()
    bal = balances(db, card.user_id, today.replace(day=1))
    bill = svc.current_bill(db, card, today, svc.owed(bal.get(card.id, {}).get("balance", 0)))
    db.commit()
    return _card_out(db, card, bal, bill)


@router.get("/{account_id}/bills", response_model=list[CardBillOut])
def list_bills(account_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    card = _card(db, viewer, account_id)
    rows = db.scalars(select(CardBill).where(CardBill.account_id == card.id).order_by(CardBill.due_date.desc()).limit(24))
    return [_bill_out(db, b) for b in rows]


@router.post("/{account_id}/bills", response_model=CardBillOut, status_code=201)
def add_bill(account_id: int, data: CardBillIn, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A bill typed in from the card's statement (replaces an estimate or message for the same cycle)."""
    card = _card(db, viewer, account_id, write=True)
    if data.statement_date and data.statement_date > data.due_date:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The statement date must be before the due date")
    if data.min_due and data.min_due > data.total_due:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The minimum due can't be more than the total due")
    bill = svc.save_bill(db, card, data.due_date, data.total_due, svc.SRC_MANUAL, data.min_due, data.statement_date)
    db.commit()
    return _bill_out(db, bill)


def _bill(db: Session, viewer: User, bill_id: int) -> CardBill:
    bill = db.get(CardBill, bill_id)
    if not bill:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bill not found")
    resolve_target(db, viewer, bill.user_id, write=True)
    return bill


@router.post("/bills/{bill_id}/paid", response_model=CardBillOut)
def mark_paid(bill_id: int, data: CardMarkPaidIn, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Mark paid (e.g. paid from an account not in the app), or undo that."""
    bill = _bill(db, viewer, bill_id)
    svc.mark_paid(bill, data.paid)
    db.commit()
    return _bill_out(db, bill)


@router.delete("/bills/{bill_id}", status_code=204)
def delete_bill(bill_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_bill(db, viewer, bill_id))
    db.commit()
