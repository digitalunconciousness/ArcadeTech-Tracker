import time
from functools import cache
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from app.actor import set_actor
from app.auth import totp
from app.auth.decorators import ALL_ROLES, requires_role
from app.auth.forms import EmptyForm, LoginForm, PasswordChangeForm, TotpForm
from app.extensions import db, limiter, login_manager
from app.models import User
from app.settings_store import get_settings
from app.timeutil import utcnow

bp = Blueprint("auth", __name__)

LOGIN_LIMIT = "5 per minute;20 per hour"
PENDING_TTL = 300  # seconds between the password step and the code step


@login_manager.user_loader
def load_user(ident):
    uid, _, version = ident.partition(":")
    if not uid.isdigit() or not version.isdigit():
        return None
    user = db.session.get(User, int(uid))
    if user is None or not user.active or user.session_version != int(version):
        return None
    set_actor(user.id)
    return user


@cache
def _dummy_hash():
    return generate_password_hash("timing-equalizer-not-a-real-password", method="scrypt")


def safe_next(target):
    """Only same-site paths; anything else goes to the dashboard."""
    if target:
        parts = urlsplit(target)
        if not parts.scheme and not parts.netloc and target.startswith("/") \
                and not target.startswith(("//", "/\\")):
            return target
    return url_for("dashboard.index")


def complete_login(user):
    session.clear()
    login_user(user)
    session.permanent = True
    set_actor(user.id)
    user.last_login_at = utcnow()
    db.session.commit()


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit(LOGIN_LIMIT, methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip().lower()
        user = db.session.scalar(select(User).where(User.username == username))
        if user is None:
            check_password_hash(_dummy_hash(), form.password.data)
            ok = False
        else:
            ok = user.check_password(form.password.data) and user.active
        if ok:
            target = request.args.get("next")
            if user.totp_enabled:
                session.clear()
                session["pending_login"] = {
                    "uid": user.id, "ver": user.session_version, "at": time.time(), "next": target,
                }
                return redirect(url_for("auth.login_totp"))
            complete_login(user)
            return redirect(safe_next(target))
        flash("Wrong username or password.", "error")
    return render_template("auth/login.html", form=form)


@bp.route("/login/totp", methods=["GET", "POST"])
@limiter.limit(LOGIN_LIMIT, methods=["POST"])
def login_totp():
    pending = session.get("pending_login")
    if not pending or time.time() - pending.get("at", 0) > PENDING_TTL:
        session.pop("pending_login", None)
        flash("Sign in with your password first.", "error")
        return redirect(url_for("auth.login"))
    form = TotpForm()
    if form.validate_on_submit():
        # Row lock: two requests racing with the same code can't both pass.
        user = db.session.get(User, pending["uid"], with_for_update=True)
        if user and user.active and user.totp_enabled and user.session_version == pending["ver"]:
            step = totp.matching_step(user.totp_secret, form.code.data, user.totp_last_step)
            if step is not None:
                user.totp_last_step = step
                complete_login(user)
                return redirect(safe_next(pending.get("next")))
        db.session.rollback()
        flash("That code didn't work. Codes change every 30 seconds and work once.", "error")
    return render_template("auth/login_totp.html", form=form)


@bp.route("/logout", methods=["POST"])
@requires_role(*ALL_ROLES)
def logout():
    if EmptyForm().validate_on_submit():
        logout_user()
        session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account")
@requires_role(*ALL_ROLES)
def account():
    return render_template("auth/account.html", form=PasswordChangeForm(), logout_form=EmptyForm())


@bp.route("/account/password", methods=["POST"])
@requires_role(*ALL_ROLES)
@limiter.limit("5 per minute")
def change_password():
    user = current_user._get_current_object()
    form = PasswordChangeForm()
    if form.validate_on_submit():
        if not user.check_password(form.current_password.data):
            flash("Current password is wrong.", "error")
        else:
            user.set_password(form.new_password.data)
            user.end_sessions()
            db.session.commit()
            login_user(user)  # this session carries the new version; all others end
            flash("Password changed. Other sessions are signed out.", "ok")
            return redirect(url_for("auth.account"))
    for errors in form.errors.values():
        for error in errors:
            flash(error, "error")
    return render_template("auth/account.html", form=form, logout_form=EmptyForm()), 400


@bp.route("/account/2fa", methods=["GET", "POST"])
@requires_role(*ALL_ROLES)
def totp_setup():
    user = current_user._get_current_object()
    if user.totp_enabled:
        return render_template("auth/totp_setup.html", enabled=True)
    if not user.totp_secret:
        # Pending until a code confirms it; totp_enabled_at stays NULL until then.
        user.totp_secret = totp.new_secret()
        db.session.commit()
    form = TotpForm()
    if form.validate_on_submit():
        step = totp.matching_step(user.totp_secret, form.code.data)
        if step is not None:
            user.totp_enabled_at = utcnow()
            user.totp_last_step = step
            db.session.commit()
            flash("Two-factor sign-in is on.", "ok")
            return redirect(url_for("dashboard.index"))
        flash("That code didn't match. Check the phone's clock and try the next code.", "error")
    issuer = get_settings().business_name
    uri = totp.provisioning_uri(user.totp_secret, user.username, issuer)
    return render_template(
        "auth/totp_setup.html", enabled=False, form=form, qr=totp.qr_svg(uri),
        secret=totp.grouped(user.totp_secret), otpauth_uri=uri,
    )
