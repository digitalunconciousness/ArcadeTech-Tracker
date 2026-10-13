from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_user
from psycopg.errors import ExclusionViolation, UniqueViolation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import requires_role
from app.auth.forms import EmptyForm
from app.billing.forms import JurisdictionForm, TaxSettingsForm
from app.books.forms import BooksSettingsForm, CategoryForm, RateForm
from app.extensions import db
from app.models import (
    Carrier,
    ExpenseCategory,
    MarkupTier,
    MileageRate,
    ShopSetting,
    TaxJurisdiction,
    User,
)
from app.pricing import multiplier_margin
from app.purchasing.forms import CarrierForm
from app.settings.forms import (
    BusinessForm,
    LaborPolicyForm,
    MarkupTierForm,
    SetPasswordForm,
    UserCreateForm,
    UserEditForm,
)

bp = Blueprint("settings", __name__, url_prefix="/settings")


def _flash_errors(form):
    for name, errors in form.errors.items():
        label = getattr(form, name).label.text if hasattr(form, name) else name
        for error in errors:
            flash(f"{label}: {error}", "error")


@bp.route("/", methods=["GET", "POST"])
@requires_role("owner")
def index():
    row = db.session.get(ShopSetting, True)
    if row is None:
        abort(500, "shop_setting row missing; run flask db upgrade")
    form = BusinessForm(obj=row)
    if form.validate_on_submit():
        form.populate_obj(row)
        db.session.commit()
        flash("Settings saved.", "ok")
        return redirect(url_for("settings.index"))
    _flash_errors(form)
    return render_template("settings/index.html", form=form)


@bp.route("/users")
@requires_role("owner")
def users():
    rows = db.session.scalars(select(User).order_by(User.active.desc(), User.username)).all()
    return render_template("settings/users.html", users=rows)


@bp.route("/new-user", methods=["GET", "POST"])
@requires_role("owner")
def user_new():
    form = UserCreateForm(active=True)
    if form.validate_on_submit():
        taken = db.session.scalar(select(User.id).where(User.username == form.username.data))
        if taken:
            form.username.errors.append("Already taken.")
        else:
            user = User(username=form.username.data)
            _apply_edit(form, user)
            user.set_password(form.new_password.data)
            db.session.add(user)
            db.session.commit()
            flash(f"User {user.username} created. They set up 2FA at first sign-in.", "ok")
            return redirect(url_for("settings.users"))
    _flash_errors(form)
    return render_template("settings/user_form.html", form=form, user=None)


def _apply_edit(form, user):
    user.display_name = form.display_name.data
    user.email = form.email.data
    user.role = form.role.data
    user.active = form.active.data
    user.is_partner = form.is_partner.data
    user.split_pct = form.split_pct.data


def _leaves_no_owner(user, new_role, new_active):
    """True if this change would leave no active owner. Locks the owner rows first, so
    two owners demoting each other at the same moment can't both succeed."""
    owners = db.session.scalars(
        select(User).where(User.role == "owner", User.active.is_(True)).with_for_update()
    ).all()
    if user.role != "owner" or not user.active or (new_role == "owner" and new_active):
        return False
    return not any(o.id != user.id for o in owners)


@bp.route("/users/<int:user_id>", methods=["GET", "POST"])
@requires_role("owner")
def user_edit(user_id):
    user = db.session.get(User, user_id) or abort(404)
    form = UserEditForm(obj=user)
    if form.validate_on_submit():
        if _leaves_no_owner(user, form.role.data, form.active.data):
            db.session.rollback()
            flash("That would leave no active owner. Make someone else an owner first.", "error")
        else:
            role_or_access_changed = (user.role, user.active) != (form.role.data, form.active.data)
            _apply_edit(form, user)
            if role_or_access_changed:
                user.end_sessions()
            db.session.commit()
            if user.id == current_user.id and user.active:
                login_user(user)
            flash(f"Saved {user.username}.", "ok")
            return redirect(url_for("settings.users"))
    _flash_errors(form)
    return render_template(
        "settings/user_form.html", form=form, user=user,
        password_form=SetPasswordForm(), reset_form=EmptyForm(),
    )


@bp.route("/users/<int:user_id>/password", methods=["POST"])
@requires_role("owner")
def user_password(user_id):
    user = db.session.get(User, user_id) or abort(404)
    form = SetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.new_password.data)
        user.end_sessions()
        db.session.commit()
        if user.id == current_user.id:
            login_user(user)
        flash(f"Password set for {user.username}; their other sessions are signed out.", "ok")
    else:
        _flash_errors(form)
    return redirect(url_for("settings.user_edit", user_id=user.id))


