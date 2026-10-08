"""Appointments and the private .ics feeds."""

import hashlib
import re
from datetime import UTC, datetime, timedelta

from test_work import setup, text

from app.calendar_ics import calendar, escape, fold
from conftest import db_query


def test_escape_and_fold():
    assert escape("Bar, Grill; back\\room\nline 2") == "Bar\\, Grill\\; back\\\\room\\nline 2"
    line = "SUMMARY:" + "Ω" * 60                      # 2-byte characters across the fold
    folded = fold(line)
    parts = folded.split("\r\n ")
    assert "".join(parts) == line
    assert all(len(p.encode()) <= 75 for p in parts[:1])
    assert all(len(p.encode()) <= 74 for p in parts[1:])
    assert fold("SHORT:x") == "SHORT:x"


def test_calendar_document():
    start = datetime(2026, 10, 9, 14, 0, tzinfo=UTC)
    ics = calendar("Shop, Test", [{"uid": "appointment-1@shop-hub", "start": start,
                                   "end": start + timedelta(hours=1),
                                   "summary": "WO-2026-0001 · Starlite Lanes Test",
                                   "location": "100 Main St, Testville OK",
                                   "description": "No picture\nbring flyback",
                                   "url": "https://shop.example.test/work/1"}], start)
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert "\n" not in ics.replace("\r\n", "")
    assert "DTSTART:20261009T140000Z" in ics and "DTEND:20261009T150000Z" in ics
    assert "X-WR-CALNAME:Shop\\, Test" in ics and "LOCATION:100 Main St\\, Testville OK" in ics
    assert "DESCRIPTION:No picture\\nbring flyback" in ics


def test_appointments_and_the_feed(app, signed_in):
    owner, tech = signed_in("owner"), signed_in("tech")
    cid, aid, wo_id, job_id = setup(owner, app)
    owner.post(f"/customers/{cid}/sites/new", data={"name": "Main St", "address_line1": "100 Main St",
                                                   "city": "Testville", "region": "OK",
                                                   "active": "y"})
    site = db_query(app, "SELECT id FROM site")[0][0]
    resp = owner.post(f"/work/{wo_id}/appointments", data={
        "starts_at": "2026-10-09T09:30", "minutes": "90", "site_id": str(site),
        "users": [str(tech.user["id"])], "note": "Bring the flyback"})
    assert resp.status_code == 302
    assert db_query(app, "SELECT status FROM work_order") == [("scheduled",)]
    assert "Bring the flyback" in text(owner.get(f"/work/{wo_id}"))
    # the tech's feed link: shown once, stored as a hash
    page = text(tech.post("/account/calendar"))
    url = re.search(r"https?://\S+/d/cal/([A-Za-z0-9_-]{43})\.ics", page)
    assert url, page[-800:]
    token = url.group(1)
    assert db_query(app, "SELECT token_sha256 FROM calendar_feed") == \
        [(hashlib.sha256(token.encode()).hexdigest(),)]
    assert token not in text(tech.get("/account"))
    anon = app.test_client()
    resp = anon.get(f"/d/cal/{token}.ics")
    ics = resp.get_data(as_text=True)
    assert resp.status_code == 200 and resp.mimetype == "text/calendar"
    assert "SUMMARY:WO-" in ics and "Starlite Lanes Test" in ics
    assert "100 Main St\\, Testville OK" in ics and "Bring the flyback" in ics
    # Pacific/Kiritimati is UTC+14: 09:30 there is 19:30 the day before in UTC
    assert "DTSTART:20261008T193000Z" in ics and "DTEND:20261008T210000Z" in ics
    # not on the owner's feed: they aren't on it
    owner_page = text(owner.post("/account/calendar"))
    owner_token = re.search(r"/d/cal/([A-Za-z0-9_-]{43})\.ics", owner_page).group(1)
    assert "VEVENT" not in anon.get(f"/d/cal/{owner_token}.ics").get_data(as_text=True)
    # a new link retires the old one
    tech.post("/account/calendar")
    assert anon.get(f"/d/cal/{token}.ics").status_code == 404
    assert anon.get("/d/cal/short.ics").status_code == 404
    # removing the appointment
    appt = db_query(app, "SELECT id FROM appointment")[0][0]
    owner.post(f"/work/appointments/{appt}/delete")
    assert db_query(app, "SELECT count(*) FROM appointment") == [(0,)]


def test_appointment_validation(app, signed_in):
    owner = signed_in("owner")
    cid, aid, wo_id, job_id = setup(owner, app)
    for bad in ({"minutes": "0"}, {"starts_at": ""}, {"minutes": "2000"}):
        data = {"starts_at": "2026-10-09T09:30", "minutes": "60", "site_id": "None", **bad}
        resp = owner.post(f"/work/{wo_id}/appointments", data=data, follow_redirects=True)
        assert resp.status_code == 200, bad
    assert db_query(app, "SELECT count(*) FROM appointment") == [(0,)]
