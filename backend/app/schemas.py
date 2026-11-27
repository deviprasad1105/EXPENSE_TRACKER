from datetime import date, datetime
from decimal import Decimal

from typing import Literal

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


class LoginOut(BaseModel):
    """A sign-in token, or (two-step sign-in on) a short-lived ticket to send with the authenticator code."""

    access_token: str | None = None
    token_type: str = "bearer"
    mfa_required: bool = False
    mfa_token: str | None = None
    # Signed in with a temporary password: send password_token with the new password to /login/new-password
    password_change_required: bool = False
    password_token: str | None = None


class MfaLoginIn(BaseModel):
    mfa_token: str = Field(min_length=10, max_length=1000)
    code: str = Field(min_length=1, max_length=20, description="6-digit code from the app, or a recovery code")


class MfaStatusOut(BaseModel):
    enabled: bool
    enabled_at: datetime | None = None
    recovery_codes_left: int = 0


class MfaSetupIn(BaseModel):
    password: str = Field(min_length=1, max_length=100)


class MfaSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class MfaEnableIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)


class MfaConfirmIn(BaseModel):
    """Turning two-step sign-in off, or making new recovery codes: the password and a current code."""

    password: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=20)


class MfaRecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


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
    feature_accounting: bool = False
    feature_upi: bool = False
    upi_enabled: bool = False
    in_split_group: bool = False
    ask_account: bool = True
    background_updated_at: datetime | None = None
    email_verified: bool = True
    mfa_enabled: bool = False


class ManagedUserOut(UserOut):
    """A user as the admin or their org admin sees them. Never returned to the user themselves, so a member
    doesn't learn which organisation admin can see their data."""

    org_admin_id: int | None = None
    org_locked: bool = False


class OrgMemberCreate(BaseModel):
    """An org admin adding someone to their organisation. The account is approved straight away."""

    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=100)


class AccessibleUser(BaseModel):
    id: int
    username: str
    full_name: str
    access: str
    writable: bool
    avatar_updated_at: datetime | None = None


class AdminUserUpdate(BaseModel):
    role: str | None = Field(default=None, pattern="^(ADMIN|ORG_ADMIN|USER)$")
    status: str | None = Field(default=None, pattern="^(PENDING|APPROVED|REJECTED)$")
    manager_id: int | None = None
    clear_manager: bool = False
    # Organisation membership: the ORG_ADMIN this user belongs to
    org_admin_id: int | None = None
    clear_org: bool = False
    full_name: str | None = None
    email: EmailStr | None = None
    new_password: str | None = Field(default=None, min_length=8, max_length=100)
    feature_expense: bool | None = None
    feature_split: bool | None = None
    feature_subscription: bool | None = None
    feature_accounting: bool | None = None
    feature_upi: bool | None = None


OTP_FIELD = Field(default="", max_length=10, description="The code emailed by POST /api/profile/otp")


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=100)
    otp: str = OTP_FIELD


class OtpRequestIn(BaseModel):
    purpose: Literal["PASSWORD", "USERNAME"]


class OtpSentOut(BaseModel):
    message: str
    expires_at: datetime | None = None
    # Seconds the code stays valid from now, so the app can count down without depending on time zones
    expires_in_seconds: int | None = None
    email_sent: bool = True


class VerifyEmailIn(BaseModel):
    username: str = Field(min_length=1, max_length=120, description="Username or email address")
    code: str = Field(min_length=1, max_length=10)


class ResendSignupOtpIn(BaseModel):
    username: str = Field(min_length=1, max_length=120, description="Username or email address")


class RecoverCodeIn(BaseModel):
    """The owner of a deleted account asks for a code to restore it, or to erase its data for good."""

    email: EmailStr
    action: Literal["RESTORE", "PURGE"]


class RecoverConfirmIn(RecoverCodeIn):
    code: str = Field(min_length=1, max_length=10)


class SignupChoiceIn(BaseModel):
    """Someone who just signed up and confirmed an email that a deleted account used: bring that account back
    (the new sign-up is cancelled) or erase its data (the new sign-up goes on)."""

    ticket: str = Field(min_length=1, max_length=2000)
    action: Literal["RESTORE", "PURGE"]


class OtpSettings(BaseModel):
    signup_minutes: int = Field(ge=1, le=60)
    account_minutes: int = Field(ge=1, le=60)


class ForgotPasswordIn(BaseModel):
    username: str = Field(min_length=1, max_length=120, description="Username or email address")


class NewPasswordIn(BaseModel):
    """The new password, chosen after signing in with a temporary password."""

    password_token: str = Field(min_length=10, max_length=1000)
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
    otp: str = OTP_FIELD


