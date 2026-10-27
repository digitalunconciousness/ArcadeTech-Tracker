"""Work in motion (Phase 9b): the work board (cards moved between statuses, a stale move
refused), the dispatch calendar (book, move, swap a tech, resize, double-booking flagged or
refused, the edit form), Settings → Audit (owner only, what changed, a record's history),
and as found against as left (the chart, the numbers, the service report's copy)."""

import copy
import hashlib
import re
import secrets
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
from test_api_v1_contract import PUBLIC_ID, bench, post, request_for  # noqa: F401
from test_assets import asset, customer
from test_billing import billable
from test_work import first_job, new_wo, text

from conftest import TEST_TZ, db_query, libpq

DAY = "2030-03-14"                       # a Thursday well ahead: bookings stay "upcoming"
JSON = {"Accept": "application/json"}


def column(page, status):
    """One board column's markup."""
    start = page.index(f'data-column="{status}"')
    end = page.find('data-column="', start + 10)
    return page[start:end if end > 0 else None]


def numbers(app):
    return dict(db_query(app, "SELECT id, number FROM work_order"))


# --- the work board ---------------------------------------------------------------------------

def test_the_board_moves_cards_and_refuses_a_stale_move(app, signed_in):
    owner, tech, viewer = signed_in("owner"), signed_in("tech"), signed_in("viewer")
    cid = customer(owner, "Board Test Arcade")
    first, second = new_wo(owner, cid, summary="Alpha board"), new_wo(owner, cid)
    number = numbers(app)[first]
    page = text(tech.get("/work/board"))
    assert number in column(page, "new") and 'draggable="true"' in page
    # a drag: JSON in, JSON out
    resp = tech.post(f"/work/{first}/status", headers=JSON,
                     data={"status": "in_progress", "from": "new"})
    assert resp.status_code == 200 and resp.get_json()["status"] == "in_progress"
    assert number in column(text(tech.get("/work/board")), "in_progress")
    # someone else moved it meanwhile: nothing saved, the card learns where it is
    resp = tech.post(f"/work/{first}/status", headers=JSON, data={"status": "ready", "from": "new"})
    assert resp.status_code == 409 and resp.get_json()["status"] == "in_progress"
    assert db_query(app, "SELECT status FROM work_order WHERE id = :i", i=first) == \
        [("in_progress",)]
    # the Move menu without scripts comes back to the board
    resp = tech.post(f"/work/{second}/status", data={"status": "ready", "from": "new",
                                                     "back": "board"})
    assert resp.status_code == 302 and resp.location == "/work/board"
    # finding a card
    page = text(tech.get("/work/board", query_string={"q": "alpha"}))
    cards = page[page.index("data-board"):]
    assert number in cards and numbers(app)[second] not in cards
    # viewers see the board but move nothing
    page = text(viewer.get("/work/board"))
    assert number in page and 'name="status"' not in page and 'draggable="true"' not in page
    assert viewer.post(f"/work/{first}/status", headers=JSON,
                       data={"status": "new"}).status_code == 403


def test_board_cards_say_what_matters(app, signed_in, tdb):
    owner = signed_in("owner")
    b = billable(owner, app)                                   # lines not billed yet
    page = text(owner.get("/work/board"))
    card = page[page.index(f'data-id="{b["wo"]}"'):]
    assert card[:card.index(">")].count("data-unbilled") == 1
    # completed lately: on the board; completed long ago: off it
    owner.post(f"/work/{b['wo']}/status", data={"status": "completed"})
    assert numbers(app)[b["wo"]] in column(text(owner.get("/work/board")), "completed")
    with psycopg.connect(libpq(tdb.url("app"))) as c:
        c.execute("UPDATE work_order SET completed_at = now() - interval '20 days'")
    assert numbers(app)[b["wo"]] not in text(owner.get("/work/board"))


def test_board_and_dispatch_tabs_are_for_the_desktop(app, signed_in):
    owner = signed_in("owner")
    page = text(owner.get("/work"))
    side = re.search(r'<aside class="sidebar">(.*?)</aside>', page, re.S).group(1)
    assert ">Board<" in side and ">Dispatch<" in side
    sub = re.search(r'<nav class="subnav">(.*?)</nav>', page, re.S).group(1)
    assert ">Board<" not in sub and ">Dispatch<" not in sub and ">Work orders<" in sub


# --- the dispatch calendar --------------------------------------------------------------------

def local(app, appt_id):
    return db_query(app, "SELECT to_char(starts_at AT TIME ZONE :tz, 'YYYY-MM-DD HH24:MI'), "
                         "to_char(ends_at AT TIME ZONE :tz, 'HH24:MI') FROM appointment "
                         "WHERE id = :i", tz=TEST_TZ, i=appt_id)[0]


