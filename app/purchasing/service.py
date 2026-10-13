"""Purchasing: the shopping list, purchase orders, receiving, shipments. Callers commit;
a PurchasingError's message is shown to the user as is.

Quantities are never stored twice: what's received on a line is the sum of its receipts,
what's on hand the sum of stock moves. "Delivered" isn't "received": a shipment's status
never touches stock; receiving (someone counted the box) does."""

import re
from collections import defaultdict
from decimal import Decimal
from urllib.parse import quote

from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    OPEN_LINE_STATUSES,
    Carrier,
    Part,
    PoLine,
    PoReceipt,
    PurchaseOrder,
    Reservation,
    Shipment,
    Vendor,
    WoJob,
    WorkOrder,
)
from app.numbering import issue_number
from app.parts import stock
from app.pricing import round_money
from app.timeutil import local_today, utcnow
from app.work import service as work

ZERO = Decimal("0")
TRACKING_RE = re.compile(r"^[A-Za-z0-9-]{4,40}$")


class PurchasingError(Exception):
    """Something the user can fix; the message is shown as is."""


# --- quantities -------------------------------------------------------------------------------

def received_map(line_ids):
    """{po line id: qty received so far}."""
    line_ids = list(line_ids)
    if not line_ids:
        return {}
    return dict(db.session.execute(
        select(PoReceipt.po_line_id, func.sum(PoReceipt.qty))
        .where(PoReceipt.po_line_id.in_(line_ids)).group_by(PoReceipt.po_line_id)).all())


def received(line):
    return received_map([line.id]).get(line.id, ZERO)


def on_order_map(part_ids=None):
    """{part id: qty still coming}: lines to order or on order, less what's received."""
    stmt = select(PoLine.id, PoLine.part_id, PoLine.qty).where(
        PoLine.status.in_(OPEN_LINE_STATUSES))
    if part_ids is not None:
        stmt = stmt.where(PoLine.part_id.in_(list(part_ids)))
    rows = db.session.execute(stmt).all()
    got = received_map(r.id for r in rows)
    out = defaultdict(Decimal)
    for line_id, part_id, qty in rows:
        out[part_id] += max(qty - got.get(line_id, ZERO), ZERO)
    return dict(out)


def reserved_map(part_ids=None):
    stmt = select(Reservation.part_id, func.sum(Reservation.qty)).where(
        Reservation.status == "reserved").group_by(Reservation.part_id)
    if part_ids is not None:
        stmt = stmt.where(Reservation.part_id.in_(list(part_ids)))
    return dict(db.session.execute(stmt).all())


def suggestions():
    """Stocked parts to buy: projected = on hand + on order − set aside. At or below the
    reorder point: order the reorder qty (or enough to get back to the point); below zero
    with no reorder point: order the shortfall. Each lists the WOs whose set-aside parts
    stock doesn't cover."""
    parts = db.session.scalars(select(Part).where(Part.active.is_(True),
                                                  Part.track_stock.is_(True))
                               .order_by(func.lower(Part.sku))).all()
    ids = [p.id for p in parts]
    held, coming, kept = stock.on_hand_map(ids), on_order_map(ids), reserved_map(ids)
    out = []
    for p in parts:
        on_hand, on_order = held.get(p.id, ZERO), coming.get(p.id, ZERO)
        reserved = kept.get(p.id, ZERO)
        projected = on_hand + on_order - reserved
        if p.reorder_point is not None and projected <= p.reorder_point:
            qty = max(p.reorder_qty or ZERO, p.reorder_point - projected) or Decimal(1)
        elif projected < 0:
            qty = -projected
        else:
            continue
        out.append({"part": p, "on_hand": on_hand, "on_order": on_order,
                    "reserved": reserved, "projected": projected, "qty": qty})
    if out:
        short = work.short_reservations(s["part"].id for s in out)
        rows = db.session.execute(
            select(Reservation.id, Reservation.part_id, WorkOrder.number)
            .join(WoJob, WoJob.id == Reservation.wo_job_id)
            .join(WorkOrder, WorkOrder.id == WoJob.work_order_id)
            .where(Reservation.id.in_(short)).order_by(Reservation.id)).all() if short else []
        short_for = defaultdict(list)
        for _, part_id, number in rows:
            if number not in short_for[part_id]:
                short_for[part_id].append(number)
        for s in out:
            s["short_for"] = short_for.get(s["part"].id, [])
    return out


