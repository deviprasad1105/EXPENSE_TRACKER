"""Accounting API: books settings, chart of accounts, vouchers and posting rules.

Access is the app's usual: the owner and admins can write; managers and people the owner shared with can read.
The person whose books they are needs the Accounting feature (which needs Expenses), granted by an admin.
"""

from collections import Counter
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import FEATURE_ACCOUNTING, Target, get_current_user, has_feature, resolve_target
from ..models import Expense, Fund, Subscription, User
from ..timeutil import now_local, today_local
from . import autopost
from .balances import balances_as_of, is_nominal, ledger_usage, ledgers_of
from .chart import GROUPS, STATES, fy_end, fy_label, fy_of, fy_start
from .engine import (
    VOUCHER_TYPES,
    AccountingError,
    Line,
    PeriodLocked,
    create_books,
    get_books,
    post_voucher,
    reverse_voucher,
    unique_ledger_name,
)
from .models import RULE_FUND_SOURCE, AccLedger, AccSettings, PostingRule, Voucher, VoucherLine
from .schemas import (
    LedgerIn,
    LedgerOut,
    ReverseIn,
    RuleOut,
    RuleUpdate,
    SettingsIn,
    SettingsOut,
    SyncOut,
    VoucherIn,
    VoucherOut,
    VoucherPage,
)

router = APIRouter(prefix="/api/accounting", tags=["accounting"])


# ---------- access ----------
def open_books(db: Session, viewer: User, user_id: int | None, write: bool = False) -> tuple[Target, AccSettings]:
    """Resolve whose books, check access and the feature, and set the books up on first use."""
    target = resolve_target(db, viewer, user_id, write=write)
    if not has_feature(target.user, FEATURE_ACCOUNTING):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Accounting isn't enabled for @{target.user.username}")
    books = get_books(db, target.user.id)
    if books is None:
        try:
            books = create_books(db, target.user)
            autopost.sync_all(db, books)
            db.commit()
        except IntegrityError:
            # Another request set them up at the same moment
            db.rollback()
            books = get_books(db, target.user.id)
    return target, books


def _ledger(db: Session, books: AccSettings, ledger_id: int) -> AccLedger:
    ledger = db.get(AccLedger, ledger_id)
    if ledger is None or ledger.user_id != books.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ledger not found")
    return ledger


def _voucher(db: Session, books: AccSettings, voucher_id: int) -> Voucher:
    v = db.get(Voucher, voucher_id)
    if v is None or v.user_id != books.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Voucher not found")
    return v


# ---------- reference data ----------
@router.get("/meta")
def meta():
    return {
        "groups": [
            {"code": g.code, "name": g.name, "parent": g.parent, "nature": g.nature, "section": g.section}
            for g in sorted(GROUPS.values(), key=lambda g: g.order)
        ],
        "voucher_types": list(VOUCHER_TYPES),
        "states": STATES,
    }


# ---------- settings ----------
def _settings_out(books: AccSettings, target: Target) -> SettingsOut:
    current = fy_of(today_local())
    first = min(fy_of(books.books_start), current)
    return SettingsOut(
        user_id=books.user_id, business_name=books.business_name, state=books.state, gstin=books.gstin,
        books_start=books.books_start, lock_date=books.lock_date, locked_by=books.locked_by, locked_at=books.locked_at,
        synced_at=books.synced_at, current_fy=current, writable=target.writable,
        years=[{"fy": y, "label": fy_label(y), "start": fy_start(y), "end": fy_end(y)} for y in range(current, first - 1, -1)],
    )


