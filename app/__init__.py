"""ArcadeTech Tracker (shop-hub): create_app() builds the Flask app."""

from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for
from flask_login import current_user

from app import actor  # noqa: F401  (registers the after_begin listener)
from app.config import load_config
from app.extensions import csrf, db, limiter, login_manager, migrate

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Reachable without signing in. Everything else needs a login (and, for an owner,
# 2FA); every view outside this set must carry @requires_role (a test checks).
PUBLIC_ENDPOINTS = frozenset({"auth.login", "auth.login_totp", "health.healthz", "static"})
# Reachable by an owner who hasn't enrolled 2FA yet.
ENROLLMENT_ENDPOINTS = frozenset({"auth.totp_setup", "auth.logout", "static"})

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'self'"
)


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.update(load_config())
    if overrides:
        app.config.update(overrides)

    db.init_app(app)
    migrate.init_app(app, db, directory=str(MIGRATIONS_DIR), compare_type=True)
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = None

    from app import models  # noqa: F401
    from app.assets.routes import bp as assets_bp
    from app.auth.routes import bp as auth_bp
    from app.customers.routes import bp as customers_bp
    from app.dashboard.routes import bp as dashboard_bp
    from app.health.routes import bp as health_bp
    from app.parts.routes import bp as parts_bp
    from app.pricebook.routes import bp as pricebook_bp
    from app.search.routes import bp as search_bp
    from app.settings.routes import bp as settings_bp
    from app.work.routes import bp as work_bp

    for bp in (auth_bp, dashboard_bp, health_bp, settings_bp, customers_bp, assets_bp, search_bp,
               pricebook_bp, parts_bp, work_bp):
        app.register_blueprint(bp)

    _register_hooks(app)
    return app


def _register_hooks(app):
    from app.formatting import FILTERS
    from app.models.settings import DEFAULT_BUSINESS_NAME, ShopSetting
    from app.settings_store import get_settings
    from app.timeutil import localdt

    app.add_template_filter(localdt, "localdt")
    for name, fn in FILTERS.items():
        app.add_template_filter(fn, name)

    @app.context_processor
    def inject_roles():
        from app.auth.decorators import COST_ROLES, EDIT_ROLES

        role = current_user.role if current_user.is_authenticated else None
        return {"can_edit": role in EDIT_ROLES, "can_see_costs": role in COST_ROLES,
                "is_owner": role == "owner"}

    @app.context_processor
    def inject_timer():
        """The signed-in user's running timer, for the bar at the top of every page."""
        if not current_user.is_authenticated or request.endpoint == "static":
            return {}
        from sqlalchemy import select

        from app.models import TimeEntry, WoJob, WorkOrder

        try:
            row = db.session.execute(
                select(TimeEntry, WorkOrder.number)
                .join(WoJob, WoJob.id == TimeEntry.wo_job_id)
                .join(WorkOrder, WorkOrder.id == WoJob.work_order_id)
                .where(TimeEntry.user_id == current_user.id, TimeEntry.ended_at.is_(None))
            ).first()
        except Exception:  # an error page must render even with the database down
            db.session.rollback()
            return {}
        return {"running_timer": row}

    @app.context_processor
    def inject_shop():
        try:
            return {"shop": get_settings()}
        except Exception:  # an error page must render even with the database down
            db.session.rollback()
            return {"shop": ShopSetting(business_name=DEFAULT_BUSINESS_NAME)}

    @app.before_request
    def gate():
        endpoint = request.endpoint
        if endpoint in PUBLIC_ENDPOINTS:
            return None
        if not current_user.is_authenticated:
            if endpoint is None:
                return redirect(url_for("auth.login"))
            return login_manager.unauthorized()
        if current_user.role == "owner" and not current_user.totp_enabled \
                and endpoint not in ENROLLMENT_ENDPOINTS:
            return redirect(url_for("auth.totp_setup"))
        return None

    @app.after_request
    def security_headers(response):
        h = response.headers
        h.setdefault("Content-Security-Policy", CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        h.setdefault("X-Robots-Tag", "noindex, nofollow")
        if request.endpoint != "static":
            h["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(429)
    @app.errorhandler(500)
    def error_page(error):
        code = getattr(error, "code", 500)
        messages = {
            403: "Your role can't open this page.",
            404: "Nothing here.",
            429: "Too many attempts. Wait a minute and try again.",
            500: "Something broke. It's been logged.",
        }
        return render_template("errors/error.html", code=code, message=messages.get(code, "")), code
