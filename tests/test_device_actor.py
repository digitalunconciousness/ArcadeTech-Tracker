"""Phase 10: a GATBOX device acts as a user, audit_log names the device, and a jobs switch
per device (owner, 2026-10-28). Migration 0025's round trip is in test_migrations.py.

audit_row() runs on every audited table, so it is checked here from every side: a device
ingest (user and device on each row), a person's request straight after one (unchanged),
a device with no acts-as (device only, as before otherwise), a switched-off acts-as user,
the settings read directly in SQL, and a rejected item (its savepoint takes its audit rows
with it)."""
# The bench fixture is imported from test_api_v1_contract and named as a parameter, as in
# test_gatbox.py: F811 for every test here.
# ruff: noqa: F811

import hashlib
import re

import psycopg
import pytest
from test_api_v1_contract import (  # noqa: F401
    PUBLIC_ID,
    auth,
    bench,
    example,
    post,
    request_for,
    tag_payload,
)
from test_billing import owner_sql
from test_work import new_wo, text

from conftest import db_query, libpq

GATBOX_TABLES = ("work_order", "wo_job", "wo_job_session", "device")


def device_id(app):
    return db_query(app, "SELECT id FROM device WHERE public_id = :p", p=PUBLIC_ID)[0][0]


def owner_id(bench):
    return bench["owner"].user["id"]


def set_access(bench, app, acts_as=None, jobs=True):
    """Through Settings → Devices, as the owner."""
    did = device_id(app)
    data = {f"d{did}-acts_as_user_id": str(acts_as) if acts_as else "None"}
    if jobs:
        data[f"d{did}-jobs"] = "y"
    resp = bench["owner"].post(f"/settings/devices/{did}/edit", data=data)
    assert resp.status_code == 302, text(resp)[-800:]
    return did


def audit_since(app, last_id, tables=GATBOX_TABLES):
    return db_query(app, "SELECT table_name, action, user_id, device_id FROM audit_log "
                         "WHERE id > :i AND table_name = ANY(:t) ORDER BY id",
                    i=last_id, t=list(tables))


def last_audit_id(app):
    return db_query(app, "SELECT coalesce(max(id), 0) FROM audit_log")[0][0]


# --- the device acts as her, and the audit log names both ----------------------------------

def test_a_device_ingest_is_audited_as_her_via_the_device(client, bench, app):
    did = set_access(bench, app, acts_as=owner_id(bench))
    mark = last_audit_id(app)
    resp = post(client, bench, request_for(bench["tag"]))
    assert resp.get_json() == example("ingest-response.json")       # contract unchanged
    rows = audit_since(app, mark)
    # the order made a work order and a job, the trace went on it, last seen was written
    assert {t for t, *_ in rows} == set(GATBOX_TABLES)
    assert {(u, d) for _, _, u, d in rows} == {(owner_id(bench), did)}
    # every row Postgres stamped with created_by names her too
    for table in ("work_order", "wo_job", "wo_job_session", "rail_session", "reading"):
        assert db_query(app, f"SELECT DISTINCT created_by FROM {table}") == \
            [(owner_id(bench),)], table


def test_a_person_straight_after_a_device_is_unchanged(client, bench, app):
    """Same process, same connection pool: nothing of the device's request carries over."""
    set_access(bench, app, acts_as=owner_id(bench))
    post(client, bench, request_for(bench["tag"]))
    owner = bench["owner"]
    wo_id = db_query(app, "SELECT id FROM work_order")[0][0]
    mark = last_audit_id(app)
    resp = owner.post(f"/work/{wo_id}/jobs", data={"asset_id": str(bench["widget"]),
                                                    "complaint": "Coin door"})
    assert resp.status_code == 302
    assert audit_since(app, mark, ("wo_job",)) == [("wo_job", "I", owner_id(bench), None)]
    # and a device request after that is the device again
    new_job = db_query(app, "SELECT max(id) FROM wo_job")[0][0]
    session_uid = db_query(app, "SELECT uid FROM rail_session")[0][0]
    mark = last_audit_id(app)
    assert post(client, bench, tag_payload(new_job, session_uid)).get_json()[
        "results"][0]["status"] == "created"
    assert audit_since(app, mark, ("wo_job_session",)) == \
        [("wo_job_session", "I", owner_id(bench), device_id(app))]


def test_a_device_acting_as_no_one_still_names_the_device(client, bench, app):
    did = device_id(app)
    assert db_query(app, "SELECT acts_as_user_id FROM device") == [(None,)]
    mark = last_audit_id(app)
    post(client, bench, request_for(bench["tag"]))
    rows = audit_since(app, mark)
    assert rows and {(u, d) for _, _, u, d in rows} == {(None, did)}
    assert db_query(app, "SELECT DISTINCT created_by FROM wo_job") == [(None,)]  # as before


