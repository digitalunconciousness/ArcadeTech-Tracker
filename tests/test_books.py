"""The books (Phase 6a): expenses with receipts (and what purchase orders spend), mileage
at the rate in effect on the day, what partners are owed and paying it back, sales tax
by period and recorded filings, and who may see any of it."""

import io
from decimal import Decimal as D

import psycopg
import pytest
from PIL import Image
from test_billing import add_jurisdiction, billable, issue, issued_id, owner_sql, today
from test_partlinks import add_vendor
from test_parts import add_part
from test_purchasing import make_po, order_for_job
from test_work import setup, text

from conftest import db_query, libpq


def png():
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(out, "PNG")
    out.seek(0)
    return out


def category(app, name="Supplies"):
    return db_query(app, "SELECT id FROM expense_category WHERE name = :n", n=name)[0][0]


def partner(tdb, client):
    owner_sql(tdb, "UPDATE app_user SET is_partner = true WHERE id = %(i)s", i=client.user["id"])
    return client.user["id"]


def add_expense(client, app, amount="40.00", **extra):
    data = {"spent_on": str(today(app)), "payee": "Example Supply", "amount": amount,
            "category_id": str(category(app)), "paid_by_user_id": "None", **extra}
    resp = client.post("/books/expenses/new", data=data, content_type="multipart/form-data")
    assert resp.status_code == 302, text(resp)[-1500:]
    return int(resp.location.rsplit("/", 1)[1])


def test_expenses_receipts_and_voids(app, signed_in):
    owner = signed_in("owner")
    eid = add_expense(owner, app, tax_included="2.80",
                      receipt=(png(), "receipt.png", "image/png"))
    sha, kind = db_query(app, "SELECT receipt_sha256, receipt_type FROM expense")[0]
    assert kind == "image/jpeg" and len(sha) == 64          # re-encoded: metadata dropped
    resp = owner.get(f"/books/receipts/{sha}")
    assert resp.status_code == 200 and resp.mimetype == "image/jpeg"
    pdf = add_expense(owner, app, receipt=(io.BytesIO(b"%PDF-1.4 receipt"), "r.pdf",
                                           "application/pdf"))
    assert db_query(app, "SELECT receipt_type FROM expense WHERE id = :i", i=pdf) == \
        [("application/pdf",)]
    resp = owner.post("/books/expenses/new", data={
        "spent_on": str(today(app)), "payee": "X", "amount": "5", "paid_by_user_id": "None",
        "category_id": str(category(app)), "receipt": (io.BytesIO(b"not a receipt"), "x.txt")},
        content_type="multipart/form-data")
    assert "can be a photo or a PDF" in text(resp)
    for bad, message in (({"spent_on": "2999-01-01"}, "can't be dated in the future"),
                         ({"tax_included": "50"}, "can't be more than the amount"),
                         ({"wo": "WO-2026-9999"}, "There's no WO-2026-9999")):
        resp = owner.post("/books/expenses/new", data={
            "spent_on": str(today(app)), "payee": "X", "amount": "40", "paid_by_user_id": "None",
            "category_id": str(category(app)), **bad}, content_type="multipart/form-data")
        assert message in text(resp), message
    page = text(owner.get("/books/expenses"))
    assert "Example Supply" in page and "$80.00" in page
    # edit, then void: a voided expense drops out of the list and can't be edited
    owner.post(f"/books/expenses/{eid}/edit", data={
        "spent_on": str(today(app)), "payee": "Example Supply Co", "amount": "41.00",
        "category_id": str(category(app)), "paid_by_user_id": "None"},
        content_type="multipart/form-data")
    assert db_query(app, "SELECT payee, amount, receipt_sha256 FROM expense WHERE id = :i",
                    i=eid) == [("Example Supply Co", D("41.00"), sha)]   # receipt kept
    owner.post(f"/books/expenses/{eid}/void", data={"reason": " "})      # a reason is needed
    assert db_query(app, "SELECT voided_at FROM expense WHERE id = :i", i=eid) == [(None,)]
    owner.post(f"/books/expenses/{eid}/void", data={"reason": "Duplicate"})
    assert db_query(app, "SELECT void_reason FROM expense WHERE id = :i", i=eid) == \
        [("Duplicate",)]
    assert "$40.00" in text(owner.get("/books/expenses"))
    resp = owner.post(f"/books/expenses/{eid}/edit", data={
        "spent_on": str(today(app)), "payee": "Again", "amount": "1", "paid_by_user_id": "None",
        "category_id": str(category(app))}, content_type="multipart/form-data",
        follow_redirects=True)
    assert "That expense is voided" in text(resp)