def techs_on(app, appt_id):
    return {r[0] for r in db_query(app, "SELECT user_id FROM appointment_user "
                                        "WHERE appointment_id = :i", i=appt_id)}


def test_booking_moving_and_resizing_on_the_calendar(app, signed_in, make_user):
    owner, tech = signed_in("owner"), signed_in("tech")
    helper = make_user("helper", "tech")
    cid = customer(owner, "Dispatch Test Arcade")
    wo = new_wo(owner, cid)
    number = numbers(app)[wo]
    assert number in text(tech.get("/work/dispatch", query_string={"day": DAY}))   # to schedule
    resp = tech.post("/work/dispatch/book", data={
        "wo_id": wo, "starts_at": f"{DAY}T09:00", "minutes": "60", "user": tech.user["id"]})
    assert resp.status_code == 200 and resp.get_json()["warnings"] == []
    appt = db_query(app, "SELECT id FROM appointment")[0][0]
    assert local(app, appt) == (f"{DAY} 09:00", "10:00")
    assert techs_on(app, appt) == {tech.user["id"]}
    assert db_query(app, "SELECT status FROM work_order") == [("scheduled",)]
    page = text(tech.get("/work/dispatch", query_string={"day": DAY}))
    assert f'data-id="{appt}" data-start="540" data-end="600"' in page
    queue = page[page.index("To schedule"):page.index('class="cal panel"')]
    assert number not in queue                                  # booked: off the list
    # dragged to the helper's lane at 13:00, then made two hours long
    resp = tech.post(f"/work/appointments/{appt}/move", data={
        "starts_at": f"{DAY}T13:00", "minutes": "90", "from_user": tech.user["id"],
        "to_user": helper["id"]})
    assert resp.status_code == 200, resp.get_json()
    assert local(app, appt) == (f"{DAY} 13:00", "14:30") and techs_on(app, appt) == {helper["id"]}
    tech.post(f"/work/appointments/{appt}/move", data={
        "starts_at": f"{DAY}T13:00", "minutes": "120", "from_user": "*", "to_user": "*"})
    assert local(app, appt) == (f"{DAY} 13:00", "15:00") and techs_on(app, appt) == {helper["id"]}
    # onto Unassigned: the helper comes off it
    tech.post(f"/work/appointments/{appt}/move", data={
        "starts_at": f"{DAY}T13:00", "minutes": "120", "from_user": helper["id"], "to_user": ""})
    assert techs_on(app, appt) == set()
    # the week shows it on its day
    week = text(tech.get("/work/dispatch", query_string={"day": DAY, "view": "week"}))
    assert "Thu Mar 14" in week and number in week
    # nonsense is refused, not crashed on
    assert tech.post("/work/dispatch/book", data={"wo_id": wo, "starts_at": "soon"}) \
        .status_code == 400
    assert tech.post("/work/dispatch/book", data={
        "wo_id": wo, "starts_at": f"{DAY}T09:00", "user": "99999"}).status_code == 400


def test_double_booking_is_flagged_or_refused(app, signed_in):
    owner, tech = signed_in("owner"), signed_in("tech")
    cid = customer(owner, "Overlap Test Arcade")
    first, second, third = (new_wo(owner, cid) for _ in range(3))
    tech.post("/work/dispatch/book", data={"wo_id": first, "starts_at": f"{DAY}T09:00",
                                           "minutes": "60", "user": tech.user["id"]})
    resp = tech.post("/work/dispatch/book", data={"wo_id": second, "starts_at": f"{DAY}T09:30",
                                                  "minutes": "60", "user": tech.user["id"]})
    warnings = resp.get_json()["warnings"]
    assert resp.status_code == 200 and len(warnings) == 1 and "09:00–10:00" in warnings[0]
    page = text(tech.get("/work/dispatch", query_string={"day": DAY}))
    assert "Double-booked" in page and page.count("appt conflict") == 2
    # the owner switches double-booking off: the third is refused, from either page
    owner.post("/settings/", data={"business_name": "ArcadeTech Tracker",
                                   "doc_accent_color": "#b0126f", "estimate_link_days": "30",
                                   "dispatch_block_overlap": "y"})
    assert db_query(app, "SELECT dispatch_block_overlap FROM shop_setting") == [(True,)]
    resp = tech.post("/work/dispatch/book", data={"wo_id": third, "starts_at": f"{DAY}T09:15",
                                                  "minutes": "30", "user": tech.user["id"]})
    assert resp.status_code == 409 and "switched off in Settings" in resp.get_json()["error"]
    resp = tech.post(f"/work/{third}/appointments", data={
        "starts_at": f"{DAY}T09:15", "minutes": "30", "users": [str(tech.user["id"])],
        "site_id": "None", "note": ""}, follow_redirects=True)
    assert "switched off in Settings" in text(resp)
    assert db_query(app, "SELECT count(*) FROM appointment") == [(2,)]
    # anyone else, or a free slot, is still fine
    resp = tech.post("/work/dispatch/book", data={"wo_id": third, "starts_at": f"{DAY}T11:00",
                                                  "minutes": "30", "user": tech.user["id"]})
    assert resp.status_code == 200


