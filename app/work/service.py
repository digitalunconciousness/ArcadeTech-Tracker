"""Work orders, jobs, lines, time and parts. Callers commit; a WorkError's message is
shown to the user as is.

Money: a line's amount is qty × unit_price rounded half-up to the cent (a discount
subtracts). A line that isn't billable (warranty work) still shows its amount and cost
but charges nothing."""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    Asset,
    Reservation,
    ShopSetting,
    StockLot,
    TimeEntry,
    WoJob,
    WoLine,
    WorkOrder,
    WorkOrderTech,
)
from app.numbering import issue_number
from app.parts import stock
from app.pricing import bill_hours, load_tiers, margin_pct, part_price, round_money
from app.timeutil import utcnow

# Warranty defaults (owner, 2026-10-07): a year on parts we supply and on labor; labor on
# a job that used a customer's own part gets 90 days, and that part gets none.
PART_WARRANTY_DAYS = 365
CUSTOMER_PART_LABOR_WARRANTY_DAYS = 90

SERVICE_LINE_KIND = {"labor": "labor", "fee": "fee", "trip": "fee", "diagnostic": "fee",
                     "shop_supplies": "fee", "sublet": "sublet", "discount": "discount"}
ZERO = Decimal(0)


class WorkError(Exception):
    """Something the user can fix; the message is shown as is."""


# --- work orders and jobs -------------------------------------------------------------------

def create_work_order(customer, *, kind, summary, site_id=None, priority="normal",
                      promised_date=None, not_to_exceed=None, notes=None, tech_ids=(),
                      warranty_of_job_id=None):
    wo = WorkOrder(number=issue_number("WO"), customer_id=customer.id, site_id=site_id, kind=kind,
                   summary=summary, priority=priority, promised_date=promised_date,
                   not_to_exceed=not_to_exceed, notes=notes, status="new",
                   warranty_of_job_id=warranty_of_job_id)
    db.session.add(wo)
    db.session.flush()
    set_techs(wo, tech_ids)
    return wo


def tech_ids(wo):
    return set(db.session.scalars(select(WorkOrderTech.user_id)
                                  .where(WorkOrderTech.work_order_id == wo.id)))


def set_techs(wo, user_ids):
    want, have = set(user_ids or ()), tech_ids(wo)
    for uid in want - have:
        db.session.add(WorkOrderTech(work_order_id=wo.id, user_id=uid))
    for row in db.session.scalars(select(WorkOrderTech).where(
            WorkOrderTech.work_order_id == wo.id, WorkOrderTech.user_id.in_(have - want))):
        db.session.delete(row)
    db.session.flush()


def set_status(wo, status):
    wo.status = status
    if status == "completed":
        wo.completed_at = wo.completed_at or utcnow()
    else:
        wo.completed_at = None


def is_open(wo):
    return wo.status not in ("completed", "cancelled")


def add_job(wo, asset_id, complaint):
    if asset_id is not None:
        asset = db.session.get(Asset, asset_id)
        if asset is None or asset.customer_id != wo.customer_id:
            raise WorkError("That asset belongs to a different customer.")
    job = WoJob(work_order_id=wo.id, asset_id=asset_id, complaint=complaint, status="open")
    db.session.add(job)
    db.session.flush()
    return job


def set_job_status(job, status):
    job.status = status
    job.done_at = (job.done_at or utcnow()) if status == "done" else None


# --- amounts --------------------------------------------------------------------------------

def line_amount(line):
    """The line's signed amount (a discount is negative), billable or not."""
    if line.customer_supplied:
        return Decimal("0.00")
    amount = round_money(line.qty * line.unit_price)
    return -amount if line.kind == "discount" else amount


def line_charge(line):
    return line_amount(line) if line.billable else Decimal("0.00")


def line_cost(line):
    """What the line cost us (parts at their lot cost, sublets at what we paid); None
    when it has no cost (labor) or the cost isn't known."""
    if line.unit_cost is None or line.customer_supplied:
        return None
    return line.qty * line.unit_cost


def totals(lines):
    charge = sum((line_charge(x) for x in lines), Decimal("0.00"))
    costs = [line_cost(x) for x in lines]
    cost = sum((c for c in costs if c is not None), ZERO)
    goods = sum((line_charge(x) for x in lines if x.kind in ("part", "sublet")), Decimal("0.00"))
    return {"charge": charge, "cost": round_money(cost),
            "margin": margin_pct(goods, cost) if any(c is not None for c in costs) else None,
            "unbilled": sum((line_amount(x) for x in lines if not x.billable), Decimal("0.00"))}


