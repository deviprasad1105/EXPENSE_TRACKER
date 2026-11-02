from datetime import date, datetime
from zoneinfo import ZoneInfo

from .config import settings

_TZ = ZoneInfo(settings.timezone)


def now_local() -> datetime:
    """Naive datetime in the app's configured timezone (what we store in the DB)."""
    return datetime.now(_TZ).replace(tzinfo=None, microsecond=0)


def today_local() -> date:
    return now_local().date()
