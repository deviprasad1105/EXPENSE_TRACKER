"""Read a bank message (SMS or app notification) into a transaction, a bill that is due, or a reason to skip it.

Indian banks word their alerts in a handful of ways ("Rs 250.00 debited from A/c XX1234 on 12-Oct-26 to VPA …",
"INR 1,299 spent on HDFC Bank Card XX9876 at AMAZON on …", "Rs.5000 credited to your a/c … by NEFT from ACME LTD",
"Your card statement: Total due Rs 3,324.51, min due Rs 170, due by 05-Nov"). Rather than one pattern per bank, this
looks for the parts every alert has: the amount, which way the money went, the account or card's last digits, the other
party, the reference number, the balance after it, and the date. Anything that isn't a completed transaction (one-time
passwords, offers, payment requests, failed payments, balance-only updates) is skipped with the reason.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .aliases import UPI_PATH_RE, VPA_RE

DEBIT = "DEBIT"
CREDIT = "CREDIT"

# What a message is about
KIND_TXN = "TXN"          # money moved: an expense, money received, or a transfer between own accounts
KIND_BILL = "BILL"        # a card / utility bill is due

# How the money moved
CH_UPI, CH_CARD, CH_ATM, CH_BANK, CH_CARD_BILL, CH_AUTOPAY = "UPI", "CARD", "ATM", "BANK", "CARD_BILL", "AUTOPAY"

_AMT = r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)"
_AMOUNT = re.compile(_AMT, re.I)
# "debited by 120.0" (some banks leave out Rs)
_AMOUNT_VERB = re.compile(r"(?:debited|credited|sent|paid|received|spent|withdrawn|deducted)\s+(?:by|for|with|of)?\s*([\d,]+(?:\.\d{1,2})?)\b", re.I)
# The amount after these words is a balance, a limit or a due amount, not the transaction
_NOT_TXN_AMOUNT = re.compile(
    r"(?:bal(?:ance)?|avl|avbl|available|limit|outstanding|total\s+(?:amt|amount)?\s*due|min(?:imum)?\.?\s*(?:amt|amount)?\s*due"
    r"|due\s+amount|amount\s+due)[\s.:-]*(?:is|of|:)?\s*$", re.I)
_BALANCE = re.compile(r"(?:avl|avbl|available|a/c|clear|closing|total)?\s*bal(?:ance)?[\s.:-]*(?:is|of|:)?\s*(?:rs\.?|inr|₹)\s*(-?[\d,]+(?:\.\d{1,2})?)", re.I)
_TOTAL_DUE = re.compile(r"(?:total\s+(?:amt\.?|amount)?\s*due|amount\s+due|total\s+outstanding|stmt\s+amt|statement\s+amount|bill\s+amount|bill\s+of)[\s.:-]*(?:is|of|:)?\s*" + _AMT, re.I)
_MIN_DUE = re.compile(r"min(?:imum)?\.?\s*(?:amt\.?|amount)?\s*due[\s.:-]*(?:is|of|:)?\s*" + _AMT, re.I)
_DUE_DATE = re.compile(r"(?:due\s*(?:date|on|by)?|pay\s+by|payable\s+by|by)[\s.:-]*(?:is|on)?\s*(\d{1,2}[-/.\s]?(?:\d{1,2}|[A-Za-z]{3,9})[-/.\s,]*\d{2,4})", re.I)

_OTP = re.compile(r"\b(otp|one[\s-]?time\s+password|verification\s+code|passcode|password|cvv|mpin|activation\s+code)\b", re.I)
_REQUEST = re.compile(r"\b(has\s+requested|requested\s+(?:money|rs|inr|₹)|collect\s+request|payment\s+request|request\s+(?:of|for)\s+(?:rs|inr|₹))", re.I)
_FAILED = re.compile(r"\b(failed|declined|unsuccessful|could\s+not\s+be\s+processed|was\s+not\s+successful|insufficient)\b", re.I)
_PROMO = re.compile(
    r"\b(offer|pre[\s-]?approved|eligible|apply\s+now|get\s+up\s*to|win|lucky|congratulations|instant\s+loan|limit\s+increase"
    r"|upgrade|reward\s+points?\s+(?:worth|expir)|click|download|t&c|cashback\s+up\s*to|emi\s+offer|voucher)\b", re.I)
_DEBIT = re.compile(
    r"\b(debited|debit(?:ed)?\s+(?:by|for|with)|spent|withdrawn|withdrawal|paid|sent|purchase|used\s+(?:for|at)|txn\s+of|transaction\s+of"
    r"|transferred\s+to|payment\s+of|charged|deducted|auto[\s-]?debit|autopay|mandate\s+executed)\b", re.I)
_CREDIT = re.compile(r"\b(credited|received|deposited|refund(?:ed)?|reversed|reversal|cashback\s+of|added\s+to\s+your)\b", re.I)
_LAST4 = re.compile(
    r"(?:a/?c|acct|account|card|ac)\s*(?:no\.?|number|num)?\s*(?:ending(?:\s+(?:with|in))?)?\s*[:#-]?\s*[*xX.\s]*(\d{3,6})\b"
    r"|ending\s+(?:with|in)?\s*[*xX]*(\d{4})\b", re.I)
_REF = re.compile(
    r"(?:ref(?:erence)?\.?\s*(?:no|num|number|id)?\.?|refno|rrn|utr(?:\s*no)?|upi\s*(?:ref|txn|transaction)?\s*(?:no|id)?\.?"
    r"|txn\s*(?:id|no|ref)?\.?|transaction\s+(?:id|ref(?:erence)?)\s*(?:no)?\.?|imps\s*ref)[\s:#.-]*([A-Za-z0-9]{6,22})", re.I)
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_DATE_NUM = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b")
_DATE_ISO = re.compile(r"\b(20\d\d)[-/.](\d{1,2})[-/.](\d{1,2})\b")
_DATE_MON = re.compile(r"\b(\d{1,2})[-\s]?([A-Za-z]{3})[a-z]*[-\s,]*'?(\d{2,4})\b")
_NAME_END = (r"(?=\s+(?:on|ref|refno|upi|via|using|at|dated|avl|avbl|bal|not|if|for|from|with|thru|through|info|txn|is|has|-)\b"
             r"|\s*[.;:(\n,]|\s*$)")
_AT = re.compile(r"\b(?:at|@)\s+([A-Za-z0-9][\w .&'*/-]{1,60}?)" + _NAME_END, re.I)
_INFO = re.compile(r"\b(?:info|merchant|towards)[\s:.-]+([A-Za-z0-9][\w .&'*/-]{1,60}?)" + _NAME_END, re.I)
_TO = re.compile(r"\b(?:to|trf\s+to|transfer\s+to|paid\s+to|sent\s+to)\s+(?:vpa\s+|a/c\s+)?([A-Za-z][\w .&'@-]{1,60}?)" + _NAME_END, re.I)
_FROM = re.compile(r"\b(?:from|by|transfer\s+from|received\s+from|sent\s+by)\s+(?:vpa\s+|a/c\s+)?([A-Za-z][\w .&'@-]{1,60}?)" + _NAME_END, re.I)
_SEMI_CREDITED = re.compile(r";\s*([A-Za-z][\w .&'-]{1,60}?)\s+credited", re.I)
# "from HDFC Bank A/c", "to your a/c", "by NEFT": the user's own bank or the channel, not the other party
_NOT_A_PARTY = re.compile(
    r"^(?:your|a/?c|ac|acct|account|card|credit\s+card|debit\s+card|bank|\w+\s+bank\b|(?:rs|inr)\b|neft|imps|rtgs|upi|net\s*banking"
    r"|atm|pos|cash|mobile|the|a|an|self|xx|\d)(?:\b|$)", re.I)
# Where a bank's own footer starts after the payee in a UPI narration ("SWIGGY LIMITED. Not you? SMS BLOCKUPI…")
_FOOTER = re.compile(r"\.\s|\s+(?:not\s+you|if\s+not|call|sms\s|-\s*\w+\s+bank|avl|bal)\b", re.I)
_CARD_BILL_DEBIT = re.compile(r"\b(credit\s*card\s+(?:bill|payment|dues?)|towards\s+(?:your\s+)?(?:\w+\s+)?credit\s*card|cc\s+(?:bill|payment)|card\s+bill\s+payment|billdesk.*card|cred\s+club)\b", re.I)
_CARD_PAYMENT_RECEIVED = re.compile(r"\b(payment\s+(?:of\s+(?:rs\.?|inr|₹)\s*[\d,.]+\s+)?(?:has\s+been\s+)?(?:received|credited)\s+(?:towards|on|to|for|against)\s+(?:your\s+)?(?:\w+\s+)?(?:credit\s+)?card|received\s+(?:towards|against)\s+your\s+(?:\w+\s+)?card)", re.I)
_CARD = re.compile(
    r"\b(credit\s+card|debit\s+card|card\s+(?:no\.?\s*)?[xX*]*\d{4}\b|card\s+ending|\bcard\b.*\b(?:spent|used|txn|transaction|purchase)"
    r"|\bspent\b.*\bcard\b)", re.I)
# "towards Metal One credit card bill": the card being paid
_CARD_NAME = re.compile(r"\b(?:towards|for|to)\s+(?:your\s+)?([A-Za-z][\w ]{1,30}?)\s+credit\s*card", re.I)
_ATM = re.compile(r"\b(atm|cash\s+withdrawal|withdrawn\s+at|cash\s+wdl|cwdr)\b", re.I)
_UPI = re.compile(r"\b(upi|vpa|bhim|gpay|phonepe|paytm)\b", re.I)
_AUTOPAY = re.compile(r"\b(auto[\s-]?pay|auto[\s-]?debit|mandate|si\s+executed|standing\s+instruction|nach|ecs)\b", re.I)
_SALARY = re.compile(r"\b(salary|sal\s+for|payroll)\b", re.I)
_INTEREST = re.compile(r"\b(interest|int\.?\s+pd|int\s+credit)\b", re.I)
_REFUND = re.compile(r"\b(refund(?:ed)?|reversed|reversal|cashback)\b", re.I)
_BILL = re.compile(r"\b(statement|total\s+(?:amt\.?|amount)?\s*due|min(?:imum)?\.?\s*(?:amt\.?|amount)?\s*due|amount\s+due|bill\s+(?:is\s+)?(?:generated|due)|due\s+(?:date|on|by)|payment\s+due|pay\s+by)\b", re.I)

# DLT sender headers: "AD-HDFCBK", "VM-SBIUPI-S", "JD-AXISBK-T", or the bare code
_SENDER = re.compile(r"^(?:[A-Z]{2}-)?([A-Z0-9]{3,9})(?:-[A-Z])?$")
BANKS = {
    "AXISBK": "Axis Bank", "AXISCR": "Axis Bank", "AXISBN": "Axis Bank",
    "SBIINB": "SBI", "SBIUPI": "SBI", "SBICRD": "SBI Card", "SBMSMS": "SBI", "ATMSBI": "SBI", "SBYONO": "SBI", "CBSSBI": "SBI",
    "HDFCBK": "HDFC Bank", "HDFCCC": "HDFC Bank", "ICICIB": "ICICI Bank", "ICICIT": "ICICI Bank", "KOTAKB": "Kotak",
    "KMBANK": "Kotak", "HSBCIN": "HSBC", "HSBCBK": "HSBC", "HSBCCC": "HSBC", "SCAPIA": "Scapia", "SCPIAA": "Scapia",
    "IDFCFB": "IDFC FIRST", "YESBNK": "Yes Bank", "YESBKK": "Yes Bank", "INDUSB": "IndusInd", "PNBSMS": "PNB",
    "BOIIND": "Bank of India", "CANBNK": "Canara Bank", "UNIONB": "Union Bank", "BOBTXN": "Bank of Baroda",
    "BOBSMS": "Bank of Baroda", "FEDBNK": "Federal Bank", "AUBANK": "AU Bank", "ONECRD": "OneCard", "AMEXIN": "Amex",
    "CITIBK": "Citi", "SCBANK": "Standard Chartered", "PAYTMB": "Paytm", "AIRBNK": "Airtel Payments Bank",
    "JUPITR": "Jupiter", "FIMONY": "Fi", "SLICEIT": "slice", "RBLBNK": "RBL Bank", "RBLCRD": "RBL Bank", "IDBIBK": "IDBI",
    "BANDHN": "Bandhan Bank", "CENTBK": "Central Bank", "IOBCHN": "Indian Overseas Bank", "INDBNK": "Indian Bank",
    "UCOBNK": "UCO Bank", "BOMBNK": "Bank of Maharashtra", "DBSBNK": "DBS", "EQUTAS": "Equitas", "UJJIVN": "Ujjivan",
    "KVBANK": "Karur Vysya", "CSBBNK": "CSB Bank", "SIBSMS": "South Indian Bank", "TMBANK": "TMB",
    "FASTAG": "FASTag", "IHMCL": "FASTag", "NETCFT": "FASTag",
}


class NotATransaction(ValueError):
    """The message isn't about money that moved (an OTP, an offer, a balance update…): skipped quietly."""


