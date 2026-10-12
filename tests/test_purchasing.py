"""Purchasing (Phase 5b): the shopping list and its suggestions, Order for this job (set
aside at once), draft POs per vendor, PO numbers at ordering, receiving (lot, move,
receipt; the job's part is already set aside; the WO leaves Waiting on parts), FIFO
between jobs, cancel and close, shipments and carriers, roles."""

from decimal import Decimal as D

import psycopg
import pytest
from test_billing import today
from test_estimates import approve_by_link, built, send, set_terms
from test_partlinks import add_vendor
from test_parts import add_part
from test_work import setup, text

from app.purchasing import service
from conftest import db_query, libpq


def po_of(app):
    return db_query(app, "SELECT id FROM purchase_order ORDER BY id DESC LIMIT 1")[0][0]


def order_for_job(client, job, pid, qty="2", wait=True, **extra):
    data = {"part_id": str(pid), "qty": qty, **extra}
    if wait:
        data["wait"] = "y"
    resp = client.post(f"/work/jobs/{job}/order", data=data)
    assert resp.status_code == 302, text(resp)[-1500:]
    return resp


def make_po(client, app, ordered=True, **extra):
    resp = client.post("/purchasing/create-pos")
    assert resp.status_code == 302, text(resp)[-1500:]
    po = po_of(app)
    if ordered:
        resp = client.post(f"/purchasing/orders/{po}/ordered", data={
            "ordered_on": str(today(app)),
            "vendor_order_ref": "W123456", "paid_by_user_id": "None", **extra})
        assert resp.status_code == 302
    return po


def lines_of(app, po):
    return [r[0] for r in db_query(app, "SELECT id FROM po_line WHERE po_id = :p ORDER BY id",
                                   p=po)]


def receive(client, po, counts, **extra):
    data = {f"qty-{line}": str(qty) for line, qty in counts.items()}
    data.update(extra)
    return client.post(f"/purchasing/orders/{po}/receive", data=data, follow_redirects=True)


def wo_status(app, wo_id):
    return db_query(app, "SELECT status, parts_arrived_at IS NOT NULL FROM work_order "
                         "WHERE id = :w", w=wo_id)[0]


def test_suggestions_and_the_list(app, signed_in):
    owner = signed_in("owner")
    vid = add_vendor(owner)
    cid, aid, pid, est_id = built(owner, app)        # quotes 10 of CAP-470-25
    owner.post(f"/parts/{pid}/receive", data={"qty": "4", "unit_cost": "0.40",
                                              "vendor_id": "None"})
    set_terms(owner)
    approve_by_link(app.test_client(), send(owner, est_id))
    owner.post(f"/estimates/{est_id}/convert", data={"kind": "bench",
                                                     "techs": [str(owner.user["id"])]})
    [s] = service_suggestions(app)
    assert (s["on_hand"], s["reserved"], s["on_order"], s["projected"], s["qty"]) == \
        (D(4), D(10), D(0), D(-6), D(50))                 # max(reorder qty, 10 − −6)
    page = text(owner.get("/purchasing"))
    assert "CAP-470-25" in page and "short" in page and "WO-" in page
    assert 'value="50"' in page
    resp = owner.post(f"/purchasing/suggest/{pid}", data={"qty": "50"}, follow_redirects=True)
    assert "is on the list" in text(resp)
    assert db_query(app, "SELECT qty, vendor_id, unit_cost, status, po_id FROM po_line") == \
        [(D(50), None, D("0.4120"), "to_order", None)]       # the part has no usual vendor
    assert service_suggestions(app) == []                  # 4 + 50 − 10 = 44: covered
    # a manual line, then an edit: its vendor and cost
    other = add_part(owner, sku="POT-10K", name="10k pot", reorder_point="", reorder_qty="")
    owner.post("/purchasing/list", data={"part_id": str(other), "qty": "3", "vendor_id": "None"})
    line = db_query(app, "SELECT id, vendor_id FROM po_line WHERE part_id = :p", p=other)[0]
    assert line[1] is None
    resp = owner.post(f"/purchasing/lines/{line[0]}/edit", data={
        "qty": "4", "vendor_id": str(vid), "unit_cost": "1.25"})
    assert resp.status_code == 302
    assert db_query(app, "SELECT qty, vendor_id, unit_cost FROM po_line WHERE id = :i",
                    i=line[0]) == [(D(4), vid, D("1.25"))]


