"""Transaction aliases: give a payee a readable name ("ramesh@ybl" / "UPI/DR/4123.../RAMESH TEA STALL" → "Tea").

Every expense and money received gets a *payee key* worked out from its description: the UPI ID in it when there is
one, otherwise the words of the description without reference numbers, dates and banking noise (UPI, DR, IMPS…).
Records with the same key are the same payee, which is how the settings page groups them.

An alias matches a record when its `match_key` equals the record's payee key, or (for keys the user typed) when all of
its words appear in the description. A matching record shows the alias as its name (item_name of an expense, source of
money received); the original description is kept in raw_name so the alias can be renamed or removed later.
"""

import re
from dataclasses import dataclass, field

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..accounting.engine import get_books, is_locked
from ..models import Expense, Fund, TransactionAlias
from ..timeutil import now_local

VPA_RE = re.compile(r"(?<![\w.@-])([a-z0-9][a-z0-9._-]{1,63}@[a-z][a-z0-9]{1,30})(?![\w@])", re.I)
# Words that say how money moved, not who it went to
NOISE = {
    "upi", "dr", "cr", "imps", "neft", "rtgs", "pos", "ach", "nach", "ecs", "txn", "ref", "refno", "no", "to", "from", "paid",
    "payment", "pay", "sent", "received", "by", "via", "p2a", "p2m", "p2p", "mb", "ib", "trf", "transfer", "debit", "credit",
    "debited", "credited", "vpa", "rs", "inr", "a", "c", "ac", "the", "for", "on", "at", "collect", "request", "upiintent", "mmt",
}
KEY_MAX = 120
# A bank narration of a UPI payment: UPI/DR/<ref>/<name>/<bank>/<upi id>/…  (the name is the 4th part)
UPI_PATH_RE = re.compile(r"upi/(?:p2[am]|dr|cr)/\d+/([^/\n]+)", re.I)


def payee_key(text: str | None) -> str | None:
    """The payee a description is about: a UPI ID when it has one, else its meaningful words, lower case."""
    if not text:
        return None
    m = VPA_RE.search(text)
    if m:
        return m.group(1).lower()[:KEY_MAX]
    # A bank's UPI narration: the payee's name is its own part (the rest is the reference and the payee's bank)
    path = UPI_PATH_RE.search(text)
    if path:
        text = path.group(1)
    words = re.sub(r"[^a-z0-9&]+", " ", text.lower()).split()
    # Reference numbers, dates, card digits: anything with a digit in it
    words = [w for w in words if w not in NOISE and not any(ch.isdigit() for ch in w)]
    return " ".join(words)[:KEY_MAX] or None


def normalise_key(text: str) -> str:
    """A key the user typed: a UPI ID as it is, anything else cleaned like a description."""
    text = text.strip()
    if VPA_RE.fullmatch(text):
        return text.lower()
    words = re.sub(r"[^a-z0-9&@._-]+", " ", text.lower()).split()
    return " ".join(words)[:KEY_MAX]


def _words(text: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9&@._-]+", " ", text.lower()).split())


def alias_matches(alias: TransactionAlias, key: str | None, text: str | None) -> bool:
    if key and alias.match_key == key:
        return True
    if not text:
        return False
    if "@" in alias.match_key:
        return alias.match_key in text.lower()
    # Every word of the alias's key is a word of the description ("tea stall" matches "UPI-RAMESH TEA STALL-…")
    wanted = alias.match_key.split()
    return bool(wanted) and len(alias.match_key) >= 3 and set(wanted) <= _words(text)


def find_alias(aliases: list[TransactionAlias], key: str | None, text: str | None) -> TransactionAlias | None:
    """The alias for a record: an exact key match first, else the most specific (longest) key found in the text."""
    exact = [a for a in aliases if key and a.match_key == key]
    if exact:
        return exact[0]
    found = [a for a in aliases if alias_matches(a, key, text)]
    return max(found, key=lambda a: len(a.match_key)) if found else None


def user_aliases(db: Session, user_id: int) -> list[TransactionAlias]:
    return list(db.scalars(select(TransactionAlias).where(TransactionAlias.user_id == user_id)))


# ---------- records ----------
def _name_attr(row) -> str:
    return "item_name" if isinstance(row, Expense) else "source"


def original_text(row) -> str:
    return row.raw_name or getattr(row, _name_attr(row)) or ""


def name_record(row, alias: TransactionAlias, *, set_category: bool = True) -> None:
    """Show `row` under `alias`, keeping its own description in raw_name."""
    attr = _name_attr(row)
    if row.raw_name is None:
        row.raw_name = (getattr(row, attr) or "")[:200]
    setattr(row, attr, alias.alias[:100])
    row.alias_id = alias.id
    if set_category and alias.category and isinstance(row, Expense):
        row.category = alias.category


def label_new(db: Session, row, *, set_category: bool = True, aliases: list[TransactionAlias] | None = None,
              text: str | None = None) -> TransactionAlias | None:
    """For a record being created: note its payee key and, when an alias matches, name it. Call before the flush.

    `aliases` saves the query when many records are created at once; `text` is the description to match when it isn't
    the record's name (money received imported with a category as its source)."""
    text = text or getattr(row, _name_attr(row))
    row.payee_key = payee_key(text)
    alias = find_alias(user_aliases(db, row.user_id) if aliases is None else aliases, row.payee_key, text)
    if alias:
        if text != getattr(row, _name_attr(row)):
            row.raw_name = text[:200]  # keep the description it was matched on as the original
        name_record(row, alias, set_category=set_category)
    return alias


