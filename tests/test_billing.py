"""Invoices: issuing from a work order (snapshot, gapless number, frozen PDF), the freeze
in the database, tax (off until the permit; half-up at the frozen rate when on),
partial billing and voids, backdating, fixed prices, counter sales, who sees what, and
the customer's link."""

import hashlib
import re
from datetime import timedelta
from decimal import Decimal as D

import psycopg
import pytest
from test_assets import asset, customer, site
from test_estimates import est_job, new_estimate
from test_parts import add_part
from test_work import activate, new_wo, setup, text

from app.formatting import rate_pct, scope_items
from app.timeutil import local_today
from app.work import files
from conftest import db_query, libpq

INTEGRITY = (psycopg.errors.IntegrityConstraintViolation, psycopg.errors.InsufficientPrivilege,
             psycopg.errors.CheckViolation)


def today(app):
    with app.app_context():
        return local_today()


def billable(owner, app, part_price=None):
    """A bench WO with a stocked, taxable part (2 × $1.95, or part_price each) and 1.5 h of
    flat labor at $80."""
    cid, aid, wo_id, job_id = setup(owner, app)
    bench = activate(owner, app, "LAB-BENCH", "80")
    pid = add_part(owner)
    owner.post(f"/parts/{pid}/receive", data={"qty": "10", "unit_cost": "0.40",
                                              "vendor_id": "None"})
    owner.post(f"/work/jobs/{job_id}/lines/part", data={
        "part_id": pid, "qty": "2", "billable": "y",
        **({"unit_price": part_price} if part_price else {})})
    owner.post(f"/work/jobs/{job_id}/lines/service", data={
        "service_id": bench, "labor_mode": "flat", "qty": "1.5", "tech_user_id": "None",
        "billable": "y"})
    return {"cid": cid, "aid": aid, "wo": wo_id, "job": job_id, "pid": pid}


def line_ids(app, wo_id, job_id=None):
    rows = db_query(app, "SELECT l.id FROM wo_line l JOIN wo_job j ON j.id = l.wo_job_id "
                         "WHERE j.work_order_id = :w AND (CAST(:j AS integer) IS NULL OR j.id = :j) "
                         "AND l.invoice_id IS NULL ORDER BY l.id", w=wo_id, j=job_id)
    return [r[0] for r in rows]


def issue(client, app, wo_id, lines=None, on=None, **extra):
    data = {"lines": [str(i) for i in (line_ids(app, wo_id) if lines is None else lines)],
            "issue_date": str(on or today(app)), "jurisdiction_id": "None", "action": "issue",
            **extra}
    return client.post(f"/work/{wo_id}/invoice", data=data)


def issued_id(resp):
    assert resp.status_code == 302, text(resp)[-2500:]
    return int(resp.location.rsplit("/", 1)[1])


def owner_sql(tdb, sql, **params):
    """Change data behind the app's back, as the schema owner."""
    with psycopg.connect(libpq(tdb.url("owner")), autocommit=True) as c:
        c.execute(sql, params)


def capture(monkeypatch, module):
    """Render a document's HTML instead of a PDF; returns the dict it lands in."""
    seen = {}

    def fake(template, **ctx):
        from app.work.docs import RECEIVED_VIA, WO_KINDS, render_template
        seen[template] = render_template(template, doc_css="css/doc.css", wo_kinds=WO_KINDS,
                                         received_via=RECEIVED_VIA, **ctx)
        return b"%PDF-1.7 " + hashlib.sha256(seen[template].encode()).hexdigest().encode()

    monkeypatch.setattr(module, "render_pdf", fake)
    return seen


def test_filters():
    assert rate_pct(D("0.085000")) == "8.5%" and rate_pct("0.045") == "4.5%"
    assert rate_pct(D("0.100000")) == "10%" and rate_pct(D(0)) == "0%"
    assert scope_items("- Cap kit\n\n* Decals\n• Locks  \n") == ["Cap kit", "Decals", "Locks"]


