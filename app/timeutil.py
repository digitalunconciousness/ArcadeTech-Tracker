"""Times are stored in UTC (timestamptz) and shown in SHOP_TZ."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from flask import current_app


def shop_tz():
    return ZoneInfo(current_app.config["SHOP_TZ"])


def utcnow():
    return datetime.now(UTC)


def local_today(now=None):
    """The business's calendar date, which decides a document number's year."""
    return (now or utcnow()).astimezone(shop_tz()).date()


def localdt(value, fmt="%Y-%m-%d %H:%M"):
    if value is None:
        return ""
    return value.astimezone(shop_tz()).strftime(fmt)


def to_local_naive(value):
    """For a datetime-local input: the shop's wall-clock time, no zone."""
    return None if value is None else value.astimezone(shop_tz()).replace(tzinfo=None, second=0,
                                                                             microsecond=0)


def from_local_naive(value):
    """A datetime-local input's value, read as the shop's wall-clock time, in UTC."""
    return None if value is None else value.replace(tzinfo=shop_tz()).astimezone(UTC)
