"""The desktop (Phase 9a): the theme setting and the sidebar, records rendered for the split
pane, global search as JSON for the command bar, the power tables on the eight lists
(filters, sort, CSV, cost columns), saved views, and bulk actions on work orders."""

import csv
import html
import io
import json
import re

import pytest
from test_assets import asset, customer
from test_billing import billable, issue, issued_id
from test_work import new_wo, text

from conftest import db_query

LISTS = ("/work", "/estimates", "/customers", "/assets", "/invoices", "/payments", "/parts",
         "/purchasing/orders")


def desk(html):
    """The desktop half of a list page."""
    i = html.find('<div class="desk-only">')
    assert i >= 0, "no desktop list on the page"
    return html[i:]


# --- theme and shell ------------------------------------------------------------------------

def test_desktop_theme_is_per_user(app, signed_in, client):
    owner = signed_in("owner")
    assert 'data-desk="ledger"' in text(owner.get("/"))
    assert 'data-desk="ledger"' in text(client.get("/login"))       # before signing in
    resp = owner.post("/account/theme", data={"desk_theme": "graphite"}, follow_redirects=True)
    assert "Desktop look saved." in text(resp)
    assert 'data-desk="graphite"' in text(owner.get("/"))
    owner.post("/account/theme", data={"desk_theme": "neon"})
    assert db_query(app, "SELECT desk_theme FROM app_user WHERE id = :i",
                    i=owner.user["id"]) == [("graphite",)]
    tech = signed_in("tech")
    assert 'data-desk="ledger"' in text(tech.get("/"))              # someone else's is theirs
    page = text(owner.get("/account"))
    assert 'selected value="graphite"' in page or 'value="graphite" selected' in page


def test_the_sidebar_follows_roles(app, signed_in, client):
    tech, owner = signed_in("tech"), signed_in("owner")
    side = re.search(r'<aside class="sidebar">(.*?)</aside>', text(tech.get("/work")), re.S).group(1)
    assert ">Work<" in side and ">Work orders<" in side and ">Money<" not in side
    assert ">Settings<" not in side
    side = re.search(r'<aside class="sidebar">(.*?)</aside>', text(owner.get("/invoices")),
                     re.S).group(1)
    assert 'class="active">Money<' in side and ">Reports<" in side and ">Settings<" in side
    assert '<aside class="sidebar">' not in text(client.get("/login"))
    for path in ("/static/css/desk.css", "/static/js/desk.js",
                 "/static/fonts/IBMPlexSans-Regular.woff2"):
        assert client.get(path).status_code == 200, path


def test_a_record_renders_alone_for_the_pane(app, signed_in):
    owner = signed_in("owner")
    cid = customer(owner, "Pane Test Arcade")
    wo = new_wo(owner, cid)
    full = text(owner.get(f"/work/{wo}"))
    assert full.lstrip().startswith("<!doctype html>") and '<aside class="sidebar">' in full
    part = text(owner.get(f"/work/{wo}", headers={"X-Partial": "1"}))
    assert part.lstrip().startswith('<div class="pane-doc"') and "<html" not in part
    assert '<aside class="sidebar">' not in part and "Pane Test Arcade" in part


# --- the command bar --------------------------------------------------------------------------