class UsernameRequestIn(BaseModel):
    new_username: str = Field(min_length=3, max_length=50, pattern=USERNAME_PATTERN)
    reason: str | None = Field(default=None, max_length=500)
    otp: str = OTP_FIELD


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


class AccountDeletionIn(BaseModel):
    password: str = Field(min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=500)


class AccountDeletionOut(ORM):
    id: int
    user_id: int | None
    username: str
    full_name: str
    reason: str | None
    status: str
    admin_comment: str | None
    created_at: datetime
    decided_at: datetime | None
    decided_by_username: str | None
    backup_id: int | None


class ProfileOut(BaseModel):
    user: UserOut
    manager: UserBrief | None
    can_change_username: bool
    username_locked_until: datetime | None
    pending_username_request: UsernameRequestOut | None
    pending_deletion_request: AccountDeletionOut | None = None


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
    # Whether the viewer can change it (the owner and admin, at any time)
    editable: bool = False
    raw_name: str | None = None
    alias_id: int | None = None
    # Which payee it is (for "always use this category for it")
    payee_key: str | None = None
    origin: str | None = None


# ---------- funds ----------
class FundIn(BaseModel):
    fund_date: date
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    source: str = Field(default="Income", max_length=100)
    # The kind of income; empty: the matching alias's income category, else "Money received"
    category: str | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=500)
    account_id: int | None = None


class FundUpdate(BaseModel):
    source: str | None = Field(default=None, min_length=1, max_length=100)
    category: str | None = Field(default=None, min_length=1, max_length=50)
    note: str | None = Field(default=None, max_length=500)


class FundOut(ORM, FundIn):
    id: int
    user_id: int
    created_at: datetime
    raw_name: str | None = None
    alias_id: int | None = None
    payee_key: str | None = None
    origin: str | None = None
    # Whether the viewer can change it (the owner and admin, at any time, as for expenses)
    editable: bool = False


# ---------- transaction aliases ----------
class AliasIn(BaseModel):
    # A payee key from the detected groups (a UPI ID, or the words of the description), or words to look for
    match_key: str = Field(min_length=2, max_length=120)
    alias: str = Field(min_length=1, max_length=100)
    category: str | None = Field(default=None, max_length=50)
    # Category given to money received it names
    income_category: str | None = Field(default=None, max_length=50)
    # Existing records to rename now (from the preview); empty leaves old records as they are
    expense_ids: list[int] = Field(default_factory=list, max_length=20000)
    fund_ids: list[int] = Field(default_factory=list, max_length=20000)
    # Rename every existing record it matches (the import review's "also rename old records"), instead of the ids
    apply_all_existing: bool = False
    # Give the renamed old records the alias's category / income category too (new ones always get it)
    set_category_existing: bool = False


class AliasUpdate(BaseModel):
    alias: str | None = Field(default=None, min_length=1, max_length=100)
    category: str | None = Field(default=None, max_length=50)
    clear_category: bool = False
    income_category: str | None = Field(default=None, max_length=50)
    clear_income_category: bool = False


class AliasApply(BaseModel):
    expense_ids: list[int] = Field(default_factory=list, max_length=20000)
    fund_ids: list[int] = Field(default_factory=list, max_length=20000)
    set_category_existing: bool = False


class AliasOut(ORM):
    id: int
    match_key: str
    alias: str
    category: str | None
    income_category: str | None = None
    created_at: datetime
    updated_at: datetime | None
    records: int = 0
    unnamed: int = 0


class AliasRecord(BaseModel):
    """An expense (kind EXPENSE) or money received (kind FUND) shown in the alias preview."""

    kind: str
    id: int
    date: date
    name: str
    original: str | None
    amount: Decimal
    category: str | None = None
    origin: str | None = None


class AliasGroup(BaseModel):
    """Records with the same payee key and no alias yet: a payee the user might want to name."""

    match_key: str
    names: list[str]
    suggestion: str
    expense_count: int
    expense_total: Decimal
    fund_count: int
    fund_total: Decimal
    last_date: date
    # An alias with this key already exists: these are old records it hasn't renamed
    alias_id: int | None = None
    alias: str | None = None


class AliasChangeOut(BaseModel):
    alias: AliasOut | None = None
    renamed: int = 0
    skipped_locked: int = 0


# ---------- UPI tracker ----------
class UpiIdIn(BaseModel):
    vpa: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}@[A-Za-z][A-Za-z0-9]{1,30}$")
    account_id: int | None = None
    is_active: bool = True


class UpiIdOut(ORM):
    id: int
    vpa: str
    account_id: int | None
    is_active: bool
    created_at: datetime