def test_issue_snapshots_numbers_and_freezes(app, signed_in, tdb):
    owner = signed_in("owner")
    b = billable(owner, app)
    page = text(owner.get(f"/work/{b['wo']}"))
    assert "Issue an invoice" in page and "Not billed yet." in page
    form = text(owner.get(f"/work/{b['wo']}/invoice"))
    assert "$123.90" in form and "not collected" in form
    inv_id = issued_id(issue(owner, app, b["wo"]))
    year = today(app).year
    row = db_query(app, "SELECT number, status, subtotal_taxable, subtotal_nontaxable, tax, total, "
                        "amount_paid, balance, tax_status, issue_date, due_date, "
                        "bill_to->>'name', pdf_sha256, report_sha256 FROM invoice")[0]
    assert row[:12] == (f"INV-{year}-0001", "issued", D("3.90"), D("120.00"), D(0),
                        D("123.90"), D(0), D("123.90"), "not_collected", today(app), today(app),
                        "Starlite Lanes Test")
    lines = db_query(app, "SELECT position, kind, labor_mode, qty, unit_price, line_total, taxable, "
                          "unit_cost, warranty_until, group_label FROM invoice_line ORDER BY position")
    assert [r[:8] for r in lines] == [
        (1, "part", None, D(2), D("1.95"), D("3.90"), True, D("0.4000")),
        (2, "labor", "flat", D("1.5"), D(80), D("120.00"), False, None)]
    assert {r[8] for r in lines} == {today(app) + timedelta(days=365)}
    assert lines[0][9].endswith("· Galaga")
    assert db_query(app, "SELECT count(*) FROM wo_line WHERE invoice_id = :i", i=inv_id) == [(2,)]
    # the PDFs are stored by hash and served byte for byte
    with app.app_context():
        pdf_path, report_path = files.path_for(row[12], ".pdf"), files.path_for(row[13], ".pdf")
    data = pdf_path.read_bytes()
    assert data[:5] == b"%PDF-" and hashlib.sha256(data).hexdigest() == row[12]
    assert report_path.exists()
    got = owner.get(f"/invoices/{inv_id}/invoice.pdf")
    assert got.status_code == 200 and got.data == data and got.mimetype == "application/pdf"
    assert owner.get(f"/invoices/{inv_id}/service-report.pdf").status_code == 200
    page = text(owner.get(f"/invoices/{inv_id}"))
    assert f"INV-{year}-0001" in page and "Sales tax not collected" in page
    assert "$3.90 of it is taxable goods, untaxed" in page and "$0.80" in page     # cost
    # audited
    assert db_query(app, "SELECT count(*) > 0 FROM audit_log WHERE table_name = 'invoice'") == \
        [(True,)]

    # billed lines are locked on the work order...
    job_page = text(owner.get(f"/work/jobs/{b['job']}"))
    assert f'title="Billed">INV-{year}-0001</a>' in job_page and "/work/lines/" not in job_page
    wl = db_query(app, "SELECT id FROM wo_line WHERE kind = 'labor'")[0][0]
    resp = owner.post(f"/work/lines/{wl}/edit", data={"description": "x", "unit_price": "1",
                                                       "billable": "y"}, follow_redirects=True)
    assert f"billed on INV-{year}-0001" in text(resp)
    resp = owner.post(f"/work/lines/{wl}/delete", follow_redirects=True)
    assert "void the invoice" in text(resp)
    assert db_query(app, "SELECT unit_price FROM wo_line WHERE id = :i", i=wl) == [(D(80),)]
    # ...and nothing left to bill
    assert "Issue an invoice" not in text(owner.get(f"/work/{b['wo']}"))
    assert "Nothing left to bill" in text(owner.get(f"/work/{b['wo']}/invoice"))

    # the database refuses edits, as the app role and as the schema owner
    with psycopg.connect(libpq(tdb.url("app")), autocommit=True) as c:
        for sql in ("UPDATE invoice_line SET unit_price = 0", "DELETE FROM invoice_line",
                    "DELETE FROM invoice", "UPDATE invoice SET total = 1",
                    "UPDATE invoice SET bill_to = '{}'", "UPDATE invoice SET status = 'draft'",
                    "UPDATE wo_line SET unit_price = 1 WHERE invoice_id IS NOT NULL",
                    "UPDATE wo_line SET invoice_id = NULL",
                    "DELETE FROM wo_line WHERE invoice_id IS NOT NULL",
                    "INSERT INTO invoice_line (invoice_id, position, kind, description, qty, "
                    "unit_price) SELECT id, 9, 'fee', 'x', 1, 1 FROM invoice"):
            with pytest.raises(INTEGRITY):
                c.execute(sql)
    with psycopg.connect(libpq(tdb.url("owner")), autocommit=True) as c:
        for sql in ("UPDATE invoice_line SET unit_price = 0", "DELETE FROM invoice_line",
                    "DELETE FROM invoice", "UPDATE invoice SET tax = 1, total = total + 1",
                    "INSERT INTO invoice_line (invoice_id, position, kind, description, qty, "
                    "unit_price) SELECT id, 9, 'fee', 'x', 1, 1 FROM invoice"):
            with pytest.raises(INTEGRITY):
                c.execute(sql)
        # paid and balance move only with money actually applied (test_payments.py)
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="applied to it"):
            c.execute("UPDATE invoice SET amount_paid = 3.90, balance = 120.00, "
                      "status = 'partially_paid'")
    assert db_query(app, "SELECT status, balance FROM invoice") == [("issued", D("123.90"))]


