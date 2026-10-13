"""Dashboard, reports and the year-end package (Phase 6b). The exit test builds a synthetic
year (taxed and untaxed invoices, a void, partial, over and bounced payments, a refund, a
credit memo, expenses with a receipt, a purchase order, trips) and checks the year-end
CSVs, recomputed from their raw rows, equal the P&L CSV and the dashboard to the cent on
both bases."""

import csv
import hashlib
import io
import re
import zipfile
from datetime import date, timedelta
from decimal import ROUND_HALF_UP
from decimal import Decimal as D

import pytest
from test_billing import add_jurisdiction, billable, issue, issued_id, owner_sql, today
from test_books import add_expense, partner, png
from test_partlinks import add_vendor
from test_parts import add_part
from test_payments import fixed_invoice, pay
from test_purchasing import make_po, order_for_job
from test_work import new_wo, setup, text

from app.formatting import money
from app.reports import figures, svg
from app.reports.service import csv_text
from conftest import db_query

LIVE = {"issued", "partially_paid", "paid", "written_off"}


def cents(v):
    return D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def day_in_year(t, frac):
    """A day between Jan 1 and today, frac of the way along."""
    jan1 = date(t.year, 1, 1)
    return jan1 + timedelta(days=int((t - jan1).days * frac))


def backdated(t, on):
    return {"on": on, "backdate_reason": "Synthetic year"} if on < t else {"on": on}


def synthetic_year(owner, app, tdb):
    t = today(app)
    partner(tdb, owner)
    jid = add_jurisdiction(owner, app)
    owner.post("/settings/tax", data={"collect_sales_tax": "y",
                                       "default_tax_jurisdiction_id": str(jid)})
    # A: a part (2 × $1.95, cost $0.40) and 1.5 h of labor, taxed
    a = billable(owner, app)
    inv_a = issued_id(issue(owner, app, a["wo"], jurisdiction_id=str(jid),
                            **backdated(t, day_in_year(t, 0.1))))
    # B: a fixed price; C: issued then voided (never counts)
    _, inv_b = fixed_invoice(owner, app, a["cid"], "250", jurisdiction_id=str(jid),
                             **backdated(t, day_in_year(t, 0.3)))
    _, inv_c = fixed_invoice(owner, app, a["cid"], "99", jurisdiction_id=str(jid),
                             **backdated(t, day_in_year(t, 0.4)))
    owner.post(f"/invoices/{inv_c}/void", data={"reason": "Entered twice"})
    total_a, total_b = (db_query(app, "SELECT total FROM invoice WHERE id = :i", i=i)[0][0]
                        for i in (inv_a, inv_b))
    # money in: partial then final on A, a bounced check on B, B overpaid by venmo
    pay(owner, app, inv_a, "50", received_on=str(day_in_year(t, 0.2)))
    pay(owner, app, inv_a, str(total_a - 50), method="check",
        received_on=str(day_in_year(t, 0.5)))
    pay(owner, app, inv_b, "20", method="check", received_on=str(day_in_year(t, 0.35)))
    bounced = db_query(app, "SELECT max(id) FROM payment")[0][0]
    owner.post(f"/payments/{bounced}/reverse", data={"reason": "Bounced",
                                                     "received_on": str(day_in_year(t, 0.45))})
    pay(owner, app, inv_b, str(total_b + 30), method="venmo",
        received_on=str(day_in_year(t, 0.6)))
    over = db_query(app, "SELECT max(id) FROM payment")[0][0]
    owner.post(f"/payments/{over}/refund", data={"amount": "10", "method": "zelle",
                                                 "reason": "Overpaid"})
    # a credit memo on B (untaxed), which sits on the account
    owner.post(f"/invoices/{inv_b}/credit-memo", data={
        "reason": "Goodwill", "lines-0-description": "Labor adjustment", "lines-0-amount": "25"})
    # spending: two expenses (one with a receipt), one voided, a PO with shipping and tax
    add_expense(owner, app, "40.00", spent_on=str(day_in_year(t, 0.15)),
                receipt=(png(), "r.png", "image/png"))
    add_expense(owner, app, "12.34", spent_on=str(day_in_year(t, 0.55)))
    gone = add_expense(owner, app, "99.00")
    owner.post(f"/books/expenses/{gone}/void", data={"reason": "Duplicate"})
    vid = add_vendor(owner)
    pid = add_part(owner, sku="CAP-SYN-1", preferred_vendor_id=str(vid))
    _, _, _, job = setup(owner, app)
    order_for_job(owner, job, pid)
    po = make_po(owner, app)
    owner.post(f"/purchasing/orders/{po}/amounts", data={"shipping": "7.50", "tax_paid": "1.20"})
    for frac, miles in ((0.25, "10"), (0.7, "23.5")):
        owner.post("/books/trips/new", data={
            "trip_on": str(day_in_year(t, frac)), "user_id": str(owner.user["id"]),
            "miles": miles, "purpose": "Service call", "own_vehicle": "y"})
    return {"t": t, "a": inv_a, "b": inv_b, "c": inv_c, "cid": a["cid"]}


