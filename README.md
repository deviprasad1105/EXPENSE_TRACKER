# Expense Tracker (Python + React + Oracle)

Daily expense tracker with customisable daily items, a balance dashboard with charts, Excel/PDF reports,
missed-entry reminders, a manager hierarchy, read-only sharing, profiles, and admin approval of new users.

| Layer    | Tech |
|----------|------|
| Backend  | FastAPI, SQLAlchemy 2, python-oracledb (thin mode, no Instant Client needed), APScheduler, openpyxl, reportlab |
| Frontend | React 18 + Vite, React Router, Recharts, Material UI icons (and lucide-react on older pages), Inter font |
| Database | Oracle 23ai Free: existing `face_tracker_oracle` container, schema **`DEV_DR`** in `FREEPDB1` |

## Run it

```bash
# 1. Database: no new container. Uses face_tracker_oracle (localhost:1521), schema DEV_DR.
#    Put the DEV_DR password in backend/.env (DATABASE_URL). DEV_DR needs CREATE TABLE + CREATE SEQUENCE.

# 2. Backend → http://localhost:8000   API docs → http://localhost:8000/api/docs
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # set the DEV_DR password, JWT secret, reminder time, SMTP
.venv/bin/uvicorn app.main:app --reload --port 8000

# 3. Frontend → http://localhost:5173
cd frontend
npm install
npm run dev
```

On first start the backend creates its 22 tables in `DEV_DR` and an admin from `.env`
(default **admin / admin123**, change it: with `ENVIRONMENT=production` the backend refuses to start while the admin
password is the default or `JWT_SECRET` is shorter than 32 characters). **[docs/DATABASE.md](docs/DATABASE.md)** describes every table,
what it stores, and how the rules work. `backend/sql/schema.sql` holds the equivalent Oracle DDL.

### Tests

```bash
cd backend
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

They run against an in-memory SQLite database, so they need neither Oracle nor a running server.

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
  (only the last 4 digits are stored). Mark one **primary**. Money received (*Balance*) is recorded against an
  account, Daily Entry asks **which account paid** (starting on the primary one), and *Transfers between accounts*
  records money moved from one of your accounts to another without counting it as income or spending. Each
  account shows its balance and this month's spending. Turn off "Ask which account for each expense" to send
  everything to the primary account automatically. Subscription charges are paid from the primary account.
- **Custom background** (*Settings → Appearance*): upload your own picture (up to 5 MB) to show behind the app;
  *Reset to default* removes it.
- **Reports**: Excel (summary with charts, daily totals, detailed list) or PDF for any period.
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
  a password, *Settings → Sign out other devices*, and an admin lock sign out every other session at once.
  Passwords need at least 8 characters. Responses carry security headers; in production the API docs are hidden
  and HSTS is sent.
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
backend/tests/           pytest suite (auth, access rules, splits, subscriptions, accounts, budgets, insights)
backend/sql/schema.sql   Oracle DDL reference
docs/DATABASE.md         table-by-table guide
frontend/src/
  auth.jsx         session, theme, "viewing user" selection
  components/      Layout, Tour, Avatar, Charts, Modal, BillForm, Accounts (account picker), ui helpers
  pages/           Dashboard, DailyEntry, Expenses, DailyItems, Funds, Reports, Sharing,
                   Splits, SplitGroup, Subscriptions, Notifications, Profile, Settings, Admin, Login, Register, ForgotPassword,
                   ResetPassword
```
