from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- auth / users ----------
class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=100)
    manager_username: str | None = Field(default=None, description="The user this account reports to")


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


USERNAME_PATTERN = r"^[A-Za-z0-9_.-]+$"


class UserBrief(ORM):
    id: int
    username: str
    full_name: str
    avatar_updated_at: datetime | None = None


class UserOut(ORM):
    id: int
    username: str
    email: str
    full_name: str
    role: str
    status: str
    manager_id: int | None
    created_at: datetime
    approved_at: datetime | None
    username_changed_at: datetime | None
    avatar_updated_at: datetime | None
    show_tour: bool
    theme: str
    is_locked: bool = False
    locked_at: datetime | None = None
    locked_by: str | None = None
    lock_reason: str | None = None
    feature_expense: bool = True
    feature_split: bool = False
    feature_subscription: bool = False
    in_split_group: bool = False
    ask_account: bool = True
    background_updated_at: datetime | None = None


class AccessibleUser(BaseModel):
    id: int
    username: str
    full_name: str
    access: str
    writable: bool
    avatar_updated_at: datetime | None = None


class AdminUserUpdate(BaseModel):
    role: str | None = Field(default=None, pattern="^(ADMIN|USER)$")
    status: str | None = Field(default=None, pattern="^(PENDING|APPROVED|REJECTED)$")
    manager_id: int | None = None
    clear_manager: bool = False
    full_name: str | None = None
    email: EmailStr | None = None
    new_password: str | None = Field(default=None, min_length=8, max_length=100)
    feature_expense: bool | None = None
    feature_split: bool | None = None
    feature_subscription: bool | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=100)


class ForgotPasswordIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    temp_password: str = Field(min_length=1, max_length=100)
    new_password: str = Field(min_length=8, max_length=100)


# ---------- profile ----------
class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    email: EmailStr | None = None


class PreferencesUpdate(BaseModel):
    show_tour: bool | None = None
    theme: str | None = Field(default=None, pattern="^(light|dark)$")
    ask_account: bool | None = None


class UsernameChange(BaseModel):
    new_username: str = Field(min_length=3, max_length=50, pattern=USERNAME_PATTERN)
    password: str


class UsernameRequestIn(BaseModel):
    new_username: str = Field(min_length=3, max_length=50, pattern=USERNAME_PATTERN)
    reason: str | None = Field(default=None, max_length=500)


class UsernameRequestOut(ORM):
    id: int
    user_id: int
    current_username: str
    requested_username: str
    reason: str | None
    status: str
    admin_comment: str | None
    created_at: datetime
    decided_at: datetime | None


