from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import false, func, select, update
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Notification, User
from ..schemas import NotificationOut
from ..services.reminders import ensure_missed_day_notifications

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
def list_notifications(unread_only: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Catch up on reminders for days the scheduler missed (e.g. server was down at reminder time)
    ensure_missed_day_notifications(db, user)
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.is_read == false())
    return db.scalars(stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(100)).all()


@router.get("/unread-count")
def unread_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    count = db.scalar(select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.is_read == false()))
    return {"count": count}


@router.post("/{notification_id}/read", status_code=204)
def mark_read(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if not n or n.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    n.is_read = True
    db.commit()


@router.post("/read-all", status_code=204)
def mark_all_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.execute(update(Notification).where(Notification.user_id == user.id).values(is_read=True))
    db.commit()
