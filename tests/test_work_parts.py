"""Parts on jobs: issued oldest lot first (one line per lot, at that lot's cost), returned
when the line goes, customer-supplied parts, reservations, and what techs may see."""

from decimal import Decimal as D

from sqlalchemy import select
from test_parts import add_part
from test_work import activate, setup, text

from app.extensions import db
from app.models import WoLine
from app.work import service
from conftest import db_query


def stock_part(owner, app, **overrides):
    pid = add_part(owner, **overrides)
    owner.post(f"/parts/{pid}/receive", data={"qty": "3", "unit_cost": "0.40", "vendor_id": "None",
                                              "date_code": "2219"})
    owner.post(f"/parts/{pid}/receive", data={"qty": "10", "unit_cost": "0.55", "vendor_id": "None",
                                              "date_code": "2301"})
    return pid


def lines(app):
    return db_query(app, "SELECT kind, qty, unit_price, unit_cost, stock_lot_id, warranty_days, "
                         "customer_supplied FROM wo_line ORDER BY id")


def test_issue_spans_lots_fifo_and_returns(app, signed_in):
    owner = signed_in("owner")
    pid = stock_part(owner, app)
    cid, aid, wo_id, job_id = setup(owner, app)
    lots = [r[0] for r in db_query(app, "SELECT id FROM stock_lot ORDER BY id")]
    resp = owner.post(f"/work/jobs/{job_id}/lines/part", data={"part_id": pid, "qty": "5",
                                                               "billable": "y"})
    assert resp.status_code == 302
    assert lines(app) == [
        ("part", D("3.000"), D("1.95"), D("0.4000"), lots[0], 365, False),
        ("part", D("2.000"), D("1.95"), D("0.5500"), lots[1], 365, False),
    ]
    moves = db_query(app, "SELECT reason, qty, lot_id, wo_job_id FROM stock_move "
                          "WHERE reason = 'issue' ORDER BY id")
    assert moves == [("issue", D("-3.000"), lots[0], job_id), ("issue", D("-2.000"), lots[1], job_id)]
    page = text(owner.get(f"/work/jobs/{job_id}"))
    assert "dc 2219" in page and "dc 2301" in page and "$9.75" in page     # 5 × 1.95
    # removing the second line puts its 2 back into the lot it came from
    second = db_query(app, "SELECT id FROM wo_line ORDER BY id")[1][0]
    owner.post(f"/work/lines/{second}/delete")
    assert db_query(app, "SELECT reason, qty, lot_id, wo_job_id FROM stock_move "
                         "WHERE reason = 'return'") == [("return", D("2.000"), lots[1], job_id)]
    on_hand = db_query(app, "SELECT sum(qty) FROM stock_move WHERE part_id = :p", p=pid)[0][0]
    assert on_hand == D("10.000")
    # more than there is: refused, nothing written
    resp = owner.post(f"/work/jobs/{job_id}/lines/part", data={"part_id": pid, "qty": "11"},
                      follow_redirects=True)
    assert "Only 10 on hand" in text(resp) and len(lines(app)) == 1


def test_unpriced_part_needs_a_price(app, signed_in):
    owner, tech = signed_in("owner"), signed_in("tech")
    pid = stock_part(owner, app, sell_price="")
    cid, aid, wo_id, job_id = setup(owner, app)
    resp = tech.post(f"/work/jobs/{job_id}/lines/part", data={"part_id": pid, "qty": "1"},
                     follow_redirects=True)
    assert "has no price yet" in text(resp) and lines(app) == []
    tech.post(f"/work/jobs/{job_id}/lines/part", data={"part_id": pid, "qty": "1",
                                                       "unit_price": "2.50", "billable": "y"})
    assert [r[2] for r in lines(app)] == [D("2.50")]


def test_untracked_part_has_no_moves(app, signed_in):
    owner = signed_in("owner")
    pid = add_part(owner, sku="FLUX-PEN", track_stock="", default_cost="3.10", sell_price="6")
    cid, aid, wo_id, job_id = setup(owner, app)
    owner.post(f"/work/jobs/{job_id}/lines/part", data={"part_id": pid, "qty": "1",
                                                        "billable": "y"})
    assert lines(app) == [("part", D("1.000"), D("6.00"), D("3.1000"), None, 365, False)]
    assert db_query(app, "SELECT count(*) FROM stock_move") == [(0,)]


def test_customer_supplied_part_and_labor_warranty(app, signed_in):
    owner = signed_in("owner")
    cid, aid, wo_id, job_id = setup(owner, app)
    bench = activate(owner, app, "LAB-BENCH", "80")
    owner.post(f"/work/jobs/{job_id}/lines/service", data={
        "service_id": bench, "labor_mode": "flat", "qty": "1", "tech_user_id": "None",
        "billable": "y"})
    owner.post(f"/work/jobs/{job_id}/lines/customer-part", data={
        "description": "Customer's own flyback", "qty": "1"})
    assert lines(app)[1] == ("part", D("1.000"), D("0.00"), None, None, 0, True)
    assert db_query(app, "SELECT count(*) FROM stock_move") == [(0,)]
    with app.app_context():
        labor, theirs = db.session.scalars(select(WoLine).order_by(WoLine.id)).all()
        assert service.warranty_days(labor, True) == 90 and service.warranty_days(labor, False) == 365
        assert service.warranty_days(theirs, True) == 0 and service.line_amount(theirs) == 0
    page = text(owner.get(f"/work/jobs/{job_id}"))
    assert "customer's" in page and "warranty 90 d" in page