def job_lines(job_ids):
    rows = db.session.scalars(select(WoLine).where(WoLine.wo_job_id.in_(list(job_ids)))
                              .order_by(WoLine.id)).all()
    out = {jid: [] for jid in job_ids}
    for line in rows:
        out[line.wo_job_id].append(line)
    return out


def warranty_days(line, job_has_customer_part):
    """Warranty for this line as printed: 90 days on labor if the customer supplied a
    part on the job; none on the customer's part itself."""
    if line.customer_supplied:
        return 0
    if line.kind == "labor" and job_has_customer_part:
        return min(line.warranty_days, CUSTOMER_PART_LABOR_WARRANTY_DAYS)
    return line.warranty_days


# --- lines ----------------------------------------------------------------------------------

def actual_line(job):
    return db.session.scalar(select(WoLine).where(WoLine.wo_job_id == job.id,
                                                  WoLine.labor_mode == "actual"))


def add_service_line(job, service, *, qty=None, labor_mode=None, unit_price=None,
                     description=None, tech_user_id=None, billable=True, unit_cost=None):
    kind = SERVICE_LINE_KIND[service.kind]
    if kind == "labor":
        labor_mode = labor_mode or "flat"
        if labor_mode == "actual":
            if actual_line(job) is not None:
                raise WorkError("This job already has an actual-time labor line; time on the "
                                "job feeds that one.")
            qty = actual_hours(job)
    else:
        labor_mode = None
    if qty is None or (qty <= 0 and labor_mode != "actual"):
        raise WorkError("Quantity must be more than zero.")
    line = WoLine(wo_job_id=job.id, kind=kind, service_id=service.id,
                  description=description or service.name, qty=qty, labor_mode=labor_mode,
                  unit_price=service.rate if unit_price is None else unit_price,
                  unit_cost=unit_cost if kind == "sublet" else None,
                  taxable=service.taxable, warranty_days=service.warranty_days,
                  billable=billable, tech_user_id=tech_user_id if kind == "labor" else None)
    db.session.add(line)
    db.session.flush()
    return line


def add_custom_line(job, *, kind, description, qty, unit_price, taxable=False, unit_cost=None,
                    billable=True):
    if kind not in ("fee", "sublet", "discount"):
        raise WorkError("Pick a fee, sublet or discount.")
    line = WoLine(wo_job_id=job.id, kind=kind, description=description, qty=qty,
                  unit_price=unit_price, taxable=taxable, billable=billable,
                  unit_cost=unit_cost if kind == "sublet" else None)
    db.session.add(line)
    db.session.flush()
    return line


def issue_part(job, part, qty, *, unit_price=None, billable=True, wo_number=""):
    """Sell a part on the job. A stocked part comes out oldest lot first, one line per
    lot, each at that lot's cost and linked to it for recalls."""
    price = unit_price if unit_price is not None else part_price(part, load_tiers())
    if price is None:
        raise WorkError(f"{part.sku} has no price yet: enter one.")
    common = dict(wo_job_id=job.id, kind="part", part_id=part.id, description=part.name,
                  unit_price=price, taxable=part.taxable, warranty_days=PART_WARRANTY_DAYS,
                  billable=billable)
    if not part.track_stock:
        lines = [WoLine(qty=qty, unit_cost=part.default_cost, **common)]
    else:
        try:
            moves = stock.take(part, qty, "issue", note=wo_number, wo_job_id=job.id)
        except stock.StockError as e:
            raise WorkError(str(e)) from None
        costs = dict(db.session.execute(select(StockLot.id, StockLot.unit_cost).where(
            StockLot.id.in_([m.lot_id for m in moves]))).all())
        lines = [WoLine(qty=-m.qty, stock_lot_id=m.lot_id, unit_cost=costs[m.lot_id], **common)
                 for m in moves]
    db.session.add_all(lines)
    db.session.flush()
    return lines


def add_customer_part(job, *, description, qty, part_id=None):
    """A part the customer brought: no stock move, no charge, no warranty."""
    line = WoLine(wo_job_id=job.id, kind="part", part_id=part_id, description=description,
                  qty=qty, unit_price=0, warranty_days=0, customer_supplied=True)
    db.session.add(line)
    db.session.flush()
    return line


def remove_line(line, part=None, wo_number=""):
    """Delete a line. An issued part goes back into the lot it came from."""
    if line.stock_lot_id is not None:
        try:
            stock.put(part, line.qty, "return", note=wo_number, lot_id=line.stock_lot_id,
                      wo_job_id=line.wo_job_id)
        except stock.StockError as e:
            raise WorkError(str(e)) from None
    db.session.delete(line)
    db.session.flush()


