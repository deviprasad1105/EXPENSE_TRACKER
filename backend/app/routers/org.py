"""Organisation admin: the customer who bought the app for their organisation.

They can view (read-only, through the "viewing" switch), add, remove, lock and unlock the members of their own
organisation, and nobody else. Members are never told that their org admin can see their data. The app's super
admin (ROLE_ADMIN) keeps full control over everyone, org admins included.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_org_admin
from ..models import ROLE_USER, STATUS_APPROVED, User
from ..schemas import AdminReason, BackupOut, ManagedUserOut, OrgMemberCreate
from ..security import hash_password
from ..timeutil import now_local
from .admin import delete_account, lock_account, unlock_account

router = APIRouter(prefix="/api/org", tags=["organisation"])

FEATURES = ("feature_expense", "feature_split", "feature_subscription", "feature_accounting")


def _member(db: Session, org_admin: User, user_id: int) -> User:
    user = db.get(User, user_id)
    # A user outside the organisation looks the same as one that doesn't exist
    if not user or user.org_admin_id != org_admin.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    return user


@router.get("/members", response_model=list[ManagedUserOut])
def list_members(org_admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    return db.scalars(select(User).where(User.org_admin_id == org_admin.id).order_by(User.full_name)).all()


@router.post("/members", response_model=ManagedUserOut, status_code=201)
def add_member(data: OrgMemberCreate, org_admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    """Create an account in the organisation. It is approved straight away and gets the org admin's plan."""
    if db.scalar(select(User.id).where(or_(User.username == data.username, User.email == data.email))):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username or email already registered")
    user = User(
        username=data.username,
        email=data.email,
        full_name=data.full_name,
        password_hash=hash_password(data.password),
        role=ROLE_USER,
        status=STATUS_APPROVED,
        approved_at=now_local(),
        org_admin_id=org_admin.id,
        **{f: getattr(org_admin, f) for f in FEATURES},
    )
    db.add(user)
    db.commit()
    return user


@router.delete("/members/{user_id}", response_model=BackupOut)
def remove_member(
    user_id: int, reason: str | None = None, org_admin: User = Depends(require_org_admin), db: Session = Depends(get_db)
):
    """Delete a member's account and data. A full backup is kept, which the app admin can restore."""
    return delete_account(db, _member(db, org_admin, user_id), org_admin, reason)


@router.post("/members/{user_id}/lock", response_model=ManagedUserOut)
def lock_member(
    user_id: int, data: AdminReason, org_admin: User = Depends(require_org_admin), db: Session = Depends(get_db)
):
    user = _member(db, org_admin, user_id)
    lock_account(user, org_admin, data.reason)
    db.commit()
    return user


@router.post("/members/{user_id}/unlock", response_model=ManagedUserOut)
def unlock_member(user_id: int, org_admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    user = _member(db, org_admin, user_id)
    unlock_account(db, user, org_admin)
    db.commit()
    return user
