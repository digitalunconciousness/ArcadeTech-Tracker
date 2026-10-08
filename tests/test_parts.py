"""Parts, vendors and stock through the pages: who may do what, who sees costs, the
count sheet, search by equivalent."""

import html
import re
from decimal import Decimal as D

from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Part, StockLot, StockMove, Vendor
from conftest import db_query

PART = {"sku": "CAP-470-25", "name": "470µF 25V radial, 105°C", "category": "caps",
        "manufacturer": "Nichicon", "mpn": "UPW1E471MPD", "equivalents": "UHE1E471MPD, EEU-FR1E471",
        "unit": "each", "price_mode": "fixed", "sell_price": "1.95", "default_cost": "0.4120",
        "taxable": "y", "track_stock": "y", "reorder_point": "10", "reorder_qty": "50",
        "preferred_vendor_id": "None", "bin": "C-07"}


def text(resp):
    return html.unescape(resp.get_data(as_text=True))


def add_part(client, **overrides):
    resp = client.post("/parts/new", data={**PART, **overrides})
    assert resp.status_code == 302, resp.get_data(as_text=True)[-2000:]
    return int(resp.location.rsplit("/", 1)[1])


def part_row(app, pid):
    with app.app_context():
        return db.session.get(Part, pid)


def moves(app, pid):
    return [(r.reason, r.qty) for r in db_query(
        app, "SELECT reason, qty FROM stock_move WHERE part_id = :p ORDER BY id", p=pid)]


def test_owner_adds_part_with_pricing(app, signed_in):
    owner = signed_in("owner")
    pid = add_part(owner)
    p = part_row(app, pid)
    assert p.active and p.default_cost == D("0.4120") and p.sell_price == D("1.95")
    assert p.equivalents == ["UHE1E471MPD", "EEU-FR1E471"]
    page = owner.get(f"/parts/{pid}").get_data(as_text=True)
    assert "$0.412" in page and "$1.95" in page and "Margin" in page
    # SKU is unique, case-insensitively
    resp = owner.post("/parts/new", data={**PART, "sku": "cap-470-25"})
    assert resp.status_code == 200 and "Another part has that SKU" in resp.get_data(as_text=True)


def test_decimal_inputs_are_checked_not_rounded(app, signed_in):
    owner = signed_in("owner")
    for field, value in (("sell_price", "1.955"), ("default_cost", "0.41205"),
                         ("sell_price", "NaN"), ("default_cost", "Infinity"),
                         ("sell_price", "-1"), ("reorder_qty", "0"), ("sell_price", "1e12")):
        resp = owner.post("/parts/new", data={**PART, "sku": f"X-{field}-{value}"[:40],
                                              field: value})
        assert resp.status_code == 200, (field, value)
    assert db_query(app, "SELECT count(*) FROM part") == [(0,)]


def test_tech_sees_no_costs_and_cannot_set_them(app, signed_in):
    owner, tech = signed_in("owner"), signed_in("tech")
    pid = add_part(owner)
    owner.post(f"/parts/{pid}/receive", data={"qty": "20", "unit_cost": "0.3999",
                                              "vendor_id": "None", "date_code": "2219"})
    page = tech.get(f"/parts/{pid}").get_data(as_text=True)
    assert "$1.95" in page and "20" in page                      # price and stock: yes
    for secret in ("0.412", "0.3999", "Margin", "Stock value", "Unit cost", "unit_cost"):
        assert secret not in page, secret
    assert "Default cost" not in tech.get(f"/parts/{pid}/edit").get_data(as_text=True)
    # a tech's edit leaves pricing alone, even if the fields are posted
    resp = tech.post(f"/parts/{pid}/edit", data={**PART, "name": "Renamed by tech",
                                                 "default_cost": "9.9999", "sell_price": "0.01",
                                                 "price_mode": "markup", "active": "y"})
    assert resp.status_code == 302
    p = part_row(app, pid)
    assert p.name == "Renamed by tech"
    assert (p.default_cost, p.sell_price, p.price_mode) == (D("0.4120"), D("1.95"), "fixed")
    # a tech's receipt is costed at the default cost, whatever is posted
    tech.post(f"/parts/{pid}/receive", data={"qty": "5", "unit_cost": "0.0001", "vendor_id": "None"})
    costs = [r[0] for r in db_query(app, "SELECT unit_cost FROM stock_lot ORDER BY id")]
    assert costs == [D("0.3999"), D("0.4120")]
    # the list, search and vendor pages show no cost either
    for path in ("/parts", "/search?q=cap-470", "/vendors"):
        assert "0.412" not in tech.get(path).get_data(as_text=True), path


def test_tech_cannot_receive_a_part_with_no_cost(app, signed_in):
    owner, tech = signed_in("owner"), signed_in("tech")
    pid = add_part(owner, default_cost="")
    resp = tech.post(f"/parts/{pid}/receive", data={"qty": "5", "vendor_id": "None"},
                     follow_redirects=True)
    assert "no default cost yet" in text(resp)
    assert moves(app, pid) == []


