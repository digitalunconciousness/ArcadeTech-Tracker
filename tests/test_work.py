"""Work orders and jobs: numbers, roles, the board, NTE, 3C and intake, the timer and
labor billed from it."""

import html
from datetime import timedelta
from decimal import Decimal as D

import psycopg
import pytest
from sqlalchemy import select
from test_assets import asset, customer

from app.extensions import db
from app.models import TimeEntry, WoJob, WoLine, WorkOrder
from app.timeutil import local_today, utcnow
from app.work import service
from conftest import db_query, libpq


def text(resp):
    return html.unescape(resp.get_data(as_text=True))


def new_wo(client, cid, aid=None, **extra):
    data = {"kind": "bench", "summary": "Won't boot", "priority": "normal",
            "complaint": "Dead screen, no sound", "asset_id": str(aid) if aid else "None",
            "site_id": "None", **extra}
    resp = client.post(f"/work/new?customer={cid}", data=data)
    assert resp.status_code == 302, text(resp)[-1500:]
    return int(resp.location.rsplit("/", 1)[1])


def first_job(app, wo_id):
    return db_query(app, "SELECT id FROM wo_job WHERE work_order_id = :w ORDER BY id", w=wo_id)[0][0]


def setup(client, app):
    cid = customer(client, "Starlite Lanes Test")
    aid = asset(client, cid, "Galaga", serial="GAL-0042")
    wo_id = new_wo(client, cid, aid)
    return cid, aid, wo_id, first_job(app, wo_id)


def service_id(app, code):
    return db_query(app, "SELECT id FROM service WHERE code = :c", c=code)[0][0]


def test_numbers_are_gapless_by_year(app, signed_in):
    tech = signed_in("tech")
    cid = customer(tech, "Starlite Lanes Test")
    ids = [new_wo(tech, cid) for _ in range(3)]
    year = None
    with app.app_context():
        year = local_today().year
    numbers = [r[0] for r in db_query(app, "SELECT number FROM work_order ORDER BY id")]
    assert numbers == [f"WO-{year}-{n:04d}" for n in (1, 2, 3)]
    # a failed creation (bad asset) gives its number back
    other = customer(tech, "Other Test")
    foreign = asset(tech, other, "Robotron")
    resp = tech.post(f"/work/new?customer={cid}", data={
        "kind": "bench", "summary": "x", "priority": "normal", "complaint": "x",
        "asset_id": str(foreign), "site_id": "None"})
    assert resp.status_code == 200
    new_wo(tech, cid)
    assert db_query(app, "SELECT max(number) FROM work_order")[0][0] == f"WO-{year}-0004"
    assert len(ids) == 3


def test_new_work_order_page_and_defaults(app, signed_in):
    tech = signed_in("tech")
    cid, aid, wo_id, job_id = setup(tech, app)
    with app.app_context():
        wo = db.session.get(WorkOrder, wo_id)
        job = db.session.get(WoJob, job_id)
        assert (wo.status, wo.kind, job.asset_id, job.status) == ("new", "bench", aid, "open")
    # the form starts with the creator ticked; what's posted is what's assigned
    form = text(tech.get(f"/work/new?customer={cid}"))
    assert f'checked id="techs-0" name="techs" type="checkbox" value="{tech.user["id"]}"' in form
    assert db_query(app, "SELECT user_id FROM work_order_tech WHERE work_order_id = :w",
                    w=wo_id) == []
    wo2 = new_wo(tech, cid, aid, techs=[str(tech.user["id"])])
    assert db_query(app, "SELECT user_id FROM work_order_tech WHERE work_order_id = :w",
                    w=wo2) == [(tech.user["id"],)]
    page = text(tech.get(f"/work/{wo_id}"))
    assert "Galaga" in page and "Claim ticket" in page and "Dead screen" in page
    assert "Galaga" in text(tech.get(f"/customers/{cid}"))     # listed on the customer
    assert f"/work/jobs/{job_id}" in text(tech.get(f"/assets/{aid}"))  # and the asset


