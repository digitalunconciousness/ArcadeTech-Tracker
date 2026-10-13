from flask import Blueprint, render_template, request
from flask_login import current_user

from app.auth.decorators import ALL_ROLES, COST_ROLES, requires_role
from app.books import service as books
from app.reports import dashboard, figures, svg
from app.reports.figures import BASES, chart_json
from app.timeutil import local_today

bp = Blueprint("dashboard", __name__)


@bp.route("/")
@requires_role(*ALL_ROLES)
def index():
    sees_money = current_user.role in COST_ROLES
    ctx = {"work": dashboard.work_counts(), "parts": dashboard.parts_counts(),
           "sees_money": sees_money}
    if sees_money:
        basis = request.args.get("basis") if request.args.get("basis") in BASES else "cash"
        today = local_today()
        year_start = today.replace(month=1, day=1)
        _, buckets, owed_total = figures.receivables(today)
        monthly = figures.chart_monthly(today)
        customers = figures.chart_customers(year_start, today, basis)
        mix = figures.chart_wo_mix(year_start, today)
        accent = figures.settings().doc_accent_color
        ctx.update(
            basis=basis, bases=BASES,
            rows=[(figures.top_figures(p, basis), figures.top_figures(p.last_year(), basis))
                  for p in figures.to_date(today)],
            buckets=buckets, owed_total=owed_total, unfiled=books.unfiled_tax(today),
            money=dashboard.money_counts(today), warranty=dashboard.warranty_counts(today),
            charts=[
                ("Collected and invoiced, last 12 months", chart_json(monthly),
                 svg.bars(monthly, accent=accent)),
                (f"Revenue by customer, {today.year} ({BASES[basis].lower()})",
                 chart_json(customers, horizontal=True), svg.hbars(customers, accent=accent)),
                (f"Work orders by kind, {today.year}", chart_json(mix, money=False),
                 svg.bars(mix, money=False, accent=accent)),
            ])
    return render_template("dashboard/index.html", **ctx)
