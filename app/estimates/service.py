"""Estimates: build, send a link, approve (link, on our phone, verbal), decline, revise,
convert to a work order. Callers commit; an EstimateError's message is shown as is.

What the customer approves is pinned: the approval form carries a hash of the estimate as
shown, and an estimate that changed since is refused. After approval, triggers freeze the
estimate, its jobs and its lines in the database."""

import hashlib
import json
import secrets
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    Asset,
    Customer,
    DocLink,
    Estimate,
    EstimateJob,
    EstimateLine,
    JobTemplateLine,
    Part,
    Reservation,
    Service,
    ShopSetting,
    WoLine,
    WorkOrder,
)
from app.numbering import issue_number
from app.pricing import load_tiers, margin_pct, part_price, round_money
from app.timeutil import local_today, utcnow
from app.work import files
from app.work import service as work

TOKEN_BYTES = 32
OPEN_STATUSES = ("draft", "sent")


class EstimateError(Exception):
    """Something the user can fix; the message is shown as is."""


def label(est):
    return est.number if est.revision == 1 else f"{est.number} r{est.revision}"


def shop():
    return db.session.get(ShopSetting, True)


# --- building ---------------------------------------------------------------------------------

def create(customer, *, summary, site_id=None, valid_until=None, not_to_exceed=None,
           deposit_required=None, notes=None):
    est = Estimate(number=issue_number("EST"), revision=1, customer_id=customer.id,
                   site_id=site_id, summary=summary, status="draft", notes=notes,
                   valid_until=valid_until or local_today() + timedelta(days=shop().estimate_link_days),
                   not_to_exceed=not_to_exceed, deposit_required=deposit_required)
    db.session.add(est)
    db.session.flush()
    return est


def editable(est):
    return est.approved_at is None and est.status in OPEN_STATUSES


def ensure_editable(est):
    if not editable(est):
        raise EstimateError(f"{label(est)} is {est.status}: make a revision to change it.")


def add_job(est, asset_id, complaint):
    ensure_editable(est)
    if asset_id is not None:
        asset = db.session.get(Asset, asset_id)
        if asset is None or asset.customer_id != est.customer_id:
            raise EstimateError("That asset belongs to a different customer.")
    job = EstimateJob(estimate_id=est.id, asset_id=asset_id, complaint=complaint)
    db.session.add(job)
    db.session.flush()
    return job


def _line(job, **fields):
    line = EstimateLine(estimate_id=job.estimate_id, estimate_job_id=job.id, **fields)
    db.session.add(line)
    db.session.flush()
    return line


def add_service_line(est, job, service, *, qty, labor_mode=None, unit_price=None,
                     description=None):
    ensure_editable(est)
    kind = work.SERVICE_LINE_KIND[service.kind]
    if kind == "labor" and labor_mode == "actual" and db.session.scalar(
            select(EstimateLine.id).where(EstimateLine.estimate_job_id == job.id,
                                          EstimateLine.labor_mode == "actual")):
        raise EstimateError("This job already has an actual-time labor line.")
    return _line(job, kind=kind, service_id=service.id, description=description or service.name,
                 qty=qty, labor_mode=(labor_mode or "flat") if kind == "labor" else None,
                 unit_price=service.rate if unit_price is None else unit_price,
                 taxable=service.taxable, warranty_days=service.warranty_days)


def add_part_line(est, job, part, *, qty, unit_price=None):
    ensure_editable(est)
    price = unit_price if unit_price is not None else part_price(part, load_tiers())
    if price is None:
        raise EstimateError(f"{part.sku} has no price yet: enter one.")
    return _line(job, kind="part", part_id=part.id, description=part.name, qty=qty,
                 unit_price=price, unit_cost=part.default_cost, taxable=part.taxable,
                 warranty_days=work.PART_WARRANTY_DAYS)


