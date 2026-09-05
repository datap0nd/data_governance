"""Application calendar policy, also included in portable Flow scripts."""
from datetime import datetime
from zoneinfo import ZoneInfo

TIMEZONE = 'Asia/Dubai'


def dubai_now():
    return datetime.now(ZoneInfo(TIMEZONE))


def dubai_today():
    return dubai_now().date()