def test_board_groups_by_status_and_mine(app, signed_in):
    owner, tech = signed_in("owner"), signed_in("tech")
    cid = customer(owner, "Starlite Lanes Test")
    mine = new_wo(tech, cid, summary="Tech's job", techs=[str(tech.user["id"])])
    theirs = new_wo(owner, cid, summary="Owner's job", techs=[str(owner.user["id"])])
    owner.post(f"/work/{theirs}/status", data={"status": "waiting_parts"})
    board = text(tech.get("/work"))
    assert "Waiting on parts" in board and "Tech's job" in board and "Owner's job" in board
    only_mine = text(tech.get("/work?mine=1"))
    assert "Tech's job" in only_mine and "Owner's job" not in only_mine
    owner.post(f"/work/{mine}/status", data={"status": "completed"})
    assert "Tech's job" not in text(tech.get("/work"))
    assert "Tech's job" in text(tech.get("/work?all=1"))
    with app.app_context():
        assert db.session.get(WorkOrder, mine).completed_at is not None


def test_roles(app, signed_in):
    owner, viewer = signed_in("owner"), signed_in("viewer")
    cid, aid, wo_id, job_id = setup(owner, app)
    assert viewer.get(f"/work/{wo_id}").status_code == 200
    assert viewer.get(f"/work/jobs/{job_id}").status_code == 200
    assert "Start timer" not in text(viewer.get(f"/work/jobs/{job_id}"))
    for path in (f"/work/new?customer={cid}", f"/work/{wo_id}/edit", f"/work/{wo_id}/status",
                 f"/work/{wo_id}/jobs", f"/work/jobs/{job_id}/edit", f"/work/jobs/{job_id}/timer",
                 f"/work/jobs/{job_id}/time", f"/work/jobs/{job_id}/lines/part",
                 f"/work/jobs/{job_id}/readings", f"/work/jobs/{job_id}/photos"):
        assert viewer.post(path, data={}).status_code == 403, path


def test_job_3c_and_intake(app, signed_in):
    tech = signed_in("tech")
    cid, aid, wo_id, job_id = setup(tech, app)
    resp = tech.post(f"/work/jobs/{job_id}/edit", data={
        "status": "done", "complaint": "Dead screen", "cause": "Open +5 V trace at the edge",
        "correction": "Jumpered the trace; reflowed the connector",
        "received_at": "2026-10-08T14:30", "received_via": "drop_off",
        "condition": "Scratched cabinet side", "accessories": ["Harness", "Manual / schematics"],
        "accessories_note": "Spare fuse", "inbound_tracking": ""})
    assert resp.status_code == 302
    with app.app_context():
        job = db.session.get(WoJob, job_id)
        assert job.cause.startswith("Open +5 V") and job.status == "done" and job.done_at
        assert job.accessories == ["Harness", "Manual / schematics"]
        assert job.received_via == "drop_off" and job.inbound_tracking is None
        # typed in shop time (UTC+14 in tests), stored in UTC
        assert job.received_at.utcoffset() == timedelta(0) and job.received_at.hour == 0
    page = text(tech.get(f"/work/jobs/{job_id}"))
    assert 'value="2026-10-08T14:30"' in page


def test_add_job_only_for_this_customers_assets(app, signed_in):
    tech = signed_in("tech")
    cid, aid, wo_id, job_id = setup(tech, app)
    second = asset(tech, cid, "Dig Dug")
    tech.post(f"/work/{wo_id}/jobs", data={"asset_id": str(second), "complaint": "No coin up"})
    other = asset(tech, customer(tech, "Other Test"), "Joust")
    resp = tech.post(f"/work/{wo_id}/jobs", data={"asset_id": str(other), "complaint": "x"},
                     follow_redirects=True)
    assert "Not a valid choice" in text(resp)                # not offered, so refused
    with app.app_context():                                  # and the service refuses it too
        wo = db.session.get(WorkOrder, wo_id)
        with pytest.raises(service.WorkError, match="different customer"):
            service.add_job(wo, other, "x")
        db.session.rollback()
    assert [r[0] for r in db_query(app, "SELECT asset_id FROM wo_job WHERE work_order_id = :w "
                                        "ORDER BY id", w=wo_id)] == [aid, second]


