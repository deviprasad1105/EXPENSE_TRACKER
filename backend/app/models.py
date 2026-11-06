from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    Numeric,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, object_session, relationship

from .database import Base
from .timeutil import now_local

ROLE_ADMIN = "ADMIN"
# Admin of one organisation (the customer who bought the app for their people): manages only their members
ROLE_ORG_ADMIN = "ORG_ADMIN"
ROLE_USER = "USER"

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"


class User(Base):
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default=ROLE_USER)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=STATUS_PENDING)
    # Reporting line: a manager can view (read-only) the data of everyone below them, at any depth
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("app_users.id", ondelete="SET NULL"))
    # Organisation: the ORG_ADMIN this user belongs to. The org admin can view (read-only), add, remove, lock and
    # unlock their members; members are never told. Locking the org admin locks every member too.
    org_admin_id: Mapped[int | None] = mapped_column(ForeignKey("app_users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Set whenever the username changes; the user cannot change it again for USERNAME_LOCK_DAYS
    username_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    avatar_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    show_tour: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    theme: Mapped[str] = mapped_column(String(10), nullable=False, default="light")
    # Admin lock: a locked user cannot sign in, and their open sessions stop working
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime)
    locked_by: Mapped[str | None] = mapped_column(String(50))
    lock_reason: Mapped[str | None] = mapped_column(String(500))
    # Features ("subscriptions") the admin has granted. Admins always have every feature.
    feature_expense: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    feature_split: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    feature_subscription: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Double-entry books and reports; needs feature_expense as well
    feature_accounting: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # UPI tracker: bank / UPI app messages forwarded to the user's private link become expenses and money received.
    # Needs feature_expense as well. upi_enabled is the user's own on / off switch.
    feature_upi: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    upi_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # SHA-256 of the private forwarding token (the token itself is shown once and never stored)
    upi_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    upi_token_hint: Mapped[str | None] = mapped_column(String(8))
    # Part of every sign-in token: raising it (password change, "sign out everywhere", lock) revokes all tokens
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Wrong passwords in a row; at LOGIN_MAX_FAILURES sign-in is blocked until login_blocked_until
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    login_blocked_until: Mapped[datetime | None] = mapped_column(DateTime)
    # Ask which bank account paid for each expense; when off, everything goes to the primary account
    ask_account: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    background_updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    manager: Mapped["User | None"] = relationship(
        remote_side="User.id", back_populates="reports", foreign_keys="User.manager_id"
    )
    reports: Mapped[list["User"]] = relationship(back_populates="manager", foreign_keys="User.manager_id")
    org_admin: Mapped["User | None"] = relationship(
        remote_side="User.id", back_populates="org_members", foreign_keys="User.org_admin_id"
    )
    org_members: Mapped[list["User"]] = relationship(back_populates="org_admin", foreign_keys="User.org_admin_id")

    @property
    def org_locked(self) -> bool:
        """Locked because their organisation's admin is locked (the member's own lock is `is_locked`)."""
        return bool(self.org_admin_id and self.org_admin and self.org_admin.is_locked)

    @property
    def in_split_group(self) -> bool:
        """Member of a split group. They can use that group even if their plan doesn't include split bills."""
        db = object_session(self)
        if db is None:
            return False
        member = db.scalar(
            select(SplitMember.id).where(SplitMember.user_id == self.id, SplitMember.left_at.is_(None)).limit(1)
        )
        return member is not None


class UserAvatar(Base):
    """Profile photo, kept out of app_users so user queries never load the BLOB."""

    __tablename__ = "user_avatars"

    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), primary_key=True)
    image: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(50), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)


class UserBackground(Base):
    """Custom background image for the app, kept out of app_users like the avatar."""

    __tablename__ = "user_backgrounds"

    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), primary_key=True)
    image: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(50), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)


# ---------- bank accounts ----------
# INVESTMENT (mutual funds, shares, a broker balance) and RECEIVABLE (money lent or paid on someone's behalf, to
# get back) hold your money but aren't spendable cash: moving money into them is a transfer, not spending.
ACCOUNT_KINDS = ("SAVINGS", "CURRENT", "SALARY", "CREDIT_CARD", "WALLET", "CASH", "INVESTMENT", "RECEIVABLE")


class Account(Base):
    """A bank account, card, wallet or cash the user receives or spends money from.

    Its balance is opening_balance + funds received - expenses paid + transfers in - transfers out.
    """

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_account_user_name"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    bank: Mapped[str | None] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(15), nullable=False, default="SAVINGS")
    # Last 4 digits only, never the full number
    last4: Mapped[str | None] = mapped_column(String(4))
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    # Expenses without an account chosen are paid from the primary one (at most one per user)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)


