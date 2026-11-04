"""Tally-style chart of accounts: the fixed group tree, the ledgers every user starts with, and financial years.

Groups are fixed (users add ledgers, not groups), so they live here instead of in a table. Each group has:
- nature: ASSET / LIABILITY / INCOME / EXPENSE (decides which side its balance normally sits on)
- section: BS (Balance Sheet), TRADING (Trading Account) or PL (Profit & Loss Account)
- cash_flow: how the cash flow statement treats its ledgers: CASH (cash and cash equivalents), OPERATING
  (working capital), INVESTING or FINANCING. Trading / P&L groups have none: they make up the profit.
"""

from dataclasses import dataclass
from datetime import date

ASSET, LIABILITY, INCOME, EXPENSE = "ASSET", "LIABILITY", "INCOME", "EXPENSE"
BS, TRADING, PL = "BS", "TRADING", "PL"
CASH, OPERATING, INVESTING, FINANCING = "CASH", "OPERATING", "INVESTING", "FINANCING"


@dataclass(frozen=True)
class Group:
    code: str
    name: str
    parent: str | None
    nature: str
    section: str
    cash_flow: str | None
    order: int


_G = [
    # Liabilities side of the Balance Sheet
    ("CAPITAL", "Capital Account", None, LIABILITY, BS, FINANCING),
    ("RESERVES", "Reserves & Surplus", "CAPITAL", LIABILITY, BS, FINANCING),
    ("LOANS", "Loans (Liability)", None, LIABILITY, BS, FINANCING),
    ("BANK_OD", "Bank OD A/c", "LOANS", LIABILITY, BS, FINANCING),
    ("SECURED_LOANS", "Secured Loans", "LOANS", LIABILITY, BS, FINANCING),
    ("UNSECURED_LOANS", "Unsecured Loans", "LOANS", LIABILITY, BS, FINANCING),
    ("CURRENT_LIABILITIES", "Current Liabilities", None, LIABILITY, BS, OPERATING),
    ("DUTIES_TAXES", "Duties & Taxes", "CURRENT_LIABILITIES", LIABILITY, BS, OPERATING),
    ("PROVISIONS", "Provisions", "CURRENT_LIABILITIES", LIABILITY, BS, OPERATING),
    ("SUNDRY_CREDITORS", "Sundry Creditors", "CURRENT_LIABILITIES", LIABILITY, BS, OPERATING),
    ("SUSPENSE", "Suspense A/c", None, LIABILITY, BS, OPERATING),
    # Assets side
    ("FIXED_ASSETS", "Fixed Assets", None, ASSET, BS, INVESTING),
    ("INVESTMENTS", "Investments", None, ASSET, BS, INVESTING),
    ("CURRENT_ASSETS", "Current Assets", None, ASSET, BS, OPERATING),
    ("BANK_ACCOUNTS", "Bank Accounts", "CURRENT_ASSETS", ASSET, BS, CASH),
    ("CASH_IN_HAND", "Cash-in-Hand", "CURRENT_ASSETS", ASSET, BS, CASH),
    ("DEPOSITS", "Deposits (Asset)", "CURRENT_ASSETS", ASSET, BS, OPERATING),
    ("LOANS_ADVANCES", "Loans & Advances (Asset)", "CURRENT_ASSETS", ASSET, BS, OPERATING),
    ("SUNDRY_DEBTORS", "Sundry Debtors", "CURRENT_ASSETS", ASSET, BS, OPERATING),
    # Trading Account
    ("SALES", "Sales Accounts", None, INCOME, TRADING, None),
    ("PURCHASE", "Purchase Accounts", None, EXPENSE, TRADING, None),
    ("DIRECT_INCOMES", "Direct Incomes", None, INCOME, TRADING, None),
    ("DIRECT_EXPENSES", "Direct Expenses", None, EXPENSE, TRADING, None),
    # Profit & Loss Account
    ("INDIRECT_INCOMES", "Indirect Incomes", None, INCOME, PL, None),
    ("INDIRECT_EXPENSES", "Indirect Expenses", None, EXPENSE, PL, None),
]
GROUPS: dict[str, Group] = {c: Group(c, n, p, nat, sec, cf, i) for i, (c, n, p, nat, sec, cf) in enumerate(_G)}

# Ledgers in these groups are cash or bank: Payment / Receipt / Contra vouchers need one, and Bank
# Reconciliation works on them
CASH_BANK_GROUPS = {"BANK_ACCOUNTS", "CASH_IN_HAND", "BANK_OD"}
EXPENSE_GROUPS = {c for c, g in GROUPS.items() if g.nature == EXPENSE}
INCOME_GROUPS = {c for c, g in GROUPS.items() if g.nature == INCOME}

