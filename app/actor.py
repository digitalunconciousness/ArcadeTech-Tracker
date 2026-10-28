"""Who is acting, as Postgres sees it.

Every transaction the app opens starts with set_config('shop.user_id', <id>, true), the
bind-parameter form of SET LOCAL, and set_config('shop.device_id', <id>, true). The audit
trigger and the created_by defaults read them. They are set on each new transaction
(after_begin), not once per request, so a second transaction after a commit still carries
them.

A person's request sets the user and no device. A GATBOX device's request (requires_device)
sets the user the device acts as, if any, and the device (owner, 2026-10-28)."""

from flask import g, has_app_context
from flask_sqlalchemy.session import Session
from sqlalchemy import event, text

SET_ACTOR = text("SELECT set_config('shop.user_id', :uid, true), "
                 "set_config('shop.device_id', :did, true)")


def _id_param(value):
    return "" if value is None else str(int(value))


def current_actor():
    return g.get("audit_user_id") if has_app_context() else None


def current_device():
    return g.get("audit_device_id") if has_app_context() else None


def _params():
    return {"uid": _id_param(current_actor()), "did": _id_param(current_device())}


@event.listens_for(Session, "after_begin")
def _set_actor_on_begin(session, transaction, connection):
    connection.execute(SET_ACTOR, _params())


def set_actor(user_id, device_id=None):
    """Make user_id (and device_id, for a GATBOX device's request) the actor for the rest
    of this request (or CLI app context), including the transaction already open."""
    from app.extensions import db

    g.audit_user_id = user_id
    g.audit_device_id = device_id
    db.session.execute(SET_ACTOR, _params())
