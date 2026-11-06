"""Settings → Categories: add your own categories, and hide the ones you never want to see (e.g. Smoking)."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, resolve_target
from ..models import Expense, User, UserCategory
from .expenses import DEFAULT_CATEGORIES, user_categories

router = APIRouter(prefix="/api/categories", tags=["categories"])


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class CategoryUpdate(BaseModel):
    is_hidden: bool


class CategoryOut(BaseModel):
    name: str
    is_default: bool
    is_custom: bool
    is_hidden: bool
    # How many expenses use it
    records: int


def _listing(db: Session, user_id: int) -> list[CategoryOut]:
    own = {c.name.casefold(): c for c in db.scalars(select(UserCategory).where(UserCategory.user_id == user_id))}
    counts = dict(db.execute(select(Expense.category, func.count(Expense.id)).where(Expense.user_id == user_id).group_by(Expense.category)).all())
    defaults = {d.casefold() for d in DEFAULT_CATEGORIES}
    return [
        CategoryOut(
            name=name, is_default=name.casefold() in defaults,
            is_custom=name.casefold() in own and name.casefold() not in defaults and not own[name.casefold()].is_hidden,
            is_hidden=bool(own.get(name.casefold()) and own[name.casefold()].is_hidden), records=counts.get(name, 0),
        )
        for name in user_categories(db, user_id, include_hidden=True)
    ]


def _row(db: Session, user_id: int, name: str) -> UserCategory | None:
    return db.scalar(select(UserCategory).where(UserCategory.user_id == user_id, func.lower(UserCategory.name) == name.casefold()))


@router.get("", response_model=list[CategoryOut])
def list_categories(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id)
    return _listing(db, target.user.id)


@router.post("", response_model=list[CategoryOut], status_code=201)
def add_category(data: CategoryIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id, write=True)
    name = " ".join(data.name.split())
    existing = next((c for c in user_categories(db, target.user.id, include_hidden=True) if c.casefold() == name.casefold()), None)
    row = _row(db, target.user.id, name)
    if existing and not (row and row.is_hidden):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"You already have the category “{existing}”")
    if row:
        row.is_hidden = False  # adding a hidden one shows it again
    else:
        db.add(UserCategory(user_id=target.user.id, name=name))
    db.commit()
    return _listing(db, target.user.id)


@router.patch("/{name}", response_model=list[CategoryOut])
def hide_category(name: str, data: CategoryUpdate, user_id: int | None = None, viewer: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Hide a category from every list (its records keep it), or show it again."""
    target = resolve_target(db, viewer, user_id, write=True)
    known = next((c for c in user_categories(db, target.user.id, include_hidden=True) if c.casefold() == name.casefold()), None)
    if not known:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    row = _row(db, target.user.id, known)
    if row:
        row.is_hidden = data.is_hidden
    elif data.is_hidden:
        db.add(UserCategory(user_id=target.user.id, name=known, is_hidden=True))
    db.commit()
    return _listing(db, target.user.id)


@router.delete("/{name}", response_model=list[CategoryOut])
def remove_category(name: str, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Remove a category you added. One that expenses still use stays in the list while they do: hide it instead."""
    target = resolve_target(db, viewer, user_id, write=True)
    row = _row(db, target.user.id, name)
    if not row or row.is_hidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Only categories you added can be removed")
    db.delete(row)
    db.commit()
    return _listing(db, target.user.id)
