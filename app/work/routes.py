from datetime import timedelta

from flask import (
    Blueprint,
    abort,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import ALL_ROLES, COST_ROLES, EDIT_ROLES, requires_role
from app.auth.forms import EmptyForm
from app.customers.routes import flash_errors, get_or_404
from app.extensions import db
from app.formatting import qty as fmt_qty
from app.models import (
    JOB_STATUSES,
    LABOR_MODES,
    OPEN_WO_STATUSES,
    PRIORITIES,
    READING_PHASES,
    WO_KINDS,
    WO_STATUSES,
    Appointment,
    AppointmentUser,
    Asset,
    Attachment,
    Customer,
    Estimate,
    ManualReading,
    Part,
    Reservation,
    Service,
    Site,
    StockLot,
    TimeEntry,
    User,
    WoJob,
    WoLine,
    WorkOrder,
    WorkOrderTech,
)
from app.parts import stock
from app.parts.stock import StockError
from app.timeutil import from_local_naive, to_local_naive, utcnow
from app.work import docs, files, service
from app.work.forms import (
    AddJobForm,
    AppointmentForm,
    CustomerPartForm,
    CustomLineForm,
    JobForm,
    LineEditForm,
    NewWorkOrderForm,
    PartLineForm,
    PhotoForm,
    ReadingForm,
    ReserveForm,
    ServiceLineForm,
    StatusForm,
    TimeForm,
    WorkOrderForm,
)
from app.work.service import WorkError

bp = Blueprint("work", __name__)


@bp.context_processor
def labels():
    return {"statuses": WO_STATUSES, "kinds": WO_KINDS, "priorities": PRIORITIES,
            "job_statuses": JOB_STATUSES, "labor_modes": LABOR_MODES, "phases": READING_PHASES}


def sees_costs():
    return current_user.role in COST_ROLES


def attempt(action, ok=None):
    """Run a write and commit it; a refusal rolls back with a message. True if saved."""
    try:
        action()
        db.session.commit()
    except (WorkError, StockError, files.FileError) as e:
        db.session.rollback()
        flash(str(e), "error")
        return False
    except IntegrityError:  # a concurrent change got there first (stock, a timer)
        db.session.rollback()
        flash("Something changed while you were working. Reload and try again.", "error")
        return False
    if ok:
        flash(ok, "ok")
    return True


def tech_choices():
    users = db.session.scalars(select(User).where(User.active.is_(True),
                                                  User.role.in_(EDIT_ROLES))
                               .order_by(User.display_name)).all()
    return [(u.id, u.display_name) for u in users]


def site_choices(customer, keep=None):
    sites = db.session.scalars(select(Site).where(
        Site.customer_id == customer.id, or_(Site.active.is_(True), Site.id == keep))
        .order_by(Site.name)).all()
    return [(None, "—")] + [(s.id, s.name) for s in sites]


def asset_choices(customer, blank="— none (a consult) —"):
    assets = db.session.scalars(select(Asset).where(Asset.customer_id == customer.id)
                                .order_by(Asset.tag)).all()
    return [(None, blank)] + [(a.id, f"{a.tag} · {a.name}") for a in assets]


def load_wo(wo_id):
    wo = get_or_404(WorkOrder, wo_id)
    return wo, db.session.get(Customer, wo.customer_id)


def load_job(job_id):
    job = get_or_404(WoJob, job_id)
    return job, db.session.get(WorkOrder, job.work_order_id)


def back_to_job(job_id, anchor=""):
    return redirect(url_for("work.job", job_id=job_id) + anchor)


# --- board and work orders --------------------------------------------------------------

@bp.route("/work")
@requires_role(*ALL_ROLES)
def index():
    show_all = request.args.get("all") == "1"
    mine = request.args.get("mine") == "1"
    q = select(WorkOrder, Customer).join(Customer, Customer.id == WorkOrder.customer_id)
    if not show_all:
        q = q.where(WorkOrder.status.in_(OPEN_WO_STATUSES))
    if mine:
        q = q.join(WorkOrderTech, WorkOrderTech.work_order_id == WorkOrder.id) \
            .where(WorkOrderTech.user_id == current_user.id)
    rows = db.session.execute(q.order_by(WorkOrder.promised_date.nulls_last(),
                                         WorkOrder.id.desc())).all()
    names = dict(db.session.execute(
        select(WorkOrderTech.work_order_id, func.string_agg(User.display_name, ", "))
        .join(User, User.id == WorkOrderTech.user_id)
        .where(WorkOrderTech.work_order_id.in_([wo.id for wo, _ in rows]))
        .group_by(WorkOrderTech.work_order_id)).all())
    groups = {status: [] for status in WO_STATUSES}
    for wo, customer in rows:
        groups[wo.status].append((wo, customer, names.get(wo.id, "")))
    return render_template("work/index.html", groups=groups, show_all=show_all, mine=mine,
                           today=to_local_naive(utcnow()).date())


@bp.route("/work/new", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def new():
    customer_id = request.args.get("customer", type=int)
    if not customer_id:
        customers = db.session.scalars(select(Customer).where(Customer.active.is_(True))
                                       .order_by(Customer.is_shop.desc(),
                                                 func.lower(Customer.name))).all()
        return render_template("work/pick_customer.html", customers=customers)
    customer = get_or_404(Customer, customer_id)
    form = NewWorkOrderForm(kind="bench" if customer.is_shop else "on_site",
                            techs=[current_user.id], asset_id=request.args.get("asset", type=int))
    form.site_id.choices = site_choices(customer)
    form.asset_id.choices = asset_choices(customer)
    form.techs.choices = tech_choices()
    if form.validate_on_submit():
        holder = {}

        def create():
            wo = service.create_work_order(
                customer, kind=form.kind.data, summary=form.summary.data,
                site_id=form.site_id.data, priority=form.priority.data,
                promised_date=form.promised_date.data, not_to_exceed=form.not_to_exceed.data,
                notes=form.notes.data, tech_ids=form.techs.data)
            service.add_job(wo, form.asset_id.data, form.complaint.data)
            holder["wo"] = wo

        if attempt(create):
            flash(f"Opened {holder['wo'].number}.", "ok")
            return redirect(url_for("work.show", wo_id=holder["wo"].id))
    flash_errors(form)
    return render_template("work/form.html", form=form, customer=customer, wo=None)


@bp.route("/work/<int:wo_id>")
@requires_role(*ALL_ROLES)
def show(wo_id):
    wo, customer = load_wo(wo_id)
    site = db.session.get(Site, wo.site_id) if wo.site_id else None
    jobs = db.session.execute(
        select(WoJob, Asset).outerjoin(Asset, Asset.id == WoJob.asset_id)
        .where(WoJob.work_order_id == wo.id).order_by(WoJob.id)).all()
    lines = service.job_lines([j.id for j, _ in jobs])
    job_totals = {jid: service.totals(ls) for jid, ls in lines.items()}
    total = service.totals([x for ls in lines.values() for x in ls])
    techs = db.session.scalars(select(User).join(WorkOrderTech, WorkOrderTech.user_id == User.id)
                               .where(WorkOrderTech.work_order_id == wo.id)
                               .order_by(User.display_name)).all()
    over_nte = wo.not_to_exceed is not None and total["charge"] > wo.not_to_exceed
    add_job = AddJobForm()
    add_job.asset_id.choices = asset_choices(customer)
    warranty_of = None
    if wo.warranty_of_job_id:
        orig = db.session.get(WoJob, wo.warranty_of_job_id)
        warranty_of = (orig, db.session.get(WorkOrder, orig.work_order_id))
    appointments = db.session.scalars(select(Appointment).where(Appointment.work_order_id == wo.id)
                                      .order_by(Appointment.starts_at)).all()
    who = {}
    for appt_id, name in db.session.execute(
            select(AppointmentUser.appointment_id, User.display_name)
            .join(User, User.id == AppointmentUser.user_id)
            .where(AppointmentUser.appointment_id.in_([a.id for a in appointments]))):
        who.setdefault(appt_id, []).append(name)
    estimates = db.session.scalars(select(Estimate).where(Estimate.work_order_id == wo.id)
                                   .order_by(Estimate.revision)).all()
    return render_template("work/show.html", wo=wo, customer=customer, site=site, jobs=jobs,
                           job_totals=job_totals, total=total, techs=techs, over_nte=over_nte,
                           status_form=StatusForm(status=wo.status), add_job=add_job,
                           warranty_of=warranty_of, costs=sees_costs(),
                           appointments=appointments, who=who, estimates=estimates,
                           appointment_form=appointment_form(wo, customer)
                           if current_user.role in EDIT_ROLES else None)


@bp.route("/work/<int:wo_id>/edit", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def edit(wo_id):
    wo, customer = load_wo(wo_id)
    form = WorkOrderForm(obj=wo, techs=sorted(service.tech_ids(wo)))
    form.site_id.choices = site_choices(customer, wo.site_id)
    form.techs.choices = tech_choices()
    if form.validate_on_submit():
        def save():
            for name in ("kind", "summary", "site_id", "priority", "promised_date",
                         "not_to_exceed", "notes"):
                setattr(wo, name, getattr(form, name).data)
            service.set_techs(wo, form.techs.data)

        if attempt(save, "Saved."):
            return redirect(url_for("work.show", wo_id=wo.id))
    flash_errors(form)
    return render_template("work/form.html", form=form, customer=customer, wo=wo)


@bp.route("/work/<int:wo_id>/status", methods=["POST"])
@requires_role(*EDIT_ROLES)
def status(wo_id):
    wo, _ = load_wo(wo_id)
    form = StatusForm()
    if form.validate_on_submit():
        attempt(lambda: service.set_status(wo, form.status.data),
                f"{wo.number}: {WO_STATUSES[form.status.data].lower()}.")
    else:
        flash_errors(form)
    return redirect(url_for("work.show", wo_id=wo.id))


@bp.route("/work/<int:wo_id>/hold", methods=["POST"])
@requires_role(*EDIT_ROLES)
def hold(wo_id):
    """Over the NTE: one tap to wait on the customer's approval."""
    wo, _ = load_wo(wo_id)
    if EmptyForm().validate_on_submit():
        attempt(lambda: service.set_status(wo, "waiting_approval"),
                f"{wo.number} is waiting on the customer's approval.")
    return redirect(url_for("work.show", wo_id=wo.id))


@bp.route("/work/<int:wo_id>/jobs", methods=["POST"])
@requires_role(*EDIT_ROLES)
def add_job(wo_id):
    wo, customer = load_wo(wo_id)
    form = AddJobForm()
    form.asset_id.choices = asset_choices(customer)
    holder = {}
    if form.validate_on_submit():
        if attempt(lambda: holder.update(job=service.add_job(wo, form.asset_id.data,
                                                             form.complaint.data))):
            return back_to_job(holder["job"].id)
    flash_errors(form)
    return redirect(url_for("work.show", wo_id=wo.id))


# --- appointments ------------------------------------------------------------------------

def appointment_form(wo, customer):
    form = AppointmentForm(site_id=wo.site_id, users=sorted(service.tech_ids(wo)) or
                           [current_user.id])
    form.site_id.choices = site_choices(customer, wo.site_id)
    form.users.choices = tech_choices()
    return form


@bp.route("/work/<int:wo_id>/appointments", methods=["POST"])
@requires_role(*EDIT_ROLES)
def appointment_add(wo_id):
    wo, customer = load_wo(wo_id)
    form = appointment_form(wo, customer)
    if form.validate_on_submit():
        def add():
            start = from_local_naive(form.starts_at.data)
            appt = Appointment(work_order_id=wo.id, starts_at=start,
                               ends_at=start + timedelta(minutes=form.minutes.data),
                               site_id=form.site_id.data, note=form.note.data)
            db.session.add(appt)
            db.session.flush()
            for uid in form.users.data:
                db.session.add(AppointmentUser(appointment_id=appt.id, user_id=uid))
            if wo.status == "new":
                wo.status = "scheduled"

        attempt(add, "Appointment added.")
    else:
        flash_errors(form)
    return redirect(url_for("work.show", wo_id=wo.id) + "#appointments")


@bp.route("/work/appointments/<int:appt_id>/delete", methods=["POST"])
@requires_role(*EDIT_ROLES)
def appointment_delete(appt_id):
    appt = get_or_404(Appointment, appt_id)
    if EmptyForm().validate_on_submit():
        def remove():
            for row in db.session.scalars(select(AppointmentUser).where(
                    AppointmentUser.appointment_id == appt.id)):
                db.session.delete(row)
            db.session.flush()
            db.session.delete(appt)

        attempt(remove, "Appointment removed.")
    return redirect(url_for("work.show", wo_id=appt.work_order_id) + "#appointments")


# --- a job --------------------------------------------------------------------------------

def job_forms(job, wo):
    services = db.session.scalars(select(Service).where(Service.active.is_(True))
                                  .order_by(Service.kind, Service.code)).all()
    parts = db.session.scalars(select(Part).where(Part.active.is_(True))
                               .order_by(func.lower(Part.sku))).all()
    counts = stock.on_hand_map(p.id for p in parts)
    techs = [(None, "—")] + tech_choices()

    def part_label(p):
        return f"{p.sku} · {p.name}" + (f" ({fmt_qty(counts.get(p.id, 0))} on hand)"
                                        if p.track_stock else "")

    forms = {
        "service": ServiceLineForm(tech_user_id=current_user.id),
        "part": PartLineForm(),
        "customer_part": CustomerPartForm(),
        "other": CustomLineForm(),
        "time": TimeForm(started_at=to_local_naive(utcnow())),
        "reserve": ReserveForm(),
        "reading": ReadingForm(),
        "photo": PhotoForm(),
    }
    forms["service"].service_id.choices = [(s.id, f"{s.code} · {s.name}") for s in services]
    forms["service"].tech_user_id.choices = techs
    forms["part"].part_id.choices = [(p.id, part_label(p)) for p in parts]
    forms["reserve"].part_id.choices = [(p.id, part_label(p)) for p in parts if p.track_stock]
    if not sees_costs():
        del forms["other"].unit_cost
    return forms


def job_form(job):
    form = JobForm(obj=job)
    form.received_at.data = to_local_naive(job.received_at)  # obj would win over a kwarg
    return form


@bp.route("/work/jobs/<int:job_id>")
@requires_role(*ALL_ROLES)
def job(job_id):
    job, wo = load_job(job_id)
    customer = db.session.get(Customer, wo.customer_id)
    asset = db.session.get(Asset, job.asset_id) if job.asset_id else None
    lines = service.job_lines([job.id])[job.id]
    has_customer_part = any(x.customer_supplied for x in lines)
    lots = dict(db.session.execute(select(StockLot.id, StockLot.date_code).where(
        StockLot.id.in_([x.stock_lot_id for x in lines if x.stock_lot_id]))).all())
    users = {u.id: u for u in db.session.scalars(select(User))}
    entries = db.session.scalars(select(TimeEntry).where(TimeEntry.wo_job_id == job.id)
                                 .order_by(TimeEntry.started_at.desc())).all()
    mine = service.running_timer(current_user.id)
    reservations = db.session.execute(
        select(Reservation, Part).join(Part, Part.id == Reservation.part_id)
        .where(Reservation.wo_job_id == job.id).order_by(Reservation.id)).all()
    readings = db.session.scalars(select(ManualReading).where(ManualReading.wo_job_id == job.id)
                                  .order_by(ManualReading.test_point, ManualReading.taken_at)
                                  ).all()
    photos = db.session.scalars(select(Attachment).where(Attachment.wo_job_id == job.id,
                                                         Attachment.kind == "photo")
                                .order_by(Attachment.id)).all()
    edit = current_user.role in EDIT_ROLES
    return render_template(
        "work/job.html", job=job, wo=wo, customer=customer, asset=asset, lines=lines,
        totals=service.totals(lines), has_customer_part=has_customer_part, lots=lots,
        users=users, entries=entries, my_timer=mine, reservations=reservations,
        readings=readings, photos=photos, costs=sees_costs(),
        billable_minutes=service.billable_minutes(job), actual_hours=service.actual_hours(job),
        warranty=lambda line: service.warranty_days(line, has_customer_part),
        amount=service.line_amount, line_cost=service.line_cost,
        job_form=job_form(job) if edit else None, forms=job_forms(job, wo) if edit else None)


@bp.route("/work/jobs/<int:job_id>/edit", methods=["POST"])
@requires_role(*EDIT_ROLES)
def job_edit(job_id):
    job, _ = load_job(job_id)
    form = JobForm()
    if form.validate_on_submit():
        def save():
            for name in ("complaint", "cause", "correction", "received_via", "condition",
                         "accessories", "accessories_note", "inbound_tracking"):
                setattr(job, name, getattr(form, name).data)
            job.received_at = from_local_naive(form.received_at.data)
            service.set_job_status(job, form.status.data)

        attempt(save, "Saved.")
    else:
        flash_errors(form)
    return back_to_job(job.id)


@bp.route("/work/jobs/<int:job_id>/timer", methods=["POST"])
@requires_role(*EDIT_ROLES)
def timer(job_id):
    job, wo = load_job(job_id)
    if not EmptyForm().validate_on_submit():
        return back_to_job(job.id)
    if request.form.get("action") == "stop":
        entry = service.running_timer(current_user.id)
        if entry is None:
            flash("Your timer isn't running.", "error")
        else:
            attempt(lambda: service.stop_timer(entry), "Timer stopped.")
    else:
        holder = {}

        def start():
            holder["entry"], holder["stopped"] = service.start_timer(job, current_user.id)

        if attempt(start, "Timer running."):
            if holder["stopped"] is not None:
                other = db.session.get(WoJob, holder["stopped"].wo_job_id)
                flash(f"Stopped your timer on {db.session.get(WorkOrder, other.work_order_id).number}"
                      f" ({holder['stopped'].minutes} min).", "ok")
    return back_to_job(job.id)


@bp.route("/work/jobs/<int:job_id>/time", methods=["POST"])
@requires_role(*EDIT_ROLES)
def time_add(job_id):
    job, _ = load_job(job_id)
    form = TimeForm()
    if form.validate_on_submit():
        attempt(lambda: service.add_manual_time(
            job, current_user.id, minutes=form.minutes.data,
            started_at=from_local_naive(form.started_at.data), billable=form.billable.data,
            note=form.note.data), "Time added.")
    else:
        flash_errors(form)
    return back_to_job(job.id, "#time")


@bp.route("/work/time/<int:entry_id>/delete", methods=["POST"])
@requires_role(*EDIT_ROLES)
def time_delete(entry_id):
    entry = get_or_404(TimeEntry, entry_id)
    if EmptyForm().validate_on_submit():
        attempt(lambda: service.delete_time(entry), "Time removed.")
    return back_to_job(entry.wo_job_id, "#time")


# --- lines --------------------------------------------------------------------------------

@bp.route("/work/jobs/<int:job_id>/lines/service", methods=["POST"])
@requires_role(*EDIT_ROLES)
def line_service(job_id):
    job, wo = load_job(job_id)
    form = job_forms(job, wo)["service"]
    if form.validate_on_submit():
        svc = get_or_404(Service, form.service_id.data)
        attempt(lambda: service.add_service_line(
            job, svc, qty=form.qty.data, labor_mode=form.labor_mode.data,
            unit_price=form.unit_price.data, description=form.description.data,
            tech_user_id=form.tech_user_id.data, billable=form.billable.data), "Line added.")
    else:
        flash_errors(form)
    return back_to_job(job.id, "#lines")


@bp.route("/work/jobs/<int:job_id>/lines/part", methods=["POST"])
@requires_role(*EDIT_ROLES)
def line_part(job_id):
    job, wo = load_job(job_id)
    form = job_forms(job, wo)["part"]
    if form.validate_on_submit():
        part = get_or_404(Part, form.part_id.data)
        attempt(lambda: service.issue_part(job, part, form.qty.data,
                                           unit_price=form.unit_price.data,
                                           billable=form.billable.data, wo_number=wo.number),
                f"Issued {fmt_qty(form.qty.data)} × {part.sku}.")
    else:
        flash_errors(form)
    return back_to_job(job.id, "#lines")


@bp.route("/work/jobs/<int:job_id>/lines/customer-part", methods=["POST"])
@requires_role(*EDIT_ROLES)
def line_customer_part(job_id):
    job, _ = load_job(job_id)
    form = CustomerPartForm()
    if form.validate_on_submit():
        attempt(lambda: service.add_customer_part(job, description=form.description.data,
                                                  qty=form.qty.data),
                "Added the customer's part (no charge, no warranty).")
    else:
        flash_errors(form)
    return back_to_job(job.id, "#lines")


@bp.route("/work/jobs/<int:job_id>/lines/other", methods=["POST"])
@requires_role(*EDIT_ROLES)
def line_other(job_id):
    job, _ = load_job(job_id)
    form = CustomLineForm()
    if not sees_costs():
        del form.unit_cost
    if form.validate_on_submit():
        attempt(lambda: service.add_custom_line(
            job, kind=form.kind.data, description=form.description.data, qty=form.qty.data,
            unit_price=form.unit_price.data, taxable=form.taxable.data,
            billable=form.billable.data,
            unit_cost=form.unit_cost.data if "unit_cost" in form else None), "Line added.")
    else:
        flash_errors(form)
    return back_to_job(job.id, "#lines")


@bp.route("/work/lines/<int:line_id>/edit", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def line_edit(line_id):
    line = get_or_404(WoLine, line_id)
    job, wo = load_job(line.wo_job_id)
    form = LineEditForm(obj=line)
    form.tech_user_id.choices = [(None, "—")] + tech_choices()
    if not sees_costs():
        del form.unit_cost
    if form.validate_on_submit():
        if attempt(lambda: service.edit_line(
                line, description=form.description.data, unit_price=form.unit_price.data,
                billable=form.billable.data, taxable=form.taxable.data, qty=form.qty.data,
                unit_cost=form.unit_cost.data if "unit_cost" in form else line.unit_cost,
                tech_user_id=form.tech_user_id.data), "Line saved."):
            return back_to_job(job.id, "#lines")
    flash_errors(form)
    return render_template("work/line_form.html", form=form, line=line, job=job, wo=wo)


@bp.route("/work/lines/<int:line_id>/delete", methods=["POST"])
@requires_role(*EDIT_ROLES)
def line_delete(line_id):
    line = get_or_404(WoLine, line_id)
    job, wo = load_job(line.wo_job_id)
    if EmptyForm().validate_on_submit():
        part = db.session.get(Part, line.part_id) if line.part_id else None
        attempt(lambda: service.remove_line(line, part, wo.number),
                "Line removed" + (" and the part returned to stock." if line.stock_lot_id
                                  else "."))
    return back_to_job(job.id, "#lines")


# --- reservations ------------------------------------------------------------------------

@bp.route("/work/jobs/<int:job_id>/reserve", methods=["POST"])
@requires_role(*EDIT_ROLES)
def reserve(job_id):
    job, wo = load_job(job_id)
    form = job_forms(job, wo)["reserve"]
    if form.validate_on_submit():
        part = get_or_404(Part, form.part_id.data)
        attempt(lambda: service.reserve(job, part, form.qty.data, form.note.data),
                f"Set aside {fmt_qty(form.qty.data)} × {part.sku}.")
    else:
        flash_errors(form)
    return back_to_job(job.id, "#parts")


@bp.route("/work/reservations/<int:res_id>/<action>", methods=["POST"])
@requires_role(*EDIT_ROLES)
def reservation(res_id, action):
    res = get_or_404(Reservation, res_id)
    job, wo = load_job(res.wo_job_id)
    if action not in ("issue", "release"):
        abort(404)
    if EmptyForm().validate_on_submit():
        if action == "issue":
            part = db.session.get(Part, res.part_id)
            attempt(lambda: service.issue_reservation(res, part, wo_number=wo.number),
                    f"Issued {fmt_qty(res.qty)} × {part.sku}.")
        else:
            attempt(lambda: service.release_reservation(res), "Released.")
    return back_to_job(job.id, "#parts")


# --- readings and photos -------------------------------------------------------------------

@bp.route("/work/jobs/<int:job_id>/readings", methods=["POST"])
@requires_role(*EDIT_ROLES)
def reading_add(job_id):
    job, _ = load_job(job_id)
    form = ReadingForm()
    if form.validate_on_submit():
        def add():
            db.session.add(ManualReading(
                wo_job_id=job.id, test_point=form.test_point.data, phase=form.phase.data,
                value=form.value.data, unit=form.unit.data, spec_lo=form.spec_lo.data,
                spec_hi=form.spec_hi.data, note=form.note.data))

        attempt(add, "Reading saved.")
    else:
        flash_errors(form)
    return back_to_job(job.id, "#readings")


@bp.route("/work/readings/<int:reading_id>/delete", methods=["POST"])
@requires_role(*EDIT_ROLES)
def reading_delete(reading_id):
    reading = get_or_404(ManualReading, reading_id)
    if EmptyForm().validate_on_submit():
        attempt(lambda: db.session.delete(reading), "Reading removed.")
    return back_to_job(reading.wo_job_id, "#readings")


@bp.route("/work/jobs/<int:job_id>/photos", methods=["POST"])
@requires_role(*EDIT_ROLES)
def photo_add(job_id):
    job, _ = load_job(job_id)
    form = PhotoForm()
    if form.validate_on_submit():
        def add():
            sha, size, width, height = files.store_photo(form.photo.data.stream)
            db.session.add(Attachment(wo_job_id=job.id, kind="photo", sha256=sha,
                                      content_type="image/jpeg", size_bytes=size, width=width,
                                      height=height, caption=form.caption.data))

        attempt(add, "Photo added.")
    else:
        flash_errors(form)
    return back_to_job(job.id, "#photos")


@bp.route("/work/photos/<int:attachment_id>/delete", methods=["POST"])
@requires_role(*EDIT_ROLES)
def photo_delete(attachment_id):
    att = get_or_404(Attachment, attachment_id)
    if EmptyForm().validate_on_submit():
        attempt(lambda: db.session.delete(att), "Photo removed.")
    return back_to_job(att.wo_job_id, "#photos")


@bp.route("/files/<sha256>.jpg")
@requires_role(*ALL_ROLES)
def file(sha256):
    """A stored photo, by hash: only one the database knows about, only when signed in."""
    if db.session.scalar(select(Attachment.id).where(Attachment.sha256 == sha256)) is None:
        abort(404)
    path = files.thumb_path(sha256) if request.args.get("thumb") else files.path_for(sha256)
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/jpeg", max_age=0)


# --- documents ---------------------------------------------------------------------------

def pdf_response(data, filename):
    resp = make_response(data)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp


@bp.route("/work/<int:wo_id>/claim-ticket.pdf")
@requires_role(*ALL_ROLES)
def claim_ticket(wo_id):
    wo, customer = load_wo(wo_id)
    return pdf_response(docs.claim_ticket(wo, customer), f"{wo.number}-claim-ticket.pdf")


@bp.route("/work/<int:wo_id>/service-report.pdf")
@requires_role(*ALL_ROLES)
def service_report(wo_id):
    wo, customer = load_wo(wo_id)
    return pdf_response(docs.service_report(wo, customer), f"{wo.number}-service-report.pdf")
