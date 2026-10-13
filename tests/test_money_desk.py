"""Money at the desk (Phase 9c): invoicing ready work in bulk (numbers stay gapless when
one is refused), statements for everyone who owes (one PDF, links that open a live
statement), bank reconciliation (import, match, reconcile, the locks), and the general
journal for an accountant (every entry balances, and it agrees with the P&L)."""

import csv
import io
import re
from datetime import date, timedelta
from decimal import Decimal as D

import psycopg
import pytest
from test_billing import billable, capture, issue, issued_id, owner_sql, today
from test_books import add_expense, category
from test_parts import add_part
from test_payments import fixed_invoice
from test_work import first_job, new_wo, setup, text

from conftest import db_query, libpq


def fixed_wo(owner, app, cid, price, status="ready"):
    """A work order with one fixed-price line, not yet invoiced, in this status."""
    wo = new_wo(owner, cid)
    owner.post(f"/work/jobs/{first_job(app, wo)}/lines/fixed", data={
        "description": "Repair", "unit_price": price, "billable": "y", "tech_user_id": "None"})
    owner.post(f"/work/{wo}/status", data={"status": status})
    return wo


def numbers(app):
    return [r[0] for r in db_query(app, "SELECT number FROM invoice ORDER BY number")]


# --- bulk invoicing ---------------------------------------------------------------------------

def test_invoicing_ready_work_in_bulk(app, signed_in, monkeypatch):
    from app.billing import service
    owner, viewer, tech = signed_in("owner"), signed_in("viewer"), signed_in("tech")
    b = billable(owner, app)                                   # $123.90, new
    owner.post(f"/work/{b['wo']}/status", data={"status": "completed"})
    second = fixed_wo(owner, app, b["cid"], "50")
    third = fixed_wo(owner, app, b["cid"], "75")
    waiting = fixed_wo(owner, app, b["cid"], "20")
    owner.post(f"/work/jobs/{first_job(app, waiting)}/lines/customer-part",
               data={"description": "Their flyback", "qty": "1", "wait": "y"})
    owner.post(f"/work/{waiting}/status", data={"status": "ready"})
    # $20 on account: it goes on the first invoice
    owner.post(f"/customers/{b['cid']}/payment", data={
        "amount": "20", "method": "cash", "received_on": str(today(app)),
        "estimate_id": "None", "action": "record"})
    page = text(owner.get("/invoices/bulk"))
    wo_numbers = dict(db_query(app, "SELECT id, number FROM work_order"))
    assert all(wo_numbers[w] in page for w in (b["wo"], second, third, waiting))
    assert "Can't issue: The customer's part isn't here yet: Their flyback." in page
    assert "-$20.00" in page
    base = {"issue_date": str(today(app)), "status": ["completed", "ready"]}
    resp = owner.post("/invoices/bulk", data={**base, "action": "preview",
                                              "wo": [b["wo"], second, third, waiting]})
    assert "<strong>3</strong> invoices: $248.90, $20.00 paid from credit, $228.90 to collect." \
        in text(resp)
    # one refused in the middle: the others are issued, and the numbers don't skip
    real_issue = service.issue

    def picky(wo, *args, **kwargs):
        if wo.id == second:
            raise service.InvoiceError("refused for the test")
        return real_issue(wo, *args, **kwargs)

    monkeypatch.setattr(service, "issue", picky)
    resp = owner.post("/invoices/bulk", data={**base, "action": "issue",
                                              "wo": [b["wo"], second, third]},
                      follow_redirects=True)
    page = text(resp)
    assert f"{wo_numbers[second]}: refused for the test" in page and "Issued 2 invoices." in page
    year = str(today(app).year)
    assert numbers(app) == [f"INV-{year}-0001", f"INV-{year}-0002"]
    first_inv = db_query(app, "SELECT balance FROM invoice WHERE work_order_id = :w",
                         w=b["wo"])[0][0]
    assert first_inv == D("103.90")                            # the credit went on it
    monkeypatch.setattr(service, "issue", real_issue)
    resp = owner.post("/invoices/bulk", data={**base, "action": "issue", "wo": [second]},
                      follow_redirects=True)
    assert numbers(app)[-1] == f"INV-{year}-0003"              # the refused one, next
    # links for what was issued
    ids = re.search(r'name="ids" value="([0-9,]+)"', resp.get_data(as_text=True)).group(1)
    page = text(owner.post("/invoices/bulk/links", data={"ids": ids}))
    assert page.count("/d/") >= 1 and db_query(app, "SELECT count(*) FROM doc_link") == [(1,)]
    # dates and who
    fourth = fixed_wo(owner, app, b["cid"], "30")
    resp = owner.post("/invoices/bulk", data={**base, "action": "issue", "wo": [fourth],
                                              "issue_date": str(today(app) - timedelta(days=3))},
                      follow_redirects=True)
    assert "say why" in text(resp) and len(numbers(app)) == 3
    for who in (viewer, tech):
        assert who.get("/invoices/bulk").status_code == 403


