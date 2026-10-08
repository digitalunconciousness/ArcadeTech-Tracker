from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select

from app.assets import service
from app.assets.forms import AssetForm, NewAssetForm, NoteForm, StatusForm, TransferForm
from app.auth.decorators import ALL_ROLES, EDIT_ROLES, requires_role
from app.customers.routes import flash_errors, get_or_404
from app.extensions import db
from app.models import Asset, AssetEvent, Customer, Site

bp = Blueprint("assets", __name__)

FIELDS = ("name", "kind", "site_id", "parent_asset_id", "manufacturer", "model", "model_key",
          "year", "serial", "shop_location", "notes")


def fill_choices(form, customer, asset=None):
    sites = db.session.scalars(select(Site).where(Site.customer_id == customer.id,
                                                  Site.active.is_(True)).order_by(Site.name)).all()
    form.site_id.choices = [(None, "—")] + [(s.id, s.name) for s in sites]
    excluded = set(service.subtree_ids(asset.id)) if asset else set()
    others = db.session.scalars(select(Asset).where(Asset.customer_id == customer.id)
                                .order_by(Asset.tag)).all()
    form.parent_asset_id.choices = [(None, "—")] + [
        (a.id, f"{a.tag} · {a.name}") for a in others if a.id not in excluded]


@bp.route("/assets")
@requires_role(*ALL_ROLES)
def index():
    status = request.args.get("status")
    q = select(Asset, Customer).join(Customer, Customer.id == Asset.customer_id)
    if status:
        q = q.where(Asset.status == status)
    elif request.args.get("all") != "1":
        q = q.where(Asset.status.in_(service.ACTIVE_STATUSES))
    rows = db.session.execute(q.order_by(Asset.tag)).all()
    return render_template("assets/index.html", rows=rows, status=status)


@bp.route("/assets/new", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def new():
    customer_id = request.args.get("customer", type=int)
    if not customer_id:
        customers = db.session.scalars(select(Customer).where(Customer.active.is_(True))
                                       .order_by(Customer.is_shop.desc(),
                                                 func.lower(Customer.name))).all()
        return render_template("assets/pick_customer.html", customers=customers)
    customer = get_or_404(Customer, customer_id)
    form = NewAssetForm()
    fill_choices(form, customer)
    if form.validate_on_submit():
        fields = {f: getattr(form, f).data for f in FIELDS}
        asset = service.create(customer, status=form.status.data, **fields)
        db.session.commit()
        flash(f"Added {asset.tag}.", "ok")
        return redirect(url_for("assets.show", asset_id=asset.id))
    flash_errors(form)
    return render_template("assets/form.html", form=form, customer=customer, asset=None)


@bp.route("/assets/<int:asset_id>")
@requires_role(*ALL_ROLES)
def show(asset_id):
    asset = get_or_404(Asset, asset_id)
    customer = db.session.get(Customer, asset.customer_id)
    site = db.session.get(Site, asset.site_id) if asset.site_id else None
    parent = db.session.get(Asset, asset.parent_asset_id) if asset.parent_asset_id else None
    children = db.session.scalars(select(Asset).where(Asset.parent_asset_id == asset.id)
                                  .order_by(Asset.tag)).all()
    events = db.session.scalars(select(AssetEvent).where(AssetEvent.asset_id == asset.id)
                                .order_by(AssetEvent.at.desc(), AssetEvent.id.desc())).all()
    status_form = StatusForm(status=asset.status)
    return render_template("assets/show.html", asset=asset, customer=customer, site=site,
                           parent=parent, children=children, events=events,
                           status_form=status_form, note_form=NoteForm())


@bp.route("/assets/<int:asset_id>/edit", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def edit(asset_id):
    asset = get_or_404(Asset, asset_id)
    customer = db.session.get(Customer, asset.customer_id)
    form = AssetForm(obj=asset)
    fill_choices(form, customer, asset)
    if form.validate_on_submit():
        for f in FIELDS:
            setattr(asset, f, getattr(form, f).data)
        db.session.commit()
        flash("Saved. The tag stays the same.", "ok")
        return redirect(url_for("assets.show", asset_id=asset.id))
    flash_errors(form)
    return render_template("assets/form.html", form=form, customer=customer, asset=asset)


@bp.route("/assets/<int:asset_id>/status", methods=["POST"])
@requires_role(*EDIT_ROLES)
def status(asset_id):
    asset = get_or_404(Asset, asset_id)
    form = StatusForm()
    if form.validate_on_submit():
        service.set_status(asset, form.status.data, form.note.data)
        db.session.commit()
        flash("Status updated.", "ok")
    else:
        flash_errors(form)
    return redirect(url_for("assets.show", asset_id=asset.id))


@bp.route("/assets/<int:asset_id>/note", methods=["POST"])
@requires_role(*EDIT_ROLES)
def note(asset_id):
    asset = get_or_404(Asset, asset_id)
    form = NoteForm()
    if form.validate_on_submit():
        service.log(asset.id, "note", note=form.note.data)
        db.session.commit()
    else:
        flash_errors(form)
    return redirect(url_for("assets.show", asset_id=asset.id) + "#history")


@bp.route("/assets/<int:asset_id>/transfer", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def transfer(asset_id):
    asset = get_or_404(Asset, asset_id)
    customers = db.session.scalars(select(Customer).where(Customer.active.is_(True))
                                   .order_by(Customer.is_shop.desc(),
                                             func.lower(Customer.name))).all()
    sites = db.session.execute(select(Site, Customer).join(Customer, Customer.id == Site.customer_id)
                               .where(Site.active.is_(True))
                               .order_by(func.lower(Customer.name), Site.name)).all()
    form = TransferForm(customer_id=asset.customer_id, site_id=asset.site_id)
    form.customer_id.choices = [(c.id, c.display_name) for c in customers]
    form.site_id.choices = [(None, "— no site —")] + [
        (s.id, f"{c.display_name} · {s.name}") for s, c in sites]
    if form.validate_on_submit():
        customer = get_or_404(Customer, form.customer_id.data)
        site = db.session.get(Site, form.site_id.data) if form.site_id.data else None
        if site and site.customer_id != customer.id:
            flash("That site belongs to a different customer.", "error")
        elif customer.id == asset.customer_id and (site.id if site else None) == asset.site_id:
            flash("Nothing changed.", "error")
        else:
            moved = service.transfer(asset, customer, site, form.note.data)
            db.session.commit()
            extra = f" (with {len(moved) - 1} part(s) inside it)" if len(moved) > 1 else ""
            flash(f"Moved{extra}.", "ok")
            return redirect(url_for("assets.show", asset_id=asset.id))
    flash_errors(form)
    return render_template("assets/transfer.html", form=form, asset=asset)


# --- tags -------------------------------------------------------------------------------

@bp.route("/g/<tag>")
@requires_role(*ALL_ROLES)
def by_tag(tag):
    """An asset by its tag. Same /g/<slug> shape as the tracker's, for GATBOX later. (No
    QR labels on customer machines: owner's decision, 2026-10-08.)"""
    asset = db.session.scalar(select(Asset).where(Asset.tag == tag.strip().lower())) or abort(404)
    return redirect(url_for("assets.show", asset_id=asset.id))