@router.get("/settings", response_model=SettingsOut)
def get_settings(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target, books = open_books(db, viewer, user_id)
    return _settings_out(books, target)


@router.put("/settings", response_model=SettingsOut)
def update_settings(
    data: SettingsIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    target, books = open_books(db, viewer, user_id, write=True)
    if data.state is not None and data.state and data.state not in STATES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a state from the list")
    for field in ("business_name", "state", "gstin"):
        value = getattr(data, field)
        if value is not None:
            setattr(books, field, value.strip() or None)
    if data.clear_lock:
        books.lock_date = books.locked_by = books.locked_at = None
    elif data.lock_date is not None:
        if data.lock_date > today_local():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The lock date can't be in the future")
        books.lock_date, books.locked_by, books.locked_at = data.lock_date, viewer.username, now_local()
    db.commit()
    return _settings_out(books, target)


@router.post("/sync", response_model=SyncOut)
def resync(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Post anything that isn't posted yet (or changed); locked periods are left alone and listed."""
    _, books = open_books(db, viewer, user_id, write=True)
    report = autopost.sync_all(db, books)
    db.commit()
    return report.as_dict()


# ---------- ledgers ----------
def _ledger_out(g: AccLedger, balance: Decimal, used: set[int]) -> LedgerOut:
    return LedgerOut(
        id=g.id, name=g.name, group_code=g.group_code, group_name=GROUPS[g.group_code].name, code=g.code,
        system=bool(g.code or g.account_id or g.split_group_id),
        opening_balance=abs(Decimal(g.opening_balance)), opening_side="DR" if g.opening_balance >= 0 else "CR",
        balance=abs(balance), balance_side="DR" if balance >= 0 else "CR",
        is_active=g.is_active, is_depreciation=g.is_depreciation, account_id=g.account_id,
        split_group_id=g.split_group_id, gstin=g.gstin, state=g.state, used=g.id in used,
    )


@router.get("/ledgers", response_model=list[LedgerOut])
def list_ledgers(
    user_id: int | None = None, as_of: date | None = None, viewer: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, books = open_books(db, viewer, user_id)
    ledgers = ledgers_of(db, books.user_id)
    bal = balances_as_of(db, books.user_id, as_of or today_local(), ledgers)
    used = ledger_usage(db, books.user_id)
    order = {c: g.order for c, g in GROUPS.items()}
    return [_ledger_out(g, bal.get(g.id, Decimal(0)), used) for g in sorted(ledgers, key=lambda g: (order[g.group_code], g.name.casefold()))]


def _apply_ledger(db: Session, books: AccSettings, ledger: AccLedger, data: LedgerIn) -> None:
    if data.group_code not in GROUPS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a group from the list")
    if data.state and data.state not in STATES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a state from the list")
    opening = data.opening_balance if data.opening_side == "DR" else -data.opening_balance
    linked = ledger.account_id is not None or ledger.split_group_id is not None
    if linked and (data.group_code != ledger.group_code or opening != Decimal(ledger.opening_balance or 0)):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This ledger follows a bank account or split group: change its type and opening balance there",
        )
    if ledger.code and data.group_code != ledger.group_code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The group of a standard ledger can't be changed")
    probe = AccLedger(group_code=data.group_code)
    if opening and is_nominal(probe):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Income and expense ledgers start every year at zero: they have no opening balance"
        )
    if ledger.id and opening != Decimal(ledger.opening_balance or 0) and books.lock_date:
        raise PeriodLocked("The books are locked, so opening balances can't change. Use a journal voucher instead.")
    if ledger.code and not data.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Standard ledgers can't be deactivated")
    ledger.name = unique_ledger_name(db, books.user_id, data.name.strip(), exclude_id=ledger.id)
    ledger.group_code, ledger.opening_balance = data.group_code, opening
    ledger.is_active, ledger.is_depreciation = data.is_active, data.is_depreciation
    ledger.gstin, ledger.state = data.gstin or None, data.state or None


@router.post("/ledgers", response_model=LedgerOut, status_code=201)
def create_ledger(
    data: LedgerIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    _, books = open_books(db, viewer, user_id, write=True)
    clash = db.scalar(select(AccLedger.id).where(AccLedger.user_id == books.user_id,
                                                 func.lower(AccLedger.name) == data.name.strip().lower()))
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a ledger with that name")
    ledger = AccLedger(user_id=books.user_id, opening_balance=Decimal(0))
    _apply_ledger(db, books, ledger, data)
    db.add(ledger)
    db.commit()
    return _ledger_out(ledger, Decimal(ledger.opening_balance), set())


@router.put("/ledgers/{ledger_id}", response_model=LedgerOut)
def update_ledger(
    ledger_id: int, data: LedgerIn, user_id: int | None = None, viewer: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, books = open_books(db, viewer, user_id, write=True)
    ledger = _ledger(db, books, ledger_id)
    clash = db.scalar(select(AccLedger.id).where(AccLedger.user_id == books.user_id, AccLedger.id != ledger.id,
                                                 func.lower(AccLedger.name) == data.name.strip().lower()))
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a ledger with that name")
    _apply_ledger(db, books, ledger, data)
    db.commit()
    bal = balances_as_of(db, books.user_id, today_local(), [ledger])
    return _ledger_out(ledger, bal[ledger.id], ledger_usage(db, books.user_id))


@router.delete("/ledgers/{ledger_id}", status_code=204)
def delete_ledger(ledger_id: int, user_id: int | None = None, viewer: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    _, books = open_books(db, viewer, user_id, write=True)
    ledger = _ledger(db, books, ledger_id)
    if ledger.code or ledger.account_id or ledger.split_group_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Standard and linked ledgers can't be deleted. Deactivate it instead.")
    if db.scalar(select(VoucherLine.id).where(VoucherLine.ledger_id == ledger.id).limit(1)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This ledger has entries, so it can't be deleted. Deactivate it instead.")
    if db.scalar(select(PostingRule.id).where(PostingRule.ledger_id == ledger.id).limit(1)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A posting rule uses this ledger. Point the rule elsewhere first.")
    if ledger.opening_balance and books.lock_date:
        raise PeriodLocked("The books are locked, so a ledger with an opening balance can't be deleted.")
    db.delete(ledger)
    db.commit()


# ---------- vouchers ----------
def voucher_out(v: Voucher, names: dict[int, str], reversed_by: dict[int, int]) -> VoucherOut:
    label = None
    if v.source_type:
        label = f"{autopost.SOURCE_LABEL.get(v.source_type, v.source_type)} #{v.source_id}"
    return VoucherOut(
        id=v.id, voucher_type=v.voucher_type, voucher_no=v.voucher_no, fy=v.fy, voucher_date=v.voucher_date,
        narration=v.narration, status=v.status, is_reversal=v.is_reversal, reversal_of_id=v.reversal_of_id,
        reversed_by_id=reversed_by.get(v.id), source_type=v.source_type, source_id=v.source_id, source_label=label,
        party_ledger_id=v.party_ledger_id, invoice_no=v.invoice_no, place_of_supply=v.place_of_supply,
        total=sum((Decimal(l.debit) for l in v.lines), Decimal(0)), created_at=v.created_at,
        lines=[{"id": l.id, "ledger_id": l.ledger_id, "ledger_name": names.get(l.ledger_id, "?"), "debit": l.debit,
                "credit": l.credit, "gst_rate": l.gst_rate} for l in v.lines],
    )


def vouchers_out(db: Session, user_id: int, vouchers: list[Voucher]) -> list[VoucherOut]:
    names = {g.id: g.name for g in ledgers_of(db, user_id)}
    ids = [v.id for v in vouchers]
    reversed_by = (
        dict(db.execute(select(Voucher.reversal_of_id, Voucher.id).where(Voucher.reversal_of_id.in_(ids))).all()) if ids else {}
    )
    return [voucher_out(v, names, reversed_by) for v in vouchers]


@router.get("/vouchers", response_model=VoucherPage)
def list_vouchers(
    user_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    voucher_type: str | None = Query(None, pattern="^(PAYMENT|RECEIPT|CONTRA|JOURNAL|SALES|PURCHASE)$"),
    ledger_id: int | None = None,
    q: str | None = Query(None, max_length=100),
    origin: str | None = Query(None, pattern="^(manual|auto)$"),
    hide_reversed: bool = False,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    viewer: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, books = open_books(db, viewer, user_id)
    stmt = select(Voucher).where(Voucher.user_id == books.user_id)
    if start:
        stmt = stmt.where(Voucher.voucher_date >= start)
    if end:
        stmt = stmt.where(Voucher.voucher_date <= end)
    if voucher_type:
        stmt = stmt.where(Voucher.voucher_type == voucher_type)
    if ledger_id:
        stmt = stmt.where(Voucher.id.in_(select(VoucherLine.voucher_id).where(VoucherLine.ledger_id == ledger_id)))
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Voucher.narration).like(like), func.lower(Voucher.invoice_no).like(like)))
    if origin == "manual":
        stmt = stmt.where(Voucher.source_type.is_(None))
    elif origin == "auto":
        stmt = stmt.where(Voucher.source_type.is_not(None))
    if hide_reversed:
        stmt = stmt.where(Voucher.status == "POSTED", Voucher.is_reversal == false())
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.options(selectinload(Voucher.lines))
        .order_by(Voucher.voucher_date.desc(), Voucher.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return {"items": vouchers_out(db, books.user_id, list(rows)), "total": total, "page": page, "size": size}


@router.get("/vouchers/{voucher_id}", response_model=VoucherOut)
def get_voucher(voucher_id: int, user_id: int | None = None, viewer: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    _, books = open_books(db, viewer, user_id)
    return vouchers_out(db, books.user_id, [_voucher(db, books, voucher_id)])[0]


@router.post("/vouchers", response_model=VoucherOut, status_code=201)
def create_voucher(data: VoucherIn, user_id: int | None = None, viewer: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    _, books = open_books(db, viewer, user_id, write=True)
    if data.voucher_date > today_local():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A voucher can't be dated in the future")
    v = post_voucher(
        db, books, voucher_type=data.voucher_type, voucher_date=data.voucher_date, narration=data.narration,
        lines=[Line(l.ledger_id, l.debit, l.credit) for l in data.lines], created_by=viewer.id, manual=True,
    )
    db.commit()
    return vouchers_out(db, books.user_id, [v])[0]


@router.post("/vouchers/{voucher_id}/reverse", response_model=VoucherOut, status_code=201)
def reverse(voucher_id: int, data: ReverseIn, user_id: int | None = None, viewer: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    _, books = open_books(db, viewer, user_id, write=True)
    v = _voucher(db, books, voucher_id)
    if v.source_type:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"This voucher was posted from a {autopost.SOURCE_LABEL[v.source_type].lower()}: edit or delete that instead, "
            "and the books follow.",
        )
    reversal = reverse_voucher(db, books, v, created_by=viewer.id, narration=data.narration)
    db.commit()
    return vouchers_out(db, books.user_id, [reversal])[0]


# ---------- posting rules ----------
def _rules_out(db: Session, books: AccSettings) -> list[RuleOut]:
    uid = books.user_id
    rules = db.scalars(select(PostingRule).where(PostingRule.user_id == uid).order_by(PostingRule.kind, PostingRule.label))
    ledgers = {g.id: g for g in ledgers_of(db, uid)}
    usage: Counter = Counter()
    for cat, n in db.execute(select(Expense.category, func.count()).where(Expense.user_id == uid).group_by(Expense.category)):
        usage[("EXPENSE_CATEGORY", autopost.rule_key(cat))] += n
    for src, n in db.execute(select(Fund.source, func.count()).where(Fund.user_id == uid).group_by(Fund.source)):
        usage[(RULE_FUND_SOURCE, autopost.rule_key(src))] += n
    for cat, n in db.execute(select(Subscription.category, func.count()).where(Subscription.user_id == uid).group_by(Subscription.category)):
        usage[("EXPENSE_CATEGORY", autopost.rule_key(cat))] += n
    out = []
    for r in rules:
        g = ledgers.get(r.ledger_id)
        if g is None:
            continue
        out.append(RuleOut(id=r.id, kind=r.kind, label=r.label, ledger_id=g.id, ledger_name=g.name, group_code=g.group_code,
                           group_name=GROUPS[g.group_code].name, nature=GROUPS[g.group_code].nature,
                           usage=usage[(r.kind, r.rule_key)]))
    return out


@router.get("/rules", response_model=list[RuleOut])
def list_rules(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _, books = open_books(db, viewer, user_id)
    return _rules_out(db, books)


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, data: RuleUpdate, user_id: int | None = None, viewer: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Point a category (or fund source) at another ledger, and re-post its transactions in open periods."""
    _, books = open_books(db, viewer, user_id, write=True)
    rule = db.get(PostingRule, rule_id)
    if rule is None or rule.user_id != books.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    ledger = _ledger(db, books, data.ledger_id)
    if not ledger.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose an active ledger")
    if ledger.group_code in ("BANK_ACCOUNTS", "CASH_IN_HAND", "BANK_OD") or ledger.account_id or ledger.split_group_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A category can't post to a bank, cash or split ledger")
    rule.ledger_id, rule.updated_at = ledger.id, now_local()
    db.flush()
    report = autopost.sync_rule(db, books, rule)
    db.commit()
    return {"rules": _rules_out(db, books), "sync": report.as_dict()}


def register_error_handler(app) -> None:
    from fastapi.responses import JSONResponse

    @app.exception_handler(AccountingError)
    async def _accounting_error(request, exc: AccountingError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)


# Reports, stock, GST invoices and bank reconciliation add their routes to the same router
from . import router_reports  # noqa: E402,F401