def read_csv(zf_or_bytes, name=None):
    raw = zf_or_bytes.read(name) if name else zf_or_bytes
    rows = list(csv.DictReader(io.StringIO(raw.decode())))
    return [r for r in rows if next(iter(r.values())) != "Total"], \
        next((r for r in rows if next(iter(r.values())) == "Total"), None)


def recompute(zf):
    """The P&L parts, from the raw rows in the package alone."""
    invoices, _ = read_csv(zf, "invoices.csv")
    status = {r["Number"]: r["Status"] for r in invoices}
    billed = sum((D(r["Taxable"]) + D(r["Non-taxable"]) for r in invoices
                  if r["Status"] in LIVE), D(0))
    memos, _ = read_csv(zf, "credit-memos.csv")
    credited = sum((D(r["Taxable"]) + D(r["Non-taxable"]) for r in memos
                    if r["Status"] == "issued"), D(0))
    payments, _ = read_csv(zf, "payments.csv")
    allocations, _ = read_csv(zf, "allocations.csv")
    tax = sum((cents(D(r["Applied"]) * D(r["Invoice tax"]) / D(r["Invoice total"]))
               for r in allocations if r["From"] == "payment" and D(r["Invoice tax"]) > 0), D(0))
    lines, _ = read_csv(zf, "invoice-lines.csv")
    cogs = sum((cents(D(r["Qty"]) * D(r["Unit cost"])) for r in lines
                if status[r["Invoice"]] in LIVE and r["Kind"] in ("part", "sublet")
                and r["Unit cost"] and r["Customer's own part"] != "yes"), D(0))
    expenses, _ = read_csv(zf, "expenses.csv")
    trips, _ = read_csv(zf, "mileage.csv")
    out = {"invoiced": billed - credited, "tax": tax,
           "collected": sum((D(r["Amount"]) for r in payments), D(0)) - tax, "cogs": cogs,
           "expenses": sum((D(r["Amount"]) for r in expenses), D(0)),
           "mileage": sum((D(r["Value"]) for r in trips), D(0))}
    for basis, revenue in (("cash", out["collected"]), ("accrual", out["invoiced"])):
        out[basis] = revenue - cogs - out["expenses"] - out["mileage"]
    return out


def pnl_csv(raw):
    rows, _ = read_csv(raw)
    return {(r["Section"], r["Line"]): D(r["Amount"]) for r in rows}


