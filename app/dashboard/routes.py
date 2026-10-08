from flask import Blueprint, render_template

from app.auth.decorators import ALL_ROLES, requires_role

bp = Blueprint("dashboard", __name__)


@bp.route("/")
@requires_role(*ALL_ROLES)
def index():
    return render_template("dashboard/index.html")