def service_suggestions(app):
    with app.app_context():
        return [{k: v for k, v in s.items() if k != "part"} for s in service.suggestions()]


def test_order_for_job_then_receive_releases_the_wo(app, signed_in):
    """The Phase 5 exit: receiving a line tied to a job fills what's set aside for it and
    moves that WO out of Waiting on parts."""
    owner = signed_in("owner")
    vid = add_vendor(owner)
    pid = add_part(owner, preferred_vendor_id=str(vid))
    cid, aid, wo_id, job = setup(owner, app)
    resp = order_for_job(owner, job, pid, "2")
    assert db_query(app, "SELECT qty, status, wo_job_id IS NOT NULL, reservation_id IS NOT NULL "
                         "FROM po_line") == [(D(2), "to_order", True, True)]
    res = db_query(app, "SELECT id, qty, status, wo_job_id FROM reservation")[0]
    assert res[1:] == (D(2), "reserved", job)              # set aside before it's in stock
    assert wo_status(app, wo_id) == ("waiting_parts", False)
    assert "CAP-470-25 × 2 to order" in text(owner.get(f"/work/{wo_id}"))
    assert "Bought for this job" in text(owner.get(f"/work/jobs/{job}"))
    po = make_po(owner, app)
    number, status, ref = db_query(app, "SELECT number, status, vendor_order_ref "
                                        "FROM purchase_order")[0]
    assert (number[:3], number[-5:], status, ref) == ("PO-", "-0001", "ordered", "W123456")
    [line] = lines_of(app, po)
    # one in: the WO still waits
    resp = receive(owner, po, {line: "1"}, **{f"dc-{line}": "2419"})
    assert "Received 1 line" in text(resp) and "back In progress" not in text(resp)
    assert db_query(app, "SELECT status FROM purchase_order") == [("partially_received",)]
    lot = db_query(app, "SELECT unit_cost, vendor_id, date_code, po_line_id FROM stock_lot")
    assert lot == [(D("0.4120"), vid, "2419", line)]
    assert db_query(app, "SELECT qty, reason, po_line_id FROM stock_move") == \
        [(D(1), "receive", line)]
    assert wo_status(app, wo_id) == ("waiting_parts", False)
    # the rest: the WO is back In progress and flagged
    resp = receive(owner, po, {line: "1"})
    assert f"that {db_query(app, 'SELECT number FROM work_order')[0][0]} was waiting on" \
        in text(resp)
    assert db_query(app, "SELECT status FROM po_line") == [("received",)]
    assert db_query(app, "SELECT status FROM purchase_order") == [("received",)]
    assert wo_status(app, wo_id) == ("in_progress", True)
    assert "parts in" in text(owner.get("/work"))
    assert db_query(app, "SELECT sum(qty) FROM po_receipt") == [(D(2),)]
    # the job uses what was set aside for it
    resp = owner.post(f"/work/reservations/{res[0]}/issue", follow_redirects=True)
    assert "Issued 2" in text(resp)
    # changing the status by hand clears the flag
    owner.post(f"/work/{wo_id}/status", data={"status": "ready"})
    assert wo_status(app, wo_id) == ("ready", False)


def test_the_oldest_job_gets_the_stock_first(app, signed_in):
    owner = signed_in("owner")
    vid = add_vendor(owner)
    pid = add_part(owner, preferred_vendor_id=str(vid))
    _, _, first, first_job = setup(owner, app)
    _, _, second, second_job = setup(owner, app)
    order_for_job(owner, first_job, pid, "2")
    order_for_job(owner, second_job, pid, "2")
    po = make_po(owner, app)
    a, b = lines_of(app, po)
    # two come in on the second job's line: the first job's reservation is older, so the
    # stock covers it; the second job still waits on its own line
    receive(owner, po, {b: "2"})
    assert wo_status(app, first)[0] == "waiting_parts"     # its own line is still on order
    receive(owner, po, {a: "2"})
    assert wo_status(app, first) == ("in_progress", True)
    assert wo_status(app, second) == ("in_progress", True)