class Unreadable(ValueError):
    """It looks like a transaction, but a part of it couldn't be read: shown to the user to add by hand."""


@dataclass
class ParsedBank:
    kind: str
    amount: Decimal
    direction: str | None = None
    channel: str | None = None
    bank: str | None = None
    last4: str | None = None
    party: str | None = None
    vpa: str | None = None
    ref: str | None = None
    balance: Decimal | None = None
    txn_date: date | None = None
    due_date: date | None = None
    min_due: Decimal | None = None
    is_credit_card: bool = False
    # The card a bill payment went to, as the bank names it ("Metal One")
    card_name: str | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """What the record is called before any alias."""
        if self.party:
            return self.party[:100]
        if self.channel == CH_ATM:
            return "ATM withdrawal"
        if self.channel == CH_CARD_BILL:
            return "Credit card bill payment"
        word = {CH_UPI: "UPI", CH_CARD: "Card", CH_AUTOPAY: "Auto-debit"}.get(self.channel or "", "Bank")
        return f"{word} {'payment' if self.direction == DEBIT else 'credit'}"


def bank_for_sender(sender: str | None) -> str | None:
    if not sender:
        return None
    m = _SENDER.match(sender.strip().upper())
    if not m:
        return None
    code = m.group(1)
    return BANKS.get(code) or next((name for key, name in BANKS.items() if code.startswith(key[:5])), None)


