"""Configuration from the environment. Missing or unsafe values stop the app at startup."""

import os
from datetime import timedelta
from urllib.parse import urlsplit
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

    library = library_settings(env)

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
        **library,
    }


def library_settings(env):
    """The GATBOX archive this shop reads the game library from (contract/library-v1/).

    LIBRARY_ARCHIVE_URL is the archive's address for the pull job: its private address
    (plain http on the node's own network is fine, the token is read-only) or its tunnel
    address (https, with the Cloudflare Access service token pair). LIBRARY_ARCHIVE_WEB_URL
    is where people open the archive in a browser, for the "edit it in the library" link; it
    is optional. Without the first two the library is off: the pull job does nothing and the
    asset page has no game section. One half of a pair is a mistake, said at startup."""
    url = env.get("LIBRARY_ARCHIVE_URL", "").strip().rstrip("/")
    token = env.get("LIBRARY_ARCHIVE_TOKEN", "").strip()
    access_id = env.get("LIBRARY_ACCESS_CLIENT_ID", "").strip()
    access_secret = env.get("LIBRARY_ACCESS_CLIENT_SECRET", "").strip()
    web = env.get("LIBRARY_ARCHIVE_WEB_URL", "").strip().rstrip("/")

    def address(name, value):
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.path or parts.query \
                or parts.fragment:
            raise ConfigError(f"{name}={value!r} must be just an address, like "
                              "http://archive.example.test:8081 or https://archive.example.org")

    if url:
        address("LIBRARY_ARCHIVE_URL", url)
    if web:
        address("LIBRARY_ARCHIVE_WEB_URL", web)
    if token and not (token.startswith("gbx_") and "." in token):
        raise ConfigError("LIBRARY_ARCHIVE_TOKEN must be a reader token of the form gbx_<id>.<secret>")
    if bool(access_id) != bool(access_secret):
        raise ConfigError("LIBRARY_ACCESS_CLIENT_ID and LIBRARY_ACCESS_CLIENT_SECRET go together")
    try:
        max_mb = int(env.get("LIBRARY_MAX_FILE_MB", "100"))
    except ValueError:
        raise ConfigError("LIBRARY_MAX_FILE_MB must be a whole number of megabytes") from None
    if max_mb < 1:
        raise ConfigError("LIBRARY_MAX_FILE_MB must be at least 1")
    on = bool(url and token)                  # half a setting is no setting
    return {
        "LIBRARY_URL": url if on else "",
        "LIBRARY_TOKEN": token if on else "",
        "LIBRARY_ACCESS_ID": access_id if on else "",
        "LIBRARY_ACCESS_SECRET": access_secret if on else "",
        "LIBRARY_WEB_URL": web if on else "",
        "LIBRARY_MAX_FILE": max_mb * 1024 * 1024,
    }