def test_fifo_when_stock_is_short(app, signed_in):
    owner = signed_in("owner")
    vid = add_vendor(owner)
    pid = add_part(owner, preferred_vendor_id=str(vid))
    _, _, first, first_job = setup(owner, app)
    _, _, second, second_job = setup(owner, app)
    order_for_job(owner, first_job, pid, "2")
    order_for_job(owner, second_job, pid, "2")
    po = make_po(owner, app)
    a, b = lines_of(app, po)
    receive(owner, po, {b: "2"})            # in on the second job's line…
    with app.app_context():
        from app.extensions import db
        from app.models import WorkOrder
        # …but the first job set aside first, so the stock is its
        assert service.work.waiting_on(db.session.get(WorkOrder, second)) == \
            ["CAP-470-25 × 2 set aside, not in stock"]
        assert service.work.waiting_on(db.session.get(WorkOrder, first)) == \
            ["CAP-470-25 × 2 on order"]
    # giving up on the rest of the order settles the first job (its stock is here)
    resp = owner.post(f"/purchasing/orders/{po}/close", follow_redirects=True)
    assert db_query(app, "SELECT status FROM po_line ORDER BY id") == \
        [("cancelled",), ("received",)]
    assert "back In progress" in text(resp)
    assert wo_status(app, first) == ("in_progress", True)
    assert wo_status(app, second)[0] == "waiting_parts"
    # the shortfall is on the list as a suggestion for the second job
    page = text(owner.get("/purchasing"))
    assert "short" in page and db_query(app, "SELECT number FROM work_order WHERE id = :w",
                                        w=second)[0][0] in page


def test_numbers_drafts_and_dates(app, signed_in):
    owner = signed_in("owner")
    vid = add_vendor(owner)
    pid = add_part(owner, preferred_vendor_id=str(vid))
    owner.post(f"/purchasing/suggest/{pid}", data={"qty": "50"})
    draft = make_po(owner, app, ordered=False)
    assert db_query(app, "SELECT number, status FROM purchase_order") == [(None, "draft")]
    # dropping a draft puts its lines back on the list and uses no number
    owner.post(f"/purchasing/orders/{draft}/cancel")
    assert db_query(app, "SELECT po_id, status FROM po_line") == [(None, "to_order")]
    resp = owner.post(f"/purchasing/orders/{make_po(owner, app, ordered=False)}/ordered",
                      data={"ordered_on": "2999-01-01", "paid_by_user_id": "None"},
                      follow_redirects=True)
    assert "can't be dated in the future" in text(resp)
    po = po_of(app)
    owner.post(f"/purchasing/orders/{po}/ordered", data={
        "ordered_on": "2026-01-05", "paid_by_user_id": str(owner.user["id"])})
    assert db_query(app, "SELECT number, ordered_on::text, paid_by_user_id FROM purchase_order "
                         "WHERE id = :p", p=po) == [("PO-2026-0001", "2026-01-05",
                                                     owner.user["id"])]
    assert "own card" in text(owner.get(f"/purchasing/orders/{po}"))
    # a line with no vendor stays on the list
    other = add_part(owner, sku="POT-10K", name="10k pot")
    owner.post("/purchasing/list", data={"part_id": str(other), "qty": "1", "vendor_id": "None"})
    resp = owner.post("/purchasing/create-pos", follow_redirects=True)
    assert "pick one for each line" in text(resp)