class AdminReason(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class BackupOut(ORM):
    id: int
    kind: str
    user_id: int
    username: str
    summary: str
    reason: str | None
    row_count: int
    size_bytes: int
    created_at: datetime
    created_by: str
    restored_at: datetime | None
    restored_by: str | None


class UsernameDecision(BaseModel):
    comment: str | None = Field(default=None, max_length=500)


class ProfileOut(BaseModel):
    user: UserOut
    manager: UserBrief | None
    can_change_username: bool
    username_locked_until: datetime | None
    pending_username_request: UsernameRequestOut | None


# ---------- daily items ----------
class DailyItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: str = Field(default="General", max_length=50)
    default_amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    default_quantity: int = Field(default=1, ge=1)
    is_constant: bool = False
    is_active: bool = True
    sort_order: int = 0


class DailyItemOut(ORM, DailyItemIn):
    id: int
    user_id: int


# ---------- expenses ----------
class ExpenseIn(BaseModel):
    expense_date: date
    item_name: str = Field(min_length=1, max_length=100)
    category: str = Field(default="General", max_length=50)
    quantity: int = Field(default=1, ge=1)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    note: str | None = Field(default=None, max_length=500)
    is_extra: bool = False
    daily_item_id: int | None = None
    account_id: int | None = None


class ExpenseUpdate(BaseModel):
    expense_date: date | None = None
    item_name: str | None = Field(default=None, min_length=1, max_length=100)
    category: str | None = Field(default=None, max_length=50)
    quantity: int | None = Field(default=None, ge=1)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    note: str | None = Field(default=None, max_length=500)
    account_id: int | None = None


class DailyEntryIn(BaseModel):
    """Confirm & save a whole day's expenses in one go."""

    expense_date: date
    # Paid from this account (default: the primary account), unless an item names its own
    account_id: int | None = None
    items: list[ExpenseIn] = Field(min_length=1)


class ExpenseOut(ORM):
    id: int
    user_id: int
    expense_date: date
    item_name: str
    category: str
    quantity: int
    amount: Decimal
    note: str | None
    is_extra: bool
    daily_item_id: int | None
    account_id: int | None = None
    created_at: datetime
    updated_at: datetime | None
    locked: bool = False
    editable: bool = False
    lock_at: datetime | None = None


# ---------- funds ----------
class FundIn(BaseModel):
    fund_date: date
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    source: str = Field(default="Income", max_length=100)
    note: str | None = Field(default=None, max_length=500)
    account_id: int | None = None


class FundOut(ORM, FundIn):
    id: int
    user_id: int
    created_at: datetime


# ---------- sharing ----------
class ShareIn(BaseModel):
    viewer_username: str


class ShareOut(BaseModel):
    id: int
    owner: UserBrief
    viewer: UserBrief
    created_at: datetime


# ---------- notifications ----------
class NotificationOut(ORM):
    id: int
    kind: str
    message: str
    ref_date: date | None
    is_read: bool
    created_at: datetime


# ---------- split bills ----------
class SplitGroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class SplitGroupCreate(SplitGroupIn):
    members: list[str] = Field(default_factory=list, description="'@username' for app users, a plain name for guests")


class SplitShareIn(BaseModel):
    member_id: int
    share_type: str = Field(default="EQUAL", pattern="^(EQUAL|PERCENT|AMOUNT)$")
    share_value: Decimal | None = Field(default=None, ge=0)


class SplitBillIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    total_amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    bill_date: date
    paid_by: int = Field(description="Member id of whoever paid")
    note: str | None = Field(default=None, max_length=500)
    shares: list[SplitShareIn] = Field(min_length=1)


class SplitMemberAdd(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="'@username' for an app user, a plain name for a guest")
    include_bill_ids: list[int] = Field(default_factory=list, description="Existing bills to add them to")


class SplitMemberRemove(BaseModel):
    exclude_bill_ids: list[int] = Field(default_factory=list, description="Bills to take them out of")
    dry_run: bool = Field(default=False, description="Only return the summary, change nothing")


class SplitMemberUpdate(BaseModel):
    is_admin: bool


class SplitSettlementIn(BaseModel):
    from_member_id: int
    to_member_id: int
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    settled_on: date
    note: str | None = Field(default=None, max_length=500)


# ---------- subscriptions ----------
FREQUENCY_PATTERN = "^(WEEKLY|MONTHLY|QUARTERLY|HALF_YEARLY|YEARLY|CUSTOM)$"
PAYMENT_METHOD_PATTERN = "^(GPAY|PHONEPE|PAYTM|AMAZONPAY|CRED|BHIM|UPI|CARD|NETBANKING|CASH|OTHER)$"


class SubscriptionIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    merchant: str | None = Field(default=None, max_length=100)
    payment_method: str | None = Field(default=None, pattern=PAYMENT_METHOD_PATTERN)
    payment_ref: str | None = Field(default=None, max_length=100)
    category: str = Field(default="Subscriptions", min_length=1, max_length=50)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    frequency: str = Field(default="MONTHLY", pattern=FREQUENCY_PATTERN)
    # CUSTOM only: charged every interval_count interval_unit (e.g. every 45 DAY, every 2 MONTH)
    interval_count: int = Field(default=1, ge=1, le=3650)
    interval_unit: str = Field(default="MONTH", pattern="^(DAY|WEEK|MONTH|YEAR)$")
    start_date: date
    end_date: date | None = None
    is_active: bool = True
    remind_days_before: int = Field(default=1, ge=0, le=30)
    auto_log_expense: bool = True
    source: str = Field(default="MANUAL", pattern="^(MANUAL|SMS|DETECTED)$")
    note: str | None = Field(default=None, max_length=500)


class SubscriptionOut(ORM):
    id: int
    user_id: int
    name: str
    merchant: str | None
    payment_method: str | None
    payment_ref: str | None
    category: str
    amount: Decimal
    frequency: str
    interval_count: int
    interval_unit: str
    start_date: date
    end_date: date | None
    next_charge_date: date | None
    is_active: bool
    paused_at: datetime | None
    remind_days_before: int
    auto_log_expense: bool
    source: str
    note: str | None
    created_at: datetime
    updated_at: datetime | None
    monthly_cost: Decimal = Decimal(0)
    days_until: int | None = None
    total_charged: Decimal = Decimal(0)
    charge_count: int = 0


class SubscriptionToggle(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    is_active: bool


class SubscriptionChargeOut(ORM):
    id: int
    charge_date: date
    amount: Decimal
    expense_id: int | None
    created_at: datetime


class SubscriptionSmsIn(BaseModel):
    text: str = Field(min_length=10, max_length=50_000)


class SubscriptionSuggestion(BaseModel):
    name: str
    merchant: str | None
    amount: Decimal
    frequency: str
    interval_count: int
    interval_unit: str
    start_date: date
    payment_method: str | None
    payment_ref: str | None
    category: str = "Subscriptions"
    source: str
    auto_log_expense: bool
    confidence: str
    already_tracked: bool
    evidence: list[str]


class SubscriptionBulkIn(BaseModel):
    items: list[SubscriptionIn] = Field(min_length=1, max_length=100)


# ---------- bank accounts ----------
class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    bank: str | None = Field(default=None, max_length=60)
    kind: str = Field(default="SAVINGS", pattern="^(SAVINGS|CURRENT|SALARY|CREDIT_CARD|WALLET|CASH)$")
    last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    opening_balance: Decimal = Field(default=Decimal(0), max_digits=12, decimal_places=2)
    is_primary: bool = False
    is_active: bool = True


class AccountOut(ORM, AccountIn):
    id: int
    created_at: datetime
    received: Decimal = Decimal(0)
    spent: Decimal = Decimal(0)
    transferred_in: Decimal = Decimal(0)
    transferred_out: Decimal = Decimal(0)
    balance: Decimal = Decimal(0)
    spent_this_month: Decimal = Decimal(0)


class TransferIn(BaseModel):
    from_account_id: int
    to_account_id: int
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    transfer_date: date
    note: str | None = Field(default=None, max_length=200)


class TransferOut(ORM, TransferIn):
    id: int
    created_at: datetime


# ---------- budgets ----------
class BudgetIn(BaseModel):
    scope: str = Field(default="OVERALL", pattern="^(OVERALL|CATEGORY|ITEM)$")
    target: str | None = Field(default=None, max_length=100)
    monthly_limit: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    alert_percent: int = Field(default=90, ge=10, le=100)
    is_active: bool = True


class BudgetOut(ORM):
    id: int
    scope: str
    target: str
    monthly_limit: Decimal
    alert_percent: int
    is_active: bool
    created_at: datetime
    spent: Decimal = Decimal(0)
    percent: float = 0
    # OK, WARN (reached alert_percent) or OVER (100% or more)
    level: str = "OK"