@bp.route("/users/<int:user_id>/reset-2fa", methods=["POST"])
@requires_role("owner")
def user_reset_2fa(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if EmptyForm().validate_on_submit():
        user.totp_secret = None
        user.totp_enabled_at = None
        user.totp_last_step = None
        user.end_sessions()
        db.session.commit()
        flash(f"2FA reset for {user.username}; they enroll again at next sign-in.", "ok")
    return redirect(url_for("settings.user_edit", user_id=user.id))


# --- pricing: labor policy and markup brackets -----------------------------------------------

def _pricing_page(labor_form=None, tier_form=None, status=200):
    row = db.session.get(ShopSetting, True)
    tiers = db.session.scalars(select(MarkupTier).order_by(MarkupTier.min_cost)).all()
    return render_template(
        "settings/pricing.html", labor_form=labor_form or LaborPolicyForm(obj=row),
        tier_form=tier_form or MarkupTierForm(), tiers=tiers,
        margins={t.id: multiplier_margin(t.multiplier) for t in tiers},
        delete_form=EmptyForm()), status


@bp.route("/pricing", methods=["GET", "POST"])
@requires_role("owner")
def pricing():
    form = LaborPolicyForm()
    if form.validate_on_submit():
        form.populate_obj(db.session.get(ShopSetting, True))
        db.session.commit()
        flash("Labor policy saved.", "ok")
        return redirect(url_for("settings.pricing"))
    if form.is_submitted():
        _flash_errors(form)
        return _pricing_page(labor_form=form, status=422)
    return _pricing_page()


@bp.route("/pricing/tiers", methods=["POST"])
@requires_role("owner")
def tier_add():
    form = MarkupTierForm()
    if form.validate_on_submit():
        db.session.add(MarkupTier(min_cost=form.min_cost.data, max_cost=form.max_cost.data,
                                  multiplier=form.multiplier.data))
        try:
            db.session.commit()
        except IntegrityError as e:
            db.session.rollback()
            if not isinstance(e.orig, (ExclusionViolation, UniqueViolation)):
                raise
            form.min_cost.errors.append("Overlaps another bracket.")
        else:
            flash("Bracket added.", "ok")
            return redirect(url_for("settings.pricing"))
    _flash_errors(form)
    return _pricing_page(tier_form=form, status=422)


@bp.route("/pricing/tiers/<int:tier_id>/delete", methods=["POST"])
@requires_role("owner")
def tier_delete(tier_id):
    tier = db.session.get(MarkupTier, tier_id) or abort(404)
    if EmptyForm().validate_on_submit():
        db.session.delete(tier)
        db.session.commit()
        flash("Bracket removed. Parts it priced have no markup price until another covers them.",
              "ok")
    return redirect(url_for("settings.pricing"))


# --- sales tax: the switch, the shop's jurisdiction, the rates -------------------------------

def _jurisdictions():
    return db.session.scalars(select(TaxJurisdiction).order_by(
        TaxJurisdiction.name, TaxJurisdiction.effective_from.desc())).all()


@bp.route("/tax", methods=["GET", "POST"])
@requires_role("owner")
def tax():
    row = db.session.get(ShopSetting, True)
    rows = _jurisdictions()
    form = TaxSettingsForm(obj=row)
    form.default_tax_jurisdiction_id.choices = [(None, "—")] + [
        (j.id, f"{j.name} (from {j.effective_from})") for j in rows]
    if form.validate_on_submit():
        turned_on = form.collect_sales_tax.data and not row.collect_sales_tax
        form.populate_obj(row)
        db.session.commit()
        flash("Tax settings saved." + (" New invoices now charge sales tax on taxable lines; "
                                       "issued ones don't change." if turned_on else ""), "ok")
        return redirect(url_for("settings.tax"))
    _flash_errors(form)
    return render_template("settings/tax.html", form=form, jurisdictions=rows,
                           new_form=JurisdictionForm())


def _save_jurisdiction(form, j):
    form.populate_obj(j)
    for name in ("state_rate", "county_rate", "city_rate", "other_rate"):
        if getattr(j, name) is None:
            setattr(j, name, 0)
    try:
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        if not isinstance(e.orig, UniqueViolation):
            raise
        form.effective_from.errors.append("That name already has a rate starting that day.")
        return False
    return True


@bp.route("/tax/jurisdictions/new", methods=["POST"])
@requires_role("owner")
def jurisdiction_new():
    form = JurisdictionForm()
    if form.validate_on_submit():
        j = TaxJurisdiction()
        db.session.add(j)
        if _save_jurisdiction(form, j):
            flash(f"Added {j.name}.", "ok")
            return redirect(url_for("settings.tax"))
    _flash_errors(form)
    return redirect(url_for("settings.tax"))


@bp.route("/tax/jurisdictions/<int:jurisdiction_id>", methods=["GET", "POST"])
@requires_role("owner")
def jurisdiction_edit(jurisdiction_id):
    j = db.session.get(TaxJurisdiction, jurisdiction_id) or abort(404)
    form = JurisdictionForm(obj=j)
    if form.validate_on_submit():
        if _save_jurisdiction(form, j):
            flash(f"Saved {j.name}. Invoices already issued keep the rate they were issued at.",
                  "ok")
            return redirect(url_for("settings.tax"))
    _flash_errors(form)
    return render_template("settings/jurisdiction_form.html", form=form, j=j)


# --- carriers -----------------------------------------------------------------------------

def _save_carrier(form, carrier):
    form.populate_obj(carrier)
    try:
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        if not isinstance(e.orig, UniqueViolation):
            raise
        form.code.errors.append("Another carrier has that code.")
        return False
    return True


@bp.route("/carriers", methods=["GET", "POST"])
@requires_role("owner")
def carriers():
    """Each carrier's tracking page, as a template with {number} in it. A carrier moving
    its page is an edit here, not a deploy."""
    form = CarrierForm()
    if form.validate_on_submit():
        carrier = Carrier()
        db.session.add(carrier)
        if _save_carrier(form, carrier):
            flash(f"Added {carrier.name}.", "ok")
            return redirect(url_for("settings.carriers"))
    _flash_errors(form)
    rows = db.session.scalars(select(Carrier).order_by(Carrier.position, Carrier.id)).all()
    return render_template("settings/carriers.html", carriers=rows, form=form)


@bp.route("/carriers/<int:carrier_id>", methods=["GET", "POST"])
@requires_role("owner")
def carrier_edit(carrier_id):
    carrier = db.session.get(Carrier, carrier_id) or abort(404)
    form = CarrierForm(obj=carrier)
    if form.validate_on_submit():
        if _save_carrier(form, carrier):
            flash(f"Saved {carrier.name}.", "ok")
            return redirect(url_for("settings.carriers"))
    _flash_errors(form)
    return render_template("settings/carrier_form.html", form=form, carrier=carrier)


# --- books --------------------------------------------------------------------------------

@bp.route("/books", methods=["GET", "POST"])
@requires_role("owner")
def books():
    row = db.session.get(ShopSetting, True)
    form = BooksSettingsForm(obj=row)
    if form.validate_on_submit():
        form.populate_obj(row)
        db.session.commit()
        flash("Saved.", "ok")
        return redirect(url_for("settings.books"))
    _flash_errors(form)
    return render_template(
        "settings/books.html", form=form, rate_form=RateForm(), category_form=CategoryForm(),
        rates=db.session.scalars(select(MileageRate).order_by(MileageRate.effective_from)).all(),
        categories=db.session.scalars(select(ExpenseCategory).order_by(
            ExpenseCategory.position, ExpenseCategory.name)).all())


def _save_unique(form, obj, field):
    form.populate_obj(obj)
    try:
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        if not isinstance(e.orig, UniqueViolation):
            raise
        getattr(form, field).errors.append("There's one already.")
        return False
    return True


@bp.route("/books/rates", methods=["POST"])
@requires_role("owner")
def rate_new():
    form = RateForm()
    if form.validate_on_submit():
        rate = MileageRate()
        db.session.add(rate)
        if _save_unique(form, rate, "effective_from"):
            flash("Rate added. Trips from that date on use it.", "ok")
    _flash_errors(form)
    return redirect(url_for("settings.books"))


@bp.route("/books/rates/<int:rate_id>", methods=["GET", "POST"])
@requires_role("owner")
def rate_edit(rate_id):
    rate = db.session.get(MileageRate, rate_id) or abort(404)
    form = RateForm(obj=rate)
    if form.validate_on_submit() and _save_unique(form, rate, "effective_from"):
        flash("Saved. Trips on those dates are valued at the new rate.", "ok")
        return redirect(url_for("settings.books"))
    _flash_errors(form)
    return render_template("settings/rate_form.html", form=form, rate=rate)


@bp.route("/books/categories", methods=["POST"])
@requires_role("owner")
def category_new():
    form = CategoryForm()
    if form.validate_on_submit():
        cat = ExpenseCategory()
        db.session.add(cat)
        if _save_unique(form, cat, "name"):
            flash(f"Added {cat.name}.", "ok")
    _flash_errors(form)
    return redirect(url_for("settings.books"))


@bp.route("/books/categories/<int:category_id>", methods=["GET", "POST"])
@requires_role("owner")
def category_edit(category_id):
    cat = db.session.get(ExpenseCategory, category_id) or abort(404)
    form = CategoryForm(obj=cat)
    if form.validate_on_submit() and _save_unique(form, cat, "name"):
        flash("Saved.", "ok")
        return redirect(url_for("settings.books"))
    _flash_errors(form)
    return render_template("settings/category_form.html", form=form, cat=cat)

