from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import ALL_ROLES, COST_ROLES, EDIT_ROLES, requires_role
from app.auth.forms import EmptyForm
from app.customers.routes import flash_errors, get_or_404
from app.extensions import db
from app.forms_common import QTY_MAX
from app.models import (
    PO_LINE_STATUSES,
    PO_STATUSES,
    SHIPMENT_DIRECTIONS,
    SHIPMENT_STATUSES,
    Carrier,
    Customer,
    Part,
    PoLine,
    PurchaseOrder,
    Shipment,
    User,
    Vendor,
    WoJob,
    WorkOrder,
)
from app.parts.routes import vendor_choices
from app.parts.stock import StockError
from app.purchasing import service
from app.purchasing.forms import (
    AddToListForm,
    AmountsForm,
    ListLineForm,
    OrderedForm,
    OrderForJobForm,
    PoLineForm,
    ShipmentEditForm,
    ShipmentForm,
    SuggestionForm,
)
from app.purchasing.service import PurchasingError
from app.timeutil import local_today
from app.work.service import WorkError

bp = Blueprint("purchasing", __name__)

# Who places orders and sees what they cost (owner, 2026-10-12). Techs put parts on the
# list for a job, see what's on order and its tracking, and receive boxes.
BUY_ROLES = ("owner",)


@bp.context_processor
def labels():
    return {"po_statuses": PO_STATUSES, "line_statuses": PO_LINE_STATUSES}


@bp.app_context_processor
def shipment_labels():
    """Tracking and parts bought for a job show on work orders too."""
    return {"ship_statuses": SHIPMENT_STATUSES, "directions": SHIPMENT_DIRECTIONS,
            "po_line_statuses": PO_LINE_STATUSES}


def sees_costs():
    return current_user.role in COST_ROLES


def buys():
    return current_user.role in BUY_ROLES


def attempt(action, ok=None):
    try:
        action()
        db.session.commit()
    except (PurchasingError, WorkError, StockError) as e:
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


def part_choices():
    parts = db.session.scalars(select(Part).where(Part.active.is_(True))
                               .order_by(func.lower(Part.sku))).all()
    return [(None, "—")] + [(p.id, f"{p.sku} · {p.name}") for p in parts]


def carrier_choices(blank="Guess from the number"):
    carriers = db.session.scalars(select(Carrier).where(Carrier.active.is_(True))
                                  .order_by(Carrier.position, Carrier.id)).all()
    return [(None, blank)] + [(c.id, c.name) for c in carriers]


def payer_choices():
    owners = db.session.scalars(select(User).where(User.active.is_(True), User.role == "owner")
                                .order_by(User.display_name)).all()
    return [(None, "Business account")] + [(u.id, f"{u.display_name}'s own card")
                                           for u in owners]


def load_po(po_id):
    po = get_or_404(PurchaseOrder, po_id)
    return po, db.session.get(Vendor, po.vendor_id)


def moved_flash(moved):
    for wo in moved:
        flash(f"Nothing left that {wo.number} was waiting on: it's back In progress.", "ok")


# --- the shopping list ------------------------------------------------------------------------

@bp.route("/purchasing")
@requires_role(*ALL_ROLES)
def index():
    lines = service.list_lines()
    groups = {}
    for line, part, vendor, wo in lines:
        groups.setdefault(vendor.name if vendor else None, []).append((line, part, wo))
    add = AddToListForm()
    add.part_id.choices = part_choices()
    add.vendor_id.choices = [(None, "Part's usual vendor")] + vendor_choices()[1:]
    drafts = db.session.execute(
        select(PurchaseOrder, Vendor).join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .where(PurchaseOrder.status == "draft").order_by(PurchaseOrder.id)).all()
    return render_template(
        "purchasing/index.html", suggestions=service.suggestions(), groups=groups,
        drafts=drafts, orders=service.open_orders(), add=add, suggest=SuggestionForm(),
        costs=sees_costs(), buys=buys(), any_vendor=any(k for k in groups))


@bp.route("/purchasing/suggest/<int:part_id>", methods=["POST"])
@requires_role(*EDIT_ROLES)
def suggest(part_id):
    part = get_or_404(Part, part_id)
    form = SuggestionForm()
    if form.validate_on_submit():
        attempt(lambda: service.add_to_list(part, form.qty.data), f"{part.sku} is on the list.")
    else:
        flash_errors(form)
    return redirect(url_for("purchasing.index"))


@bp.route("/purchasing/list", methods=["POST"])
@requires_role(*EDIT_ROLES)
def add_line():
    form = AddToListForm()
    form.part_id.choices = part_choices()
    if form.validate_on_submit():
        part = get_or_404(Part, form.part_id.data)
        attempt(lambda: service.add_to_list(part, form.qty.data, vendor_id=form.vendor_id.data,
                                            note=form.note.data), f"{part.sku} is on the list.")
    else:
        flash_errors(form)
    return redirect(url_for("purchasing.index"))


