from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import StandardColumns
from app.models.parts import UNIT_COST

PO_STATUSES = {
    "draft": "Draft",
    "ordered": "Ordered",
    "partially_received": "Partly received",
    "received": "Received",
    "closed": "Closed short",
    "cancelled": "Cancelled",
}
OPEN_PO_STATUSES = ("ordered", "partially_received")
# to_order: on the shopping list (no PO yet) or on a draft PO. closed: done with fewer
# received than ordered (the rest given up on).
PO_LINE_STATUSES = {
    "to_order": "To order",
    "ordered": "Ordered",
    "backordered": "Backordered",
    "received": "Received",
    "closed": "Closed short",
    "cancelled": "Cancelled",
}
OPEN_LINE_STATUSES = ("to_order", "ordered", "backordered")
SHIPMENT_DIRECTIONS = {
    "inbound_purchase": "Coming from a vendor",
    "inbound_customer": "Coming from the customer",
    "outbound_customer": "Going to the customer",
}
SHIPMENT_STATUSES = {
    "label_created": "Label created",
    "in_transit": "In transit",
    "out_for_delivery": "Out for delivery",
    "delivered": "Delivered",
    "exception": "Problem",
    "returned": "Returned to sender",
}


def _in(column, values):
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class Carrier(StandardColumns, db.Model):
    """A shipping carrier and its tracking page. The URL template and the tracking-number
    pattern live here, in Settings, so a carrier changing its site is an edit, not a
    deploy."""

    __tablename__ = "carrier"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("code", name="uq_carrier_code"),
        CheckConstraint("code ~ '^[a-z0-9_]{2,16}$'", name="code_format"),
        CheckConstraint("url_template ~ '^https://[^[:space:]]+$' AND "
                        "position('{number}' in url_template) > 0", name="url_template"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    url_template: Mapped[str] = mapped_column(String(300), nullable=False)
    # A regular expression for the number (spaces taken out, upper case); the first active
    # carrier whose pattern matches is the guess.
    number_pattern: Mapped[str | None] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class PurchaseOrder(StandardColumns, db.Model):
    """An order to one vendor. The number (PO-2026-0001) is issued from doc_counter when
    it's marked ordered, so abandoned drafts don't use numbers up. Lines' cost and what's
    received live on po_line and po_receipt; subtotal and total are computed."""

    __tablename__ = "purchase_order"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("number", name="uq_purchase_order_number"),
        CheckConstraint("number ~ '^PO-[0-9]{4}-[0-9]{4,}$'", name="number_format"),
        CheckConstraint(_in("status", PO_STATUSES), name="status"),
        CheckConstraint("status <> 'draft' OR number IS NULL", name="draft_unnumbered"),
        CheckConstraint("status IN ('draft', 'cancelled') OR number IS NOT NULL",
                        name="ordered_numbered"),
        CheckConstraint("number IS NULL OR ordered_on IS NOT NULL", name="ordered_dated"),
        CheckConstraint("shipping >= 0 AND tax_paid >= 0", name="amounts_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    number: Mapped[str | None] = mapped_column(String(20))
    vendor_id: Mapped[int] = mapped_column(Integer, ForeignKey("vendor.id"), nullable=False,
                                           index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False,
                                        server_default=text("'draft'"))
    vendor_order_ref: Mapped[str | None] = mapped_column(String(60))
    ordered_on: Mapped[date | None] = mapped_column(Date)
    ordered_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.id"))
    # Null: the business account. A partner's id: their own card, owed back (Phase 6).
    paid_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.id"))
    shipping: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False,
                                              server_default=text("0"))
    tax_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False,
                                              server_default=text("0"))
    notes: Mapped[str | None] = mapped_column(Text)


class PoLine(StandardColumns, db.Model):
    """One part to buy. On the shopping list while po_id is null; tied to a job when it's
    bought for one (and then the part was set aside for that job when it was ordered).
    What's been received is the sum of its receipts, never a stored count."""

    __tablename__ = "po_line"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint(_in("status", PO_LINE_STATUSES), name="status"),
        CheckConstraint("status = 'to_order' OR po_id IS NOT NULL", name="ordered_on_a_po"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("unit_cost >= 0", name="cost_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    po_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("purchase_order.id"),
                                              index=True)
    part_id: Mapped[int] = mapped_column(Integer, ForeignKey("part.id"), nullable=False,
                                         index=True)
    # Who to buy it from while it's on the list; a PO's lines are all its vendor's.
    vendor_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vendor.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(UNIT_COST, nullable=False,
                                               server_default=text("0"))
    wo_job_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("wo_job.id"), index=True)
    reservation_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("reservation.id"))
    status: Mapped[str] = mapped_column(String(12), nullable=False,
                                        server_default=text("'to_order'"))
    expected_on: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(200))


class PoReceipt(StandardColumns, db.Model):
    """Someone opened the box and counted it. Append-only. A stocked part's receipt made a
    lot (at the line's cost) and its receive move."""

    __tablename__ = "po_receipt"
    __audited__ = ()
    __table_args__ = (CheckConstraint("qty > 0", name="qty_positive"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    po_line_id: Mapped[int] = mapped_column(Integer, ForeignKey("po_line.id"), nullable=False,
                                            index=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    lot_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("stock_lot.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.now())
    date_code: Mapped[str | None] = mapped_column(String(20))
    vendor_lot: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(String(200))


class Shipment(StandardColumns, db.Model):
    """A box moving: from a vendor on a PO, or to or from a customer on a work order.
    Status is set by hand. Delivered isn't received: only receiving touches stock."""

    __tablename__ = "shipment"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint(_in("direction", SHIPMENT_DIRECTIONS), name="direction"),
        CheckConstraint(_in("status", SHIPMENT_STATUSES), name="status"),
        CheckConstraint("num_nonnulls(po_id, work_order_id) = 1", name="one_parent"),
        CheckConstraint("(direction = 'inbound_purchase') = (po_id IS NOT NULL)",
                        name="direction_matches_parent"),
        CheckConstraint("tracking_number ~ '^[A-Za-z0-9-]{4,40}$'", name="tracking_format"),
        CheckConstraint("cost IS NULL OR cost >= 0", name="cost_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    po_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("purchase_order.id"),
                                              index=True)
    work_order_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("work_order.id"),
                                                      index=True)
    carrier_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("carrier.id"))
    tracking_number: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False,
                                        server_default=text("'in_transit'"))
    shipped_on: Mapped[date | None] = mapped_column(Date)
    eta: Mapped[date | None] = mapped_column(Date)
    delivered_on: Mapped[date | None] = mapped_column(Date)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    note: Mapped[str | None] = mapped_column(String(200))
    status_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                        nullable=False,
                                                        server_default=func.now())