def test_viewer_sees_costs_but_changes_nothing(app, signed_in):
    owner, viewer = signed_in("owner"), signed_in("viewer")
    pid = add_part(owner)
    owner.post(f"/parts/{pid}/receive", data={"qty": "20", "unit_cost": "0.3999",
                                              "vendor_id": "None"})
    page = viewer.get(f"/parts/{pid}").get_data(as_text=True)
    assert "$0.3999" in page and "Margin" in page and "Receive (no PO)" not in page
    for path, data in ((f"/parts/{pid}/edit", PART), ("/parts/new", PART),
                       (f"/parts/{pid}/receive", {"qty": "1"}),
                       (f"/parts/{pid}/adjust", {"direction": "up", "qty": "1", "note": "x"}),
                       (f"/parts/{pid}/scrap", {"qty": "1"}), ("/parts/count", {}),
                       ("/vendors/new", {"name": "V"})):
        assert viewer.post(path, data=data).status_code == 403, path
    assert viewer.get("/parts/count").status_code == 403
    assert moves(app, pid) == [("receive", D(20))]


def test_adjust_and_scrap(app, signed_in):
    owner = signed_in("owner")
    pid = add_part(owner)
    owner.post(f"/parts/{pid}/receive", data={"qty": "10", "unit_cost": "0.40", "vendor_id": "None"})
    owner.post(f"/parts/{pid}/receive", data={"qty": "10", "unit_cost": "0.45", "vendor_id": "None",
                                              "date_code": "1999"})
    lots = [r[0] for r in db_query(app, "SELECT id FROM stock_lot ORDER BY id")]
    # adjust needs a reason and a non-zero change
    page = owner.post(f"/parts/{pid}/adjust", data={"direction": "down", "qty": "2",
                                                    "lot_id": "None", "note": ""},
                      follow_redirects=True).get_data(as_text=True)
    assert "Why" in page
    for bad in ({"qty": "0"}, {"qty": "-2"}, {"direction": "sideways"}):
        owner.post(f"/parts/{pid}/adjust", data={"direction": "down", "lot_id": "None",
                                                  "note": "x", **bad})
    owner.post(f"/parts/{pid}/adjust", data={"direction": "down", "qty": "2", "lot_id": "None",
                                              "note": "missing"})
    owner.post(f"/parts/{pid}/adjust", data={"direction": "up", "qty": "1", "lot_id": str(lots[0]),
                                              "note": "found one"})
    # scrap the bad date code from its own lot, not FIFO
    owner.post(f"/parts/{pid}/scrap", data={"qty": "10", "lot_id": str(lots[1]), "note": "old dc"})
    page = owner.post(f"/parts/{pid}/scrap", data={"qty": "10", "lot_id": "None"},
                      follow_redirects=True).get_data(as_text=True)
    assert "Only 9 on hand" in page
    assert moves(app, pid) == [("receive", D(10)), ("receive", D(10)), ("adjust", D(-2)),
                               ("adjust", D(1)), ("scrap", D(-10))]
    by_lot = dict(db_query(app, "SELECT lot_id, sum(qty) FROM stock_move GROUP BY lot_id"))
    assert by_lot == {lots[0]: D(9), lots[1]: D(0)}


def test_count_sheet(app, signed_in):
    owner = signed_in("owner")
    a = add_part(owner)
    b = add_part(owner, sku="IC-74LS245", name="Octal bus transceiver", bin="IC-01",
                 equivalents="74HCT245", default_cost="0.35")
    c = add_part(owner, sku="SOLDER-63-37", name="Solder 63/37", track_stock="", bin="Z")
    for pid in (a, b):
        owner.post(f"/parts/{pid}/receive", data={"qty": "10", "unit_cost": "0.40",
                                                  "vendor_id": "None"})
    sheet = owner.get("/parts/count").get_data(as_text=True)
    assert f'name="count-{a}"' in sheet and f'name="count-{b}"' in sheet
    assert f'name="count-{c}"' not in sheet                           # not stocked
    sheet = owner.get("/parts/count?bin=c-").get_data(as_text=True)
    assert f'name="count-{a}"' in sheet and f'name="count-{b}"' not in sheet

    # one bad box: nothing at all is saved
    resp = owner.post("/parts/count", data={f"count-{a}": "7", f"seen-{a}": "10",
                                            f"count-{b}": "lots", f"seen-{b}": "10"})
    assert resp.status_code == 422 and "isn't a count" in text(resp)
    assert 'value="7"' in resp.get_data(as_text=True)                # what you typed is kept
    assert moves(app, a) == [("receive", D(10))]

    # stock moved after the sheet was loaded: that part is skipped, the rest saved
    resp = owner.post("/parts/count", data={f"count-{a}": "7", f"seen-{a}": "10",
                                            f"count-{b}": "12", f"seen-{b}": "9",
                                            "note": "Q4 count"}, follow_redirects=True)
    page = text(resp)
    assert "Counted 1 part; 1 changed." in page
    assert "weren't counted: IC-74LS245." in page
    assert moves(app, a) == [("receive", D(10)), ("count", D(-3))]
    assert moves(app, b) == [("receive", D(10))]
    assert moves(app, c) == []
    with app.app_context():
        move = db.session.scalar(select(StockMove).where(StockMove.reason == "count"))
        assert move.note == "Q4 count" and move.created_by == owner.user["id"]
        audit = db.session.scalar(select(AuditLog).where(AuditLog.table_name == "stock_move",
                                                         AuditLog.row_id == str(move.id)))
        assert audit.user_id == owner.user["id"] and audit.action == "I"

    # counting what's there changes nothing
    owner.post("/parts/count", data={f"count-{b}": "10", f"seen-{b}": "10.000"})
    assert moves(app, b) == [("receive", D(10))]


