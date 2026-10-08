from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
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

PRICE_MODES = ("fixed", "markup")
MOVE_REASONS = ("receive", "issue", "return", "adjust", "count", "scrap", "rma_out")

# Unit costs carry 4 decimals: a resistor bought by the hundred costs $0.012, and cents
# would be a 17% error. Prices and every amount on a document stay NUMERIC(12,2).
UNIT_COST = Numeric(14, 4)


class Vendor(StandardColumns, db.Model):
    __tablename__ = "vendor"
    __audited__ = ()

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    website: Mapped[str | None] = mapped_column(String(200))
    account_ref: Mapped[str | None] = mapped_column(String(60))
    contact: Mapped[str | None] = mapped_column(String(200))
    # Our resale certificate is on file with them, so resale parts come in untaxed.
    resale_cert_on_file: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class Part(StandardColumns, db.Model):
    __tablename__ = "part"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("sku"),
        CheckConstraint("price_mode IN ('fixed', 'markup')", name="price_mode"),
        CheckConstraint("default_cost IS NULL OR default_cost >= 0", name="cost_nonnegative"),
        CheckConstraint("sell_price IS NULL OR sell_price >= 0", name="price_nonnegative"),
        CheckConstraint("reorder_point IS NULL OR reorder_point >= 0", name="reorder_nonnegative"),
        CheckConstraint("reorder_qty IS NULL OR reorder_qty > 0", name="reorder_qty_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    sku: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str | None] = mapped_column(String(60))
    manufacturer: Mapped[str | None] = mapped_column(String(80))
    mpn: Mapped[str | None] = mapped_column(String(80))
    # Interchangeable parts: 74LS245 <-> 74HCT245. Searchable.
    equivalents: Mapped[list[str]] = mapped_column(ARRAY(String(80)), nullable=False,
                                                   server_default=text("'{}'"))
    unit: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'each'"))
    default_cost: Mapped[Decimal | None] = mapped_column(UNIT_COST)
    price_mode: Mapped[str] = mapped_column(String(8), nullable=False,
                                            server_default=text("'fixed'"))
    sell_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    # Consumables (solder, flux, cleaner) aren't inventory: they're expenses.
    track_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    reorder_point: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    reorder_qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    preferred_vendor_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vendor.id"))
    bin: Mapped[str | None] = mapped_column(String(40))
    barcode: Mapped[str | None] = mapped_column(String(64))
    datasheet_url: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class StockLot(StandardColumns, db.Model):
    """One receipt of a part at one cost. Lots give FIFO cost and recall tracing. A lot is
    never edited; what's left in it is the sum of its moves."""

    __tablename__ = "stock_lot"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("id", "part_id", name="uq_stock_lot_id_part"),
        CheckConstraint("unit_cost >= 0", name="cost_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    part_id: Mapped[int] = mapped_column(Integer, ForeignKey("part.id"), nullable=False, index=True)
    unit_cost: Mapped[Decimal] = mapped_column(UNIT_COST, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.now())
    vendor_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vendor.id"))
    date_code: Mapped[str | None] = mapped_column(String(20))
    vendor_lot: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(String(200))


class StockMove(StandardColumns, db.Model):
    """The stock ledger. Append-only; on hand = the sum of moves. A trigger refuses a move
    that would take its lot below zero."""

    __tablename__ = "stock_move"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint("qty <> 0", name="qty_nonzero"),
        CheckConstraint("reason IN ('receive', 'issue', 'return', 'adjust', 'count', 'scrap', "
                        "'rma_out')", name="reason"),
        ForeignKeyConstraint(["lot_id", "part_id"], ["stock_lot.id", "stock_lot.part_id"],
                             name="fk_stock_move_lot_same_part"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    part_id: Mapped[int] = mapped_column(Integer, ForeignKey("part.id"), nullable=False, index=True)
    lot_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    reason: Mapped[str] = mapped_column(String(10), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                         server_default=func.now())
    note: Mapped[str | None] = mapped_column(String(200))
    # The job a part was issued to or returned from: recall tracing ("which jobs got
    # caps from that lot?"). Jobs are never deleted, so this always resolves.
    wo_job_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("wo_job.id"), index=True)
