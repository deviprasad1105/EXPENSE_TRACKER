"""Double-entry accounting tables. Every user has their own books.

Oracle note: a composite UNIQUE key treats (5, NULL) as a duplicate of another (5, NULL), unlike SQLite and
PostgreSQL. So uniqueness that involves a nullable column (a ledger's system code, its split group) is enforced
in code, and each auto-posted voucher carries a single-column `source_key` instead of a composite key.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Identity, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..timeutil import now_local

# Up to ₹99,99,99,99,99,999.99: room for totals of many Numeric(12,2) amounts
AMOUNT = Numeric(15, 2)

VOUCHER_POSTED = "POSTED"
VOUCHER_REVERSED = "REVERSED"


class AccSettings(Base):
    """One row per user: their books exist once this row does."""

    __tablename__ = "acc_settings"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), unique=True, nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(120))
    # Place of business: decides CGST + SGST (same state) or IGST (other state) on invoices
    state: Mapped[str | None] = mapped_column(String(40))
    gstin: Mapped[str | None] = mapped_column(String(15))
    # 1 April of the first financial year (the year of the earliest transaction)
    books_start: Mapped[date] = mapped_column(Date, nullable=False)
    # Nothing dated on or before this can be posted, reversed, edited or deleted
    lock_date: Mapped[date | None] = mapped_column(Date)
    locked_by: Mapped[str | None] = mapped_column(String(50))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)


class AccLedger(Base):
    __tablename__ = "acc_ledgers"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_acc_ledger_name"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # One of chart.GROUPS
    group_code: Mapped[str] = mapped_column(String(30), nullable=False)
    # System ledgers the app posts to (CAPITAL, DRAWINGS, SUSPENSE, OUT_CGST…); unique per user, checked in code
    code: Mapped[str | None] = mapped_column(String(30))
    # Signed: debit balances positive, credit balances negative
    opening_balance: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Added back to profit in the cash flow statement (a non-cash expense)
    is_depreciation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The bank account / card / wallet / cash this ledger stands for
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), unique=True)
    # "Split – <group>": what others in a split group owe this user (or the other way round)
    split_group_id: Mapped[int | None] = mapped_column(ForeignKey("split_groups.id", ondelete="SET NULL"))
    gstin: Mapped[str | None] = mapped_column(String(15))
    state: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)


class Voucher(Base):
    __tablename__ = "vouchers"
    __table_args__ = (
        UniqueConstraint("user_id", "voucher_type", "fy", "voucher_no", name="uq_voucher_number"),
        Index("ix_voucher_source", "source_type", "source_id"),
        Index("ix_voucher_user_date", "user_id", "voucher_date"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False)
    voucher_type: Mapped[str] = mapped_column(String(10), nullable=False)
    # Numbered per type within each financial year (fy = the year it starts: 2025 is 2025-26)
    voucher_no: Mapped[int] = mapped_column(Integer, nullable=False)
    fy: Mapped[int] = mapped_column(Integer, nullable=False)
    voucher_date: Mapped[date] = mapped_column(Date, nullable=False)
    narration: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=VOUCHER_POSTED)
    # A reversal mirrors the lines of the voucher it reverses; both stay in the books
    is_reversal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reversal_of_id: Mapped[int | None] = mapped_column(ForeignKey("vouchers.id"))
    # Auto-posted from an expense, fund, transfer…: source_type + source_id, and a version that goes up each
    # time the source changes. source_key ("EXPENSE:12:u3:v2") is unique, so a retried request can't post twice.
    source_type: Mapped[str | None] = mapped_column(String(20))
    source_id: Mapped[int | None] = mapped_column(Integer)
    source_version: Mapped[int | None] = mapped_column(Integer)
    source_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    # GST invoices (Sales / Purchase)
    party_ledger_id: Mapped[int | None] = mapped_column(ForeignKey("acc_ledgers.id"))
    invoice_no: Mapped[str | None] = mapped_column(String(40))
    place_of_supply: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)

    lines: Mapped[list["VoucherLine"]] = relationship(
        back_populates="voucher", cascade="all, delete-orphan", order_by="VoucherLine.line_no"
    )


class VoucherLine(Base):
    __tablename__ = "voucher_lines"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    voucher_id: Mapped[int] = mapped_column(ForeignKey("vouchers.id", ondelete="CASCADE"), nullable=False, index=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("acc_ledgers.id"), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    debit: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False, default=0)
    credit: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False, default=0)
    # On the sales / purchase line of a GST invoice
    gst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    voucher: Mapped[Voucher] = relationship(back_populates="lines")
    ledger: Mapped[AccLedger] = relationship()


RULE_EXPENSE_CATEGORY = "EXPENSE_CATEGORY"
RULE_FUND_SOURCE = "FUND_SOURCE"


class PostingRule(Base):
    """Which ledger an expense category (or a fund's source) posts to."""

    __tablename__ = "acc_posting_rules"
    __table_args__ = (UniqueConstraint("user_id", "kind", "rule_key", name="uq_acc_posting_rule"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # Case-folded category / source name; `label` keeps how it was first written
    rule_key: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("acc_ledgers.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)


class StockValue(Base):
    """Opening and closing stock for a financial year, entered by hand (no item-level inventory)."""

    __tablename__ = "acc_stock"
    __table_args__ = (UniqueConstraint("user_id", "fy", name="uq_acc_stock"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False)
    fy: Mapped[int] = mapped_column(Integer, nullable=False)
    # Only stored for the first financial year: later years open with the previous year's closing stock
    opening_value: Mapped[Decimal | None] = mapped_column(AMOUNT)
    closing_value: Mapped[Decimal | None] = mapped_column(AMOUNT)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)


class BankImport(Base):
    __tablename__ = "bank_imports"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("acc_ledgers.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)


class BankStatementLine(Base):
    __tablename__ = "bank_statement_lines"
    __table_args__ = (UniqueConstraint("ledger_id", "fingerprint", name="uq_bank_line_fingerprint"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("acc_ledgers.id"), nullable=False, index=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("bank_imports.id", ondelete="CASCADE"), nullable=False)
    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(String(300))
    reference: Mapped[str | None] = mapped_column(String(60))
    # As the bank sees it: a withdrawal is a credit in our bank ledger, a deposit a debit
    withdrawal: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False, default=0)
    deposit: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False, default=0)
    balance: Mapped[Decimal | None] = mapped_column(AMOUNT)
    # Same file imported twice doesn't add the lines again
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    matched_line_id: Mapped[int | None] = mapped_column(ForeignKey("voucher_lines.id", ondelete="SET NULL"), unique=True)
    match_kind: Mapped[str | None] = mapped_column(String(10))
    matched_at: Mapped[datetime | None] = mapped_column(DateTime)