def add_custom_line(est, job, *, kind, description, qty, unit_price, taxable=False,
                    unit_cost=None):
    ensure_editable(est)
    if kind not in ("fee", "sublet", "discount"):
        raise EstimateError("Pick a fee, sublet or discount.")
    return _line(job, kind=kind, description=description, qty=qty, unit_price=unit_price,
                 taxable=taxable, unit_cost=unit_cost if kind == "sublet" else None)


def apply_template(est, job, template):
    """A canned job's services and parts, at today's book prices."""
    ensure_editable(est)
    rows = db.session.execute(
        select(JobTemplateLine, Service, Part)
        .outerjoin(Service, Service.id == JobTemplateLine.service_id)
        .outerjoin(Part, Part.id == JobTemplateLine.part_id)
        .where(JobTemplateLine.template_id == template.id).order_by(JobTemplateLine.id)).all()
    if not rows:
        raise EstimateError("That template has no lines yet.")
    added = []
    for tline, service, part in rows:
        if service is not None:
            added.append(add_service_line(est, job, service, qty=tline.qty, labor_mode="flat",
                                          description=tline.note or None))
        else:
            added.append(add_part_line(est, job, part, qty=tline.qty))
    return added


def edit_line(est, line, *, description, qty, unit_price, taxable, labor_mode=None,
              unit_cost=None):
    ensure_editable(est)
    line.description, line.qty, line.unit_price, line.taxable = description, qty, unit_price, taxable
    if line.kind == "labor" and labor_mode:
        line.labor_mode = labor_mode
    if line.kind == "sublet":
        line.unit_cost = unit_cost
    db.session.flush()


def delete_line(est, line):
    ensure_editable(est)
    db.session.delete(line)
    db.session.flush()


def delete_job(est, job):
    ensure_editable(est)
    for line in db.session.scalars(select(EstimateLine).where(EstimateLine.estimate_job_id == job.id)):
        db.session.delete(line)
    db.session.flush()
    db.session.delete(job)
    db.session.flush()


# --- reading ----------------------------------------------------------------------------------

def jobs_and_lines(est):
    jobs = db.session.execute(
        select(EstimateJob, Asset).outerjoin(Asset, Asset.id == EstimateJob.asset_id)
        .where(EstimateJob.estimate_id == est.id).order_by(EstimateJob.id)).all()
    lines = {job.id: [] for job, _ in jobs}
    for line in db.session.scalars(select(EstimateLine).where(EstimateLine.estimate_id == est.id)
                                   .order_by(EstimateLine.id)):
        lines[line.estimate_job_id].append(line)
    return jobs, lines


def line_amount(line):
    amount = round_money(line.qty * line.unit_price)
    return -amount if line.kind == "discount" else amount


def totals(lines):
    total = sum((line_amount(x) for x in lines), Decimal("0.00"))
    taxable = sum((line_amount(x) for x in lines if x.taxable), Decimal("0.00"))
    costed = [x for x in lines if x.unit_cost is not None and x.kind in ("part", "sublet")]
    cost = sum((x.qty * x.unit_cost for x in costed), Decimal(0))
    goods = sum((line_amount(x) for x in lines if x.kind in ("part", "sublet")), Decimal("0.00"))
    return {"total": total, "taxable": taxable, "cost": round_money(cost),
            "margin": margin_pct(goods, cost) if costed else None,
            "hours": sum((x.qty for x in lines if x.kind == "labor"), Decimal(0))}


