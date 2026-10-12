"""Money in: payments (partial, final, over), credit on account, deposits applied at issue,
reversals, refunds, credit memos, statements, receipts, who may touch it, and a randomized
run checking that every balance always equals what's applied."""

import hashlib
import random
import re
from datetime import timedelta
from decimal import Decimal as D

import psycopg
import pytest
from sqlalchemy import select
from test_billing import billable, capture, issue, issued_id, line_ids, today
from test_estimates import est_job, new_estimate
from test_work import new_wo, text

from app.billing import payments
from app.billing.service import InvoiceError
from app.extensions import db
from app.models import CreditMemo, Customer, Invoice, Payment
from app.work import files
from conftest import db_query, libpq


def pay(client, app, inv_id, amount, method="zelle", **extra):
    return client.post(f"/invoices/{inv_id}/payment", data={
        "amount": amount, "method": method, "received_on": str(today(app)), "reference": "",
        "note": "", **extra}, follow_redirects=True)


def invoice_row(app, inv_id):
    return db_query(app, "SELECT status, amount_paid, amount_credited, balance FROM invoice "
                         "WHERE id = :i", i=inv_id)[0]


def fixed_invoice(owner, app, cid, price, **extra):
    """Another WO for the customer with one fixed-price line, issued."""
    wo = new_wo(owner, cid)
    job = db_query(app, "SELECT id FROM wo_job WHERE work_order_id = :w", w=wo)[0][0]
    owner.post(f"/work/jobs/{job}/lines/fixed", data={"description": "Repair", "unit_price": price,
                                                      "billable": "y", "tech_user_id": "None"})
    return wo, issued_id(issue(owner, app, wo, **extra))


def check_money(app):
    """The invariants: every invoice's paid, credited, balance and status are what's
    applied to it; no source is overspent; credits never exceed what an invoice billed."""
    bad = db_query(app, """
        SELECT i.number FROM invoice i
        LEFT JOIN LATERAL (
            SELECT coalesce(sum(amount) FILTER (WHERE payment_id IS NOT NULL), 0) AS paid,
                   coalesce(sum(amount) FILTER (WHERE credit_memo_id IS NOT NULL), 0) AS credited
            FROM payment_allocation a WHERE a.invoice_id = i.id) s ON true
        WHERE i.status NOT IN ('draft', 'void') AND (
              i.amount_paid <> s.paid OR i.amount_credited <> s.credited
           OR i.balance <> i.total - s.paid - s.credited OR i.balance < 0
           OR i.status <> CASE WHEN i.balance = 0 THEN 'paid'
                               WHEN s.paid + s.credited > 0 THEN 'partially_paid'
                               ELSE 'issued' END)""")
    assert bad == [], f"invoice money disagrees: {bad}"
    over = db_query(app, """
        SELECT p.id FROM payment p WHERE p.kind = 'receipt' AND NOT EXISTS (
            SELECT 1 FROM payment r WHERE r.reverses_payment_id = p.id) AND
          p.amount - coalesce((SELECT sum(amount) FROM payment_allocation WHERE payment_id = p.id), 0)
          + coalesce((SELECT sum(amount) FROM payment WHERE refund_of_payment_id = p.id), 0) < 0""")
    assert over == [], f"payments overspent: {over}"
    unbalanced = db_query(app, """
        SELECT p.id FROM payment p WHERE p.kind = 'reversal' AND
          (SELECT coalesce(sum(amount), 0) FROM payment_allocation
            WHERE payment_id IN (p.id, p.reverses_payment_id)) <> 0""")
    assert unbalanced == [], f"reversals that don't net out: {unbalanced}"
    cm_over = db_query(app, """
        SELECT c.id FROM credit_memo c WHERE c.status = 'issued' AND
          c.total - coalesce((SELECT sum(amount) FROM payment_allocation
                              WHERE credit_memo_id = c.id), 0)
          + coalesce((SELECT sum(amount) FROM payment WHERE refund_of_credit_memo_id = c.id), 0) < 0
          OR (SELECT sum(total) FROM credit_memo WHERE invoice_id = c.invoice_id
              AND status = 'issued') > (SELECT total FROM invoice WHERE id = c.invoice_id)""")
    assert cm_over == [], f"credit memos overspent: {cm_over}"


