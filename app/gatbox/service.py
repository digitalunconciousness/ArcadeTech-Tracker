"""GATBOX on the hub side: what /api/v1 stores (contract v1, contract/v1/README.md) and how
traces meet jobs.

Ingest, as the tracker does it: all or nothing at the request level, per-item outcomes in
a 200; a machine the hub doesn't have is rejected, never created; an order or tag naming
a session not yet received is rejected so the Pi sends the session and retries. Each item
is checked in full before anything is written, and written inside a savepoint, so a
rejected item leaves nothing behind.

The shop's own rules (owner, 2026-10-13):
  an order becomes a job: on the asset's open work order if it has one, otherwise on a new
  work order (bench if the asset is in the shop, on site otherwise);
  a trace attaches itself to its asset's job when the asset has exactly one open job, and
  otherwise waits on the asset page for someone to attach it;
  `phase` (as_found | during | as_left) is an optional addition to rail_session and
  session_tag items; a bad value rejects the item."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import func, insert, select

from app.extensions import db
from app.models import (
    ASSET_STATUSES,
    JOB_STATUSES,
    OPEN_WO_STATUSES,
    PRIORITIES,
    TRACE_PHASES,
    WO_STATUSES,
    Asset,
    Customer,
    RailSession,
    Reading,
    WoJob,
    WoJobSession,
    WorkOrder,
)
from app.models.gatbox import COUNTS, VERDICTS
from app.timeutil import utcnow

CONTRACT = "v1"
CAPABILITIES = ["jobs"]

# Contract limits: over any of them is a 400 that writes nothing.
MAX_ITEMS = 100
MAX_READINGS_PER_SESSION = 2000
MAX_READINGS_PER_REQUEST = 20000
MAX_BODY = 8 * 1024 * 1024

ROSTER_STATUSES = ("in_service", "in_shop", "awaiting_pickup", "shipped_back")
IN_SHOP = ("in_shop", "awaiting_pickup")
# GATBOX's order priorities (gatboxweb/orders.py) to the shop's.
PRIORITY_MAP = {"low": "low", "medium": "normal", "high": "high", "critical": "rush",
                "urgent": "rush"}

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SEEN_EVERY = timedelta(minutes=15)
NUMBER_LIMIT = Decimal("1e15")
TEXT_MAX = 2000


class ApiError(Exception):
    """The whole request is refused: nothing was written."""

    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


class Rejected(Exception):
    """One item is refused, with the reason the Pi will log."""


class TraceError(Exception):
    """Something a person did on a page that can't be done; the message is shown."""


# --- reading the wire -------------------------------------------------------------------------

def _uid(value, what="uid"):
    if not isinstance(value, str) or len(value) != 32 or \
            any(c not in "0123456789abcdef" for c in value):
        raise Rejected(f"{what} must be 32 lowercase hex characters")
    return value


def _num(value, field):
    """A JSON number (int, or Decimal: the body is parsed with parse_float=Decimal)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise Rejected(f"{field} must be a number")
    d = Decimal(value)
    if not d.is_finite() or abs(d) >= NUMBER_LIMIT:
        raise Rejected(f"{field} is out of range")
    return d


def _count(value, field, required=False):
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2_000_000_000:
        raise Rejected(f"{field} must be a whole number")
    return value


def _when(value, field):
    """Epoch seconds (UTC, authoritative in the contract) to a timestamptz, exactly."""
    d = _num(value, field)
    if d is None:
        return None
    if not 0 <= d <= 4_102_444_800:                              # 1970 to 2100
        raise Rejected(f"{field} is out of range")
    micros = int((d * 1_000_000).to_integral_value(rounding=ROUND_HALF_EVEN))
    return EPOCH + timedelta(microseconds=micros)


def _text(value, field, limit=TEXT_MAX):
    if value is None:
        return None
    if not isinstance(value, str):
        raise Rejected(f"{field} must be text")
    if len(value) > limit:
        raise Rejected(f"{field} is longer than {limit} characters")
    return value


def _obj(value, field):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise Rejected(f"{field} must be an object")
    return value


def _phase(value):
    if value is None:
        return None
    if value not in TRACE_PHASES:
        raise Rejected(f"phase must be one of {', '.join(TRACE_PHASES)}")
    return value


def epoch_of(moment):
    """A timestamptz back to epoch seconds, for the wire."""
    return None if moment is None else moment.timestamp()  # float-ok: the wire's epoch


# --- jobs and traces --------------------------------------------------------------------------

def open_jobs(asset_id):
    """The asset's open jobs on open work orders, oldest first."""
    return db.session.scalars(
        select(WoJob).join(WorkOrder, WorkOrder.id == WoJob.work_order_id)
        .where(WoJob.asset_id == asset_id, WoJob.status == "open",
               WorkOrder.status.in_(OPEN_WO_STATUSES))
        .order_by(WoJob.id)).all()