def test_the_appointment_form_and_who_may_book(app, signed_in):
    owner, viewer = signed_in("owner"), signed_in("viewer")
    cid = customer(owner, "Edit Test Arcade")
    wo = new_wo(owner, cid)
    owner.post("/work/dispatch/book", data={"wo_id": wo, "starts_at": f"{DAY}T08:00",
                                            "user": owner.user["id"]})
    appt = db_query(app, "SELECT id FROM appointment")[0][0]
    back = f"/work/dispatch?day={DAY}"
    assert "Remove the appointment" in text(owner.get(f"/work/appointments/{appt}/edit"))
    resp = owner.post(f"/work/appointments/{appt}/edit", data={
        "starts_at": f"{DAY}T10:30", "minutes": "45", "users": [str(owner.user["id"])],
        "site_id": "None", "note": "Bring the cap kit", "next": back})
    assert resp.status_code == 302 and resp.location == back
    assert local(app, appt) == (f"{DAY} 10:30", "11:15")
    assert db_query(app, "SELECT note FROM appointment") == [("Bring the cap kit",)]
    # an outside "next" is ignored
    resp = owner.post(f"/work/appointments/{appt}/edit", data={
        "starts_at": f"{DAY}T10:30", "minutes": "45", "users": [str(owner.user["id"])],
        "site_id": "None", "note": "", "next": "https://example.com/"})
    assert resp.location == f"/work/{wo}#appointments"
    # viewers see the calendar, book nothing
    page = text(viewer.get("/work/dispatch", query_string={"day": DAY}))
    assert "Booked" in page and "data-can-book" not in page and "/edit" not in page
    assert viewer.post("/work/dispatch/book", data={"wo_id": wo, "starts_at": f"{DAY}T09:00"}) \
        .status_code == 403
    assert viewer.post(f"/work/appointments/{appt}/move", data={
        "starts_at": f"{DAY}T09:00"}).status_code == 403
    assert viewer.get(f"/work/appointments/{appt}/edit").status_code == 403
    # removing it from the calendar comes back to the calendar
    resp = owner.post(f"/work/appointments/{appt}/delete", data={"next": back})
    assert resp.location == back and db_query(app, "SELECT count(*) FROM appointment") == [(0,)]


