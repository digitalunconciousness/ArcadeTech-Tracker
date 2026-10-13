from datetime import timedelta

from flask import (
    Blueprint,
    Response,
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
from app.gatbox import service as gatbox
from app.lists import desk_list
from app.models import (
    ALL_LABOR_MODES,
    JOB_STATUSES,
    OPEN_WO_STATUSES,
    PRIORITIES,
    READING_PHASES,
    WO_KINDS,
    WO_STATUSES,
    Appointment,
    AppointmentUser,
    Asset,
    Attachment,
    CommLog,
    Customer,
    Estimate,
    Expense,
    Invoice,
    ManualReading,
    Part,
    PartLink,
    PoLine,
    PurchaseOrder,
    Reservation,
    Service,
    Site,
    StockLot,
    TimeEntry,
    Trip,
    User,
    WoJob,
    WoLine,
    WorkOrder,
    WorkOrderTech,
)
from app.partlinks import service as links
from app.partlinks.forms import AddLinkForm, SendLinksForm
from app.parts import stock
from app.parts.stock import StockError
from app.timeutil import from_local_naive, local_today, to_local_naive, utcnow
from app.work import docs, files, service
from app.work.forms import (
    AddJobForm,
    AppointmentForm,
    CustomerPartForm,
    CustomLineForm,
    FixedLineForm,
    JobForm,
    LineEditForm,
    NewWorkOrderForm,
    OrderedForm,
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
            "job_statuses": JOB_STATUSES, "labor_modes": ALL_LABOR_MODES, "phases": READING_PHASES}


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


def job_order_form():
    from app.purchasing.routes import job_order_form as make
    return make()


def job_po_lines(job):
    """What's been bought (or is to be bought) for this job: [(line, part, PO number)]."""
    return db.session.execute(
        select(PoLine, Part, PurchaseOrder.number).join(Part, Part.id == PoLine.part_id)
        .outerjoin(PurchaseOrder, PurchaseOrder.id == PoLine.po_id)
        .where(PoLine.wo_job_id == job.id).order_by(PoLine.id)).all()


def back_to_job(job_id, anchor=""):
    return redirect(url_for("work.job", job_id=job_id) + anchor)


# --- board and work orders --------------------------------------------------------------

@bp.route("/work")
@requires_role(*ALL_ROLES)
def index():
    power = desk_list("work")
    if isinstance(power, Response):
        return power
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
    return render_template("work/index.html", power=power, groups=groups, show_all=show_all, mine=mine,
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
    invoices = db.session.scalars(select(Invoice).where(Invoice.work_order_id == wo.id,
                                                        Invoice.status != "draft")
                                  .order_by(Invoice.id)).all() if sees_costs() else []
    unbilled = any(x.billable and x.invoice_id is None and x.qty > 0
                   for job, _ in jobs if job.status != "cancelled" for x in lines[job.id])
    from app.books import service as books
    from app.purchasing import service as buying
    from app.purchasing.routes import shipment_form
    book_rows = None
    if sees_costs():
        book_rows = {
            "expenses": db.session.scalars(select(Expense).where(
                Expense.work_order_id == wo.id, Expense.voided_at.is_(None))
                .order_by(Expense.spent_on)).all(),
            "trips": [(t, books.trip_value(t)) for t in db.session.scalars(select(Trip).where(
                Trip.work_order_id == wo.id, Trip.voided_at.is_(None)).order_by(Trip.trip_on))],
        }
    return render_template("work/show.html", wo=wo, customer=customer, site=site, jobs=jobs,
                           book_rows=book_rows,
                           waiting=service.waiting_on(wo) if wo.status == "waiting_parts"
                           else [], shipments=buying.shipments(wo=wo),
                           ship_form=shipment_form() if current_user.role in EDIT_ROLES
                           and service.is_open(wo) else None,
                           job_totals=job_totals, total=total, techs=techs, over_nte=over_nte,
                           status_form=StatusForm(status=wo.status), add_job=add_job,
                           warranty_of=warranty_of, costs=sees_costs(),
                           appointments=appointments, who=who, estimates=estimates,
                           invoices=invoices, unbilled=unbilled, buying=customer_parts(wo),
                           today=local_today(),
                           job_tags={job.id: asset.tag if asset else None for job, asset in jobs},
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
        "fixed": FixedLineForm(tech_user_id=current_user.id),
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
    forms["fixed"].tech_user_id.choices = techs
    forms["part"].part_id.choices = [(p.id, part_label(p)) for p in parts]
    forms["reserve"].part_id.choices = [(p.id, part_label(p)) for p in parts if p.track_stock]
    if not sees_costs():
        del forms["other"].unit_cost
    return forms


def billed_numbers(lines):
    """{invoice_id: number} for the invoices these lines are billed on."""
    ids = {x.invoice_id for x in lines if x.invoice_id}
    if not ids:
        return {}
    return dict(db.session.execute(select(Invoice.id, Invoice.number)
                                   .where(Invoice.id.in_(ids))).all())


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
    part_links = {link.id: link for link in db.session.scalars(select(PartLink).where(
        PartLink.id.in_([x.part_link_id for x in lines if x.part_link_id])))}
    return render_template(
        "work/job.html", job=job, wo=wo, customer=customer, asset=asset, lines=lines,
        part_links=part_links, today=local_today(),
        totals=service.totals(lines), has_customer_part=has_customer_part, lots=lots,
        users=users, entries=entries, my_timer=mine, reservations=reservations,
        readings=readings, photos=photos, costs=sees_costs(),
        billable_minutes=service.billable_minutes(job), actual_hours=service.actual_hours(job),
        fixed=service.fixed_price_rate(job, lines), invoices=billed_numbers(lines),
        warranty=lambda line: service.warranty_days(line, has_customer_part),
        amount=service.line_amount, line_cost=service.line_cost,
        job_form=job_form(job) if edit else None, forms=job_forms(job, wo) if edit else None,
        order_form=job_order_form() if edit and service.is_open(wo) else None,
        po_lines=job_po_lines(job), traces=gatbox.traces_for_job(job.id),
        loose_traces=gatbox.unattached(job.asset_id) if edit and job.asset_id else [])


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


@bp.route("/work/jobs/<int:job_id>/lines/fixed", methods=["POST"])
@requires_role(*EDIT_ROLES)
def line_fixed(job_id):
    job, wo = load_job(job_id)
    form = job_forms(job, wo)["fixed"]
    if form.validate_on_submit():
        attempt(lambda: service.add_fixed_line(
            job, description=form.description.data, unit_price=form.unit_price.data,
            scope=form.scope.data, billable=form.billable.data,
            tech_user_id=form.tech_user_id.data), "Fixed price added.")
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
        wait = form.wait.data
        if attempt(lambda: service.add_own_part(job, description=form.description.data,
                                                qty=form.qty.data, wait=wait),
                   "Added the customer's part: waiting for it to arrive." if wait else
                   "Added the customer's part (no charge, no warranty)."):
            return back_to_job(job.id, "#customer-parts" if wait else "#lines")
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
                tech_user_id=form.tech_user_id.data, scope=form.scope.data), "Line saved."):
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


# --- parts the customer is buying -----------------------------------------------------------

@bp.route("/work/jobs/<int:job_id>/part-links", methods=["POST"])
@requires_role(*EDIT_ROLES)
def add_part_link(job_id):
    job, wo = load_job(job_id)
    form = AddLinkForm()
    if form.validate_on_submit() and form.part_link_id.data.isdigit():
        link = get_or_404(PartLink, int(form.part_link_id.data))
        if attempt(lambda: service.add_part_link(job, link, qty=form.qty.data,
                                                 wait=form.wait.data),
                   f"Added “{link.title}”: waiting for the customer's part."):
            return back_to_job(job.id, "#customer-parts")
    else:
        flash_errors(form)
    return redirect(url_for("partlinks.pick", **{"for": f"wo:{job.id}"}))


def back_to_parts(job, wo):
    """Back to the customer's parts on the page the button was pressed on."""
    if request.form.get("back") == "wo":
        return redirect(url_for("work.show", wo_id=wo.id) + "#customer-parts")
    return back_to_job(job.id, "#customer-parts")


@bp.route("/work/lines/<int:line_id>/ordered", methods=["POST"])
@requires_role(*EDIT_ROLES)
def ordered(line_id):
    """The customer says they've ordered their part: when, and when they expect it."""
    line = get_or_404(WoLine, line_id)
    job, wo = load_job(line.wo_job_id)
    form = OrderedForm()
    if form.validate_on_submit():
        attempt(lambda: service.mark_ordered(line, ordered_on=form.ordered_on.data,
                                             expected_on=form.expected_on.data),
                f"“{line.description}”: ordered.")
    else:
        flash_errors(form)
    return back_to_parts(job, wo)


@bp.route("/work/lines/<int:line_id>/arrived", methods=["POST"])
@requires_role(*EDIT_ROLES)
def arrived(line_id):
    line = get_or_404(WoLine, line_id)
    job, wo = load_job(line.wo_job_id)
    holder = {}
    if EmptyForm().validate_on_submit() and \
            attempt(lambda: holder.update(moved=service.mark_arrived(line)),
                    f"“{line.description}” is here."):
        if holder["moved"]:
            flash(f"Nothing else is on its way: {wo.number} is back In progress.", "ok")
    return back_to_parts(job, wo)


def customer_parts(wo):
    """The customer's parts on this WO that are on their way or came from a saved link,
    each with its link (or None)."""
    return db.session.execute(
        select(WoLine, PartLink).outerjoin(PartLink, PartLink.id == WoLine.part_link_id)
        .join(WoJob, WoJob.id == WoLine.wo_job_id)
        .where(WoJob.work_order_id == wo.id, WoJob.status != "cancelled",
               WoLine.customer_supplied.is_(True),
               or_(WoLine.awaiting.is_(True), WoLine.part_link_id.is_not(None)))
        .order_by(WoLine.awaiting.desc(), WoLine.id)).all()


def link_lines(wo):
    """The customer's-part lines on this WO that came from a saved link, with the link."""
    return db.session.execute(
        select(WoLine, PartLink).join(PartLink, PartLink.id == WoLine.part_link_id)
        .join(WoJob, WoJob.id == WoLine.wo_job_id)
        .where(WoJob.work_order_id == wo.id, WoJob.status != "cancelled")
        .order_by(WoLine.awaiting.desc(), WoLine.id)).all()


@bp.route("/work/<int:wo_id>/send-links", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def send_links(wo_id):
    """The parts the customer is buying, as a text: shared from the phone, and logged on
    the customer's comm log so there's a record of what was sent."""
    wo, customer = load_wo(wo_id)
    rows = link_lines(wo)
    if not rows:
        flash("No parts on this work order came from a saved link.", "error")
        return redirect(url_for("work.show", wo_id=wo.id))
    form = SendLinksForm()
    if request.method == "GET":
        form.message.data = links.message((link.title, link.url) for line, link in rows
                                          if line.awaiting) or \
            links.message((link.title, link.url) for line, link in rows)
    if form.validate_on_submit():
        db.session.add(CommLog(customer_id=customer.id, kind=form.how.data,
                               summary=f"Parts links for {wo.number}:\n{form.message.data}"))
        db.session.commit()
        flash(f"Logged on {customer.display_name}'s record.", "ok")
        return redirect(url_for("work.show", wo_id=wo.id))
    flash_errors(form)
    return render_template("work/send_links.html", wo=wo, customer=customer, rows=rows,
                           form=form)


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