def link_for(job_id, session_id):
    return db.session.scalar(select(WoJobSession).where(
        WoJobSession.wo_job_id == job_id, WoJobSession.rail_session_id == session_id))


def attach(job, session, source, phase=None, note=None):
    """Put a trace on a job. Returns the link, new or existing."""
    if job.asset_id is not None and job.asset_id != session.asset_id:
        raise TraceError("That trace is from a different machine than the job.")
    link = link_for(job.id, session.id)
    if link is None:
        link = WoJobSession(wo_job_id=job.id, rail_session_id=session.id, source=source,
                            phase=phase if phase is not None else session.phase, note=note)
        db.session.add(link)
        db.session.flush()
    return link


def detach(link):
    db.session.delete(link)
    db.session.flush()


def set_phase(link, phase):
    if phase is not None and phase not in TRACE_PHASES:
        raise TraceError("Pick as found, during or as left.")
    link.phase = phase
    db.session.flush()


def traces_for_job(job_id):
    """[(link, session)] on a job, oldest first."""
    return db.session.execute(
        select(WoJobSession, RailSession)
        .join(RailSession, RailSession.id == WoJobSession.rail_session_id)
        .where(WoJobSession.wo_job_id == job_id)
        .order_by(RailSession.started_at.nulls_last(), RailSession.id)).all()


def traces_for_asset(asset_id):
    """[(session, [job ids it's on])], newest first."""
    sessions = db.session.scalars(select(RailSession).where(RailSession.asset_id == asset_id)
                                  .order_by(RailSession.started_at.desc().nulls_last(),
                                            RailSession.id.desc())).all()
    on = {}
    for job_id, session_id in db.session.execute(
            select(WoJobSession.wo_job_id, WoJobSession.rail_session_id)
            .where(WoJobSession.rail_session_id.in_([s.id for s in sessions]))):
        on.setdefault(session_id, []).append(job_id)
    return [(s, sorted(on.get(s.id, []))) for s in sessions]


def unattached(asset_id):
    """The asset's traces that aren't on any job."""
    linked = select(WoJobSession.rail_session_id)
    return db.session.scalars(select(RailSession).where(
        RailSession.asset_id == asset_id, RailSession.id.notin_(linked))
        .order_by(RailSession.started_at.desc().nulls_last(), RailSession.id.desc())).all()