# --- statements -------------------------------------------------------------------------------

def test_statements_for_everyone_who_owes(app, signed_in, monkeypatch, client, tdb):
    from app.billing import docs
    owner, viewer = signed_in("owner"), signed_in("viewer")
    b = billable(owner, app)
    _, inv = fixed_invoice(owner, app, b["cid"], "50")
    owner.post(f"/invoices/{inv}/payment", data={"amount": "10", "method": "check",
                                                 "received_on": str(today(app)),
                                                 "reference": "1234", "note": ""})
    page = text(viewer.get("/statements"))
    assert "Starlite Lanes Test" in page and "$40.00" in page
    assert "Make links" not in page                            # viewers read, only
    assert "Nobody owes anything overdue." in text(owner.get("/statements?overdue=1"))
    seen = capture(monkeypatch, docs)
    resp = viewer.post("/statements", data={"c": [b["cid"]], "action": "pdf"})
    assert resp.status_code == 200 and resp.mimetype == "application/pdf"
    html = seen["docs/statements.html"]
    assert "Starlite Lanes Test" in html and "Payments in the last 60 days" in html
    assert "1234" in html and "$40.00" in html
    assert viewer.post("/statements", data={"c": [b["cid"]], "action": "links"}) \
        .status_code == 403
    # a link: opens the live statement, notes itself on the comm log
    page = text(owner.post("/statements", data={"c": [b["cid"]], "action": "links"}))
    path = re.search(r"https?://[^/\s]+(/d/[A-Za-z0-9_-]{43})", page).group(1)
    assert db_query(app, "SELECT count(*) FROM doc_link WHERE customer_id = :c",
                    c=b["cid"]) == [(1,)]
    assert db_query(app, "SELECT summary FROM comm_log")[0][0].startswith("Statement link made")
    public = text(client.get(path))
    assert "Statement" in public and "$40.00" in public and "Download the PDF" in public
    pdf = client.get(path + "/statement.pdf")
    assert pdf.mimetype == "application/pdf", (pdf.status_code, text(pdf)[:600])
    owner_sql(tdb, "UPDATE doc_link SET expires_at = now() - interval '1 minute'")
    assert client.get(path).status_code == 404
    assert client.get(path + "/statement.pdf").status_code == 404
    # the database still holds one document per link
    with psycopg.connect(libpq(tdb.url("app"))) as c, pytest.raises(psycopg.errors.CheckViolation):
        c.execute("INSERT INTO doc_link (customer_id, invoice_id, token_sha256, expires_at) "
                  "VALUES (%s, %s, repeat('a', 64), now())", (b["cid"], inv))


# --- bank reconciliation ----------------------------------------------------------------------

