import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, select

from .accounting import router as accounting
from .config import settings
from .importer import router as importer
from .database import SessionLocal, engine
from .deps import FEATURE_ACCOUNTING, FEATURE_EXPENSE, FEATURE_SUBSCRIPTION, require_feature
from .migrate import sync_schema
from .models import ROLE_ADMIN, STATUS_APPROVED, JobRun, User
from .routers import (
    accounts, admin, auth, budgets, cron, daily_items, dashboard, expenses, funds, notifications, org, profile, reports, shares,
    splits, subscriptions,
)
from .security import hash_password
from .services.reminders import daily_reminder_job
from .services.subscriptions import subscription_job
from .timeutil import now_local

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("expense_tracker")


def seed_admin():
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.username == settings.admin_username)):
            return
        db.add(
            User(
                username=settings.admin_username,
                email=settings.admin_email,
                full_name="Administrator",
                password_hash=hash_password(settings.admin_password),
                role=ROLE_ADMIN,
                status=STATUS_APPROVED,
                approved_at=now_local(),
            )
        )
        db.commit()
        log.info("Created admin user '%s'", settings.admin_username)


def check_settings():
    problems = settings.insecure_defaults()
    if not problems:
        return
    if settings.is_production:
        raise RuntimeError("Refusing to start in production: " + "; ".join(problems) + ". Fix them in backend/.env")
    for problem in problems:
        log.warning("INSECURE SETTING (fine for local development only): %s", problem)


def prune_job_runs():
    with SessionLocal() as db:
        db.execute(delete(JobRun).where(JobRun.started_at < now_local() - timedelta(days=60)))
        db.commit()


_initialised = False


def init_app():
    """Check settings, sync the schema and seed the admin. Runs once per process (on Vercel: once per cold start)."""
    global _initialised
    if _initialised:
        return
    check_settings()
    sync_schema(engine)
    seed_admin()
    prune_job_runs()
    _initialised = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_app()
    if settings.serverless:
        # Serverless instances are frozen between requests, so Vercel Cron calls /api/cron/* instead
        yield
        return
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(
        daily_reminder_job,
        CronTrigger(hour=settings.reminder_hour, minute=settings.reminder_minute, timezone=settings.timezone),
        id="daily_reminder",
    )
    scheduler.add_job(
        subscription_job,
        CronTrigger(minute=settings.subscription_job_minute, timezone=settings.timezone),
        id="subscriptions",
    )
    # Catch up on charges and reminders missed while the server was down
    scheduler.add_job(subscription_job, id="subscriptions_startup")
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="Expense Tracker API",
    lifespan=lifespan,
    # The interactive docs list every endpoint, so they are off in production
    docs_url=None if settings.is_production else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/api/openapi.json",
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/docs"):
        response.headers.setdefault("Cache-Control", "no-store")
    if settings.is_production:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

for r in (auth, admin, org, profile, notifications, splits, cron):
    app.include_router(r.router)
# Expense tracking is a feature the admin grants per user
for r in (expenses, daily_items, funds, dashboard, reports, shares, accounts, budgets):
    app.include_router(r.router, dependencies=[Depends(require_feature(FEATURE_EXPENSE))])
app.include_router(importer.router, dependencies=[Depends(require_feature(FEATURE_EXPENSE))])
app.include_router(subscriptions.router, dependencies=[Depends(require_feature(FEATURE_SUBSCRIPTION))])
app.include_router(accounting.router, dependencies=[Depends(require_feature(FEATURE_ACCOUNTING))])
accounting.register_error_handler(app)


@app.get("/api/health")
def health():
    return {"status": "ok"}