def test_synthetic_year_adds_up_everywhere(app, signed_in, tdb):
    owner = signed_in("owner")
    s = synthetic_year(owner, app, tdb)
    t, y = s["t"], s["t"].year
    resp = owner.get(f"/reports/year-end/{y}.zip")
    assert resp.status_code == 200 and resp.mimetype == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    got = recompute(zf)
    # the scenario did what it says: the void is out, the credit memo and tax are in
    subtotal = {i: db_query(app, "SELECT subtotal_taxable + subtotal_nontaxable FROM invoice "
                                 "WHERE id = :i", i=i)[0][0] for i in (s["a"], s["b"])}
    assert got["invoiced"] == subtotal[s["a"]] + subtotal[s["b"]] - 25
    assert got["tax"] > 0 and got["cogs"] == D("0.80")
    assert got["expenses"] == D("40.00") + D("12.34") + D("7.50") + D("1.20")
    assert got["mileage"] > 0
    assert db_query(app, "SELECT kind, count(*) FROM payment GROUP BY kind ORDER BY kind") == \
        [("receipt", 4), ("refund", 1), ("reversal", 1)]
    assert db_query(app, "SELECT status FROM invoice WHERE id = :i", i=s["c"]) == [("void",)]
    for basis in ("cash", "accrual"):
        revenue = got["collected"] if basis == "cash" else got["invoiced"]
        in_zip = pnl_csv(zf.read(f"pnl-{basis}.csv"))
        lines = {section: amount for (section, _), amount in in_zip.items()}
        assert lines["Revenue"] == revenue
        assert lines["Cost of goods"] == got["cogs"]
        assert in_zip[("Expenses", "Total expenses")] == got["expenses"]
        assert lines["Mileage"] == got["mileage"]
        assert lines["Net profit"] == got[basis]
        # the report page's CSV is the same file
        page = owner.get("/reports/pnl", query_string={"p": "year", "basis": basis,
                                                        "format": "csv"})
        assert page.data == zf.read(f"pnl-{basis}.csv")
        # the months add up to the year
        _, total = read_csv(zf, f"pnl-monthly-{basis}.csv")
        assert D(total[f"Revenue ({basis})"]) == revenue and D(total["Net"]) == got[basis]
        assert D(total["Collected"]) == got["collected"]
        assert D(total["Invoiced"]) == got["invoiced"]
        # the dashboard's year to date
        with app.app_context():
            ytd = figures.top_figures(figures.to_date(t)[2], basis)
        assert (ytd["collected"], ytd["invoiced"], ytd["cogs"], ytd["expenses"],
                ytd["mileage"], ytd["net"]) == (got["collected"], got["invoiced"], got["cogs"],
                                                got["expenses"], got["mileage"], got[basis])
        dash = text(owner.get("/", query_string={"basis": basis}))
        assert money(got[basis]) in dash and money(got["collected"]) in dash
    # the package holds the documents behind the numbers, byte for byte
    names = set(zf.namelist())
    for sha, number in db_query(app, "SELECT pdf_sha256, number FROM invoice"):
        assert hashlib.sha256(zf.read(f"invoices/{number}.pdf")).hexdigest() == sha
    for sha, number in db_query(app, "SELECT pdf_sha256, number FROM credit_memo"):
        assert hashlib.sha256(zf.read(f"credit-memos/{number}.pdf")).hexdigest() == sha
    receipt = db_query(app, "SELECT receipt_sha256 FROM expense WHERE receipt_sha256 "
                            "IS NOT NULL")[0][0]
    [name] = [n for n in names if n.startswith("receipts/")]
    assert name.endswith(f"-{receipt[:8]}.jpg")
    assert hashlib.sha256(zf.read(name)).hexdigest() == receipt
    for must in ("README.txt", "pnl-cash.pdf", "pnl-accrual.pdf", "payments.csv",
                 "partner-ledger.csv", "sales-tax.csv", "1099.csv", "1099-by-method.csv",
                 f"inventory-{t}.csv", "sales-tax-filings.csv", "partners-cash.csv"):
        assert must in names, must
    assert zf.read("pnl-cash.pdf")[:5] == b"%PDF-"
    readme = zf.read("README.txt").decode()
    assert f"{y} (so far)" in readme and "MISSING" not in readme
    # 1099: the venmo payment is a 1099-K's, the rest the customer's
    people, _ = read_csv(zf, "1099.csv")
    venmo = db_query(app, "SELECT sum(amount) FROM payment WHERE method = 'venmo'")[0][0]
    assert [D(r["Payment apps (1099-K)"]) for r in people] == [venmo]


def test_year_end_package_says_what_is_missing(app, signed_in, tdb):
    owner = signed_in("owner")
    b = billable(owner, app)
    issued_id(issue(owner, app, b["wo"]))
    sha = db_query(app, "SELECT pdf_sha256 FROM invoice")[0][0]
    from app.work import files
    with app.app_context():
        path = files.path_for(sha, ".pdf")
        kept = path.read_bytes()
        path.unlink()
    try:
        zf = zipfile.ZipFile(io.BytesIO(owner.get(f"/reports/year-end/{today(app).year}.zip").data))
        assert "MISSING" in zf.read("README.txt").decode()
    finally:
        path.write_bytes(kept)
    assert owner.get("/reports/year-end/1999.zip").status_code == 404
    assert owner.get(f"/reports/year-end/{today(app).year + 1}.zip").status_code == 404


