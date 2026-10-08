from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import ALL_ROLES, COST_ROLES, EDIT_ROLES, requires_role
from app.auth.forms import EmptyForm
from app.customers.routes import flash_errors, get_or_404
from app.estimates import docs, service
from app.estimates.forms import (
    ConvertForm,
    CustomLineForm,
    DeclineForm,
    EstimateForm,
    LineEditForm,
    NewEstimateForm,
    PartLineForm,
    ServiceLineForm,
    SignForm,
    TemplateForm,
    VerbalForm,
)
from app.estimates.service import EstimateError
from app.extensions import client_ip, db
from app.models import (
    APPROVAL_METHODS,
    ESTIMATE_STATUSES,
    Customer,
    DocLink,
    Estimate,
    EstimateJob,
    EstimateLine,
    JobTemplate,
    Part,
    Service,
)
from app.timeutil import utcnow
from app.work import files
from app.work.forms import AddJobForm
from app.work.routes import asset_choices, pdf_response, site_choices, tech_choices

bp = Blueprint("estimates", __name__)


@bp.context_processor
def labels():
    return {"est_statuses": ESTIMATE_STATUSES, "methods": APPROVAL_METHODS}


def attempt(action, ok=None):
    try:
        action()
        db.session.commit()
    except EstimateError as e:
        db.session.rollback()
        flash(str(e), "error")
        return False
    except IntegrityError:
        db.session.rollback()
        flash("Something changed while you were working. Reload and try again.", "error")
        return False
    if ok:
        flash(ok, "ok")
    return True


def load(est_id):
    est = get_or_404(Estimate, est_id)
    return est, db.session.get(Customer, est.customer_id)


# --- list and create --------------------------------------------------------------------------

@bp.route("/estimates")
@requires_role(*ALL_ROLES)
def index():
    show_all = request.args.get("all") == "1"
    q = select(Estimate, Customer).join(Customer, Customer.id == Estimate.customer_id)
    if not show_all:
        q = q.where(Estimate.status.in_(("draft", "sent", "approved")))
    rows = db.session.execute(q.order_by(Estimate.id.desc()).limit(300)).all()
    groups = {s: [] for s in ESTIMATE_STATUSES}
    for est, customer in rows:
        groups[est.status].append((est, customer))
    return render_template("estimates/index.html", groups=groups, show_all=show_all,
                           label=service.label)