def test_unbilled_lines_are_costed_not_charged(app, signed_in):
    owner = signed_in("owner")
    pid = stock_part(owner, app)
    cid, aid, wo_id, job_id = setup(owner, app)
    owner.post(f"/work/jobs/{job_id}/lines/part", data={"part_id": pid, "qty": "2"})   # unbilled
    owner.post(f"/work/jobs/{job_id}/lines/other", data={
        "kind": "discount", "description": "Loyalty", "qty": "1", "unit_price": "1.00",
        "billable": "y"})
    with app.app_context():
        ls = db.session.scalars(select(WoLine).order_by(WoLine.id)).all()
        t = service.totals(ls)
    assert t["charge"] == D("-1.00") and t["unbilled"] == D("3.90") and t["cost"] == D("0.80")
    page = text(owner.get(f"/work/{wo_id}"))
    assert "$3.90 not billed" in page


def test_reservations(app, signed_in):
    owner, tech = signed_in("owner"), signed_in("tech")
    pid = stock_part(owner, app)
    cid, aid, wo_id, job_id = setup(owner, app)
    resp = tech.post(f"/work/jobs/{job_id}/reserve", data={"part_id": pid, "qty": "14"},
                     follow_redirects=True)
    assert "Only 13 of CAP-470-25 available" in text(resp)
    tech.post(f"/work/jobs/{job_id}/reserve", data={"part_id": pid, "qty": "4", "note": "for cap kit"})
    tech.post(f"/work/jobs/{job_id}/reserve", data={"part_id": pid, "qty": "2"})
    with app.app_context():
        assert service.available(pid) == D(7)
    first, second = [r[0] for r in db_query(app, "SELECT id FROM reservation ORDER BY id")]
    tech.post(f"/work/reservations/{first}/issue")
    tech.post(f"/work/reservations/{second}/release")
    assert db_query(app, "SELECT status FROM reservation ORDER BY id") == [("issued",), ("released",)]
    assert sum(r[1] for r in lines(app)) == D(4)
    with app.app_context():
        assert service.available(pid) == D(9)
    resp = tech.post(f"/work/reservations/{first}/issue", follow_redirects=True)
    assert "already closed" in text(resp)


def test_techs_see_prices_not_costs(app, signed_in):
    owner, tech, viewer = signed_in("owner"), signed_in("tech"), signed_in("viewer")
    pid = stock_part(owner, app)
    cid, aid, wo_id, job_id = setup(owner, app)
    tech.post(f"/work/jobs/{job_id}/lines/part", data={"part_id": pid, "qty": "4", "billable": "y"})
    tech.post(f"/work/jobs/{job_id}/lines/other", data={
        "kind": "sublet", "description": "Monitor rebuild", "qty": "1", "unit_price": "90",
        "unit_cost": "55", "billable": "y"})
    assert db_query(app, "SELECT unit_cost FROM wo_line WHERE kind = 'sublet'") == [(None,)]
    assert "$1.95" in text(tech.get(f"/work/jobs/{job_id}"))      # prices: yes
    for path in (f"/work/jobs/{job_id}", f"/work/{wo_id}"):
        page = text(tech.get(path))
        assert "$1.75" not in page and "margin" not in page, path   # costs: no
        assert "$1.75" in text(viewer.get(path)), path             # 3 × 0.40 + 1 × 0.55
        assert "$1.75" in text(owner.get(path)), path
    # a tech editing a sublet line can't set its cost
    sublet = db_query(app, "SELECT id FROM wo_line WHERE kind = 'sublet'")[0][0]
    tech.post(f"/work/lines/{sublet}/edit", data={"description": "Monitor rebuild",
                                                  "unit_price": "95", "unit_cost": "1",
                                                  "billable": "y"})
    assert db_query(app, "SELECT unit_price, unit_cost FROM wo_line WHERE id = :i", i=sublet) == \
        [(D("95.00"), None)]


def test_line_edit_rules(app, signed_in):
    owner = signed_in("owner")
    pid = stock_part(owner, app)
    cid, aid, wo_id, job_id = setup(owner, app)
    owner.post(f"/work/jobs/{job_id}/lines/part", data={"part_id": pid, "qty": "2", "billable": "y"})
    line = db_query(app, "SELECT id FROM wo_line")[0][0]
    page = text(owner.get(f"/work/lines/{line}/edit"))
    assert "remove the line and issue again" in page
    owner.post(f"/work/lines/{line}/edit", data={"description": "470µF cap", "qty": "9",
                                                 "unit_price": "1.50", "billable": "y"})
    assert db_query(app, "SELECT description, qty, unit_price FROM wo_line") == \
        [("470µF cap", D("2.000"), D("1.50"))]                  # qty unchanged: stock didn't move