# --- figures: the definitions -----------------------------------------------------------------

def test_cash_basis_takes_the_tax_out_half_up(app, signed_in):
    owner = signed_in("owner")
    jid = add_jurisdiction(owner, app)
    owner.post("/settings/tax", data={"collect_sales_tax": "y",
                                       "default_tax_jurisdiction_id": str(jid)})
    b = billable(owner, app, part_price="0.50")          # $1.00 taxable → $0.09 tax
    inv = issued_id(issue(owner, app, b["wo"], jurisdiction_id=str(jid)))
    assert db_query(app, "SELECT tax, total FROM invoice") == [(D("0.09"), D("121.09"))]
    pay(owner, app, inv, "60")                           # 60 × 0.09 / 121.09 = 0.0446 → 0.04
    t = today(app)
    with app.app_context():
        assert figures.tax_in_payments(t, t) == D("0.04")
        assert figures.collected(t, t) == D("59.96")
        assert figures.invoiced(t, t) == D("121.00")
    pay(owner, app, inv, "61.09")                        # 0.0454 → 0.05; 0.04 + 0.05 = 0.09
    with app.app_context():
        assert figures.tax_in_payments(t, t) == D("0.09")
        assert figures.collected(t, t) == D("121.00")    # once paid, the bases agree


def test_customers_own_parts_cost_nothing(app, signed_in):
    owner = signed_in("owner")
    cid, _, wo, job = setup(owner, app)
    owner.post(f"/work/jobs/{job}/lines/customer-part", data={"description": "Their caps",
                                                               "qty": "1"})
    owner.post(f"/work/jobs/{job}/lines/fixed", data={"description": "Recap", "unit_price": "80",
                                                       "billable": "y", "tech_user_id": "None"})
    issued_id(issue(owner, app, wo))
    t = today(app)
    with app.app_context():
        assert figures.cogs(t, t) == 0
        p = figures.pnl(t, t, "accrual")
    assert (p["revenue"], p["cogs"], p["net"]) == (D("80.00"), 0, D("80.00"))


def test_partner_shares_add_up_to_net(app, signed_in, tdb, make_user):
    owner = signed_in("owner")
    partner(tdb, owner)
    other = make_user("pat", "owner")
    owner_sql(tdb, "UPDATE app_user SET is_partner = true WHERE id = %(i)s", i=other["id"])
    third = make_user("quinn", "owner")
    owner_sql(tdb, "UPDATE app_user SET is_partner = true WHERE id = %(i)s", i=third["id"])
    t = today(app)
    with app.app_context():
        shares = figures.partner_shares(D("100.00"), t, t)
        assert [a for _, _, a in shares] == [D("33.33"), D("33.33"), D("33.34")]
        assert sum(a for _, _, a in shares) == D("100.00")
        assert figures.partner_shares(D("-10.00"), t, t)[-1][2] == D("-3.34")
    owner_sql(tdb, "UPDATE shop_setting SET split_mode = 'custom'")
    owner_sql(tdb, "UPDATE app_user SET split_pct = CASE WHEN username = 'pat' THEN 60 "
                   "WHEN username = 'quinn' THEN 0 ELSE 40 END WHERE is_partner")
    with app.app_context():
        got = {u.username: (pct, a) for u, pct, a in figures.partner_shares(D("10.01"), t, t)}
    assert got["pat"] == (D("60.00"), D("6.01")) and got["quinn"][1] == 0
    assert sum(a for _, a in got.values()) == D("10.01")


def test_set_aside_only_on_a_profit(app, tdb):
    owner_sql(tdb, "UPDATE shop_setting SET set_aside_pct = 25")
    with app.app_context():
        assert figures.set_aside(D("100.01")) == D("25.00")
        assert figures.set_aside(D("-5")) is None
    owner_sql(tdb, "UPDATE shop_setting SET set_aside_pct = NULL")


def test_aging_buckets(app, signed_in, tdb):
    owner = signed_in("owner")
    t = today(app)
    cid, _, _, _ = setup(owner, app)
    for days in (0, 20, 45, 75, 120):
        fixed_invoice(owner, app, cid, "10", **backdated(t, t - timedelta(days=days)))
    with app.app_context():
        rows, buckets, total = figures.receivables(t)
    # due on receipt: due the day it's issued
    assert list(buckets.values()) == [D(10)] * 5 and total == D(50)
    page = text(owner.get("/reports/aging"))
    assert "Over 90 days late" in page and "$50.00" in page