def test_the_calendar_keeps_wall_clock_time_across_daylight_saving(app):
    """Central European clocks fall back on 2026-10-25: that day is 25 hours, and 09:00 is
    still 09:00."""
    from app.work.planning import day_bounds, wall_minutes
    day = date(2026, 10, 25)
    with app.app_context():
        saved = app.config["SHOP_TZ"]
        app.config["SHOP_TZ"] = "Europe/Berlin"
        try:
            start, end = day_bounds(day)
            assert (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() == 25 * 3600
            assert wall_minutes(datetime(2026, 10, 25, 8, 0, tzinfo=UTC), day) == 9 * 60
            assert wall_minutes(datetime(2026, 10, 24, 23, 30, tzinfo=UTC), day) == 90
        finally:
            app.config["SHOP_TZ"] = saved


# --- Settings → Audit -------------------------------------------------------------------------

def test_the_audit_log_is_for_owners(app, signed_in):
    tech, viewer = signed_in("tech"), signed_in("viewer")
    for who in (tech, viewer):
        assert who.get("/settings/audit").status_code == 403
    cid = customer(tech, "History Test Arcade")
    wo = new_wo(tech, cid)
    assert "History" not in re.search(r'<div class="head">(.*?)</div>\s*<p',
                                      text(tech.get(f"/work/{wo}")), re.S).group(1)


def test_what_changed_and_a_records_history(app, signed_in):
    owner, tech = signed_in("owner"), signed_in("tech")
    cid = customer(owner, "History Test Arcade")
    wo = new_wo(owner, cid)
    tech.post(f"/work/{wo}/status", data={"status": "in_progress"})
    page = text(owner.get(f"/work/{wo}"))
    assert f"/settings/audit?table=work_order&row={wo}&related=1" in page
    page = text(owner.get("/settings/audit", query_string={"table": "work_order", "row": wo}))
    assert "History of" in page and "status" in page and "new → in_progress" not in page
    assert re.search(r"status</span>: <del>new</del> → <ins>in_progress</ins>", page)
    assert tech.user["username"].title() in page                  # who did it
    # with the rows under it: its job too
    job = first_job(app, wo)
    page = text(owner.get("/settings/audit", query_string={
        "table": "work_order", "row": wo, "related": "1"}))
    assert f"Job {job}" in page and f"Work order {wo}" in page
    # filters and CSV
    page = text(owner.get("/settings/audit", query_string={"table": "work_order",
                                                            "action": "I"}))
    assert "added" in page and "<del>new</del>" not in page
    resp = owner.get("/settings/audit", query_string={"table": "work_order", "row": wo,
                                                      "format": "csv"})
    assert resp.mimetype == "text/csv"
    body = resp.get_data(as_text=True)
    assert body.splitlines()[0] == "When,Who,What,Row,Action,Changes"
    assert "status: new → in_progress" in body


def test_audit_values_are_decimal_never_float():
    from app.settings.audit import changes, load, show
    row = load('{"unit_price": 12.50, "qty": 2, "ok": true, "at": "2026-10-13T08:00:00+00:00"}')
    assert isinstance(row["unit_price"], Decimal) and str(row["unit_price"]) == "12.50"
    assert show(True) == "yes" and show(None) == "—"
    assert changes("U", {"a": Decimal("1.10"), "updated_at": "x"},
                   {"a": Decimal("1.20"), "updated_at": "y"}) == [("a", "1.10", "1.20")]


# --- as found against as left -----------------------------------------------------------------

def as_left(item):
    """A second trace of the same rail, after the work: its own file (so its own uid, by the
    contract's rule), readings in the window."""
    item = copy.deepcopy(item)
    item["file"] = "rail_as_left.csv"
    item["uid"] = hashlib.sha256(f"{PUBLIC_ID}|rail_session|{item['file']}".encode()) \
        .hexdigest()[:32]
    item["phase"] = "as_left"
    for r in item["readings"]:
        r["uid"] = secrets.token_hex(16)
        r["v"], r["raw"], r["unit"] = 5.02, "5.02", "V"
    return item


def test_as_found_against_as_left(app, client, bench, monkeypatch):     # noqa: F811
    from app.work import docs
    owner = bench["owner"]
    wo = new_wo(owner, bench["cid"], bench["widget"])
    job = first_job(app, wo)
    payload = request_for(bench["tag"])
    found = payload["items"][0]
    found["phase"] = "as_found"
    payload["items"] = [found]
    post(client, bench, payload)                                   # lands on the job by itself
    assert '"kind": "overlay"' not in text(owner.get(f"/work/jobs/{job}"))
    payload["items"] = [as_left(found)]
    assert post(client, bench, payload).status_code == 200
    assert db_query(app, "SELECT phase FROM wo_job_session ORDER BY id") == [
        ("as_found",), ("as_left",)]
    page = text(owner.get(f"/work/jobs/{job}"))
    assert '"kind": "overlay"' in page and '"name": "As left"' in page
    assert "As found against as left" in page and "chart.umd.min.js" in page
    session = db_query(app, "SELECT rail_session_id FROM wo_job_session ORDER BY id")[0][0]
    assert "Before and after" in text(owner.get(f"/traces/{session}"))
    # the service report draws it for print
    seen = {}
    monkeypatch.setattr(docs, "render_pdf", lambda template, **ctx: seen.setdefault(
        template, docs.render_template(template, doc_css="css/doc.css", wo_kinds=docs.WO_KINDS,
                                       received_via=docs.RECEIVED_VIA, **ctx)).encode())
    owner.get(f"/work/{wo}/service-report.pdf")
    report = seen["docs/service_report.html"]
    assert report.count("<polyline") == 2 and "As found against as left" in report
    assert "style=" not in report[report.index("<svg"):report.index("</svg>")]


def test_readings_before_and_after_chart(app, signed_in):
    tech = signed_in("tech")
    cid = customer(tech, "Readings Test Arcade")
    wo = new_wo(tech, cid, asset(tech, cid, "Galaga"))
    job = first_job(app, wo)
    for phase, value in (("as_found", "4.61"), ("as_left", "5.02")):
        tech.post(f"/work/jobs/{job}/readings", data={
            "test_point": "+5 V at edge", "phase": phase, "value": value, "unit": "V",
            "spec_lo": "4.75", "spec_hi": "5.25"})
    tech.post(f"/work/jobs/{job}/readings", data={
        "test_point": "-5 V", "phase": "as_found", "value": "-4.9", "unit": "V"})  # no pair
    page = text(tech.get(f"/work/jobs/{job}"))
    chart = re.search(r'data-chart="(\{&#34;kind&#34;: &#34;readings&#34;.*?)"',
                      tech.get(f"/work/jobs/{job}").get_data(as_text=True))
    assert chart and "As found → as left (V)" in page
    data = text(tech.get(f"/work/jobs/{job}"))
    assert '"labels": ["+5 V at edge"]' in data and '"found": ["4.610000"]' in data
