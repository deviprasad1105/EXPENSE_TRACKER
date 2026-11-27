"""Shops and people a split group pays together (split_payees): a payment to one of them, read by auto-tracking,
asks the payer "Split this with <group>?" (the pop-up in the app and on the site).

Only the payer's own payments match their own payees (in groups they are still in), only money going out, and
never transfers (card bills, ATM). Answering never changes the expense in their records.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import SPLIT_ASK, BankMessage, SplitMember, SplitPayee, User
from .bank_sms import CH_ATM, CH_CARD_BILL, DEBIT


def normal_vpa(vpa: str | None) -> str | None:
    vpa = (vpa or "").strip().lower()
    return vpa or None


def _has_word(text: str, word: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(word.casefold())}(?![a-z0-9])", text.casefold()))


def find_payee(db: Session, user: User, msg: BankMessage) -> SplitPayee | None:
    """The user's payee this payment went to: by UPI ID first, else by a word in the payee's name."""
    if msg.direction != DEBIT or msg.channel in (CH_CARD_BILL, CH_ATM) or not msg.amount:
        return None
    payees = db.scalars(
        select(SplitPayee).join(SplitMember, (SplitMember.group_id == SplitPayee.group_id) & (SplitMember.user_id == user.id))
        .where(SplitPayee.user_id == user.id, SplitPayee.is_active.is_(True), SplitMember.left_at.is_(None))
        .order_by(SplitPayee.id)
    ).all()
    vpa = normal_vpa(msg.vpa)
    if vpa:
        hit = next((p for p in payees if p.vpa and p.vpa == vpa), None)
        if hit:
            return hit
    text = " ".join(x for x in (msg.party, msg.name, msg.body) if x)
    return next((p for p in payees if p.name_match and _has_word(text, p.name_match)), None)


def attach(db: Session, user: User, msg: BankMessage) -> None:
    """Mark a payment to one of the user's split payees so the pop-up asks about it."""
    payee = find_payee(db, user, msg)
    if payee:
        msg.split_payee_id, msg.split_status = payee.id, SPLIT_ASK
