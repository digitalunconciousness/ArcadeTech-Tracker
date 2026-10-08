from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy import func, select

from app.auth.decorators import ALL_ROLES, COST_ROLES, EDIT_ROLES, requires_role
from app.auth.forms import EmptyForm
from app.customers.routes import flash_errors, get_or_404
from app.extensions import db
from app.models import JobTemplate, JobTemplateLine, Part, Service
from app.models.pricebook import SERVICE_KINDS
from app.pricebook.forms import KIND_LABELS, ServiceForm, TemplateForm, TemplateLineForm
from app.pricing import load_tiers, margin_pct, part_price, round_money

bp = Blueprint("pricebook", __name__, url_prefix="/pricebook")


# --- services -----------------------------------------------------------------------------

@bp.route("/")
@requires_role(*ALL_ROLES)
def index():
    services = db.session.scalars(select(Service).order_by(
        Service.active.desc(), Service.kind, Service.code)).all()
    return render_template("pricebook/index.html", services=services, kinds=KIND_LABELS,
                           kind_order=SERVICE_KINDS)


def code_taken(code, service_id=None):
    q = select(Service.id).where(Service.code == code)
    if service_id:
        q = q.where(Service.id != service_id)
    return db.session.scalar(q) is not None


@bp.route("/services/new", methods=["GET", "POST"])
@requires_role("owner")
def service_new():
    form = ServiceForm()
    if form.validate_on_submit():
        if code_taken(form.code.data):
            form.code.errors.append("Already used.")
        else:
            service = Service()
            form.populate_obj(service)
            db.session.add(service)
            db.session.commit()
            flash(f"Added {service.code}.", "ok")
            return redirect(url_for("pricebook.index"))
    flash_errors(form)
    return render_template("pricebook/service_form.html", form=form, service=None)


@bp.route("/services/<int:service_id>", methods=["GET", "POST"])
@requires_role("owner")
def service_edit(service_id):
    service = get_or_404(Service, service_id)
    form = ServiceForm(obj=service)
    if form.validate_on_submit():
        if code_taken(form.code.data, service.id):
            form.code.errors.append("Already used.")
        else:
            form.populate_obj(service)
            db.session.commit()
            flash(f"Saved {service.code}.", "ok")
            return redirect(url_for("pricebook.index"))
    flash_errors(form)
    return render_template("pricebook/service_form.html", form=form, service=service)


# --- job templates ------------------------------------------------------------------------

@bp.route("/templates")
@requires_role(*ALL_ROLES)
def templates():
    rows = db.session.execute(
        select(JobTemplate, func.count(JobTemplateLine.id))
        .outerjoin(JobTemplateLine, JobTemplateLine.template_id == JobTemplate.id)
        .group_by(JobTemplate.id).order_by(JobTemplate.active.desc(), func.lower(JobTemplate.name))
    ).all()
    return render_template("pricebook/templates.html", rows=rows)