def test_nte_banner_and_hold(app, signed_in):
    owner = signed_in("owner")
    owner.post(f"/pricebook/services/{service_id(app, 'FEE-DIAG')}", data={
        "code": "FEE-DIAG", "name": "Diagnostic fee", "kind": "diagnostic", "unit": "each",
        "rate": "75", "warranty_days": "0", "active": "y"})
    cid, aid, wo_id, job_id = setup(owner, app)
    owner.post(f"/work/{wo_id}/edit", data={"kind": "bench", "summary": "Won't boot",
                                            "priority": "normal", "site_id": "None",
                                            "not_to_exceed": "100", "techs": []})
    owner.post(f"/work/jobs/{job_id}/lines/service",
               data={"service_id": service_id(app, "FEE-DIAG"), "qty": "1", "labor_mode": "actual",
                     "tech_user_id": "None", "billable": "y"})
    assert "Over the not-to-exceed" not in text(owner.get(f"/work/{wo_id}"))
    owner.post(f"/work/jobs/{job_id}/lines/other", data={
        "kind": "sublet", "description": "Monitor rebuild (sent out)", "qty": "1",
        "unit_price": "60", "unit_cost": "40", "billable": "y"})
    page = text(owner.get(f"/work/{wo_id}"))
    assert "Over the not-to-exceed by $35.00" in page and "Hold for approval" in page
    owner.post(f"/work/{wo_id}/hold")
    assert db_query(app, "SELECT status FROM work_order WHERE id = :i", i=wo_id) == \
        [("waiting_approval",)]
    assert "Hold for approval" not in text(owner.get(f"/work/{wo_id}"))


def test_timer_runs_one_at_a_time(app, signed_in):
    tech = signed_in("tech")
    cid, aid, wo_a, job_a = setup(tech, app)
    wo_b = new_wo(tech, cid, aid)
    job_b = first_job(app, wo_b)
    tech.post(f"/work/jobs/{job_a}/timer", data={"action": "start"})
    page = text(tech.get(f"/work/jobs/{job_a}"))
    assert "Stop timer" in page and "timerbar" in page
    resp = tech.post(f"/work/jobs/{job_b}/timer", data={"action": "start"}, follow_redirects=True)
    assert "Stopped your timer on" in text(resp)
    rows = db_query(app, "SELECT wo_job_id, ended_at IS NULL FROM time_entry ORDER BY id")
    assert rows == [(job_a, False), (job_b, True)]
    tech.post(f"/work/jobs/{job_b}/timer", data={"action": "stop"})
    assert db_query(app, "SELECT count(*) FROM time_entry WHERE ended_at IS NULL") == [(0,)]


def test_one_running_timer_per_user_in_the_database(app, tdb, make_user):
    user = make_user("tess", "tech")
    with app.app_context():
        wo = service.create_work_order(
            db.session.scalar(select(__import__("app.models", fromlist=["Customer"]).Customer)),
            kind="bench", summary="x")
        job = service.add_job(wo, None, "x")
        db.session.commit()
        job_id = job.id
    with psycopg.connect(libpq(tdb.url("app"))) as c:
        c.execute("INSERT INTO time_entry (wo_job_id, user_id, started_at, source) "
                  "VALUES (%s, %s, now(), 'timer')", (job_id, user["id"]))
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute("INSERT INTO time_entry (wo_job_id, user_id, started_at, source) "
                      "VALUES (%s, %s, now(), 'timer')", (job_id, user["id"]))


def test_stopping_rounds_to_whole_minutes(app, make_user):
    user = make_user("tess", "tech")
    with app.app_context():
        from app.models import Customer

        wo = service.create_work_order(db.session.scalar(select(Customer)), kind="bench",
                                       summary="x")
        job = service.add_job(wo, None, "x")
        start = utcnow() - timedelta(hours=1)
        entry, stopped = service.start_timer(job, user["id"], now=start)
        assert stopped is None
        service.stop_timer(entry, now=start + timedelta(minutes=36, seconds=29))
        assert entry.minutes == 36
        entry2, _ = service.start_timer(job, user["id"], now=start)
        service.stop_timer(entry2, now=start + timedelta(minutes=36, seconds=30))
        assert entry2.minutes == 37
        db.session.rollback()