def test_who_sees_the_books(app, signed_in, tdb):
    owner = signed_in("owner")
    add_expense(owner, app, receipt=(png(), "r.png", "image/png"))
    sha = db_query(app, "SELECT receipt_sha256 FROM expense")[0][0]
    tech, viewer = signed_in("tech"), signed_in("viewer")
    for path in ("/books", "/books/expenses", "/books/mileage", "/books/partners",
                 "/books/sales-tax", f"/books/receipts/{sha}"):
        assert tech.get(path).status_code == 403, path
        assert viewer.get(path).status_code == 200, path
    assert "Books" not in text(tech.get("/work")).split("<main")[0]
    assert viewer.get("/books/expenses/new").status_code == 403
    assert viewer.post("/books/trips/new", data={}).status_code == 403
    assert viewer.post("/books/ledger", data={}).status_code == 403
    assert viewer.post("/books/sales-tax", data={}).status_code == 403
    assert viewer.get("/settings/books").status_code == 403
    assert owner.get("/books/receipts/" + "0" * 64).status_code == 404
    # nothing in the books can be deleted by the app
    with psycopg.connect(libpq(tdb.url("app"))) as c:
        for table in ("expense", "trip", "partner_entry", "sales_tax_filing", "mileage_rate",
                      "expense_category"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"DELETE FROM {table}")
            c.rollback()


def test_purchase_orders_in_the_expenses(app, signed_in):
    owner = signed_in("owner")
    vid = add_vendor(owner)
    stocked = add_part(owner, preferred_vendor_id=str(vid))
    flux = add_part(owner, sku="FLUX-1", name="Flux pen", track_stock="", reorder_point="",
                    reorder_qty="", default_cost="3.3330", preferred_vendor_id=str(vid))
    _, _, _, job = setup(owner, app)
    order_for_job(owner, job, stocked, "10")
    order_for_job(owner, job, flux, "2")
    po = make_po(owner, app)
    owner.post(f"/purchasing/orders/{po}/amounts", data={"shipping": "7.50", "tax_paid": "1.20"})
    rows = text(owner.get("/books/expenses"))
    number = db_query(app, "SELECT number FROM purchase_order")[0][0]
    assert rows.count(number) == 3
    assert "$6.67" in rows and "$7.50" in rows and "$1.20" in rows   # 2 × 3.333, not the caps
    assert "$15.37" in rows                                          # the period's total
    # a draft or cancelled PO spends nothing
    order_for_job(owner, job, flux, "1")
    draft = make_po(owner, app, ordered=False)
    assert text(owner.get("/books/expenses")).count(number) == 3
    owner.post(f"/purchasing/orders/{draft}/cancel")
    owner.post(f"/purchasing/orders/{po}/cancel")
    assert number not in text(owner.get("/books/expenses"))


