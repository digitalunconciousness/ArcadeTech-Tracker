from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import StandardColumns
from app.models.parts import UNIT_COST

ESTIMATE_STATUSES = {
    "draft": "Draft",
    "sent": "Sent",
    "approved": "Approved",
    "declined": "Declined",
    "superseded": "Replaced by a revision",
    "converted": "Converted to a work order",
}
APPROVAL_METHODS = {"link": "Signed through the link", "on_screen": "Signed on our phone",
                    "verbal": "Approved verbally"}


def _in(column, values):
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class Estimate(StandardColumns, db.Model):
    """A quote. Revisions share the number (EST-2026-0004 r2) and point at the one they
    replace. Once approved_at is set, a trigger freezes the row, its jobs and its lines:
    only status (approved -> converted) and work_order_id may change after that."""

    __tablename__ = "estimate"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("number", "revision", name="uq_estimate_number_revision"),
        CheckConstraint("number ~ '^EST-[0-9]{4}-[0-9]{4,}$'", name="number_format"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(_in("status", ESTIMATE_STATUSES), name="status"),
        CheckConstraint("approval_method IS NULL OR " + _in("approval_method", APPROVAL_METHODS),
                        name="approval_method"),
        CheckConstraint("(approved_at IS NULL) = (approval_method IS NULL) AND "
                        "(approved_at IS NULL) = (approved_name IS NULL)", name="approval_complete"),
        CheckConstraint("approval_method <> 'link' OR (signature_sha256 IS NOT NULL AND "
                        "approval_ip IS NOT NULL)", name="link_approval_signed"),
        CheckConstraint("status NOT IN ('approved', 'converted') OR approved_at IS NOT NULL",
                        name="approved_has_approval"),
        CheckConstraint("signature_sha256 IS NULL OR signature_sha256 ~ '^[0-9a-f]{64}$'",
                        name="signature_sha256_hex"),
        CheckConstraint("not_to_exceed IS NULL OR not_to_exceed >= 0", name="nte_nonnegative"),
        CheckConstraint("deposit_required IS NULL OR deposit_required >= 0",
                        name="deposit_nonnegative"),
        ForeignKeyConstraint(["site_id", "customer_id"], ["site.id", "site.customer_id"],
                             name="fk_estimate_site_same_customer"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    supersedes_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("estimate.id"))
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customer.id"), nullable=False,
                                             index=True)
    site_id: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default=text("'draft'"))
    valid_until: Mapped[date | None] = mapped_column(Date)
    not_to_exceed: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    deposit_required: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    terms_snapshot: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_name: Mapped[str | None] = mapped_column(String(120))
    approval_method: Mapped[str | None] = mapped_column(String(10))
    approval_note: Mapped[str | None] = mapped_column(String(300))
    approval_ip: Mapped[str | None] = mapped_column(String(45))
    approval_ua: Mapped[str | None] = mapped_column(String(300))
    signature_sha256: Mapped[str | None] = mapped_column(String(64))
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decline_reason: Mapped[str | None] = mapped_column(String(300))
    # Set when converted; a revision of a converted estimate is a change order for it.
    work_order_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("work_order.id"),
                                                      index=True)


class EstimateJob(StandardColumns, db.Model):
    __tablename__ = "estimate_job"
    __audited__ = ()
    __table_args__ = (UniqueConstraint("id", "estimate_id", name="uq_estimate_job_id_estimate"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    estimate_id: Mapped[int] = mapped_column(Integer, ForeignKey("estimate.id"), nullable=False,
                                             index=True)
    asset_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("asset.id"))
    complaint: Mapped[str] = mapped_column(Text, nullable=False)


class EstimateLine(StandardColumns, db.Model):
    """A quoted line. Labor is flat (the quoted hours are billed) or actual (an estimate of
    hours; the timer decides). Parts carry their book price and, for margins, the part's
    default cost."""

    __tablename__ = "estimate_line"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint(_in("kind", ("labor", "part", "fee", "sublet", "discount")), name="kind"),
        CheckConstraint("(kind = 'labor') = (labor_mode IS NOT NULL)", name="labor_mode_iff_labor"),
        CheckConstraint("labor_mode IS NULL OR labor_mode IN ('actual', 'flat')", name="labor_mode"),
        CheckConstraint("kind <> 'part' OR part_id IS NOT NULL", name="part_line_has_part"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("unit_price >= 0", name="price_nonnegative"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="cost_nonnegative"),
        CheckConstraint("warranty_days >= 0", name="warranty_nonnegative"),
        ForeignKeyConstraint(["estimate_job_id", "estimate_id"],
                             ["estimate_job.id", "estimate_job.estimate_id"],
                             name="fk_estimate_line_job_same_estimate"),
        Index("ix_estimate_line_estimate_job_id", "estimate_job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    estimate_id: Mapped[int] = mapped_column(Integer, ForeignKey("estimate.id"), nullable=False,
                                             index=True)
    estimate_job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    service_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("service.id"))
    part_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("part.id"))
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    labor_mode: Mapped[str | None] = mapped_column(String(8))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(UNIT_COST)
    taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    warranty_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class DocLink(StandardColumns, db.Model):
    """A customer link, /d/<token>. Only the token's sha256 is stored, so the database
    can't open a link; "Send link" mints a new token each time. One document per link.
    Not audited: every view updates it."""

    __tablename__ = "doc_link"
    __table_args__ = (
        UniqueConstraint("token_sha256"),
        CheckConstraint("token_sha256 ~ '^[0-9a-f]{64}$'", name="token_sha256_hex"),
        CheckConstraint("view_count >= 0", name="views_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    estimate_id: Mapped[int] = mapped_column(Integer, ForeignKey("estimate.id"), nullable=False,
                                             index=True)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Appointment(StandardColumns, db.Model):
    __tablename__ = "appointment"
    __audited__ = ()
    __table_args__ = (CheckConstraint("ends_at > starts_at", name="ends_after_start"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    work_order_id: Mapped[int] = mapped_column(Integer, ForeignKey("work_order.id"),
                                               nullable=False, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    site_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("site.id"))
    note: Mapped[str | None] = mapped_column(String(300))


class AppointmentUser(StandardColumns, db.Model):
    __tablename__ = "appointment_user"
    __audited__ = ()
    __table_args__ = (UniqueConstraint("appointment_id", "user_id", name="uq_appointment_user"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    appointment_id: Mapped[int] = mapped_column(Integer, ForeignKey("appointment.id"),
                                                nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_user.id"), nullable=False,
                                         index=True)


class CalendarFeed(StandardColumns, db.Model):
    """A user's private .ics feed (/d/cal/<token>.ics), token stored hashed. One live
    feed per user; making a new one revokes the old."""

    __tablename__ = "calendar_feed"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("token_sha256"),
        CheckConstraint("token_sha256 ~ '^[0-9a-f]{64}$'", name="token_sha256_hex"),
        Index("uq_calendar_feed_one_live", "user_id", unique=True,
              postgresql_where=text("revoked_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_user.id"), nullable=False)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