def test_search_as_json_for_the_command_bar(app, signed_in):
    owner, tech = signed_in("owner"), signed_in("tech")
    b = billable(owner, app)
    issued_id(issue(owner, app, b["wo"]))
    number = db_query(app, "SELECT number FROM invoice")[0][0]
    body = owner.get("/search", query_string={"q": "Starlite", "format": "json"}).get_json()
    assert {i["kind"] for i in body["items"]} >= {"Customer"}
    body = owner.get("/search", query_string={"q": "boot", "format": "json"}).get_json()
    assert any(i["kind"] == "Work order" for i in body["items"])     # by its summary
    assert all(i["url"].startswith("/") for i in body["items"])
    body = owner.get("/search", query_string={"q": number[-4:], "format": "json"}).get_json()
    assert any(i["kind"] == "Invoice" and i["label"] == number for i in body["items"])
    body = tech.get("/search", query_string={"q": number[-4:], "format": "json"}).get_json()
    assert not any(i["kind"] == "Invoice" for i in body["items"])
    def commands(client):
        raw = client.get("/").get_data(as_text=True)
        return {c["label"] for c in json.loads(html.unescape(
            re.search(r'data-commands="([^"]*)"', raw).group(1)))}

    mine = commands(tech)
    assert "Work orders" in mine and "Invoices" not in mine and "Settings" not in mine
    assert {"Invoices", "Settings", "Add an expense"} <= commands(owner)


# --- power tables -----------------------------------------------------------------------------

@pytest.mark.parametrize("path", LISTS)
def test_every_list_has_a_desktop_table_and_csv(app, signed_in, path):
    owner = signed_in("owner")
    b = billable(owner, app)
    inv = issued_id(issue(owner, app, b["wo"]))
    owner.post(f"/invoices/{inv}/payment", data={"amount": "10", "method": "cash",
                                                  "received_on": str(
                                                      db_query(app, "SELECT current_date")[0][0]),
                                                  "reference": "", "note": ""})
    page = desk(text(owner.get(path)))
    assert 'class="grid power-table"' in page and "data-split" in page and 'id="pane"' in page
    assert "Views" in page and "Columns" in page
    resp = owner.get(path, query_string={"format": "csv"})
    assert resp.status_code == 200 and resp.mimetype == "text/csv"
    rows = list(csv.reader(io.StringIO(resp.get_data(as_text=True))))
    assert rows[0] and all(rows[0])                                   # a header row


def test_work_filters_sort_and_legacy_switches(app, signed_in):
    owner = signed_in("owner")
    cid = customer(owner, "Sort Test Arcade")
    first = new_wo(owner, cid, summary="Alpha board")
    second = new_wo(owner, cid, summary="Zulu monitor")
    owner.post(f"/work/{second}/status", data={"status": "completed"})
    numbers = dict(db_query(app, "SELECT id, number FROM work_order"))
    page = desk(text(owner.get("/work")))
    assert numbers[first] in page and numbers[second] not in page      # open by default
    page = desk(text(owner.get("/work", query_string={"status": "all"})))
    assert numbers[second] in page
    page = desk(text(owner.get("/work?all=1")))                         # the phone switch
    assert numbers[second] in page
    page = desk(text(owner.get("/work", query_string={"status": "all", "q": "zulu"})))
    assert numbers[second] in page and numbers[first] not in page
    asc = desk(text(owner.get("/work", query_string={"status": "all", "sort": "summary",
                                                      "dir": "asc"})))
    assert asc.index("Alpha board") < asc.index("Zulu monitor")
    desc = desk(text(owner.get("/work", query_string={"status": "all", "sort": "summary",
                                                       "dir": "desc"})))
    assert desc.index("Zulu monitor") < desc.index("Alpha board")
    # nonsense sort and filter values fall back rather than fail
    assert owner.get("/work", query_string={"sort": "drop table", "status": "nope"}) \
        .status_code == 200


def test_cost_columns_follow_roles(app, signed_in):
    owner, tech = signed_in("owner"), signed_in("tech")
    billable(owner, app)                                                  # a part at 0.40
    assert 'data-col="cost"' in desk(text(owner.get("/parts")))
    assert 'data-col="cost"' not in desk(text(tech.get("/parts")))
    header = tech.get("/parts?format=csv").get_data(as_text=True).splitlines()[0]
    assert "Cost" not in header
    assert 'data-col="total"' not in desk(text(tech.get("/purchasing/orders")))
    assert tech.get("/invoices?format=csv").status_code == 403


