"""The desktop's planning pages (Phase 9b): the work board, a column per status with cards
moved between them, and the dispatch calendar, appointments in a lane per tech. Both are
shortcuts for what the work order page already does: a moved card is the status form, a
booked slot is an appointment."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import case, or_, select

from app.auth.decorators import ALL_ROLES, EDIT_ROLES, requires_role
from app.auth.forms import EmptyForm
from app.customers.routes import flash_errors
from app.extensions import db
from app.models import (
    OPEN_WO_STATUSES,
    WO_STATUSES,
    Appointment,
    AppointmentUser,
    Customer,
    User,
    WoJob,
    WoLine,
    WorkOrder,
    WorkOrderTech,
)
from app.timeutil import from_local_naive, local_today, localdt, shop_tz, to_local_naive, utcnow
from app.work import service
from app.work.forms import AppointmentForm
from app.work.routes import bp, site_choices, tech_choices
from app.work.service import WorkError

BOARD_COLUMNS = (*OPEN_WO_STATUSES, "completed")
DONE_DAYS = 14                      # completed work orders stay on the board this long
PRIORITY_ORDER = case((WorkOrder.priority == "rush", 0), (WorkOrder.priority == "high", 1),
                      (WorkOrder.priority == "normal", 2), else_=3)


def initials(name):
    return "".join(part[0] for part in name.split()[:2]).upper()


def board_cards(tech=None, text=""):
    """{status: [card]} for the board's columns. Open work orders by priority, then the
    promised date; completed ones from the last DONE_DAYS days, newest first. A card is
    a dict: wo, customer, techs (names), late, unbilled (lines not billed yet), waiting
    (what a WO waiting on parts still waits for)."""
    since = utcnow() - timedelta(days=DONE_DAYS)
    q = (select(WorkOrder, Customer).join(Customer, Customer.id == WorkOrder.customer_id)
         .where(or_(WorkOrder.status.in_(OPEN_WO_STATUSES),
                    (WorkOrder.status == "completed") & (WorkOrder.completed_at >= since))))
    if tech:
        q = q.where(WorkOrder.id.in_(select(WorkOrderTech.work_order_id)
                                     .where(WorkOrderTech.user_id == tech)))
    if text:
        pat = f"%{text}%"
        q = q.where(or_(WorkOrder.number.ilike(pat), WorkOrder.summary.ilike(pat),
                        Customer.name.ilike(pat), Customer.dba.ilike(pat)))
    rows = db.session.execute(q.order_by(
        PRIORITY_ORDER, WorkOrder.promised_date.nulls_last(), WorkOrder.completed_at.desc(),
        WorkOrder.id)).all()
    ids = [wo.id for wo, _ in rows]
    names = {}
    for wo_id, name in db.session.execute(
            select(WorkOrderTech.work_order_id, User.display_name)
            .join(User, User.id == WorkOrderTech.user_id)
            .where(WorkOrderTech.work_order_id.in_(ids)).order_by(User.display_name)):
        names.setdefault(wo_id, []).append(name)
    unbilled = set(db.session.scalars(
        select(WoJob.work_order_id).join(WoLine, WoLine.wo_job_id == WoJob.id)
        .where(WoJob.work_order_id.in_(ids), WoJob.status != "cancelled",
               WoLine.billable.is_(True), WoLine.invoice_id.is_(None), WoLine.qty > 0,
               WoLine.awaiting.is_(False)).distinct()))
    today = local_today()
    columns = {status: [] for status in BOARD_COLUMNS}
    for wo, customer in rows:
        columns[wo.status].append({
            "wo": wo, "customer": customer, "techs": names.get(wo.id, []),
            "late": wo.promised_date is not None and wo.promised_date < today
            and wo.status != "completed",
            "unbilled": wo.id in unbilled,
            "waiting": service.waiting_on(wo) if wo.status == "waiting_parts" else [],
        })
    # completed: newest first, whatever the priority
    columns["completed"].sort(key=lambda c: c["wo"].completed_at, reverse=True)
    return columns


@bp.route("/work/board")
@requires_role(*ALL_ROLES)
def board():
    tech = request.args.get("tech", "")
    tech_id = current_user.id if tech == "mine" else (int(tech) if tech.isdigit() else None)
    text = (request.args.get("q") or "").strip()[:100]
    columns = board_cards(tech_id, text)
    return render_template("work/board.html", columns=columns, column_labels=WO_STATUSES,
                           techs=tech_choices(), tech=tech, q=text, done_days=DONE_DAYS,
                           initials=initials, can_move=current_user.role in EDIT_ROLES,
                           moves=[(s, WO_STATUSES[s]) for s in WO_STATUSES])


# --- the dispatch calendar ------------------------------------------------------------------

DAY_START, DAY_END = 6, 22          # the calendar's hours (local)
SLOT = 15                           # minutes; drags snap to it
DEFAULT_MINUTES = 60                # a work order dropped on the calendar
QUEUE_STATUSES = ("new", "scheduled", "in_progress", "ready")


def parse_day(raw):
    try:
        return date.fromisoformat(raw or "")
    except ValueError:
        return local_today()


def parse_local(raw):
    """'2026-10-13T09:15' (the shop's wall clock) -> UTC, or None."""
    try:
        return from_local_naive(datetime.strptime(raw or "", "%Y-%m-%dT%H:%M"))
    except ValueError:
        return None


def day_bounds(first, days=1):
    tz = shop_tz()
    return (datetime.combine(first, time.min, tzinfo=tz),
            datetime.combine(first + timedelta(days=days), time.min, tzinfo=tz))


def wall_minutes(value, day):
    """Minutes from the day's local midnight by the wall clock (so a daylight-saving day
    still lays out 09:00 at 09:00)."""
    local = value.astimezone(shop_tz()).replace(tzinfo=None)
    return int((local - datetime.combine(day, time.min)).total_seconds() // 60)


def appointments_between(start, end):
    """[{appt, wo, customer, users: [ids]}] overlapping start..end, earliest first."""
    rows = db.session.execute(
        select(Appointment, WorkOrder, Customer)
        .join(WorkOrder, WorkOrder.id == Appointment.work_order_id)
        .join(Customer, Customer.id == WorkOrder.customer_id)
        .where(Appointment.starts_at < end, Appointment.ends_at > start)
        .order_by(Appointment.starts_at, Appointment.id)).all()
    users = defaultdict(list)
    for appt_id, uid in db.session.execute(
            select(AppointmentUser.appointment_id, AppointmentUser.user_id)
            .where(AppointmentUser.appointment_id.in_([a.id for a, _, _ in rows]))):
        users[appt_id].append(uid)
    return [{"appt": a, "wo": wo, "customer": c, "users": sorted(users[a.id])}
            for a, wo, c in rows]


def double_booked(items):
    """Ids of appointments that share a tech with another at the same time."""
    by_user = defaultdict(list)
    for item in items:
        for uid in item["users"]:
            by_user[uid].append(item["appt"])
    flagged = set()
    for appts in by_user.values():
        appts.sort(key=lambda a: a.starts_at)
        for i, a in enumerate(appts):
            for b in appts[i + 1:]:
                if b.starts_at >= a.ends_at:
                    break
                flagged |= {a.id, b.id}
    return flagged


def to_schedule(limit=40):
    """Open work orders with nothing booked from now on: new ones first."""
    booked = select(Appointment.work_order_id).where(Appointment.ends_at >= utcnow())
    return db.session.execute(
        select(WorkOrder, Customer).join(Customer, Customer.id == WorkOrder.customer_id)
        .where(WorkOrder.status.in_(QUEUE_STATUSES), WorkOrder.id.notin_(booked))
        .order_by(case((WorkOrder.status == "new", 0), else_=1), PRIORITY_ORDER,
                  WorkOrder.promised_date.nulls_last(), WorkOrder.id).limit(limit)).all()


def techs_by_id(extra=()):
    """{id: name}: the active owners and techs, plus anyone else on these appointments."""
    users = db.session.scalars(select(User).where(or_(
        (User.active.is_(True)) & User.role.in_(EDIT_ROLES), User.id.in_(list(extra))))
        .order_by(User.display_name)).all()
    return {u.id: u.display_name for u in users}


@bp.route("/work/dispatch")
@requires_role(*ALL_ROLES)
def dispatch():
    day = parse_day(request.args.get("day"))
    view = "week" if request.args.get("view") == "week" else "day"
    days = [day - timedelta(days=day.weekday()) + timedelta(days=i) for i in range(7)] \
        if view == "week" else [day]
    items = appointments_between(*day_bounds(days[0], len(days)))
    flagged = double_booked(items)
    names = techs_by_id({uid for item in items for uid in item["users"]})
    tz = shop_tz()
    for item in items:
        appt = item["appt"]
        item["day"] = appt.starts_at.astimezone(tz).date()
        if item["day"] < days[0]:                    # began the evening before
            item["day"] = days[0]
        item["start"] = max(wall_minutes(appt.starts_at, item["day"]), DAY_START * 60)
        item["end"] = min(max(wall_minutes(appt.ends_at, item["day"]), item["start"] + SLOT),
                          DAY_END * 60)
        item["start"] = min(item["start"], item["end"] - SLOT)
        item["conflict"] = appt.id in flagged
        item["names"] = [names.get(uid, "?") for uid in item["users"]]
    if view == "week":
        columns = [{"label": d.strftime("%a %b %-d"), "day": d.isoformat(), "user": "*",
                    "today": d == local_today(),
                    "items": [i for i in items if i["day"] == d]} for d in days]
    else:
        columns = [{"label": name, "day": day.isoformat(), "user": str(uid), "today": False,
                    "items": [i for i in items if uid in i["users"]]}
                   for uid, name in names.items()]
        columns.append({"label": "Unassigned", "day": day.isoformat(), "user": "",
                        "today": False, "items": [i for i in items if not i["users"]]})
    step = timedelta(days=7 if view == "week" else 1)
    return render_template(
        "work/dispatch.html", day=day, days=days, view=view, columns=columns, items=items,
        flagged=[i for i in items if i["conflict"]], queue=to_schedule(),
        prev_day=(day - step).isoformat(), next_day=(day + step).isoformat(),
        hours=range(DAY_START, DAY_END), day_start=DAY_START, day_end=DAY_END, slot=SLOT,
        default_minutes=DEFAULT_MINUTES, can_book=current_user.role in EDIT_ROLES,
        statuses=WO_STATUSES, localdt=localdt, here=request.full_path.rstrip("?"))


def _json_refusal(message, code=400):
    return jsonify(error=message), code


def _tech(raw):
    """A lane's user id from the form: an active owner or tech, or None (unassigned)."""
    if not raw:
        return None
    user = db.session.get(User, int(raw)) if raw.isdigit() else None
    if user is None or not user.active or user.role not in EDIT_ROLES:
        abort(400)
    return user.id


def _minutes(raw, default):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(SLOT, min(value, 24 * 60))


@bp.route("/work/dispatch/book", methods=["POST"])
@requires_role(*EDIT_ROLES)
def dispatch_book():
    """A work order dropped on the calendar: an appointment at that time, with that lane's
    tech."""
    if not EmptyForm().validate_on_submit():
        return _json_refusal("That didn't save. Reload the page and try again.")
    wo = db.session.get(WorkOrder, request.form.get("wo_id", type=int) or 0)
    start = parse_local(request.form.get("starts_at"))
    if wo is None or start is None:
        return _json_refusal("Pick a work order and a time.")
    tech = _tech(request.form.get("user", ""))
    try:
        _, warnings = service.book(wo, start=start,
                                   minutes=_minutes(request.form.get("minutes"),
                                                    DEFAULT_MINUTES),
                                   user_ids=[tech] if tech else [], site_id=wo.site_id)
        db.session.commit()
    except WorkError as e:
        db.session.rollback()
        return _json_refusal(str(e), 409)
    return jsonify(message=f"Booked {wo.number} at {localdt(start, '%a %H:%M')}.",
                   warnings=warnings)


@bp.route("/work/appointments/<int:appt_id>/move", methods=["POST"])
@requires_role(*EDIT_ROLES)
def appointment_move(appt_id):
    """An appointment dragged to another time or lane, or resized. from_user/to_user are
    the lanes ("" unassigned, "*" keep who's on it)."""
    appt = db.session.get(Appointment, appt_id) or abort(404)
    if not EmptyForm().validate_on_submit():
        return _json_refusal("That didn't save. Reload the page and try again.")
    start = parse_local(request.form.get("starts_at"))
    if start is None:
        return _json_refusal("Pick a time.")
    minutes = _minutes(request.form.get("minutes"),
                       int((appt.ends_at - appt.starts_at).total_seconds() // 60))
    source, target = request.form.get("from_user", "*"), request.form.get("to_user", "*")
    user_ids = None
    if target != "*" and source != target:
        user_ids = service.appointment_users(appt)
        if source.isdigit():
            user_ids.discard(int(source))
        new = _tech(target)
        if new:
            user_ids.add(new)
    try:
        warnings = service.reschedule(appt, start=start, minutes=minutes, user_ids=user_ids)
        db.session.commit()
    except WorkError as e:
        db.session.rollback()
        return _json_refusal(str(e), 409)
    return jsonify(message=f"{appt_label(appt)}: now {localdt(appt.starts_at, '%a %H:%M')}–"
                           f"{localdt(appt.ends_at, '%H:%M')}.", warnings=warnings)


def appt_label(appt):
    return db.session.scalar(select(WorkOrder.number).where(WorkOrder.id == appt.work_order_id))


def safe_next(raw, default):
    """Only back to a page of this app."""
    return raw if raw and raw.startswith("/") and not raw.startswith("//") else default


@bp.route("/work/appointments/<int:appt_id>/edit", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def appointment_edit(appt_id):
    appt = db.session.get(Appointment, appt_id) or abort(404)
    wo = db.session.get(WorkOrder, appt.work_order_id)
    customer = db.session.get(Customer, wo.customer_id)
    back = safe_next(request.values.get("next"),
                     url_for("work.show", wo_id=wo.id) + "#appointments")
    minutes = int((appt.ends_at - appt.starts_at).total_seconds() // 60)
    form = AppointmentForm(starts_at=to_local_naive(appt.starts_at), minutes=minutes,
                           site_id=appt.site_id, users=sorted(service.appointment_users(appt)),
                           note=appt.note)
    form.site_id.choices = site_choices(customer, appt.site_id)
    form.users.choices = tech_choices()
    if form.validate_on_submit():
        try:
            warnings = service.reschedule(appt, start=from_local_naive(form.starts_at.data),
                                          minutes=form.minutes.data, user_ids=form.users.data)
            appt.site_id, appt.note = form.site_id.data, form.note.data
            db.session.commit()
        except WorkError as e:
            db.session.rollback()
            flash(str(e), "error")
        else:
            flash("Appointment saved.", "ok")
            for warning in warnings:
                flash(f"Heads up: {warning}.", "error")
            return redirect(back)
    else:
        flash_errors(form)
    return render_template("work/appointment_edit.html", appt=appt, wo=wo, customer=customer,
                           form=form, back=back)
