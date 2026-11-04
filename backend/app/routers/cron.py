"""Scheduled jobs triggered over HTTP, for hosts without a long-running process (Vercel Cron, see vercel.json).

Vercel Cron sends "Authorization: Bearer <CRON_SECRET>". Both jobs claim their period in job_runs, so extra or
repeated calls do nothing.
"""

import secrets

from fastapi import APIRouter, Header, HTTPException, status

from ..config import settings
from ..services.reminders import daily_reminder_job
from ..services.subscriptions import subscription_job

router = APIRouter(prefix="/api/cron", tags=["cron"])


def _authorise(authorization: str | None) -> None:
    expected = f"Bearer {settings.cron_secret}"
    if not settings.cron_secret or not secrets.compare_digest((authorization or "").encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid cron secret")


@router.get("/daily-reminder")
def run_daily_reminder(authorization: str | None = Header(default=None)):
    _authorise(authorization)
    daily_reminder_job()
    return {"status": "ok"}


@router.get("/subscriptions")
def run_subscriptions(authorization: str | None = Header(default=None)):
    _authorise(authorization)
    subscription_job()
    return {"status": "ok"}