def test_reading_bank_exports():
    from app.bank.service import BankError, detect_format, parse_amount, read_lines
    assert parse_amount("$1,234.56") == D("1234.56") and parse_amount("(12.00)") == D("-12.00")
    assert parse_amount("5.00-") == D("-5.00") and parse_amount(" ") is None
    with pytest.raises(BankError):
        parse_amount("twelve")
    assert detect_format(["10/13/2026", "10/01/2026"]) == "%m/%d/%Y"
    assert detect_format(["2026-10-13"]) == "%Y-%m-%d"
    # account details above the header, a byte-order mark, debit and credit columns
    data = ("\ufeffAccount: Example Bank checking\nDate,Description,Debit,Credit\n"
            "10/13/2026,DEPOSIT,,173.90\n10/14/2026,CARD PURCHASE,42.15,\n").encode()
    rows = read_lines(data, {"date": 0, "description": 1, "debit": 2, "credit": 3})
    assert rows == [(date(2026, 10, 13), "DEPOSIT", D("173.90")),
                    (date(2026, 10, 14), "CARD PURCHASE", D("-42.15"))]
    # a card export: one amount column, charges positive
    card = b"Transaction Date;Payee;Amount\n2026-10-14;FUEL;30.00\n2026-10-15;PAYMENT;-100.00\n"
    rows = read_lines(card, {"date": 0, "description": 1, "amount": 2, "flip": True})
    assert [r[2] for r in rows] == [D("-30.00"), D("100.00")]
    with pytest.raises(BankError):
        read_lines(data, {"date": 0, "description": 9, "amount": 2})


def bank_csv(app, rows):
    body = "Account: Example Bank checking\nDate,Description,Debit,Credit,Balance\n" + \
        "".join(f"{d.strftime('%m/%d/%Y')},{desc},{out},{inn},\n" for d, desc, out, inn in rows)
    return io.BytesIO(body.encode())


def upload(owner, account_id, data, name="checking.csv"):
    page = owner.post(f"/bank/{account_id}/import", data={"file": (data, name)},
                      content_type="multipart/form-data").get_data(as_text=True)
    sha = re.search(r'name="file_sha256" value="([0-9a-f]{64})"', page).group(1)
    return owner.post(f"/bank/{account_id}/import", data={
        "step": "2", "file_sha256": sha, "filename": name, "date": "0", "description": "1",
        "debit": "2", "credit": "3", "amount": "", "date_format": ""}, follow_redirects=True)