class UpiSettingsOut(BaseModel):
    enabled: bool
    has_token: bool
    token_hint: str | None
    upi_ids: list[UpiIdOut]


class UpiSettingsIn(BaseModel):
    enabled: bool


class UpiTokenOut(BaseModel):
    token: str
    hook_path: str


class UpiMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class UpiEventOut(ORM):
    id: int
    received_at: datetime
    status: str
    reason: str | None
    direction: str | None
    amount: Decimal | None
    counterparty: str | None
    payee_key: str | None
    ref: str | None
    txn_date: date | None
    raw_text: str | None
    expense_id: int | None
    fund_id: int | None
    upi_id: int | None
    name: str | None = None
    alias_id: int | None = None


class UpiDay(BaseModel):
    date: date
    paid: Decimal
    received: Decimal
    count: int


class UpiActivityOut(BaseModel):
    events: list[UpiEventOut]
    days: list[UpiDay]


# ---------- credit cards ----------
class CardSettingsIn(BaseModel):
    credit_limit: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    statement_day: int | None = Field(default=None, ge=1, le=31)
    due_day: int | None = Field(default=None, ge=1, le=31)
    remind_days: int = Field(default=3, ge=0, le=15)
    remind_on: bool = True


class CardBillIn(BaseModel):
    due_date: date
    total_due: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    min_due: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    statement_date: date | None = None


class CardBillOut(BaseModel):
    id: int
    account_id: int
    due_date: date
    statement_date: date | None
    total_due: Decimal
    min_due: Decimal | None
    source: str
    paid_at: datetime | None
    paid: bool
    paid_amount: Decimal
    days_left: int
    status: str


class CardOut(BaseModel):
    id: int
    name: str
    bank: str | None
    last4: str | None
    is_active: bool
    owed: Decimal
    spent_this_month: Decimal
    credit_limit: Decimal | None
    available: Decimal | None
    used_percent: float | None
    statement_day: int | None
    due_day: int | None
    remind_days: int
    remind_on: bool
    bill: CardBillOut | None


class CardMarkPaidIn(BaseModel):
    paid: bool = True


# ---------- investments ----------
HOLDING_KINDS = ("MUTUAL_FUND", "STOCK", "ETF", "CRYPTO", "GOLD", "BOND", "DEPOSIT", "OTHER")


class HoldingIn(BaseModel):
    kind: Literal[HOLDING_KINDS]  # type: ignore[valid-type]
    name: str = Field(min_length=1, max_length=120)
    platform: str | None = Field(default=None, max_length=60)
    symbol: str | None = Field(default=None, max_length=30)
    units: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=6)
    invested: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    current_value: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    valued_on: date | None = None
    note: str | None = Field(default=None, max_length=300)
    is_active: bool = True
    # Part of another line (e.g. a stock under its broker's total): shown under it, not counted in the totals
    parent_id: int | None = None


class HoldingOut(ORM):
    id: int
    kind: str
    name: str
    platform: str | None
    symbol: str | None
    units: Decimal | None
    invested: Decimal | None
    current_value: Decimal | None
    valued_on: date | None
    note: str | None
    is_active: bool
    parent_id: int | None
    # Worked out only when the figures behind them are known
    avg_cost: Decimal | None = None
    price: Decimal | None = None
    gain: Decimal | None = None
    gain_percent: float | None = None


class PendingInvestment(BaseModel):
    """A payment that looks like buying an investment, not yet added to the Investments page."""

    source: str  # EXPENSE or TRANSFER
    id: int
    date: date
    amount: Decimal
    label: str
    account: str | None


class PendingInvestmentsOut(BaseModel):
    since: date
    items: list[PendingInvestment]


class InvestActionIn(BaseModel):
    """ADD it to holding_id (with the units bought, if known), NEW holding, or SKIP (not an investment)."""

    action: Literal["ADD", "NEW", "SKIP"]
    holding_id: int | None = None
    units: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=6)
    holding: HoldingIn | None = None


class InvestActionOut(BaseModel):
    holding: HoldingOut | None


class HoldingGroup(BaseModel):
    kind: str
    count: int
    invested: Decimal
    current_value: Decimal
    gain: Decimal | None
    gain_percent: float | None


class InvestmentsOut(BaseModel):
    holdings: list[HoldingOut]
    groups: list[HoldingGroup]
    invested: Decimal
    current_value: Decimal
    # Over holdings with both figures known
    gain: Decimal | None
    gain_percent: float | None
    # Holdings without a current value (left out of the value total)
    unvalued: int
    # Balance of the user's Investment-type accounts, to compare with the holdings' value
    accounts_balance: Decimal | None
    accounts: list[str]


