from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
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
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import StandardColumns
from app.models.parts import UNIT_COST

WO_KINDS = {"on_site": "On site", "bench": "Bench", "mail_in": "Mail-in", "warranty": "Warranty",
            "consult": "Consult"}
WO_STATUSES = {
    "new": "New",
    "scheduled": "Scheduled",
    "in_progress": "In progress",
    "waiting_parts": "Waiting on parts",
    "waiting_approval": "Waiting on approval",
    "ready": "Ready",
    "completed": "Completed",
    "cancelled": "Cancelled",
}
OPEN_WO_STATUSES = ("new", "scheduled", "in_progress", "waiting_parts", "waiting_approval", "ready")
PRIORITIES = {"low": "Low", "normal": "Normal", "high": "High", "rush": "Rush"}
JOB_STATUSES = {"open": "Open", "done": "Done", "cancelled": "Cancelled"}
RECEIVED_VIA = {"drop_off": "Dropped off", "pickup": "We picked it up", "mail": "Mailed in"}
LINE_KINDS = ("labor", "part", "fee", "sublet", "discount")
LABOR_MODES = {"actual": "Actual time", "flat": "Flat hours"}
RESERVATION_STATUSES = ("reserved", "issued", "released")
READING_PHASES = {"as_found": "As found", "during": "During", "as_left": "As left"}
ATTACHMENT_KINDS = ("photo", "pdf", "receipt", "signature", "rail_report", "other")


def _in(column, values):
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class WorkOrder(StandardColumns, db.Model):
    """A visit or a bench job: one customer, one or more jobs. The number is issued from
    doc_counter in the transaction that creates it."""

    __tablename__ = "work_order"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("number"),
        CheckConstraint("number ~ '^WO-[0-9]{4}-[0-9]{4,}$'", name="number_format"),
        CheckConstraint(_in("kind", WO_KINDS), name="kind"),
        CheckConstraint(_in("status", WO_STATUSES), name="status"),
        CheckConstraint(_in("priority", PRIORITIES), name="priority"),
        CheckConstraint("not_to_exceed IS NULL OR not_to_exceed >= 0", name="nte_nonnegative"),
        ForeignKeyConstraint(["site_id", "customer_id"], ["site.id", "site.customer_id"],
                             name="fk_work_order_site_same_customer"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customer.id"), nullable=False,
                                             index=True)
    site_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'new'"))
    priority: Mapped[str] = mapped_column(String(8), nullable=False,
                                          server_default=text("'normal'"))
    summary: Mapped[str] = mapped_column(String(200), nullable=False)
    promised_date: Mapped[date | None] = mapped_column(Date)
    not_to_exceed: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # A warranty WO points at the job it warrants.
    warranty_of_job_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("wo_job.id", use_alter=True, name="fk_work_order_warranty_of_job"))
    notes: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkOrderTech(StandardColumns, db.Model):
    """Who's assigned. Rows come and go (DELETE allowed, audited)."""

    __tablename__ = "work_order_tech"
    __audited__ = ()
    __table_args__ = (UniqueConstraint("work_order_id", "user_id", name="uq_work_order_tech"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    work_order_id: Mapped[int] = mapped_column(Integer, ForeignKey("work_order.id"),
                                               nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_user.id"), nullable=False)


class WoJob(StandardColumns, db.Model):
    """One concern on one asset: complaint, cause, correction (the mechanic's 3C). A
    consult can have no asset."""

    __tablename__ = "wo_job"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint(_in("status", JOB_STATUSES), name="status"),
        CheckConstraint("received_via IS NULL OR " + _in("received_via", RECEIVED_VIA),
                        name="received_via"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    work_order_id: Mapped[int] = mapped_column(Integer, ForeignKey("work_order.id"),
                                               nullable=False, index=True)
    asset_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("asset.id"), index=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'open'"))
    complaint: Mapped[str] = mapped_column(Text, nullable=False)
    cause: Mapped[str | None] = mapped_column(Text)
    correction: Mapped[str | None] = mapped_column(Text)
    # Intake, for bench and mail-in work.
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_via: Mapped[str | None] = mapped_column(String(10))
    condition: Mapped[str | None] = mapped_column(Text)
    accessories: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False,
                                                   server_default=text("'{}'"))
    accessories_note: Mapped[str | None] = mapped_column(String(200))
    inbound_tracking: Mapped[str | None] = mapped_column(String(60))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WoLine(StandardColumns, db.Model):
    """What the job is billed: labor, a part, a fee, a sublet or a discount. Amounts are
    qty × unit_price, rounded half-up; a discount line subtracts. unit_cost is a snapshot
    (a part's lot cost), never looked up later."""

    __tablename__ = "wo_line"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint(_in("kind", LINE_KINDS), name="kind"),
        CheckConstraint("(kind = 'labor') = (labor_mode IS NOT NULL)", name="labor_mode_iff_labor"),
        CheckConstraint("labor_mode IS NULL OR labor_mode IN ('actual', 'flat')", name="labor_mode"),
        # A part we sell is a catalog part; one the customer brought needn't be.
        CheckConstraint("kind <> 'part' OR part_id IS NOT NULL OR customer_supplied",
                        name="part_line_has_part"),
        CheckConstraint("qty > 0 OR (labor_mode = 'actual' AND qty >= 0)", name="qty_positive"),
        CheckConstraint("unit_price >= 0", name="price_nonnegative"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="cost_nonnegative"),
        CheckConstraint("warranty_days >= 0", name="warranty_nonnegative"),
        CheckConstraint("NOT customer_supplied OR (kind = 'part' AND unit_price = 0 AND "
                        "warranty_days = 0 AND stock_lot_id IS NULL)",
                        name="customer_supplied_part"),
        CheckConstraint("stock_lot_id IS NULL OR kind = 'part'", name="lot_only_on_parts"),
        ForeignKeyConstraint(["stock_lot_id", "part_id"], ["stock_lot.id", "stock_lot.part_id"],
                             name="fk_wo_line_lot_same_part"),
        # Time on the job feeds one actual-time labor line.
        Index("uq_wo_line_one_actual_labor", "wo_job_id", unique=True,
              postgresql_where=text("labor_mode = 'actual'")),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    wo_job_id: Mapped[int] = mapped_column(Integer, ForeignKey("wo_job.id"), nullable=False,
                                           index=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    service_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("service.id"))
    part_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("part.id"), index=True)
    stock_lot_id: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    labor_mode: Mapped[str | None] = mapped_column(String(8))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False,
                                                server_default=text("0"))
    unit_cost: Mapped[Decimal | None] = mapped_column(UNIT_COST)
    taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    warranty_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    billable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    customer_supplied: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                                    server_default=false())
    tech_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.id"))