def test_import_match_reconcile_and_the_locks(app, signed_in, tdb):
    owner, viewer, tech = signed_in("owner"), signed_in("viewer"), signed_in("tech")
    day = today(app)
    owner.post("/bank/", data={"name": "Checking", "kind": "checking",
                               "opening_on": str(day - timedelta(days=30)),
                               "opening_balance": "1000.00", "last4": ""})
    account = db_query(app, "SELECT id FROM bank_account")[0][0]
    # what the app knows: a check and cash (deposited together), a card purchase
    b = billable(owner, app)
    inv = issued_id(issue(owner, app, b["wo"]))
    owner.post(f"/invoices/{inv}/payment", data={"amount": "123.90", "method": "check",
                                                 "received_on": str(day), "reference": "1001",
                                                 "note": ""})
    _, inv2 = fixed_invoice(owner, app, b["cid"], "50")
    owner.post(f"/invoices/{inv2}/payment", data={"amount": "50", "method": "cash",
                                                  "received_on": str(day), "reference": "",
                                                  "note": ""})
    expense = add_expense(owner, app, amount="42.15", payee="Example Parts")
    data = bank_csv(app, [(day, "DEPOSIT", "", "173.90"),
                          (day, "CARD PURCHASE EXAMPLE PARTS", "42.15", ""),
                          (day, "MONTHLY FEE", "5.00", ""), (day, "INTEREST", "", "0.12")])
    raw = data.getvalue()
    assert "Added 4 line(s); skipped 0" in text(upload(owner, account, data))
    assert "Added 0 line(s); skipped 4" in text(upload(owner, account, io.BytesIO(raw)))
    assert db_query(app, "SELECT mapping->>'debit' FROM bank_account") == [("2",)]
    lines = dict(db_query(app, "SELECT description, id FROM bank_line"))
    page = owner.get(f"/bank/{account}?show=todo").get_data(as_text=True)
    payments = [r[0] for r in db_query(app, "SELECT id FROM payment ORDER BY id")]
    keys = ",".join(f"payment_id:{p}" for p in payments)
    assert f'name="keys" value="{keys}"' in page              # a deposit of both receipts
    assert f'name="keys" value="expense_id:{expense}"' in page
    owner.post(f"/bank/lines/{lines['DEPOSIT']}/match", data={"keys": keys})
    owner.post(f"/bank/lines/{lines['CARD PURCHASE EXAMPLE PARTS']}/match",
               data={"keys": f"expense_id:{expense}"})
    resp = owner.post(f"/bank/lines/{lines['MONTHLY FEE']}/expense",
                      data={"category_id": category(app), "payee": "Example Bank"},
                      follow_redirects=True)
    assert "Expense added and matched." in text(resp)
    assert db_query(app, "SELECT amount, payee FROM expense ORDER BY id DESC LIMIT 1") == [
        (D("5.00"), "Example Bank")]
    resp = owner.post(f"/bank/lines/{lines['INTEREST']}/match", data={"keys": keys},
                      follow_redirects=True)
    assert "matched already" in text(resp)                    # a record is matched once
    owner.post(f"/bank/lines/{lines['INTEREST']}/aside", data={"reason": "interest"})
    # reconcile: it must balance
    ending = D("1000.00") + D("173.90") - D("42.15") - D("5.00") + D("0.12")
    resp = owner.post(f"/bank/{account}/reconcile", data={
        "statement_end": str(day), "ending_balance": str(ending + 1), "action": "reconcile"})
    assert "-$1.00" in text(resp) or "$1.00" in text(resp)
    assert db_query(app, "SELECT count(*) FROM bank_reconciliation") == [(0,)]
    resp = owner.post(f"/bank/{account}/reconcile", data={
        "statement_end": str(day), "ending_balance": str(ending), "action": "reconcile"},
        follow_redirects=True)
    assert "those lines are locked" in text(resp)
    assert db_query(app, "SELECT count(*) FROM bank_line WHERE reconciliation_id IS NULL") == [(0,)]
    # locked in the database, whatever the app does
    with psycopg.connect(libpq(tdb.url("app"))) as c:
        for sql in ("UPDATE bank_line SET amount = 1", "UPDATE bank_line SET ignored = NULL",
                    "DELETE FROM bank_line", "DELETE FROM bank_match",
                    "UPDATE bank_reconciliation SET ending_balance = 0"):
            with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
                c.execute(sql)
            c.rollback()
        for sql in ("UPDATE bank_match SET amount = 1", "DELETE FROM bank_reconciliation"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)
            c.rollback()
    with psycopg.connect(libpq(tdb.url("owner"))) as c:     # even the schema's owner
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            c.execute("DELETE FROM bank_reconciliation")
    match_id = db_query(app, "SELECT id FROM bank_match ORDER BY id LIMIT 1")[0][0]
    assert "reconciled" in text(owner.post(f"/bank/matches/{match_id}/unmatch",
                                           follow_redirects=True))
    # a matched expense edited afterwards is flagged, not hidden
    with psycopg.connect(libpq(tdb.url("app"))) as c:
        c.execute("UPDATE expense SET amount = 40 WHERE id = %s", (expense,))
    assert "changed since reconciled" in text(owner.get(f"/bank/{account}?show=all"))
    # the latest reconciliation can be undone, with a reason
    rec = db_query(app, "SELECT id FROM bank_reconciliation")[0][0]
    resp = owner.post(f"/bank/reconciliations/{rec}/undo", data={"reason": ""},
                      follow_redirects=True)
    assert "Say why" in text(resp)
    owner.post(f"/bank/reconciliations/{rec}/undo", data={"reason": "Matched the wrong check"})
    assert db_query(app, "SELECT count(*) FROM bank_line WHERE reconciliation_id IS NULL") == [(4,)]
    assert db_query(app, "SELECT undo_reason FROM bank_reconciliation") == [
        ("Matched the wrong check",)]
    owner.post(f"/bank/matches/{match_id}/unmatch")
    assert db_query(app, "SELECT count(*) FROM bank_match WHERE id = :i", i=match_id) == [(0,)]
    # who
    assert "Checking" in text(viewer.get("/bank/"))
    assert viewer.get(f"/bank/{account}").status_code == 200
    assert viewer.get(f"/bank/{account}/import").status_code == 403
    assert viewer.post(f"/bank/lines/{lines['DEPOSIT']}/match", data={"keys": keys}) \
        .status_code == 403
    assert tech.get("/bank/").status_code == 403


