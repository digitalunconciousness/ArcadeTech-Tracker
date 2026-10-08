from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import StandardColumns

ASSET_KINDS = ("machine", "board", "monitor", "chassis", "psu", "other")
ASSET_STATUSES = {
    "in_service": "In service",
    "in_shop": "In the shop",
    "awaiting_pickup": "Awaiting pickup",
    "shipped_back": "Shipped back",
    "sold": "Sold",
    "scrapped": "Scrapped",
}
EVENT_KINDS = ("created", "intake", "returned", "ownership_change", "moved", "status", "note")

# GATBOX-safe: s-<number>-<name slug>, lowercase letters, digits, single dashes.
TAG_RE = r"^s-[0-9]{4,}(-[a-z0-9]+)+$"
TAG_MAX = 40


class Asset(StandardColumns, db.Model):
    """A customer's machine or board (or the shop's own exchange unit). The tag is
    assigned once from asset_tag_seq and can never change or be reused: a trigger
    refuses UPDATEs of it and the app role can't DELETE."""

    __tablename__ = "asset"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("tag"),
        # Target of the parent FK, which keeps a board with the same owner as its machine.
        UniqueConstraint("id", "customer_id", name="uq_asset_id_customer"),
        CheckConstraint(f"tag ~ '{TAG_RE}' AND length(tag) <= {TAG_MAX}", name="tag_format"),
        CheckConstraint("kind IN ('machine', 'board', 'monitor', 'chassis', 'psu', 'other')",
                        name="kind"),
        CheckConstraint(
            "status IN ('in_service', 'in_shop', 'awaiting_pickup', 'shipped_back', 'sold', "
            "'scrapped')", name="status"),
        CheckConstraint("year BETWEEN 1950 AND 2100", name="year_range"),
        CheckConstraint("model_key ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="model_key_format"),
        CheckConstraint("parent_asset_id IS NULL OR parent_asset_id <> id", name="not_own_parent"),
        ForeignKeyConstraint(["site_id", "customer_id"], ["site.id", "site.customer_id"],
                             name="fk_asset_site_same_customer"),
        ForeignKeyConstraint(["parent_asset_id", "customer_id"], ["asset.id", "asset.customer_id"],
                             name="fk_asset_parent_same_customer"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    tag: Mapped[str] = mapped_column(String(TAG_MAX), nullable=False)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customer.id"), nullable=False,
                                             index=True)
    site_id: Mapped[int | None] = mapped_column(Integer)
    parent_asset_id: Mapped[int | None] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(80))
    # Slug of the GATBOX manuals library entry, when there is one.
    model_key: Mapped[str | None] = mapped_column(String(60))
    year: Mapped[int | None] = mapped_column(SmallInteger)
    serial: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    in_shop_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shop_location: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)
    label_printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssetEvent(StandardColumns, db.Model):
    """History that follows the asset across owners. Append-only (INSERT and SELECT
    for the app role). created_by is who; `at` is when it happened."""

    __tablename__ = "asset_event"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('created', 'intake', 'returned', 'ownership_change', 'moved', 'status', "
            "'note')", name="kind"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    asset_id: Mapped[int] = mapped_column(Integer, ForeignKey("asset.id"), nullable=False,
                                          index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                         server_default=func.now())
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    from_value: Mapped[str | None] = mapped_column(String(200))
    to_value: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