def test_issue_arithmetic_is_checked_by_postgres(app, signed_in, tdb):
    owner = signed_in("owner")
    cid = customer(owner, "Arithmetic Test")
    with psycopg.connect(libpq(tdb.url("owner"))) as c:
        def draft(**cols):
            base = {"customer_id": cid, "status": "draft", "payment_terms": "due_on_receipt",
                    "bill_to": "{}", "tax_status": "not_collected"}
            base.update(cols)
            names = ", ".join(base)
            marks = ", ".join(f"%({k})s" for k in base)
            inv = c.execute(f"INSERT INTO invoice ({names}) VALUES ({marks}) RETURNING id",
                            base).fetchone()[0]
            c.execute("INSERT INTO invoice_line (invoice_id, position, kind, description, qty, "
                      "unit_price, taxable) VALUES (%s, 1, 'part', 'x', 2, 0.50, true)", (inv,))
            return inv

        issue_sql = ("UPDATE invoice SET status = 'issued', number = 'INV-2026-0999', "
                     "issue_date = current_date, due_date = current_date, issued_at = now(), "
                     "pdf_sha256 = repeat('a', 64), balance = total WHERE id = %s")
        # subtotals that don't match the lines
        inv = draft(subtotal_nontaxable=5, total=5)
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="don't match"):
            c.execute(issue_sql, (inv,))
        c.rollback()
        # collected tax that isn't half-up at the frozen rate: 1.00 × 8.5% = 0.085 -> 0.09
        juris = '{"name": "Testville", "combined_rate": "0.085000"}'
        inv = draft(subtotal_taxable=1, tax=D("0.08"), total=D("1.08"), tax_status="collected",
                    tax_jurisdiction=juris)
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="frozen rate"):
            c.execute(issue_sql, (inv,))
        c.rollback()
        inv = draft(subtotal_taxable=1, tax=D("0.09"), total=D("1.09"), tax_status="collected",
                    tax_jurisdiction=juris)
        c.execute(issue_sql, (inv,))
        c.rollback()
        # a total that doesn't add up never gets in
        with pytest.raises(psycopg.errors.CheckViolation):
            draft(subtotal_nontaxable=1, total=2)


def test_numbers_stay_gapless_when_issuing_fails(app, signed_in, monkeypatch):
    owner = signed_in("owner")
    b = billable(owner, app)
    from app.billing import docs as invoice_docs

    def jammed(*args, **kwargs):
        raise RuntimeError("printer on fire")

    monkeypatch.setattr(invoice_docs, "invoice_pdf", jammed)
    with pytest.raises(RuntimeError):
        issue(owner, app, b["wo"])
    assert db_query(app, "SELECT count(*) FROM invoice") == [(0,)]
    assert db_query(app, "SELECT count(*) FROM invoice_line") == [(0,)]
    assert db_query(app, "SELECT count(*) FROM wo_line WHERE invoice_id IS NOT NULL") == [(0,)]
    assert db_query(app, "SELECT last FROM doc_counter WHERE kind = 'INV'") in ([], [(0,)])
    monkeypatch.undo()
    issued_id(issue(owner, app, b["wo"]))
    assert db_query(app, "SELECT number FROM invoice") == [(f"INV-{today(app).year}-0001",)]


