"""Check a bank statement against its own figures, so a misread or missing row can't slip through.

A statement says what it holds: an opening and a closing balance, the total debits and credits (often with their
counts), and a running balance on every row. `statement_summary` reads those figures from the lines that aren't
transactions (a "TRANSACTION TOTAL" row, an "Opening Balance" line, or a summary block of labels with the values on
the row below). `reconcile` then compares them with the records that were read:

- total debits / credits and their counts, as stated vs as read;
- opening balance + credits - debits = closing balance;
- each row's balance = the previous row's balance +/- its amount (oldest-first or newest-first, whichever the file
  is), which points at the exact row that was misread or is missing.

Amounts are compared exactly (to the paisa).
"""

import re
from decimal import Decimal

from .models import KIND_EXPENSE, KIND_INCOME, KIND_OPENING, KIND_SPLIT, KIND_TRANSFER
from .parse import Mapping, parse_date, parse_money

MAX_ROW_ISSUES = 50
# Label → figure. Counts come before totals ("Dr Count" isn't a debit total); "total" alone is read from the
# debit and credit columns.
_LABELS = (
    ("opening", re.compile(r"opening\s*bal|\bbal(ance)?\s*(b\s*/?\s*f\b|brought\s*forward)|^brought\s*forward", re.I)),
    ("closing", re.compile(r"closing\s*bal|\bbal(ance)?\s*(c\s*/?\s*f\b|carried\s*forward)|^carried\s*forward", re.I)),
    ("debit_count", re.compile(r"\b(dr|debit|withdrawal)s?\.?\s*(count|nos?\b|number)|no\.?\s*of\s*(dr|debit|withdrawal)s?\b", re.I)),
    ("credit_count", re.compile(r"\b(cr|credit|deposit)s?\.?\s*(count|nos?\b|number)|no\.?\s*of\s*(cr|credit|deposit)s?\b", re.I)),
    ("debits", re.compile(r"total\s*(debit|withdrawal|dr\b)|(debit|withdrawal)s?\s*(total|amount)|^(debits?|withdrawals?|dr)$", re.I)),
    ("credits", re.compile(r"total\s*(credit|deposit|cr\b)|(credit|deposit)s?\s*(total|amount)|^(credits?|deposits?|cr)$", re.I)),
    ("total", re.compile(r"\btotals?\b", re.I)),
)
NAMES = {
    "debits": "Total money out (debits)", "credits": "Total money in (credits)",
    "debit_count": "Number of debits", "credit_count": "Number of credits",
    "closing": "Opening balance + money in - money out = closing balance",
}


def _label(cell: str) -> str | None:
    text = " ".join(str(cell or "").split())
    if not text or len(text) > 60 or not re.search(r"[a-z]", text, re.I):
        return None
    for key, rx in _LABELS:
        if rx.search(text):
            return key
    return None


def _money(cell, balance: bool = False) -> Decimal | None:
    """An amount in a cell ("1,23,456.50", "₹ 500 Cr", "Opening Balance: 10,000.00"); a balance marked Dr is negative."""
    text = str(cell or "").strip()
    if not text or parse_date(text):
        return None
    value, side, err = parse_money(text)
    if err:  # a label and an amount in one cell: the last amount in it
        found = re.findall(r"-?\(?\s*(?:₹|rs\.?|inr)?\s*\d[\d,]*(?:\.\d{1,2})?\)?\s*(?:dr|cr)?\.?\s*$", text, re.I)
        if not found:
            return None
        value, side, err = parse_money(found[-1].strip())
        if err:
            return None
    if balance and side == "DR":
        value = -abs(value)
    return value


def _count(cell) -> int | None:
    text = str(cell or "").strip().replace(",", "")
    m = re.search(r"(\d+)\s*$", text)
    return int(m.group(1)) if m and "." not in text[m.start():] else None


def statement_summary(rows: list[list[str]], m: Mapping, extra: list[list[list[str]]] = ()) -> dict:
    """The statement's own figures, from the rows that aren't transactions (and from the file's other tables)."""
    found: dict = {}

    def put(key: str, cell, from_column: bool = False):
        if key in found:
            return
        if key.endswith("_count"):
            value = _count(cell)
        else:
            value = _money(cell, balance=key in ("opening", "closing"))
            if value is not None and key in ("debits", "credits"):
                value = abs(value)
        if value is not None:
            found[key] = value

    def scan(block: list[list[str]], transactions: bool):
        consumed = set()
        for i, row in enumerate(block):
            if i in consumed:
                continue
            if transactions and m.date is not None and m.date < len(row) and parse_date(row[m.date]):
                continue  # a transaction
            labels = {j: k for j, c in enumerate(row) if (k := _label(c)) and _money(c) is None}
            # A summary block: a row of labels, the values on the row below
            nxt = block[i + 1] if i + 1 < len(block) else None
            if len(labels) >= 2 and nxt and sum(1 for j in labels if j < len(nxt) and _money(nxt[j]) is not None) >= 2:
                for j, k in labels.items():
                    if j < len(nxt) and k != "total":
                        put(k, nxt[j])
                consumed.add(i + 1)
                continue
            for j, c in enumerate(row):
                k = _label(c)
                if not k:
                    continue
                if k == "total":
                    if transactions and m.debit is not None and m.credit is not None:
                        put("debits", row[m.debit] if m.debit < len(row) else "")
                        put("credits", row[m.credit] if m.credit < len(row) else "")
                    continue
                if k in ("opening", "closing") and transactions and m.balance is not None and m.balance < len(row) \
                        and _money(row[m.balance]) is not None:
                    put(k, row[m.balance])
                    continue
                inline = _money(c, balance=k in ("opening", "closing"))
                if inline is not None:  # "Opening Balance : 10,000.00" in one cell
                    put(k, c)
                    continue
                for c2 in row[j + 1:]:  # the next value to the right, before another label
                    if _label(c2) and _money(c2) is None:
                        break
                    if (k.endswith("_count") and _count(c2) is not None) or _money(c2) is not None:
                        put(k, c2)
                        break

    scan(rows[: max(0, m.header_row)], transactions=False)
    scan(rows[max(0, m.header_row + 1):], transactions=True)
    for block in extra:
        scan(block, transactions=False)
    return {k: str(v) for k, v in found.items()}


