"""Transaction aliases (Settings → Transaction aliases): name a payee once, and every record from or to it follows."""

from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, resolve_target
from ..models import Expense, Fund, TransactionAlias, User
from ..schemas import AliasApply, AliasChangeOut, AliasGroup, AliasIn, AliasOut, AliasRecord, AliasUpdate
from ..services import aliases as svc

router = APIRouter(prefix="/api/aliases", tags=["aliases"])

PREVIEW_LIMIT = 500


def _out(db: Session, alias: TransactionAlias, unnamed: int | None = None) -> AliasOut:
    out = AliasOut.model_validate(alias)
    out.records = sum(
        db.scalar(select(func.count(m.id)).where(m.alias_id == alias.id)) or 0 for m in (Expense, Fund)
    )
    if unnamed is None:
        exp, funds = svc.unnamed_matches(db, alias.user_id, alias)
        unnamed = len(exp) + len(funds)
    out.unnamed = unnamed
    return out


def _unnamed_counts(db: Session, user_id: int, aliases: list[TransactionAlias]) -> dict[int, int]:
    """For each alias, how many records without an alias it matches (one pass over the records)."""
    counts = dict.fromkeys((a.id for a in aliases), 0)
    if not aliases:
        return counts
    for model, name in ((Expense, Expense.item_name), (Fund, Fund.source)):
        rows = db.execute(select(model.payee_key, name, model.raw_name).where(model.user_id == user_id, model.alias_id.is_(None)))
        for key, text, raw in rows:
            for a in aliases:
                if svc.alias_matches(a, key, raw or text):
                    counts[a.id] += 1
    return counts


def _load(db: Session, viewer: User, alias_id: int) -> TransactionAlias:
    alias = db.get(TransactionAlias, alias_id)
    if not alias:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alias not found")
    resolve_target(db, viewer, alias.user_id, write=True)
    return alias


def _records(expenses: list[Expense], funds: list[Fund]) -> list[AliasRecord]:
    rows = [AliasRecord(kind="EXPENSE", id=e.id, date=e.expense_date, name=e.item_name, original=e.raw_name, amount=e.amount,
                        category=e.category, origin=e.origin) for e in expenses]
    rows += [AliasRecord(kind="FUND", id=f.id, date=f.fund_date, name=f.source, original=f.raw_name, amount=f.amount,
                         origin=f.origin) for f in funds]
    rows.sort(key=lambda r: (r.date, r.id), reverse=True)
    return rows[:PREVIEW_LIMIT]


@router.get("", response_model=list[AliasOut])
def list_aliases(user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id)
    svc.backfill_keys(db, target.user.id)
    db.commit()
    aliases = list(db.scalars(
        select(TransactionAlias).where(TransactionAlias.user_id == target.user.id).order_by(func.lower(TransactionAlias.alias))
    ))
    counts = _unnamed_counts(db, target.user.id, aliases)
    return [_out(db, a, counts[a.id]) for a in aliases]