def add_jurisdiction(owner, app, name="Testville", state="0.045", county="0.01", city="0.03",
                     start="2020-01-01", **extra):
    resp = owner.post("/settings/tax/jurisdictions/new", data={
        "name": name, "state_rate": state, "county_rate": county, "city_rate": city,
        "other_rate": "", "effective_from": start, "effective_to": "", "source_note": "OTC table",
        **extra}, follow_redirects=True)
    assert resp.status_code == 200
    rows = db_query(app, "SELECT id FROM tax_jurisdiction WHERE name = :n ORDER BY id DESC", n=name)
    return rows[0][0] if rows else None


def test_tax_when_collecting_is_half_up_and_frozen(app, signed_in, monkeypatch):
    owner = signed_in("owner")
    jid = add_jurisdiction(owner, app)
    assert db_query(app, "SELECT combined_rate FROM tax_jurisdiction") == [(D("0.085"),)]
    resp = owner.post("/settings/tax", data={"collect_sales_tax": "y",
                                              "default_tax_jurisdiction_id": str(jid)},
                      follow_redirects=True)
    assert "New invoices now charge sales tax" in text(resp)
    b = billable(owner, app, part_price="0.50")       # 2 × $0.50 = $1.00 taxable
    form = text(owner.get(f"/work/{b['wo']}/invoice"))
    assert f'selected value="{jid}"' in form and "$0.09" in form
    # no jurisdiction picked, taxable lines: refused
    resp = issue(owner, app, b["wo"])
    assert "pick the tax jurisdiction" in text(resp)
    seen = capture(monkeypatch, __import__("app.billing.docs", fromlist=["x"]))
    inv_id = issued_id(issue(owner, app, b["wo"], jurisdiction_id=str(jid)))
    assert db_query(app, "SELECT subtotal_taxable, tax, total, tax_status, "
                         "tax_jurisdiction->>'combined_rate' FROM invoice") == \
        [(D("1.00"), D("0.09"), D("121.09"), "collected", "0.085000")]    # 0.085 rounds up
    html = seen["docs/invoice.html"]
    assert "Sales tax, Testville 8.5%" in html and "(T)" in html and "not collected" not in html
    # a later rate change doesn't touch the issued invoice
    owner.post(f"/settings/tax/jurisdictions/{jid}", data={
        "name": "Testville", "state_rate": "0.045", "county_rate": "0.01", "city_rate": "0.04",
        "other_rate": "", "effective_from": "2020-01-01", "effective_to": "", "source_note": ""})
    assert db_query(app, "SELECT combined_rate FROM tax_jurisdiction") == [(D("0.095"),)]
    assert db_query(app, "SELECT tax, tax_jurisdiction->>'combined_rate' FROM invoice") == \
        [(D("0.09"), "0.085000")]
    assert "8.5%" in text(owner.get(f"/invoices/{inv_id}"))


def test_tax_off_says_so_and_exempt_customers(app, signed_in, monkeypatch, tdb):
    owner = signed_in("owner")
    b = billable(owner, app)
    seen = capture(monkeypatch, __import__("app.billing.docs", fromlist=["x"]))
    issued_id(issue(owner, app, b["wo"]))
    html = seen["docs/invoice.html"]
    assert "Sales tax not collected." in html and "(T)" not in html and "0.40" not in html
    # collecting, but the customer is exempt
    jid = add_jurisdiction(owner, app)
    owner.post("/settings/tax", data={"collect_sales_tax": "y", "default_tax_jurisdiction_id": ""})
    wo2 = new_wo(owner, b["cid"], b["aid"])
    job2 = db_query(app, "SELECT id FROM wo_job WHERE work_order_id = :w", w=wo2)[0][0]
    owner.post(f"/work/jobs/{job2}/lines/part", data={"part_id": b["pid"], "qty": "1",
                                                       "billable": "y"})
    owner_sql(tdb, "UPDATE customer SET tax_exempt = true WHERE id = %(c)s", c=b["cid"])
    assert "no certificate number" in text(issue(owner, app, wo2, jurisdiction_id=str(jid)))
    owner_sql(tdb, "UPDATE customer SET exempt_cert_no = 'EX-0001', exempt_cert_expires = "
                   "%(d)s WHERE id = %(c)s", c=b["cid"], d=today(app) - timedelta(days=1))
    assert "certificate expired" in text(issue(owner, app, wo2, jurisdiction_id=str(jid)))
    owner_sql(tdb, "UPDATE customer SET exempt_cert_expires = NULL WHERE id = %(c)s", c=b["cid"])
    issued_id(issue(owner, app, wo2, jurisdiction_id=str(jid)))
    assert db_query(app, "SELECT tax_status, tax, exempt_cert_no FROM invoice ORDER BY id DESC "
                         "LIMIT 1") == [("exempt", D(0), "EX-0001")]
    assert "Tax exempt: certificate EX-0001." in seen["docs/invoice.html"]