def test_cancel_close_and_guards(app, signed_in, tdb):
    owner = signed_in("owner")
    vid = add_vendor(owner)
    pid = add_part(owner, preferred_vendor_id=str(vid))
    _, _, wo_id, job = setup(owner, app)
    # removing a line bought for a job releases what was set aside
    order_for_job(owner, job, pid, "2")
    line = db_query(app, "SELECT id FROM po_line")[0][0]
    owner.post(f"/purchasing/lines/{line}/remove")
    assert db_query(app, "SELECT status FROM reservation") == [("released",)]
    assert db_query(app, "SELECT count(*) FROM po_line") == [(0,)]
    # cancelling an order keeps the job's part set aside: it still needs it
    order_for_job(owner, job, pid, "2")
    po = make_po(owner, app)
    owner.post(f"/purchasing/orders/{po}/cancel")
    assert db_query(app, "SELECT status FROM purchase_order WHERE id = :p", p=po) == \
        [("cancelled",)]
    assert db_query(app, "SELECT status FROM reservation ORDER BY id") == \
        [("released",), ("reserved",)]
    # something received: cancel refused, close it instead
    order_for_job(owner, job, pid, "3")
    po = make_po(owner, app)
    [line] = lines_of(app, po)
    receive(owner, po, {line: "1"})
    resp = owner.post(f"/purchasing/orders/{po}/cancel", follow_redirects=True)
    assert "close it instead" in text(resp)
    owner.post(f"/purchasing/orders/{po}/close")
    assert db_query(app, "SELECT status FROM purchase_order WHERE id = :p", p=po) == [("closed",)]
    # the database keeps ordered lines and receipts whole
    with psycopg.connect(libpq(tdb.url("app"))) as c:
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            c.execute("DELETE FROM po_line WHERE id = %s", (line,))
        c.rollback()
        for sql in ("UPDATE po_receipt SET qty = 5", "DELETE FROM po_receipt"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)
            c.rollback()


def test_parts_we_dont_stock(app, signed_in):
    owner = signed_in("owner")
    vid = add_vendor(owner)
    pid = add_part(owner, sku="FLUX-1", name="Flux pen", track_stock="", reorder_point="",
                   reorder_qty="", preferred_vendor_id=str(vid))
    _, _, wo_id, job = setup(owner, app)
    order_for_job(owner, job, pid, "1")
    assert db_query(app, "SELECT count(*) FROM reservation") == [(0,)]
    po = make_po(owner, app)
    receive(owner, po, {lines_of(app, po)[0]: "1"})
    assert db_query(app, "SELECT lot_id FROM po_receipt") == [(None,)]
    assert db_query(app, "SELECT count(*) FROM stock_lot") == [(0,)]
    assert wo_status(app, wo_id) == ("in_progress", True)


@pytest.mark.parametrize("number,code", [
    ("1Z999AA10123456784", "ups"), ("9400 1000 0000 0000 0000 00", "usps"),
    ("EC123456789US", "usps"), ("123456789012", "fedex"), ("1234567890", "dhl"),
    ("JJD0099999999", "dhl"), ("ABCD1234", None)])
def test_carrier_guess(app, number, code):
    with app.app_context():
        guess = service.guess_carrier(service.clean_tracking(number))
        assert (guess.code if guess else None) == code