def looks_like_bank_sender(sender: str | None) -> bool:
    """A business header (AD-HDFCBK), not a person's phone number."""
    s = (sender or "").strip()
    return bool(s) and not re.fullmatch(r"\+?[\d\s-]{6,}", s)


def _money(text: str) -> Decimal | None:
    try:
        value = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None
    return value if value > 0 else None


def _txn_amount(flat: str) -> Decimal | None:
    """The first amount that isn't a balance, a limit or a due amount."""
    for rx in (_AMOUNT, _AMOUNT_VERB):
        for m in rx.finditer(flat):
            if _NOT_TXN_AMOUNT.search(flat[max(0, m.start() - 40):m.start()]):
                continue
            value = _money(m.group(1))
            if value is not None:
                return value
    return None


def parse_date(text: str) -> date | None:
    for m in _DATE_ISO.finditer(text):
        try:
            return date(*(int(x) for x in m.groups()))
        except ValueError:
            continue
    for m in _DATE_NUM.finditer(text):
        d, mo, y = (int(x) for x in m.groups())
        try:
            return date(y + 2000 if y < 100 else y, mo, d)
        except ValueError:
            continue
    for m in _DATE_MON.finditer(text):
        mo = _MONTHS.get(m.group(2).lower()[:3])
        if not mo:
            continue
        y = int(m.group(3))
        try:
            return date(y + 2000 if y < 100 else y, mo, int(m.group(1)))
        except ValueError:
            continue
    return None


