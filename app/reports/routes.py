"""Reports for owners and viewers: each a page and a CSV (?format=csv), the P&L also a PDF,
and the year-end package as one zip."""

from datetime import date, timedelta

from flask import Blueprint, Response, abort, render_template, request, url_for

from app.auth.decorators import COST_ROLES, requires_role
from app.books import journal
from app.reports import figures, svg, yearend
from app.reports import tables as t
from app.reports.figures import BASES, chart_json
from app.timeutil import local_today, localdt, utcnow
from app.work.docs import render_pdf
from app.work.routes import pdf_response

bp = Blueprint("reports", __name__)

PRESETS = {"month": "This month", "last_month": "Last month", "quarter": "This quarter",
           "year": "This year", "last_year": "Last year"}


# --- choosing a period ------------------------------------------------------------------------

def preset_dates(key, today):
    if key == "month":
        return today.replace(day=1), today
    if key == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if key == "quarter":
        return date(today.year, (today.month - 1) // 3 * 3 + 1, 1), today
    if key == "last_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    return date(today.year, 1, 1), today


def _date_arg(name):
    try:
        return date.fromisoformat(request.args.get(name, ""))
    except ValueError:
        return None


def period():
    """start/end from the query string (?p=preset, or ?start=&end=); year to date by
    default."""
    today = local_today()
    if request.args.get("p") in PRESETS:
        return preset_dates(request.args["p"], today)
    start, end = _date_arg("start"), _date_arg("end")
    if start is None or end is None:
        start, end = preset_dates("year", today)
    return (end, start) if end < start else (start, end)


def basis_arg():
    return request.args.get("basis") if request.args.get("basis") in BASES else "cash"


def year_arg():
    try:
        y = int(request.args.get("year", ""))
    except ValueError:
        return local_today().year
    return y if 2000 <= y <= 2100 else local_today().year


# --- responses --------------------------------------------------------------------------------

def csv_response(filename, table):
    resp = Response(t.to_csv(table), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def respond(slug, tables, *, filters="period", stamp=None, facts=None, **extra):
    """The page, or the first table as CSV with ?format=csv."""
    if request.args.get("format") == "csv":
        return csv_response(f"{slug}-{stamp}.csv", tables[0])
    return render_template("reports/table.html", tables=tables, filters=filters, facts=facts,
                           presets=PRESETS, slug=slug, **extra)


def here(**changes):
    """This page's URL with some query arguments changed (None drops one)."""
    args = {**request.args.to_dict(), **changes}
    return url_for(request.endpoint, **(request.view_args or {}),
                   **{k: v for k, v in args.items() if v is not None})


@bp.app_context_processor
def report_helpers():
    return {"csv_url": lambda: here(format="csv"), "here": here,
            "this_year": lambda: local_today().year}


# --- the index --------------------------------------------------------------------------------

@bp.route("/reports")
@requires_role(*COST_ROLES)
def index():
    this_year = local_today().year
    return render_template("reports/index.html",
                           years=list(range(this_year, t.first_year() - 1, -1)),
                           this_year=this_year)


# --- the P&L ----------------------------------------------------------------------------------

def pnl_chart(start, end, basis):
    """Revenue, costs and net by month across the period (up to two years)."""
    periods = [m for y in range(start.year, end.year + 1) for m in figures.months(y)
               if m.end >= start and m.start <= end][:24]
    rows = [figures.pnl(max(m.start, start), min(m.end, end), basis) for m in periods]
    return {"labels": [m.start.strftime("%b %Y") for m in periods],
            "series": [("Revenue", [r["revenue"] for r in rows]),
                       ("Costs", [r["cogs"] + r["expenses"] + r["mileage"] for r in rows]),
                       ("Net", [r["net"] for r in rows])]}


def pnl_context(start, end, basis):
    table, p = t.pnl_table(start, end, basis)
    accent = figures.settings().doc_accent_color
    chart = pnl_chart(start, end, basis)
    return {"table": table, "p": p, "start": start, "end": end, "basis": basis,
            "collected": figures.collected(start, end), "invoiced": figures.invoiced(start, end),
            "set_aside": figures.set_aside(p["net"]), "pct": figures.settings().set_aside_pct,
            "shares": figures.partner_shares(p["net"], start, end),
            "chart": chart, "chart_data": chart_json(chart),
            "chart_svg": svg.bars(chart, accent=accent),
            "chart_svg_doc": svg.bars(chart, accent=accent,
                                      title="Revenue, costs and net by month")}


@bp.route("/reports/pnl")
@requires_role(*COST_ROLES)
def pnl():
    start, end = period()
    basis = basis_arg()
    ctx = pnl_context(start, end, basis)
    if request.args.get("format") == "csv":
        return csv_response(f"pnl-{basis}-{start}-{end}.csv", ctx["table"])
    return render_template("reports/pnl.html", presets=PRESETS, bases=BASES, **ctx)


def pnl_pdf_bytes(start, end, basis):
    return render_pdf("docs/pnl.html", bases=BASES, today=local_today(),
                      **pnl_context(start, end, basis))


@bp.route("/reports/pnl.pdf")
@requires_role(*COST_ROLES)
def pnl_pdf():
    start, end = period()
    basis = basis_arg()
    return pdf_response(pnl_pdf_bytes(start, end, basis), f"pnl-{basis}-{start}-{end}.pdf")


@bp.route("/reports/pnl/monthly")
@requires_role(*COST_ROLES)
def pnl_monthly():
    y, basis = year_arg(), basis_arg()
    return respond(f"pnl-monthly-{basis}", [t.pnl_monthly_table(y, basis)], filters="year",
                   stamp=y, year=y, basis=basis, bases=BASES, title="Profit and loss by month")


# --- money ------------------------------------------------------------------------------------

@bp.route("/reports/aging")
@requires_role(*COST_ROLES)
def aging():
    today = local_today()
    table, buckets = t.aging_table(today)
    return respond("aging", [table], filters=None, stamp=today,
                   facts=[(label, total, "money") for label, total in buckets.items()],
                   title="Receivables aging")


@bp.route("/reports/sales-tax")
@requires_role(*COST_ROLES)
def sales_tax():
    start, end = period()
    return respond("sales-tax", [t.sales_tax_table(start, end)], stamp=f"{start}-{end}",
                   start=start, end=end, title="Sales tax")


@bp.route("/reports/expenses")
@requires_role(*COST_ROLES)
def expenses():
    start, end = period()
    return respond("expenses", [t.expenses_table(start, end)], stamp=f"{start}-{end}",
                   start=start, end=end, title="Expenses")


@bp.route("/reports/mileage")
@requires_role(*COST_ROLES)
def mileage():
    start, end = period()
    return respond("mileage", [t.mileage_table(start, end)], stamp=f"{start}-{end}",
                   start=start, end=end, title="Mileage")


@bp.route("/reports/partners")
@requires_role(*COST_ROLES)
def partners():
    start, end = period()
    basis = basis_arg()
    table, _ = t.partners_table(start, end, basis)
    return respond(f"partners-{basis}", [table], stamp=f"{start}-{end}", start=start, end=end,
                   basis=basis, bases=BASES, title="Partners")


@bp.route("/reports/1099")
@requires_role(*COST_ROLES)
def nec():
    y = year_arg()
    return respond("1099", list(t.nec_tables(y)), filters="year", stamp=y, year=y,
                   title="1099 reconciliation")


# --- parts ------------------------------------------------------------------------------------

@bp.route("/reports/inventory")
@requires_role(*COST_ROLES)
def inventory():
    as_of = _date_arg("as_of") or local_today()
    return respond("inventory", [t.inventory_table(as_of)], filters="as_of", stamp=as_of,
                   as_of=as_of, title="Inventory value")


@bp.route("/reports/lots/<int:lot_id>")
@requires_role(*COST_ROLES)
def lot(lot_id):
    found = t.lot_for(lot_id)
    if found is None:
        abort(404)
    table = t.lot_table(found)
    return respond(f"lot-{lot_id}", [table], filters=None, stamp=local_today(),
                   title=f"Lot {lot_id}",
                   facts=[("Received", localdt(found.received_at, "%Y-%m-%d"), "text"),
                          ("Unit cost", found.unit_cost, "cost"),
                          ("Date code", found.date_code or "", "text"),
                          ("Vendor lot", found.vendor_lot or "", "text")])


@bp.route("/reports/parts-usage")
@requires_role(*COST_ROLES)
def usage():
    start, end = period()
    return respond("parts-usage", [t.usage_table(start, end)], stamp=f"{start}-{end}",
                   start=start, end=end, title="Parts used")


# --- work -------------------------------------------------------------------------------------

@bp.route("/reports/labor")
@requires_role(*COST_ROLES)
def labor():
    start, end = period()
    return respond("labor", [t.labor_table(start, end)], stamp=f"{start}-{end}",
                   start=start, end=end, title="Labor by person")


@bp.route("/reports/jobs")
@requires_role(*COST_ROLES)
def jobs():
    start, end = period()
    return respond("jobs", [t.jobs_table(start, end)], stamp=f"{start}-{end}",
                   start=start, end=end, title="Job profitability")


@bp.route("/reports/turnaround")
@requires_role(*COST_ROLES)
def turnaround():
    start, end = period()
    table, mid = t.turnaround_table(start, end)
    return respond("turnaround", [table], stamp=f"{start}-{end}", start=start, end=end,
                   title="Turnaround",
                   facts=[("Median days", mid if mid is not None else "—", "text"),
                          ("Work orders", len(table.rows), "text")])


@bp.route("/reports/warranty")
@requires_role(*COST_ROLES)
def warranty():
    today = local_today()
    comebacks, expiring, rate = t.warranty_tables(today)
    return respond("warranty", [comebacks, expiring], filters=None, stamp=today,
                   title="Warranty and comebacks",
                   facts=[("Comeback rate, 90 days", f"{rate}%" if rate is not None else "—",
                           "text")])


# --- for an accountant ------------------------------------------------------------------------

@bp.route("/reports/accountant")
@requires_role(*COST_ROLES)
def accountant():
    """The general journal and trial balance for a period, double entry on the accrual
    basis, under the account names in Settings → Accounts."""
    start, end = period()
    es = journal.entries(start, end)
    if request.args.get("format") == "journal":
        return csv_response(f"general-journal-{start}-{end}.csv", t.journal_table(es))
    table = t.trial_balance_table(es, start, end)
    if request.args.get("format") == "csv":
        return csv_response(f"trial-balance-{start}-{end}.csv", table)
    net, pnl_net = journal.net_income(es), figures.pnl(start, end, "accrual")["net"]
    return render_template("reports/accountant.html", table=table, start=start, end=end,
                           presets=PRESETS, filters="period", entries=len(es),
                           unbalanced=sum(1 for e in es if not e.balanced),
                           net=net, pnl_net=pnl_net)


# --- the year-end package ---------------------------------------------------------------------

@bp.route("/reports/year-end/<int:y>.zip")
@requires_role(*COST_ROLES)
def year_end(y):
    if not 2000 <= y <= local_today().year:
        abort(404)
    stream = yearend.build(y, utcnow())
    resp = Response(yearend.chunks(stream), mimetype="application/zip")
    resp.headers["Content-Disposition"] = f'attachment; filename="year-end-{y}.zip"'
    return resp