# Where each kind of bank account from Settings → Bank accounts goes
ACCOUNT_KIND_GROUP = {
    "SAVINGS": "BANK_ACCOUNTS",
    "CURRENT": "BANK_ACCOUNTS",
    "SALARY": "BANK_ACCOUNTS",
    "CREDIT_CARD": "CURRENT_LIABILITIES",
    # A wallet balance is spendable on demand, so it is a cash equivalent like cash in hand
    "WALLET": "CASH_IN_HAND",
    "CASH": "CASH_IN_HAND",
}


def root_of(group_code: str) -> Group:
    g = GROUPS[group_code]
    while g.parent:
        g = GROUPS[g.parent]
    return g


# (code, name, group, is_depreciation). Code None = an ordinary ledger the user may rename or delete.
SEED_LEDGERS = [
    ("CAPITAL", "Capital Account", "CAPITAL", False),
    ("DRAWINGS", "Drawings", "CAPITAL", False),
    ("CASH", "Cash", "CASH_IN_HAND", False),
    ("SUSPENSE", "Suspense", "SUSPENSE", False),
    ("SALES", "Sales", "SALES", False),
    ("PURCHASE", "Purchases", "PURCHASE", False),
    ("OUT_CGST", "Output CGST", "DUTIES_TAXES", False),
    ("OUT_SGST", "Output SGST", "DUTIES_TAXES", False),
    ("OUT_IGST", "Output IGST", "DUTIES_TAXES", False),
    ("IN_CGST", "Input CGST", "DUTIES_TAXES", False),
    ("IN_SGST", "Input SGST", "DUTIES_TAXES", False),
    ("IN_IGST", "Input IGST", "DUTIES_TAXES", False),
    ("TDS_PAYABLE", "TDS Payable", "DUTIES_TAXES", False),
    ("TDS_RECEIVABLE", "TDS Receivable", "LOANS_ADVANCES", False),
    ("OUTSTANDING", "Outstanding Expenses", "PROVISIONS", False),
    ("PREPAID", "Prepaid Expenses", "LOANS_ADVANCES", False),
    ("ACCRUED_INCOME", "Accrued Income", "LOANS_ADVANCES", False),
    ("DEPRECIATION", "Depreciation", "INDIRECT_EXPENSES", True),
    (None, "Wages", "DIRECT_EXPENSES", False),
    (None, "Freight Inward", "DIRECT_EXPENSES", False),
    (None, "Rent", "INDIRECT_EXPENSES", False),
    (None, "Salaries", "INDIRECT_EXPENSES", False),
    (None, "Electricity", "INDIRECT_EXPENSES", False),
    (None, "Telephone & Internet", "INDIRECT_EXPENSES", False),
    (None, "Office Expenses", "INDIRECT_EXPENSES", False),
    (None, "Printing & Stationery", "INDIRECT_EXPENSES", False),
    (None, "Travelling & Conveyance", "INDIRECT_EXPENSES", False),
    (None, "Repairs & Maintenance", "INDIRECT_EXPENSES", False),
    (None, "Advertisement", "INDIRECT_EXPENSES", False),
    (None, "Professional Fees", "INDIRECT_EXPENSES", False),
    (None, "Bank Charges", "INDIRECT_EXPENSES", False),
    (None, "Interest Paid", "INDIRECT_EXPENSES", False),
    (None, "Interest Received", "INDIRECT_INCOMES", False),
    (None, "Commission Received", "INDIRECT_INCOMES", False),
    (None, "Discount Received", "INDIRECT_INCOMES", False),
    (None, "Other Income", "INDIRECT_INCOMES", False),
    (None, "Furniture & Fixtures", "FIXED_ASSETS", False),
    (None, "Computers & Equipment", "FIXED_ASSETS", False),
]

GST_CODES = {"OUT_CGST", "OUT_SGST", "OUT_IGST", "IN_CGST", "IN_SGST", "IN_IGST"}

# Indian states and union territories, for the place of business / place of supply
STATES = [
    "Andaman and Nicobar Islands", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chandigarh",
    "Chhattisgarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand", "Karnataka", "Kerala", "Ladakh", "Lakshadweep",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Puducherry",
    "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
    "West Bengal",
]


# ---------- financial years (1 April – 31 March) ----------
def fy_of(d: date) -> int:
    """The financial year a date falls in, named by the year it starts: 15 Feb 2026 is in FY 2025 (2025-26)."""
    return d.year if d.month >= 4 else d.year - 1


def fy_start(fy: int) -> date:
    return date(fy, 4, 1)


def fy_end(fy: int) -> date:
    return date(fy + 1, 3, 31)


def fy_label(fy: int) -> str:
    return f"{fy}-{str(fy + 1)[-2:]}"