def test_partial_billing_void_and_rebill(app, signed_in, tdb):
    owner = signed_in("owner")
    b = billable(owner, app)
    aid2 = asset(owner, b["cid"], "Robotron")
    owner.post(f"/work/{b['wo']}/jobs", data={"asset_id": str(aid2), "complaint": "Restore cab"})
    job2 = db_query(app, "SELECT max(id) FROM wo_job")[0][0]
    resp = owner.post(f"/work/jobs/{job2}/lines/fixed", data={
        "description": "Cabinet restoration", "unit_price": "1200", "scope": "Decals\nT-molding",
        "tech_user_id": "None", "billable": "y"}, follow_redirects=True)
    assert "Fixed price added." in text(resp)
    first = line_ids(app, b["wo"], b["job"])
    inv1 = issued_id(issue(owner, app, b["wo"], lines=first))
    page = text(owner.get(f"/work/{b['wo']}"))
    assert "Issue an invoice" in page and "Some lines aren't on an invoice yet." in page
    inv2 = issued_id(issue(owner, app, b["wo"], lines=line_ids(app, b["wo"], job2)))
    year = today(app).year
    assert db_query(app, "SELECT number, total FROM invoice ORDER BY id") == [
        (f"INV-{year}-0001", D("123.90")), (f"INV-{year}-0002", D("1200.00"))]
    # the same lines can't be billed twice
    resp = issue(owner, app, b["wo"], lines=first)
    assert resp.status_code == 200 and "billed or changed meanwhile" in text(resp)
    assert db_query(app, "SELECT count(*) FROM invoice") == [(2,)]
    # void: needs a reason; frees the lines; the number stays used
    resp = owner.post(f"/invoices/{inv1}/void", data={"reason": ""}, follow_redirects=True)
    assert "Say why." in text(resp)
    resp = owner.post(f"/invoices/{inv1}/void", data={"reason": "Wrong customer"},
                      follow_redirects=True)
    assert "is void" in text(resp)
    assert db_query(app, "SELECT status, balance, void_reason FROM invoice WHERE id = :i",
                    i=inv1) == [("void", D(0), "Wrong customer")]
    assert db_query(app, "SELECT count(*) FROM wo_line WHERE invoice_id = :i", i=inv1) == [(0,)]
    assert "already void" in text(owner.post(f"/invoices/{inv1}/void", data={"reason": "x"},
                                             follow_redirects=True))
    with psycopg.connect(libpq(tdb.url("owner")), autocommit=True) as c:
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="void"):
            c.execute("UPDATE invoice SET void_reason = 'changed my mind' WHERE id = %s", (inv1,))
    # freed lines edit again and bill on a new number
    wl = db_query(app, "SELECT id FROM wo_line WHERE kind = 'labor' AND labor_mode = 'flat'")[0][0]
    owner.post(f"/work/lines/{wl}/edit", data={"description": "Bench labor", "qty": "2",
                                               "unit_price": "80", "billable": "y"})
    inv3 = issued_id(issue(owner, app, b["wo"], lines=first))
    assert db_query(app, "SELECT number, total FROM invoice WHERE id = :i", i=inv3) == \
        [(f"INV-{year}-0003", D("163.90"))]
    listing = text(owner.get("/invoices"))
    assert f"INV-{year}-0001" not in listing and "$1,363.90" in listing    # open only
    assert f"INV-{year}-0001" in text(owner.get("/invoices?all=1"))
    assert inv2


