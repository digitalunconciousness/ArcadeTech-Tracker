from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import ALL_ROLES, COST_ROLES, EDIT_ROLES, requires_role
from app.customers.routes import flash_errors, get_or_404
from app.extensions import db
from app.formatting import qty as fmt_qty
from app.formatting import unit_cost as fmt_cost
from app.forms_common import QTY_MAX
from app.models import Part, StockLot, StockMove, User, Vendor
from app.parts import stock
from app.parts.forms import (
    PRICING_FIELDS,
    AdjustForm,
    CountForm,
    PartForm,
    ReceiveForm,
    ScrapForm,
    VendorForm,
)
from app.pricing import load_tiers, margin_pct, part_price, tier_for
from app.search.routes import like
from app.timeutil import localdt

bp = Blueprint("parts", __name__)

STOCK_CHANGED = "Stock changed while you were working. Reload and try again."
LEDGER_ROWS = 100


def sees_costs():
    return current_user.role in COST_ROLES


def vendor_choices(keep=None):
    """Active vendors, plus `keep` (the one already chosen) even if it's inactive."""
    vendors = db.session.scalars(
        select(Vendor).where(or_(Vendor.active.is_(True), Vendor.id == keep))
        .order_by(func.lower(Vendor.name))).all()
    return [(None, "—")] + [(v.id, v.name) for v in vendors]


def lot_label(lot, remaining):
    bits = [f"#{lot.id}", localdt(lot.received_at, "%Y-%m-%d")]
    if lot.date_code:
        bits.append(f"dc {lot.date_code}")
    if sees_costs():
        bits.append(fmt_cost(lot.unit_cost))
    bits.append(f"{fmt_qty(remaining)} left")
    return " · ".join(bits)


def lot_choices(lots, first):
    return [(None, first)] + [(lot.id, lot_label(lot, rem)) for lot, rem in lots]


def write_stock(action, ok):
    """Run a ledger write and commit it; a refused write rolls back with a message."""
    try:
        action()
        db.session.commit()
    except stock.StockError as e:
        db.session.rollback()
        flash(str(e), "error")
        return False
    except IntegrityError:  # stock_move_lot_guard: a concurrent move got there first
        db.session.rollback()
        flash(STOCK_CHANGED, "error")
        return False
    flash(ok, "ok")
    return True


# --- parts --------------------------------------------------------------------------------

@bp.route("/parts")
@requires_role(*ALL_ROLES)
def index():
    q = (request.args.get("q") or "").strip()[:100]
    low = request.args.get("low") == "1"
    show_all = request.args.get("all") == "1"
    query = select(Part)
    if not show_all:
        query = query.where(Part.active.is_(True))
    if q:
        pat = like(q)
        query = query.where(or_(
            Part.sku.ilike(pat), Part.name.ilike(pat), Part.mpn.ilike(pat),
            Part.category.ilike(pat), Part.bin.ilike(pat),
            func.array_to_string(Part.equivalents, " ").ilike(pat)))
    parts = db.session.scalars(query.order_by(func.lower(Part.sku))).all()
    counts = stock.on_hand_map(p.id for p in parts)
    tiers = load_tiers()
    rows = []
    for p in parts:
        on_hand = counts.get(p.id, Decimal(0))
        is_low = p.track_stock and p.reorder_point is not None and on_hand <= p.reorder_point
        if low and not is_low:
            continue
        rows.append((p, on_hand, is_low, part_price(p, tiers)))
    return render_template("parts/index.html", rows=rows, q=q, low=low, show_all=show_all)


def part_form(part=None):
    form = PartForm(obj=part)
    form.preferred_vendor_id.choices = vendor_choices(part.preferred_vendor_id if part else None)
    if current_user.role != "owner":
        for name in PRICING_FIELDS:
            delattr(form, name)
    return form


def sku_taken(sku, part_id=None):
    q = select(Part.id).where(func.lower(Part.sku) == sku.lower())
    if part_id:
        q = q.where(Part.id != part_id)
    return db.session.scalar(q) is not None


def save_part(form, part):
    if sku_taken(form.sku.data, part.id):
        form.sku.errors.append("Another part has that SKU.")
        return False
    form.populate_obj(part)
    if part.price_mode == "markup" and part.default_cost is None and "default_cost" in form:
        flash("Priced by markup but no default cost yet: it has no price until it gets one.",
              "error")
    return True