# --- saved views ------------------------------------------------------------------------------

def test_saved_views_are_personal(app, signed_in):
    owner, tech = signed_in("owner"), signed_in("tech")
    resp = owner.post("/views", data={"page": "work", "name": "Rush jobs",
                                      "query": "status=all&priority=rush&format=csv&pane=/work/1"})
    assert resp.status_code == 302 and resp.location == "/work?status=all&priority=rush"
    assert db_query(app, "SELECT page, name, query FROM saved_view") == \
        [("work", "Rush jobs", "status=all&priority=rush")]
    owner.post("/views", data={"page": "work", "name": "Rush jobs", "query": "priority=rush"})
    assert db_query(app, "SELECT query FROM saved_view") == [("priority=rush",)]
    page = desk(text(owner.get("/work")))
    assert "Rush jobs" in page and 'href="/work?priority=rush"' in page
    assert "Rush jobs" not in desk(text(tech.get("/work")))
    view_id = db_query(app, "SELECT id FROM saved_view")[0][0]
    assert tech.post(f"/views/{view_id}/delete").status_code == 404
    assert owner.post("/views", data={"page": "nope", "name": "x"}).status_code == 400
    resp = owner.post("/views", data={"page": "work", "name": " ", "query": ""},
                      follow_redirects=True)
    assert "Give the view a name." in text(resp)
    owner.post(f"/views/{view_id}/delete")
    assert db_query(app, "SELECT count(*) FROM saved_view") == [(0,)]


# --- bulk actions -----------------------------------------------------------------------------

def test_bulk_status_and_tech_on_work_orders(app, signed_in, make_user):
    owner, tech, viewer = signed_in("owner"), signed_in("tech"), signed_in("viewer")
    cid = customer(owner, "Bulk Test Arcade")
    ids = [new_wo(owner, cid) for _ in range(3)]
    resp = tech.post("/work/bulk", data={"ids": [str(i) for i in ids[:2]], "action": "status",
                                         "value_status": "in_progress",
                                         "query": "status=all"}, follow_redirects=False)
    assert resp.status_code == 302 and resp.location == "/work?status=all"
    assert db_query(app, "SELECT id, status FROM work_order ORDER BY id") == [
        (ids[0], "in_progress"), (ids[1], "in_progress"), (ids[2], "new")]
    helper = make_user("helper", "tech")
    owner.post("/work/bulk", data={"ids": [str(ids[2])], "action": "tech",
                                   "value_tech": str(helper["id"])})
    assert db_query(app, "SELECT work_order_id FROM work_order_tech WHERE user_id = :u",
                    u=helper["id"]) == [(ids[2],)]
    assert viewer.post("/work/bulk", data={"ids": [str(ids[0])], "action": "status",
                                           "value_status": "completed"}).status_code == 403
    assert owner.post("/work/bulk", data={"ids": [str(ids[0])], "action": "status",
                                          "value_status": "exploded"}).status_code == 400
    assert owner.post("/work/bulk", data={"ids": [str(ids[0])], "action": "tech",
                                          "value_tech": str(viewer.user["id"])}).status_code == 400
    resp = owner.post("/work/bulk", data={"action": "status", "value_status": "completed"},
                      follow_redirects=True)
    assert "Tick at least one work order." in text(resp)
    # the ticks only show for those who may use them
    assert 'name="ids"' in desk(text(tech.get("/work")))
    assert 'name="ids"' not in desk(text(viewer.get("/work")))


def test_phone_markup_is_untouched(app, signed_in):
    """Phones keep their own lists: the desktop table is an addition beside them."""
    owner = signed_in("owner")
    cid = customer(owner, "Phone Test Arcade")
    asset(owner, cid, "Galaga")
    page = text(owner.get("/assets"))
    phone = page[page.find('<div class="phone-only">'):page.find('<div class="desk-only">')]
    assert "Galaga" in phone and "power-table" not in phone
