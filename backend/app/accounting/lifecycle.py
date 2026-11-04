"""Deleting and backing up a user's books, for the admin's delete-user / delete-data actions."""

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .models import (
    AccLedger,
    AccSettings,
    BankImport,
    BankStatementLine,
    PostingRule,
    StockValue,
    Voucher,
    VoucherLine,
)

# Restore order: parents before children
BACKUP_MODELS = [AccSettings, AccLedger, PostingRule, StockValue, Voucher, VoucherLine, BankImport, BankStatementLine]


def delete_books(db: Session, user_id: int) -> None:
    """Explicit deletes in dependency order (so it doesn't rely on database cascades)."""
    vouchers = select(Voucher.id).where(Voucher.user_id == user_id)
    db.execute(delete(BankStatementLine).where(BankStatementLine.user_id == user_id))
    db.execute(delete(BankImport).where(BankImport.user_id == user_id))
    db.execute(delete(VoucherLine).where(VoucherLine.voucher_id.in_(vouchers)))
    # Reversals point at the voucher they reverse: unlink first so the delete order doesn't matter
    db.execute(update(Voucher).where(Voucher.user_id == user_id).values(reversal_of_id=None, party_ledger_id=None))
    db.execute(delete(Voucher).where(Voucher.user_id == user_id))
    db.execute(delete(PostingRule).where(PostingRule.user_id == user_id))
    db.execute(delete(StockValue).where(StockValue.user_id == user_id))
    db.execute(delete(AccLedger).where(AccLedger.user_id == user_id))
    db.execute(delete(AccSettings).where(AccSettings.user_id == user_id))


def snapshot_books(db: Session, rows, user_id: int) -> dict:
    """{table: [row dicts]} for a backup. `rows(db, model, *where)` is the backup module's row reader."""
    return {
        "acc_settings": rows(db, AccSettings, AccSettings.user_id == user_id),
        "acc_ledgers": rows(db, AccLedger, AccLedger.user_id == user_id),
        "acc_posting_rules": rows(db, PostingRule, PostingRule.user_id == user_id),
        "acc_stock": rows(db, StockValue, StockValue.user_id == user_id),
        # By id, so a voucher is restored before the reversal that points at it
        "vouchers": sorted(rows(db, Voucher, Voucher.user_id == user_id), key=lambda r: r["id"]),
        "voucher_lines": rows(db, VoucherLine, VoucherLine.voucher_id.in_(select(Voucher.id).where(Voucher.user_id == user_id))),
        "bank_imports": rows(db, BankImport, BankImport.user_id == user_id),
        "bank_statement_lines": rows(db, BankStatementLine, BankStatementLine.user_id == user_id),
    }