# --- the shopping list ------------------------------------------------------------------------

def add_to_list(part, qty, *, vendor_id=None, unit_cost=None, note=None, wo_job_id=None):
    if not part.active:
        raise PurchasingError(f"{part.sku} is inactive.")
    line = PoLine(part_id=part.id, qty=qty, status="to_order", wo_job_id=wo_job_id,
                  vendor_id=vendor_id if vendor_id is not None else part.preferred_vendor_id,
                  unit_cost=unit_cost if unit_cost is not None else (part.default_cost or 0),
                  note=note)
    db.session.add(line)
    db.session.flush()
    return line


def order_for_job(job, part, qty, *, wait=True, note=None):
    """A part to buy for this job: on the shopping list, tied to the job, and (stocked
    parts) set aside for the job now, even before it's in stock, so receiving it fills the
    job first. With `wait`, the WO goes to Waiting on parts."""
    wo = db.session.get(WorkOrder, job.work_order_id)
    if not work.is_open(wo):
        raise PurchasingError(f"{wo.number} is {wo.status}.")
    line = add_to_list(part, qty, note=note, wo_job_id=job.id)
    if part.track_stock:
        res = Reservation(part_id=part.id, wo_job_id=job.id, qty=qty, status="reserved",
                          note="Ordered for this job")
        db.session.add(res)
        db.session.flush()
        line.reservation_id = res.id
    if wait and wo.status != "waiting_parts":
        work.set_status(wo, "waiting_parts")
    db.session.flush()
    return line


def list_lines():
    """The shopping list: lines to order that aren't on a PO yet, with part, vendor and
    the WO they're for."""
    return db.session.execute(
        select(PoLine, Part, Vendor, WorkOrder)
        .join(Part, Part.id == PoLine.part_id)
        .outerjoin(Vendor, Vendor.id == PoLine.vendor_id)
        .outerjoin(WoJob, WoJob.id == PoLine.wo_job_id)
        .outerjoin(WorkOrder, WorkOrder.id == WoJob.work_order_id)
        .where(PoLine.po_id.is_(None), PoLine.status == "to_order")
        .order_by(func.lower(func.coalesce(Vendor.name, "")), PoLine.id)).all()


def _ensure_to_order(line):
    if line.status != "to_order":
        raise PurchasingError("That line has been ordered: change it on its PO.")


def edit_list_line(line, *, qty, unit_cost=None, vendor_id=None, note=None, costs=True):
    """Quantity, vendor and note (and cost, for those who see costs) of a line not yet
    ordered. Its set-aside quantity follows."""
    _ensure_to_order(line)
    line.qty, line.note = qty, note
    if line.po_id is None:
        line.vendor_id = vendor_id
    if costs and unit_cost is not None:
        line.unit_cost = unit_cost
    if line.reservation_id:
        res = db.session.get(Reservation, line.reservation_id)
        if res.status == "reserved":
            res.qty = qty
    db.session.flush()


def remove_line(line):
    """Take a line off the list (or a draft PO). If it was bought for a job, what was set
    aside for it is released: you're not buying it after all."""
    _ensure_to_order(line)
    if line.reservation_id:
        res = db.session.get(Reservation, line.reservation_id)
        if res.status == "reserved":
            res.status = "released"
    db.session.delete(line)
    db.session.flush()
    return release_waiting()