def _clean_party(name: str | None) -> str | None:
    if not name:
        return None
    name = re.sub(r"\s+", " ", name).strip(" .-'*/")
    if len(name) < 2 or _NOT_A_PARTY.match(name) or re.fullmatch(r"[\d\sxX*]+", name):
        return None
    return name[:80]


def _party(text: str, flat: str, direction: str, channel: str) -> str | None:
    m = UPI_PATH_RE.search(text)
    if m:
        name = _clean_party(_FOOTER.split(m.group(1), 1)[0])
        if name:
            return name
    if direction == CREDIT and _SALARY.search(flat):
        return "Salary"
    if direction == CREDIT and _INTEREST.search(flat):
        return "Interest"
    tries = []
    if channel in (CH_CARD, CH_ATM) or direction == DEBIT:
        tries += [_AT, _INFO]
    if direction == DEBIT:
        m = _SEMI_CREDITED.search(flat)
        if m and _clean_party(m.group(1)):
            return _clean_party(m.group(1))
        tries.append(_TO)
    else:
        tries += [_FROM, _INFO]
    for rx in tries:
        for m in rx.finditer(flat):
            name = _clean_party(m.group(1))
            if name:
                return name
    if direction == CREDIT and _REFUND.search(flat):
        return "Refund"
    return None