# --- the exit test: partial + final payment closes the invoice -------------------------------

def test_partial_then_final_payment_closes_the_invoice(app, signed_in, tdb, monkeypatch):
    owner = signed_in("owner")
    b = billable(owner, app)
    inv = issued_id(issue(owner, app, b["wo"]))
    page = text(owner.post(f"/invoices/{inv}/send"))
    token = re.search(r"/d/([A-Za-z0-9_-]{43})", page).group(1)
    page = text(pay(owner, app, inv, "50", method="check", reference="1042"))
    assert "Recorded $50.00." in page
    assert invoice_row(app, inv) == ("partially_paid", D(50), D(0), D("73.90"))
    page = text(pay(owner, app, inv, "73.90"))
    assert invoice_row(app, inv) == ("paid", D("123.90"), D(0), D(0))
    assert "Record a payment" not in page and "Paid in full" not in page   # staff page
    assert db_query(app, "SELECT kind, amount, method, reference FROM payment ORDER BY id") == [
        ("receipt", D(50), "check", "1042"), ("receipt", D("73.90"), "zelle", None)]
    check_money(app)
    # paid: the customer's link now runs out within 90 days
    assert db_query(app, "SELECT expires_at <= now() + interval '90 days 1 minute' "
                         "FROM doc_link") == [(True,)]
    # receipts say what was still owed after each payment, and never change
    seen = capture(monkeypatch, __import__("app.billing.docs", fromlist=["x"]))
    first, second = [r[0] for r in db_query(app, "SELECT id FROM payment ORDER BY id")]
    assert owner.get(f"/payments/{first}/receipt.pdf").status_code == 200
    html = seen["docs/receipt.html"]
    assert "$50.00" in html and "$73.90" in html and "Check" in html and "1042" in html
    owner.get(f"/payments/{second}/receipt.pdf")
    assert "$0.00" in seen["docs/receipt.html"]
    # the customer's page lists them, each with its receipt
    anon = app.test_client()
    public = text(anon.get(f"/d/{token}"))
    assert "Payments received" in public and "How to pay" not in public
    assert anon.get(f"/d/{token}/receipt/{first}.pdf").status_code == 200
    # nothing more can be paid, and the database agrees
    assert "nothing to pay" in text(pay(owner, app, inv, "1"))
    with psycopg.connect(libpq(tdb.url("app")), autocommit=True) as c:
        for sql in ("UPDATE payment SET amount = 1", "DELETE FROM payment",
                    "UPDATE payment_allocation SET amount = 1", "DELETE FROM payment_allocation",
                    "INSERT INTO payment_allocation (invoice_id, payment_id, amount) "
                    "SELECT invoice_id, payment_id, 1 FROM payment_allocation LIMIT 1"):
            with pytest.raises((psycopg.errors.InsufficientPrivilege,
                                psycopg.errors.IntegrityConstraintViolation)):
                c.execute(sql)
    with psycopg.connect(libpq(tdb.url("owner")), autocommit=True) as c:
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="applied to it"):
            c.execute("UPDATE invoice SET amount_paid = 0, balance = 123.90, status = 'issued'")


