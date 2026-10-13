"""Customer links, /d/<token>: 32 random bytes, read-only, expiring and revocable. The
database keeps only the token's sha256, so it can't open a link. One document per link:
an estimate, an invoice, or a customer's statement."""

import hashlib
import secrets

from sqlalchemy import select

from app.extensions import db
from app.models import DocLink
from app.timeutil import utcnow

TOKEN_BYTES = 32
TOKEN_LENGTH = 43   # token_urlsafe(32)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_token():
    return secrets.token_urlsafe(TOKEN_BYTES)


def live_link(token):
    """The DocLink for a live token, else None. Malformed tokens never reach the
    database; an expired or withdrawn one looks the same as one that never existed."""
    if not isinstance(token, str) or len(token) != TOKEN_LENGTH or \
            not all(c.isalnum() or c in "-_" for c in token):
        return None
    link = db.session.scalar(select(DocLink).where(DocLink.token_sha256 == token_hash(token)))
    if link is None or link.revoked_at is not None or link.expires_at <= utcnow():
        return None
    return link
