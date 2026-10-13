"""GATBOX, the bench Pi: the devices allowed to push here, and what they push (contract
v1, contract/v1/). A device is not a user: no session, no role, nothing a person sees.
It authenticates with a bearer token to /api/v1 and nothing else.

Traces are evidence: the app role may add them and read them, never change them. Measured
values are NUMERIC, stored exactly as the Pi sent them (the request is parsed with
Decimal, so binary floating point never touches them); times are timestamptz from the
contract's epoch. Which job a trace belongs to, and its phase there, lives in
wo_job_session, which can be changed."""

import secrets
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models.base import StandardColumns
from app.models.work import READING_PHASES

# A token reads gbx_<public_id>.<secret> (contract v1, "Authentication"). The public id is
# stored in the clear and indexed, because the secret is only kept as a hash and a hash
# can't be looked up by: the device is found first, then its secret checked.
TOKEN_PREFIX = "gbx_"
PUBLIC_ID_CHARS = 12          # hex
SECRET_BYTES = 32             # 43 characters of URL-safe base64

VERDICTS = ("held", "left", "over")
TRACE_PHASES = tuple(READING_PHASES)
LINK_SOURCES = {"auto": "Attached on arrival", "order": "Raised with an order",
                "tag": "Tagged at the bench", "manual": "Attached here"}
COUNTS = ("over_voltage", "excursions", "power_cycles", "gaps", "ol_events", "marks",
          "suspect")

_UID = "^[0-9a-f]{32}$"


def _in(column, values):
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class Device(StandardColumns, db.Model):
    """A machine client allowed to post to the v1 API. Switched off, never deleted: its
    traces keep pointing at it."""

    __tablename__ = "device"
    # Audited; the hash never reaches audit_log.
    __audited__ = ("token_hash",)
    __table_args__ = (
        UniqueConstraint("name"),
        UniqueConstraint("public_id"),
        CheckConstraint("public_id ~ '^[0-9a-f]{12}$'", name="public_id_format"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    public_id: Mapped[str] = mapped_column(String(12), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ip: Mapped[str | None] = mapped_column(String(45))
    notes: Mapped[str | None] = mapped_column(Text)

    @staticmethod
    def new_public_id():
        return secrets.token_hex(PUBLIC_ID_CHARS // 2)

    def issue_token(self):
        """Mint a new token, keep only its hash, and return it: the one time anyone sees
        it. A leaked database doesn't leak a working token."""
        if not self.public_id:
            self.public_id = self.new_public_id()
        secret = secrets.token_urlsafe(SECRET_BYTES)
        self.token_hash = generate_password_hash(secret)
        return f"{TOKEN_PREFIX}{self.public_id}.{secret}"

    def check_secret(self, secret):
        return check_password_hash(self.token_hash, secret)

    @staticmethod
    def split_token(token):
        """gbx_<public_id>.<secret> -> (public_id, secret), or None. Shape only."""
        if not token or not token.startswith(TOKEN_PREFIX):
            return None
        public_id, _, secret = token[len(TOKEN_PREFIX):].partition(".")
        if not public_id or not secret:
            return None
        return public_id, secret


class RailSession(StandardColumns, db.Model):
    """One metered session, as GATBOX summarised it. The hub stores what the Pi computed
    and never recomputes it (contract v1, "rail_session")."""

    __tablename__ = "rail_session"
    __table_args__ = (
        UniqueConstraint("uid"),
        CheckConstraint(f"uid ~ '{_UID}'", name="uid_format"),
        CheckConstraint(f"verdict IS NULL OR {_in('verdict', VERDICTS)}", name="verdict"),
        CheckConstraint(f"phase IS NULL OR {_in('phase', TRACE_PHASES)}", name="phase"),
        CheckConstraint(" AND ".join(f"{c} >= 0" for c in COUNTS), name="counts_nonnegative"),
        Index("ix_rail_session_asset_started", "asset_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    uid: Mapped[str] = mapped_column(String(32), nullable=False)
    device_id: Mapped[int] = mapped_column(Integer, ForeignKey("device.id"), nullable=False,
                                           index=True)
    asset_id: Mapped[int] = mapped_column(Integer, ForeignKey("asset.id"), nullable=False)
    file: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[Decimal | None] = mapped_column(Numeric)
    samples: Mapped[int | None] = mapped_column(Integer)
    rate: Mapped[Decimal | None] = mapped_column(Numeric)
    timebase: Mapped[str | None] = mapped_column(Text)
    clock: Mapped[str | None] = mapped_column(Text)
    clock_ntp_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile_id: Mapped[str | None] = mapped_column(Text)
    profile_label: Mapped[str | None] = mapped_column(Text)
    profile_kind: Mapped[str | None] = mapped_column(Text)
    rail: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str | None] = mapped_column(Text)
    window_lo: Mapped[Decimal | None] = mapped_column(Numeric)
    window_hi: Mapped[Decimal | None] = mapped_column(Numeric)
    window_unit: Mapped[str | None] = mapped_column(Text)
    window_source: Mapped[str | None] = mapped_column(Text)
    alarm_hi: Mapped[Decimal | None] = mapped_column(Numeric)
    powered_mean: Mapped[Decimal | None] = mapped_column(Numeric)
    powered_min: Mapped[Decimal | None] = mapped_column(Numeric)
    powered_max: Mapped[Decimal | None] = mapped_column(Numeric)
    powered_readings: Mapped[int | None] = mapped_column(Integer)
    in_window_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    verdict: Mapped[str | None] = mapped_column(String(4))
    verdict_detail: Mapped[str | None] = mapped_column(Text)
    over_voltage: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    excursions: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    power_cycles: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    gaps: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    ol_events: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    marks: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    suspect: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # The phase the bench gave it, if any (an optional addition to v1). Each job it's on
    # keeps its own phase in wo_job_session.
    phase: Mapped[str | None] = mapped_column(String(10))


class Reading(StandardColumns, db.Model):
    """One point of a session's downsampled trace (at most 2,000 per session)."""

    __tablename__ = "reading"
    __table_args__ = (
        UniqueConstraint("uid"),
        CheckConstraint(f"uid ~ '{_UID}'", name="uid_format"),
        Index("ix_reading_session_at", "rail_session_id", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    uid: Mapped[str] = mapped_column(String(32), nullable=False)
    rail_session_id: Mapped[int] = mapped_column(Integer, ForeignKey("rail_session.id"),
                                                 nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    up: Mapped[Decimal | None] = mapped_column(Numeric)
    v: Mapped[Decimal | None] = mapped_column(Numeric)
    raw: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str | None] = mapped_column(Text)
    alarm: Mapped[str | None] = mapped_column(Text)
    ol: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())


class WoJobSession(StandardColumns, db.Model):
    """A trace on a job, and its phase there. Attached on arrival (the asset had one open
    job), with an order raised at the bench, by a session tag, or by hand on the job page.
    Detaching deletes the row; the trace stays on the asset."""

    __tablename__ = "wo_job_session"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("wo_job_id", "rail_session_id"),
        CheckConstraint(f"phase IS NULL OR {_in('phase', TRACE_PHASES)}", name="phase"),
        CheckConstraint(_in("source", LINK_SOURCES), name="source"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    wo_job_id: Mapped[int] = mapped_column(Integer, ForeignKey("wo_job.id"), nullable=False,
                                           index=True)
    rail_session_id: Mapped[int] = mapped_column(Integer, ForeignKey("rail_session.id"),
                                                 nullable=False, index=True)
    phase: Mapped[str | None] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))
