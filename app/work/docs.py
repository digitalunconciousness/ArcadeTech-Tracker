"""Customer documents for work orders, as PDFs (WeasyPrint): the claim ticket and the
service report. Print-first: templates/docs/ + static/css/doc.css; the accent colour
from Settings is injected as its own stylesheet, so the templates carry no inline CSS."""

from collections import defaultdict
from decimal import Decimal

from flask import current_app, render_template
from sqlalchemy import func, select
from weasyprint import CSS, HTML

from app.extensions import db
from app.formatting import plain
from app.models import (
    RECEIVED_VIA,
    WO_KINDS,
    Asset,
    ManualReading,
    Site,
    StockLot,
    TimeEntry,
    User,
    WoJob,
    WorkOrderTech,
)
from app.settings_store import get_settings
from app.work import service


def render_pdf(template, **context):
    html = render_template(template, doc_css="css/doc.css", wo_kinds=WO_KINDS,
                           received_via=RECEIVED_VIA, **context)
    accent = CSS(string=f":root {{ --accent: {get_settings().doc_accent_color} !important; }}")
    return HTML(string=html, base_url=current_app.static_folder + "/").write_pdf(
        stylesheets=[accent])


def _jobs(wo):
    return db.session.execute(
        select(WoJob, Asset).outerjoin(Asset, Asset.id == WoJob.asset_id)
        .where(WoJob.work_order_id == wo.id, WoJob.status != "cancelled")
        .order_by(WoJob.id)).all()


def claim_ticket(wo, customer):
    return render_pdf("docs/claim_ticket.html", wo=wo, customer=customer, jobs=_jobs(wo))


def reading_rows(readings):
    """One row per test point: the latest as-found and as-left readings side by side."""
    rows = {}
    for r in sorted(readings, key=lambda r: (r.taken_at, r.id)):
        if r.phase == "during":
            continue
        row = rows.setdefault(r.test_point, {"test_point": r.test_point})
        row[r.phase] = r
    for row in rows.values():
        r = row.get("as_left") or row.get("as_found")
        lo, hi = plain(r.spec_lo), plain(r.spec_hi)
        row["spec"] = (f"{lo}–{hi} {r.unit}" if lo and hi else f"≥ {lo} {r.unit}" if lo
                       else f"≤ {hi} {r.unit}" if hi else "—")
    return list(rows.values())


def service_report(wo, customer):
    jobs = _jobs(wo)
    job_ids = [j.id for j, _ in jobs]
    lines = service.job_lines(job_ids)
    lots = dict(db.session.execute(select(StockLot.id, StockLot.date_code).where(
        StockLot.id.in_([x.stock_lot_id for ls in lines.values() for x in ls
                         if x.stock_lot_id]))).all())
    readings = defaultdict(list)
    for r in db.session.scalars(select(ManualReading).where(ManualReading.wo_job_id.in_(job_ids))):
        readings[r.wo_job_id].append(r)
    labor = defaultdict(list)
    for job_id, name, minutes in db.session.execute(
            select(TimeEntry.wo_job_id, User.display_name, func.sum(TimeEntry.minutes))
            .join(User, User.id == TimeEntry.user_id)
            .where(TimeEntry.wo_job_id.in_(job_ids), TimeEntry.ended_at.is_not(None))
            .group_by(TimeEntry.wo_job_id, User.display_name).order_by(User.display_name)):
        if minutes:
            labor[job_id].append((name, (Decimal(minutes) / 60).quantize(Decimal("0.01"))))
    techs = db.session.scalars(select(User.display_name).join(
        WorkOrderTech, WorkOrderTech.user_id == User.id)
        .where(WorkOrderTech.work_order_id == wo.id).order_by(User.display_name)).all()
    warranty = {}
    for job_id, ls in lines.items():
        has_customer_part = any(x.customer_supplied for x in ls)
        warranty[job_id] = [(x, service.warranty_days(x, has_customer_part)) for x in ls]
    return render_pdf("docs/service_report.html", wo=wo, customer=customer, jobs=jobs,
                      lines=lines, lots=lots, readings={k: reading_rows(v)
                                                        for k, v in readings.items()},
                      labor=labor, techs=techs, warranty=warranty,
                      labor_warranty={k: [(x, d) for x, d in v if x.kind == "labor"]
                                      for k, v in warranty.items()},
                      site=db.session.get(Site, wo.site_id) if wo.site_id else None)
