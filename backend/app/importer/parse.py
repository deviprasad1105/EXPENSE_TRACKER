"""Turn the rows of a table into records: which column is what, and the dates and amounts in them.

Column names differ between apps and banks ("Txn Date", "Narration", "Withdrawal Amt.", "Cost", "Particulars"…), so
the columns are guessed from their headings, or from their contents when there is no heading row, and the user can
change any of it. Dates may be day-first (Indian), month-first or year-first, with or without month names or times;
amounts may carry ₹ / Rs / INR, Indian digit grouping, Dr / Cr, brackets or a minus sign.
"""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field

from .models import KIND_EXPENSE, KIND_INCOME, KIND_OPENING, KIND_SPLIT, KIND_TRANSFER

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("9999999999.99")  # NUMERIC(12,2)
ROLES = ("date", "description", "category", "amount", "debit", "credit", "type", "account", "to_account", "note",
         "quantity", "my_share", "split_with")
DATE_ORDERS = ("DMY", "MDY", "YMD")


class Mapping(BaseModel):
    # Row index of the column headings in the table; -1 = the table has no heading row
    header_row: int = Field(default=0, ge=-1)
    date: int | None = Field(default=None, ge=0)
    description: int | None = Field(default=None, ge=0)
    category: int | None = Field(default=None, ge=0)
    # One amount column (its sign or a Dr/Cr mark, a type column, or default_type tells money in from out)…
    amount: int | None = Field(default=None, ge=0)
    # …or separate money-out and money-in columns
    debit: int | None = Field(default=None, ge=0)
    credit: int | None = Field(default=None, ge=0)
    type: int | None = Field(default=None, ge=0)
    account: int | None = Field(default=None, ge=0)
    # Transfers: the account the money went to (or an account cell like "Axis Bank->SBI Bank")
    to_account: int | None = Field(default=None, ge=0)
    note: int | None = Field(default=None, ge=0)
    quantity: int | None = Field(default=None, ge=0)
    my_share: int | None = Field(default=None, ge=0)
    split_with: int | None = Field(default=None, ge=0)
    date_order: str = Field(default="DMY", pattern="^(DMY|MDY|YMD)$")
    # What a plain amount is when nothing else says: EXPENSE, INCOME, or SIGN (negative = expense)
    default_type: str = Field(default=KIND_EXPENSE, pattern="^(EXPENSE|INCOME|SIGN)$")


# ---------- dates ----------
_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_NUMERIC_DATE = re.compile(r"^(\d{1,4})[/\-. ](\d{1,2})[/\-. ](\d{1,4})$")
_NAMED_DATE = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?[\s\-/.,]*([a-z]{3,9})\.?[\s\-/.,]*(\d{2,4})$")
_NAMED_DATE_US = re.compile(r"^([a-z]{3,9})\.?[\s\-/.]*(\d{1,2})(?:st|nd|rd|th)?[\s,\-/.]+(\d{2,4})$")
_TIME = re.compile(r"[ T]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?\s*(am|pm)?(z|[+-]\d{2}:?\d{2})?$", re.I)
_EXCEL_EPOCH = date(1899, 12, 30)


def _year(y: int) -> int:
    if y >= 100:
        return y
    pivot = (date.today().year % 100) + 1
    return 2000 + y if y <= pivot else 1900 + y


def _make(y: int, m: int, d: int) -> date | None:
    try:
        return date(_year(y), m, d)
    except ValueError:
        return None


