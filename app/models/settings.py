from sqlalchemy import Boolean, CheckConstraint, String, text, true
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