def test_backdating_needs_a_reason_and_sets_the_year(app, signed_in):
    owner = signed_in("owner")
    b = billable(owner, app)
    aid2 = asset(owner, b["cid"], "Robotron")
    owner.post(f"/work/{b['wo']}/jobs", data={"asset_id": str(aid2), "complaint": "Dead"})
    job2 = db_query(app, "SELECT max(id) FROM wo_job")[0][0]
    owner.post(f"/work/jobs/{job2}/lines/fixed", data={"description": "Repair",
                                                       "unit_price": "90", "billable": "y",
                                                       "tech_user_id": "None"})
    t = today(app)
    first = line_ids(app, b["wo"], b["job"])
    assert "can't be in the future" in text(issue(owner, app, b["wo"], lines=first,
                                                  on=t + timedelta(days=1)))
    assert "say why" in text(issue(owner, app, b["wo"], lines=first, on=t - timedelta(days=10)))
    assert db_query(app, "SELECT count(*) FROM invoice") == [(0,)]
    earlier = t - timedelta(days=10)
    issued_id(issue(owner, app, b["wo"], lines=first, on=earlier,
                    backdate_reason="Billed on paper first"))
    row = db_query(app, "SELECT number, issue_date, due_date, backdate_reason, "
                        "issued_at::date >= issue_date FROM invoice")[0]
    assert row == (f"INV-{earlier.year}-0001", earlier, earlier, "Billed on paper first", True)
    last_year = t.replace(year=t.year - 1, month=12, day=31)
    issued_id(issue(owner, app, b["wo"], lines=line_ids(app, b["wo"], job2), on=last_year,
                    backdate_reason="Last year's job"))
    assert db_query(app, "SELECT number FROM invoice ORDER BY id DESC LIMIT 1") == \
        [(f"INV-{t.year - 1}-0001",)]


def test_fixed_price_from_estimate_to_invoice(app, signed_in, tdb, monkeypatch):
    owner = signed_in("owner")
    cid = customer(owner, "Restoration Test")
    aid = asset(owner, cid, "Dig Dug")
    est_id = new_estimate(owner, cid, aid, summary="Restore two cabinets")
    job = est_job(app, est_id)
    resp = owner.post(f"/estimates/{est_id}/lines/fixed", data={
        "estimate_job_id": job, "description": "Restoration, labor only", "unit_price": "2500",
        "scope": "- Cap kit\n- Decal replacement\n- Lock replacement\nCustomer supplies all parts"},
        follow_redirects=True)
    page = text(resp)
    assert "Added." in page and "fixed price" in page and "Decal replacement" in page
    assert "h labor" not in page                          # a fixed price isn't hours
    assert db_query(app, "SELECT kind, labor_mode, qty, unit_price, taxable, warranty_days "
                         "FROM estimate_line") == [("labor", "fixed", D(1), D(2500), False, 365)]
    seen = capture(monkeypatch, __import__("app.estimates.docs", fromlist=["x"]))
    owner.get(f"/estimates/{est_id}/estimate.pdf")
    html = seen["docs/estimate.html"]
    assert "Fixed price" in html and "<li>Lock replacement</li>" in html
    # the database keeps a fixed price at qty 1
    with psycopg.connect(libpq(tdb.url("owner")), autocommit=True) as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("UPDATE estimate_line SET qty = 2")
    # editing keeps it fixed
    line = db_query(app, "SELECT id FROM estimate_line")[0][0]
    owner.post(f"/estimates/lines/{line}/edit", data={
        "description": "Restoration, labor only", "qty": "1", "unit_price": "2400",
        "scope": "Cap kit\nDecals", "labor_mode": "flat"})
    assert db_query(app, "SELECT labor_mode, qty, unit_price, scope FROM estimate_line") == \
        [("fixed", D(1), D(2400), "Cap kit\nDecals")]
    # approve and convert: the scope carries over
    owner.post(f"/estimates/{est_id}/verbal", data={"name": "Pat Customer", "note": "by phone"})
    owner.post(f"/estimates/{est_id}/convert", data={"kind": "bench"})
    wo_id = db_query(app, "SELECT work_order_id FROM estimate")[0][0]
    assert db_query(app, "SELECT labor_mode, qty, unit_price, scope FROM wo_line") == \
        [("fixed", D(1), D(2400), "Cap kit\nDecals")]
    job_id = db_query(app, "SELECT id FROM wo_job WHERE work_order_id = :w", w=wo_id)[0][0]
    owner.post(f"/work/jobs/{job_id}/lines/customer-part", data={"description": "Cap kit (theirs)",
                                                                 "qty": "1"})
    owner.post(f"/work/jobs/{job_id}/time", data={"started_at": "2026-10-08T15:00",
                                                  "minutes": "120", "billable": "y"})
    job_page = text(owner.get(f"/work/jobs/{job_id}"))
    assert "Fixed price $2,400.00 · 2 h logged → $1,200.00/h so far." in job_page
    seen = capture(monkeypatch, __import__("app.billing.docs", fromlist=["x"]))
    issued_id(issue(owner, app, wo_id))
    rows = db_query(app, "SELECT labor_mode, qty, line_total, customer_supplied, warranty_until, "
                         "scope FROM invoice_line ORDER BY position")
    assert rows == [("fixed", D(1), D("2400.00"), False, today(app) + timedelta(days=90),
                     "Cap kit\nDecals"),
                    (None, D(1), D("0.00"), True, None, None)]
    assert db_query(app, "SELECT total FROM invoice") == [(D("2400.00"),)]
    html = seen["docs/invoice.html"]
    assert "Fixed price" in html and "<li>Decals</li>" in html
    assert "Your part: no charge, no warranty" in html