# --- reports ----------------------------------------------------------------------------------

REPORTS = ("/reports", "/reports/pnl", "/reports/pnl/monthly", "/reports/aging",
           "/reports/sales-tax", "/reports/expenses", "/reports/mileage", "/reports/partners",
           "/reports/1099", "/reports/inventory", "/reports/parts-usage", "/reports/labor",
           "/reports/jobs", "/reports/turnaround", "/reports/warranty")


def test_every_report_renders_and_writes_csv(app, signed_in, tdb):
    owner = signed_in("owner")
    synthetic_year(owner, app, tdb)
    viewer, tech = signed_in("viewer"), signed_in("tech")
    lot = db_query(app, "SELECT id FROM stock_lot ORDER BY id LIMIT 1")[0][0]
    for path in REPORTS + (f"/reports/lots/{lot}",):
        for who in (owner, viewer):
            resp = who.get(path)
            assert resp.status_code == 200, (path, text(resp)[-1500:])
        assert tech.get(path).status_code == 403, path
        if path not in ("/reports",):
            got = owner.get(path, query_string={"format": "csv"})
            assert got.status_code == 200 and got.mimetype == "text/csv", path
            assert got.headers["Content-Disposition"].startswith("attachment; filename=")
            assert got.data.count(b"\n") >= 1, path
    assert tech.get(f"/reports/year-end/{today(app).year}.zip").status_code == 403
    assert tech.get("/reports/pnl.pdf").status_code == 403
    pdf = owner.get("/reports/pnl.pdf", query_string={"p": "year", "basis": "accrual"})
    assert pdf.status_code == 200 and pdf.data[:5] == b"%PDF-"
    assert owner.get("/reports/lots/999999").status_code == 404
    # presets and periods
    page = text(owner.get("/reports/expenses", query_string={"p": "last_year"}))
    assert f"{today(app).year - 1}-01-01 to {today(app).year - 1}-12-31" in page
    page = text(owner.get("/reports/expenses", query_string={"start": "2026-03-31",
                                                             "end": "2026-03-01"}))
    assert "2026-03-01 to 2026-03-31" in page                          # swapped
    page = text(owner.get("/reports/expenses", query_string={"start": "nonsense"}))
    assert f"{today(app).year}-01-01 to {today(app)}" in page          # year to date


def test_csv_text_cells_cant_run_as_formulas():
    out = csv_text(["a", "b", "c"], [["=SUM(A1)", D("-5.00"), "+1"], ["@x", None, "ok"]])
    assert out.splitlines() == ["a,b,c", "'=SUM(A1),-5.00,'+1", "'@x,,ok"]


def test_turnaround_uses_ready_and_comebacks(app, signed_in):
    owner = signed_in("owner")
    cid, _, wo, job = setup(owner, app)
    owner.post(f"/work/{wo}/status", data={"status": "ready"})
    first = db_query(app, "SELECT ready_at FROM work_order WHERE id = :w", w=wo)[0][0]
    assert first is not None
    owner.post(f"/work/{wo}/status", data={"status": "in_progress"})
    owner.post(f"/work/{wo}/status", data={"status": "ready"})
    assert db_query(app, "SELECT ready_at FROM work_order WHERE id = :w", w=wo) == [(first,)]
    page = text(owner.get("/reports/turnaround"))
    assert db_query(app, "SELECT number FROM work_order WHERE id = :w", w=wo)[0][0] in page
    # comeback rate: warranty WOs ÷ jobs invoiced in the last 90 days
    owner.post(f"/work/jobs/{job}/lines/fixed", data={"description": "Fix", "unit_price": "50",
                                                       "billable": "y", "tech_user_id": "None"})
    issued_id(issue(owner, app, wo))
    new_wo(owner, cid, kind="warranty")
    with app.app_context():
        from app.reports.service import warranty
        claims, jobs, rate, _ = warranty(90, today(app))
    assert (len(claims), jobs, rate) == (1, 1, D("100.0"))
    assert "100.0%" in text(owner.get("/reports/warranty"))


