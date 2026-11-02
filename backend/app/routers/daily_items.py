from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, resolve_target
from ..models import ROLE_ADMIN, DailyItem, User
from ..schemas import DailyItemIn, DailyItemOut
from ..services.backup import KIND_DAILY_ITEM, create_backup, row_to_dict

router = APIRouter(prefix="/api/daily-items", tags=["daily items"])


def _load(db: Session, viewer: User, item_id: int) -> DailyItem:
    item = db.get(DailyItem, item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    resolve_target(db, viewer, item.user_id, write=True)
    return item


def _commit(db: Session):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a daily item with this name")


@router.get("", response_model=list[DailyItemOut])
def list_items(
    user_id: int | None = None,
    active_only: bool = False,
    viewer: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = resolve_target(db, viewer, user_id)
    stmt = select(DailyItem).where(DailyItem.user_id == target.user.id)
    if active_only:
        stmt = stmt.where(DailyItem.is_active == true())
    return db.scalars(stmt.order_by(DailyItem.sort_order, DailyItem.name)).all()


@router.post("", response_model=DailyItemOut, status_code=201)
def create_item(
    data: DailyItemIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    target = resolve_target(db, viewer, user_id, write=True)
    item = DailyItem(**data.model_dump(), user_id=target.user.id)
    db.add(item)
    _commit(db)
    return item


@router.put("/{item_id}", response_model=DailyItemOut)
def update_item(item_id: int, data: DailyItemIn, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _load(db, viewer, item_id)
    for key, value in data.model_dump().items():
        setattr(item, key, value)
    _commit(db)
    return item


@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: int, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _load(db, viewer, item_id)
    if viewer.role == ROLE_ADMIN:
        owner = db.get(User, item.user_id)
        create_backup(
            db, KIND_DAILY_ITEM, owner, viewer, {"daily_items": [row_to_dict(item)]},
            summary=f"Daily item '{item.name}' of @{owner.username}",
        )
    db.delete(item)
    db.commit()