def test_mileage_at_the_rate_on_the_day(app, signed_in):
    owner = signed_in("owner")
    for day, miles in (("2026-06-30", "100"), ("2026-07-01", "100"), ("2025-12-31", "10")):
        resp = owner.post("/books/trips/new", data={
            "trip_on": day, "user_id": str(owner.user["id"]), "miles": miles,
            "purpose": "Service call", "own_vehicle": "y"})
        assert resp.status_code == 302, text(resp)[-800:]
    page = text(owner.get("/books/mileage", query_string={"start": "2025-01-01",
                                                          "end": str(today(app))}))
    assert "$72.50" in page and "$76.00" in page and "1 without a rate" in page
    assert "210 miles · $148.50" in page
    # a rate fixed later revalues the trips on its dates
    rid = db_query(app, "SELECT id FROM mileage_rate WHERE effective_from = '2026-07-01'")[0][0]
    owner.post(f"/settings/books/rates/{rid}", data={"effective_from": "2026-07-01",
                                                     "rate": "0.77", "source": "corrected"})
    assert "$77.00" in text(owner.get("/books/mileage"))
    # the same start date twice is refused
    resp = owner.post("/settings/books/rates", data={"effective_from": "2026-07-01",
                                                     "rate": "0.80"}, follow_redirects=True)
    assert "There's one already" in text(resp)
    resp = owner.post("/books/trips/new", data={"trip_on": "2999-01-01", "miles": "5",
                                                "user_id": str(owner.user["id"]),
                                                "purpose": "x"})
    assert "can't be dated in the future" in text(resp)
    # logging a trip from a work order fills it in
    _, _, wo_id, _ = setup(owner, app)
    number = db_query(app, "SELECT number FROM work_order WHERE id = :w", w=wo_id)[0][0]
    form = text(owner.get("/books/trips/new", query_string={"wo": number}))
    assert f'value="{number}"' in form and f"{number}: Won" in form


def test_what_a_partner_is_owed_and_paying_it_back(app, signed_in, tdb):
    """6a exit: an expense, a trip and a PO paid by a partner each show as owed to them;
    recording a reimbursement clears it."""
    owner = signed_in("owner")
    me = partner(tdb, owner)
    other = signed_in("owner")
    # only a partner can be paid back
    resp = owner.post("/books/expenses/new", data={
        "spent_on": str(today(app)), "payee": "X", "amount": "5",
        "category_id": str(category(app)), "paid_by_user_id": str(other.user["id"])},
        content_type="multipart/form-data")
    assert "Only a partner" in text(resp)
    add_expense(owner, app, amount="40.00", paid_by_user_id=str(me))
    owner.post("/books/trips/new", data={"trip_on": "2026-07-02", "user_id": str(me),
                                         "miles": "10", "purpose": "Parts run",
                                         "own_vehicle": "y"})
    owner.post("/books/trips/new", data={"trip_on": "2026-07-02", "user_id": str(me),
                                         "miles": "10", "purpose": "Shop van"})  # not owed
    vid = add_vendor(owner)
    pid = add_part(owner, preferred_vendor_id=str(vid))
    _, _, _, job = setup(owner, app)
    order_for_job(owner, job, pid, "10")                    # 10 × 0.4120 = 4.12
    po = make_po(owner, app, paid_by_user_id=str(me))
    owner.post(f"/purchasing/orders/{po}/amounts", data={"shipping": "5.00", "tax_paid": "0"})
    page = text(owner.get(f"/books/partners/{me}"))
    assert "Owed <strong>$56.72</strong>" in page            # 40 + 7.60 + 9.12
    assert "$40.00" in page and "$7.60" in page and "$9.12" in page
    # more than owed is refused; the rest is a draw
    resp = owner.post("/books/ledger", data={"user_id": str(me), "kind": "reimbursement",
                                             "amount": "100", "entry_on": str(today(app))},
                      follow_redirects=True)
    assert "more than they're owed" in text(resp)
    owner.post("/books/ledger", data={"user_id": str(me), "kind": "reimbursement",
                                      "amount": "56.72", "entry_on": str(today(app))})
    assert "Owed <strong>$0.00</strong>" in text(owner.get(f"/books/partners/{me}"))
    entry = db_query(app, "SELECT id FROM partner_entry")[0][0]
    owner.post(f"/books/ledger/{entry}/void", data={"reason": "Entered twice"})
    assert "Owed <strong>$56.72</strong>" in text(owner.get(f"/books/partners/{me}"))
    owner.post("/books/ledger", data={"user_id": str(me), "kind": "draw", "amount": "200",
                                      "entry_on": str(today(app))})
    assert db_query(app, "SELECT kind, amount FROM partner_entry WHERE voided_at IS NULL") == \
        [("draw", D("200.00"))]