@bp.route("/parts/new", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def new():
    form = part_form()
    if form.validate_on_submit():
        part = Part()
        if save_part(form, part):
            part.active = True  # the new form has no Active box; unticked would mean False
            db.session.add(part)
            db.session.commit()
            flash(f"Added {part.sku}.", "ok")
            return redirect(url_for("parts.show", part_id=part.id))
    flash_errors(form)
    return render_template("parts/form.html", form=form, part=None)


@bp.route("/parts/<int:part_id>/edit", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def edit(part_id):
    part = get_or_404(Part, part_id)
    form = part_form(part)
    if form.validate_on_submit() and save_part(form, part):
        db.session.commit()
        flash("Saved.", "ok")
        return redirect(url_for("parts.show", part_id=part.id))
    flash_errors(form)
    return render_template("parts/form.html", form=form, part=part)


@bp.route("/parts/<int:part_id>")
@requires_role(*ALL_ROLES)
def show(part_id):
    part = get_or_404(Part, part_id)
    lots = stock.lots(part.id)
    on_hand = stock.on_hand(part.id)
    moves = db.session.execute(
        select(StockMove, User.username).outerjoin(User, User.id == StockMove.created_by)
        .where(StockMove.part_id == part.id)
        .order_by(StockMove.at.desc(), StockMove.id.desc()).limit(LEDGER_ROWS)).all()
    tiers = load_tiers()
    price = part_price(part, tiers)
    costs = None
    if sees_costs():
        next_cost = stock.fifo_cost(part.id)
        costs = {
            "next": next_cost,
            "margin_next": margin_pct(price, next_cost),
            "margin_default": margin_pct(price, part.default_cost),
            "tier": (tier_for(part.default_cost, tiers)
                     if part.price_mode == "markup" and part.default_cost is not None else None),
            "value": sum((rem * lot.unit_cost for lot, rem in lots if rem > 0), Decimal(0)),
        }
    vendor = db.session.get(Vendor, part.preferred_vendor_id) if part.preferred_vendor_id else None
    vendors = {v.id: v for v in db.session.scalars(
        select(Vendor).where(Vendor.id.in_([lot.vendor_id for lot, _ in lots if lot.vendor_id])))}
    forms = {}
    if current_user.role in EDIT_ROLES and part.track_stock:
        forms["receive"] = ReceiveForm(unit_cost=part.default_cost, vendor_id=part.preferred_vendor_id)
        forms["receive"].vendor_id.choices = vendor_choices(part.preferred_vendor_id)
        if not sees_costs():
            del forms["receive"].unit_cost
        forms["adjust"] = AdjustForm()
        forms["adjust"].lot_id.choices = lot_choices(lots, "Oldest first / newest lot")
        forms["scrap"] = ScrapForm()
        forms["scrap"].lot_id.choices = lot_choices([x for x in lots if x[1] > 0], "Oldest first")
    return render_template("parts/show.html", part=part, lots=lots, on_hand=on_hand,
                           moves=moves, price=price, costs=costs, vendor=vendor,
                           vendors=vendors, forms=forms)


@bp.route("/parts/<int:part_id>/receive", methods=["POST"])
@requires_role(*EDIT_ROLES)
def receive(part_id):
    part = get_or_404(Part, part_id)
    form = ReceiveForm()
    form.vendor_id.choices = vendor_choices(part.preferred_vendor_id)
    if not sees_costs():
        del form.unit_cost  # a tech's receipt is costed at the part's default cost
    if form.validate_on_submit():
        cost = form.unit_cost.data if "unit_cost" in form else part.default_cost
        if cost is None:
            flash("Unit cost needed." if sees_costs() else
                  f"{part.sku} has no default cost yet; an owner receives it.", "error")
        else:
            write_stock(lambda: stock.receive(
                part, form.qty.data, cost, vendor_id=form.vendor_id.data,
                date_code=form.date_code.data, vendor_lot=form.vendor_lot.data,
                note=form.note.data), f"Received {fmt_qty(form.qty.data)} {part.unit}.")
    else:
        flash_errors(form)
    return redirect(url_for("parts.show", part_id=part.id))


@bp.route("/parts/<int:part_id>/adjust", methods=["POST"])
@requires_role(*EDIT_ROLES)
def adjust(part_id):
    part = get_or_404(Part, part_id)
    form = AdjustForm()
    form.lot_id.choices = lot_choices(stock.lots(part.id), "")
    if form.validate_on_submit():
        write_stock(lambda: stock.adjust(part, form.delta, "adjust", form.note.data,
                                         form.lot_id.data), "Adjusted.")
    else:
        flash_errors(form)
    return redirect(url_for("parts.show", part_id=part.id))


@bp.route("/parts/<int:part_id>/scrap", methods=["POST"])
@requires_role(*EDIT_ROLES)
def scrap(part_id):
    part = get_or_404(Part, part_id)
    form = ScrapForm()
    form.lot_id.choices = lot_choices(stock.lots(part.id, open_only=True), "")
    if form.validate_on_submit():
        write_stock(lambda: stock.take(part, form.qty.data, "scrap", form.note.data,
                                       form.lot_id.data), f"Scrapped {fmt_qty(form.qty.data)}.")
    else:
        flash_errors(form)
    return redirect(url_for("parts.show", part_id=part.id))


def parse_count(raw):
    """A counted quantity from the sheet, or None if it isn't one."""
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0 or value > QTY_MAX or value.as_tuple().exponent < -3:
        return None
    return value


@bp.route("/parts/count", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def count():
    """A count sheet: what's on hand per part, a box for what you counted. Blank boxes
    aren't counted. A part whose stock moved after the sheet was loaded is skipped, so
    a count never overwrites a move it didn't see."""
    bin_prefix = (request.values.get("bin") or "").strip()[:40]
    query = select(Part).where(Part.active.is_(True), Part.track_stock.is_(True))
    if bin_prefix:
        escaped = bin_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(Part.bin.ilike(escaped + "%"))
    parts = db.session.scalars(query.order_by(Part.bin.nulls_last(), func.lower(Part.sku))).all()
    form = CountForm()
    if form.validate_on_submit():
        note = (request.form.get("note") or "").strip()[:200] or None
        now = stock.on_hand_map(p.id for p in parts)
        errors, skipped, counted, changed = [], [], 0, 0
        try:
            for p in parts:
                raw = (request.form.get(f"count-{p.id}") or "").strip()
                if not raw:
                    continue
                value = parse_count(raw)
                if value is None:
                    errors.append(f"{p.sku}: “{raw}” isn't a count (0 or more, up to 3 decimals).")
                    continue
                if parse_count(request.form.get(f"seen-{p.id}") or "") != now.get(p.id, 0):
                    skipped.append(p.sku)
                    continue
                counted += 1
                changed += stock.count(p, value, note) != 0
            if errors:
                raise stock.StockError("\n".join(errors))
            db.session.commit()
        except stock.StockError as e:
            db.session.rollback()
            for line in str(e).splitlines():
                flash(line, "error")
            flash("Nothing was saved; fix those and save the sheet again.", "error")
            return render_template("parts/count.html", parts=parts, on_hand=now, form=form,
                                   bin_prefix=bin_prefix), 422
        except IntegrityError:
            db.session.rollback()
            flash(STOCK_CHANGED, "error")
            return redirect(url_for("parts.count", bin=bin_prefix or None))
        flash(f"Counted {counted} part{'s' if counted != 1 else ''}; {changed} changed.", "ok")
        if skipped:
            flash("Stock moved after you loaded the sheet, so these weren't counted: "
                  + ", ".join(skipped) + ". Count them again.", "error")
        return redirect(url_for("parts.count", bin=bin_prefix or None))
    return render_template("parts/count.html", parts=parts, form=form, bin_prefix=bin_prefix,
                           on_hand=stock.on_hand_map(p.id for p in parts))


# --- vendors ------------------------------------------------------------------------------

@bp.route("/vendors")
@requires_role(*ALL_ROLES)
def vendors():
    show_all = request.args.get("all") == "1"
    q = select(Vendor).order_by(Vendor.active.desc(), func.lower(Vendor.name))
    if not show_all:
        q = q.where(Vendor.active.is_(True))
    return render_template("parts/vendors.html", vendors=db.session.scalars(q).all(),
                           show_all=show_all)


@bp.route("/vendors/new", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def vendor_new():
    form = VendorForm()
    if form.validate_on_submit():
        vendor = Vendor()
        form.populate_obj(vendor)
        vendor.active = True
        db.session.add(vendor)
        db.session.commit()
        flash(f"Added {vendor.name}.", "ok")
        return redirect(url_for("parts.vendor_show", vendor_id=vendor.id))
    flash_errors(form)
    return render_template("parts/vendor_form.html", form=form, vendor=None)


@bp.route("/vendors/<int:vendor_id>")
@requires_role(*ALL_ROLES)
def vendor_show(vendor_id):
    vendor = get_or_404(Vendor, vendor_id)
    parts = db.session.scalars(select(Part).where(Part.preferred_vendor_id == vendor.id)
                               .order_by(func.lower(Part.sku))).all()
    lots = db.session.execute(
        select(StockLot, Part).join(Part, Part.id == StockLot.part_id)
        .where(StockLot.vendor_id == vendor.id)
        .order_by(StockLot.received_at.desc(), StockLot.id.desc()).limit(50)).all()
    return render_template("parts/vendor_show.html", vendor=vendor, parts=parts, lots=lots)


@bp.route("/vendors/<int:vendor_id>/edit", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def vendor_edit(vendor_id):
    vendor = get_or_404(Vendor, vendor_id)
    form = VendorForm(obj=vendor)
    if form.validate_on_submit():
        form.populate_obj(vendor)
        db.session.commit()
        flash("Saved.", "ok")
        return redirect(url_for("parts.vendor_show", vendor_id=vendor.id))
    flash_errors(form)
    return render_template("parts/vendor_form.html", form=form, vendor=vendor)
