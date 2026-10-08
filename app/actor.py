"""Who is acting, as Postgres sees it.

Every transaction the app opens starts with set_config('shop.user_id', <id>, true), the
bind-parameter form of SET LOCAL. The audit trigger and the created_by defaults read it.
It is set on each new transaction (after_begin), not once per request, so a second
transaction after a commit still carries the user."""

from flask import g, has_app_context
from flask_sqlalchemy.session import Session
from sqlalchemy import event, text

SET_ACTOR = text("SELECT set_config('shop.user_id', :uid, true)")


def _uid_param(user_id):
    return "" if user_id is None else str(int(user_id))


def current_actor():
    return g.get("audit_user_id") if has_app_context() else None


@event.listens_for(Session, "after_begin")
def _set_actor_on_begin(session, transaction, connection):
    connection.execute(SET_ACTOR, {"uid": _uid_param(current_actor())})


def set_actor(user_id):
    """Make user_id the actor for the rest of this request (or CLI app context),
    including the transaction already open."""
    from app.extensions import db

    g.audit_user_id = user_id
    db.session.execute(SET_ACTOR, {"uid": _uid_param(user_id)})