def test_shipments_and_carriers(app, signed_in):
    owner = signed_in("owner")
    vid = add_vendor(owner)
    pid = add_part(owner, preferred_vendor_id=str(vid))
    _, _, wo_id, job = setup(owner, app)
    order_for_job(owner, job, pid, "2")
    po = make_po(owner, app)
    resp = owner.post(f"/purchasing/orders/{po}/shipments", data={
        "direction": "inbound_purchase", "tracking_number": "1z999aa1 0123456784",
        "carrier_id": "None"}, follow_redirects=True)
    page = text(resp)
    assert "Tracking added" in page
    assert 'href="https://www.ups.com/track?loc=en_US&tracknum=1Z999AA10123456784"' in page
    ship = db_query(app, "SELECT id, tracking_number, status FROM shipment")[0]
    assert ship[1:] == ("1Z999AA10123456784", "in_transit")
    # delivered isn't received
    owner.post(f"/shipments/{ship[0]}", data={"status": "delivered", "carrier_id": "None"})
    assert db_query(app, "SELECT status, delivered_on IS NOT NULL FROM shipment") == \
        [("delivered", True)]
    assert db_query(app, "SELECT count(*) FROM stock_move") == [(0,)]
    assert wo_status(app, wo_id)[0] == "waiting_parts"
    # a customer's box on the WO
    resp = owner.post(f"/work/{wo_id}/shipments", data={
        "direction": "inbound_customer", "tracking_number": "9400100000000000000000",
        "carrier_id": "None"}, follow_redirects=True)
    assert "tools.usps.com" in text(owner.get(f"/work/{wo_id}"))
    resp = owner.post(f"/work/{wo_id}/shipments", data={
        "direction": "outbound_customer", "tracking_number": "no!", "carrier_id": "None"},
        follow_redirects=True)
    assert "4 to 40 letters" in text(resp)
    # search: PO number, their order number, tracking number
    number = db_query(app, "SELECT number FROM purchase_order")[0][0]
    for q in (number, "W123456", "1Z999AA1"):
        assert f"/purchasing/orders/{po}" in text(owner.get("/search", query_string={"q": q}))
    # carriers live in settings; a template needs {number}
    ups = db_query(app, "SELECT id FROM carrier WHERE code = 'ups'")[0][0]
    resp = owner.post(f"/settings/carriers/{ups}", data={
        "code": "ups", "name": "UPS", "url_template": "https://www.ups.com/track",
        "number_pattern": "^1Z[0-9A-Z]{16}$", "position": "10", "active": "y"})
    assert "Put {number} where" in text(resp)
    resp = owner.post(f"/settings/carriers/{ups}", data={
        "code": "ups", "name": "UPS", "url_template": "https://example.com/t/{number}",
        "number_pattern": "^1Z[0-9A-Z]{16}$", "position": "10", "active": "y"})
    assert resp.status_code == 302
    assert "https://example.com/t/1Z999AA10123456784" in text(owner.get(f"/purchasing/orders/{po}"))
    assert signed_in("tech").get("/settings/carriers").status_code == 403


def test_roles(app, signed_in):
    owner = signed_in("owner")
    vid = add_vendor(owner)
    pid = add_part(owner, preferred_vendor_id=str(vid))
    tech = signed_in("tech")
    _, _, wo_id, job = setup(tech, app)
    order_for_job(tech, job, pid, "2")                       # techs order for a job
    assert tech.post("/purchasing/create-pos").status_code == 403
    po = make_po(owner, app, ordered=False)
    draft_line = lines_of(app, po)[0]
    assert tech.post(f"/purchasing/lines/{draft_line}/edit", data={"qty": "9"}).status_code == 403
    assert tech.post(f"/purchasing/lines/{draft_line}/remove").status_code == 403
    owner.post(f"/purchasing/orders/{po}/ordered", data={"ordered_on": str(today(app)),
                                                         "paid_by_user_id": "None"})
    page = text(tech.get(f"/purchasing/orders/{po}"))
    assert "CAP-470-25" in page and "0.412" not in page and "Mark ordered" not in page
    assert tech.post(f"/purchasing/orders/{po}/close").status_code == 403
    resp = receive(tech, po, {lines_of(app, po)[0]: "2"})    # and receive
    assert "Received 1 line" in text(resp)
    assert db_query(app, "SELECT unit_cost FROM stock_lot") == [(D("0.4120"),)]
    viewer = signed_in("viewer")
    assert "0.412" in text(viewer.get(f"/purchasing/orders/{po}"))
    assert viewer.get("/purchasing").status_code == 200
    assert viewer.post(f"/work/jobs/{job}/order", data={"part_id": str(pid), "qty": "1"}) \
        .status_code == 403
    assert viewer.post(f"/purchasing/orders/{po}/receive").status_code == 403
    assert viewer.post(f"/purchasing/suggest/{pid}", data={"qty": "1"}).status_code == 403