@bp.route("/purchasing/lines/<int:line_id>/edit", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def line_edit(line_id):
    line = get_or_404(PoLine, line_id)
    part = db.session.get(Part, line.part_id)
    if line.status != "to_order":
        flash("That line has been ordered: change it on its PO.", "error")
        return redirect(url_for("purchasing.order", po_id=line.po_id))
    if line.po_id is not None and not buys():   # a draft PO's lines are the buyer's
        abort(403)
    form = ListLineForm(obj=line)
    form.vendor_id.choices = vendor_choices(keep=line.vendor_id)
    if not sees_costs():
        del form.unit_cost
    if form.validate_on_submit():
        if attempt(lambda: service.edit_list_line(
                line, qty=form.qty.data, vendor_id=form.vendor_id.data, note=form.note.data,
                unit_cost=form.unit_cost.data if "unit_cost" in form else None,
                costs=sees_costs()), "Saved."):
            return redirect(url_for("purchasing.order", po_id=line.po_id) if line.po_id
                            else url_for("purchasing.index"))
    else:
        flash_errors(form)
    return render_template("purchasing/line_form.html", form=form, line=line, part=part)


@bp.route("/purchasing/lines/<int:line_id>/remove", methods=["POST"])
@requires_role(*EDIT_ROLES)
def line_remove(line_id):
    line = get_or_404(PoLine, line_id)
    po_id = line.po_id
    if po_id is not None and not buys():
        abort(403)
    holder = {}
    if EmptyForm().validate_on_submit() and \
            attempt(lambda: holder.update(moved=service.remove_line(line)), "Taken off."):
        moved_flash(holder["moved"])
    return redirect(url_for("purchasing.order", po_id=po_id) if po_id
                    else url_for("purchasing.index"))


@bp.route("/purchasing/lines/<int:line_id>/unlist", methods=["POST"])
@requires_role(*BUY_ROLES)
def line_back_to_list(line_id):
    """Off a draft PO, back onto the shopping list."""
    line = get_or_404(PoLine, line_id)
    po = get_or_404(PurchaseOrder, line.po_id) if line.po_id else abort(404)
    if po.status != "draft":
        flash("Only a draft's lines go back on the list.", "error")
    elif EmptyForm().validate_on_submit():
        line.po_id = None
        db.session.commit()
        flash("Back on the shopping list.", "ok")
    return redirect(url_for("purchasing.order", po_id=po.id))


@bp.route("/purchasing/create-pos", methods=["POST"])
@requires_role(*BUY_ROLES)
def create_pos():
    holder = {}
    if EmptyForm().validate_on_submit() and \
            attempt(lambda: holder.update(pos=service.create_pos())):
        pos = holder["pos"]
        flash(f"{len(pos)} draft PO{'s' if len(pos) != 1 else ''} ready to order.", "ok")
        if len(pos) == 1:
            return redirect(url_for("purchasing.order", po_id=pos[0].id))
    return redirect(url_for("purchasing.index"))


# --- orders -----------------------------------------------------------------------------------

@bp.route("/purchasing/orders")
@requires_role(*ALL_ROLES)
def orders():
    show_all = request.args.get("all") == "1"
    q = select(PurchaseOrder, Vendor).join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
    if not show_all:
        q = q.where(PurchaseOrder.status.in_(("draft", "ordered", "partially_received")))
    rows = db.session.execute(q.order_by(PurchaseOrder.id.desc()).limit(300)).all()
    return render_template("purchasing/orders.html", rows=rows, show_all=show_all,
                           label=service.label)


@bp.route("/purchasing/orders/<int:po_id>")
@requires_role(*ALL_ROLES)
def order(po_id):
    po, vendor = load_po(po_id)
    lines = service.po_lines(po)
    forms = {}
    if buys() and po.status == "draft":
        forms["ordered"] = OrderedForm(ordered_on=local_today())
        forms["ordered"].paid_by_user_id.choices = payer_choices()
    if buys() and po.status != "cancelled":
        forms["amounts"] = AmountsForm(obj=po)
    if current_user.role in EDIT_ROLES and po.status in ("ordered", "partially_received"):
        ship = ShipmentForm()
        ship.carrier_id.choices = carrier_choices()
        forms["ship"] = ship
    users = {u.id: u for u in db.session.scalars(select(User))}
    return render_template(
        "purchasing/order.html", po=po, vendor=vendor, lines=lines, label=service.label(po),
        totals=service.totals(po, lines) if sees_costs() else None, costs=sees_costs(),
        buys=buys(), forms=forms, shipments=service.shipments(po=po), users=users)


@bp.route("/purchasing/orders/<int:po_id>/ordered", methods=["POST"])
@requires_role(*BUY_ROLES)
def ordered(po_id):
    po, _ = load_po(po_id)
    form = OrderedForm()
    form.paid_by_user_id.choices = payer_choices()
    if form.validate_on_submit():
        attempt(lambda: service.mark_ordered(
            po, ordered_on=form.ordered_on.data, vendor_order_ref=form.vendor_order_ref.data,
            paid_by_user_id=form.paid_by_user_id.data, user_id=current_user.id),
            "Ordered.")
    else:
        flash_errors(form)
    return redirect(url_for("purchasing.order", po_id=po.id))


@bp.route("/purchasing/orders/<int:po_id>/lines/<int:line_id>", methods=["GET", "POST"])
@requires_role(*BUY_ROLES)
def po_line_edit(po_id, line_id):
    po, vendor = load_po(po_id)
    line = get_or_404(PoLine, line_id)
    if line.po_id != po.id:
        abort(404)
    form = PoLineForm(obj=line, backordered=line.status == "backordered")
    if form.validate_on_submit():
        if attempt(lambda: service.edit_po_line(
                po, line, qty=form.qty.data, unit_cost=form.unit_cost.data,
                expected_on=form.expected_on.data, backordered=form.backordered.data), "Saved."):
            return redirect(url_for("purchasing.order", po_id=po.id))
    else:
        flash_errors(form)
    return render_template("purchasing/po_line_form.html", form=form, po=po, line=line,
                           part=db.session.get(Part, line.part_id), label=service.label(po))


@bp.route("/purchasing/orders/<int:po_id>/amounts", methods=["POST"])
@requires_role(*BUY_ROLES)
def amounts(po_id):
    po, _ = load_po(po_id)
    form = AmountsForm()
    if form.validate_on_submit():
        attempt(lambda: service.set_amounts(po, shipping=form.shipping.data,
                                            tax_paid=form.tax_paid.data, notes=form.notes.data),
                "Saved.")
    else:
        flash_errors(form)
    return redirect(url_for("purchasing.order", po_id=po.id))


@bp.route("/purchasing/orders/<int:po_id>/receipt", methods=["POST"])
@requires_role(*BUY_ROLES)
def receipt_upload(po_id):
    """The vendor's receipt or order confirmation, a photo or a PDF."""
    from app.books.forms import ReceiptForm
    from app.books.service import BooksError, store_receipt

    po, _ = load_po(po_id)
    form = ReceiptForm()
    if form.validate_on_submit() and form.receipt.data and \
            getattr(form.receipt.data, "filename", ""):
        try:
            po.receipt_sha256, po.receipt_type = store_receipt(form.receipt.data.stream)
            db.session.commit()
            flash("Receipt saved.", "ok")
        except BooksError as e:
            db.session.rollback()
            flash(str(e), "error")
    else:
        flash("Pick a photo or a PDF.", "error")
    return redirect(url_for("purchasing.order", po_id=po.id))


@bp.route("/purchasing/orders/<int:po_id>/<action>", methods=["POST"])
@requires_role(*BUY_ROLES)
def finish(po_id, action):
    po, _ = load_po(po_id)
    if action not in ("cancel", "close"):
        abort(404)
    holder = {}
    if EmptyForm().validate_on_submit():
        if action == "cancel":
            ok = "Draft dropped; its lines are back on the list." if po.status == "draft" \
                else "Order cancelled."
            done = attempt(lambda: holder.update(moved=service.cancel(po)), ok)
        else:
            done = attempt(lambda: holder.update(moved=service.close(po)), "Closed.")
        if done:
            moved_flash(holder["moved"])
    return redirect(url_for("purchasing.order", po_id=po.id))


def parse_qty(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise PurchasingError(f"“{raw}” isn't a quantity.") from None
    if not value.is_finite() or value < 0 or value > QTY_MAX or value.as_tuple().exponent < -3:
        raise PurchasingError(f"“{raw}” isn't a quantity.")
    return value


@bp.route("/purchasing/orders/<int:po_id>/receive", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def receive(po_id):
    """Count the box on a phone: how many came in per line (filled with what's still due),
    and a date code where it matters."""
    po, vendor = load_po(po_id)
    lines = [row for row in service.po_lines(po) if row[0].status in ("ordered", "backordered")]
    form = EmptyForm()
    if request.method == "POST" and form.validate_on_submit():
        holder = {}

        def go():
            entries = []
            for line, *_ in lines:
                qty = parse_qty(request.form.get(f"qty-{line.id}"))
                date_code = (request.form.get(f"dc-{line.id}") or "").strip()[:20] or None
                vendor_lot = (request.form.get(f"lot-{line.id}") or "").strip()[:40] or None
                entries.append((line, qty, date_code, vendor_lot))
            holder["receipts"], holder["moved"] = service.receive(po, entries)

        if attempt(go):
            n = len(holder["receipts"])
            flash(f"Received {n} line{'s' if n != 1 else ''}.", "ok")
            moved_flash(holder["moved"])
            return redirect(url_for("purchasing.order", po_id=po.id))
    return render_template("purchasing/receive.html", po=po, vendor=vendor, lines=lines,
                           label=service.label(po), form=form)


# --- shipments --------------------------------------------------------------------------------

def add_shipment(po=None, wo=None):
    form = ShipmentForm()
    form.carrier_id.choices = carrier_choices()
    if po is not None:
        form.direction.choices = [("inbound_purchase", "Coming from the vendor")]
        form.direction.data = "inbound_purchase"
    if form.validate_on_submit():
        attempt(lambda: service.add_shipment(
            direction=form.direction.data, tracking_number=form.tracking_number.data, po=po,
            wo=wo, carrier_id=form.carrier_id.data, shipped_on=form.shipped_on.data,
            eta=form.eta.data, cost=form.cost.data if sees_costs() else None,
            note=form.note.data), "Tracking added.")
    else:
        flash_errors(form)


@bp.route("/purchasing/orders/<int:po_id>/shipments", methods=["POST"])
@requires_role(*EDIT_ROLES)
def po_shipment(po_id):
    po, _ = load_po(po_id)
    add_shipment(po=po)
    return redirect(url_for("purchasing.order", po_id=po.id) + "#shipments")


@bp.route("/work/<int:wo_id>/shipments", methods=["POST"])
@requires_role(*EDIT_ROLES)
def wo_shipment(wo_id):
    wo = get_or_404(WorkOrder, wo_id)
    add_shipment(wo=wo)
    return redirect(url_for("work.show", wo_id=wo.id) + "#shipments")


@bp.route("/shipments/<int:shipment_id>", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def shipment_edit(shipment_id):
    shipment = get_or_404(Shipment, shipment_id)
    back = url_for("purchasing.order", po_id=shipment.po_id) if shipment.po_id else \
        url_for("work.show", wo_id=shipment.work_order_id)
    form = ShipmentEditForm(obj=shipment)
    form.carrier_id.choices = carrier_choices()
    if form.validate_on_submit():
        cost = form.cost.data if sees_costs() else shipment.cost  # techs don't see it
        if attempt(lambda: service.update_shipment(
                shipment, status=form.status.data, carrier_id=form.carrier_id.data,
                eta=form.eta.data, delivered_on=form.delivered_on.data, cost=cost,
                note=form.note.data), "Saved."):
            return redirect(back + "#shipments")
    else:
        flash_errors(form)
    carrier = db.session.get(Carrier, shipment.carrier_id) if shipment.carrier_id else None
    return render_template("purchasing/shipment_form.html", form=form, shipment=shipment,
                           back=back, url=service.tracking_url(carrier, shipment.tracking_number))


# --- for a job --------------------------------------------------------------------------------

@bp.route("/work/jobs/<int:job_id>/order", methods=["POST"])
@requires_role(*EDIT_ROLES)
def order_for_job(job_id):
    job = get_or_404(WoJob, job_id)
    form = OrderForJobForm()
    form.part_id.choices = part_choices()
    if form.validate_on_submit():
        part = get_or_404(Part, form.part_id.data)
        attempt(lambda: service.order_for_job(job, part, form.qty.data, wait=form.wait.data,
                                              note=form.note.data),
                f"{part.sku} is on the shopping list for this job"
                + (" and set aside for it." if part.track_stock else "."))
    else:
        flash_errors(form)
    return redirect(url_for("work.job", job_id=job.id) + "#parts")


def job_order_form():
    form = OrderForJobForm()
    form.part_id.choices = part_choices()
    return form


def shipment_form():
    form = ShipmentForm()
    form.carrier_id.choices = carrier_choices()
    return form


def search(pat):
    """For global search: POs by number or vendor's order number, and shipments by
    tracking number."""
    pos = db.session.execute(
        select(PurchaseOrder, Vendor).join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .where(or_(PurchaseOrder.number.ilike(pat), PurchaseOrder.vendor_order_ref.ilike(pat)))
        .order_by(PurchaseOrder.id.desc()).limit(25)).all()
    ships = db.session.execute(
        select(Shipment, PurchaseOrder.number, WorkOrder.number, Customer.name)
        .outerjoin(PurchaseOrder, PurchaseOrder.id == Shipment.po_id)
        .outerjoin(WorkOrder, WorkOrder.id == Shipment.work_order_id)
        .outerjoin(Customer, Customer.id == WorkOrder.customer_id)
        .where(Shipment.tracking_number.ilike(pat.replace(" ", "")))
        .order_by(Shipment.id.desc()).limit(25)).all()
    return pos, ships