# --- for an accountant ------------------------------------------------------------------------

def test_general_journal_balances_and_ties_to_the_pnl(app, signed_in, tdb):
    """9c exit: the synthetic year (invoices, a void, partial, bounced and over payments, a
    refund, a credit memo, expenses, a PO, trips) plus partners' money, a business-vehicle
    trip, a sales tax payment and a payment matched to a wallet: every entry balances,
    income less costs is the accrual P&L's net, and the balances are what the app says."""
    from test_reports import day_in_year, synthetic_year

    from app.books import journal
    from app.books import service as books
    from app.reports import figures
    owner, viewer, tech = signed_in("owner"), signed_in("viewer"), signed_in("tech")
    s = synthetic_year(owner, app, tdb)
    t, me = s["t"], owner.user["id"]
    jan1 = date(t.year, 1, 1)
    add_expense(owner, app, "15.00", spent_on=str(day_in_year(t, 0.3)),
                paid_by_user_id=str(me))
    owner.post("/books/trips/new", data={"trip_on": str(day_in_year(t, 0.4)),
                                         "user_id": str(me), "miles": "8",
                                         "purpose": "Shop van"})
    for kind, amount in (("contribution", "500"), ("draw", "120"), ("reimbursement", "5")):
        owner.post("/books/ledger", data={"user_id": str(me), "kind": kind, "amount": amount,
                                          "entry_on": str(day_in_year(t, 0.8))})
    owner.post("/books/sales-tax", data={"period_start": str(jan1), "period_end": str(jan1),
                                         "tax_due": "1.11", "filed_on": str(t),
                                         "paid_on": str(t)})
    assert db_query(app, "SELECT kind FROM partner_entry ORDER BY id") == [
        ("contribution",), ("draw",), ("reimbursement",)]
    assert db_query(app, "SELECT tax_due FROM sales_tax_filing") == [(D("1.11"),)]
    # the venmo payment, matched to a Venmo account in Money → Bank
    owner.post("/bank/", data={"name": "Venmo", "kind": "wallet", "opening_on": str(jan1),
                               "opening_balance": "0", "last4": ""})
    wallet = db_query(app, "SELECT id FROM bank_account")[0][0]
    venmo_id, venmo, on = db_query(app, "SELECT id, amount, received_on FROM payment "
                                        "WHERE method = 'venmo'")[0]
    upload(owner, wallet, bank_csv(app, [(on, "PAYMENT FROM A CUSTOMER", "", str(venmo))]))
    line = db_query(app, "SELECT id FROM bank_line")[0][0]
    owner.post(f"/bank/lines/{line}/match", data={"keys": f"payment_id:{venmo_id}"})
    assert db_query(app, "SELECT count(*) FROM bank_match") == [(1,)]

    with app.app_context():
        es = journal.entries(jan1, t)
        assert es and all(e.balanced for e in es)
        assert journal.net_income(es) == figures.pnl(jan1, t, "accrual")["net"]
        tb = {name: net for name, _, _, _, net in journal.trial_balance(es)}
        owed = books.owed(me)["owed"]
        assert owed > 0
    assert sum(tb.values()) == 0
    # receivables: what was invoiced (live) less credit memos less money in and out
    billed = db_query(app, "SELECT sum(total) FROM invoice WHERE status <> 'void'")[0][0]
    credited = db_query(app, "SELECT coalesce(sum(total), 0) FROM credit_memo "
                             "WHERE status = 'issued'")[0][0]
    paid = db_query(app, "SELECT sum(amount) FROM payment")[0][0]
    assert tb["Accounts receivable"] == billed - credited - paid
    tax = db_query(app, "SELECT sum(tax) FROM invoice WHERE status <> 'void'")[0][0]
    assert tb["Sales tax payable"] == -(tax - D("1.11"))
    assert tb["Venmo"] == venmo                                # the bank account, by name
    name = db_query(app, "SELECT display_name FROM app_user WHERE id = :i", i=me)[0][0]
    assert tb[f"Owed to {name}"] == -owed
    assert tb[f"{name}, capital"] == D("-500.00") and tb[f"{name}, draws"] == D("120.00")
    assert tb["Business vehicle mileage"] < 0
    assert tb["Cost of goods sold"] == D("0.80")

    # the export, for owners and viewers
    page = text(viewer.get("/reports/accountant", query_string={"p": "year"}))
    assert "Out of balance" in page and "ties" in page and "Accounts receivable" in page
    resp = viewer.get("/reports/accountant", query_string={"p": "year", "format": "journal"})
    assert resp.mimetype == "text/csv"
    assert resp.headers["Content-Disposition"].startswith('attachment; filename="general-journal')
    rows = list(csv.DictReader(io.StringIO(resp.get_data(as_text=True))))
    assert list(rows[0]) == ["Date", "Entry", "Account", "Number", "Debit", "Credit", "Memo",
                             "Name", "Reference"]
    debits = sum(D(r["Debit"] or 0) for r in rows)
    assert debits == sum(D(r["Credit"] or 0) for r in rows) and debits > 0
    assert {r["Entry"] for r in rows} == {str(n) for n in range(1, len(es) + 1)}
    tb_csv = viewer.get("/reports/accountant", query_string={"p": "year", "format": "csv"})
    total = [r for r in csv.reader(io.StringIO(tb_csv.get_data(as_text=True)))][-1]
    assert total[0] == "Total" and D(total[2]) == D(total[3]) == debits and D(total[4]) == 0
    assert tech.get("/reports/accountant").status_code == 403

    # account names: two keys under one name are one account; clearing puts it back
    assert viewer.get("/settings/accounts").status_code == 403
    page = text(owner.get("/settings/accounts"))
    assert "Labor lines on invoices" in page and "Venmo payments not matched" in page
    resp = owner.post("/settings/accounts", data={
        "name-income:labor": "Repair income", "number-income:labor": "4010",
        "name-income:part": "Repair  income ", "number-income:part": "4010"},
        follow_redirects=True)
    assert "2 accounts changed." in text(resp)
    with app.app_context():
        rows = journal.trial_balance(journal.entries(jan1, t))
    repair = [r for r in rows if r[0] == "Repair income"]
    assert len(repair) == 1 and repair[0][1] == "4010"
    assert not any(r[0] in ("Sales: labor", "Sales: parts") for r in rows)
    journal_csv = viewer.get("/reports/accountant", query_string={
        "p": "year", "format": "journal"}).get_data(as_text=True)
    assert "Repair income,4010," in journal_csv
    resp = owner.post("/settings/accounts", data={"name-income:labor": "",
                                                  "number-income:labor": "",
                                                  "name-income:part": "x" * 121},
                      follow_redirects=True)
    assert resp.status_code == 400 and "up to 120 characters" in text(resp)
    assert db_query(app, "SELECT key, name FROM account_map ORDER BY key") == [
        ("income:labor", "Repair income"), ("income:part", "Repair income")]
    owner.post("/settings/accounts", data={"name-income:labor": "", "number-income:labor": ""})
    assert db_query(app, "SELECT key FROM account_map") == [("income:part",)]


def test_journal_rounds_a_half_cent_like_the_pnl(app, signed_in):
    """A lot cost of $0.1250 is exactly half a cent: Postgres rounds it up, as the P&L
    does, and the journal must too (Python's round() would go to the even cent)."""
    from app.books import journal
    from app.reports import figures
    owner = signed_in("owner")
    _, _, wo, job = setup(owner, app)
    pid = add_part(owner, sku="HALF-CENT")
    owner.post(f"/parts/{pid}/receive", data={"qty": "1", "unit_cost": "0.1250",
                                              "vendor_id": "None"})
    owner.post(f"/work/jobs/{job}/lines/part", data={"part_id": pid, "qty": "1",
                                                      "billable": "y"})
    issued_id(issue(owner, app, wo))
    t = today(app)
    with app.app_context():
        es = journal.entries(t, t)
        tb = {name: amount for name, _, _, _, amount in journal.trial_balance(es)}
        assert figures.cogs(t, t) == D("0.13")
        assert journal.net_income(es) == figures.pnl(t, t, "accrual")["net"]
    assert tb["Cost of goods sold"] == D("0.13")
