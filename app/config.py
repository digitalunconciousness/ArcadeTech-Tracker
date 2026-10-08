"""Configuration from the environment. Missing or unsafe values stop the app at startup."""

import os
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(RuntimeError):
    pass


def load_config(env=None):
    env = os.environ if env is None else env

    url = env.get("DATABASE_URL", "")
    if not url.startswith("postgresql+psycopg://"):
        raise ConfigError("DATABASE_URL must be a postgresql+psycopg:// URL (PostgreSQL only)")

    secret = env.get("SECRET_KEY", "")
    if len(secret) < 32:
        raise ConfigError("SECRET_KEY must be set, at least 32 characters (python -c "
                          "'import secrets; print(secrets.token_hex(32))')")

    tz = env.get("SHOP_TZ", "")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"SHOP_TZ must be an IANA time zone name, got {tz!r}") from None

    base_url = env.get("SHOP_BASE_URL", "").rstrip("/")
    if not base_url.startswith(("https://", "http://")):
        raise ConfigError("SHOP_BASE_URL must be the app's public URL, e.g. https://shop.example.com")

    return {
        "SQLALCHEMY_DATABASE_URI": url,
        "SQLALCHEMY_ENGINE_OPTIONS": {"pool_pre_ping": True},
        "SECRET_KEY": secret,
        "SHOP_TZ": tz,
        "SHOP_BASE_URL": base_url,
        "SESSION_COOKIE_NAME": "shop_session",
        # Off only for plain-http local dev (SHOP_COOKIE_SECURE=0); the tunnel is https.
        "SESSION_COOKIE_SECURE": env.get("SHOP_COOKIE_SECURE", "1") != "0",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=12),
        "REMEMBER_COOKIE_DURATION": timedelta(seconds=0),
        # CSRF tokens live as long as the session rather than an hour.
        "WTF_CSRF_TIME_LIMIT": None,
        "RATELIMIT_STORAGE_URI": "memory://",
        "RATELIMIT_HEADERS_ENABLED": True,
        "MAX_CONTENT_LENGTH": 16 * 1024 * 1024,
        # Photos, signatures and frozen PDFs; backed up with the database (backup.sh).
        "SHOP_FILES_DIR": env.get("SHOP_FILES_DIR", "/var/lib/shop-hub/files"),
    }