def test_overpayment_is_credit_and_goes_on_the_next_invoice(app, signed_in, monkeypatch):
    owner = signed_in("owner")
    b = billable(owner, app)
    first = issued_id(issue(owner, app, b["wo"]))
    page = text(pay(owner, app, first, "150"))
    assert "$26.10 is credit on the customer's account." in page
    assert invoice_row(app, first)[0] == "paid"
    assert "$26.10" in text(owner.get(f"/customers/{b['cid']}"))
    wo = new_wo(owner, b["cid"])
    job = db_query(app, "SELECT id FROM wo_job WHERE work_order_id = :w", w=wo)[0][0]
    owner.post(f"/work/jobs/{job}/lines/fixed", data={"description": "Repair", "unit_price": "50",
                                                      "billable": "y", "tech_user_id": "None"})
    form = text(owner.get(f"/work/{wo}/invoice"))
    assert "$26.10 credit" in form and "Apply the customer's credit on account" in form
    seen = capture(monkeypatch, __import__("app.billing.docs", fromlist=["x"]))
    second = issued_id(issue(owner, app, wo, apply_credit="y"))
    assert invoice_row(app, second) == ("partially_paid", D("26.10"), D(0), D("23.90"))
    html = seen["docs/invoice.html"]          # the frozen PDF shows it paid
    assert "Paid" in html and "$26.10" in html and "$23.90" in html
    with app.app_context():
        assert payments.credit_available(b["cid"]) == 0
    check_money(app)


def test_deposit_on_an_estimate_goes_on_its_invoice_first(app, signed_in):
    owner = signed_in("owner")
    from test_assets import asset, customer
    cid = customer(owner, "Deposit Test")
    aid = asset(owner, cid, "Joust")
    est = new_estimate(owner, cid, aid, deposit_required="50")
    owner.post(f"/estimates/{est}/lines/fixed", data={"estimate_job_id": est_job(app, est),
                                                      "description": "Restore", "unit_price": "30"})
    owner.post(f"/estimates/{est}/verbal", data={"name": "Pat Customer", "note": ""})
    owner.post(f"/estimates/{est}/convert", data={"kind": "bench"})
    wo = db_query(app, "SELECT work_order_id FROM estimate")[0][0]
    page = text(owner.get(f"/estimates/{est}"))
    assert "Record the deposit" in page and "$50.00 asked" in page
    # an older, unrelated credit, then the deposit
    owner.post(f"/customers/{cid}/payment", data={
        "amount": "10", "method": "cash", "received_on": str(today(app) - timedelta(days=3)),
        "estimate_id": "None", "action": "record"})
    resp = owner.post(f"/customers/{cid}/payment", data={
        "amount": "50", "method": "zelle", "received_on": str(today(app)),
        "estimate_id": str(est), "action": "record"}, follow_redirects=True)
    assert "Recorded $50.00." in text(resp) and "Deposit on" in text(resp)
    assert "$50.00 received" in text(owner.get(f"/estimates/{est}"))
    inv = issued_id(issue(owner, app, wo, apply_credit="y"))
    assert invoice_row(app, inv) == ("paid", D(30), D(0), D(0))
    deposit = db_query(app, "SELECT id FROM payment WHERE estimate_id IS NOT NULL")[0][0]
    assert db_query(app, "SELECT payment_id, amount FROM payment_allocation") == [(deposit, D(30))]
    with app.app_context():
        assert payments.credit_available(cid) == D(30)        # $20 of the deposit + the $10
    check_money(app)


