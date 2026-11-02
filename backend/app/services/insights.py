"""Month-by-month comparison of expenses, subscriptions and split bills, and plain-language insights from it."""

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select, true
from sqlalchemy.orm import Session

from ..models import (
    Budget,
    Expense,
    Fund,
    SplitBill,
    SplitGroup,
    SplitMember,
    SplitShare,
    Subscription,
    SubscriptionCharge,
)
from .budgets import label, month_bounds, status_of
from .splits import member_totals
from .subscriptions import monthly_cost

MONTHS = 6


def _shift(day: date, months: int) -> date:
    total = day.year * 12 + day.month - 1 + months
    return date(total // 12, total % 12 + 1, 1)


def _pct(now: Decimal, before: Decimal) -> float | None:
    """Change from `before` to `now` in percent, or None when there is nothing to compare with."""
    if not before:
        return None
    return round(float((now - before) / before * 100), 1)


def _f(v: Decimal) -> float:
    return float(round(v, 2))


def build(db: Session, user_id: int, today: date) -> dict:
    this_month = today.replace(day=1)
    first = _shift(this_month, -MONTHS)  # six full months before the current one
    keys = [_shift(this_month, -i) for i in range(MONTHS, -1, -1)]
    _, month_end = month_bounds(today)
    days_in_month = calendar.monthrange(today.year, today.month)[1]

    # ----- raw rows for the whole window -----
    expenses = db.execute(
        select(Expense.id, Expense.expense_date, Expense.category, Expense.item_name, Expense.amount).where(
            Expense.user_id == user_id, Expense.expense_date >= first, Expense.expense_date < month_end
        )
    ).all()
    charges = db.execute(
        select(SubscriptionCharge.charge_date, SubscriptionCharge.amount, SubscriptionCharge.expense_id)
        .join(Subscription, Subscription.id == SubscriptionCharge.subscription_id)
        .where(Subscription.user_id == user_id, SubscriptionCharge.charge_date >= first, SubscriptionCharge.charge_date < month_end)
    ).all()
    shares = db.execute(
        select(SplitBill.bill_date, SplitShare.amount)
        .join(SplitShare, SplitShare.bill_id == SplitBill.id)
        .join(SplitMember, SplitMember.id == SplitShare.member_id)
        .where(SplitMember.user_id == user_id, SplitBill.bill_date >= first, SplitBill.bill_date < month_end)
    ).all()
    funds = db.execute(
        select(Fund.fund_date, Fund.amount).where(Fund.user_id == user_id, Fund.fund_date >= first, Fund.fund_date < month_end)
    ).all()

    # Subscription charges logged as expenses are counted once, under subscriptions
    logged = {c.expense_id for c in charges if c.expense_id}
    zero = lambda: {"expenses": Decimal(0), "subscriptions": Decimal(0), "splits": Decimal(0), "funds": Decimal(0)}  # noqa: E731
    month: dict[date, dict] = {k: zero() for k in keys}
    to_date: dict[date, dict] = {k: zero() for k in keys}  # only days 1..today.day of each month, for fair comparison
    cats: dict[date, dict[str, Decimal]] = {k: defaultdict(Decimal) for k in keys}
    items: dict[date, dict[str, Decimal]] = {k: defaultdict(Decimal) for k in keys}

    def add(section: str, day: date, amount):
        k = day.replace(day=1)
        month[k][section] += Decimal(amount)
        if day.day <= today.day:
            to_date[k][section] += Decimal(amount)

    for e in expenses:
        k = e.expense_date.replace(day=1)
        cats[k][e.category] += Decimal(e.amount)
        items[k][e.item_name] += Decimal(e.amount)
        if e.id not in logged:
            add("expenses", e.expense_date, e.amount)
    for c in charges:
        add("subscriptions", c.charge_date, c.amount)
        if not c.expense_id:
            cats[c.charge_date.replace(day=1)]["Subscriptions"] += Decimal(c.amount)
    for s in shares:
        add("splits", s.bill_date, s.amount)
        cats[s.bill_date.replace(day=1)]["Split bills"] += Decimal(s.amount)
    for f in funds:
        add("funds", f.fund_date, f.amount)

    def total(row):
        return row["expenses"] + row["subscriptions"] + row["splits"]

    previous = keys[:-1]
    last = keys[-2]
    cur = month[this_month]

    months = [
        {
            "month": k.isoformat(), "label": f"{k:%b %y}", "current": k == this_month,
            **{s: _f(v) for s, v in month[k].items()}, "total": _f(total(month[k])),
        }
        for k in keys
    ]

    # ----- sections: this month vs last month (to date and full) vs the six-month average -----
    sections = {}
    for s in ("expenses", "subscriptions", "splits", "funds"):
        avg = sum((month[k][s] for k in previous), Decimal(0)) / MONTHS
        sections[s] = {
            "this_month": _f(cur[s]),
            "last_month": _f(month[last][s]),
            "last_month_to_date": _f(to_date[last][s]),
            "avg_6m": _f(avg),
            "vs_last_month": _pct(cur[s], to_date[last][s]),
            # Compared with the average prorated to today, since the month isn't over
            "vs_avg": _pct(cur[s], avg * today.day / days_in_month),
        }
    spent_now = total(cur)
    spent_last_td = total(to_date[last])
    avg_total = sum((total(month[k]) for k in previous), Decimal(0)) / MONTHS
    projected = spent_now / today.day * days_in_month if today.day else spent_now
    sections["total"] = {
        "this_month": _f(spent_now),
        "last_month": _f(total(month[last])),
        "last_month_to_date": _f(spent_last_td),
        "avg_6m": _f(avg_total),
        "vs_last_month": _pct(spent_now, spent_last_td),
        "projected": _f(projected),
        "vs_avg": _pct(projected, avg_total),
    }

    # ----- categories -----
    names = set(cats[this_month]) | set(cats[last])
    categories = []
    for name in names:
        now_v, last_v = cats[this_month].get(name, Decimal(0)), cats[last].get(name, Decimal(0))
        avg = sum((cats[k].get(name, Decimal(0)) for k in previous), Decimal(0)) / MONTHS
        categories.append({
            "category": name, "this_month": _f(now_v), "last_month": _f(last_v), "avg_6m": _f(avg),
            "change": _f(now_v - last_v), "change_pct": _pct(now_v, last_v),
            "trend": [_f(cats[k].get(name, Decimal(0))) for k in keys],
        })
    categories.sort(key=lambda c: -c["this_month"])

    # ----- subscriptions -----
    subs = db.scalars(select(Subscription).where(Subscription.user_id == user_id)).all()
    active = [s for s in subs if s.is_active]
    upcoming = sorted(
        (s for s in active if s.next_charge_date and s.next_charge_date <= today + timedelta(days=30)),
        key=lambda s: s.next_charge_date,
    )
    subscriptions = {
        "active": len(active),
        "paused": len(subs) - len(active),
        "monthly_total": _f(sum((monthly_cost(s) for s in active), Decimal(0))),
        "due_7_days": len([s for s in upcoming if s.next_charge_date <= today + timedelta(days=7)]),
        "upcoming": [
            {"id": s.id, "name": s.name, "amount": _f(s.amount), "date": s.next_charge_date.isoformat(),
             "days_until": (s.next_charge_date - today).days, "frequency": s.frequency, "payment_method": s.payment_method,
             "category": s.category}
            for s in upcoming[:6]
        ],
    }

    # ----- split bills: what the user owes / is owed across groups -----
    owe = owed = Decimal(0)
    groups = db.scalars(
        select(SplitGroup).join(SplitMember, SplitMember.group_id == SplitGroup.id).where(SplitMember.user_id == user_id)
    ).unique().all()
    for g in groups:
        me = next((m for m in g.members if m.user_id == user_id), None)
        net = member_totals(g).get(me.id, {}).get("net", Decimal(0)) if me else Decimal(0)
        if net > 0:
            owed += net
        else:
            owe -= net
    splits = {"groups": len(groups), "you_owe": _f(owe), "owed_to_you": _f(owed)}

    # ----- budgets -----
    budgets = []
    for b in db.scalars(select(Budget).where(Budget.user_id == user_id, Budget.is_active == true())):
        st = status_of(db, b, today)
        budgets.append({
            "id": b.id, "scope": b.scope, "target": b.target, "label": label(b), "monthly_limit": _f(b.monthly_limit),
            "alert_percent": b.alert_percent, "spent": _f(st["spent"]), "percent": st["percent"], "level": st["level"],
        })
    budgets.sort(key=lambda b: (b["scope"] != "OVERALL", -b["percent"]))

    return {
        "today": today.isoformat(),
        "day_of_month": today.day,
        "days_in_month": days_in_month,
        "months": months,
        "sections": sections,
        "categories": categories,
        "subscriptions": subscriptions,
        "splits": splits,
        "budgets": budgets,
        "insights": _insights(sections, categories, subscriptions, splits, budgets),
    }


def _money(v: float) -> str:
    return f"₹{v:,.0f}"


def _insights(sections, categories, subscriptions, splits, budgets) -> list[dict]:
    """Short sentences about what changed. `tone` is good, bad or neutral."""
    out = []
    t = sections["total"]
    if t["vs_last_month"] is not None:
        up = t["vs_last_month"] > 0
        out.append({
            "tone": "bad" if up else "good",
            "text": f"You've spent {_money(t['this_month'])} so far this month: {abs(t['vs_last_month']):.0f}% "
                    f"{'more' if up else 'less'} than by this day last month ({_money(t['last_month_to_date'])}).",
        })
    elif t["this_month"]:
        out.append({"tone": "neutral", "text": f"You've spent {_money(t['this_month'])} so far this month."})
    if t["avg_6m"] and t["this_month"]:
        diff = t["projected"] - t["avg_6m"]
        out.append({
            "tone": "bad" if diff > 0 else "good",
            "text": f"At this pace the month ends near {_money(t['projected'])}, "
                    f"{_money(abs(diff))} {'above' if diff > 0 else 'below'} your 6-month average of {_money(t['avg_6m'])}.",
        })
    changed = [c for c in categories if c["change_pct"] is not None and abs(c["change"]) >= 100]
    if changed:
        worst = max(changed, key=lambda c: c["change"])
        if worst["change"] > 0:
            out.append({"tone": "bad", "text": f"{worst['category']} is up {worst['change_pct']:.0f}% on last month "
                                               f"({_money(worst['last_month'])} → {_money(worst['this_month'])})."})
        best = min(changed, key=lambda c: c["change"])
        if best["change"] < 0:
            out.append({"tone": "good", "text": f"{best['category']} is down {abs(best['change_pct']):.0f}%: "
                                                f"{_money(abs(best['change']))} saved compared with last month."})
    for b in budgets:
        if b["level"] == "OVER":
            out.append({"tone": "bad", "text": f"{b['label']} is over budget: {_money(b['spent'])} of {_money(b['monthly_limit'])}."})
        elif b["level"] == "WARN":
            out.append({"tone": "bad", "text": f"{b['label']} has used {b['percent']:.0f}% of its {_money(b['monthly_limit'])} budget."})
    if subscriptions["active"]:
        out.append({"tone": "neutral", "text": f"{subscriptions['active']} active subscription(s) cost about "
                                               f"{_money(subscriptions['monthly_total'])} a month ({_money(subscriptions['monthly_total'] * 12)} a year)."})
    if splits["you_owe"]:
        out.append({"tone": "bad", "text": f"You owe {_money(splits['you_owe'])} in split bills."})
    if splits["owed_to_you"]:
        out.append({"tone": "good", "text": f"Friends owe you {_money(splits['owed_to_you'])} in split bills."})
    return out