def trace(session_id):
    """A session's readings for a chart: [(seconds from the first, volts)] by uptime."""
    rows = db.session.execute(select(Reading.up, Reading.at, Reading.v)
                              .where(Reading.rail_session_id == session_id)
                              .order_by(Reading.at, Reading.id)).all()
    if not rows:
        return []
    first_up, first_at = rows[0].up, rows[0].at
    out = []
    for up, at, v in rows:
        t = (up - first_up) if up is not None and first_up is not None else \
            Decimal((at - first_at) // timedelta(microseconds=1)) / 1_000_000
        out.append((t, v))
    return out


# --- ingest -----------------------------------------------------------------------------------

def check_request(device, body):
    """The request-level checks: any failure is a 400 and nothing is written."""
    if not isinstance(body, dict):
        raise ApiError("body must be a JSON object")
    if body.get("contract") != CONTRACT:
        raise ApiError(f"contract must be {CONTRACT!r}")
    # The uids are hashed with the device's public id: a payload built for another device
    # carries ids this one could never reproduce.
    if body.get("device") != device.public_id:
        raise ApiError("the payload names a different device than the token does")
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise ApiError("items must be a non-empty list")
    if len(items) > MAX_ITEMS:
        raise ApiError(f"at most {MAX_ITEMS} items per request, got {len(items)}")
    total = sum(len(i["readings"]) for i in items
                if isinstance(i, dict) and isinstance(i.get("readings"), list))
    if total > MAX_READINGS_PER_REQUEST:
        raise ApiError(f"at most {MAX_READINGS_PER_REQUEST} readings per request, got {total}")
    return items


def ingest(device, body, ip=None):
    items = check_request(device, body)
    slugs = {i.get("machine") for i in items if isinstance(i, dict)}
    slugs = {s for s in slugs if isinstance(s, str) and s}
    assets = {a.tag: a for a in db.session.scalars(select(Asset).where(Asset.tag.in_(slugs)))} \
        if slugs else {}
    results = []
    for item in items:
        if not isinstance(item, dict):
            results.append({"uid": None, "kind": None, "status": "rejected",
                            "reason": "item must be a JSON object"})
            continue
        kind = item.get("kind")
        handler = {"rail_session": _ingest_session, "order": _ingest_order,
                   "session_tag": _ingest_tag}.get(kind)
        if handler is None:
            results.append({"uid": item.get("uid"), "kind": kind, "status": "rejected",
                            "reason": f"unknown kind {kind!r}"})
            continue
        savepoint = db.session.begin_nested()
        try:
            result = handler(device, item, assets)
            savepoint.commit()
        except Rejected as e:
            savepoint.rollback()
            result = {"uid": item.get("uid"), "kind": kind, "status": "rejected",
                      "reason": str(e)}
        results.append(result)
    seen(device, ip)
    return {"contract": CONTRACT, "received": len(items), "results": results}


def seen(device, ip):
    """Last seen, at most every SEEN_EVERY (or on a new address): the device row is
    audited, and gatbox-sync polls the roster and orders every couple of minutes."""
    now, ip = utcnow(), (ip or "")[:45] or None
    if device.last_seen_at is None or now - device.last_seen_at >= SEEN_EVERY \
            or device.last_ip != ip:
        device.last_seen_at, device.last_ip = now, ip


def _asset_for(item, assets):
    slug = item.get("machine")
    asset = assets.get(slug) if isinstance(slug, str) else None
    if asset is None:
        # Never invent a machine: the fix is adding the asset, not a row conjured here.
        raise Rejected(f"no machine with slug {slug!r}")
    return asset


def _session_fields(item):
    """Everything a rail_session carries, checked, as column values."""
    window, powered = _obj(item.get("window"), "window"), _obj(item.get("powered"), "powered")
    profile, clock = _obj(item.get("profile"), "profile"), _obj(item.get("clock"), "clock")
    verdict, counts = _obj(item.get("verdict"), "verdict"), _obj(item.get("counts"), "counts")
    state = verdict.get("state")
    if state is not None and state not in VERDICTS:
        raise Rejected(f"verdict.state must be one of {', '.join(VERDICTS)} or null")
    fields = {
        "started_at": _when(item.get("started"), "started"),
        "ended_at": _when(item.get("ended"), "ended"),
        "duration_s": _num(item.get("duration_s"), "duration_s"),
        "samples": _count(item.get("samples"), "samples"),
        "rate": _num(item.get("rate"), "rate"),
        "timebase": _text(item.get("timebase"), "timebase"),
        "clock": _text(clock.get("source"), "clock.source"),
        "clock_ntp_from": _when(clock.get("ntp_from"), "clock.ntp_from"),
        "profile_id": _text(profile.get("id"), "profile.id"),
        "profile_label": _text(profile.get("label"), "profile.label"),
        "profile_kind": _text(profile.get("kind"), "profile.kind"),
        "rail": _text(profile.get("rail"), "profile.rail"),
        "mode": _text(item.get("mode"), "mode"),
        "window_lo": _num(window.get("lo"), "window.lo"),
        "window_hi": _num(window.get("hi"), "window.hi"),
        "window_unit": _text(window.get("unit"), "window.unit"),
        "window_source": _text(window.get("source"), "window.source"),
        "alarm_hi": _num(item.get("alarm_hi"), "alarm_hi"),
        "powered_mean": _num(powered.get("mean"), "powered.mean"),
        "powered_min": _num(powered.get("min"), "powered.min"),
        "powered_max": _num(powered.get("max"), "powered.max"),
        "powered_readings": _count(powered.get("readings"), "powered.readings"),
        "in_window_pct": _num(powered.get("in_window_pct"), "powered.in_window_pct"),
        "verdict": state,
        "verdict_detail": _text(verdict.get("detail"), "verdict.detail"),
        "phase": _phase(item.get("phase")),
    }
    for name in COUNTS:
        fields[name] = _count(counts.get(name), f"counts.{name}") or 0
    return fields


def _reading_rows(readings):
    rows = []
    for r in readings:
        if not isinstance(r, dict):
            raise Rejected("every reading must be a JSON object")
        uid = _uid(r.get("uid"), "every reading's uid")
        if r.get("epoch") is None:
            raise Rejected(f"reading {uid} has no numeric epoch")
        rows.append({"uid": uid, "at": _when(r.get("epoch"), f"reading {uid} epoch"),
                     "up": _num(r.get("up"), f"reading {uid} up"),
                     "v": _num(r.get("v"), f"reading {uid} v"),
                     "raw": _text(r.get("raw"), f"reading {uid} raw", 64),
                     "unit": _text(r.get("unit"), f"reading {uid} unit", 16),
                     "mode": _text(r.get("mode"), f"reading {uid} mode", 16),
                     "alarm": _text(r.get("alarm"), f"reading {uid} alarm", 16),
                     "ol": bool(r.get("ol"))})
    return rows


def _ingest_session(device, item, assets):
    uid = _uid(item.get("uid"))
    out = {"uid": uid, "kind": "rail_session"}
    file_name = _text(item.get("file"), "file", 100)
    if not file_name:
        raise Rejected("file is required")
    readings = item.get("readings")
    readings = [] if readings is None else readings
    if not isinstance(readings, list):
        raise Rejected("readings must be a list")
    if len(readings) > MAX_READINGS_PER_SESSION:
        raise Rejected(f"at most {MAX_READINGS_PER_SESSION} readings per session, "
                       f"got {len(readings)}")
    asset = _asset_for(item, assets)
    fields = _session_fields(item)
    rows = _reading_rows(readings)

    session = db.session.scalar(select(RailSession).where(RailSession.uid == uid))
    status = "duplicate" if session is not None else "created"
    if session is None:
        session = RailSession(uid=uid, device_id=device.id, asset_id=asset.id,
                              file=file_name, **fields)
        db.session.add(session)
        db.session.flush()
        jobs = open_jobs(asset.id)
        if len(jobs) == 1:
            attach(jobs[0], session, "auto")

    # Readings are reconciled even for a duplicate session: a first attempt that failed
    # part-way leaves some of the trace, and "all duplicate" without looking would be a lie.
    uids = [r["uid"] for r in rows]
    have = set(db.session.scalars(select(Reading.uid).where(Reading.uid.in_(uids)))) \
        if uids else set()
    new = []
    for r in rows:
        if r["uid"] not in have:
            have.add(r["uid"])
            new.append({**r, "rail_session_id": session.id})
    if new:
        db.session.execute(insert(Reading), new)
    return {**out, "status": status, "readings": {"created": len(new),
                                                  "duplicate": len(rows) - len(new)}}


def _ingest_order(device, item, assets):
    """A fault raised at the bench becomes a job (owner, 2026-10-13, decision 1)."""
    uid = _uid(item.get("uid"))
    out = {"uid": uid, "kind": "order"}
    issue = _text(item.get("issue"), "issue")
    if not issue or not issue.strip():
        raise Rejected("issue is required")
    asset = _asset_for(item, assets)
    priority = item.get("priority")
    priority = PRIORITY_MAP.get(priority.lower(), "normal") if isinstance(priority, str) \
        else "normal"
    reported = _when(item.get("created"), "created")
    if db.session.scalar(select(WoJob.id).where(WoJob.external_id == uid)) is not None:
        return {**out, "status": "duplicate"}

    session = None
    session_uid = item.get("rail_session")
    if session_uid:
        _uid(session_uid, "rail_session")
        session = db.session.scalar(select(RailSession).where(RailSession.uid == session_uid))
        if session is None:
            # Send the session first: refusing keeps the link rather than dropping it, and
            # the retry costs nothing because both items are idempotent.
            raise Rejected(f"rail_session {session_uid!r} has not been ingested yet")
        if session.asset_id != asset.id:
            raise Rejected("that session is for a different machine than the order")

    wo = db.session.scalar(
        select(WorkOrder).join(WoJob, WoJob.work_order_id == WorkOrder.id)
        .where(WoJob.asset_id == asset.id, WoJob.status != "cancelled",
               WorkOrder.status.in_(OPEN_WO_STATUSES))
        .order_by(WorkOrder.id.desc()).limit(1))
    if wo is None:
        from app.work.service import create_work_order

        customer = db.session.get(Customer, asset.customer_id)
        summary = issue.strip().splitlines()[0][:200]
        wo = create_work_order(customer, kind="bench" if asset.status in IN_SHOP else "on_site",
                               summary=summary, site_id=asset.site_id, priority=priority,
                               notes=f"Raised at the bench on GATBOX ({device.name}).")
    job = WoJob(work_order_id=wo.id, asset_id=asset.id, complaint=issue.strip(),
                external_id=uid, reported_at=reported)
    db.session.add(job)
    db.session.flush()
    if session is not None:
        attach(job, session, "order")
    return {**out, "status": "created"}


def _ingest_tag(device, item, assets):
    """A trace for a job that already exists. Deduped on the (job, session) pair, not the
    uid: a re-minted uid for the same pair is still a duplicate."""
    uid = _uid(item.get("uid"))
    out = {"uid": uid, "kind": "session_tag"}
    job_id = item.get("order")
    if isinstance(job_id, bool) or not isinstance(job_id, int):
        raise Rejected("order must be the hub's integer id for the record")
    job = db.session.get(WoJob, job_id)
    if job is None:
        raise Rejected(f"no order with id {job_id}")
    session_uid = item.get("rail_session")
    if not isinstance(session_uid, str) or not session_uid:
        raise Rejected("rail_session is required")
    session = db.session.scalar(select(RailSession).where(RailSession.uid == session_uid))
    if session is None:
        raise Rejected(f"rail_session {session_uid!r} has not been ingested yet")
    if job.asset_id is not None and job.asset_id != session.asset_id:
        raise Rejected("that session is for a different machine than the order")
    phase = _phase(item.get("phase"))
    note = _text(item.get("note"), "note", 300)
    if link_for(job.id, session.id) is not None:
        return {**out, "status": "duplicate"}
    # Accepted for a closed job: GATBOX tags from a cache that can be minutes stale, and a
    # rejection that never clears is a retry loop, not a diagnosis.
    attach(job, session, "tag", phase=phase, note=(note or "").strip() or None)
    return {**out, "status": "created"}


# --- roster and orders ------------------------------------------------------------------------

def location(asset):
    """Where the machine is, without naming the customer: gatbox-web has no login."""
    if asset.status in IN_SHOP:
        return asset.shop_location or "Shop"
    return {"in_service": "On site", "shipped_back": "Shipped back"}.get(asset.status, "")


def roster():
    assets = db.session.scalars(select(Asset).where(Asset.status.in_(ROSTER_STATUSES))
                                .order_by(Asset.tag)).all()
    return {"contract": CONTRACT, "machines": [
        {"slug": a.tag, "name": a.name, "status": ASSET_STATUSES.get(a.status, a.status),
         "location": location(a), "model_key": a.model_key} for a in assets]}


def orders(slug, status):
    """A machine's jobs, as v1 "orders". Additive fields for the JOB card: work_order and
    nte (money as a string)."""
    if status not in ("open", "all"):
        raise ApiError("status must be 'open' or 'all'")
    asset = db.session.scalar(select(Asset).where(Asset.tag == slug))
    if asset is None:
        raise ApiError(f"no machine with slug {slug!r}", 404)
    q = select(WoJob, WorkOrder).join(WorkOrder, WorkOrder.id == WoJob.work_order_id) \
        .where(WoJob.asset_id == asset.id)
    if status == "open":
        q = q.where(WoJob.status == "open", WorkOrder.status.in_(OPEN_WO_STATUSES))
    rows = db.session.execute(q.order_by(
        func.coalesce(WoJob.reported_at, WoJob.created_at).desc(), WoJob.id.desc())).all()
    return {"contract": CONTRACT, "machine": slug, "status": status, "orders": [
        {"id": job.id, "external_id": job.external_id, "issue": job.complaint,
         "priority": PRIORITIES.get(wo.priority, wo.priority),
         "status": WO_STATUSES.get(wo.status, wo.status) if job.status == "open"
         else JOB_STATUSES.get(job.status, job.status),
         "source": "gatbox" if job.external_id else "web",
         "reported": epoch_of(job.reported_at or job.created_at),
         "work_order": wo.number,
         "nte": str(wo.not_to_exceed) if wo.not_to_exceed is not None else None}
        for job, wo in rows]}