def test_reversing_a_bounced_check(app, signed_in, tdb):
    owner = signed_in("owner")
    b = billable(owner, app)
    inv = issued_id(issue(owner, app, b["wo"]))
    pay(owner, app, inv, "123.90", method="check", reference="2001")
    receipt = db_query(app, "SELECT id FROM payment")[0][0]
    resp = owner.post(f"/payments/{receipt}/reverse", data={"reason": "", "received_on":
                                                            str(today(app))}, follow_redirects=True)
    assert "Say why." in text(resp)
    resp = owner.post(f"/payments/{receipt}/reverse", data={
        "reason": "Bounced: insufficient funds", "received_on": str(today(app))},
        follow_redirects=True)
    assert f"Payment #{receipt} reversed" in text(resp)
    assert invoice_row(app, inv) == ("issued", D(0), D(0), D("123.90"))
    assert db_query(app, "SELECT kind, amount, reverses_payment_id FROM payment ORDER BY id") == [
        ("receipt", D("123.90"), None), ("reversal", D("-123.90"), receipt)]
    assert "already reversed" in text(owner.post(f"/payments/{receipt}/reverse", data={
        "reason": "again", "received_on": str(today(app))}, follow_redirects=True))
    assert "reversed" in text(owner.get(f"/payments/{receipt}"))
    check_money(app)
    # the database: one reversal per payment; a reversed payment pays nothing more
    with psycopg.connect(libpq(tdb.url("owner")), autocommit=True) as c:
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute("INSERT INTO payment (customer_id, kind, received_on, amount, method, note, "
                      "reverses_payment_id) VALUES (%s, 'reversal', current_date, -123.90, "
                      "'check', 'x', %s)", (b["cid"], receipt))
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="reversed"):
            c.execute("INSERT INTO payment_allocation (invoice_id, payment_id, amount) "
                      "VALUES (%s, %s, 1)", (inv, receipt))
    # a reversal that doesn't take everything back fails at commit
    pay(owner, app, inv, "20")
    second = db_query(app, "SELECT max(id) FROM payment WHERE kind = 'receipt'")[0][0]
    with psycopg.connect(libpq(tdb.url("owner"))) as c:
        c.execute("INSERT INTO payment (customer_id, kind, received_on, amount, method, note, "
                  "reverses_payment_id) VALUES (%s, 'reversal', current_date, -20, 'zelle', "
                  "'x', %s)", (b["cid"], second))
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="take back all"):
            c.commit()
    # with nothing left applied, it can be voided after all
    owner.post(f"/payments/{second}/reverse", data={"reason": "Wrong invoice",
                                                     "received_on": str(today(app))})
    resp = owner.post(f"/invoices/{inv}/void", data={"reason": "Customer cancelled"},
                      follow_redirects=True)
    assert "is void" in text(resp)
    check_money(app)


def test_refunds_come_out_of_credit(app, signed_in, tdb):
    owner = signed_in("owner")
    b = billable(owner, app)
    inv = issued_id(issue(owner, app, b["wo"]))
    pay(owner, app, inv, "150")
    receipt = db_query(app, "SELECT id FROM payment")[0][0]
    resp = owner.post(f"/payments/{receipt}/refund", data={"amount": "30", "method": "zelle",
                                                           "reason": "Overpaid"},
                      follow_redirects=True)
    assert "Only 26.10 of it is left to refund." in text(resp)
    resp = owner.post(f"/payments/{receipt}/refund", data={"amount": "20", "method": "zelle",
                                                           "reason": "Overpaid", "reference": "Z9"},
                      follow_redirects=True)
    assert "Refund of $20.00 recorded." in text(resp)
    with app.app_context():
        assert payments.credit_available(b["cid"]) == D("6.10")
    assert "was refunded" in text(owner.post(f"/payments/{receipt}/reverse", data={
        "reason": "x", "received_on": str(today(app))}, follow_redirects=True))
    with psycopg.connect(libpq(tdb.url("owner")), autocommit=True) as c:
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="left to refund"):
            c.execute("INSERT INTO payment (customer_id, kind, received_on, amount, method, note, "
                      "refund_of_payment_id) VALUES (%s, 'refund', current_date, -7, 'cash', "
                      "'x', %s)", (b["cid"], receipt))
    check_money(app)


