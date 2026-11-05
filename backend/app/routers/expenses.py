from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import ACCESS_ADMIN, Target, get_current_user, resolve_target
from ..models import ROLE_ADMIN, DailyItem, Expense, User
from ..services.accounts import resolve_account
from ..services.backup import KIND_EXPENSE, create_backup, row_to_dict
from ..services.budgets import check_budgets
from ..schemas import DailyEntryIn, ExpenseIn, ExpenseOut, ExpenseUpdate
from ..timeutil import now_local, today_local

router = APIRouter(prefix="/api/expenses", tags=["expenses"])


def _lock_at(expense: Expense):
    return expense.created_at + timedelta(days=settings.edit_window_days)


def _to_out(expense: Expense, target: Target) -> ExpenseOut:
    out = ExpenseOut.model_validate(expense)
    out.lock_at = _lock_at(expense)
    out.locked = now_local() > out.lock_at
    # Admin can always alter; the owner only inside the edit window; viewers never
    out.editable = target.level == ACCESS_ADMIN or (target.writable and not out.locked)
    return out


def _load_for_write(db: Session, viewer: User, expense_id: int) -> tuple[Expense, Target]:
    expense = db.get(Expense, expense_id)
    if not expense:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Expense not found")
    target = resolve_target(db, viewer, expense.user_id, write=True)
    if target.level != ACCESS_ADMIN and now_local() > _lock_at(expense):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"This expense is locked: it can only be changed within {settings.edit_window_days} days of creation",
        )
    return expense, target


def _check_daily_item(db: Session, owner_id: int, daily_item_id: int | None):
    if daily_item_id is not None:
        item = db.get(DailyItem, daily_item_id)
        if not item or item.user_id != owner_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid daily item")


@router.get("", response_model=list[ExpenseOut])
def list_expenses(
    user_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    category: str | None = None,
    search: str | None = None,
    viewer: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = resolve_target(db, viewer, user_id)
    stmt = select(Expense).where(Expense.user_id == target.user.id)
    if start:
        stmt = stmt.where(Expense.expense_date >= start)
    if end:
        stmt = stmt.where(Expense.expense_date <= end)
    if category:
        stmt = stmt.where(Expense.category == category)
    if search:
        # % and _ in the search text are matched literally, not as wildcards
        term = search.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(func.lower(Expense.item_name).like(f"%{term}%", escape="\\"))
    stmt = stmt.order_by(Expense.expense_date.desc(), Expense.id.desc())
    return [_to_out(e, target) for e in db.scalars(stmt)]


DEFAULT_CATEGORIES = ("Food", "Snacks", "Smoking", "Transport", "Groceries", "Bills", "Shopping", "Health", "Entertainment",
                      "General")


def user_categories(db: Session, user_id: int) -> list[str]:
    """The categories a user has: the defaults plus every one used in their expenses or daily items."""
    used = db.scalars(select(Expense.category).where(Expense.user_id == user_id).distinct()).all()
    templ = db.scalars(select(DailyItem.category).where(DailyItem.user_id == user_id).distinct()).all()
    return sorted(set(used) | set(templ) | set(DEFAULT_CATEGORIES))


@router.get("/categories", response_model=list[str])
def categories(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id)
    return user_categories(db, target.user.id)


@router.get("/day-status")
def day_status(
    day: date | None = None,
    user_id: int | None = None,
    viewer: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Whether expenses were already saved for a day (used by the daily entry page and reminders)."""
    target = resolve_target(db, viewer, user_id)
    day = day or today_local()
    count, total = db.execute(
        select(func.count(Expense.id), func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.user_id == target.user.id, Expense.expense_date == day
        )
    ).one()
    return {"date": day, "count": count, "total": float(total), "has_entries": count > 0}


@router.post("", response_model=ExpenseOut, status_code=201)
def add_expense(
    data: ExpenseIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    target = resolve_target(db, viewer, user_id, write=True)
    _check_daily_item(db, target.user.id, data.daily_item_id)
    expense = Expense(**data.model_dump(), user_id=target.user.id, created_by=viewer.id)
    expense.account_id = resolve_account(db, target.user.id, data.account_id)
    db.add(expense)
    db.flush()
    check_budgets(db, target.user, expense.expense_date, today_local())
    db.commit()
    return _to_out(expense, target)


@router.post("/daily", response_model=list[ExpenseOut], status_code=201)
def save_daily_entry(
    data: DailyEntryIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Confirm & save all of a day's items at once."""
    target = resolve_target(db, viewer, user_id, write=True)
    if data.expense_date > today_local():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot add expenses for a future date")
    created = []
    default_account = resolve_account(db, target.user.id, data.account_id)
    for item in data.items:
        _check_daily_item(db, target.user.id, item.daily_item_id)
        fields = item.model_dump()
        fields["expense_date"] = data.expense_date
        fields["account_id"] = resolve_account(db, target.user.id, item.account_id) if item.account_id else default_account
        expense = Expense(**fields, user_id=target.user.id, created_by=viewer.id)
        db.add(expense)
        created.append(expense)
    db.flush()
    check_budgets(db, target.user, data.expense_date, today_local())
    db.commit()
    return [_to_out(e, target) for e in created]


@router.put("/{expense_id}", response_model=ExpenseOut)
def update_expense(
    expense_id: int, data: ExpenseUpdate, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    expense, target = _load_for_write(db, viewer, expense_id)
    changes = data.model_dump(exclude_unset=True)
    if "account_id" in changes:
        # Clearing the account is allowed here (unlike on create, where empty means the primary account)
        changes["account_id"] = resolve_account(db, expense.user_id, changes["account_id"]) if changes["account_id"] else None
    for key, value in changes.items():
        if value is not None or key in ("note", "account_id"):
            setattr(expense, key, value)
    expense.updated_at = now_local()
    db.flush()
    check_budgets(db, target.user, expense.expense_date, today_local())
    db.commit()
    return _to_out(expense, target)


@router.delete("/{expense_id}", status_code=204)
def delete_expense(expense_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    expense, target = _load_for_write(db, viewer, expense_id)
    if viewer.role == ROLE_ADMIN:
        create_backup(
            db, KIND_EXPENSE, target.user, viewer, {"expenses": [row_to_dict(expense)]},
            summary=f"Expense '{expense.item_name}' {expense.amount} on {expense.expense_date} of @{target.user.username}",
        )
    db.delete(expense)
    db.commit()
