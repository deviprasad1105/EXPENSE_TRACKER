from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .parse import Mapping

Kind = Literal["EXPENSE", "INCOME", "SPLIT"]


class TableOut(BaseModel):
    name: str
    source: str
    rows: list[list[str]]
    truncated: bool = False


class ExtractOut(BaseModel):
    filename: str
    file_type: str
    file_hash: str
    tables: list[TableOut]
    table_index: int
    guess: Mapping
    # The same file was imported before (and not undone): when, and how many rows
    already_imported: dict | None = None


class ImportOptions(BaseModel):
    # primary: the primary account (the app's rule for new records); none: no account; fixed: account_id for every
    # row; column: each row's account cell, through account_map
    account_mode: Literal["primary", "none", "fixed", "column"] = "primary"
    account_id: int | None = None
    # Lower-cased account cell → account id, or null to leave those rows without an account ("" = blank cells)
    account_map: dict[str, int | None] = Field(default_factory=dict)
    # Lower-cased category from the file → category to use, existing or new ("" = rows without a category)
    category_map: dict[str, str] = Field(default_factory=dict)
    # The split group that "paid for others" parts go to; null = a group named "Imported split bills"
    split_group_id: int | None = None


class RecordIn(BaseModel):
    row_no: int = Field(ge=0)
    kind: Kind | None = None
    date: Date | None = None
    description: str = Field(default="", max_length=300)
    category: str | None = Field(default=None, max_length=100)
    amount: Decimal | None = None
    my_share: Decimal | None = None
    split_with: list[str] = Field(default_factory=list, max_length=50)
    account: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=500)
    quantity: int = Field(default=1, ge=1, le=100_000)
    include: bool = True
    # Messages from reading the file, used to explain a value that is still missing
    parse_errors: list[str] = Field(default_factory=list, max_length=20)
    parse_warnings: list[str] = Field(default_factory=list, max_length=20)
    # Given by /preview or /check for the whole file and sent back with each chunk, so identical rows split over
    # chunks keep their numbering (#1, #2…)
    fingerprint: str | None = Field(default=None, max_length=64)


class RecordOut(RecordIn):
    status: str
    problems: list[str] = []
    warnings: list[str] = []
    can_import: bool
    default_include: bool
    category_final: str | None = None
    account_id: int | None = None


class PreviewIn(BaseModel):
    rows: list[list[str]] = Field(max_length=25_000)
    mapping: Mapping
    options: ImportOptions = Field(default_factory=ImportOptions)


class CheckIn(BaseModel):
    records: list[RecordIn]
    options: ImportOptions = Field(default_factory=ImportOptions)


class CheckOut(BaseModel):
    records: list[RecordOut]
    ignored_rows: int = 0
    summary: dict
    categories: list[str]
    category_values: list[dict]
    account_values: list[dict]
    accounts: list[dict]
    split_groups: list[dict]
    split_enabled: bool
    lock_date: Date | None = None
    max_records: int


class BatchIn(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    file_type: str = Field(max_length=10)
    file_hash: str | None = Field(default=None, max_length=64)


class ChunkIn(BaseModel):
    records: list[RecordIn] = Field(max_length=500)
    options: ImportOptions = Field(default_factory=ImportOptions)


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    file_type: str
    status: str
    rows_total: int
    rows_imported: int
    rows_skipped: int
    rows_failed: int
    rows_undone: int
    expense_total: Decimal
    income_total: Decimal
    created_at: datetime
    finished_at: datetime | None
    undone_at: datetime | None


class ImportRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    row_no: int
    kind: str | None
    status: str
    reason: str | None
    record_date: Date | None
    description: str | None
    amount: Decimal | None
    expense_id: int | None
    fund_id: int | None
    split_bill_id: int | None


class BatchDetail(BatchOut):
    records: list[ImportRecordOut]
    # What Undo would do now: how many records it removes, and why the others stay
    undo_removes: int
    undo_keeps: list[dict]
