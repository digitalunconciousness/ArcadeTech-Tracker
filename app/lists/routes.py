"""Saved views for the desktop lists, and bulk actions on work orders."""

from urllib.parse import parse_qsl, urlencode

from flask import Blueprint, abort, flash, redirect, request, url_for
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import ALL_ROLES, EDIT_ROLES, requires_role
from app.auth.forms import EmptyForm
from app.extensions import db
from app.lists.specs import SPECS
from app.models import WO_STATUSES, SavedView, User, WorkOrder
from app.work import service as work

bp = Blueprint("lists", __name__)

ENDPOINTS = {"work": "work.index", "estimates": "estimates.index",
             "customers": "customers.index", "assets": "assets.index",
             "invoices": "billing.index", "payments": "billing.payment_list",
             "parts": "parts.index", "orders": "purchasing.orders"}


def clean_query(raw):
    """Keep only a list's own arguments: no format, no pane."""
    pairs = [(k, v) for k, v in parse_qsl(raw or "", keep_blank_values=False)
             if k not in ("format", "pane") and len(k) <= 30 and len(v) <= 100]
    return urlencode(pairs)[:1000]


def list_url(page, query=""):
    return url_for(ENDPOINTS[page]) + (f"?{query}" if query else "")


@bp.route("/views", methods=["POST"])
@requires_role(*ALL_ROLES)
def save_view():
    page = request.form.get("page", "")
    if page not in SPECS or not EmptyForm().validate_on_submit():
        abort(400)
    name = (request.form.get("name") or "").strip()[:60]
    query = clean_query(request.form.get("query"))
    if not name:
        flash("Give the view a name.", "error")
        return redirect(list_url(page, query))
    view = db.session.scalar(select(SavedView).where(
        SavedView.user_id == current_user.id, SavedView.page == page, SavedView.name == name))
    if view is None:
        view = SavedView(user_id=current_user.id, page=page, name=name, query=query)
        db.session.add(view)
    else:
        view.query = query
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Couldn't save that view.", "error")
        return redirect(list_url(page, query))
    flash(f"Saved the view “{name}”.", "ok")
    return redirect(list_url(page, query))


@bp.route("/views/<int:view_id>/delete", methods=["POST"])
@requires_role(*ALL_ROLES)
def delete_view(view_id):
    view = db.session.get(SavedView, view_id)
    if view is None or view.user_id != current_user.id or not EmptyForm().validate_on_submit():
        abort(404)
    page = view.page
    db.session.delete(view)
    db.session.commit()
    flash("View deleted.", "ok")
    return redirect(list_url(page))


def saved_views(page):
    return db.session.scalars(select(SavedView).where(
        SavedView.user_id == current_user.id, SavedView.page == page)
        .order_by(SavedView.name)).all()


@bp.app_context_processor
def list_helpers():
    return {"saved_views": saved_views}


@bp.route("/work/bulk", methods=["POST"])
@requires_role(*EDIT_ROLES)
def bulk_work():
    """Set the status of, or add a tech to, the work orders ticked on the list."""
    back = list_url("work", clean_query(request.form.get("query")))
    if not EmptyForm().validate_on_submit():
        abort(400)
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()][:500]
    action = request.form.get("action", "")
    value = request.form.get(f"value_{action}", "")
    orders = db.session.scalars(select(WorkOrder).where(WorkOrder.id.in_(ids))).all() \
        if ids else []
    if not orders:
        flash("Tick at least one work order.", "error")
        return redirect(back)
    if action == "status" and value in WO_STATUSES:
        for wo in orders:
            work.set_status(wo, value)
        message = f"{len(orders)} work order{'s' if len(orders) != 1 else ''}: " \
                  f"{WO_STATUSES[value].lower()}."
    elif action == "tech" and value.isdigit():
        user = db.session.get(User, int(value))
        if user is None or not user.active or user.role not in EDIT_ROLES:
            abort(400)
        for wo in orders:
            work.set_techs(wo, work.tech_ids(wo) | {user.id})
        message = f"Added {user.display_name} to {len(orders)} work " \
                  f"order{'s' if len(orders) != 1 else ''}."
    else:
        abort(400)
    db.session.commit()
    flash(message, "ok")
    return redirect(back)
