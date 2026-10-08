from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_user
from sqlalchemy import select

from app.auth.decorators import requires_role
from app.auth.forms import EmptyForm
from app.extensions import db
from app.models import ShopSetting, User
from app.settings.forms import BusinessForm, SetPasswordForm, UserCreateForm, UserEditForm

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
