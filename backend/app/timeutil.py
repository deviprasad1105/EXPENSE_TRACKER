from datetime import date, datetime
from zoneinfo import ZoneInfo

from .config import settings

_TZ = ZoneInfo(settings.timezone)


def now_local() -> datetime:
    """Naive datetime in the app's configured timezone (what we store in the DB)."""
    return datetime.now(_TZ).replace(tzinfo=None, microsecond=0)


def today_local() -> date:
    return now_local().date()


def validate_range(start: date, end: date) -> None:
    """Reject a reversed range, or one longer than MAX_REPORT_DAYS (reports build one entry per day)."""
    # Imported here so this module stays free of FastAPI for the scheduler jobs that use it
    from fastapi import HTTPException, status

    if start > end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Start date must be before end date")
    if (end - start).days + 1 > settings.max_report_days:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"The date range is too long: at most {settings.max_report_days} days ({settings.max_report_days // 366} years)",
        )