def test_inventory_value_and_lot_trace(app, signed_in):
    owner = signed_in("owner")
    b = billable(owner, app)                               # 10 received at 0.40, 2 used
    page = text(owner.get("/reports/inventory"))
    assert "$3.20" in page                                  # 8 × 0.40
    before = text(owner.get("/reports/inventory", query_string={"as_of": "2000-01-01"}))
    assert "Nothing in stock on that day." in before
    lot = db_query(app, "SELECT id FROM stock_lot")[0][0]
    trace = text(owner.get(f"/reports/lots/{lot}"))
    number = db_query(app, "SELECT number FROM work_order WHERE id = :w", w=b["wo"])[0][0]
    assert number in trace and "receive" in trace


# --- the dashboard and the nav ----------------------------------------------------------------

def test_dashboard_by_role(app, signed_in, tdb):
    owner = signed_in("owner")
    synthetic_year(owner, app, tdb)
    page = text(owner.get("/"))
    for want in ("Month to date", "Quarter to date", "Year to date", "Last year", "Owed to you",
                 "data-chart=", "<svg", "In the shop", "On the shopping list", "Comebacks"):
        assert want in page, want
    assert "vendor/chartjs/chart.umd.min.js" in page
    tech = signed_in("tech")
    page = text(tech.get("/"))
    assert "In the shop" in page and "On the shopping list" in page
    for hidden in ("Year to date", "Owed to you", "data-chart=", "chart.umd", "Comebacks",
                   ">Money<"):
        assert hidden not in page, hidden
    viewer = signed_in("viewer")
    assert "Year to date" in text(viewer.get("/"))
    # a bad basis falls back to cash
    assert "Cash basis: revenue is what was collected" in text(owner.get("/?basis=nope"))


def test_nav_sections_and_tabs(app, signed_in):
    owner, tech, viewer = signed_in("owner"), signed_in("tech"), signed_in("viewer")
    top = re.compile(r'<nav class="nav">(.*?)</nav>', re.S)
    sub = re.compile(r'<nav class="subnav">(.*?)</nav>', re.S)

    def labels(html, rx):
        found = rx.search(html)
        return re.findall(r">([^<]+)</a>", found.group(1)) if found else []

    page = text(owner.get("/invoices"))
    assert labels(page, top)[:6] == ["Dashboard", "Work", "Customers", "Money", "Parts",
                                     "Settings"]
    assert labels(page, sub) == ["Invoices", "Payments", "Books", "Reports"]
    assert 'class="active">Money<' in page and 'class="active">Invoices<' in page
    assert 'class="active">Payments<' in text(owner.get("/payments"))
    assert labels(text(owner.get("/purchasing")), sub) == ["Parts", "Links", "Buying",
                                                           "Price book"]
    assert labels(text(owner.get("/assets")), sub) == ["Customers", "Assets"]
    assert labels(text(owner.get("/")), sub) == []                     # one tab: no row
    assert labels(text(tech.get("/work")), top)[:4] == ["Dashboard", "Work", "Customers", "Parts"]
    assert labels(text(viewer.get("/reports")), top)[:5] == ["Dashboard", "Work", "Customers",
                                                             "Money", "Parts"]


# --- charts -----------------------------------------------------------------------------------

def test_svg_charts_are_csp_safe_and_handle_negatives():
    data = {"labels": ["Jan", "Feb", "Mar <&>"],
            "series": [("Revenue", [D("100.00"), D(0), D("50.50")]),
                       ("Net", [D("-20.00"), D(0), D("10.00")])]}
    out = str(svg.bars(data, title="By month"))
    assert out.startswith("<svg") and out.endswith("</svg>")
    assert "style=" not in out and "<script" not in out
    assert "Mar &lt;&amp;&gt;" in out and "-$20" in out and "$101" not in out
    one = {"labels": ["A very long customer name that goes on", "B"],
           "series": [("Revenue", [D("10"), D("0")])]}
    out = str(svg.hbars(one))
    assert "style=" not in out and "$10" in out
    assert str(svg.bars({"labels": [], "series": [("x", [])]})).endswith("</svg>")


@pytest.mark.parametrize("path", ["js/charts.js", "js/prefs.js",
                                  "vendor/chartjs/chart.umd.min.js"])
def test_chart_assets_are_served(app, signed_in, path):
    owner = signed_in("owner")
    resp = owner.get(f"/static/{path}")
    assert resp.status_code == 200 and len(resp.data) > 100