def edit_line(line, *, description, unit_price, billable, taxable, qty=None, unit_cost=None,
              tech_user_id=None):
    """Prices, wording and flags can change on any line. Quantity can't on an issued part
    (remove the line and issue again) or on actual-time labor (the timer sets it)."""
    line.description = description
    line.unit_price = unit_price
    line.billable = billable
    line.taxable = taxable
    if qty is not None and line.stock_lot_id is None and line.labor_mode != "actual":
        if qty <= 0:
            raise WorkError("Quantity must be more than zero.")
        line.qty = qty
    if line.kind == "sublet":
        line.unit_cost = unit_cost
    if line.kind == "labor":
        line.tech_user_id = tech_user_id
    db.session.flush()


# --- time -----------------------------------------------------------------------------------

def billable_minutes(job):
    return db.session.scalar(select(func.coalesce(func.sum(TimeEntry.minutes), 0)).where(
        TimeEntry.wo_job_id == job.id, TimeEntry.billable.is_(True),
        TimeEntry.ended_at.is_not(None)))


def actual_hours(job):
    """The labor policy applied to the job's billable time: rounded up to the step, then
    at least the minimum, once per job."""
    shop = db.session.get(ShopSetting, True)
    return bill_hours(Decimal(billable_minutes(job)) / 60, shop.labor_increment_hours,
                      shop.labor_minimum_hours)


def sync_actual_labor(job):
    line = actual_line(job)
    if line is not None:
        line.qty = actual_hours(job)
        db.session.flush()


def running_timer(user_id):
    return db.session.scalar(select(TimeEntry).where(TimeEntry.user_id == user_id,
                                                     TimeEntry.ended_at.is_(None)))


def whole_minutes(delta):
    return int((Decimal(delta.total_seconds()) / 60).to_integral_value(rounding=ROUND_HALF_UP))


def stop_timer(entry, now=None):
    entry.ended_at = max(now or utcnow(), entry.started_at)
    entry.minutes = whole_minutes(entry.ended_at - entry.started_at)
    db.session.flush()
    sync_actual_labor(db.session.get(WoJob, entry.wo_job_id))
    return entry


def start_timer(job, user_id, now=None):
    """Start the user's timer on this job, stopping one running elsewhere. Returns
    (entry, the entry that was stopped or None)."""
    stopped = running_timer(user_id)
    if stopped is not None:
        if stopped.wo_job_id == job.id:
            raise WorkError("Your timer is already running on this job.")
        stop_timer(stopped, now)
    entry = TimeEntry(wo_job_id=job.id, user_id=user_id, started_at=now or utcnow(),
                      source="timer", billable=True)
    db.session.add(entry)
    db.session.flush()
    return entry, stopped


def add_manual_time(job, user_id, *, minutes, started_at, billable=True, note=None):
    entry = TimeEntry(wo_job_id=job.id, user_id=user_id, started_at=started_at,
                      ended_at=started_at + timedelta(minutes=minutes), minutes=minutes,
                      billable=billable, source="manual", note=note)
    db.session.add(entry)
    db.session.flush()
    sync_actual_labor(job)
    return entry


def delete_time(entry):
    job = db.session.get(WoJob, entry.wo_job_id)
    db.session.delete(entry)
    db.session.flush()
    sync_actual_labor(job)


# --- reservations ---------------------------------------------------------------------------

def reserved(part_id):
    return db.session.scalar(select(func.coalesce(func.sum(Reservation.qty), 0)).where(
        Reservation.part_id == part_id, Reservation.status == "reserved"))


def available(part_id):
    return stock.on_hand(part_id) - reserved(part_id)


def reserve(job, part, qty, note=None):
    if not part.track_stock:
        raise WorkError(f"{part.sku} isn't stocked, so there's nothing to set aside.")
    free = available(part.id)
    if free < qty:
        raise WorkError(f"Only {stock.fmt_qty(free)} of {part.sku} available.")
    res = Reservation(part_id=part.id, wo_job_id=job.id, qty=qty, note=note, status="reserved")
    db.session.add(res)
    db.session.flush()
    return res


def issue_reservation(res, part, *, unit_price=None, wo_number=""):
    if res.status != "reserved":
        raise WorkError("That reservation is already closed.")
    res.status = "issued"
    db.session.flush()
    return issue_part(db.session.get(WoJob, res.wo_job_id), part, res.qty,
                      unit_price=unit_price if unit_price is not None else res.unit_price,
                      wo_number=wo_number)


def release_reservation(res):
    if res.status != "reserved":
        raise WorkError("That reservation is already closed.")
    res.status = "released"
    db.session.flush()