def create_pos():
    """One draft PO per vendor for everything on the list (added to that vendor's draft if
    there is one). Lines with no vendor stay on the list."""
    lines = db.session.scalars(select(PoLine).where(PoLine.po_id.is_(None),
                                                    PoLine.status == "to_order")
                               .order_by(PoLine.id)).all()
    by_vendor = defaultdict(list)
    for line in lines:
        if line.vendor_id is not None:
            by_vendor[line.vendor_id].append(line)
    if not by_vendor:
        raise PurchasingError("Nothing on the list has a vendor yet: pick one for each line.")
    pos = []
    for vendor_id, group in by_vendor.items():
        po = db.session.scalar(select(PurchaseOrder).where(
            PurchaseOrder.vendor_id == vendor_id, PurchaseOrder.status == "draft")
            .order_by(PurchaseOrder.id))
        if po is None:
            po = PurchaseOrder(vendor_id=vendor_id, status="draft")
            db.session.add(po)
            db.session.flush()
        for line in group:
            line.po_id = po.id
        pos.append(po)
    db.session.flush()
    return pos


# --- purchase orders --------------------------------------------------------------------------

def po_lines(po):
    """[(line, part, received, WO number or None)] in order."""
    rows = db.session.execute(
        select(PoLine, Part, WorkOrder.number)
        .join(Part, Part.id == PoLine.part_id)
        .outerjoin(WoJob, WoJob.id == PoLine.wo_job_id)
        .outerjoin(WorkOrder, WorkOrder.id == WoJob.work_order_id)
        .where(PoLine.po_id == po.id).order_by(PoLine.id)).all()
    got = received_map(line.id for line, _, _ in rows)
    return [(line, part, got.get(line.id, ZERO), number) for line, part, number in rows]


def totals(po, lines=None):
    lines = po_lines(po) if lines is None else lines
    subtotal = sum((round_money(line.qty * line.unit_cost) for line, *_ in lines
                    if line.status != "cancelled"), Decimal("0.00"))
    return {"subtotal": subtotal, "total": subtotal + po.shipping + po.tax_paid}


def label(po):
    return po.number or f"Draft PO #{po.id}"


def mark_ordered(po, *, ordered_on=None, vendor_order_ref=None, paid_by_user_id=None,
                 user_id=None):
    """The order was placed: the PO gets its number (gapless, in this transaction) and
    its lines are on order."""
    if po.status != "draft":
        raise PurchasingError(f"{label(po)} is {po.status}.")
    lines = db.session.scalars(select(PoLine).where(PoLine.po_id == po.id)).all()
    if not lines:
        raise PurchasingError("There's nothing on this PO.")
    ordered_on = ordered_on or local_today()
    if ordered_on > local_today():
        raise PurchasingError("An order can't be dated in the future.")
    po.number = issue_number("PO", on=ordered_on)
    po.status, po.ordered_on, po.ordered_by = "ordered", ordered_on, user_id
    po.vendor_order_ref, po.paid_by_user_id = vendor_order_ref, paid_by_user_id
    for line in lines:
        line.status = "ordered"
    db.session.flush()
    return po


def edit_po_line(po, line, *, qty, unit_cost, expected_on=None, backordered=False):
    """What changed after ordering: a quantity (never below what's received), the real
    cost, when it's expected, backordered or not."""
    if line.po_id != po.id:
        raise PurchasingError("That line is on another PO.")
    if line.status not in OPEN_LINE_STATUSES:
        raise PurchasingError(f"That line is {line.status}.")
    got = received(line)
    if qty < got:
        raise PurchasingError(f"{stock.fmt_qty(got)} already came in; the quantity can't be "
                              "less.")
    line.qty, line.unit_cost, line.expected_on = qty, unit_cost, expected_on
    if line.status != "to_order":
        line.status = "backordered" if backordered else "ordered"
    if line.reservation_id:
        res = db.session.get(Reservation, line.reservation_id)
        if res.status == "reserved":
            res.qty = qty
    refresh(po)
    db.session.flush()