def content_hash(est):
    """What the customer sees, hashed. The approval form carries it back."""
    jobs, lines = jobs_and_lines(est)
    doc = {
        "id": est.id, "revision": est.revision, "summary": est.summary,
        "valid_until": str(est.valid_until), "nte": str(est.not_to_exceed),
        "deposit": str(est.deposit_required), "terms": est.terms_snapshot,
        "jobs": [[j.id, j.asset_id, j.complaint,
                  [[x.id, x.kind, x.description, str(x.qty), str(x.unit_price), x.labor_mode,
                    x.taxable, x.warranty_days] for x in lines[j.id]]] for j, _ in jobs],
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()


def is_expired(est, today=None):
    return est.valid_until is not None and est.valid_until < (today or local_today())


def latest_revision(est):
    return db.session.scalar(select(func.max(Estimate.revision)).where(Estimate.number == est.number))


# --- links ------------------------------------------------------------------------------------

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_link(est):
    """Mark the estimate sent and mint a link. Returns the token: shown once, only its
    hash is kept."""
    terms = shop().estimate_terms
    if not terms:
        raise EstimateError("Write your estimate terms in Settings → Business first; they print "
                            "on every estimate a customer approves.")
    if est.approved_at is None:
        ensure_editable(est)
        _, lines = jobs_and_lines(est)
        if not any(lines.values()):
            raise EstimateError("Add at least one line before sending it.")
        if is_expired(est):
            raise EstimateError("This estimate's valid-until date has passed; change it first.")
        est.terms_snapshot = terms
        if est.status == "draft":
            est.status, est.sent_at = "sent", utcnow()
    elif est.status not in ("approved", "converted"):
        raise EstimateError(f"{label(est)} is {est.status}.")
    token = secrets.token_urlsafe(TOKEN_BYTES)
    db.session.add(DocLink(estimate_id=est.id, token_sha256=token_hash(token),
                           expires_at=utcnow() + timedelta(days=shop().estimate_link_days)))
    db.session.flush()
    return token


def find_link(token):
    """(link, estimate) for a live token, else None. Malformed tokens never reach the
    database."""
    if not isinstance(token, str) or len(token) != 43 or \
            not all(c.isalnum() or c in "-_" for c in token):
        return None
    link = db.session.scalar(select(DocLink).where(DocLink.token_sha256 == token_hash(token)))
    if link is None or link.revoked_at is not None or link.expires_at <= utcnow():
        return None
    return link, db.session.get(Estimate, link.estimate_id)


def revoke_links(est):
    for link in db.session.scalars(select(DocLink).where(DocLink.estimate_id == est.id,
                                                         DocLink.revoked_at.is_(None))):
        link.revoked_at = utcnow()
    db.session.flush()


# --- decisions --------------------------------------------------------------------------------

def _lock(est):
    return db.session.scalars(select(Estimate).where(Estimate.id == est.id).with_for_update()
                              .execution_options(populate_existing=True)).one()


def approve(est, *, name, method, signature=None, ip=None, ua=None, note=None, seen_hash=None):
    est = _lock(est)
    if est.approved_at is not None:
        raise EstimateError(f"{label(est)} is already approved.")
    if est.status not in OPEN_STATUSES:
        raise EstimateError(f"{label(est)} is {est.status}; it can't be approved now.")
    if is_expired(est):
        raise EstimateError(f"{label(est)} expired on {est.valid_until}; ask for a new one.")
    if seen_hash is not None and seen_hash != content_hash(est):
        raise EstimateError("This estimate changed since it was opened. Reload it and look again.")
    if method in ("link", "on_screen"):
        try:
            est.signature_sha256 = files.store_signature(signature)
        except files.FileError as e:
            raise EstimateError(str(e)) from None
    if est.terms_snapshot is None:
        est.terms_snapshot = shop().estimate_terms
    est.approved_at = utcnow()
    est.approved_name = name
    est.approval_method = method
    est.approval_note = note
    est.approval_ip = ip
    est.approval_ua = (ua or "")[:300] or None
    est.status = "approved"
    if est.work_order_id is not None:   # a change order for work already under way
        wo = db.session.get(WorkOrder, est.work_order_id)
        wo.not_to_exceed = est.not_to_exceed
        if wo.status == "waiting_approval":
            wo.status = "in_progress"
        est.status = "converted"
    db.session.flush()
    return est


def decline(est, reason=None):
    est = _lock(est)
    if est.status not in OPEN_STATUSES or est.approved_at is not None:
        raise EstimateError(f"{label(est)} is {est.status}; it can't be declined now.")
    est.status, est.declined_at, est.decline_reason = "declined", utcnow(), reason
    db.session.flush()
    return est


def revise(est):
    """A new draft revision with the same number, copying jobs and lines. The old one, if
    nobody approved it, is replaced and its links stop working."""
    if est.status == "draft":
        raise EstimateError("It's still a draft: edit it instead.")
    if est.revision != latest_revision(est):
        raise EstimateError("A newer revision already exists.")
    new = Estimate(number=est.number, revision=est.revision + 1, supersedes_id=est.id,
                   customer_id=est.customer_id, site_id=est.site_id, summary=est.summary,
                   status="draft", notes=est.notes, not_to_exceed=est.not_to_exceed,
                   deposit_required=est.deposit_required, work_order_id=est.work_order_id,
                   valid_until=local_today() + timedelta(days=shop().estimate_link_days))
    db.session.add(new)
    db.session.flush()
    jobs, lines = jobs_and_lines(est)
    for job, _ in jobs:
        copy = EstimateJob(estimate_id=new.id, asset_id=job.asset_id, complaint=job.complaint)
        db.session.add(copy)
        db.session.flush()
        for x in lines[job.id]:
            db.session.add(EstimateLine(
                estimate_id=new.id, estimate_job_id=copy.id, kind=x.kind, service_id=x.service_id,
                part_id=x.part_id, description=x.description, qty=x.qty, labor_mode=x.labor_mode,
                unit_price=x.unit_price, unit_cost=x.unit_cost, taxable=x.taxable,
                warranty_days=x.warranty_days))
    if est.approved_at is None:
        est.status = "superseded"
        revoke_links(est)
    db.session.flush()
    return new


def convert(est, *, kind, tech_ids=()):
    """The approved estimate becomes a work order: its jobs and its labor, fee, sublet and
    discount lines carry over; quoted stocked parts are set aside for the job at the quoted
    price (issued when used). Returns (work_order, parts short of stock)."""
    est = _lock(est)
    if est.status != "approved" or est.work_order_id is not None:
        raise EstimateError(f"{label(est)} is {est.status}: only an approved estimate converts.")
    if est.revision != latest_revision(est):
        raise EstimateError("A newer revision exists; convert that one once it's approved.")
    customer = db.session.get(Customer, est.customer_id)
    wo = work.create_work_order(customer, kind=kind, summary=est.summary, site_id=est.site_id,
                                not_to_exceed=est.not_to_exceed, notes=est.notes,
                                tech_ids=tech_ids)
    short = []
    jobs, lines = jobs_and_lines(est)
    for ejob, _ in jobs:
        job = work.add_job(wo, ejob.asset_id, ejob.complaint)
        for x in lines[ejob.id]:
            part = db.session.get(Part, x.part_id) if x.part_id else None
            if part is not None and part.track_stock:
                db.session.add(Reservation(part_id=part.id, wo_job_id=job.id, qty=x.qty,
                                           unit_price=x.unit_price, status="reserved",
                                           note=f"Quoted on {label(est)}"))
                db.session.flush()
                free = work.available(part.id)
                if free < 0:
                    short.append((part.sku, min(x.qty, -free)))
                continue
            qty = Decimal(0) if x.labor_mode == "actual" else x.qty
            db.session.add(WoLine(
                wo_job_id=job.id, kind=x.kind, service_id=x.service_id, part_id=x.part_id,
                description=x.description, qty=qty, labor_mode=x.labor_mode,
                unit_price=x.unit_price, unit_cost=x.unit_cost, taxable=x.taxable,
                warranty_days=x.warranty_days))
        db.session.flush()
        work.sync_actual_labor(job)
    est.status, est.work_order_id = "converted", wo.id
    db.session.flush()
    return wo, short
