from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, resolve_target
from ..models import BUDGET_OVERALL, Budget, User
from ..schemas import BudgetIn, BudgetOut
from ..services.budgets import check_budgets, status_of
from ..timeutil import today_local

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


def _out(db: Session, budget: Budget) -> BudgetOut:
    return BudgetOut.model_validate(budget).model_copy(update=status_of(db, budget, today_local()))


def _target(data: BudgetIn) -> str:
    if data.scope == BUDGET_OVERALL:
        return "*"
    if not (data.target or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose the category or item this budget is for")
    return data.target.strip()


def _save(db: Session, budget: Budget, user: User):
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "There is already a budget for that") from None
    # Alert straight away if this month's spending is already past the new limit
    check_budgets(db, user, today_local(), today_local())
    db.commit()


@router.get("", response_model=list[BudgetOut])
def list_budgets(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Every budget with how much of it this month's spending has used."""
    target = resolve_target(db, viewer, user_id)
    budgets = db.scalars(select(Budget).where(Budget.user_id == target.user.id).order_by(Budget.scope.desc(), Budget.target)).all()
    return [_out(db, b) for b in budgets]


@router.post("", response_model=BudgetOut, status_code=201)
def add_budget(data: BudgetIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id, write=True)
    name = _target(data)
    clash = db.scalar(
        select(Budget.id).where(Budget.user_id == target.user.id, Budget.scope == data.scope, func.lower(Budget.target) == name.lower())
    )
    if clash:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "There is already a budget for that")
    budget = Budget(**data.model_dump(exclude={"target"}), target=name, user_id=target.user.id)
    db.add(budget)
    _save(db, budget, target.user)
    return _out(db, budget)


@router.put("/{budget_id}", response_model=BudgetOut)
def update_budget(budget_id: int, data: BudgetIn, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    budget = db.get(Budget, budget_id)
    if not budget:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Budget not found")
    target = resolve_target(db, viewer, budget.user_id, write=True)
    budget.scope, budget.target = data.scope, _target(data)
    budget.monthly_limit, budget.alert_percent, budget.is_active = data.monthly_limit, data.alert_percent, data.is_active
    # A changed limit starts the month's alerts afresh
    budget.alerted = None
    _save(db, budget, target.user)
    return _out(db, budget)


@router.delete("/{budget_id}", status_code=204)
def delete_budget(budget_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    budget = db.get(Budget, budget_id)
    if not budget:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Budget not found")
    resolve_target(db, viewer, budget.user_id, write=True)
    db.delete(budget)
    db.commit()