def set_amounts(po, *, shipping, tax_paid, notes=None):
    if po.status == "cancelled":
        raise PurchasingError("That PO is cancelled.")
    po.shipping, po.tax_paid, po.notes = shipping, tax_paid, notes
    db.session.flush()


def cancel(po):
    """A draft: its lines go back on the shopping list. An order with nothing received:
    cancelled, lines and all. Parts set aside for a job stay set aside (the job still
    needs them), so they come back on the list as short."""
    if po.status == "draft":
        for line in db.session.scalars(select(PoLine).where(PoLine.po_id == po.id)):
            line.po_id = None
        po.status = "cancelled"
    elif po.status == "ordered":
        lines = db.session.scalars(select(PoLine).where(PoLine.po_id == po.id)).all()
        if any(received_map(x.id for x in lines).values()):
            raise PurchasingError("Some of it came in: close it instead.")
        for line in lines:
            line.status = "cancelled"
        po.status = "cancelled"
    else:
        raise PurchasingError(f"{label(po)} is {po.status}: close it instead.")
    db.session.flush()
    return release_waiting()


def close(po):
    """Done with an order that came in short: what's still open is given up on (lines
    with something received are closed short, the rest cancelled)."""
    if po.status not in ("ordered", "partially_received"):
        raise PurchasingError(f"{label(po)} is {po.status}.")
    lines = db.session.scalars(select(PoLine).where(PoLine.po_id == po.id)).all()
    got = received_map(x.id for x in lines)
    for line in lines:
        if line.status in OPEN_LINE_STATUSES:
            line.status = "closed" if got.get(line.id) else "cancelled"
    po.status = "closed" if any(got.values()) else "cancelled"
    db.session.flush()
    return release_waiting()


def refresh(po):
    """The PO's status from its lines."""
    if po.status in ("draft", "cancelled"):
        return
    lines = db.session.scalars(select(PoLine).where(PoLine.po_id == po.id)).all()
    live = [x for x in lines if x.status != "cancelled"]
    if live and all(x.status in ("received", "closed") for x in live):
        po.status = "closed" if any(x.status == "closed" for x in live) else "received"
    elif any(received_map(x.id for x in live).values()):
        po.status = "partially_received"
    else:
        po.status = "ordered"


def receive(po, entries):
    """Count the box. entries: [(line, qty, date_code, vendor_lot)]. A stocked part becomes
    a lot at the line's cost with its receive move; anything set aside for a job is
    already waiting for it. Then every WO waiting on parts is checked: those with nothing
    left outstanding go back to In progress. Returns ([receipts], [WOs moved])."""
    if po.status not in ("ordered", "partially_received"):
        raise PurchasingError(f"{label(po)} is {po.status}: nothing to receive.")
    receipts = []
    for line, qty, date_code, vendor_lot in entries:
        if not qty:
            continue
        if line.po_id != po.id or line.status not in ("ordered", "backordered"):
            raise PurchasingError("That line isn't open on this PO.")
        if qty < 0:
            raise PurchasingError("Quantities received can't be negative.")
        part = db.session.get(Part, line.part_id)
        lot = None
        if part.track_stock:
            try:
                lot = stock.receive(part, qty, line.unit_cost, vendor_id=po.vendor_id,
                                    date_code=date_code, vendor_lot=vendor_lot,
                                    note=po.number, po_line_id=line.id)
            except stock.StockError as e:
                raise PurchasingError(str(e)) from None
        receipt = PoReceipt(po_line_id=line.id, qty=qty, lot_id=lot.id if lot else None,
                            date_code=date_code, vendor_lot=vendor_lot)
        db.session.add(receipt)
        db.session.flush()
        if received(line) >= line.qty:
            line.status = "received"
        receipts.append(receipt)
    if not receipts:
        raise PurchasingError("Type how many came in for at least one line.")
    refresh(po)
    db.session.flush()
    return receipts, release_waiting()