@router.get("/groups", response_model=list[AliasGroup])
def groups(user_id: int | None = None, min_count: int = 1, limit: int = 100, viewer: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    """Payees found in the records that have no alias yet, most frequent first."""
    target = resolve_target(db, viewer, user_id)
    uid = target.user.id
    svc.backfill_keys(db, uid)
    db.commit()
    found: dict[str, dict] = defaultdict(lambda: {"names": set(), "ec": 0, "et": Decimal(0), "fc": 0, "ft": Decimal(0), "last": None})
    for model, name, day, prefix in ((Expense, Expense.item_name, Expense.expense_date, "e"), (Fund, Fund.source, Fund.fund_date, "f")):
        stmt = (
            select(model.payee_key, func.count(model.id), func.sum(model.amount), func.max(day), func.min(name), func.max(name))
            .where(model.user_id == uid, model.alias_id.is_(None), model.payee_key.is_not(None), model.payee_key != "-")
            .group_by(model.payee_key)
        )
        for key, count, total, last, first_name, last_name in db.execute(stmt):
            g = found[key]
            g["names"].update(n for n in (first_name, last_name) if n)
            g[f"{prefix}c"] += count
            g[f"{prefix}t"] += Decimal(total or 0)
            g["last"] = max(d for d in (g["last"], last) if d)
    by_key = {a.match_key: a for a in db.scalars(select(TransactionAlias).where(TransactionAlias.user_id == uid))}
    out = []
    for key, g in found.items():
        if g["ec"] + g["fc"] < max(min_count, 1):
            continue
        names = sorted(g["names"])[:3]
        alias = by_key.get(key)
        out.append(AliasGroup(
            match_key=key, names=names, suggestion=alias.alias if alias else svc.suggest_alias(key, names[0] if names else None),
            expense_count=g["ec"], expense_total=g["et"], fund_count=g["fc"], fund_total=g["ft"], last_date=g["last"],
            alias_id=alias.id if alias else None, alias=alias.alias if alias else None,
        ))
    out.sort(key=lambda g: (-(g.expense_count + g.fund_count), g.match_key))
    return out[: max(1, min(limit, 500))]


@router.get("/matches", response_model=list[AliasRecord])
def matches(match_key: str, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Records without an alias that a key would name: the preview shown before saving an alias."""
    target = resolve_target(db, viewer, user_id)
    svc.backfill_keys(db, target.user.id)
    db.commit()
    return _records(*svc.unnamed_matches(db, target.user.id, match_key))


@router.get("/{alias_id}/records", response_model=list[AliasRecord])
def alias_records(alias_id: int, unnamed: bool = False, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The records an alias names, or (unnamed=true) the old ones it matches but hasn't renamed."""
    alias = db.get(TransactionAlias, alias_id)
    if not alias:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alias not found")
    resolve_target(db, viewer, alias.user_id)
    if unnamed:
        return _records(*svc.unnamed_matches(db, alias.user_id, alias))
    return _records(
        list(db.scalars(select(Expense).where(Expense.alias_id == alias.id))),
        list(db.scalars(select(Fund).where(Fund.alias_id == alias.id))),
    )


@router.post("", response_model=AliasChangeOut, status_code=201)
def create_alias(data: AliasIn, user_id: int | None = None, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = resolve_target(db, viewer, user_id, write=True)
    key = svc.normalise_key(data.match_key)
    if len(key) < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter the name or UPI ID to look for")
    if db.scalar(select(TransactionAlias.id).where(TransactionAlias.user_id == target.user.id, TransactionAlias.match_key == key)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"There is already an alias for '{key}'. Rename that one instead.")
    alias = TransactionAlias(user_id=target.user.id, match_key=key, alias=data.alias.strip(), category=(data.category or "").strip() or None)
    db.add(alias)
    db.flush()
    # Only records the alias really matches can be renamed, whatever ids were sent
    exp, funds = svc.unnamed_matches(db, target.user.id, alias)
    wanted_e, wanted_f = set(data.expense_ids), set(data.fund_ids)
    every = data.apply_all_existing
    result = svc.apply_alias(db, alias, [e.id for e in exp if every or e.id in wanted_e], [f.id for f in funds if every or f.id in wanted_f],
                             set_category=data.set_category_existing)
    db.commit()
    return AliasChangeOut(alias=_out(db, alias), renamed=result.renamed, skipped_locked=result.skipped_locked)


@router.post("/{alias_id}/apply", response_model=AliasChangeOut)
def apply_to_old(alias_id: int, data: AliasApply, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Rename old records the alias matches (chosen from GET /{id}/records?unnamed=true)."""
    alias = _load(db, viewer, alias_id)
    exp, funds = svc.unnamed_matches(db, alias.user_id, alias)
    wanted_e, wanted_f = set(data.expense_ids), set(data.fund_ids)
    result = svc.apply_alias(db, alias, [e.id for e in exp if e.id in wanted_e], [f.id for f in funds if f.id in wanted_f],
                             set_category=data.set_category_existing)
    db.commit()
    return AliasChangeOut(alias=_out(db, alias), renamed=result.renamed, skipped_locked=result.skipped_locked)


@router.put("/{alias_id}", response_model=AliasChangeOut)
def update_alias(alias_id: int, data: AliasUpdate, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Rename an alias: every record it names is renamed with it."""
    alias = _load(db, viewer, alias_id)
    old_category = alias.category
    if data.alias:
        alias.alias = data.alias.strip()
    if data.clear_category:
        alias.category = None
    elif data.category is not None:
        alias.category = data.category.strip() or None
    result = svc.rename_linked(db, alias, old_category)
    db.commit()
    return AliasChangeOut(alias=_out(db, alias), renamed=result.renamed, skipped_locked=result.skipped_locked)


@router.delete("/{alias_id}", response_model=AliasChangeOut)
def delete_alias(alias_id: int, restore: bool = True, viewer: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """restore=true puts each record's own description back; false keeps the alias's name on them."""
    alias = _load(db, viewer, alias_id)
    result = svc.unlink(db, alias, restore)
    db.delete(alias)
    db.commit()
    return AliasChangeOut(renamed=result.renamed, skipped_locked=result.skipped_locked)
