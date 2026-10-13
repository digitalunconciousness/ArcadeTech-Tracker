"""The dashboard's counts: work, money, parts and warranty, each linking to its list.
Money figures come from figures.py; these are the counts around them."""

from datetime import datetime, time, timedelta

from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    OPEN_WO_STATUSES,
    WO_STATUSES,
    Appointment,
    Asset,
    Carrier,
    Estimate,
    Invoice,
    PoLine,
    PurchaseOrder,
    Shipment,
    Vendor,
    WoJob,
    WoLine,
    WorkOrder,
)
from app.purchasing import service as purchasing
from app.reports import figures
from app.reports.figures import OPEN_INVOICE, ZERO
from app.reports.service import expiring_warranties, warranty
from app.timeutil import local_today, shop_tz


def _local_bounds(day, days=1):
    tz = shop_tz()
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=days)


def work_counts(today=None):
    today = today or local_today()
    by_status = dict(db.session.execute(
        select(WorkOrder.status, func.count(WorkOrder.id))
        .where(WorkOrder.status.in_(OPEN_WO_STATUSES)).group_by(WorkOrder.status)).all())
    storage = figures.settings().storage_days
    boards = []
    for asset in db.session.scalars(select(Asset).where(Asset.status == "in_shop")
                                    .order_by(Asset.in_shop_since.nulls_last())):
        days = (today - asset.in_shop_since.astimezone(shop_tz()).date()).days \
            if asset.in_shop_since else None
        boards.append((asset, days, storage is not None and days is not None and days > storage))
    open_jobs = select(WoJob.id).join(WorkOrder, WorkOrder.id == WoJob.work_order_id).where(
        WorkOrder.status.in_(OPEN_WO_STATUSES), WoJob.status != "cancelled")
    unbilled_hours = db.session.scalar(select(func.coalesce(func.sum(WoLine.qty), 0)).where(
        WoLine.wo_job_id.in_(open_jobs), WoLine.kind == "labor",
        WoLine.labor_mode != "fixed", WoLine.invoice_id.is_(None), WoLine.billable.is_(True)))
    done_unbilled = db.session.execute(
        select(WorkOrder).join(WoJob, WoJob.work_order_id == WorkOrder.id)
        .join(WoLine, WoLine.wo_job_id == WoJob.id)
        .where(WoJob.status == "done", WoLine.invoice_id.is_(None),
               WoLine.billable.is_(True), WoLine.qty > 0, WoLine.awaiting.is_(False))
        .distinct().order_by(WorkOrder.id)).scalars().all()
    day_start, day_end = _local_bounds(today)
    week_start, week_end = _local_bounds(today, 7)
    appts = db.session.execute(
        select(Appointment, WorkOrder).join(WorkOrder, WorkOrder.id == Appointment.work_order_id)
        .where(Appointment.starts_at >= day_start, Appointment.starts_at < week_end)
        .order_by(Appointment.starts_at)).all()
    return {
        "estimates": db.session.scalar(select(func.count(Estimate.id)).where(
            Estimate.status == "sent")),
        "by_status": [(s, WO_STATUSES[s], by_status.get(s, 0)) for s in OPEN_WO_STATUSES],
        "boards": boards,
        "unbilled_hours": unbilled_hours,
        "done_unbilled": done_unbilled,
        "today": [(a, wo) for a, wo in appts if day_start <= a.starts_at < day_end],
        "week": [(a, wo) for a, wo in appts if a.starts_at >= day_end],
        "parts_in": db.session.scalars(select(WorkOrder).where(
            WorkOrder.parts_arrived_at.isnot(None)).order_by(WorkOrder.id)).all(),
    }


def money_counts(today=None):
    today = today or local_today()
    open_q = select(Invoice).where(Invoice.status.in_(OPEN_INVOICE), Invoice.balance > 0)
    overdue = db.session.scalars(open_q.where(Invoice.due_date < today)).all()
    due_soon = db.session.scalars(open_q.where(Invoice.due_date >= today,
                                               Invoice.due_date <= today + timedelta(days=7))).all()
    return {"overdue": overdue, "overdue_total": sum((i.balance for i in overdue), ZERO),
            "due_soon": due_soon, "due_soon_total": sum((i.balance for i in due_soon), ZERO),
            "credit": figures.unapplied_credit(), "owed": figures.reimbursements_owed()}


def parts_counts():
    in_transit = db.session.execute(
        select(Shipment, Carrier, PurchaseOrder, Vendor)
        .outerjoin(Carrier, Carrier.id == Shipment.carrier_id)
        .join(PurchaseOrder, PurchaseOrder.id == Shipment.po_id)
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .where(Shipment.status.notin_(("delivered", "returned")),
               PurchaseOrder.status.in_(("ordered", "partially_received")))
        .order_by(Shipment.eta.nulls_last(), Shipment.id)).all()
    delivered = db.session.execute(
        select(Shipment, PurchaseOrder).join(PurchaseOrder, PurchaseOrder.id == Shipment.po_id)
        .where(Shipment.status == "delivered",
               PurchaseOrder.status.in_(("ordered", "partially_received")))
        .order_by(Shipment.delivered_on)).all()
    return {
        "to_order": db.session.scalar(select(func.count(PoLine.id)).where(
            PoLine.po_id.is_(None), PoLine.status == "to_order")),
        "in_transit": [(s, c, po, v, purchasing.tracking_url(c, s.tracking_number))
                       for s, c, po, v in in_transit],
        "delivered": delivered,
        "low": len(purchasing.suggestions()),
    }


def warranty_counts(today=None):
    claims, jobs, rate, _ = warranty(90, today)
    return {"expiring": expiring_warranties(today), "claims": len(claims), "jobs": jobs,
            "rate": rate}