@bp.route("/templates/new", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def template_new():
    form = TemplateForm()
    if form.validate_on_submit():
        if db.session.scalar(select(JobTemplate.id).where(JobTemplate.name == form.name.data)):
            form.name.errors.append("Already used.")
        else:
            tpl = JobTemplate()
            form.populate_obj(tpl)
            tpl.active = True
            db.session.add(tpl)
            db.session.commit()
            flash("Added. Now add its services and parts.", "ok")
            return redirect(url_for("pricebook.template_show", template_id=tpl.id))
    flash_errors(form)
    return render_template("pricebook/template_form.html", form=form, tpl=None)


@bp.route("/templates/<int:template_id>/edit", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def template_edit(template_id):
    tpl = get_or_404(JobTemplate, template_id)
    form = TemplateForm(obj=tpl)
    if form.validate_on_submit():
        if db.session.scalar(select(JobTemplate.id).where(JobTemplate.name == form.name.data,
                                                          JobTemplate.id != tpl.id)):
            form.name.errors.append("Already used.")
        else:
            form.populate_obj(tpl)
            db.session.commit()
            flash("Saved.", "ok")
            return redirect(url_for("pricebook.template_show", template_id=tpl.id))
    flash_errors(form)
    return render_template("pricebook/template_form.html", form=form, tpl=tpl)


def item_choices():
    """One select for both: services and parts as option groups."""
    services = db.session.scalars(select(Service).where(Service.active.is_(True))
                                  .order_by(Service.code)).all()
    parts = db.session.scalars(select(Part).where(Part.active.is_(True))
                               .order_by(func.lower(Part.sku))).all()
    return {
        "Services": [(f"s-{s.id}", f"{s.code} · {s.name}") for s in services],
        "Parts": [(f"p-{p.id}", f"{p.sku} · {p.name}") for p in parts],
    }


def template_lines(tpl):
    """[(line, service, part, unit_price, unit_cost)] at today's book prices."""
    rows = db.session.execute(
        select(JobTemplateLine, Service, Part)
        .outerjoin(Service, Service.id == JobTemplateLine.service_id)
        .outerjoin(Part, Part.id == JobTemplateLine.part_id)
        .where(JobTemplateLine.template_id == tpl.id).order_by(JobTemplateLine.id)).all()
    tiers = load_tiers()
    out = []
    for line, service, part in rows:
        if service:
            out.append((line, service, None, service.rate, None))
        else:
            out.append((line, None, part, part_price(part, tiers), part.default_cost))
    return out


@bp.route("/templates/<int:template_id>")
@requires_role(*ALL_ROLES)
def template_show(template_id):
    tpl = get_or_404(JobTemplate, template_id)
    lines = template_lines(tpl)
    unpriced = any(price is None for *_, price, _cost in lines)
    total = round_money(sum((line.qty * price for line, _s, _p, price, _c in lines
                             if price is not None), Decimal(0)))
    hours = sum((line.qty for line, s, *_ in lines if s and s.unit == "hour"), Decimal(0))
    costs = None
    if current_user.role in COST_ROLES:
        parts_cost = sum((line.qty * cost for line, _s, p, _pr, cost in lines
                          if p and cost is not None), Decimal(0))
        parts_price = sum((line.qty * price for line, _s, p, price, _c in lines
                           if p and price is not None), Decimal(0))
        costs = {"parts": parts_cost, "margin": margin_pct(parts_price, parts_cost),
                 "missing": any(p and cost is None for _l, _s, p, _pr, cost in lines)}
    form = None
    if current_user.role in EDIT_ROLES:
        form = TemplateLineForm()
        form.item.choices = item_choices()
    return render_template("pricebook/template_show.html", tpl=tpl, lines=lines, total=total,
                           unpriced=unpriced, hours=hours, costs=costs, form=form,
                           delete_form=EmptyForm())


@bp.route("/templates/<int:template_id>/lines", methods=["POST"])
@requires_role(*EDIT_ROLES)
def template_line_add(template_id):
    tpl = get_or_404(JobTemplate, template_id)
    form = TemplateLineForm()
    form.item.choices = item_choices()
    if form.validate_on_submit():
        kind, _, ident = form.item.data.partition("-")
        line = JobTemplateLine(template_id=tpl.id, qty=form.qty.data, note=form.note.data,
                               service_id=int(ident) if kind == "s" else None,
                               part_id=int(ident) if kind == "p" else None)
        db.session.add(line)
        db.session.commit()
    else:
        flash_errors(form)
    return redirect(url_for("pricebook.template_show", template_id=tpl.id))


@bp.route("/templates/<int:template_id>/lines/<int:line_id>/delete", methods=["POST"])
@requires_role(*EDIT_ROLES)
def template_line_delete(template_id, line_id):
    line = get_or_404(JobTemplateLine, line_id)
    if line.template_id != template_id:
        abort(404)
    if EmptyForm().validate_on_submit():
        db.session.delete(line)
        db.session.commit()
    return redirect(url_for("pricebook.template_show", template_id=template_id))