@pytest.mark.parametrize("minutes, hours", [(6, "0.50"), (36, "0.75"), (61, "1.25"),
                                            (60, "1.00")])
def test_actual_labor_follows_the_timer(app, signed_in, minutes, hours):
    owner = signed_in("owner")
    cid, aid, wo_id, job_id = setup(owner, app)
    bench = service_id(app, "LAB-BENCH")
    owner.post(f"/pricebook/services/{bench}", data={
        "code": "LAB-BENCH", "name": "Bench labor", "kind": "labor", "unit": "hour",
        "rate": "80", "warranty_days": "365", "active": "y"})
    owner.post(f"/work/jobs/{job_id}/lines/service", data={
        "service_id": bench, "labor_mode": "actual", "qty": "", "tech_user_id": "None",
        "billable": "y"})
    assert db_query(app, "SELECT qty FROM wo_line") == [(D("0.000"),)]
    # two people's time on one job: the minimum applies once, to the total
    half = minutes // 2
    owner.post(f"/work/jobs/{job_id}/time", data={"started_at": "2026-10-08T09:00",
                                                  "minutes": str(half), "billable": "y"})
    if minutes - half:
        owner.post(f"/work/jobs/{job_id}/time", data={"started_at": "2026-10-08T10:00",
                                                      "minutes": str(minutes - half),
                                                      "billable": "y"})
    # time that isn't billable doesn't count
    owner.post(f"/work/jobs/{job_id}/time", data={"started_at": "2026-10-08T11:00",
                                                  "minutes": "90", "note": "research"})
    assert db_query(app, "SELECT qty FROM wo_line") == [(D(hours),)]
    with app.app_context():
        line = db.session.scalar(select(WoLine))
        assert service.line_amount(line) == (D(hours) * 80).quantize(D("0.01"))
    # removing time re-bills
    first = db_query(app, "SELECT min(id) FROM time_entry")[0][0]
    owner.post(f"/work/time/{first}/delete")
    with app.app_context():
        remaining = db.session.scalars(select(TimeEntry.minutes).where(TimeEntry.billable)).all()
    expected = service.bill_hours(D(sum(remaining)) / 60, D("0.25"), D("0.5"))
    assert db_query(app, "SELECT qty FROM wo_line") == [(expected,)]


def activate(client, app, code, rate, kind="labor", unit="hour", warranty="365"):
    sid = service_id(app, code)
    resp = client.post(f"/pricebook/services/{sid}", data={
        "code": code, "name": code.title(), "kind": kind, "unit": unit, "rate": rate,
        "warranty_days": warranty, "active": "y"})
    assert resp.status_code == 302
    return sid


def test_one_actual_labor_line_per_job(app, signed_in):
    owner = signed_in("owner")
    cid, aid, wo_id, job_id = setup(owner, app)
    bench = activate(owner, app, "LAB-BENCH", "80")
    data = {"service_id": bench, "labor_mode": "actual", "qty": "", "tech_user_id": "None",
            "billable": "y"}
    owner.post(f"/work/jobs/{job_id}/lines/service", data=data)
    resp = owner.post(f"/work/jobs/{job_id}/lines/service", data=data, follow_redirects=True)
    assert "already has an actual-time labor line" in text(resp)
    owner.post(f"/work/jobs/{job_id}/lines/service", data={**data, "labor_mode": "flat",
                                                           "qty": "1.5"})
    assert db_query(app, "SELECT labor_mode, qty FROM wo_line ORDER BY id") == \
        [("actual", D("0.000")), ("flat", D("1.500"))]


def test_search_finds_work_orders(app, signed_in):
    tech = signed_in("tech")
    cid, aid, wo_id, job_id = setup(tech, app)
    tech.post(f"/work/jobs/{job_id}/edit", data={
        "status": "open", "complaint": "x", "inbound_tracking": "1Z999AA10123456784"})
    number = db_query(app, "SELECT number FROM work_order")[0][0]
    assert f"/work/{wo_id}" in text(tech.get(f"/search?q={number}"))
    assert f"/work/{wo_id}" in text(tech.get("/search?q=1Z999AA1012"))