def test_a_switched_off_user_s_device_acts_as_no_one(client, bench, app, tdb, make_user):
    tess = make_user("tess", "tech")
    did = set_access(bench, app, acts_as=tess["id"])
    owner_sql(tdb, "UPDATE app_user SET active = false WHERE id = %(i)s", i=tess["id"])
    mark = last_audit_id(app)
    post(client, bench, request_for(bench["tag"]))
    assert {(u, d) for _, _, u, d in audit_since(app, mark)} == {(None, did)}
    page = text(bench["owner"].get("/settings/devices"))
    assert "acts as no one" in page and "Tess is switched off" in page


def test_a_rejected_item_leaves_no_audit_rows(client, bench, app):
    set_access(bench, app, acts_as=owner_id(bench))
    payload = request_for(bench["tag"])
    order = payload["items"][1]
    payload["items"] = [order]                         # names a session not yet sent
    mark = last_audit_id(app)
    result = post(client, bench, payload).get_json()["results"][0]
    assert result["status"] == "rejected"
    assert audit_since(app, mark, ("work_order", "wo_job", "wo_job_session")) == []


def _as_app(tdb):
    return psycopg.connect(libpq(tdb.url("app")))


def test_the_trigger_reads_both_settings(app, tdb, make_user, bench):
    """Straight SQL as the app role, as a hand-run fix would: unset, empty and set."""
    tess = make_user("tess", "tech")
    did = device_id(app)
    cases = [(None, None, None, None), ("", "", None, None),
             (str(tess["id"]), "", tess["id"], None),
             (str(tess["id"]), str(did), tess["id"], did), ("", str(did), None, did)]
    for n, (uid, dev, want_user, want_device) in enumerate(cases):
        with _as_app(tdb) as c:
            if uid is not None:
                c.execute("SELECT set_config('shop.user_id', %s, true), "
                          "set_config('shop.device_id', %s, true)", (uid, dev))
            c.execute("UPDATE app_user SET display_name = %s WHERE id = %s",
                      (f"Tess {n}", tess["id"]))
        assert db_query(app, "SELECT user_id, device_id FROM audit_log WHERE table_name = "
                             "'app_user' AND after->>'display_name' = :n", n=f"Tess {n}") == \
            [(want_user, want_device)], (uid, dev)


def test_a_device_id_that_isnt_a_device_is_refused(app, tdb, make_user):
    """The foreign key: an audit row can't name a device that doesn't exist, so a bad
    setting fails the change rather than writing a dangling name."""
    tess = make_user("tess", "tech")
    with pytest.raises(psycopg.errors.ForeignKeyViolation), _as_app(tdb) as c:
        c.execute("SELECT set_config('shop.device_id', '999999', true)")
        c.execute("UPDATE app_user SET display_name = 'x' WHERE id = %s", (tess["id"],))


def test_the_audit_page_says_via_the_device(client, bench, app):
    owner = bench["owner"]
    set_access(bench, app, acts_as=owner_id(bench))
    post(client, bench, request_for(bench["tag"]))
    name = db_query(app, "SELECT display_name FROM app_user WHERE id = :i",
                    i=owner_id(bench))[0][0]
    assert f"<td>{name} via test-bench</td>" in text(owner.get("/settings/audit?table=wo_job"))
    csv = owner.get("/settings/audit?table=wo_job&format=csv").get_data(as_text=True)
    assert f"{name} via test-bench" in csv
    # her own change in Settings → Devices names only her
    assert f"<td>{name}</td>" in text(owner.get("/settings/audit?table=device"))
    # acting as no one: the device alone
    set_access(bench, app, acts_as=None)
    wo_id = db_query(app, "SELECT id FROM work_order")[0][0]
    owner.post(f"/work/{wo_id}/jobs", data={"asset_id": str(bench["widget"]),
                                             "complaint": "Second look"})
    second = db_query(app, "SELECT max(id) FROM wo_job")[0][0]
    post(client, bench, tag_payload(second, db_query(app, "SELECT uid FROM rail_session")[0][0]))
    assert "<td>system via test-bench</td>" in \
        text(owner.get("/settings/audit?table=wo_job_session"))


# --- the jobs switch ---------------------------------------------------------------------------

def test_without_jobs_orders_look_like_an_unknown_machine(client, bench, app):
    set_access(bench, app, jobs=False)
    seen = db_query(app, "SELECT last_seen_at FROM device")
    known = client.get(f"/api/v1/machines/{bench['tag']}/orders", headers=auth(bench))
    assert known.status_code == 404
    assert known.get_json() == {"error": f"no machine with slug {bench['tag']!r}"}
    # exactly what a device with jobs gets for a machine the hub doesn't have
    set_access(bench, app, jobs=True)
    unknown = client.get("/api/v1/machines/s-9999-nowhere/orders", headers=auth(bench))
    assert unknown.status_code == 404
    assert unknown.get_json() == {"error": "no machine with slug 's-9999-nowhere'"}
    assert sorted(known.headers.keys()) == sorted(unknown.headers.keys())
    assert db_query(app, "SELECT last_seen_at FROM device") == seen     # neither counts
    set_access(bench, app, jobs=False)
    # the status check comes first for both, as before
    for slug in (bench["tag"], "s-9999-nowhere"):
        assert client.get(f"/api/v1/machines/{slug}/orders?status=closed",
                          headers=auth(bench)).status_code == 400
    # with jobs on again, the machine's jobs are there
    set_access(bench, app, jobs=True)
    assert client.get(f"/api/v1/machines/{bench['tag']}/orders",
                      headers=auth(bench)).status_code == 200


