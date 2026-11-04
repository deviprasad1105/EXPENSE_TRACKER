from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

GSTIN_PATTERN = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
Amount = Field(default=Decimal(0), ge=0, max_digits=15, decimal_places=2)


class FinancialYear(BaseModel):
    fy: int
    label: str
    start: date
    end: date


class SettingsOut(BaseModel):
    user_id: int
    business_name: str | None
    state: str | None
    gstin: str | None
    books_start: date
    lock_date: date | None
    locked_by: str | None
    locked_at: datetime | None
    synced_at: datetime | None
    current_fy: int
    years: list[FinancialYear]
    writable: bool


class SettingsIn(BaseModel):
    business_name: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=40)
    gstin: str | None = Field(default=None, pattern=GSTIN_PATTERN)
    lock_date: date | None = None
    # lock_date None means "leave it"; clear_lock removes the lock
    clear_lock: bool = False


class LedgerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    group_code: str = Field(max_length=30)
    opening_balance: Decimal = Amount
    opening_side: str = Field(default="DR", pattern="^(DR|CR)$")
    is_active: bool = True
    is_depreciation: bool = False
    gstin: str | None = Field(default=None, pattern=GSTIN_PATTERN)
    state: str | None = Field(default=None, max_length=40)


class LedgerOut(BaseModel):
    id: int
    name: str
    group_code: str
    group_name: str
    code: str | None
    system: bool
    opening_balance: Decimal
    opening_side: str
    balance: Decimal
    balance_side: str
    is_active: bool
    is_depreciation: bool
    account_id: int | None
    split_group_id: int | None
    gstin: str | None
    state: str | None
    used: bool


class VoucherLineIn(BaseModel):
    ledger_id: int
    debit: Decimal = Amount
    credit: Decimal = Amount


class VoucherIn(BaseModel):
    voucher_type: str = Field(pattern="^(PAYMENT|RECEIPT|CONTRA|JOURNAL|SALES|PURCHASE)$")
    voucher_date: date
    narration: str | None = Field(default=None, max_length=500)
    lines: list[VoucherLineIn] = Field(min_length=2, max_length=100)


class ReverseIn(BaseModel):
    narration: str | None = Field(default=None, max_length=500)


class VoucherLineOut(BaseModel):
    id: int
    ledger_id: int
    ledger_name: str
    debit: Decimal
    credit: Decimal
    gst_rate: Decimal | None = None


class VoucherOut(BaseModel):
    id: int
    voucher_type: str
    voucher_no: int
    fy: int
    voucher_date: date
    narration: str | None
    status: str
    is_reversal: bool
    reversal_of_id: int | None
    reversed_by_id: int | None = None
    source_type: str | None
    source_id: int | None
    source_label: str | None
    party_ledger_id: int | None = None
    invoice_no: str | None = None
    place_of_supply: str | None = None
    total: Decimal
    created_at: datetime
    lines: list[VoucherLineOut]


class VoucherPage(BaseModel):
    items: list[VoucherOut]
    total: int
    page: int
    size: int


class RuleOut(BaseModel):
    id: int
    kind: str
    label: str
    ledger_id: int
    ledger_name: str
    group_code: str
    group_name: str
    nature: str
    usage: int


class RuleUpdate(BaseModel):
    ledger_id: int


class SyncOut(BaseModel):
    posted: int
    reposted: int
    reversed: int
    unchanged: int
    locked: list[str]