def test_credit_memos(app, signed_in, tdb):
    owner = signed_in("owner")
    b = billable(owner, app)
    inv = issued_id(issue(owner, app, b["wo"]))
    form = text(owner.get(f"/invoices/{inv}/credit-memo"))
    assert "Up to $123.90 can still be credited" in form
    data = {"reason": "Wrong cap value supplied", "lines-0-description": "Caps returned",
            "lines-0-amount": "3.90", "lines-0-taxable": "y"}
    resp = owner.post(f"/invoices/{inv}/credit-memo", data=data, follow_redirects=True)
    year = today(app).year
    assert f"Issued CM-{year}-0001." in text(resp)
    assert db_query(app, "SELECT number, total, tax, tax_rate, subtotal_taxable FROM credit_memo") \
        == [(f"CM-{year}-0001", D("3.90"), D(0), None, D("3.90"))]
    assert invoice_row(app, inv) == ("partially_paid", D(0), D("3.90"), D(120))
    sha = db_query(app, "SELECT pdf_sha256 FROM credit_memo")[0][0]
    cm_id = db_query(app, "SELECT id FROM credit_memo")[0][0]
    got = owner.get(f"/credit-memos/{cm_id}/credit-memo.pdf")
    assert got.status_code == 200 and hashlib.sha256(got.data).hexdigest() == sha
    with app.app_context():
        assert files.path_for(sha, ".pdf").exists()
    # can't credit more than was billed
    resp = owner.post(f"/invoices/{inv}/credit-memo", data={
        "reason": "x", "lines-0-description": "Everything", "lines-0-amount": "125"},
        follow_redirects=True)
    assert "Only $120.00" in text(resp) or "Only 120.00" in text(resp)
    # paid off, then credited again: the credit sits on the account
    pay(owner, app, inv, "120")
    owner.post(f"/invoices/{inv}/credit-memo", data={
        "reason": "Goodwill", "lines-0-description": "Labor adjustment", "lines-0-amount": "20"})
    cm2 = db_query(app, "SELECT max(id) FROM credit_memo")[0][0]
    with app.app_context():
        assert payments.credit_available(b["cid"]) == D(20)
    assert "$20.00" in text(owner.get(f"/credit-memos/{cm2}"))
    owner.post(f"/credit-memos/{cm2}/refund", data={"amount": "20", "method": "check",
                                                    "reason": "Mailed a check"})
    with app.app_context():
        assert payments.credit_available(b["cid"]) == 0
    check_money(app)
    # frozen in the database
    with psycopg.connect(libpq(tdb.url("app")), autocommit=True) as c:
        for sql in ("UPDATE credit_memo SET reason = 'x'", "DELETE FROM credit_memo",
                    "UPDATE credit_memo_line SET unit_price = 1", "DELETE FROM credit_memo_line"):
            with pytest.raises((psycopg.errors.IntegrityConstraintViolation,
                                psycopg.errors.InsufficientPrivilege)):
                c.execute(sql)
    with psycopg.connect(libpq(tdb.url("owner"))) as c:
        # $23.90 credited so far on a $123.90 invoice: $101 more is $1 too much
        new = c.execute("INSERT INTO credit_memo (customer_id, invoice_id, status, reason, "
                        "subtotal_nontaxable, total) VALUES (%s, %s, 'draft', 'x', 101, 101) "
                        "RETURNING id", (b["cid"], inv)).fetchone()[0]
        c.execute("INSERT INTO credit_memo_line (credit_memo_id, position, description, qty, "
                  "unit_price) VALUES (%s, 1, 'x', 1, 101)", (new,))
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="more than it billed"):
            c.execute("UPDATE credit_memo SET status = 'issued', number = 'CM-2026-0999', "
                      "issue_date = current_date, issued_at = now(), pdf_sha256 = repeat('a', 64) "
                      "WHERE id = %s", (new,))


def test_credit_memo_gives_back_collected_tax(app, signed_in):
    owner = signed_in("owner")
    from test_billing import add_jurisdiction
    jid = add_jurisdiction(owner, app)                    # 8.5%
    owner.post("/settings/tax", data={"collect_sales_tax": "y",
                                      "default_tax_jurisdiction_id": str(jid)})
    b = billable(owner, app, part_price="0.50")
    inv = issued_id(issue(owner, app, b["wo"], jurisdiction_id=str(jid)))
    owner.post(f"/invoices/{inv}/credit-memo", data={
        "reason": "Part returned", "lines-0-description": "Caps", "lines-0-amount": "1.00",
        "lines-0-taxable": "y"})
    assert db_query(app, "SELECT tax_rate, tax, total FROM credit_memo") == \
        [(D("0.085"), D("0.09"), D("1.09"))]
    check_money(app)