def test_sales_tax_by_period_and_filings(app, signed_in):
    owner = signed_in("owner")
    jid = add_jurisdiction(owner, app)                       # 8.5%
    owner.post("/settings/tax", data={"default_tax_jurisdiction_id": str(jid)})
    # tax off: what it would have been, for the CPA
    b = billable(owner, app, part_price="10.00")             # 2 × $10 taxable
    issued_id(issue(owner, app, b["wo"], jurisdiction_id=str(jid)))
    page = text(owner.get("/books/sales-tax"))
    assert "Testville" in page and "$20.00" in page and "$1.70" in page
    # tax on
    owner.post("/settings/tax", data={"collect_sales_tax": "y",
                                      "default_tax_jurisdiction_id": str(jid)})
    _, _, wo2, job2 = setup(owner, app)
    owner.post(f"/work/jobs/{job2}/lines/part", data={"part_id": b["pid"], "qty": "2",
                                                      "billable": "y", "unit_price": "10.00"})
    inv = issued_id(issue(owner, app, wo2, jurisdiction_id=str(jid)))
    owner.post(f"/invoices/{inv}/credit-memo", data={
        "reason": "Returned one", "lines-0-description": "Part", "lines-0-amount": "10.00",
        "lines-0-taxable": "y"})
    start = end = str(today(app))
    page = text(owner.get("/books/sales-tax", query_string={"start": start, "end": end}))
    assert "$30.00" in page                                  # 20 + 20 − 10 taxable
    tax = db_query(app, "SELECT tax FROM invoice WHERE id = :i", i=inv)[0][0] - \
        db_query(app, "SELECT tax FROM credit_memo")[0][0]
    assert f"${tax}" in page and tax == D("0.85")
    resp = owner.post("/books/sales-tax", data={"period_start": start, "period_end": end,
                                                "tax_due": "0.85", "filed_on": end,
                                                "confirmation": "OTC-1"}, follow_redirects=True)
    assert "Filing recorded" in text(resp)
    assert db_query(app, "SELECT taxable_sales, tax_collected, tax_due FROM sales_tax_filing") \
        == [(D("30.00"), D("0.85"), D("0.85"))]
    resp = owner.post("/books/sales-tax", data={"period_start": start, "period_end": end,
                                                "tax_due": "0"}, follow_redirects=True)
    assert "already covers part of that period" in text(resp)
    with app.app_context():
        from app.books import service
        assert service.unfiled_tax() == D("0.00")


def test_books_settings(app, signed_in):
    owner = signed_in("owner")
    resp = owner.post("/settings/books", data={"split_mode": "custom", "set_aside_pct": "25",
                                               "storage_days": "30", "labor_cost_rate": "20",
                                               "nec_threshold": "2000"})
    assert resp.status_code == 302
    assert db_query(app, "SELECT split_mode, set_aside_pct, storage_days, nec_threshold "
                         "FROM shop_setting") == [("custom", D("25.00"), 30, D("2000.00"))]
    owner.post("/settings/books/categories", data={"name": "Booth fees", "position": "300"})
    assert "Booth fees" in text(owner.get("/books/expenses/new"))
    resp = owner.post("/settings/books/categories", data={"name": "Supplies"},
                      follow_redirects=True)
    assert "There's one already" in text(resp)
    page = text(owner.get("/settings/books"))
    assert "72.5¢" in page and "76¢" in page and "Announcement 2026-11" in page
    assert signed_in("tech").get("/settings/books").status_code == 403
