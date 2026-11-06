# Expense Tracker (Python + React + Postgres)

Daily expense tracker with customisable daily items, a balance dashboard with charts, Excel/PDF reports,
missed-entry reminders, a manager hierarchy, read-only sharing, profiles, and admin approval of new users.
An optional **Accounting** module keeps Tally-style double-entry books for a sole proprietor, with the Journal,
General Ledger, Trial Balance, Trading and Profit & Loss Accounts, Balance Sheet, Cash Flow Statement and Bank
Reconciliation. **Import past records** brings in history from other apps, bank statements and your own sheets
(Excel, CSV, PDF, Word).

| Layer    | Tech |
|----------|------|
| Backend  | FastAPI, SQLAlchemy 2, psycopg 3, APScheduler, openpyxl, reportlab |
| Frontend | React 18 + Vite, React Router, Recharts, Material UI icons (and lucide-react on older pages), Inter font |
| Database | Postgres 17: existing `LOCAL_FCHUB_DB` container (port 5445), user and database **`dev_dr_expense`** |

## Run it

```bash
# 1. Database: no new container. Uses LOCAL_FCHUB_DB (localhost:5445), user/database dev_dr_expense
#    (docker start LOCAL_FCHUB_DB). Put its password in backend/.env (DATABASE_URL).

# 2. Backend → http://localhost:8000   API docs → http://localhost:8000/api/docs
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # set the dev_dr_expense password, JWT secret, reminder time, SMTP
.venv/bin/uvicorn app.main:app --reload --port 8000

# 3. Frontend → http://localhost:5173
cd frontend
npm install
npm run dev
```

On first start the backend creates its 22 tables in `dev_dr_expense` and an admin from `.env`
(default **admin / admin123**, change it: with `ENVIRONMENT=production` the backend refuses to start while the admin
password is the default or `JWT_SECRET` is shorter than 32 characters). **[docs/DATABASE.md](docs/DATABASE.md)** describes every table,
what it stores, and how the rules work. `backend/sql/schema.sql` holds the equivalent Postgres DDL.
Data from the old Oracle schema was copied with `backend/scripts/oracle_to_postgres.py`.

## Deploy to Vercel (one project: React + Python)

`vercel.json` builds the React app into static files and serves the FastAPI backend as one Python function
(`api/index.py`) under `/api`, so the frontend's relative `/api` calls work on the same domain with no CORS setup.

1. Create a hosted Postgres (Vercel Marketplace → Neon, or Supabase). Vercel's servers cannot reach the local
   Docker database. Use the provider's **pooled** connection string.
2. Import the repo in Vercel and leave *Root Directory* as the repo root (the settings come from `vercel.json`).
3. Environment variables: `DATABASE_URL`, `ENVIRONMENT=production`, `JWT_SECRET` (32+ chars), `ADMIN_PASSWORD`,
   `ADMIN_EMAIL`, `CRON_SECRET`, `FRONTEND_URL=https://<your-app>.vercel.app`, and the `SMTP_*` settings for email.
4. Deploy. The first request creates the tables and the admin user.

Scheduled jobs: there is no long-running process on Vercel, so APScheduler is off there (`VERCEL` is set) and
Vercel Cron calls `/api/cron/daily-reminder` (15:30 UTC = 21:00 IST) and `/api/cron/subscriptions` (once a day).
The Hobby plan allows only daily crons; on Pro set the subscriptions schedule to `5 * * * *` for hourly runs.
To move local data to the hosted DB: `pg_dump` the local database and `psql` it into the new one.

### On Windows (PowerShell)

```powershell
# Backend → http://localhost:8000
cd backend
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env          # then edit it
.venv\Scripts\uvicorn app.main:app --reload --port 8000   # run it from backend\ so .env is found

# Frontend → http://localhost:5173 (in a second window)
cd frontend
npm install
npm run dev
```