def test_without_jobs_orders_and_tags_are_rejected_and_traces_still_land(client, bench, app):
    set_access(bench, app, jobs=False)
    results = post(client, bench, request_for(bench["tag"])).get_json()["results"]
    assert [r["kind"] for r in results] == ["rail_session", "order"]
    assert results[0]["status"] == "created" and results[0]["readings"]["created"] == 4
    order = example("ingest-request.json")["items"][1]
    assert results[1] == {"uid": order["uid"], "kind": "order", "status": "rejected",
                          "reason": "jobs are switched off for this device "
                                    "(Settings → Devices on the hub)"}
    assert db_query(app, "SELECT count(*) FROM work_order") == [(0,)]
    # a tag onto a job someone made by hand: rejected the same way, nothing attached
    wo_id = new_wo(bench["owner"], bench["cid"], bench["widget"])
    job_id = db_query(app, "SELECT id FROM wo_job WHERE work_order_id = :w", w=wo_id)[0][0]
    session_uid = db_query(app, "SELECT uid FROM rail_session")[0][0]
    tag = post(client, bench, tag_payload(job_id, session_uid)).get_json()["results"][0]
    assert tag["status"] == "rejected" and "jobs are switched off" in tag["reason"]
    assert db_query(app, "SELECT count(*) FROM wo_job_session") == [(0,)]
    # roster and health don't need it
    assert client.get("/api/v1/roster", headers=auth(bench)).status_code == 200
    assert client.get("/api/v1/health").get_json()["capabilities"] == ["jobs"]
    # switched on: the same order goes through
    set_access(bench, app, jobs=True)
    results = post(client, bench, request_for(bench["tag"])).get_json()["results"]
    assert [r["status"] for r in results] == ["duplicate", "created"]


def test_a_new_device_starts_without_jobs(app, signed_in, client):
    owner = signed_in("owner")
    page = text(owner.post("/settings/devices", data={"name": "new-pi"}))
    token = re.search(r"gbx_[0-9a-f]{12}\.[A-Za-z0-9_-]{43}", page).group(0)
    assert db_query(app, "SELECT jobs, acts_as_user_id FROM device") == [(False, None)]
    public_id = token[4:16]
    body = {"contract": "v1", "device": public_id, "items": [{
        "kind": "order", "uid": hashlib.sha256(b"x").hexdigest()[:32], "machine": "s-0001-x",
        "issue": "No picture"}]}
    result = client.post("/api/v1/ingest", json=body,
                         headers={"Authorization": f"Bearer {token}"}).get_json()["results"][0]
    assert result["status"] == "rejected" and "jobs are switched off" in result["reason"]


# --- Settings → Devices ------------------------------------------------------------------------

def test_owners_set_acts_as_and_jobs(app, bench, make_user):
    owner = bench["owner"]
    tess = make_user("tess", "tech", display_name="Tess Tech")
    did = device_id(app)
    page = text(owner.get("/settings/devices"))
    assert "Acts as no one · jobs on" in page and "Tess Tech" in page     # offered
    resp = owner.post(f"/settings/devices/{did}/edit",
                      data={f"d{did}-acts_as_user_id": str(tess["id"])}, follow_redirects=True)
    assert "Saved test-bench." in text(resp)
    assert db_query(app, "SELECT acts_as_user_id, jobs FROM device") == [(tess["id"], False)]
    assert "Acts as Tess Tech · jobs off" in text(owner.get("/settings/devices"))
    # the change is audited, as the owner
    rows = db_query(app, "SELECT user_id, device_id, after->>'acts_as_user_id' FROM audit_log "
                         "WHERE table_name = 'device' AND action = 'U' ORDER BY id DESC LIMIT 1")
    assert rows == [(owner_id(bench), None, str(tess["id"]))]


@pytest.mark.parametrize("value", ["999999", "abc", "inactive"])
def test_only_an_active_user_can_be_chosen(app, bench, make_user, value):
    owner = bench["owner"]
    gone = make_user("gone", "tech", active=False)
    did = device_id(app)
    value = str(gone["id"]) if value == "inactive" else value
    resp = owner.post(f"/settings/devices/{did}/edit",
                      data={f"d{did}-acts_as_user_id": value, f"d{did}-jobs": "y"},
                      follow_redirects=True)
    assert "Acts as:" in text(resp)                                       # the error
    assert db_query(app, "SELECT acts_as_user_id, jobs FROM device") == [(None, True)]


def test_only_owners_edit_a_device(app, bench, signed_in):
    did = device_id(app)
    for who in (signed_in("tech"), signed_in("viewer")):
        assert who.post(f"/settings/devices/{did}/edit",
                        data={f"d{did}-jobs": "y"}).status_code == 403
    assert bench["owner"].post("/settings/devices/999999/edit").status_code == 404