def release_waiting():
    """Every WO waiting on parts with nothing left outstanding goes back to In progress.
    Run after anything that can settle what a WO waits on. Returns the WOs moved."""
    return [wo for wo in db.session.scalars(select(WorkOrder).where(
        WorkOrder.status == "waiting_parts").order_by(WorkOrder.id)) if work.release_if_ready(wo)]


def open_orders():
    """POs ordered and not all in, oldest first, with their vendor."""
    return db.session.execute(
        select(PurchaseOrder, Vendor).join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .where(PurchaseOrder.status.in_(("ordered", "partially_received")))
        .order_by(PurchaseOrder.ordered_on, PurchaseOrder.id)).all()


def on_order_for_part(part_id):
    """Open lines for one part: [(line, PO, received)]."""
    rows = db.session.execute(
        select(PoLine, PurchaseOrder).outerjoin(PurchaseOrder, PurchaseOrder.id == PoLine.po_id)
        .where(PoLine.part_id == part_id, PoLine.status.in_(OPEN_LINE_STATUSES))
        .order_by(PoLine.id)).all()
    got = received_map(line.id for line, _ in rows)
    return [(line, po, got.get(line.id, ZERO)) for line, po in rows]


# --- shipments and carriers -------------------------------------------------------------------

def clean_tracking(raw):
    number = re.sub(r"\s+", "", raw or "").upper()
    if not TRACKING_RE.match(number):
        raise PurchasingError("A tracking number is 4 to 40 letters, digits or dashes.")
    return number


def guess_carrier(number):
    """The first active carrier whose pattern matches the number, or None."""
    for carrier in db.session.scalars(select(Carrier).where(Carrier.active.is_(True))
                                      .order_by(Carrier.position, Carrier.id)):
        if carrier.number_pattern:
            try:
                if re.fullmatch(carrier.number_pattern, number):
                    return carrier
            except re.error:
                continue
    return None


def tracking_url(carrier, number):
    if carrier is None:
        return None
    return carrier.url_template.replace("{number}", quote(number, safe=""))


def add_shipment(*, direction, tracking_number, po=None, wo=None, carrier_id=None,
                 shipped_on=None, eta=None, cost=None, note=None):
    number = clean_tracking(tracking_number)
    if (direction == "inbound_purchase") != (po is not None):
        raise PurchasingError("A vendor's box goes on its PO; a customer's on the work order.")
    if carrier_id is None:
        guess = guess_carrier(number)
        carrier_id = guess.id if guess else None
    shipment = Shipment(direction=direction, tracking_number=number,
                        po_id=po.id if po else None, work_order_id=wo.id if wo else None,
                        carrier_id=carrier_id, shipped_on=shipped_on, eta=eta, cost=cost,
                        note=note, status="in_transit")
    db.session.add(shipment)
    db.session.flush()
    return shipment


def update_shipment(shipment, *, status, carrier_id, eta=None, delivered_on=None, cost=None,
                    note=None):
    """Set by hand from the carrier's page. Delivered isn't received: stock doesn't
    change here. No carrier picked: guessed from the number again."""
    if carrier_id is None:
        guess = guess_carrier(shipment.tracking_number)
        carrier_id = guess.id if guess else None
    if status != shipment.status:
        shipment.status_updated_at = utcnow()
    shipment.status, shipment.carrier_id, shipment.eta = status, carrier_id, eta
    if status == "delivered" and delivered_on is None:
        delivered_on = local_today()
    shipment.delivered_on = delivered_on if status == "delivered" else None
    shipment.cost, shipment.note = cost, note
    db.session.flush()


def shipments(po=None, wo=None):
    """[(shipment, carrier, tracking url)]."""
    stmt = select(Shipment, Carrier).outerjoin(Carrier, Carrier.id == Shipment.carrier_id)
    stmt = stmt.where(Shipment.po_id == po.id) if po is not None else \
        stmt.where(Shipment.work_order_id == wo.id)
    return [(s, c, tracking_url(c, s.tracking_number))
            for s, c in db.session.execute(stmt.order_by(Shipment.id))]