# ---------- auto-tracking (bank SMS / notifications) ----------
class AutoTrackSettingsIn(BaseModel):
    enabled: bool
    mode: Literal["REVIEW", "AUTO"] = "REVIEW"
    since: date | None = None


class AutoTrackSettingsOut(BaseModel):
    enabled: bool
    mode: str
    since: date | None
    device_linked: bool
    device_hint: str | None
    pending: int = 0
    failed: int = 0
    last_received_at: datetime | None = None


class DeviceTokenOut(BaseModel):
    token: str


class BankMessageIn(BaseModel):
    sender: str | None = Field(default=None, max_length=80)
    body: str = Field(min_length=1, max_length=2000)
    # When the phone received it: milliseconds since 1970 (what Android gives), or a date-time
    received_ms: int | None = None
    received_at: datetime | None = None
    source: Literal["SMS", "NOTIFICATION", "PASTE"] = "PASTE"


class BankMessagesIn(BaseModel):
    messages: list[BankMessageIn] = Field(min_length=1, max_length=500)


class BankMessageOut(ORM):
    id: int
    received_at: datetime
    source: str
    sender: str | None
    body: str
    status: str
    reason: str | None
    kind: str | None
    direction: str | None
    channel: str | None
    amount: Decimal | None
    bank: str | None
    last4: str | None
    party: str | None
    ref: str | None
    balance: Decimal | None
    txn_date: date | None
    due_date: date | None
    min_due: Decimal | None
    suggestion: str | None
    name: str | None
    category: str | None
    account_id: int | None
    to_account_id: int | None
    expense_id: int | None
    fund_id: int | None
    transfer_id: int | None


class BankBatchOut(BaseModel):
    results: list[BankMessageOut]
    # How many of each status this batch produced (messages already received before count as "SEEN")
    counts: dict[str, int]


class BankAcceptIn(BaseModel):
    suggestion: Literal["EXPENSE", "INCOME", "TRANSFER"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    category: str | None = Field(default=None, max_length=50)
    account_id: int | None = None
    to_account_id: int | None = None
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    txn_date: date | None = None


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


# ---------- bills ----------
BILL_KIND_PATTERN = "^(ELECTRICITY|MOBILE|BROADBAND|DTH|GAS|WATER|RENT|INSURANCE|EMI|SCHOOL|TAX|MAINTENANCE|OTHER)$"


class BillIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    biller: str | None = Field(default=None, max_length=100)
    kind: str = Field(default="OTHER", pattern=BILL_KIND_PATTERN)
    category: str = Field(default="Bills", min_length=1, max_length=50)
    # Leave empty when it changes every time
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    frequency: str = Field(default="MONTHLY", pattern=FREQUENCY_PATTERN)
    interval_count: int = Field(default=1, ge=1, le=3650)
    interval_unit: str = Field(default="MONTH", pattern="^(DAY|WEEK|MONTH|YEAR)$")
    # The next bill's due date; later ones fall on the same day of the month
    due_date: date
    end_date: date | None = None
    is_active: bool = True
    account_id: int | None = None
    remind_days_before: int = Field(default=3, ge=0, le=30)
    note: str | None = Field(default=None, max_length=500)


class BillMatch(BaseModel):
    """An expense that looks like the payment of a bill."""

    expense_id: int
    expense_date: date
    item_name: str
    amount: Decimal
    account_id: int | None = None


class BillOut(ORM):
    id: int
    user_id: int
    name: str
    biller: str | None
    kind: str
    category: str
    amount: Decimal | None
    frequency: str
    interval_count: int
    interval_unit: str
    start_date: date
    end_date: date | None
    next_due_date: date | None
    is_active: bool
    account_id: int | None
    remind_days_before: int
    note: str | None
    created_at: datetime
    days_until: int | None = None
    # UPCOMING, DUE_SOON (within the reminder days), OVERDUE, PAUSED or DONE (no more bills)
    status: str = "UPCOMING"
    last_paid_on: date | None = None
    last_amount: Decimal | None = None
    # An expense that looks like the payment of the bill now due, to confirm with one click
    match: BillMatch | None = None


class BillPayIn(BaseModel):
    """Mark the bill now due as paid: link an expense already recorded, or log a new one."""

    expense_id: int | None = None
    amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    paid_on: date | None = None
    log_expense: bool = True
    account_id: int | None = None


class BillPaymentOut(ORM):
    id: int
    due_date: date
    paid_on: date | None
    amount: Decimal | None
    expense_id: int | None
    made_expense: bool
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
    kind: str = Field(default="SAVINGS", pattern="^(SAVINGS|CURRENT|SALARY|CREDIT_CARD|WALLET|CASH|INVESTMENT|RECEIVABLE)$")
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