class AccountTransfer(Base):
    """Money moved between two of the user's own accounts. Not income and not an expense."""

    __tablename__ = "account_transfers"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    from_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    to_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    transfer_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)


# ---------- budget alerts ----------
BUDGET_OVERALL = "OVERALL"
BUDGET_CATEGORY = "CATEGORY"
BUDGET_ITEM = "ITEM"


class Budget(Base):
    """A monthly spending limit, overall or for one category or item, with an alert at `alert_percent`."""

    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("user_id", "scope", "target", name="uq_budget"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    # OVERALL (all spending), CATEGORY or ITEM
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default=BUDGET_OVERALL)
    # The category or item name; "*" for OVERALL so the unique key works on every database
    target: Mapped[str] = mapped_column(String(100), nullable=False, default="*")
    monthly_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    alert_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Latest alert sent, e.g. "2026-10:WARN" or "2026-10:OVER", so each level is notified once a month
    alerted: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)


class DailyItem(Base):
    """A user's customisable list of things they spend on daily (cigarette, chocolate, food...)."""

    __tablename__ = "daily_items"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_daily_item_user_name"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="General")
    default_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    default_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Constant items keep their default amount every day; customisable items can be changed per day
    is_constant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="General")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    is_extra: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    daily_item_id: Mapped[int | None] = mapped_column(ForeignKey("daily_items.id", ondelete="SET NULL"))
    # The account it was paid from (NULL: not tracked)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_users.id", ondelete="SET NULL"))
    # Transaction aliases: who the money went to, as found in the description ("ramesh@ybl", "tea stall"), the
    # description before an alias renamed it, and the alias that did
    payee_key: Mapped[str | None] = mapped_column(String(120), index=True)
    raw_name: Mapped[str | None] = mapped_column(String(200))
    alias_id: Mapped[int | None] = mapped_column(ForeignKey("transaction_aliases.id", ondelete="SET NULL"), index=True)
    # Where it came from when not typed in: "UPI" (UPI tracker)
    origin: Mapped[str | None] = mapped_column(String(10))

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class Fund(Base):
    """Money added to the balance (salary, pocket money, top-up...)."""

    __tablename__ = "funds"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    fund_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="Income")
    note: Mapped[str | None] = mapped_column(String(500))
    # The account it was received in (NULL: not tracked)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)
    # As on Expense: an alias renames `source`, keeping the original in raw_name
    payee_key: Mapped[str | None] = mapped_column(String(120), index=True)
    raw_name: Mapped[str | None] = mapped_column(String(200))
    alias_id: Mapped[int | None] = mapped_column(ForeignKey("transaction_aliases.id", ondelete="SET NULL"), index=True)
    origin: Mapped[str | None] = mapped_column(String(10))


class TransactionAlias(Base):
    """A readable name for a payee: every expense or money received matching `match_key` is shown as `alias`.

    `match_key` is a payee key (a UPI ID like "ramesh@ybl", or the description cleaned of reference numbers),
    matched exactly, or as words inside the description.
    """

    __tablename__ = "transaction_aliases"
    __table_args__ = (UniqueConstraint("user_id", "match_key", name="uq_alias_user_key"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    match_key: Mapped[str] = mapped_column(String(120), nullable=False)
    alias: Mapped[str] = mapped_column(String(100), nullable=False)
    # Category given to new expenses it names (NULL: keep theirs)
    category: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class UserCategory(Base):
    """A category the user added (is_hidden False), or one they hid from every category list (a default like Smoking,
    or one of their own). Records keep their category either way."""

    __tablename__ = "user_categories"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_category"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)


class UpiId(Base):
    """One of the user's own UPI IDs (VPA) for the UPI tracker, and the account it is linked to."""

    __tablename__ = "upi_ids"
    __table_args__ = (UniqueConstraint("user_id", "vpa", name="uq_upi_id_user_vpa"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    vpa: Mapped[str] = mapped_column(String(100), nullable=False)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)


UPI_CREATED = "CREATED"
UPI_DUPLICATE = "DUPLICATE"
UPI_IGNORED = "IGNORED"
UPI_FAILED = "FAILED"


class UpiEvent(Base):
    """A message received by the UPI tracker and what became of it (an expense, money received, or why not)."""

    __tablename__ = "upi_events"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    upi_id: Mapped[int | None] = mapped_column(ForeignKey("upi_ids.id", ondelete="SET NULL"))
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local, index=True)
    # CREATED, DUPLICATE, IGNORED or FAILED
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300))
    # DEBIT (paid) or CREDIT (received)
    direction: Mapped[str | None] = mapped_column(String(6))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    counterparty: Mapped[str | None] = mapped_column(String(120))
    payee_key: Mapped[str | None] = mapped_column(String(120))
    # UPI reference number (RRN): the same payment forwarded twice is recorded once
    ref: Mapped[str | None] = mapped_column(String(40), index=True)
    txn_date: Mapped[date | None] = mapped_column(Date)
    raw_text: Mapped[str | None] = mapped_column(String(1000))
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expenses.id", ondelete="SET NULL"))
    fund_id: Mapped[int | None] = mapped_column(ForeignKey("funds.id", ondelete="SET NULL"))