def test_tech_can_count_and_adjust(app, signed_in):
    owner, tech = signed_in("owner"), signed_in("tech")
    pid = add_part(owner)
    owner.post(f"/parts/{pid}/receive", data={"qty": "10", "unit_cost": "0.40", "vendor_id": "None"})
    assert "$0.40" not in tech.get("/parts/count").get_data(as_text=True)
    tech.post("/parts/count", data={f"count-{pid}": "12", f"seen-{pid}": "10"})
    tech.post(f"/parts/{pid}/adjust", data={"direction": "down", "qty": "1", "lot_id": "None",
                                             "note": "dropped"})
    assert moves(app, pid)[1:] == [("count", D(2)), ("adjust", D(-1))]


def test_low_stock_filter_and_search_by_equivalent(app, signed_in):
    owner = signed_in("owner")
    a = add_part(owner)                                      # reorder at 10, nothing on hand
    b = add_part(owner, sku="IC-74LS245", name="Octal bus transceiver", reorder_point="",
                 equivalents="74HCT245; 74ALS245")
    owner.post(f"/parts/{b}/receive", data={"qty": "3", "unit_cost": "0.35", "vendor_id": "None"})
    low = owner.get("/parts?low=1").get_data(as_text=True)
    assert f'href="/parts/{a}"' in low and f'href="/parts/{b}"' not in low
    hits = owner.get("/parts?q=hct245").get_data(as_text=True)
    assert f'href="/parts/{b}"' in hits and f'href="/parts/{a}"' not in hits
    page = owner.get("/search?q=74als245").get_data(as_text=True)
    assert f"/parts/{b}" in page and f"/parts/{a}" not in page
    assert f"/parts/{a}" in owner.get("/search?q=UPW1E471").get_data(as_text=True)   # MPN


def test_vendors(app, signed_in):
    owner, tech, viewer = signed_in("owner"), signed_in("tech"), signed_in("viewer")
    resp = tech.post("/vendors/new", data={"name": "Example Electronics", "website":
                                           "https://parts.example.com", "account_ref": "A-1001",
                                           "resale_cert_on_file": "y"})
    assert resp.status_code == 302
    vid = int(resp.location.rsplit("/", 1)[1])
    with app.app_context():
        v = db.session.get(Vendor, vid)
        assert v.active and v.resale_cert_on_file
    pid = add_part(owner, preferred_vendor_id=str(vid))
    owner.post(f"/parts/{pid}/receive", data={"qty": "4", "unit_cost": "0.3999",
                                              "vendor_id": str(vid), "date_code": "2219"})
    assert "$0.3999" in owner.get(f"/vendors/{vid}").get_data(as_text=True)
    assert "$0.3999" in viewer.get(f"/vendors/{vid}").get_data(as_text=True)
    page = tech.get(f"/vendors/{vid}").get_data(as_text=True)
    assert "CAP-470-25" in page and "0.3999" not in page
    assert viewer.get(f"/vendors/{vid}/edit").status_code == 403
    # an inactive vendor stays selectable on the part that already uses it
    owner.post(f"/vendors/{vid}/edit", data={"name": "Example Electronics"})
    assert re.search(rf'<option selected value="{vid}">Example Electronics',
                     owner.get(f"/parts/{pid}/edit").get_data(as_text=True))
    assert "Example Electronics" in owner.get("/search?q=a-1001").get_data(as_text=True)


def test_untracked_part_page_has_no_stock_forms(app, signed_in):
    owner = signed_in("owner")
    pid = add_part(owner, sku="FLUX-PEN", track_stock="")
    page = owner.get(f"/parts/{pid}").get_data(as_text=True)
    assert "not tracked" in page and "Receive (no PO)" not in page
    resp = owner.post(f"/parts/{pid}/receive", data={"qty": "1", "unit_cost": "5",
                                                     "vendor_id": "None"}, follow_redirects=True)
    assert "isn't stocked" in text(resp)
    with app.app_context():
        assert db.session.scalar(select(StockLot.id)) is None
