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
