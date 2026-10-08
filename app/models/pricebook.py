from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import StandardColumns

SERVICE_KINDS = ("labor", "fee", "trip", "diagnostic", "shop_supplies", "sublet", "discount")
SERVICE_UNITS = ("hour", "each", "mile")


class Service(StandardColumns, db.Model):
    """The labor and fee catalog. Rates are what the customer pays (NUMERIC(12,2))."""

    __tablename__ = "service"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("code"),
        CheckConstraint("kind IN ('labor', 'fee', 'trip', 'diagnostic', 'shop_supplies', "
                        "'sublet', 'discount')", name="kind"),
        CheckConstraint("unit IN ('hour', 'each', 'mile')", name="unit"),
        CheckConstraint("rate >= 0", name="rate_nonnegative"),
        CheckConstraint("warranty_days >= 0", name="warranty_nonnegative"),
        CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9-]{0,19}$'", name="code_format"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    unit: Mapped[str] = mapped_column(String(8), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0"))
    taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    # Repairs carry a year (owner, 2026-10-07); a fee row sets 0.
    warranty_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("365"))
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class JobTemplate(StandardColumns, db.Model):
    """A canned job ("Cap kit, K4600 chassis"): services and parts that fill an estimate."""

    __tablename__ = "job_template"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("name"),
        CheckConstraint("est_hours IS NULL OR est_hours >= 0", name="est_hours_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    est_hours: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class JobTemplateLine(StandardColumns, db.Model):
    __tablename__ = "job_template_line"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint("(service_id IS NULL) <> (part_id IS NULL)", name="service_xor_part"),
        CheckConstraint("qty > 0", name="qty_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    template_id: Mapped[int] = mapped_column(Integer, ForeignKey("job_template.id"),
                                             nullable=False, index=True)
    service_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("service.id"))
    part_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("part.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))


class MarkupTier(StandardColumns, db.Model):
    """Parts priced by markup: unit cost in [min_cost, max_cost) × multiplier. A null
    max_cost is open-ended. Brackets can't overlap: an exclusion constraint on
    numrange(min_cost, max_cost) (migration 0005; autogenerate doesn't see it)."""

    __tablename__ = "markup_tier"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("min_cost"),
        CheckConstraint("min_cost >= 0", name="min_nonnegative"),
        CheckConstraint("max_cost IS NULL OR max_cost > min_cost", name="max_above_min"),
        CheckConstraint("multiplier > 0", name="multiplier_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    min_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    max_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    multiplier: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