def parse_date(value, order: str = "DMY") -> date | None:
    """A date from text in most common layouts. `order` settles 05/11/2026: DMY (5 Nov) or MDY (11 May)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return None
    # An Excel serial day number (a date column exported without its format)
    if re.fullmatch(r"\d{5}(\.\d+)?", text) and 20000 <= float(text) <= 80000:
        return _EXCEL_EPOCH + timedelta(days=int(float(text)))
    text = _TIME.sub("", text).strip().rstrip(",")
    if re.fullmatch(r"\d{8}", text):  # 20261105 or 05112026
        y_first = _make(int(text[:4]), int(text[4:6]), int(text[6:]))
        return y_first if y_first and 1900 < y_first.year < 2200 else _make(int(text[4:]), int(text[2:4]), int(text[:2]))
    if m := _NUMERIC_DATE.match(text):
        a, b, c = m.groups()
        if len(a) == 4:
            return _make(int(a), int(b), int(c))
        if len(c) not in (2, 4):
            return None
        if order == "MDY":
            return _make(int(c), int(a), int(b))
        if order == "YMD" and len(a) == 2 and len(c) == 2:
            return _make(int(a), int(b), int(c))
        return _make(int(c), int(b), int(a))
    for pattern, swap in ((_NAMED_DATE, False), (_NAMED_DATE_US, True)):
        if m := pattern.match(text):
            d, mon, y = (m.group(2), m.group(1), m.group(3)) if swap else m.groups()
            month = _MONTHS.get(mon[:3])
            if month and (len(mon) == 3 or mon.startswith(("june", "july", "sept")) or any(
                    full.startswith(mon) for full in ("january", "february", "march", "april", "august", "october",
                                                       "november", "december"))):
                return _make(int(y), month, int(d))
    return None


def guess_date_order(values) -> str:
    """DMY unless a value can only be month-first (13/25 is DMY-impossible) or the dates are year-first."""
    dmy = mdy = ymd = 0
    for v in values:
        m = _NUMERIC_DATE.match(_TIME.sub("", str(v or "").strip()))
        if not m:
            continue
        a, b, c = m.groups()
        if len(a) == 4:
            ymd += 1
        elif int(a) > 12 and int(b) <= 12:
            dmy += 1
        elif int(b) > 12 and int(a) <= 12:
            mdy += 1
    if mdy > dmy:
        return "MDY"
    return "DMY"


# ---------- amounts ----------
_CURRENCY = re.compile(r"₹|rs\.?|inr|\$|€|£|/-", re.I)


def parse_money(value) -> tuple[Decimal | None, str | None, str | None]:
    """'₹1,23,456.50 Dr' → (123456.50, 'DR', None). Returns (amount, 'DR' | 'CR' | None, error). Blank → (None, None, None).
    The amount keeps its sign: '(200)', '-200' and '200-' are -200."""
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        value = str(round(value, 6)) if isinstance(value, float) else str(value)
    text = str(value or "").strip()
    if not text:
        return None, None, None
    upper = text.upper()
    side = None
    if m := re.search(r"\b(DR|CR)\.?$", upper) or re.match(r"^(DR|CR)\b\.?", upper):
        side = m.group(1)
    cleaned = re.sub(r"\b(DR|CR)\b\.?", "", upper)
    cleaned = _CURRENCY.sub("", cleaned).replace(" ", "").replace(" ", "")
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative, cleaned = True, cleaned[1:-1]
    if cleaned.endswith("-"):
        negative, cleaned = True, cleaned[:-1]
    if cleaned.startswith("-"):
        negative, cleaned = not negative, cleaned[1:]
    cleaned = cleaned.lstrip("+")
    if not re.fullmatch(r"\d{1,3}(,\d{2,3})*(\.\d+)?|\d+(\.\d+)?|\.\d+", cleaned):
        return None, None, f"“{text[:40]}” isn't an amount"
    try:
        amount = Decimal(cleaned.replace(",", ""))
    except InvalidOperation:
        return None, None, f"“{text[:40]}” isn't an amount"
    if amount != amount.quantize(CENT):
        return None, None, f"{text[:40]} has more than 2 decimal places"
    return (-amount if negative else amount), side, None


# ---------- column guessing ----------
# Words in a heading, strongest first. A heading matches a role when it equals one of its words or contains it as
# whole words. Roles are tried in this order and each column is used once.
SYNONYMS = {
    "date": ["date", "txn date", "transaction date", "tran date", "posting date", "posted on", "booking date",
             "bill date", "invoice date", "paid on", "day", "when", "value date"],
    "debit": ["debit", "withdrawal", "withdrawals", "withdrawal amt", "debit amount", "dr", "money out", "paid out",
              "outflow", "spent", "expense amount", "expenses", "out"],
    "credit": ["credit", "deposit", "deposits", "deposit amt", "credit amount", "cr", "money in", "received",
               "inflow", "income amount", "in"],
    "amount": ["amount", "amt", "total", "cost", "price", "value", "inr", "rs", "rupees", "sum", "expense",
               "transaction amount"],
    "my_share": ["my share", "your share", "own share", "my part", "share"],
    "split_with": ["split with", "shared with", "paid for", "split between", "friends", "participants", "people"],
    "type": ["type", "transaction type", "txn type", "dr cr", "cr dr", "debit credit", "in out", "income expense",
             "kind", "direction", "nature"],
    "category": ["category", "categories", "expense category", "head", "expense head", "tag", "tags", "group",
                 "sub category", "classification", "label"],
    "to_account": ["to account", "transfer to", "to", "destination account", "destination", "credited to",
                   "to acc", "beneficiary account"],
    "account": ["account", "account name", "from account", "from", "bank", "bank account", "payment mode", "mode", "paid via", "paid with",
                "payment method", "method", "wallet", "card", "source account"],
    "quantity": ["qty", "quantity", "units", "count", "nos"],
    "note": ["note", "notes", "memo", "comment", "comments", "remark", "remarks"],
    "description": ["description", "narration", "particulars", "details", "item", "item name", "merchant", "payee",
                    "title", "name", "transaction details", "transaction description", "purpose", "expense name",
                    "for", "what"],
}
ORDER = ("date", "debit", "credit", "my_share", "split_with", "amount", "type", "category", "to_account", "account",
         "quantity", "note", "description")


def _norm(text) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())


def _match(heading: str, words: list[str]) -> int | None:
    """Rank of the best synonym the heading matches (lower is better), or None."""
    if not heading:
        return None
    for rank, w in enumerate(words):
        if heading == w:
            return rank
    padded = f" {heading} "
    for rank, w in enumerate(words):
        if len(w) > 2 and f" {w} " in padded:
            return 100 + rank
    return None


def _guess_from_headings(headings: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    used: set[int] = set()
    norm = [_norm(h) for h in headings]
    for role in ORDER:
        best = None
        for j, h in enumerate(norm):
            if j in used:
                continue
            if role == "date" and "value date" in h and any("date" in x and "value" not in x for x in norm):
                continue  # prefer the transaction date over the value date
            if role in ("amount", "debit", "credit") and any(w in h.split() for w in ("balance", "share", "date", "dt")):
                continue
            rank = _match(h, SYNONYMS[role])
            if rank is not None and (best is None or rank < best[0]):
                best = (rank, j)
        if best is not None:
            found[role] = best[1]
            used.add(best[1])
    # Money out and money in found: a loosely named "amount" column (e.g. "Value") isn't needed
    if "debit" in found and "credit" in found and "amount" in found and _match(norm[found["amount"]], SYNONYMS["amount"][:2]) is None:
        del found["amount"]
    return found


def _looks_like_header(row: list[str]) -> int:
    return sum(1 for c in row if any(_match(_norm(c), words) is not None for words in SYNONYMS.values()))


def _guess_from_contents(rows: list[list[str]]) -> dict[str, int]:
    """No heading row: the column most often holding dates, the one most often holding amounts, the longest text."""
    width = max((len(r) for r in rows), default=0)
    sample = rows[:200]
    scores = []
    for j in range(width):
        values = [r[j] for r in sample if j < len(r) and r[j]]
        dates = sum(1 for v in values if parse_date(v))
        money = sum(1 for v in values if parse_money(v)[0] is not None and not parse_date(v))
        text_len = sum(len(v) for v in values if parse_money(v)[0] is None and not parse_date(v))
        scores.append((j, dates, money, text_len))
    found: dict[str, int] = {}
    if scores:
        j, d, _, _ = max(scores, key=lambda s: s[1])
        if d:
            found["date"] = j
        money_cols = [s for s in scores if s[2] and s[0] != found.get("date")]
        if money_cols:
            found["amount"] = max(money_cols, key=lambda s: s[2])[0]
        text_cols = [s for s in scores if s[3] and s[0] not in found.values()]
        if text_cols:
            found["description"] = max(text_cols, key=lambda s: s[3])[0]
    return found


def guess_mapping(rows: list[list[str]]) -> Mapping:
    header = -1
    best = 1
    for i, row in enumerate(rows[:30]):
        score = _looks_like_header(row)
        if score > best or (score == best and header == -1 and score >= 2):
            header, best = i, score
    if header >= 0:
        found = _guess_from_headings(rows[header])
        if "date" not in found:  # headings without a date column: fall back to the contents
            content = _guess_from_contents(rows[header + 1:])
            if "date" in content and content["date"] not in found.values():
                found["date"] = content["date"]
    else:
        found = _guess_from_contents(rows)
    if "description" not in found and "note" in found:
        # Money Manager style exports put what the money was for in NOTES: that is the description
        found["description"] = found.pop("note")
    data = rows[header + 1:]
    if "type" in found:
        # "Payment type: UPI / Card" isn't money in or out: keep the column only if its values say expense / income
        values = [r[found["type"]] for r in data[:300] if found["type"] < len(r) and r[found["type"]]]
        if not values or sum(1 for v in values if kind_from_text(v)) < 0.6 * len(values):
            del found["type"]
    mapping = Mapping(header_row=header, **found)
    if mapping.date is not None:
        mapping.date_order = guess_date_order(r[mapping.date] for r in data if mapping.date < len(r))
    if mapping.amount is not None and mapping.type is None and mapping.debit is None and mapping.credit is None:
        signs = {parse_money(r[mapping.amount])[0] < 0 for r in data[:500]
                 if mapping.amount < len(r) and parse_money(r[mapping.amount])[0] not in (None, Decimal(0))}
        has_drcr = any(parse_money(r[mapping.amount])[1] for r in data[:500] if mapping.amount < len(r))
        if signs == {True, False} and not has_drcr:
            mapping.default_type = "SIGN"
    return mapping


# ---------- rows → records ----------
_EXPENSE_WORDS = {"expense", "expenses", "debit", "dr", "d", "withdrawal", "paid", "payment", "spent", "spend", "out",
                  "outflow", "purchase", "bill", "sent", "debited"}
_INCOME_WORDS = {"income", "credit", "cr", "c", "deposit", "received", "receipt", "salary", "in", "inflow", "refund",
                 "cashback", "credited", "earning", "earnings"}
_SPLIT_WORDS = {"split", "shared", "split bill", "group"}
_TRANSFER_WORDS = {"transfer", "contra", "self", "internal", "move", "moved"}
# An account's balance at the start of the records ("Balance cf", "Opening balance", "Balance B/F"…)
_OPENING_LINE = re.compile(r"\bopening\s+bal|\bbal(ance)?\s*(b/?f|c/?f|bf|cf|brought forward|carried forward)\b|"
                           r"\b(b/?f|c/?f|brought forward|carried forward)\b.*\bbal(ance)?\b", re.I)
# Not a transaction: totals and closing balances
_TOTAL_LINE = re.compile(r"^(grand )?total\b|\bsub ?total\b|\bclosing\s+bal|^balance$", re.I)
_ARROW = re.compile(r"\s*(?:->|→|=>|➔|>)\s*")


def kind_from_text(value: str) -> str | None:
    words = _norm(value)
    if not words:
        return None
    if words in _SPLIT_WORDS or "split" in words.split():
        return KIND_SPLIT
    if _TRANSFER_WORDS & set(words.split()):
        return KIND_TRANSFER
    if words in _EXPENSE_WORDS or words.split()[0] in _EXPENSE_WORDS:
        return KIND_EXPENSE
    if words in _INCOME_WORDS or words.split()[0] in _INCOME_WORDS:
        return KIND_INCOME
    return None


def parse_names(text: str) -> list[str]:
    names = [n.strip() for n in re.split(r",|;|/|&|\band\b|\+", text or "") if n.strip()]
    seen, out = set(), []
    for n in names:
        key = n.casefold()
        if key not in seen and key not in ("me", "myself", "i", "you", "self"):
            seen.add(key)
            out.append(n[:100])
    return out


def parse_records(rows: list[list[str]], m: Mapping, today: date) -> tuple[list[dict], int]:
    """Records from the rows under the heading row, and how many rows were ignored (blank or without a date or
    any amount: page headings, separators). Every record keeps its row number and the reasons it can't be imported."""
    if m.date is None:
        raise ValueError("Choose the column that holds the date")
    if m.amount is None and m.debit is None and m.credit is None:
        raise ValueError("Choose the amount column, or the debit and credit columns")

    def cell(row, col):
        return row[col].strip() if col is not None and col < len(row) and row[col] is not None else ""

    records, ignored = [], 0
    start = m.header_row + 1
    desc_col, note_col = m.description, m.note
    if desc_col is None and note_col is not None:  # notes are all there is: use them as the description
        desc_col, note_col = note_col, None
    for index, row in enumerate(rows[start:], start=start):
        raw_date = cell(row, m.date)
        money_cells = [cell(row, c) for c in (m.amount, m.debit, m.credit) if c is not None]
        if not raw_date and not any(money_cells):
            ignored += 1
            continue
        errors, warnings = [], []
        description = cell(row, desc_col)
        d = parse_date(raw_date, m.date_order) if raw_date else None
        if not raw_date:
            errors.append("Missing date")
        elif d is None:
            errors.append(f"Unrecognised date “{raw_date[:30]}”")
        elif d > today:
            errors.append("Date is in the future")

        kind, amount = None, None
        type_kind = kind_from_text(cell(row, m.type)) if m.type is not None else None
        unknown_type = bool(m.type is not None and cell(row, m.type) and type_kind is None)
        if unknown_type:
            errors.append(f"Unknown type “{cell(row, m.type)[:30]}” (expected expense, income, transfer or split)")
        if m.debit is not None or m.credit is not None:
            out_amt, _, e1 = parse_money(cell(row, m.debit))
            in_amt, _, e2 = parse_money(cell(row, m.credit))
            errors += [e for e in (e1, e2) if e]
            out_amt = abs(out_amt) if out_amt else None
            in_amt = abs(in_amt) if in_amt else None
            if out_amt and in_amt:
                errors.append("Has both a debit and a credit amount")
            elif out_amt:
                kind, amount = KIND_EXPENSE, out_amt
            elif in_amt:
                kind, amount = KIND_INCOME, in_amt
            if m.amount is not None and amount is None:  # some layouts also have a signed amount column
                value, side, err = parse_money(cell(row, m.amount))
                if value:
                    kind, amount = (KIND_EXPENSE if side == "DR" or value < 0 else KIND_INCOME), abs(value)
        else:
            value, side, err = parse_money(cell(row, m.amount))
            if value is None and m.default_type in (KIND_EXPENSE, KIND_INCOME):
                kind = m.default_type  # so typing the missing amount in the review is enough
            if err:
                errors.append(err)
            elif value is not None:
                amount = abs(value)
                if side:
                    kind = KIND_EXPENSE if side == "DR" else KIND_INCOME
                elif m.default_type == "SIGN":
                    kind = KIND_EXPENSE if value < 0 else KIND_INCOME
                elif m.default_type == KIND_INCOME:
                    kind = KIND_INCOME
                else:
                    kind = KIND_EXPENSE
        if type_kind:
            kind = type_kind
        elif unknown_type:
            kind = None  # never guessed: the user chooses it in the review

        account, to_account = cell(row, m.account), cell(row, m.to_account)
        if account and _ARROW.search(account):  # "Axis Bank->SBI Bank"
            parts = [p.strip() for p in _ARROW.split(account, maxsplit=1)]
            if len(parts) == 2 and all(parts):
                account, to_account = parts[0], to_account or parts[1]
                if not unknown_type and type_kind in (None, KIND_TRANSFER):
                    kind = KIND_TRANSFER
        if to_account and kind != KIND_TRANSFER and type_kind is None and not unknown_type:
            kind = KIND_TRANSFER
        if kind == KIND_TRANSFER and not (account and to_account):
            errors.append("A transfer needs the account it came from and the one it went to")
        if amount is None and not any("amount" in e or "decimal" in e or "debit" in e for e in errors):
            errors.append("Missing amount")
        elif amount is not None and amount == 0:
            errors.append("Amount is zero")
        elif amount is not None and amount > MAX_AMOUNT:
            errors.append("Amount is too large")

        my_share, split_with = None, parse_names(cell(row, m.split_with)) if m.split_with is not None else []
        if m.my_share is not None and cell(row, m.my_share):
            share, _, err = parse_money(cell(row, m.my_share))
            if err:
                errors.append(f"My share: {err}")
            elif share is not None:
                my_share = abs(share)
        if kind == KIND_EXPENSE and amount and (my_share is not None and my_share < amount or split_with and my_share is None):
            kind = KIND_SPLIT
        if kind == KIND_SPLIT and amount:
            if my_share is None:
                if split_with:  # an equal share with the people named
                    my_share = (amount / (len(split_with) + 1)).quantize(CENT)
                else:
                    errors.append("A split bill needs your share (map the “My share” column) or who it was split with")
            elif my_share > amount:
                errors.append("Your share is more than the amount paid")
        if kind == KIND_SPLIT and my_share is not None and amount and my_share == amount:
            kind = KIND_EXPENSE

        quantity = 1
        if m.quantity is not None and cell(row, m.quantity):
            q, _, _ = parse_money(cell(row, m.quantity))
            if q is not None and q == int(q) and 1 <= q <= 100_000:
                quantity = int(q)
            else:
                warnings.append(f"Quantity “{cell(row, m.quantity)[:20]}” isn't a whole number: 1 used")
        texts = [description, cell(row, m.category), cell(row, m.note)]
        if kind in (KIND_EXPENSE, KIND_INCOME) and any(_OPENING_LINE.search(t) for t in texts if t):
            if amount is not None and kind == KIND_EXPENSE:
                amount = -amount  # a balance owed (e.g. on a credit card)
            kind = KIND_OPENING
        elif _TOTAL_LINE.search(description) or (not description and _TOTAL_LINE.search(" ".join(row))):
            warnings.append("Looks like a balance or total line, not a transaction")

        records.append({
            "row_no": index + 1,
            "kind": kind,
            "date": d.isoformat() if d else None,
            "description": description[:300],
            "category": cell(row, m.category)[:100] or None,
            "amount": str(amount) if amount is not None else None,
            "my_share": str(my_share) if kind == KIND_SPLIT and my_share is not None else None,
            "split_with": split_with if kind == KIND_SPLIT else [],
            "account": account[:100] or None,
            "to_account": (to_account[:100] or None) if kind == KIND_TRANSFER else None,
            "note": cell(row, note_col)[:500] or None,
            "quantity": quantity,
            "parse_errors": errors,
            "parse_warnings": warnings,
        })
    _check_openings(records)
    return records, ignored


def _check_openings(records: list[dict]) -> None:
    """A balance line is an account's opening balance only when it comes before that account's transactions; one
    dated later (a closing balance) is flagged and left out instead."""
    first: dict[str, str] = {}
    for r in records:
        if r["kind"] not in (KIND_OPENING, None) and r["date"]:
            for acc in (r["account"], r["to_account"]):
                if acc:
                    key = _norm(acc)
                    first[key] = min(first.get(key, r["date"]), r["date"])
    for r in records:
        if r["kind"] != KIND_OPENING:
            continue
        if not r["account"]:
            r["parse_errors"].append("An opening balance needs its account")
        elif r["date"] and r["date"] > first.get(_norm(r["account"]), r["date"]):
            negative = bool(r["amount"] and r["amount"].startswith("-"))
            r["kind"] = KIND_EXPENSE if negative else KIND_INCOME
            r["amount"] = r["amount"].lstrip("-") if r["amount"] else r["amount"]
            r["parse_warnings"].append("Looks like a balance or total line, not a transaction")