def _date_near(d: date | None, received: date) -> date:
    """The date in the message, unless it's clearly not the transaction date (future, or long before it arrived)."""
    if d is None or d > received + timedelta(days=1) or d < received - timedelta(days=45):
        return received
    return d


def parse_bank_message(text: str, sender: str | None = None, received: date | None = None) -> ParsedBank:
    """Read one bank message. Raises ValueError with a reason the user can act on when it isn't a transaction."""
    received = received or date.today()
    flat = " ".join((text or "").split())
    if not flat:
        raise NotATransaction("The message is empty")
    if _OTP.search(flat):
        raise NotATransaction("A one-time password or security message, not a transaction")
    if _REQUEST.search(flat) and not re.search(r"\b(debited|credited)\b", flat, re.I):
        raise NotATransaction("A payment request, not a payment")
    if _FAILED.search(flat) and not _REFUND.search(flat):
        raise NotATransaction("A failed or declined payment: no money moved")
    bank = bank_for_sender(sender)
    debit, credit = _DEBIT.search(flat), _CREDIT.search(flat)

    # A bill that is due (card statement, utility bill): only when the message isn't itself about money moving
    if _BILL.search(flat) and not (debit and re.search(r"\b(debited|spent|withdrawn)\b", flat, re.I)) and not _CARD_PAYMENT_RECEIVED.search(flat):
        total = _TOTAL_DUE.search(flat)
        amount = _money(total.group(1)) if total else _txn_amount(flat)
        if amount is None:
            m = _AMOUNT.search(flat)
            amount = _money(m.group(1)) if m else None
        if amount is None:
            raise Unreadable("A bill or statement message without an amount")
        due = _DUE_DATE.search(flat)
        min_due = _MIN_DUE.search(flat)
        last4 = _LAST4.search(flat)
        return ParsedBank(
            kind=KIND_BILL, amount=amount, bank=bank, channel=CH_CARD_BILL,
            last4=(last4.group(1) or last4.group(2))[-4:] if last4 else None,
            due_date=parse_date(due.group(1)) if due else None,
            min_due=_money(min_due.group(1)) if min_due else None,
            is_credit_card=bool(re.search(r"\bcard\b", flat, re.I)),
            txn_date=received,
        )

    if _PROMO.search(flat) and not (debit or credit):
        raise NotATransaction("An offer or advertisement, not a transaction")
    if not debit and not credit:
        if _BALANCE.search(flat) or re.search(r"\bbal(?:ance)?\b", flat, re.I):
            raise NotATransaction("A balance update only, no transaction")
        if _AMOUNT.search(flat):
            raise Unreadable("Couldn't tell whether money was paid or received")
        raise NotATransaction("Not about money paid or received")
    # Both can appear ("A/c debited …; RAMESH credited"): the first one says what happened to the user's account
    direction = DEBIT if debit and (not credit or debit.start() < credit.start()) else CREDIT
    if _CARD_PAYMENT_RECEIVED.search(flat):
        direction = CREDIT
    amount = _txn_amount(flat)
    if amount is None:
        raise Unreadable("Couldn't find the amount")
    if amount > Decimal("9999999999.99"):
        raise Unreadable("The amount is too large")

    if _CARD_PAYMENT_RECEIVED.search(flat) or (direction == DEBIT and _CARD_BILL_DEBIT.search(flat)):
        channel = CH_CARD_BILL
    elif _ATM.search(flat) and direction == DEBIT:
        channel = CH_ATM
    elif _UPI.search(flat) or VPA_RE.search(flat):
        channel = CH_UPI
    elif _CARD.search(flat):
        channel = CH_CARD
    elif _AUTOPAY.search(flat):
        channel = CH_AUTOPAY
    else:
        channel = CH_BANK

    last4 = _LAST4.search(flat)
    vpas = [v.lower() for v in VPA_RE.findall(flat)]
    ref = _REF.search(flat)
    ref = ref.group(1) if ref and re.search(r"\d", ref.group(1)) else (re.search(r"\b(\d{12})\b", flat) or [None, None])[1]
    bal = _BALANCE.search(flat)
    return ParsedBank(
        kind=KIND_TXN, amount=amount, direction=direction, channel=channel, bank=bank,
        last4=(last4.group(1) or last4.group(2))[-4:] if last4 else None,
        party=_party(text, flat, direction, channel) if channel != CH_CARD_BILL else None,
        vpa=vpas[-1] if direction == DEBIT and vpas else (vpas[0] if vpas else None),
        ref=ref, balance=_money(bal.group(1).lstrip("-")) if bal else None,
        txn_date=_date_near(parse_date(flat), received),
        # Paying a card bill from the bank: the account in the message is the bank's; the card is where it went
        is_credit_card=(channel == CH_CARD_BILL and direction == CREDIT)
        or (channel != CH_CARD_BILL and bool(re.search(r"\bcredit\s*card\b", flat, re.I))),
        card_name=_card_name(flat) if channel == CH_CARD_BILL else None,
    )


