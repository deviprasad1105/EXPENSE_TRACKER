"""Bills (Bills & Subscriptions page → Bills): add, change, mark paid, skip, undo, payment history."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import Target, get_current_user, resolve_target
from ..models import ROLE_ADMIN, Bill, BillPayment, User
from ..schemas import BillIn, BillMatch, BillOut, BillPayIn, BillPaymentOut
from ..services import bills as svc
from ..services.backup import KIND_BILL, create_backup, row_to_dict
from ..timeutil import now_local, today_local

router = APIRouter(prefix="/api/bills", tags=["bills"])

# Changing any of these moves the schedule
SCHEDULE_FIELDS = ("frequency", "interval_count", "interval_unit", "end_date")
_ORDER = {"OVERDUE": 0, "DUE_SOON": 1, "UPCOMING": 2, "PAUSED": 3, "DONE": 4}


def _to_out(db: Session, bill: Bill, linked: set[int] | None = None) -> BillOut:
    today = today_local()
    out = BillOut.model_validate(bill)
    out.status = svc.bill_status(bill, today)
    out.days_until = (bill.next_due_date - today).days if bill.next_due_date else None
    last = svc.last_payment(db, bill)
    if last:
        out.last_paid_on, out.last_amount = last.paid_on, last.amount
    # Only worth asking about once the bill is close (or past) its due date
    if out.status in ("DUE_SOON", "OVERDUE"):
        match = next(iter(svc.find_matches(db, bill, today, linked)), None)
        if match:
            out.match = BillMatch(expense_id=match.id, expense_date=match.expense_date, item_name=match.item_name,
                                  amount=match.amount, account_id=match.account_id)
    return out


def _load(db: Session, viewer: User, bill_id: int, write: bool = False) -> tuple[Bill, Target]:
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bill not found")
    return bill, resolve_target(db, viewer, bill.user_id, write=write)


def _check(data: BillIn):
    if data.end_date and data.end_date < data.due_date:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The last bill date is before the due date")


@router.get("", response_model=list[BillOut])
def list_bills(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id)
    bills = db.scalars(select(Bill).where(Bill.user_id == target.user.id)).all()
    linked = svc.linked_expense_ids(db, target.user.id)
    out = [_to_out(db, b, linked) for b in bills]
    return sorted(out, key=lambda b: (_ORDER[b.status], b.next_due_date or today_local(), b.name.lower()))


@router.post("", response_model=BillOut, status_code=201)
def add_bill(data: BillIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id, write=True)
    _check(data)
    values = data.model_dump(exclude={"due_date"})
    values["account_id"] = svc.check_account(db, target.user.id, data.account_id)
    bill = Bill(**values, user_id=target.user.id, start_date=data.due_date, next_due_date=data.due_date)
    db.add(bill)
    db.commit()
    return _to_out(db, bill)


@router.put("/{bill_id}", response_model=BillOut)
def update_bill(bill_id: int, data: BillIn, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bill, target = _load(db, viewer, bill_id, write=True)
    _check(data)
    values = data.model_dump(exclude={"due_date", "is_active"})
    values["account_id"] = svc.check_account(db, target.user.id, data.account_id)
    for key, value in values.items():
        setattr(bill, key, value)
    if data.due_date != bill.next_due_date:
        # A new due date: the schedule now keeps its day of the month
        bill.start_date = bill.next_due_date = data.due_date
        bill.reminded_for = bill.overdue_reminded_for = None
    if data.is_active != bill.is_active:
        bill.is_active = data.is_active
        if data.is_active and bill.next_due_date is None:
            bill.next_due_date = data.due_date
    bill.updated_at = now_local()
    db.commit()
    return _to_out(db, bill)


@router.delete("/{bill_id}", status_code=204)
def delete_bill(bill_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Expenses logged for its payments stay in the expense list."""
    bill, target = _load(db, viewer, bill_id, write=True)
    if viewer.role == ROLE_ADMIN:
        create_backup(
            db, KIND_BILL, target.user, viewer,
            {"bills": [row_to_dict(bill)], "bill_payments": [row_to_dict(p) for p in bill.payments]},
            summary=f"Bill '{bill.name}' of @{target.user.username}",
        )
    db.delete(bill)
    db.commit()


@router.post("/{bill_id}/pay", response_model=BillOut)
def pay_bill(bill_id: int, data: BillPayIn, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bill, target = _load(db, viewer, bill_id, write=True)
    svc.pay(db, bill, target.user, expense_id=data.expense_id, amount=data.amount, paid_on=data.paid_on,
            log_expense=data.log_expense, account_id=data.account_id)
    db.commit()
    return _to_out(db, bill)


@router.post("/{bill_id}/skip", response_model=BillOut)
def skip_bill(bill_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bill, _ = _load(db, viewer, bill_id, write=True)
    svc.skip(db, bill)
    db.commit()
    return _to_out(db, bill)


@router.get("/{bill_id}/payments", response_model=list[BillPaymentOut])
def payments(bill_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bill, _ = _load(db, viewer, bill_id)
    return bill.payments


@router.get("/{bill_id}/matches", response_model=list[BillMatch])
def matches(bill_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Expenses that look like the payment of the bill now due, best first (for the "Mark paid" dialog)."""
    bill, _ = _load(db, viewer, bill_id)
    return [BillMatch(expense_id=e.id, expense_date=e.expense_date, item_name=e.item_name, amount=e.amount, account_id=e.account_id)
            for e in svc.find_matches(db, bill)[:5]]


@router.delete("/{bill_id}/payments/{payment_id}", response_model=BillOut)
def undo_payment(bill_id: int, payment_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bill, _ = _load(db, viewer, bill_id, write=True)
    payment = db.get(BillPayment, payment_id)
    if not payment or payment.bill_id != bill.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    svc.undo(db, bill, payment)
    db.commit()
    return _to_out(db, bill)
