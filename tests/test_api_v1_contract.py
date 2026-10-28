"""The v1 machine API against contract/v1/ as the specification (vendored byte-identical
from arcade-tracker). The examples are the normative description: these tests post
examples/ingest-request.json and compare the replies against ingest-response.json, then
ingest-response-duplicate.json. That pair is Phase 7's exit test.

The examples name invented machines ("widget-wars"); the shop's machines are s- tags, so
the payload's machine is swapped for a real tag before posting. The replies don't carry
the machine, so the expected responses stay byte-identical."""

import copy
import hashlib
import importlib.util
import json
import pathlib
from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from test_assets import asset, customer, tag_of
from test_work import new_wo, text

from conftest import db_query

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contract" / "v1"
PUBLIC_ID = "a1b2c3d4e5f6"          # the examples' uids are hashed with this id


def example(name):
    return json.loads((CONTRACT / "examples" / name).read_text(encoding="utf-8"))


def request_for(tag, name="ingest-request.json"):
    payload = copy.deepcopy(example(name))
    for item in payload["items"]:
        if item.get("machine") == "widget-wars":
            item["machine"] = tag
    return payload


@pytest.fixture
def bench(app, signed_in):
    """A device with the examples' public id and its jobs switch on (the contract's
    behaviour), and two machines: Widget Wars (in the shop) and Sprocket (on site)."""
    from app.extensions import db
    from app.models import Device

    owner = signed_in("owner")
    cid = customer(owner, "Starlite Lanes Test")
    widget = asset(owner, cid, "Widget Wars", status="in_shop")
    sprocket = asset(owner, cid, "Sprocket")
    with app.app_context():
        dev = Device(name="test-bench", public_id=PUBLIC_ID, jobs=True)
        token = dev.issue_token()
        db.session.add(dev)
        db.session.commit()
    return {"owner": owner, "cid": cid, "token": token, "widget": widget,
            "sprocket": sprocket, "tag": tag_of(app, widget),
            "other": tag_of(app, sprocket)}


def auth(bench):
    return {"Authorization": f"Bearer {bench['token']}"}


def post(client, bench, payload, **kw):
    return client.post("/api/v1/ingest", json=payload, headers=auth(bench), **kw)


def count(app, table):
    return db_query(app, f"SELECT count(*) FROM {table}")[0][0]


# --- the contract files -----------------------------------------------------------------------