def _card_name(flat: str) -> str | None:
    m = _CARD_NAME.search(flat)
    if not m:
        return None
    name = re.sub(r"^(?:the|my|your)\s+", "", m.group(1).strip(), flags=re.I)
    return name or None


# ---------- a first category from the merchant's name (aliases the user makes take over) ----------
_CATEGORY_WORDS = [
    ("Food", r"swiggy|zomato|eatsure|domino|pizza|mcdonald|kfc|burger|cafe|restaurant|starbucks|chaayos|haldiram|bakery|dunzo|blinkit|zepto|instamart|bigbasket|grofers|dmart"),
    ("Travel", r"uber|ola\b|rapido|irctc|makemytrip|goibibo|redbus|indigo|air\s*india|vistara|akasa|metro|cleartrip|ixigo|yatra"),
    ("Fuel", r"petrol|fuel|hpcl|bpcl|iocl|indian\s*oil|bharat\s*petroleum|hindustan\s*petroleum|shell|nayara"),
    ("Shopping", r"amazon|flipkart|myntra|ajio|meesho|nykaa|tata\s*cliq|croma|reliance\s*digital|decathlon|ikea|lifestyle|westside|zudio"),
    ("Entertainment", r"netflix|spotify|hotstar|prime\s*video|bookmyshow|pvr|inox|youtube|sonyliv|zee5|jiocinema"),
    ("Mobile", r"airtel|jio\b|vodafone|\bvi\b|recharge|bsnl"),
    ("Bills", r"electricity|bescom|msedcl|tata\s*power|adani\s*electric|torrent\s*power|water\s*bill|gas\s*bill|broadband|act\s*fibernet|mahanagar\s*gas|indane|bharatgas"),
    ("Health", r"pharmacy|apollo|medplus|1mg|pharmeasy|netmeds|hospital|clinic|diagnostic|practo"),
    ("FasTag", r"fastag|toll"),
]


def guess_category(name: str | None, channel: str | None = None) -> str:
    low = (name or "").lower()
    for category, words in _CATEGORY_WORDS:
        if re.search(words, low):
            return category
    return "General"
