"""Settings → Categories: add your own categories, and hide the ones you never want to see (e.g. Smoking).

Expense and income categories are two lists (`kind`), since the same name can be both (e.g. Investment)."""

from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, resolve_target
from ..models import Expense, Fund, IncomeCategory, User, UserCategory
from .expenses import DEFAULT_CATEGORIES, user_categories
from .funds import INCOME_DEFAULTS, user_income_categories

router = APIRouter(prefix="/api/categories", tags=["categories"])

Kind = Literal["expense", "income"]


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class CategoryUpdate(BaseModel):
    is_hidden: bool


class CategoryOut(BaseModel):
    name: str
    is_default: bool
    is_custom: bool
    is_hidden: bool
    # How many expenses (or money received, for income) use it
    records: int


@dataclass(frozen=True)
class _List:
    model: type
    defaults: frozenset
    names: callable  # (db, user_id, include_hidden) -> list[str]
    record: type
    column: object


LISTS = {
    "expense": _List(UserCategory, frozenset(d.casefold() for d in DEFAULT_CATEGORIES), user_categories, Expense, Expense.category),
    "income": _List(IncomeCategory, frozenset(d.casefold() for d in INCOME_DEFAULTS), user_income_categories, Fund, Fund.category),
}


def _added(lst: _List, row) -> bool:
    """A category the user added themselves (also while hidden), as opposed to a default or used one they only hid."""
    if not row or row.name.casefold() in lst.defaults:
        return False
    return row.is_added if row.is_added is not None else not row.is_hidden


def _listing(db: Session, lst: _List, user_id: int) -> list[CategoryOut]:
    own = {c.name.casefold(): c for c in db.scalars(select(lst.model).where(lst.model.user_id == user_id))}
    counts = dict(db.execute(select(lst.column, func.count(lst.record.id)).where(lst.record.user_id == user_id).group_by(lst.column)).all())
    return [
        CategoryOut(
            name=name, is_default=name.casefold() in lst.defaults,
            is_custom=_added(lst, own.get(name.casefold())),
            is_hidden=bool(own.get(name.casefold()) and own[name.casefold()].is_hidden), records=counts.get(name, 0),
        )
        for name in lst.names(db, user_id, include_hidden=True)
    ]


def _row(db: Session, lst: _List, user_id: int, name: str):
    return db.scalar(select(lst.model).where(lst.model.user_id == user_id, func.lower(lst.model.name) == name.casefold()))


@router.get("", response_model=list[CategoryOut])
def list_categories(user_id: int | None = None, kind: Kind = Query("expense"), viewer: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id)
    return _listing(db, LISTS[kind], target.user.id)


@router.post("", response_model=list[CategoryOut], status_code=201)
def add_category(data: CategoryIn, user_id: int | None = None, kind: Kind = Query("expense"), viewer: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id, write=True)
    lst = LISTS[kind]
    name = " ".join(data.name.split())
    existing = next((c for c in lst.names(db, target.user.id, include_hidden=True) if c.casefold() == name.casefold()), None)
    row = _row(db, lst, target.user.id, name)
    if existing and not (row and row.is_hidden):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"You already have the category “{existing}”")
    if row:
        row.is_hidden = False  # adding a hidden one shows it again
    else:
        db.add(lst.model(user_id=target.user.id, name=name, is_added=True))
    db.commit()
    return _listing(db, lst, target.user.id)


@router.patch("/{name}", response_model=list[CategoryOut])
def hide_category(name: str, data: CategoryUpdate, user_id: int | None = None, kind: Kind = Query("expense"),
                  viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Hide a category from every list (its records keep it), or show it again."""
    target = resolve_target(db, viewer, user_id, write=True)
    lst = LISTS[kind]
    known = next((c for c in lst.names(db, target.user.id, include_hidden=True) if c.casefold() == name.casefold()), None)
    if not known:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    row = _row(db, lst, target.user.id, known)
    if row:
        row.is_hidden = data.is_hidden
    elif data.is_hidden:
        db.add(lst.model(user_id=target.user.id, name=known, is_hidden=True, is_added=False))
    db.commit()
    return _listing(db, lst, target.user.id)


@router.delete("/{name}", response_model=list[CategoryOut])
def remove_category(name: str, user_id: int | None = None, kind: Kind = Query("expense"), viewer: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Remove a category you added, shown or hidden. One that records still use stays in the list while they do."""
    target = resolve_target(db, viewer, user_id, write=True)
    lst = LISTS[kind]
    row = _row(db, lst, target.user.id, name)
    if not _added(lst, row):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Only categories you added can be removed")
    db.delete(row)
    db.commit()
    return _listing(db, lst, target.user.id)