class DataShare(Base):
    """owner shares all of their data with viewer (read-only)."""

    __tablename__ = "data_shares"
    __table_args__ = (UniqueConstraint("owner_id", "viewer_id", name="uq_share_owner_viewer"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False)
    viewer_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)

    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    viewer: Mapped[User] = relationship(foreign_keys=[viewer_id])


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="INFO")
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    ref_date: Mapped[date | None] = mapped_column(Date)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


class UsernameChangeRequest(Base):
    """Request to the admin to change a username while the user's own 15-day change lock is active."""

    __tablename__ = "username_change_requests"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    current_username: Mapped[str] = mapped_column(String(50), nullable=False)
    requested_username: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=STATUS_PENDING)
    admin_comment: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("app_users.id", ondelete="SET NULL"))

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class PasswordReset(Base):
    """A forgot-password request: a temporary password + one-time link sent by email."""

    __tablename__ = "password_resets"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    # Only hashes are stored: SHA-256 of the link token, bcrypt of the temporary password
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    temp_password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requested_ip: Mapped[str | None] = mapped_column(String(45))

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class DataBackup(Base):
    """A copy of data taken right before an admin deletes it, so it can be downloaded or restored."""

    __tablename__ = "data_backups"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # USER (account + all data), USER_DATA (expenses, funds, daily items), EXPENSE, FUND or DAILY_ITEM
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # Not foreign keys: the user may no longer exist
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # gzip-compressed JSON dump of every deleted row, keyed by table name
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)
    created_by: Mapped[str] = mapped_column(String(50), nullable=False)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime)
    restored_by: Mapped[str | None] = mapped_column(String(50))


# ---------- split bills ----------
SHARE_EQUAL = "EQUAL"
SHARE_PERCENT = "PERCENT"
SHARE_AMOUNT = "AMOUNT"