def test_counter_sale(app, signed_in):
    owner = signed_in("owner")
    cid = customer(owner, "Walk-in Test")
    pid = add_part(owner)
    owner.post(f"/parts/{pid}/receive", data={"qty": "5", "unit_cost": "0.40", "vendor_id": "None"})
    wo_id = new_wo(owner, cid, kind="counter", complaint="Bought caps")
    job = db_query(app, "SELECT id FROM wo_job WHERE work_order_id = :w", w=wo_id)[0][0]
    owner.post(f"/work/jobs/{job}/lines/part", data={"part_id": pid, "qty": "3", "billable": "y"})
    issued_id(issue(owner, app, wo_id))
    assert db_query(app, "SELECT report_sha256, total FROM invoice") == [(None, D("5.85"))]
    assert db_query(app, "SELECT group_label FROM invoice_line") == [("Counter sale",)]


def test_who_sees_invoices(app, signed_in):
    owner, tech, viewer = signed_in("owner"), signed_in("tech"), signed_in("viewer")
    b = billable(owner, app)
    inv_id = issued_id(issue(owner, app, b["wo"]))
    number = db_query(app, "SELECT number FROM invoice")[0][0]
    for path in ("/invoices", f"/invoices/{inv_id}", f"/invoices/{inv_id}/invoice.pdf",
                 f"/invoices/{inv_id}/service-report.pdf", f"/work/{b['wo']}/invoice",
                 "/settings/tax"):
        assert tech.get(path).status_code == 403, path
    job_page = text(tech.get(f"/work/jobs/{b['job']}"))
    assert "billed" in job_page and number not in job_page
    wo_page = text(tech.get(f"/work/{b['wo']}"))
    assert "Invoices" not in wo_page and number not in wo_page
    assert ">Invoices<" not in text(tech.get("/"))
    # viewers read, never act
    assert number in text(viewer.get("/invoices")) and ">Invoices<" in text(viewer.get("/"))
    assert "Cost" in text(viewer.get(f"/invoices/{inv_id}"))
    assert number in text(viewer.get(f"/customers/{b['cid']}"))
    assert "Void this invoice" not in text(viewer.get(f"/invoices/{inv_id}"))
    for path, data in ((f"/work/{b['wo']}/invoice", {}), (f"/invoices/{inv_id}/void",
                                                          {"reason": "x"}),
                       (f"/invoices/{inv_id}/send", {})):
        assert viewer.post(path, data=data).status_code == 403, path
    assert viewer.get(f"/work/{b['wo']}/invoice").status_code == 403
    assert number not in text(tech.get(f"/customers/{b['cid']}"))


