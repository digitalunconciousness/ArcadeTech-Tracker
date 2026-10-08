"""The price book: seeded services, who edits rates, job templates, labor policy and
markup brackets."""

import html
from decimal import Decimal as D

import psycopg
import pytest
from test_parts import add_part

from app.extensions import db
from app.models import ShopSetting
from conftest import db_query, libpq


def text(resp):
    return html.unescape(resp.get_data(as_text=True))


SEEDED = {"LAB-BENCH", "LAB-FIELD", "LAB-AFTER", "FEE-DIAG", "FEE-TRIP", "FEE-MIN", "FEE-RUSH"}


def bracket(lo, hi, mult):
    return {"min_cost": lo, "max_cost": hi, "multiplier": mult}


def service_id(app, code):
    return db_query(app, "SELECT id FROM service WHERE code = :c", c=code)[0][0]


def test_services_are_seeded_off_at_zero(app, signed_in):
    rows = db_query(app, "SELECT code, kind, unit, rate, taxable, warranty_days, active FROM service")
    assert {r.code for r in rows} == SEEDED
    assert all(r.rate == D("0.00") and not r.active and not r.taxable for r in rows)
    labor = {r.code: (r.unit, r.warranty_days) for r in rows if r.kind == "labor"}
    assert labor == {c: ("hour", 365) for c in ("LAB-BENCH", "LAB-FIELD", "LAB-AFTER")}
    assert all(r.warranty_days == 0 for r in rows if r.kind != "labor")
    page = signed_in("tech").get("/pricebook/").get_data(as_text=True)
    assert "Bench labor" in page and "Add service" not in page


def test_owner_sets_a_rate_then_switches_it_on(app, signed_in):
    owner = signed_in("owner")
    sid = service_id(app, "LAB-BENCH")
    form = {"code": "LAB-BENCH", "name": "Bench labor", "kind": "labor", "unit": "hour",
            "rate": "0", "warranty_days": "365", "active": "y"}
    resp = owner.post(f"/pricebook/services/{sid}", data=form)
    assert resp.status_code == 200 and "Set a rate before switching it on" in text(resp)
    resp = owner.post(f"/pricebook/services/{sid}", data={**form, "rate": "85.5"})
    assert resp.status_code == 302
    assert db_query(app, "SELECT rate, active FROM service WHERE id = :i", i=sid) == \
        [(D("85.50"), True)]
    # a discount's amount is set per job, so $0.00 on the book is fine
    resp = owner.post("/pricebook/services/new", data={
        "code": "disc-loyal", "name": "Loyalty discount", "kind": "discount", "unit": "each",
        "rate": "0", "warranty_days": "0", "active": "y"})
    assert resp.status_code == 302
    assert db_query(app, "SELECT code FROM service WHERE kind = 'discount'") == [("DISC-LOYAL",)]


def test_service_codes_are_checked(app, signed_in):
    owner = signed_in("owner")
    base = {"name": "X", "kind": "fee", "unit": "each", "rate": "10", "warranty_days": "0"}
    for code in ("FEE-DIAG", "fee-diag ", "BAD CODE", "-LEAD", "X" * 21):
        resp = owner.post("/pricebook/services/new", data={**base, "code": code})
        assert resp.status_code == 200, code
    for bad in ({"rate": "12.345"}, {"rate": "-1"}, {"warranty_days": ""}, {"kind": "freebie"}):
        resp = owner.post("/pricebook/services/new", data={**base, "code": "OK-1", **bad})
        assert resp.status_code == 200, bad
    assert len(db_query(app, "SELECT 1 FROM service")) == len(SEEDED)


def test_only_owners_edit_services(app, signed_in):
    sid = service_id(app, "FEE-TRIP")
    for role in ("tech", "viewer"):
        client = signed_in(role)
        assert client.get(f"/pricebook/services/{sid}").status_code == 403
        assert client.post(f"/pricebook/services/{sid}", data={"rate": "1"}).status_code == 403
        assert client.get("/pricebook/services/new").status_code == 403