def _fmt(v) -> str:
    return f"₹{Decimal(v):,.2f}" if not isinstance(v, int) else str(v)


def reconcile(records: list, summary: dict | None) -> dict:
    """Compare the records read (dicts or RecordIn) with the statement's figures and its running balance."""
    summary = summary or {}
    get = (lambda r, k: r.get(k)) if records and isinstance(records[0], dict) else (lambda r, k: getattr(r, k, None))

    def dec(v):
        return Decimal(str(v)) if v not in (None, "") else None

    rows, unread, opening_rows = [], [], []
    for r in records:
        if any("balance or total" in w for w in (get(r, "parse_warnings") or [])):
            continue  # a total line kept by mistake would count twice
        kind, amount = get(r, "kind"), dec(get(r, "amount"))
        if kind == KIND_OPENING:
            opening_rows.append(amount)
            continue
        if amount is None or kind not in (KIND_EXPENSE, KIND_SPLIT, KIND_INCOME, KIND_TRANSFER):
            unread.append(get(r, "row_no"))
        out = amount if kind in (KIND_EXPENSE, KIND_SPLIT) and amount is not None else Decimal(0)
        inn = amount if kind == KIND_INCOME and amount is not None else Decimal(0)
        rows.append({"row_no": get(r, "row_no"), "out": out, "in": inn, "balance": dec(get(r, "balance")),
                     "transfer": kind == KIND_TRANSFER})

    total_out = sum((x["out"] for x in rows), Decimal(0))
    total_in = sum((x["in"] for x in rows), Decimal(0))
    n_out = sum(1 for x in rows if x["out"])
    n_in = sum(1 for x in rows if x["in"])
    stated = {k: (int(v) if k.endswith("_count") else Decimal(v)) for k, v in summary.items()}
    if "opening" not in stated and len(opening_rows) == 1 and opening_rows[0] is not None:
        stated["opening"] = opening_rows[0]

    checks = []

    def add(key, statement, read):
        checks.append({"key": key, "label": NAMES[key], "statement": _fmt(statement), "read": _fmt(read),
                       "difference": None if statement == read else _fmt(read - statement), "ok": statement == read})

    if "debits" in stated:
        add("debits", stated["debits"], total_out)
    if "credits" in stated:
        add("credits", stated["credits"], total_in)
    if "debit_count" in stated:
        add("debit_count", stated["debit_count"], n_out)
    if "credit_count" in stated:
        add("credit_count", stated["credit_count"], n_in)
    if "opening" in stated and "closing" in stated:
        add("closing", stated["closing"], stated["opening"] + total_in - total_out)

    # The running balance, row by row (whichever order the file is in)
    issues, order, verified = [], None, 0
    with_balance = [x for x in rows if x["balance"] is not None]
    if len(with_balance) >= 2:
        def walk(seq, start):
            found, ok, prev_row, running = [], 0, None, start
            for x in seq:
                if running is not None:
                    running += x["in"] - x["out"]
                if x["balance"] is not None:
                    if running is not None and not x["transfer"]:
                        if running == x["balance"]:
                            ok += 1
                        else:
                            found.append({
                                "row_no": x["row_no"],
                                "message": f"Balance {_fmt(x['balance'])} doesn't follow from "
                                           + (f"row {prev_row}" if prev_row else "the opening balance")
                                           + f": {_fmt(running)} expected. A row near it was misread or is missing.",
                            })
                    running, prev_row = x["balance"], x["row_no"]
            return found, ok

        fwd = walk(rows, stated.get("opening"))
        back = walk(list(reversed(rows)), stated.get("opening"))
        (issues, verified), order = (fwd, "oldest first") if len(fwd[0]) <= len(back[0]) else (back, "newest first")
        # The last balance read must be the closing balance
        seq = rows if order == "oldest first" else list(reversed(rows))
        last = next((x for x in reversed(seq) if x["balance"] is not None), None)
        if "closing" in stated and last and last["balance"] != stated["closing"]:
            issues.append({"row_no": last["row_no"], "message": f"The last balance read, {_fmt(last['balance'])}, isn't the "
                                                                 f"statement's closing balance {_fmt(stated['closing'])}"})

    for n in unread:
        issues.append({"row_no": n, "message": "No amount or type was read for this row"})
    issues.sort(key=lambda i: i["row_no"] or 0)
    available = bool(checks or len(with_balance) >= 2)
    return {
        "available": available,
        "ok": available and all(c["ok"] for c in checks) and not issues,
        "checks": checks,
        "row_issues": issues[:MAX_ROW_ISSUES],
        "row_issue_count": len(issues),
        "balance_order": order,
        "balances_verified": verified,
        "rows_with_balance": len(with_balance),
        "read": {"debits": _fmt(total_out), "credits": _fmt(total_in), "debit_count": n_out, "credit_count": n_in},
        "statement": {k: _fmt(v) for k, v in stated.items()},
    }
