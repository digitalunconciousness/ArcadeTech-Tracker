from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select

from app.auth.decorators import ALL_ROLES, EDIT_ROLES, requires_role
from app.customers.forms import CommLogForm, ContactForm, CustomerForm, SiteForm
from app.extensions import db
from app.models import Asset, CommLog, Contact, Customer, Site

bp = Blueprint("customers", __name__)


def flash_errors(form):
    for name, errors in form.errors.items():
        label = getattr(form, name).label.text if hasattr(form, name) else name
        for error in errors:
            flash(f"{label}: {error}", "error")


def get_or_404(model, ident):
    return db.session.get(model, ident) or abort(404)


@bp.route("/customers")
@requires_role(*ALL_ROLES)
def index():
    show_all = request.args.get("all") == "1"
    q = select(Customer, func.count(Asset.id)).outerjoin(Asset, Asset.customer_id == Customer.id) \
        .group_by(Customer.id).order_by(Customer.is_shop.desc(), func.lower(Customer.name))
    if not show_all:
        q = q.where(Customer.active.is_(True))
    return render_template("customers/index.html", rows=db.session.execute(q).all(),
                           show_all=show_all)


@bp.route("/customers/new", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def new():
    form = CustomerForm()
    if form.validate_on_submit():
        customer = Customer()
        form.populate_obj(customer)
        customer.active = True  # the new form has no Active box; unticked would mean False
        db.session.add(customer)
        db.session.commit()
        flash(f"Added {customer.name}.", "ok")
        return redirect(url_for("customers.show", customer_id=customer.id))
    flash_errors(form)
    return render_template("customers/form.html", form=form, customer=None)


@bp.route("/customers/<int:customer_id>")
@requires_role(*ALL_ROLES)
def show(customer_id):
    customer = get_or_404(Customer, customer_id)
    contacts = db.session.scalars(select(Contact).where(Contact.customer_id == customer_id)
                                  .order_by(Contact.active.desc(), Contact.name)).all()
    sites = db.session.scalars(select(Site).where(Site.customer_id == customer_id)
                               .order_by(Site.active.desc(), Site.name)).all()
    assets = db.session.scalars(select(Asset).where(Asset.customer_id == customer_id)
                                .order_by(Asset.tag)).all()
    log = db.session.scalars(select(CommLog).where(CommLog.customer_id == customer_id)
                             .order_by(CommLog.at.desc(), CommLog.id.desc()).limit(50)).all()
    contacts_by_id = {c.id: c for c in contacts}
    return render_template("customers/show.html", customer=customer, contacts=contacts,
                           sites=sites, assets=assets, log=log, contacts_by_id=contacts_by_id,
                           log_form=CommLogForm())


@bp.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def edit(customer_id):
    customer = get_or_404(Customer, customer_id)
    form = CustomerForm(obj=customer)
    if form.validate_on_submit():
        form.populate_obj(customer)
        db.session.commit()
        flash("Saved.", "ok")
        return redirect(url_for("customers.show", customer_id=customer.id))
    flash_errors(form)
    return render_template("customers/form.html", form=form, customer=customer)


@bp.route("/customers/<int:customer_id>/log", methods=["POST"])
@requires_role(*EDIT_ROLES)
def add_log(customer_id):
    customer = get_or_404(Customer, customer_id)
    form = CommLogForm()
    if form.validate_on_submit():
        db.session.add(CommLog(customer_id=customer.id, kind=form.kind.data,
                               summary=form.summary.data))
        db.session.commit()
        flash("Logged.", "ok")
    else:
        flash_errors(form)
    return redirect(url_for("customers.show", customer_id=customer.id) + "#log")


# --- contacts -------------------------------------------------------------------------

@bp.route("/customers/<int:customer_id>/contacts/new", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def contact_new(customer_id):
    customer = get_or_404(Customer, customer_id)
    form = ContactForm()
    if form.validate_on_submit():
        contact = Contact(customer_id=customer.id)
        form.populate_obj(contact)
        contact.active = True
        db.session.add(contact)
        db.session.commit()
        flash(f"Added {contact.name}.", "ok")
        return redirect(url_for("customers.show", customer_id=customer.id))
    flash_errors(form)
    return render_template("customers/contact_form.html", form=form, customer=customer,
                           contact=None)


@bp.route("/contacts/<int:contact_id>/edit", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def contact_edit(contact_id):
    contact = get_or_404(Contact, contact_id)
    customer = db.session.get(Customer, contact.customer_id)
    form = ContactForm(obj=contact)
    if form.validate_on_submit():
        form.populate_obj(contact)
        db.session.commit()
        flash("Saved.", "ok")
        return redirect(url_for("customers.show", customer_id=customer.id))
    flash_errors(form)
    return render_template("customers/contact_form.html", form=form, customer=customer,
                           contact=contact)


# --- sites ----------------------------------------------------------------------------

def site_form(customer, site=None):
    form = SiteForm(obj=site)
    contacts = db.session.scalars(select(Contact).where(Contact.customer_id == customer.id,
                                                        Contact.active.is_(True))
                                  .order_by(Contact.name)).all()
    # Only this customer's contacts; the database enforces it too (composite FK).
    form.site_contact_id.choices = [(None, "—")] + [(c.id, c.name) for c in contacts]
    return form


@bp.route("/customers/<int:customer_id>/sites/new", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def site_new(customer_id):
    customer = get_or_404(Customer, customer_id)
    form = site_form(customer)
    if form.validate_on_submit():
        site = Site(customer_id=customer.id)
        form.populate_obj(site)
        site.active = True
        db.session.add(site)
        db.session.commit()
        flash(f"Added {site.name}.", "ok")
        return redirect(url_for("customers.show", customer_id=customer.id))
    flash_errors(form)
    return render_template("customers/site_form.html", form=form, customer=customer, site=None)


@bp.route("/sites/<int:site_id>/edit", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def site_edit(site_id):
    site = get_or_404(Site, site_id)
    customer = db.session.get(Customer, site.customer_id)
    form = site_form(customer, site)
    if form.validate_on_submit():
        form.populate_obj(site)
        db.session.commit()
        flash("Saved.", "ok")
        return redirect(url_for("customers.show", customer_id=customer.id))
    flash_errors(form)
    return render_template("customers/site_form.html", form=form, customer=customer, site=site)