def test_customer_payment_split_and_statement(app, signed_in, monkeypatch):
    owner = signed_in("owner")
    b = billable(owner, app)
    a = issued_id(issue(owner, app, b["wo"]))
    _, inv_b = fixed_invoice(owner, app, b["cid"], "50")
    page = text(owner.post(f"/customers/{b['cid']}/payment", data={
        "amount": "150", "method": "zelle", "received_on": str(today(app)),
        "estimate_id": "None", "action": "fill"}))
    assert f'name="apply-{a}" value="123.90"' in page
    assert f'name="apply-{inv_b}" value="26.10"' in page
    assert db_query(app, "SELECT count(*) FROM payment") == [(0,)]
    base = {"amount": "150", "method": "zelle", "received_on": str(today(app)),
            "estimate_id": "None", "action": "record"}
    resp = owner.post(f"/customers/{b['cid']}/payment",
                      data={**base, f"apply-{a}": "abc"}, follow_redirects=True)
    assert "dollars and cents" in text(resp)
    resp = owner.post(f"/customers/{b['cid']}/payment",
                      data={**base, f"apply-{a}": "123.90", f"apply-{inv_b}": "50",
                            "amount": "100"}, follow_redirects=True)
    assert "more than was received" in text(resp)
    resp = owner.post(f"/customers/{b['cid']}/payment",
                      data={**base, f"apply-{a}": "100", f"apply-{inv_b}": "50"},
                      follow_redirects=True)
    assert "Recorded $150.00." in text(resp)
    assert invoice_row(app, a) == ("partially_paid", D(100), D(0), D("23.90"))
    assert invoice_row(app, inv_b)[0] == "paid"
    # a statement: one late invoice, aged
    wo3 = new_wo(owner, b["cid"])
    job3 = db_query(app, "SELECT id FROM wo_job WHERE work_order_id = :w", w=wo3)[0][0]
    owner.post(f"/work/jobs/{job3}/lines/fixed", data={"description": "Old job", "unit_price": "40",
                                                       "billable": "y", "tech_user_id": "None"})
    issued_id(issue(owner, app, wo3, on=today(app) - timedelta(days=45),
                    backdate_reason="Billed on paper"))
    seen = capture(monkeypatch, __import__("app.billing.docs", fromlist=["x"]))
    assert owner.get(f"/customers/{b['cid']}/statement.pdf").status_code == 200
    html = seen["docs/statement.html"]
    assert "31–60 days late" in html and "$40.00" in html and "$23.90" in html
    assert "$63.90" in html                                # amount due
    check_money(app)


def test_who_can_touch_money(app, signed_in):
    owner, tech, viewer = signed_in("owner"), signed_in("tech"), signed_in("viewer")
    b = billable(owner, app)
    inv = issued_id(issue(owner, app, b["wo"]))
    pay(owner, app, inv, "50")
    owner.post(f"/invoices/{inv}/credit-memo", data={
        "reason": "x", "lines-0-description": "Adjustment", "lines-0-amount": "5"})
    pid = db_query(app, "SELECT id FROM payment")[0][0]
    cm = db_query(app, "SELECT id FROM credit_memo")[0][0]
    reads = ["/payments", f"/payments/{pid}", f"/payments/{pid}/receipt.pdf",
             f"/credit-memos/{cm}", f"/credit-memos/{cm}/credit-memo.pdf",
             f"/customers/{b['cid']}/statement.pdf"]
    writes = [(f"/invoices/{inv}/payment", {"amount": "1", "method": "cash",
                                            "received_on": str(today(app))}),
              (f"/invoices/{inv}/apply-credit", {}),
              (f"/invoices/{inv}/credit-memo", {"reason": "x"}),
              (f"/payments/{pid}/reverse", {"reason": "x", "received_on": str(today(app))}),
              (f"/payments/{pid}/refund", {"amount": "1", "method": "cash", "reason": "x"}),
              (f"/customers/{b['cid']}/payment", {"amount": "1"})]
    for path in reads:
        assert tech.get(path).status_code == 403, path
        assert viewer.get(path).status_code == 200, path
    for path, data in writes:
        assert tech.post(path, data=data).status_code == 403, path
        assert viewer.post(path, data=data).status_code == 403, path
    assert viewer.get(f"/customers/{b['cid']}/payment").status_code == 403
    page = text(viewer.get(f"/customers/{b['cid']}"))
    assert "Account" in page and "Record a payment" not in page
    assert "Account" not in text(tech.get(f"/customers/{b['cid']}"))
    assert db_query(app, "SELECT count(*) FROM payment") == [(1,)]