class TimeEntry(StandardColumns, db.Model):
    """Time on a job: a running timer (ended_at null) or a finished entry. One running
    timer per user, enforced by a partial unique index."""

    __tablename__ = "time_entry"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint("source IN ('timer', 'manual')", name="source"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ends_after_start"),
        CheckConstraint("(ended_at IS NULL) = (minutes IS NULL)", name="minutes_when_ended"),
        CheckConstraint("minutes IS NULL OR minutes BETWEEN 0 AND 10080", name="minutes_range"),
        Index("uq_time_entry_one_running", "user_id", unique=True,
              postgresql_where=text("ended_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    wo_job_id: Mapped[int] = mapped_column(Integer, ForeignKey("wo_job.id"), nullable=False,
                                           index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_user.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    minutes: Mapped[int | None] = mapped_column(Integer)
    billable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))


class Reservation(StandardColumns, db.Model):
    """Stock set aside for a job. Available = on hand − reserved."""

    __tablename__ = "reservation"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint(_in("status", RESERVATION_STATUSES), name="status"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("unit_price IS NULL OR unit_price >= 0", name="price_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    part_id: Mapped[int] = mapped_column(Integer, ForeignKey("part.id"), nullable=False,
                                         index=True)
    wo_job_id: Mapped[int] = mapped_column(Integer, ForeignKey("wo_job.id"), nullable=False,
                                           index=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False,
                                        server_default=text("'reserved'"))
    note: Mapped[str | None] = mapped_column(String(200))
    # The price quoted on the estimate it came from; issuing uses it.
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))


class ManualReading(StandardColumns, db.Model):
    """A reading taken without GATBOX. `passed` is computed by Postgres from the spec, so
    it can't disagree with the numbers."""

    __tablename__ = "manual_reading"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint(_in("phase", READING_PHASES), name="phase"),
        CheckConstraint("spec_lo IS NULL OR spec_hi IS NULL OR spec_lo <= spec_hi",
                        name="spec_order"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    wo_job_id: Mapped[int] = mapped_column(Integer, ForeignKey("wo_job.id"), nullable=False,
                                           index=True)
    test_point: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(16, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(12), nullable=False)
    spec_lo: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    spec_hi: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    passed: Mapped[bool | None] = mapped_column(Boolean, Computed(
        "CASE WHEN spec_lo IS NULL AND spec_hi IS NULL THEN NULL "
        "ELSE (spec_lo IS NULL OR value >= spec_lo) AND (spec_hi IS NULL OR value <= spec_hi) END",
        persisted=True))
    phase: Mapped[str] = mapped_column(String(10), nullable=False)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                               server_default=func.now())
    note: Mapped[str | None] = mapped_column(String(200))


class Attachment(StandardColumns, db.Model):
    """A file in the file store (SHOP_FILES_DIR), named by the sha256 of its bytes.
    Photos are re-encoded on upload, which drops EXIF (GPS included)."""

    __tablename__ = "attachment"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint(_in("kind", ATTACHMENT_KINDS), name="kind"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    wo_job_id: Mapped[int] = mapped_column(Integer, ForeignKey("wo_job.id"), nullable=False,
                                           index=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    caption: Mapped[str | None] = mapped_column(String(200))
