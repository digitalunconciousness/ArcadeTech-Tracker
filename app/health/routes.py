from flask import Blueprint, jsonify
from sqlalchemy import text

from app.extensions import db

bp = Blueprint("health", __name__)


@bp.route("/healthz")
def healthz():
    """For deploy.sh and monitoring on loopback. Says nothing beyond up/down."""
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        db.session.rollback()
        return jsonify(ok=False, db=False), 503
    return jsonify(ok=True, db=True)
