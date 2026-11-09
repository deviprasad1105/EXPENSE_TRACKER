"""Import history: one batch per uploaded file, one record per row of it (what it became, or why it didn't)."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Identity, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ..timeutil import now_local

# Batch status
IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"
COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
FAILED = "FAILED"
UNDONE = "UNDONE"
PARTIALLY_UNDONE = "PARTIALLY_UNDONE"
PURGED = "PURGED"  # everything it created was removed (also records edited since)
REPLACED = "REPLACED"  # purged and replaced by a corrected file (replaced_by_id)
ROLLED_BACK = "ROLLED_BACK"  # stopped before it finished: what it had saved was removed again, nothing kept

# Record status
REC_IMPORTED = "IMPORTED"
REC_SKIPPED = "SKIPPED"  # not a transaction (a balance or total line)
REC_FAILED = "FAILED"  # couldn't be imported (imports before the whole-file check)
REC_UNDONE = "UNDONE"  # removed again by Undo, a purge or a rollback
REC_KEPT = "KEPT"  # Undo left it in place (edited since, reconciled, or in a locked period)

KIND_EXPENSE = "EXPENSE"
KIND_INCOME = "INCOME"
KIND_SPLIT = "SPLIT"
KIND_TRANSFER = "TRANSFER"  # money moved between two of the user's accounts
KIND_OPENING = "OPENING"  # an account's balance at the start of the records (sets its opening balance)


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    # SHA-256 of the uploaded file (empty for records typed in by hand): spots a file uploaded twice
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(25), nullable=False, default=IN_PROGRESS)
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_undone: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expense_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    income_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Ids of the accounts this import created (comma-separated): a full Undo removes the ones nothing else uses
    created_accounts: Mapped[str | None] = mapped_column(String(500))
    # How many rows the file has (checked as a whole when the import starts): the import finishes only with all of them
    rows_expected: Mapped[int | None] = mapped_column(Integer)
    # Purge & re-import: the earlier import this one replaces, and on that one the import that replaced it
    replaces_id: Mapped[int | None] = mapped_column(Integer)
    replaced_by_id: Mapped[int | None] = mapped_column(Integer)

    records: Mapped[list["ImportRecord"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", order_by="ImportRecord.row_no"
    )


class ImportRecord(Base):
    __tablename__ = "import_records"
    __table_args__ = (Index("ix_import_records_user_fp", "user_id", "fingerprint"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False)
    row_no: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    record_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Kind, date, amount and description (+ which occurrence in the file): finds the same row uploaded again
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    # What the row became. SET NULL: the user may delete the expense later; Undo then skips it
    expense_id: Mapped[int | None] = mapped_column(ForeignKey("expenses.id", ondelete="SET NULL"))
    fund_id: Mapped[int | None] = mapped_column(ForeignKey("funds.id", ondelete="SET NULL"))
    split_bill_id: Mapped[int | None] = mapped_column(ForeignKey("split_bills.id", ondelete="SET NULL"))
    transfer_id: Mapped[int | None] = mapped_column(ForeignKey("account_transfers.id", ondelete="SET NULL"))
    # An opening balance row: the account whose opening balance it added `amount` to (signed)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))

    batch: Mapped[ImportBatch] = relationship(back_populates="records")


# What a user chose in an import, used to fill in the same choices next time
CHOICE_CATEGORY = "CATEGORY"  # a category from a file → the app category it was put in
CHOICE_TREATMENT = "TREATMENT"  # a category → the account its rows were moved to ("id:<n>" / "new:<KIND>:<name>")
CHOICE_ACCOUNT = "ACCOUNT"  # an account name from a file → "id:<n>" or "none"


class ImportChoice(Base):
    __tablename__ = "import_choices"
    __table_args__ = (UniqueConstraint("user_id", "kind", "source_key", name="uq_import_choice"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    # The value from the file, normalised (any case, spacing)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=now_local)
