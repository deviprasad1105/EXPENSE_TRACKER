from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import ROLE_ADMIN, ROLE_ORG_ADMIN, STATUS_APPROVED, DataShare, User
from .security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

ACCESS_OWNER = "OWNER"
ACCESS_ADMIN = "ADMIN"
ACCESS_MANAGER = "MANAGER"
ACCESS_SHARED = "SHARED"
# An org admin viewing one of their members (read-only; the member is not told)
ACCESS_ORG = "ORG"

ORG_LOCKED_MESSAGE = "Your organisation's access has been locked. Contact your administrator."


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    decoded = decode_access_token(token)
    user = db.get(User, decoded[0]) if decoded else None
    if not user or user.status != STATUS_APPROVED or decoded[1] != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    if user.is_locked:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your account is locked", headers={"WWW-Authenticate": "Bearer"})
    if user.org_locked:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, ORG_LOCKED_MESSAGE, headers={"WWW-Authenticate": "Bearer"})
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != ROLE_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user


def require_org_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != ROLE_ORG_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Organisation admin only")
    return user


def is_org_member(viewer: User, target: User) -> bool:
    """`target` belongs to the organisation `viewer` administers."""
    return viewer.role == ROLE_ORG_ADMIN and target.org_admin_id == viewer.id


FEATURE_EXPENSE = "expense"
FEATURE_SPLIT = "split"
FEATURE_SUBSCRIPTION = "subscription"
FEATURE_ACCOUNTING = "accounting"
FEATURE_UPI = "upi"
FEATURE_LABEL = {
    FEATURE_EXPENSE: "Expense tracking", FEATURE_SPLIT: "Split bills", FEATURE_SUBSCRIPTION: "Subscriptions",
    FEATURE_ACCOUNTING: "Accounting", FEATURE_UPI: "UPI tracker",
}


def has_feature(user: User, feature: str) -> bool:
    if user.role == ROLE_ADMIN:
        return True
    if feature in (FEATURE_ACCOUNTING, FEATURE_UPI) and not user.feature_expense:
        # The books and the UPI tracker are built on expense tracking, so they need it too
        return False
    return bool(getattr(user, f"feature_{feature}"))


def require_feature(feature: str):
    """Router dependency: the caller's subscription must include `feature` (granted by an admin)."""

    def check(user: User = Depends(get_current_user)) -> User:
        if not has_feature(user, feature):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"{FEATURE_LABEL[feature]} is not part of your plan. Ask an administrator for access."
            )
        return user

    return check


def subordinate_ids(db: Session, manager_id: int) -> set[int]:
    """Everyone below `manager_id` in the reporting line, at any depth (A > B > C gives A both B and C).

    One recursive query, so it doesn't load the whole users table. Loops can't exist: they are rejected
    whenever a manager is set (`would_create_cycle`).
    """
    team = select(User.id).where(User.manager_id == manager_id).cte("team", recursive=True)
    team = team.union_all(select(User.id).join(team, User.manager_id == team.c.id))
    return set(db.scalars(select(team.c.id))) - {manager_id}


def access_level(db: Session, viewer: User, target: User) -> str | None:
    """How `viewer` may access `target`'s data. Only OWNER and ADMIN may write."""
    if viewer.id == target.id:
        return ACCESS_OWNER
    if viewer.role == ROLE_ADMIN:
        return ACCESS_ADMIN
    if is_org_member(viewer, target):
        return ACCESS_ORG
    if target.id in subordinate_ids(db, viewer.id):
        return ACCESS_MANAGER
    shared = db.scalar(select(DataShare.id).where(DataShare.owner_id == target.id, DataShare.viewer_id == viewer.id))
    if shared:
        return ACCESS_SHARED
    return None


def can_write(level: str | None) -> bool:
    return level in (ACCESS_OWNER, ACCESS_ADMIN)


@dataclass
class Target:
    user: User
    level: str

    @property
    def writable(self) -> bool:
        return can_write(self.level)


def resolve_target(db: Session, viewer: User, user_id: int | None, write: bool = False) -> Target:
    """Resolve whose data a request is about (defaults to the caller) and enforce permissions."""
    target = viewer if user_id is None or user_id == viewer.id else db.get(User, user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    level = access_level(db, viewer, target)
    if level is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this user's data")
    if write and not can_write(level):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Read-only access: you cannot change this user's data")
    return Target(target, level)


def accessible_users(db: Session, viewer: User) -> list[tuple[User, str]]:
    """Every user whose data `viewer` can see, with the access level."""
    if viewer.role == ROLE_ADMIN:
        users = db.scalars(select(User).where(User.status == STATUS_APPROVED).order_by(User.username)).all()
        return [(u, ACCESS_OWNER if u.id == viewer.id else ACCESS_ADMIN) for u in users]

    result: list[tuple[User, str]] = [(viewer, ACCESS_OWNER)]
    seen = {viewer.id}
    if viewer.role == ROLE_ORG_ADMIN:
        stmt = select(User).where(User.org_admin_id == viewer.id, User.status == STATUS_APPROVED).order_by(User.full_name)
        for member in db.scalars(stmt):
            result.append((member, ACCESS_ORG))
            seen.add(member.id)
    team = subordinate_ids(db, viewer.id) - seen
    if team:
        stmt = select(User).where(User.id.in_(team), User.status == STATUS_APPROVED).order_by(User.full_name)
        for member in db.scalars(stmt):
            result.append((member, ACCESS_MANAGER))
            seen.add(member.id)
    for share in db.scalars(select(DataShare).where(DataShare.viewer_id == viewer.id)):
        if share.owner_id not in seen and share.owner.status == STATUS_APPROVED:
            result.append((share.owner, ACCESS_SHARED))
            seen.add(share.owner_id)
    return result


def would_create_cycle(db: Session, user_id: int, new_manager_id: int) -> bool:
    """True if making `new_manager_id` the manager of `user_id` would create a reporting loop."""
    return new_manager_id == user_id or new_manager_id in subordinate_ids(db, user_id)