def test_job_template_lines_and_totals(app, signed_in):
    owner, tech, viewer = signed_in("owner"), signed_in("tech"), signed_in("viewer")
    owner.post(f"/pricebook/services/{service_id(app, 'LAB-BENCH')}", data={
        "code": "LAB-BENCH", "name": "Bench labor", "kind": "labor", "unit": "hour",
        "rate": "90", "warranty_days": "365", "active": "y"})
    pid = add_part(owner, sku="KIT-K4600", name="K4600 cap kit", sell_price="24.95",
                   default_cost="11.2000")
    resp = tech.post("/pricebook/templates/new", data={"name": "Monitor cap kit, K4600",
                                                       "est_hours": "1.5"})
    assert resp.status_code == 302
    tid = int(resp.location.rsplit("/", 1)[1])
    sid = service_id(app, "LAB-BENCH")
    tech.post(f"/pricebook/templates/{tid}/lines", data={"item": f"s-{sid}", "qty": "1.5"})
    tech.post(f"/pricebook/templates/{tid}/lines", data={"item": f"p-{pid}", "qty": "1",
                                                         "note": "flyback checked separately"})
    # an inactive service isn't offered
    off = service_id(app, "FEE-RUSH")
    tech.post(f"/pricebook/templates/{tid}/lines", data={"item": f"s-{off}", "qty": "1"})
    assert len(db_query(app, "SELECT 1 FROM job_template_line")) == 2

    page = text(tech.get(f"/pricebook/templates/{tid}"))
    assert "$135.00" in page and "$24.95" in page and "$159.95" in page and "1.5 h labor" in page
    assert "11.2" not in page and "Parts cost" not in page              # techs: no cost
    page = text(viewer.get(f"/pricebook/templates/{tid}"))
    assert "Parts cost $11.20" in page and "parts margin 55.1%" in page
    assert "Add a line" not in page
    assert viewer.post(f"/pricebook/templates/{tid}/lines", data={"item": f"p-{pid}",
                                                                  "qty": "1"}).status_code == 403

    line = db_query(app, "SELECT id FROM job_template_line WHERE part_id = :p", p=pid)[0][0]
    other = tech.post("/pricebook/templates/new", data={"name": "Other"})
    other_id = int(other.location.rsplit("/", 1)[1])
    assert tech.post(f"/pricebook/templates/{other_id}/lines/{line}/delete").status_code == 404
    tech.post(f"/pricebook/templates/{tid}/lines/{line}/delete")
    assert db_query(app, "SELECT part_id FROM job_template_line") == [(None,)]
    assert tech.post("/pricebook/templates/new", data={"name": "Other"}).status_code == 200


def test_template_line_is_a_service_or_a_part_never_both(app, tdb, signed_in):
    owner = signed_in("owner")
    pid = add_part(owner)
    sid = service_id(app, "LAB-BENCH")
    owner.post("/pricebook/templates/new", data={"name": "T"})
    tid = db_query(app, "SELECT id FROM job_template")[0][0]
    with psycopg.connect(libpq(tdb.url("app"))) as c:
        for service, part in ((sid, pid), (None, None)):
            with pytest.raises(psycopg.errors.CheckViolation, match="service_xor_part"):
                c.execute("INSERT INTO job_template_line (template_id, service_id, part_id, qty) "
                          "VALUES (%s, %s, %s, 1)", (tid, service, part))
            c.rollback()


def test_labor_policy(app, signed_in, tdb):
    owner = signed_in("owner")
    page = text(owner.get("/settings/pricing"))
    assert 'value="0.25"' in page and 'value="0.50"' in page
    for bad in ({"labor_increment_hours": "0"}, {"labor_increment_hours": "0.125"},
                {"labor_minimum_hours": "-1"}, {"labor_minimum_hours": ""}):
        data = {"labor_increment_hours": "0.1", "labor_minimum_hours": "1", **bad}
        assert owner.post("/settings/pricing", data=data).status_code == 422, bad
    assert owner.post("/settings/pricing", data={"labor_increment_hours": "0.1",
                                                 "labor_minimum_hours": "1"}).status_code == 302
    with app.app_context():
        row = db.session.get(ShopSetting, True)
        assert (row.labor_increment_hours, row.labor_minimum_hours) == (D("0.10"), D("1.00"))
    with psycopg.connect(libpq(tdb.url("app"))) as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("UPDATE shop_setting SET labor_increment_hours = 0")
    for role in ("tech", "viewer"):
        assert signed_in(role).get("/settings/pricing").status_code == 403


def test_markup_brackets(app, signed_in, tdb):
    owner = signed_in("owner")
    assert "None yet" in text(owner.get("/settings/pricing"))
    pid = add_part(owner, price_mode="markup", sell_price="", default_cost="0.4120")
    assert "no price" in text(owner.get(f"/parts/{pid}"))
    for row in (("0", "1", "3"), ("1", "10", "2"), ("10", "", "1.5")):
        resp = owner.post("/settings/pricing/tiers", data=bracket(*row))
        assert resp.status_code == 302, text(resp)[-1500:]
    page = text(owner.get("/settings/pricing"))
    assert "66.7%" in page and "50.0%" in page and "no limit" in page
    page = text(owner.get(f"/parts/{pid}"))
    assert "$1.24 / each" in page and "× 3 bracket" in page                # 0.412 × 3 = 1.236
    # overlapping brackets are refused, by the database
    for row in (("0.5", "2", "2"), ("20", "", "1.2"), ("1", "5", "2")):
        resp = owner.post("/settings/pricing/tiers", data=bracket(*row))
        assert resp.status_code == 422 and "Overlaps another bracket" in text(resp), row
    for row in (("5", "5", "2"), ("50", "", "0.9"), ("50", "", "2.0001")):
        resp = owner.post("/settings/pricing/tiers", data=bracket(*row))
        assert resp.status_code == 422, row
    with psycopg.connect(libpq(tdb.url("app"))) as c:
        with pytest.raises(psycopg.errors.ExclusionViolation):
            c.execute("INSERT INTO markup_tier (min_cost, max_cost, multiplier) "
                      "VALUES (9.99, 10.01, 2)")
    first = db_query(app, "SELECT id FROM markup_tier WHERE min_cost = 0")[0][0]
    owner.post(f"/settings/pricing/tiers/{first}/delete")
    assert "no price" in text(owner.get(f"/parts/{pid}"))
    assert signed_in("tech").post(f"/settings/pricing/tiers/{first}/delete").status_code == 403
