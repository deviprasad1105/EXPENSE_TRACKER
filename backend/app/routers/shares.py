from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import STATUS_APPROVED, DataShare, Notification, User
from ..schemas import ShareIn, ShareOut, UserBrief

router = APIRouter(prefix="/api/shares", tags=["sharing"])


def _out(s: DataShare) -> ShareOut:
    return ShareOut(id=s.id, owner=UserBrief.model_validate(s.owner), viewer=UserBrief.model_validate(s.viewer), created_at=s.created_at)


@router.get("")
def my_shares(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    shares = db.scalars(select(DataShare).where(or_(DataShare.owner_id == user.id, DataShare.viewer_id == user.id))).all()
    return {
        "shared_by_me": [_out(s) for s in shares if s.owner_id == user.id],
        "shared_with_me": [_out(s) for s in shares if s.viewer_id == user.id],
        "team": _team_tree(db, user.id),
        "managers": _manager_chain(user),
    }


def _team_tree(db: Session, manager_id: int) -> list[dict]:
    """Everyone reporting to `manager_id`, nested by level."""
    users = db.scalars(select(User).where(User.manager_id.is_not(None), User.status == STATUS_APPROVED)).all()
    by_manager: dict[int, list[User]] = {}
    for u in users:
        by_manager.setdefault(u.manager_id, []).append(u)

    def build(mid: int, seen: set[int]) -> list[dict]:
        nodes = []
        for u in sorted(by_manager.get(mid, []), key=lambda x: x.full_name):
            if u.id in seen:
                continue
            nodes.append({**UserBrief.model_validate(u).model_dump(), "reports": build(u.id, seen | {u.id})})
        return nodes

    return build(manager_id, {manager_id})


def _manager_chain(user: User) -> list[UserBrief]:
    """The caller's manager, their manager, and so on up to the top."""
    chain, seen, cur = [], {user.id}, user.manager
    while cur and cur.id not in seen:
        chain.append(UserBrief.model_validate(cur))
        seen.add(cur.id)
        cur = cur.manager
    return chain


@router.post("", response_model=ShareOut, status_code=201)
def share(data: ShareIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    viewer = db.scalar(select(User).where(User.username == data.viewer_username, User.status == STATUS_APPROVED))
    if not viewer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if viewer.id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot share with yourself")
    if db.scalar(select(DataShare.id).where(DataShare.owner_id == user.id, DataShare.viewer_id == viewer.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Already shared with this user")
    s = DataShare(owner_id=user.id, viewer_id=viewer.id)
    db.add(s)
    db.add(Notification(user_id=viewer.id, kind="SHARE", message=f"{user.full_name} ({user.username}) shared their expenses with you (view only)"))
    db.commit()
    db.refresh(s)
    return _out(s)


@router.delete("/{share_id}", status_code=204)
def unshare(share_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.get(DataShare, share_id)
    # Either the owner revokes, or the viewer removes it from their list
    if not s or user.id not in (s.owner_id, s.viewer_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")
    db.delete(s)
    db.commit()