@bp.route("/estimates/new", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def new():
    customer_id = request.args.get("customer", type=int)
    if not customer_id:
        customers = db.session.scalars(select(Customer).where(Customer.active.is_(True))
                                       .order_by(Customer.is_shop.desc(),
                                                 func.lower(Customer.name))).all()
        return render_template("estimates/pick_customer.html", customers=customers)
    customer = get_or_404(Customer, customer_id)
    form = NewEstimateForm(asset_id=request.args.get("asset", type=int))
    form.site_id.choices = site_choices(customer)
    form.asset_id.choices = asset_choices(customer)
    if form.validate_on_submit():
        holder = {}

        def create():
            est = service.create(customer, summary=form.summary.data, site_id=form.site_id.data,
                                 valid_until=form.valid_until.data,
                                 not_to_exceed=form.not_to_exceed.data,
                                 deposit_required=form.deposit_required.data,
                                 notes=form.notes.data)
            service.add_job(est, form.asset_id.data, form.complaint.data)
            holder["est"] = est

        if attempt(create):
            flash(f"Started {service.label(holder['est'])}. Add its lines.", "ok")
            return redirect(url_for("estimates.show", est_id=holder["est"].id))
    flash_errors(form)
    return render_template("estimates/form.html", form=form, customer=customer, est=None)


# --- one estimate ----------------------------------------------------------------------------

def line_forms(est, jobs):
    job_choices = [(j.id, f"{a.tag} · {a.name}" if a else "Consultation") for j, a in jobs]
    services = db.session.scalars(select(Service).where(Service.active.is_(True))
                                  .order_by(Service.kind, Service.code)).all()
    parts = db.session.scalars(select(Part).where(Part.active.is_(True))
                               .order_by(func.lower(Part.sku))).all()
    templates = db.session.scalars(select(JobTemplate).where(JobTemplate.active.is_(True))
                                   .order_by(JobTemplate.name)).all()
    forms = {"service": ServiceLineForm(), "part": PartLineForm(), "other": CustomLineForm(),
             "template": TemplateForm()}
    for form in forms.values():
        form.estimate_job_id.choices = job_choices
    forms["service"].service_id.choices = [(s.id, f"{s.code} · {s.name}") for s in services]
    forms["part"].part_id.choices = [(p.id, f"{p.sku} · {p.name}") for p in parts]
    forms["template"].template_id.choices = [(t.id, t.name) for t in templates]
    if current_user.role not in COST_ROLES:
        del forms["other"].unit_cost
    return forms


@bp.route("/estimates/<int:est_id>")
@requires_role(*ALL_ROLES)
def show(est_id):
    est, customer = load(est_id)
    ctx = docs.context(est, customer)
    editable = service.editable(est) and current_user.role in EDIT_ROLES
    links = db.session.scalars(select(DocLink).where(DocLink.estimate_id == est.id)
                               .order_by(DocLink.id.desc())).all()
    revisions = db.session.scalars(select(Estimate).where(Estimate.number == est.number)
                                   .order_by(Estimate.revision)).all()
    convert = None
    if est.status == "approved" and est.work_order_id is None and current_user.role in EDIT_ROLES:
        convert = ConvertForm(kind="on_site" if est.site_id else "bench", techs=[current_user.id])
        convert.techs.choices = tech_choices()
    add_job = AddJobForm()
    add_job.asset_id.choices = asset_choices(customer, blank="— none (a consultation) —")
    return render_template(
        "estimates/show.html", **ctx, editable=editable, links=links, revisions=revisions,
        latest=service.latest_revision(est), expired=service.is_expired(est),
        costs=current_user.role in COST_ROLES, forms=line_forms(est, ctx["jobs"]) if editable
        else None, add_job=add_job, verbal=VerbalForm(), decline=DeclineForm(),
        convert=convert, now_hash=service.content_hash(est),
        signature_src=url_for("estimates.signature", sha256=est.signature_sha256)
        if est.signature_sha256 else None)


@bp.route("/estimates/<int:est_id>/edit", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def edit(est_id):
    est, customer = load(est_id)
    if not service.editable(est):
        flash(f"{service.label(est)} is {est.status}: make a revision to change it.", "error")
        return redirect(url_for("estimates.show", est_id=est.id))
    form = EstimateForm(obj=est)
    form.site_id.choices = site_choices(customer, est.site_id)
    if form.validate_on_submit():
        def save():
            for name in ("summary", "site_id", "valid_until", "not_to_exceed", "deposit_required",
                         "notes"):
                setattr(est, name, getattr(form, name).data)

        if attempt(save, "Saved."):
            return redirect(url_for("estimates.show", est_id=est.id))
    flash_errors(form)
    return render_template("estimates/form.html", form=form, customer=customer, est=est)


@bp.route("/estimates/<int:est_id>/jobs", methods=["POST"])
@requires_role(*EDIT_ROLES)
def add_job(est_id):
    est, customer = load(est_id)
    form = AddJobForm()
    form.asset_id.choices = asset_choices(customer, blank="— none (a consultation) —")
    if form.validate_on_submit():
        attempt(lambda: service.add_job(est, form.asset_id.data, form.complaint.data), "Job added.")
    else:
        flash_errors(form)
    return redirect(url_for("estimates.show", est_id=est.id) + "#lines")


@bp.route("/estimates/jobs/<int:job_id>/delete", methods=["POST"])
@requires_role(*EDIT_ROLES)
def delete_job(job_id):
    job = get_or_404(EstimateJob, job_id)
    est = db.session.get(Estimate, job.estimate_id)
    if EmptyForm().validate_on_submit():
        attempt(lambda: service.delete_job(est, job), "Job removed.")
    return redirect(url_for("estimates.show", est_id=est.id) + "#lines")


def job_of(est, form):
    job = db.session.get(EstimateJob, form.estimate_job_id.data)
    if job is None or job.estimate_id != est.id:
        abort(404)
    return job


@bp.route("/estimates/<int:est_id>/lines/<kind>", methods=["POST"])
@requires_role(*EDIT_ROLES)
def add_line(est_id, kind):
    est, _ = load(est_id)
    jobs, _ = service.jobs_and_lines(est)
    forms = line_forms(est, jobs)
    if kind not in forms:
        abort(404)
    form = forms[kind]
    if form.validate_on_submit():
        job = job_of(est, form)
        if kind == "service":
            svc = get_or_404(Service, form.service_id.data)
            action = lambda: service.add_service_line(  # noqa: E731
                est, job, svc, qty=form.qty.data, labor_mode=form.labor_mode.data,
                unit_price=form.unit_price.data, description=form.description.data)
        elif kind == "part":
            part = get_or_404(Part, form.part_id.data)
            action = lambda: service.add_part_line(  # noqa: E731
                est, job, part, qty=form.qty.data, unit_price=form.unit_price.data)
        elif kind == "other":
            action = lambda: service.add_custom_line(  # noqa: E731
                est, job, kind=form.kind.data, description=form.description.data,
                qty=form.qty.data, unit_price=form.unit_price.data, taxable=form.taxable.data,
                unit_cost=form.unit_cost.data if "unit_cost" in form else None)
        else:
            template = get_or_404(JobTemplate, form.template_id.data)
            action = lambda: service.apply_template(est, job, template)  # noqa: E731
        attempt(action, "Added.")
    else:
        flash_errors(form)
    return redirect(url_for("estimates.show", est_id=est.id) + "#lines")


@bp.route("/estimates/lines/<int:line_id>/edit", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def line_edit(line_id):
    line = get_or_404(EstimateLine, line_id)
    est = db.session.get(Estimate, line.estimate_id)
    form = LineEditForm(obj=line)
    if line.labor_mode is None:
        form.labor_mode.data = form.labor_mode.data or "flat"
    if current_user.role not in COST_ROLES:
        del form.unit_cost
    if form.validate_on_submit():
        if attempt(lambda: service.edit_line(
                est, line, description=form.description.data, qty=form.qty.data,
                unit_price=form.unit_price.data, taxable=form.taxable.data,
                labor_mode=form.labor_mode.data,
                unit_cost=form.unit_cost.data if "unit_cost" in form else line.unit_cost),
                "Line saved."):
            return redirect(url_for("estimates.show", est_id=est.id) + "#lines")
    flash_errors(form)
    return render_template("estimates/line_form.html", form=form, line=line, est=est,
                           label=service.label(est))


@bp.route("/estimates/lines/<int:line_id>/delete", methods=["POST"])
@requires_role(*EDIT_ROLES)
def line_delete(line_id):
    line = get_or_404(EstimateLine, line_id)
    est = db.session.get(Estimate, line.estimate_id)
    if EmptyForm().validate_on_submit():
        attempt(lambda: service.delete_line(est, line), "Line removed.")
    return redirect(url_for("estimates.show", est_id=est.id) + "#lines")


# --- sending and deciding --------------------------------------------------------------------

@bp.route("/estimates/<int:est_id>/send", methods=["POST"])
@requires_role(*EDIT_ROLES)
def send(est_id):
    est, customer = load(est_id)
    holder = {}
    if EmptyForm().validate_on_submit() and \
            attempt(lambda: holder.update(token=service.new_link(est))):
        url = current_app.config["SHOP_BASE_URL"] + url_for("public.estimate",
                                                             token=holder["token"])
        return render_template("estimates/link.html", est=est, customer=customer,
                               label=service.label(est), url=url)
    return redirect(url_for("estimates.show", est_id=est.id))


@bp.route("/estimates/links/<int:link_id>/revoke", methods=["POST"])
@requires_role(*EDIT_ROLES)
def revoke(link_id):
    link = get_or_404(DocLink, link_id)
    if EmptyForm().validate_on_submit():
        def go():
            if link.revoked_at is None:
                link.revoked_at = utcnow()

        attempt(go, "Link withdrawn: it no longer opens.")
    return redirect(url_for("estimates.show", est_id=link.estimate_id) + "#links")


@bp.route("/estimates/<int:est_id>/sign", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def sign(est_id):
    """The customer signs on our phone."""
    est, customer = load(est_id)
    form = SignForm(seen=service.content_hash(est))
    if form.validate_on_submit():
        if attempt(lambda: service.approve(
                est, name=form.name.data, method="on_screen", signature=form.signature.data,
                ip=client_ip(), ua=request.headers.get("User-Agent"), seen_hash=form.seen.data),
                f"{service.label(est)} approved."):
            return redirect(url_for("estimates.show", est_id=est.id))
    else:
        flash_errors(form)
    ctx = docs.context(est, customer)
    return render_template("estimates/sign.html", form=form, **ctx)


@bp.route("/estimates/<int:est_id>/verbal", methods=["POST"])
@requires_role(*EDIT_ROLES)
def verbal(est_id):
    est, _ = load(est_id)
    form = VerbalForm()
    if form.validate_on_submit():
        note = f"recorded by {current_user.display_name}" + \
            (f": {form.note.data}" if form.note.data else "")
        attempt(lambda: service.approve(est, name=form.name.data, method="verbal", note=note[:300],
                                        seen_hash=request.form.get("seen")),
                f"{service.label(est)} approved (verbal).")
    else:
        flash_errors(form)
    return redirect(url_for("estimates.show", est_id=est.id))


@bp.route("/estimates/<int:est_id>/decline", methods=["POST"])
@requires_role(*EDIT_ROLES)
def decline(est_id):
    est, _ = load(est_id)
    form = DeclineForm()
    if form.validate_on_submit():
        attempt(lambda: service.decline(est, form.reason.data), "Marked declined.")
    return redirect(url_for("estimates.show", est_id=est.id))


@bp.route("/estimates/<int:est_id>/revise", methods=["POST"])
@requires_role(*EDIT_ROLES)
def revise(est_id):
    est, _ = load(est_id)
    holder = {}
    if EmptyForm().validate_on_submit() and \
            attempt(lambda: holder.update(new=service.revise(est))):
        flash(f"Started {service.label(holder['new'])}.", "ok")
        return redirect(url_for("estimates.show", est_id=holder["new"].id))
    return redirect(url_for("estimates.show", est_id=est.id))


@bp.route("/estimates/<int:est_id>/convert", methods=["POST"])
@requires_role(*EDIT_ROLES)
def convert(est_id):
    est, _ = load(est_id)
    form = ConvertForm()
    form.techs.choices = tech_choices()
    holder = {}
    if form.validate_on_submit():
        def go():
            holder["wo"], holder["short"] = service.convert(est, kind=form.kind.data,
                                                           tech_ids=form.techs.data)

        if attempt(go):
            flash(f"Opened {holder['wo'].number} from {service.label(est)}.", "ok")
            for sku, qty in holder["short"]:
                flash(f"Set aside more {sku} than is on hand: order {qty:f}.", "error")
            return redirect(url_for("work.show", wo_id=holder["wo"].id))
    else:
        flash_errors(form)
    return redirect(url_for("estimates.show", est_id=est.id))


@bp.route("/estimates/<int:est_id>/estimate.pdf")
@requires_role(*ALL_ROLES)
def pdf(est_id):
    est, customer = load(est_id)
    return pdf_response(docs.pdf(est, customer), docs.filename(est))


@bp.route("/signatures/<sha256>.png")
@requires_role(*ALL_ROLES)
def signature(sha256):
    if db.session.scalar(select(Estimate.id).where(Estimate.signature_sha256 == sha256)) is None:
        abort(404)
    path = files.path_for(sha256, ".png")
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/png", max_age=0)