Without the Postgres container, local development can use SQLite: `DATABASE_URL=sqlite:///./expense_tracker.db`
in `backend/.env` (the tests run on SQLite too).

### Tests

```bash
cd backend
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

They run against an in-memory SQLite database, so they need neither Postgres nor a running server.

## Features

- **Daily items** (*My Daily Items*): the things you buy every day (cigarette, chocolate, food…).
  **Constant** items have a fixed unit price (only the quantity changes). **Custom** items let you change the
  amount every day. Items can be disabled without losing history.
- **Daily entry**: your daily items are pre-filled for the chosen date. Untick, adjust, add **extra items**, then
  **Confirm & Save**. Future dates are rejected.
- **3-day edit lock**: the owner can edit or delete an expense only within `EDIT_WINDOW_DAYS` (3) of saving it.
  Admins can always change it.
- **Reminders**: every day at `REMINDER_HOUR:REMINDER_MINUTE` users with no entry for that day get a notification
  (plus email if SMTP is set), and so does their direct manager. Missed days are back-filled for the last 7 days.
  The app shows a banner, and a browser notification after 18:00.
- **Dashboard** (*Overview*): this month's spending against the same day last month, the budget used, active
  subscriptions, and a *Smart insights* card. Below that: upcoming renewals (pause from there), spending by
  category (doughnut, with a warning badge on categories near or over their budget), budget progress bars, a
  last-six-months chart split into expenses / subscriptions / split bills, and a **month-on-month analysis**: this month
  so far vs the same point last month, all of last month and the 6-month average (projected to month end), with
  plain-language findings ("Food is up 30% on last month", "Transport is down 61%") and a per-category table.
  Subscription charges logged as expenses are counted once.
- **Budgets & alerts** (*Settings → Budgets & alerts*): a monthly limit for all spending, a category (Food) or one
  item (Cigarettes). At the alert level (90% by default, adjustable) you get a notification and an email, and again
  when you pass 100%; each level is notified once a month. The dashboard's bars, sparkline and badges turn amber at
  the alert level and red when over.
- **Bank accounts** (*Settings → Bank accounts*): add your salary account, spending account, cards, wallets or cash
  (only the last 4 digits are stored), plus **Investments** and **Money with others** (money lent or paid on
  someone's behalf) accounts. Mark one **primary**. Money received (*Balance*) is recorded against an
  account, Daily Entry asks **which account paid** (starting on the primary one), and *Transfers between accounts*
  records money moved from one of your accounts to another without counting it as income or spending. Each
  account shows its balance and this month's spending. Turn off "Ask which account for each expense" to send
  everything to the primary account automatically. Subscription charges are paid from the primary account.
- **Custom background** (*Settings → Appearance*): upload your own picture (up to 5 MB) to show behind the app;
  *Reset to default* removes it.
- **Reports**: Excel (summary with charts, daily totals, detailed list) or PDF for any period.
- **Import past records** (*Import Records*): bring in expenses and money received from before you used the app.
  - **Formats**: Excel `.xlsx` / `.xls` (every sheet), CSV (comma, semicolon, tab or pipe), PDF (its tables; or its
    text, split into columns at gaps of two or more spaces) and Word `.docx` (its tables, or lines like
    `05/11/2026 Petrol 2,000`). Up to `IMPORT_MAX_BYTES` (5 MB; a file over 1 MB is gzipped by the page before uploading, which keeps it under Vercel's 4.5 MB request cap) and
    `IMPORT_MAX_RECORDS` (5,000) per file.
  - **Not supported**: scanned PDFs and photos (no text to read), old Word `.doc` (save it as `.docx` or PDF),
    password-protected files. A file that can't be read gets a clear message; records can also be typed in by hand.
  - **Steps**: 1. upload; 2. check the columns, guessed from their headings (`Txn Date`, `Narration`,
    `Withdrawal Amt.`, `Cost`, `Paid with`, `My share`…) or their contents, and how dates are written (day-first by
    default); 3. review every record: its status, why it can't be imported, and edit, untick, remove or add rows;
    4. import (sent in chunks of 200 rows, so big files stay within the server's time limit).
  - **What a row becomes**: spending → an expense; money in (deposit, credit, income, salary…) → money received;
    a split row (a *My share* or *Split with* column) → an expense for your share plus a split bill, paid by you, for
    the part paid for the others (the people named, or "Others"), in a group you choose or "Imported split bills".
    When a *Paid by* column names someone else, the row becomes an expense for your share (from no account) plus a
    split bill paid by them, so you owe them your share.
    A **transfer** (type "Transfer", an account cell like `Axis Bank->SBI Bank`, or a *To account* column; credit-card
    bill payments and cash withdrawals too) → a transfer between your accounts. An **opening balance** row ("Balance
    cf", "Opening balance", "Balance B/F", dated before the account's other rows) → added to that account's opening
    balance (a later "closing balance" row is flagged and left out). Subscriptions in a file are imported as
    expenses. An unrecognised type is never guessed: the row waits for you to choose. Amounts may carry ₹ / Rs / INR,
    Indian grouping, Dr / Cr, brackets or a minus sign. Without a description column, the notes are the description.
  - **Categories** not in the app yet must be mapped to one, or created; nothing is put in an unrelated category.
    *Use the suggestions* maps the obvious ones (Fuel → Transport, Internet → Bills…), *Create the rest as new*
    creates the others in one click. Less certain matches (Mobile → Bills, Gift → Shopping) are only shown as
    "maybe" for you to decide, and a row much bigger than the rest of its category (a ₹54,647 phone among ₹299
    recharges) is flagged under *To check*. Each row also has a note box for a note of your own.
  - **Money that isn't spending or income**: a category can be moved to an account instead: investments (mutual
    funds, shares, F&O, PF) to an **Investments** account, money paid for someone and paid back (reimbursements,
    money returned, money given and repaid) to **Money with others**. Its rows become transfers, so bank balances stay right and spending /
    income aren't inflated. Suggested for the usual names; both account types post to Investments and Loans &
    Advances in the books.
  - **Accounts**: the file's account column by default (or the primary account, none, or one account for the whole
    file). Names match your accounts exactly (dots, spaces and capitals ignored) or by last 4 digits; the others can
    be created during the import, with a suggested type (bank, cash, credit card, wallet for FasTag / Paytm,
    investments…). Accounts created by an import are removed by a full Undo when nothing else uses them (deactivated
    instead when they have entries in the books). A bank, cash or wallet account that would end below zero is
    flagged: some of its rows are probably missing (a FasTag's top-ups), so import its own statement too.
  - **Your choices are remembered**: the categories, moves and accounts you chose are filled in again the next time
    the same names come up, e.g. in the next year's file.
  - **Checks**: missing or unrecognised dates and amounts, future dates, locked accounting periods (rejected with
    the reason), records already in the app on the same date for the same amount (left out unless ticked), rows
    imported before (shown under *Already imported* and never imported twice: re-uploading a file adds nothing),
    and balance / total lines. A description longer than 100 characters is kept in full in the note.
  - **After the import**: records keep their original date and show on every page; the 3-day edit window starts on
    the import day; no budget alert is sent; they post to the books like any other record. A whole history from
    another app (e.g. a Money Manager export with transfers and opening balances) ends with every account at the
    balance the other app shows.
  - **Import history**: each upload with its file, date, status, rows imported / left out / failed (each with its
    reason) and totals. **Undo** removes only the records that upload created, keeping any edited since, reconciled
    with a bank statement or in a locked accounting period (a big import is undone 200 records per request). With
    Accounting on, the books keep the import and its reversal (as in Tally): the earlier years it added stay, and
    the accounts it created are deactivated rather than deleted.
- **Transfers between accounts** (*Settings → Accounts*) are listed 50 at a time, newest first.
- **Profile**: change your name, email, password and **profile photo** (upload or remove).
  **Username**: you can change it yourself once every `USERNAME_LOCK_DAYS` (15) days. Inside that window you raise
  a request, and an admin approves or rejects it. An approved change restarts your 15 days.
- **Forgot password**: enter your username to get an email with a **temporary password** and a reset link
  (valid 60 minutes, single use, disabled after 5 wrong tries). On the link page you enter the temporary password
  and choose a new one. **At most 2 resets per 15 days.** Without SMTP settings the emails are saved to
  `backend/mail_outbox/*.eml`, so you can test locally.
- **Staying signed in**: a page refresh keeps you signed in, on the same page and viewing the same person. You are
  only signed out when your token is rejected (expired after `JWT_EXPIRE_MINUTES`, or invalid). If the backend is
  down you get a *Retry* screen instead.
- **Plans (feature access)**: the admin chooses what each user can use under *Administration → Users → Plan*:
  **Expenses** (everything above), **Split bills** and **Subscriptions**. New users get Expenses only. A user without
  Expenses sees only the features they have, and the API refuses the expense endpoints for them. Admins always have every feature.
- **Split Bills**:
  - **Who can use it**: creating groups and adding guests needs the Split bills plan. Anyone added to a group
    can use that group without it: see it, add and edit their own bills, record payments, leave. Split Bills
    appears in their menu while they are in a group.
  - **Groups**: create a group and add members. Type **@username** for someone on the app: they see the group
    and get notified. Type a **plain name** for a **guest**, someone who isn't on the app. Guests are tracked by
    name only in that group, can't sign in, and get no notifications. They can pay bills and be on them like
    anyone else, and whoever added them (or a group admin) records their payments. The creator is the **owner**. The owner and any **group admins** can add or remove members and make
    others admin. Only the owner can **delete** the group, and only once every balance is settled.
  - **Bills**: any member can add a bill: total, date, who paid, and who shares it. Each person pays an
    **equal share**, a **percentage**, or a **fixed amount**. Fixed amounts and percentages are taken first and the
    rest is divided equally. *Example: ₹1000 among 4 people with one fixed at ₹400 gives ₹400 + 3 × ₹200.*
    Only the person who added a bill can edit or delete it. If they leave the group, the owner takes it over.
  - **Removing a member**: tick the bills they should be taken out of. A preview shows, for each bill, the
    amount they no longer pay and how it is split between the others (into the equal shares, or spread evenly
    if everyone else is fixed). A summary is shown after removal. They stay on bills they paid for. Members can
    also leave a group themselves the same way.
  - **Adding a member**: optionally tick existing bills to add them to. They join the equal share, and the
    others' fixed amounts and percentages stay the same. A summary shows the old and new amounts.
  - **Balances and payments**: see who owes whom (net per pair), and per person what they paid, their share,
    what they paid back and received. Record a payment you made or received to settle up. Only whoever
    recorded a payment can delete it.
  - Every change notifies the other group members.
- **Subscriptions** (*Subscriptions*, plan feature): list everything that charges you again and again (Netflix, gym,
  insurance, rent, UPI AutoPay mandates) with the amount, start and optional end date, how it's paid (Google Pay,
  PhonePe, Paytm, card…) and how often: weekly, monthly, quarterly, half-yearly, yearly, or **custom** (every N days,
  weeks, months or years).
  - **Reminders**: a notification and an email `N` days before each renewal (1 by default, per subscription).
  - **Charges**: on each renewal date the charge is recorded and, if ticked, added to your expenses, so the balance
    stays right. Charges before you added the subscription aren't back-filled.
  - **Pause / resume from one place**: a switch on every row, or tick several and pause or resume them together.
    Paused subscriptions get no reminders and log no charges. Pausing here doesn't cancel the payment at the bank,
    so the app tells you where to cancel the AutoPay in Google Pay, PhonePe, Paytm and others.
  - **Totals**: cost per month and per year (a yearly ₹1,200 plan counts as ₹100 a month), what's due in the next
    7 days, what pausing saves, and everything renewing in the next 30 days.
  - **Find my subscriptions**: Google Pay, PhonePe and Paytm don't offer an API for reading someone's AutoPay
    mandates, so the app finds them two ways instead. Paste your bank / UPI **SMS or email alerts** ("AutoPay mandate
    created…", "debited towards…"), and it reads the merchant, amount, frequency, date and UPI app. Repeated debits
    to the same merchant become a subscription with the frequency worked out from the dates. Or it scans **your
    expense history** for items paid at a regular interval for a similar amount. You choose which to add.
  - The admin turns it on per user under *Administration → Users → Plan*. Managers and people you shared with can
    see your subscriptions but not change them.
- **Security**: 5 wrong passwords block an account for 15 minutes (and the owner is notified), each IP address gets
  at most 20 sign-in attempts a minute, and registration and forgot-password are rate-limited. Changing or resetting
  a password and an admin lock sign out every other session at once; *Settings → Sign out of all devices* ends every session, including the current one.
  Passwords need at least 8 characters. Responses carry security headers; in production the API docs are hidden
  and HSTS is sent.
- **Accounting** (plan feature, needs Expenses; *Administration → Users → Plan*): double-entry books per user,
  Tally style, for a sole proprietor. Financial year 1 April – 31 March.
  - **Chart of Accounts**: Tally's groups (Capital Account, Loans, Current Liabilities → Duties & Taxes / Provisions /
    Sundry Creditors, Fixed Assets, Investments, Current Assets → Bank Accounts / Cash-in-Hand / Sundry Debtors /
    Loans & Advances, Sales, Purchase, Direct / Indirect Incomes and Expenses, Suspense) with standard ledgers
    (Capital, Drawings, Cash, Output / Input CGST, SGST, IGST, TDS Payable / Receivable, Outstanding and Prepaid
    Expenses, Accrued Income, Depreciation…). Add your own ledgers with opening balances. Each bank account gets a
    ledger (credit cards as liabilities), and each split group a "Split – group" debtor ledger.
  - **Vouchers**: Payment, Receipt, Contra, Journal, Sales and Purchase. Every voucher must balance (debits =
    credits, in whole paise), and is numbered per type within the financial year. A posted voucher is never
    edited: it is **reversed** (a mirror entry) and posted again. Sales and Purchase can be **GST invoices**:
    CGST + SGST when the place of supply is the business's state, IGST otherwise (no return filing). TDS and
    year-end adjustments (depreciation, outstanding / prepaid expenses, accrued income) are journal vouchers.
  - **Automatic posting**: expenses, money received, transfers, subscription charges (unless already logged as an
    expense), split bills (your share; what you paid for others becomes owed to you) and split settlements post
    themselves, and follow every edit or deletion (reverse and re-post, never twice). **Posting rules** decide the
    ledger per expense category / money source: personal spending defaults to **Drawings** and personal money in
    to **Capital**, so neither touches the business's profit; map business categories to expense ledgers.
  - **Period lock**: nothing dated on or before the lock date can be posted, edited, deleted or reversed, not even
    by an admin (the subscription job skips a charge that would fall there). Corrections go in an open period.
  - **Reports** (screen, Excel, PDF; Indian digit grouping): Journal, General Ledger, Trial Balance (grouped or
    ledger-wise), Trading Account, Profit & Loss Account, Balance Sheet (horizontal, sole proprietor: capital +
    profit − drawings), Cash Flow Statement (indirect method, AS-3) and Bank Reconciliation Statement. Opening and
    closing **stock** are entered per year (no item inventory); earlier years' profit is carried into capital.
  - **Bank reconciliation**: import a CSV or Excel statement from any bank (pick the columns; re-importing adds
    nothing), auto-match by amount, direction and date (±3 days, only when unambiguous), match by hand, and book
    bank charges or interest straight from a statement line.
  - An account whose ledger has entries can't be deleted (deactivate it). Managers and shared viewers see the books
    read-only, as everywhere else.
- **Welcome tour**: a spotlight walkthrough after sign-in. Ticking **"Don't show again"** turns it off for the
  account. **Settings** lets you turn it back on, replay it, or switch between light and dark themes.

## Access rules

| Who is viewing | Can see | Can change |
|---|---|---|
| Owner | own data | yes (3-day lock applies) |
| **Manager at any level above** (A manages B, B manages C, so B sees C and A sees B and C; A's own manager sees all three) | the team's data | **no**, read-only |
| User the owner shared with (*Team & Sharing*) | the owner's data | **no**, read-only |
| Admin | everyone | yes, including locked entries; approves users and username requests, sets managers, deletes users |

- Switch whose data you see with the **eye selector** at the top. A badge shows when the data is read-only.
- The manager is set at registration ("Manager's username") or by the admin under *Administration → Users*.
  Loops (a manager reporting to their own team member) are rejected.
- New registrations are **pending** until an admin approves them.
- All rules are enforced by the API (`backend/app/deps.py`), not just hidden in the UI.

## Project layout

```
backend/app/
  main.py          app, settings check, security headers, admin seeding, schedulers, docs at /api/docs
  models.py        tables: app_users, user_avatars, username_change_requests, password_resets,
                   daily_items, expenses, funds, data_shares, notifications,
                   split_groups, split_members, split_bills, split_shares, split_settlements,
                   subscriptions, subscription_charges, job_runs, accounts, account_transfers, budgets,
                   user_backgrounds
  deps.py          authentication + access rules (owner / admin / manager hierarchy / shared) + plan features
  ratelimit.py     per-IP sign-in / registration limits
  routers/         auth, admin, profile, expenses, daily_items, funds, dashboard, reports, shares, notifications, splits,
                   subscriptions, accounts, budgets
  services/        summary (aggregations), reports (Excel/PDF), reminders, mailer (SMTP or .eml outbox),
                   splits (bill splitting maths and balances), subscriptions (schedules, charges, reminders),
                   subscription_import (SMS parsing, detection in expenses), jobs (one run per period),
                   accounts (balances, primary account), budgets (limits and alerts),
                   insights (month-by-month comparison for the dashboard)
  importer/        import past records: extract (read xlsx, xls, csv, pdf, docx into tables), parse (guess the
                   columns, dates, amounts), service (check, duplicates, import in chunks, undo), router (/api/imports)
  accounting/      double-entry books: models, chart (Tally groups, financial years), engine (the only code that
                   posts vouchers), autopost (session hooks that post expenses, funds, transfers, charges, splits),
                   balances + reports (all reports), exports (Excel/PDF), gst (invoices), bank (statement import,
                   matching, reconciliation), router + router_reports (/api/accounting/...)
backend/tests/           pytest suite (auth, access rules, splits, subscriptions, accounts, budgets, insights,
                         accounting engine, auto-posting, every accounting report against a hand-worked example,
                         importing past records from every file format)
backend/sql/schema.sql   Postgres DDL reference
backend/scripts/         oracle_to_postgres.py (one-off data copy)
api/index.py             Vercel entry point for the backend
docs/DATABASE.md         table-by-table guide
frontend/src/
  auth.jsx         session, theme, "viewing user" selection
  components/      Layout, Tour, Avatar, Charts, Modal, BillForm, Accounts (account picker), ui helpers
  accounting/      Vouchers (+ VoucherForm with GST invoices), Ledgers, Reports (+ ReportViews), Bank,
                   AccountingSettings, common helpers
  pages/           Dashboard, DailyEntry, Expenses, DailyItems, Funds, Reports, Import (+ ImportHistory), Sharing,
                   Splits, SplitGroup, Subscriptions, Notifications, Profile, Settings, Admin, Login, Register, ForgotPassword,
                   ResetPassword
```