def test_customer_link_to_an_invoice(app, signed_in):
    owner = signed_in("owner")
    owner.post("/settings/", data={"business_name": "ArcadeTech Tracker",
                                   "doc_accent_color": "#b0126f", "estimate_link_days": "30",
                                   "payment_instructions": "Zelle: pay@example.com"})
    b = billable(owner, app)
    inv_id = issued_id(issue(owner, app, b["wo"]))
    number, sha, report = db_query(app, "SELECT number, pdf_sha256, report_sha256 FROM invoice")[0]
    page = text(owner.post(f"/invoices/{inv_id}/send"))
    token = re.search(r"/d/([A-Za-z0-9_-]{43})", page).group(1)
    assert db_query(app, "SELECT token_sha256 FROM doc_link WHERE invoice_id = :i", i=inv_id) == \
        [(hashlib.sha256(token.encode()).hexdigest(),)]
    anon = app.test_client()
    resp = anon.get(f"/d/{token}")
    public = text(resp)
    assert resp.status_code == 200 and number in public
    assert "How to pay" in public and "Zelle: pay@example.com" in public
    assert "Balance due" in public and "$123.90" in public and "Sales tax not collected." in public
    assert "0.40" not in public and "0.4120" not in public                 # never costs
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert "noindex" in resp.headers["X-Robots-Tag"]
    assert db_query(app, "SELECT view_count FROM doc_link") == [(1,)]
    pdf = anon.get(f"/d/{token}/invoice.pdf")
    assert pdf.status_code == 200 and hashlib.sha256(pdf.data).hexdigest() == sha
    assert anon.get(f"/d/{token}/service-report.pdf").status_code == 200 and report
    # an invoice link opens nothing estimate-shaped
    for path in (f"/d/{token}/estimate.pdf", f"/d/{token}/signature.png"):
        assert anon.get(path).status_code == 404, path
    assert anon.post(f"/d/{token}/approve", data={"name": "x"}).status_code == 404
    assert anon.post(f"/d/{token}/decline", data={}).status_code == 404
    assert anon.get(f"/d/{'A' * 43}/invoice.pdf").status_code == 404
    # void: the page says so and stops asking for money
    owner.post(f"/invoices/{inv_id}/void", data={"reason": "Duplicate"})
    public = text(anon.get(f"/d/{token}"))
    assert "This invoice was voided" in public and "How to pay" not in public
    # withdrawn: gone
    link = db_query(app, "SELECT id FROM doc_link")[0][0]
    owner.post(f"/invoices/links/{link}/revoke")
    assert anon.get(f"/d/{token}").status_code == 404
    assert anon.get(f"/d/{token}/invoice.pdf").status_code == 404


def test_tax_settings_and_jurisdictions(app, signed_in):
    owner = signed_in("owner")
    page = text(owner.get("/settings/tax"))
    assert "Off: invoices say" in page and "None yet." in page
    jid = add_jurisdiction(owner, app, name="Testville")
    assert db_query(app, "SELECT state_rate, county_rate, city_rate, other_rate, combined_rate "
                         "FROM tax_jurisdiction") == [(D("0.045"), D("0.01"), D("0.03"), D(0),
                                                       D("0.085"))]
    assert "8.5%" in text(owner.get("/settings/tax"))
    resp = owner.post("/settings/tax/jurisdictions/new", data={
        "name": "Typo", "state_rate": "0.20", "county_rate": "0.06", "city_rate": "",
        "other_rate": "", "effective_from": "2020-01-01"}, follow_redirects=True)
    assert "25% or more" in text(resp)
    resp = owner.post("/settings/tax/jurisdictions/new", data={
        "name": "Testville", "state_rate": "0.045", "county_rate": "", "city_rate": "",
        "other_rate": "", "effective_from": "2020-01-01"}, follow_redirects=True)
    assert "already has a rate starting that day" in text(resp)
    assert db_query(app, "SELECT count(*) FROM tax_jurisdiction") == [(1,)]
    # a site names its jurisdiction; the issue form starts from it
    sid = site(app, owner, customer(owner, "Site Test"), "Main St")
    cid = db_query(app, "SELECT customer_id FROM site WHERE id = :s", s=sid)[0][0]
    resp = owner.post(f"/sites/{sid}/edit", data={"name": "Main St", "tax_jurisdiction_id": str(jid),
                                                  "active": "y", "site_contact_id": "None"})
    assert resp.status_code == 302
    assert db_query(app, "SELECT tax_jurisdiction_id FROM site WHERE id = :s", s=sid) == [(jid,)]
    wo = new_wo(owner, cid, kind="on_site", site_id=str(sid))
    job = db_query(app, "SELECT id FROM wo_job WHERE work_order_id = :w", w=wo)[0][0]
    owner.post(f"/work/jobs/{job}/lines/fixed", data={"description": "Visit", "unit_price": "95",
                                                      "billable": "y", "tech_user_id": "None"})
    assert f'selected value="{jid}"' in text(owner.get(f"/work/{wo}/invoice"))