def test_public_receipts_only_for_this_invoice(app, signed_in):
    owner = signed_in("owner")
    b = billable(owner, app)
    a = issued_id(issue(owner, app, b["wo"]))
    _, other = fixed_invoice(owner, app, b["cid"], "50")
    pay(owner, app, a, "10")
    pay(owner, app, other, "10")
    on_a, on_other = [r[0] for r in db_query(app, "SELECT id FROM payment ORDER BY id")]
    token = re.search(r"/d/([A-Za-z0-9_-]{43})",
                      text(owner.post(f"/invoices/{a}/send"))).group(1)
    anon = app.test_client()
    assert anon.get(f"/d/{token}/receipt/{on_a}.pdf").status_code == 200
    assert anon.get(f"/d/{token}/receipt/{on_other}.pdf").status_code == 404


@pytest.mark.parametrize("seed", [7, 2026, 31337])
def test_balances_always_equal_what_is_applied(app, signed_in, monkeypatch, seed):
    """Random payments, splits, credit, reversals, refunds and credit memos, each through
    the service and committed (or refused and rolled back); after every step, the money
    invariants hold."""
    from app.billing import docs as invoice_docs

    monkeypatch.setattr(invoice_docs, "credit_memo_pdf", lambda *a, **k: b"%PDF-1.7 test")
    owner = signed_in("owner")
    b = billable(owner, app)
    issued_id(issue(owner, app, b["wo"]))
    for price in ("50", "275.55", "19.99"):
        fixed_invoice(owner, app, b["cid"], price)
    rng = random.Random(seed)
    cents = lambda lo, hi: D(rng.randint(int(lo * 100), int(hi * 100))) / 100  # noqa: E731
    done = {"ok": 0, "refused": 0}
    with app.test_request_context():
        for _ in range(60):
            customer = db.session.get(Customer, b["cid"])
            invoices = db.session.scalars(select(Invoice).where(
                Invoice.customer_id == customer.id, Invoice.status != "draft")).all()
            receipts = db.session.scalars(select(Payment).where(Payment.kind == "receipt")).all()
            memos = db.session.scalars(select(CreditMemo).where(
                CreditMemo.status == "issued")).all()
            op = rng.choice(["pay", "pay", "split", "credit", "reverse", "refund", "memo",
                             "apply"])
            try:
                if op == "pay":
                    inv = rng.choice(invoices)
                    amount = cents(0.01, float(inv.balance) + 25)
                    payments.record_payment(customer, amount=amount, method="cash",
                                            received_on=today(app),
                                            applied={inv.id: min(amount, inv.balance)})
                elif op == "split":
                    amount = cents(0.01, 300)
                    payments.record_payment(customer, amount=amount, method="zelle",
                                            received_on=today(app),
                                            applied=payments.spread(customer.id, amount))
                elif op == "credit":
                    payments.apply_credit(rng.choice(invoices))
                elif op == "reverse" and receipts:
                    payments.reverse(rng.choice(receipts), reason="random")
                elif op == "refund" and (receipts or memos):
                    src = rng.choice(receipts + memos)
                    payments.refund(src, amount=cents(0.01, 40), method="cash", reason="random")
                elif op == "memo":
                    inv = rng.choice(invoices)
                    payments.issue_credit_memo(inv, reason="random", lines=[
                        ("Adjustment", cents(0.01, 60), rng.random() < 0.5)])
                elif op == "apply" and (receipts or memos):
                    payments.apply_source(rng.choice(receipts + memos), rng.choice(invoices))
                db.session.commit()
                done["ok"] += 1
            except InvoiceError:
                db.session.rollback()
                done["refused"] += 1
            check_money(app)
    assert done["ok"] >= 20, done
    assert line_ids(app, b["wo"]) == []                   # nothing came unbilled