def backfill_keys(db: Session, user_id: int) -> None:
    """Records saved before payee keys existed get one (a bulk update: it changes nothing the books post)."""
    for model, attr in ((Expense, Expense.item_name), (Fund, Fund.source)):
        rows = db.execute(select(model.id, attr, model.raw_name).where(model.user_id == user_id, model.payee_key.is_(None))).all()
        params = [{"id": r[0], "payee_key": payee_key(r[2] or r[1]) or "-"} for r in rows]
        for i in range(0, len(params), 500):
            db.execute(update(model), params[i:i + 500])
    db.flush()


def unnamed_matches(db: Session, user_id: int, alias_or_key) -> tuple[list[Expense], list[Fund]]:
    """Records without an alias that an alias (or a key the user is typing) would name."""
    if isinstance(alias_or_key, TransactionAlias):
        probe = alias_or_key
    else:
        probe = TransactionAlias(match_key=normalise_key(alias_or_key), alias="")
    if not probe.match_key:
        return [], []
    out = []
    for model, attr in ((Expense, Expense.item_name), (Fund, Fund.source)):
        stmt = select(model).where(model.user_id == user_id, model.alias_id.is_(None))
        if "@" in probe.match_key:
            # Cheap pre-filter in SQL; the exact rule runs below
            stmt = stmt.where(or_(model.payee_key == probe.match_key, attr.ilike(f"%{probe.match_key}%"),
                                  model.raw_name.ilike(f"%{probe.match_key}%")))
        rows = [r for r in db.scalars(stmt) if alias_matches(probe, r.payee_key, original_text(r))]
        rows.sort(key=lambda r: (getattr(r, "expense_date", None) or r.fund_date), reverse=True)
        out.append(rows)
    return out[0], out[1]


@dataclass
class ApplyResult:
    renamed: int = 0
    skipped_locked: int = 0
    skipped_ids: list[int] = field(default_factory=list)


def apply_alias(db: Session, alias: TransactionAlias, expense_ids: list[int], fund_ids: list[int], set_category: bool = False) -> ApplyResult:
    """Name the chosen existing records (and give expenses the alias's category when `set_category`). Records in a
    locked accounting period are left as they are."""
    result = ApplyResult()
    books = get_books(db, alias.user_id)
    for model, ids in ((Expense, expense_ids), (Fund, fund_ids)):
        if not ids:
            continue
        for row in db.scalars(select(model).where(model.user_id == alias.user_id, model.id.in_(ids))):
            if is_locked(books, getattr(row, "expense_date", None) or row.fund_date):
                result.skipped_locked += 1
                continue
            # Old records keep their category unless the user asked for the alias's one
            name_record(row, alias, set_category=set_category)
            result.renamed += 1
    return result


def rename_linked(db: Session, alias: TransactionAlias, old_category: str | None) -> ApplyResult:
    """After an alias is renamed: every record it names follows (an expense's category too, if it came from the alias)."""
    result = ApplyResult()
    books = get_books(db, alias.user_id)
    for model in (Expense, Fund):
        for row in db.scalars(select(model).where(model.alias_id == alias.id)):
            if is_locked(books, getattr(row, "expense_date", None) or row.fund_date):
                result.skipped_locked += 1
                continue
            setattr(row, _name_attr(row), alias.alias[:100])
            if isinstance(row, Expense) and alias.category and old_category and row.category == old_category:
                row.category = alias.category
            result.renamed += 1
    alias.updated_at = now_local()
    return result


def unlink(db: Session, alias: TransactionAlias, restore: bool) -> ApplyResult:
    """Before an alias is deleted: its records go back to their own description (restore) or keep the alias's name."""
    result = ApplyResult()
    books = get_books(db, alias.user_id)
    for model in (Expense, Fund):
        for row in db.scalars(select(model).where(model.alias_id == alias.id)):
            row.alias_id = None
            if restore and row.raw_name:
                if is_locked(books, getattr(row, "expense_date", None) or row.fund_date):
                    result.skipped_locked += 1
                    continue
                setattr(row, _name_attr(row), row.raw_name[:100])
                row.raw_name = None
                result.renamed += 1
    return result


def suggest_alias(key: str, sample: str | None) -> str:
    """A starting point for the alias field: the payee's name in title case."""
    text = VPA_RE.sub(" ", sample or "")
    path = UPI_PATH_RE.search(sample or "")
    if path:
        text = path.group(1)
    if not text.strip():
        text = key.split("@")[0]
    # Nothing after a reference marker is a name; nor is any word with a digit in it (reference codes, card numbers)
    text = re.split(r"\b(?:ref|rrn|utr)\b", text, maxsplit=1, flags=re.I)[0]
    text = " ".join(w for w in re.split(r"[\s/#:-]+", text) if not any(ch.isdigit() for ch in w))
    words = [w for w in re.split(r"[^A-Za-z&]+", text) if w and w.lower() not in NOISE]
    return " ".join(w.capitalize() for w in words)[:100] or key[:100]
