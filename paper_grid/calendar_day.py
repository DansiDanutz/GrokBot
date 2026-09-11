"""Shared Bucharest civil-day contract; storage archive dates remain UTC."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ZONE_NAME = 'Europe/Bucharest'
ZONE = ZoneInfo(ZONE_NAME)
DAILY_BOUNDARY_HOUR = 0
WEEKLY_BOUNDARY_HOUR = 9


def day_label(at):
    return datetime.fromtimestamp(at, ZONE).date().isoformat()


def next_midnight(after):
    day = datetime.fromtimestamp(after, ZONE).date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, DAILY_BOUNDARY_HOUR, tzinfo=ZONE).timestamp()


def day_samples(start, end):
    """One timestamp for every included civil day, even across DST changes."""
    at = start
    while at <= end:
        yield at
        at = next_midnight(at)


def contains(at, start, end, *, include_start=False, include_end=True):
    return (at >= start if include_start else at > start) and (at <= end if include_end else at < end)