class SplitGroup(Base):
    """People who share bills. The creator (owner) is always a group admin and is the only one who can delete it."""

    __tablename__ = "split_groups"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    owner_id: Mapped[int] = mapped_column(ForeignKey("app_users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)

    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    members: Mapped[list["SplitMember"]] = relationship(back_populates="group", cascade="all, delete-orphan")
    bills: Mapped[list["SplitBill"]] = relationship(back_populates="group", cascade="all, delete-orphan")
    settlements: Mapped[list["SplitSettlement"]] = relationship(back_populates="group", cascade="all, delete-orphan")


class SplitMember(Base):
    """Someone in a split group: an app user, or a guest who isn't on the app and is only tracked by name.

    Rows are never deleted while the group exists: someone who leaves keeps their row (with `left_at`) so their
    bills, payments and balance stay intact, and re-adding them simply reactivates it.
    """

    __tablename__ = "split_members"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("split_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    # Set for app users; NULL for guests
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_users.id"), index=True)
    guest_name: Mapped[str | None] = mapped_column(String(100))
    # Group admins can add / remove members and make others admin
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    added_by: Mapped[int | None] = mapped_column(ForeignKey("app_users.id", ondelete="SET NULL"))
    joined_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)
    left_at: Mapped[datetime | None] = mapped_column(DateTime)

    group: Mapped[SplitGroup] = relationship(back_populates="members")
    user: Mapped[User | None] = relationship(foreign_keys=[user_id])

    @property
    def is_guest(self) -> bool:
        return self.guest_name is not None

    @property
    def active(self) -> bool:
        return self.left_at is None

    @property
    def name(self) -> str:
        return self.user.full_name if self.user else self.guest_name


class SplitBill(Base):
    """One shared purchase: `paid_by` paid the whole amount, and every share is what that person owes them."""

    __tablename__ = "split_bills"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("split_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    bill_date: Mapped[date] = mapped_column(Date, nullable=False)
    # The member (user or guest) who paid
    paid_by: Mapped[int] = mapped_column(ForeignKey("split_members.id"), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    # Only the creator can change or delete the bill
    created_by: Mapped[int] = mapped_column(ForeignKey("app_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    group: Mapped[SplitGroup] = relationship(back_populates="bills")
    payer: Mapped[SplitMember] = relationship(foreign_keys=[paid_by])
    creator: Mapped[User] = relationship(foreign_keys=[created_by])
    shares: Mapped[list["SplitShare"]] = relationship(
        back_populates="bill", cascade="all, delete-orphan", order_by="SplitShare.id"
    )


class SplitShare(Base):
    """A participant of a bill. AMOUNT and PERCENT shares are fixed; EQUAL shares divide whatever is left."""

    __tablename__ = "split_shares"
    __table_args__ = (UniqueConstraint("bill_id", "member_id", name="uq_split_share"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("split_bills.id", ondelete="CASCADE"), nullable=False, index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("split_members.id"), nullable=False, index=True)
    share_type: Mapped[str] = mapped_column(String(10), nullable=False, default=SHARE_EQUAL)
    # The fixed amount (AMOUNT) or percentage (PERCENT); unused for EQUAL
    share_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    # What this participant owes for the bill, recalculated whenever the bill or its participants change
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)

    bill: Mapped[SplitBill] = relationship(back_populates="shares")
    member: Mapped[SplitMember] = relationship(foreign_keys=[member_id])


class SplitSettlement(Base):
    """Money paid back between two members (from_member paid to_member)."""

    __tablename__ = "split_settlements"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("split_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    from_member: Mapped[int] = mapped_column(ForeignKey("split_members.id"), nullable=False)
    to_member: Mapped[int] = mapped_column(ForeignKey("split_members.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    settled_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[int] = mapped_column(ForeignKey("app_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)

    group: Mapped[SplitGroup] = relationship(back_populates="settlements")
    payer: Mapped[SplitMember] = relationship(foreign_keys=[from_member])
    payee: Mapped[SplitMember] = relationship(foreign_keys=[to_member])


class JobRun(Base):
    """A claimed run of a scheduled job. The unique key lets only one worker process run it per period."""

    __tablename__ = "job_runs"
    __table_args__ = (UniqueConstraint("job_name", "run_key", name="uq_job_run"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    job_name: Mapped[str] = mapped_column(String(50), nullable=False)
    # The period the run covers, e.g. "2026-10-30" for a daily job or "2026-10-30T09" for an hourly one
    run_key: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)


# ---------- subscriptions ----------
FREQ_WEEKLY = "WEEKLY"
FREQ_MONTHLY = "MONTHLY"
FREQ_QUARTERLY = "QUARTERLY"
FREQ_HALF_YEARLY = "HALF_YEARLY"
FREQ_YEARLY = "YEARLY"
FREQ_CUSTOM = "CUSTOM"

SUB_SOURCE_MANUAL = "MANUAL"
SUB_SOURCE_SMS = "SMS"
SUB_SOURCE_DETECTED = "DETECTED"


class Subscription(Base):
    """Something charged again and again (Netflix, gym, insurance, UPI AutoPay mandate...).

    `next_charge_date` is kept up to date by the subscription job: when it arrives a charge is recorded (and
    optionally logged as an expense) and it moves to the following date. Paused subscriptions are skipped.
    """

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Who charges it (Netflix, Airtel, LIC...) and how it is paid (GPAY, PHONEPE, PAYTM, CARD...)
    merchant: Mapped[str | None] = mapped_column(String(100))
    payment_method: Mapped[str | None] = mapped_column(String(20))
    # UPI ID or mandate reference, if known
    payment_ref: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="Subscriptions")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # WEEKLY, MONTHLY, QUARTERLY, HALF_YEARLY, YEARLY, or CUSTOM: every interval_count interval_unit
    frequency: Mapped[str] = mapped_column(String(12), nullable=False, default=FREQ_MONTHLY)
    interval_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # DAY, WEEK, MONTH or YEAR (CUSTOM only)
    interval_unit: Mapped[str] = mapped_column(String(5), nullable=False, default="MONTH")
    # First charge; later charges keep its day of the month (31st becomes the last day of shorter months)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    # No charges after this date (NULL: until cancelled)
    end_date: Mapped[date | None] = mapped_column(Date)
    # NULL once it has ended
    next_charge_date: Mapped[date | None] = mapped_column(Date, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime)
    # How many days before a charge to remind (0: no reminder)
    remind_days_before: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # The charge date the last reminder was sent for, so each charge is reminded once
    reminded_for: Mapped[date | None] = mapped_column(Date)
    # Record each charge as an expense on its date
    auto_log_expense: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # MANUAL, SMS (imported from a bank/UPI message) or DETECTED (found in the expense history)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default=SUB_SOURCE_MANUAL)
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    charges: Mapped[list["SubscriptionCharge"]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan", order_by="SubscriptionCharge.charge_date.desc()"
    )


class SubscriptionCharge(Base):
    """One charge of a subscription on its due date."""

    __tablename__ = "subscription_charges"
    __table_args__ = (UniqueConstraint("subscription_id", "charge_date", name="uq_subscription_charge"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    charge_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # The expense it was logged as, if any
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expenses.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)

    subscription: Mapped[Subscription] = relationship(back_populates="charges")
