from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Integer, Numeric, String, Text, text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import StandardColumns

DEFAULT_BUSINESS_NAME = "ArcadeTech Tracker"
DEFAULT_ACCENT = "#b0126f"


class ShopSetting(StandardColumns, db.Model):
    """The business's own details, one row. The display time zone is SHOP_TZ (env)."""

    __tablename__ = "shop_setting"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint("id", name="single_row"),
        CheckConstraint("doc_accent_color ~ '^#[0-9a-fA-F]{6}$'", name="accent_hex"),
        CheckConstraint("labor_increment_hours > 0", name="labor_increment_positive"),
        CheckConstraint("labor_minimum_hours >= 0", name="labor_minimum_nonnegative"),
        CheckConstraint("estimate_link_days BETWEEN 1 AND 365", name="estimate_link_days_range"),
    )

    id: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default=true())
    business_name: Mapped[str] = mapped_column(
        String(120), nullable=False, server_default=text(f"'{DEFAULT_BUSINESS_NAME}'")
    )
    legal_name: Mapped[str | None] = mapped_column(String(160))
    address_line1: Mapped[str | None] = mapped_column(String(160))
    address_line2: Mapped[str | None] = mapped_column(String(160))
    city: Mapped[str | None] = mapped_column(String(80))
    region: Mapped[str | None] = mapped_column(String(40))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(254))
    website: Mapped[str | None] = mapped_column(String(200))
    # The one accent color on printed customer documents (white page, black text).
    doc_accent_color: Mapped[str] = mapped_column(
        String(7), nullable=False, server_default=text(f"'{DEFAULT_ACCENT}'")
    )
    # Labor billing (owner, 2026-10-07): actual time rounded UP to the increment, then at
    # least the minimum per job.
    labor_increment_hours: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, server_default=text("0.25"))
    labor_minimum_hours: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, server_default=text("0.50"))
    # Printed on customer documents. The owner writes them (and has a lawyer look).
    claim_terms: Mapped[str | None] = mapped_column(Text)
    warranty_terms: Mapped[str | None] = mapped_column(Text)
    # Estimates can't be sent until these exist (owner, 2026-10-08).
    estimate_terms: Mapped[str | None] = mapped_column(Text)
    estimate_link_days: Mapped[int] = mapped_column(Integer, nullable=False,
                                                    server_default=text("30"))