def test_the_contract_matches_its_checksums():
    """Two copies of anything drift; arcade-tracker and GATBOX check theirs the same way."""
    spec = importlib.util.spec_from_file_location("contract_checksums",
                                                  ROOT / "scripts" / "contract_checksums.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    problems = mod.compare(CONTRACT)
    assert not problems, "contract/v1 doesn't match CHECKSUMS:\n  " + "\n  ".join(problems)


def test_the_example_uids_follow_the_documented_rule():
    """`{epoch:.3f}` is contractual: `.6f` would hash differently and duplicate a trace."""
    req = example("ingest-request.json")
    pub = req["device"]
    session = next(i for i in req["items"] if i["kind"] == "rail_session")
    assert session["uid"] == hashlib.sha256(
        f"{pub}|rail_session|{session['file']}".encode()).hexdigest()[:32]
    for r in session["readings"]:
        assert r["uid"] == hashlib.sha256(
            f"{pub}|reading|{session['file']}|{r['epoch']:.3f}".encode()).hexdigest()[:32]
        assert r["uid"] != hashlib.sha256(
            f"{pub}|reading|{session['file']}|{r['epoch']:.6f}".encode()).hexdigest()[:32]
    tag = example("session-tag-request.json")["items"][0]
    assert tag["uid"] == hashlib.sha256(
        f"{pub}|session_tag|{tag['order']}|{tag['rail_session']}".encode()).hexdigest()[:32]


# --- health -----------------------------------------------------------------------------------

def test_health_needs_no_token_and_says_what_it_speaks(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200 and resp.mimetype == "application/json"
    body = resp.get_json()
    assert body["ok"] is True and body["contract"] == "v1"
    assert isinstance(body["time"], (int, float))
    assert body["app"] == "shop-hub" and body["capabilities"] == ["jobs"]
    assert "Set-Cookie" not in resp.headers


# --- the round trip: Phase 7's exit test ------------------------------------------------------

def test_posting_the_example_twice_gives_created_then_duplicate(client, bench, app):
    first = post(client, bench, request_for(bench["tag"]))
    assert first.status_code == 200, first.get_data(as_text=True)
    assert first.get_json() == example("ingest-response.json")
    stored = (count(app, "rail_session"), count(app, "reading"), count(app, "wo_job"),
              count(app, "work_order"), count(app, "wo_job_session"))
    second = post(client, bench, request_for(bench["tag"]))
    assert second.status_code == 200
    assert second.get_json() == example("ingest-response-duplicate.json")
    assert (count(app, "rail_session"), count(app, "reading"), count(app, "wo_job"),
            count(app, "work_order"), count(app, "wo_job_session")) == stored


def test_the_session_is_stored_exactly_as_sent(client, bench, app):
    from app.extensions import db
    from app.models import RailSession, Reading

    sent = next(i for i in example("ingest-request.json")["items"]
                if i["kind"] == "rail_session")
    post(client, bench, request_for(bench["tag"]))
    with app.app_context():
        row = db.session.query(RailSession).one()
        assert (row.uid, row.file, row.asset_id) == (sent["uid"], sent["file"], bench["widget"])
        assert row.powered_mean == D("5.558") and type(row.powered_mean) is D
        assert (row.window_lo, row.window_hi, row.alarm_hi) == (D("4.75"), D("5.25"),
                                                                D("5.775"))
        assert row.in_window_pct == D("50.0") and row.verdict == "over"
        assert row.started_at == datetime(2026, 9, 21, 14, 13, 20, tzinfo=UTC)
        assert row.clock == "rtc" and row.power_cycles == 1 and row.suspect == 0
        assert row.phase is None
        readings = db.session.query(Reading).order_by(Reading.at).all()
        assert [r.v for r in readings] == [D("5.012"), D("0.004"), D("6.104"), None]
        assert readings[3].raw == "inf" and readings[3].ol is True       # the meter's face
        assert readings[1].unit == "mV" and readings[2].alarm == "spike"
        assert readings[1].up == D("100.5")


# --- orders become jobs; traces find their jobs -----------------------------------------------

def test_an_order_with_no_open_work_order_starts_one(client, bench, app):
    post(client, bench, request_for(bench["tag"]))
    order = next(i for i in example("ingest-request.json")["items"] if i["kind"] == "order")
    wo = db_query(app, "SELECT kind, status, priority, summary, notes, customer_id "
                       "FROM work_order")
    assert wo == [("bench", "new", "high", order["issue"],
                   "Raised at the bench on GATBOX (test-bench).", bench["cid"])]
    job = db_query(app, "SELECT asset_id, complaint, external_id, reported_at, status "
                        "FROM wo_job")
    assert job == [(bench["widget"], order["issue"], order["uid"],
                    datetime(2026, 9, 21, 14, 15, tzinfo=UTC), "open")]
    # the session that prompted it is on the job; it arrived before the job existed
    assert db_query(app, "SELECT source, phase FROM wo_job_session") == [("order", None)]


def test_an_order_joins_the_open_work_order_and_traces_attach_themselves(client, bench, app):
    owner = bench["owner"]
    wo_id = new_wo(owner, bench["cid"], bench["widget"])
    first_job = db_query(app, "SELECT id FROM wo_job WHERE work_order_id = :w", w=wo_id)[0][0]
    resp = post(client, bench, request_for(bench["tag"]))
    assert resp.get_json() == example("ingest-response.json")
    # one open job when the trace arrived: it attached itself; the order then joined the
    # same work order as a second job, with the trace that prompted it
    assert db_query(app, "SELECT count(*) FROM work_order") == [(1,)]
    jobs = db_query(app, "SELECT id, external_id FROM wo_job WHERE work_order_id = :w "
                         "ORDER BY id", w=wo_id)
    assert jobs[0] == (first_job, None) and jobs[1][1] is not None
    links = db_query(app, "SELECT wo_job_id, source FROM wo_job_session ORDER BY id")
    assert links == [(first_job, "auto"), (jobs[1][0], "order")]


def test_a_trace_waits_when_the_machine_has_two_open_jobs(client, bench, app):
    owner = bench["owner"]
    wo_id = new_wo(owner, bench["cid"], bench["widget"])
    owner.post(f"/work/{wo_id}/jobs", data={"asset_id": str(bench["widget"]),
                                             "complaint": "Coin door"})
    payload = request_for(bench["tag"])
    payload["items"] = payload["items"][:1]
    post(client, bench, payload)
    assert count(app, "wo_job_session") == 0
    page = text(owner.get(f"/assets/{bench['widget']}"))
    assert "Not on a job" in page


def test_phase_is_optional_and_checked(client, bench, app):
    payload = request_for(bench["tag"])
    session = payload["items"][0]
    session["phase"] = "sideways"
    body = post(client, bench, {**payload, "items": [session]}).get_json()
    assert body["results"][0]["status"] == "rejected"
    assert "phase must be one of" in body["results"][0]["reason"]
    assert count(app, "rail_session") == 0                    # nothing left behind
    session["phase"] = "as_found"
    owner = bench["owner"]
    new_wo(owner, bench["cid"], bench["widget"])
    post(client, bench, {**payload, "items": [session]})
    assert db_query(app, "SELECT phase FROM rail_session") == [("as_found",)]
    assert db_query(app, "SELECT phase, source FROM wo_job_session") == [("as_found", "auto")]


# --- authentication ---------------------------------------------------------------------------

@pytest.mark.parametrize("header", [
    None, {"Authorization": ""}, {"Authorization": "Bearer"}, {"Authorization": "Bearer "},
    {"Authorization": f"Basic gbx_{PUBLIC_ID}.secret"},
    {"Authorization": "Bearer not-a-gatbox-token"},
    {"Authorization": "Bearer gbx_ffffffffffff.secret"},
    {"Authorization": f"Bearer gbx_{PUBLIC_ID}.wrong-secret"},
])
def test_a_bad_token_is_401_json_not_a_redirect(client, bench, header):
    resp = client.post("/api/v1/ingest", json=example("ingest-request.json"),
                       headers=header or {})
    assert resp.status_code == 401 and "Location" not in resp.headers
    assert resp.get_json() == {"error": "a valid device token is required"}


def test_a_switched_off_device_is_refused(client, bench, app):
    from app.extensions import db
    from app.models import Device

    with app.app_context():
        db.session.query(Device).update({"enabled": False})
        db.session.commit()
    assert post(client, bench, request_for(bench["tag"])).status_code == 401


@pytest.mark.parametrize("path", ["/api/v1/roster", "/api/v1/machines/s-0001-x/orders"])
def test_the_read_endpoints_need_a_token_too(client, bench, path):
    resp = client.get(path)
    assert resp.status_code == 401 and resp.get_json()["error"]


def test_the_device_records_when_it_was_last_seen(client, bench, app, tdb):
    from test_billing import owner_sql

    assert db_query(app, "SELECT last_seen_at FROM device") == [(None,)]
    client.get("/api/v1/roster", headers=auth(bench))
    seen, ip = db_query(app, "SELECT last_seen_at, last_ip FROM device")[0]
    assert seen is not None and ip == "127.0.0.1"
    # polling doesn't rewrite the (audited) row every time: at most every 15 minutes
    audited = db_query(app, "SELECT count(*) FROM audit_log WHERE table_name = 'device'")[0][0]
    for _ in range(3):
        client.get("/api/v1/roster", headers=auth(bench))
    assert db_query(app, "SELECT last_seen_at FROM device") == [(seen,)]
    assert db_query(app, "SELECT count(*) FROM audit_log WHERE table_name = 'device'") == \
        [(audited,)]
    owner_sql(tdb, "UPDATE device SET last_seen_at = last_seen_at - interval '16 minutes'")
    client.get("/api/v1/roster", headers=auth(bench))
    assert db_query(app, "SELECT last_seen_at FROM device")[0][0] > seen


# --- refusals and limits ----------------------------------------------------------------------

def test_a_payload_for_another_device_is_refused(client, bench):
    payload = request_for(bench["tag"])
    payload["device"] = "ffffffffffff"
    resp = post(client, bench, payload)
    assert resp.status_code == 400 and "different device" in resp.get_json()["error"]


@pytest.mark.parametrize("raw,fragment", [
    (b"[]", "JSON object"),
    (b'{"contract": "v2"}', "contract must be"),
    (b'{"contract": "v1", "device": "a1b2c3d4e5f6", "items": []}', "non-empty"),
    (b'{"contract": "v1", "device": "a1b2c3d4e5f6", "items": [{"v": NaN}]}', "must be JSON"),
    (b"not json", "must be JSON"),
])
def test_a_malformed_body_is_400(client, bench, raw, fragment):
    resp = client.post("/api/v1/ingest", data=raw, content_type="application/json",
                       headers=auth(bench))
    assert resp.status_code == 400 and fragment in resp.get_json()["error"]


def test_wrong_type_and_oversize_bodies(client, bench):
    resp = client.post("/api/v1/ingest", data="{}", content_type="text/plain",
                       headers=auth(bench))
    assert resp.status_code == 415 and resp.get_json()["error"]
    resp = client.post("/api/v1/ingest", data=b" " * (8 * 1024 * 1024 + 1),
                       content_type="application/json", headers=auth(bench))
    assert resp.status_code == 413 and resp.get_json()["error"]


def test_unknown_paths_and_methods_answer_in_json(client):
    resp = client.get("/api/v1/nothing-here")
    assert resp.status_code == 404 and resp.get_json() == {"error": "no such path"}
    resp = client.get("/api/v1/ingest")
    assert resp.status_code == 405 and resp.get_json() == {"error": "method not allowed"}


def test_too_many_items_is_refused_whole(client, bench, app):
    payload = request_for(bench["tag"])
    payload["items"] = payload["items"][:1] * 101
    resp = post(client, bench, payload)
    assert resp.status_code == 400 and "at most 100 items" in resp.get_json()["error"]
    assert count(app, "rail_session") == 0


def test_reading_limits(client, bench, app):
    payload = request_for(bench["tag"])
    session = payload["items"][0]
    session["readings"] = session["readings"][:1] * 2001
    body = post(client, bench, {**payload, "items": [session]}).get_json()
    assert body["results"][0]["status"] == "rejected"
    assert "at most 2000 readings per session" in body["results"][0]["reason"]
    many = [dict(session, readings=session["readings"][:2000]) for _ in range(11)]
    resp = post(client, bench, {**payload, "items": many})
    assert resp.status_code == 400 and "readings per request" in resp.get_json()["error"]


def test_an_unknown_machine_is_rejected_never_created(client, bench, app):
    assets = count(app, "asset")
    payload = request_for("s-9999-nowhere")
    results = post(client, bench, payload).get_json()["results"]
    assert [r["status"] for r in results] == ["rejected", "rejected"]
    assert "no machine with slug 's-9999-nowhere'" in results[0]["reason"]
    assert count(app, "asset") == assets and count(app, "rail_session") == 0


def test_an_order_naming_an_unsent_session_is_rejected(client, bench, app):
    payload = request_for(bench["tag"])
    payload["items"] = payload["items"][1:]
    result = post(client, bench, payload).get_json()["results"][0]
    assert result["status"] == "rejected" and "has not been ingested yet" in result["reason"]
    assert count(app, "work_order") == 0                      # no half-made work order


def test_an_unknown_kind_is_rejected_without_affecting_the_others(client, bench):
    payload = request_for(bench["tag"])
    payload["items"].insert(0, {"kind": "rom_dump", "uid": "0" * 32})
    statuses = [r["status"] for r in post(client, bench, payload).get_json()["results"]]
    assert statuses == ["rejected", "created", "created"]


@pytest.mark.parametrize("field,value,reason", [
    ("started", "yesterday", "started must be a number"),
    ("samples", 2.5, "samples must be a whole number"),
    ("duration_s", True, "duration_s must be a number"),
    ("mode", 7, "mode must be text"),
    ("verdict", {"state": "maybe"}, "verdict.state must be one of"),
    ("rate", 1e20, "rate is out of range"),
])
def test_bad_fields_reject_the_item_and_write_nothing(client, bench, app, field, value, reason):
    payload = request_for(bench["tag"])
    payload["items"][0][field] = value
    results = post(client, bench, payload).get_json()["results"]
    assert results[0]["status"] == "rejected" and reason in results[0]["reason"]
    assert count(app, "rail_session") == 0 and count(app, "reading") == 0


# --- roster and orders ------------------------------------------------------------------------

def test_the_roster_lists_active_machines_without_customer_names(client, bench, app):
    owner = bench["owner"]
    sold = asset(owner, bench["cid"], "Gone Game", status="sold")
    owner.post(f"/assets/{bench['widget']}/edit", data={
        "name": "Widget Wars", "kind": "machine", "model_key": "widget-wars",
        "shop_location": "Bench 2", "site_id": "None", "parent_asset_id": "None"})
    resp = client.get("/api/v1/roster", headers=auth(bench))
    body = resp.get_json()
    assert body["contract"] == "v1"
    machines = {m["slug"]: m for m in body["machines"]}
    assert set(machines) == {bench["tag"], bench["other"]}
    assert tag_of(app, sold) not in machines
    want = example("roster-response.json")["machines"][0]
    for m in machines.values():
        assert set(want) <= set(m) and all(isinstance(m[k], str) for k in want)
    assert machines[bench["other"]]["location"] == "On site"
    assert machines[bench["other"]]["status"] == "In service"
    assert "Starlite" not in resp.get_data(as_text=True)


def test_open_orders_for_a_machine(client, bench, app):
    post(client, bench, request_for(bench["tag"]))
    owner = bench["owner"]
    wo_id = db_query(app, "SELECT id FROM work_order")[0][0]
    owner.post(f"/work/{wo_id}/jobs", data={"asset_id": str(bench["widget"]),
                                             "complaint": "Coin door latch sticks"})
    body = client.get(f"/api/v1/machines/{bench['tag']}/orders?status=open",
                      headers=auth(bench)).get_json()
    want = example("orders-response.json")
    assert (body["contract"], body["machine"], body["status"]) == ("v1", bench["tag"], "open")
    assert len(body["orders"]) == 2
    for order in body["orders"]:
        assert set(want["orders"][0]) <= set(order)
        assert isinstance(order["id"], int) and isinstance(order["reported"], float)
        assert order["work_order"].startswith("WO-") and order["nte"] is None
    by_source = {o["source"]: o for o in body["orders"]}
    gatbox = by_source["gatbox"]
    assert gatbox["external_id"] == want["orders"][0]["external_id"]
    assert gatbox["issue"] == want["orders"][0]["issue"]
    assert gatbox["reported"] == want["orders"][0]["reported"]
    assert (gatbox["priority"], gatbox["status"]) == ("High", "New")
    assert by_source["web"]["external_id"] is None
    # a finished job drops off "open" but stays in "all"
    job_id = gatbox["id"]
    owner.post(f"/work/jobs/{job_id}/edit", data={"status": "done", "complaint": gatbox["issue"]})
    still = client.get(f"/api/v1/machines/{bench['tag']}/orders", headers=auth(bench)).get_json()
    assert [o["id"] for o in still["orders"]] != [] and job_id not in \
        [o["id"] for o in still["orders"]]
    everything = client.get(f"/api/v1/machines/{bench['tag']}/orders?status=all",
                            headers=auth(bench)).get_json()
    assert {o["id"]: o["status"] for o in everything["orders"]}[job_id] == "Done"


@pytest.mark.parametrize("query,code", [("?status=open", 200), ("?status=all", 200),
                                        ("?status=closed", 400), ("", 200)])
def test_the_status_filter_is_checked(client, bench, query, code):
    resp = client.get(f"/api/v1/machines/{bench['tag']}/orders{query}", headers=auth(bench))
    assert resp.status_code == code


def test_orders_for_an_unknown_machine_is_404_json(client, bench):
    resp = client.get("/api/v1/machines/s-9999-nowhere/orders", headers=auth(bench))
    assert resp.status_code == 404 and "no machine" in resp.get_json()["error"]


# --- session tags -----------------------------------------------------------------------------

def tag_payload(job_id, session_uid, uid="0734fe4f8739c1470dc6477d9364d42c", **extra):
    payload = example("session-tag-request.json")
    payload["items"][0].update({"order": job_id, "rail_session": session_uid, "uid": uid,
                                **extra})
    return payload


@pytest.fixture
def ingested(client, bench, app):
    """The example ingested: one session, and the job its order made."""
    post(client, bench, request_for(bench["tag"]))
    job_id = db_query(app, "SELECT id FROM wo_job")[0][0]
    session_uid = db_query(app, "SELECT uid FROM rail_session")[0][0]
    return job_id, session_uid


def test_a_session_tag_puts_a_trace_on_a_job(client, bench, app, ingested):
    owner = bench["owner"]
    job_id, session_uid = ingested
    wo_id = db_query(app, "SELECT work_order_id FROM wo_job")[0][0]
    owner.post(f"/work/{wo_id}/jobs", data={"asset_id": str(bench["widget"]),
                                             "complaint": "Second look"})
    other_job = db_query(app, "SELECT max(id) FROM wo_job")[0][0]
    result = post(client, bench, tag_payload(other_job, session_uid, phase="as_left",
                                             note="second pass, cold")).get_json()
    assert result["results"] == [{"uid": "0734fe4f8739c1470dc6477d9364d42c",
                                  "kind": "session_tag", "status": "created"}]
    assert db_query(app, "SELECT source, phase, note FROM wo_job_session WHERE wo_job_id = :j",
                    j=other_job) == [("tag", "as_left", "second pass, cold")]
    # the same pair again, even with a fresh uid, is a duplicate
    for uid in ("0734fe4f8739c1470dc6477d9364d42c", "f" * 32):
        again = post(client, bench, tag_payload(other_job, session_uid, uid=uid)).get_json()
        assert again["results"][0]["status"] == "duplicate"
    # the job its order made already has it
    assert post(client, bench, tag_payload(job_id, session_uid)).get_json()[
        "results"][0]["status"] == "duplicate"


@pytest.mark.parametrize("change,reason", [
    ({"order": 999999}, "no order with id 999999"),
    ({"order": "41"}, "integer id"),
    ({"rail_session": "e" * 32}, "has not been ingested yet"),
    ({"rail_session": None}, "rail_session is required"),
    ({"phase": "later"}, "phase must be one of"),
])
def test_a_bad_session_tag_is_rejected(client, bench, ingested, change, reason):
    job_id, session_uid = ingested
    payload = tag_payload(job_id, session_uid)
    payload["items"][0].update(change)
    result = post(client, bench, payload).get_json()["results"][0]
    assert result["status"] == "rejected" and reason in result["reason"]


def test_a_tag_on_a_closed_job_is_accepted_but_not_from_another_machine(client, bench, app,
                                                                         ingested):
    owner = bench["owner"]
    job_id, session_uid = ingested
    wo_id = db_query(app, "SELECT work_order_id FROM wo_job")[0][0]
    owner.post(f"/work/{wo_id}/jobs", data={"asset_id": str(bench["widget"]),
                                             "complaint": "Finished one"})
    closed = db_query(app, "SELECT max(id) FROM wo_job")[0][0]
    owner.post(f"/work/jobs/{closed}/edit", data={"status": "done", "complaint": "Finished one"})
    assert post(client, bench, tag_payload(closed, session_uid)).get_json()[
        "results"][0]["status"] == "created"
    owner.post(f"/work/{wo_id}/jobs", data={"asset_id": str(bench["sprocket"]),
                                             "complaint": "Other machine"})
    sprocket_job = db_query(app, "SELECT max(id) FROM wo_job")[0][0]
    result = post(client, bench, tag_payload(sprocket_job, session_uid)).get_json()["results"][0]
    assert result["status"] == "rejected" and "different machine" in result["reason"]


# --- rate limits ------------------------------------------------------------------------------

def test_the_read_endpoints_are_rate_limited_per_device(client, bench):
    from app.extensions import limiter

    limiter.enabled = True
    limiter.reset()
    try:
        codes = [client.get("/api/v1/roster", headers=auth(bench)).status_code
                 for _ in range(61)]
        assert codes[:60] == [200] * 60 and codes[60] == 429
        resp = client.get("/api/v1/roster", headers=auth(bench))
        assert resp.get_json() == {"error": "rate limited"}
        # another device's bucket is its own
        assert client.get("/api/v1/roster", headers={
            "Authorization": "Bearer gbx_ffffffffffff.x"}).status_code == 401
    finally:
        limiter.reset()
        limiter.enabled = False
