"""GATBOX for people (Phase 7): devices in Settings, a trace's page, traces on the job and
asset pages (phase, take off, put on), the service report's rail readings, and the console
script. The API itself is tests/test_api_v1_contract.py."""

import re

from test_api_v1_contract import PUBLIC_ID, auth, bench, post, request_for  # noqa: F401
from test_billing import capture
from test_scripts import load, scripted
from test_work import new_wo, text

from conftest import db_query


def test_owners_mint_devices_and_see_the_token_once(app, signed_in, client):
    owner = signed_in("owner")
    resp = owner.post("/settings/devices", data={"name": "bench-pi", "notes": "the bench box"})
    page = text(resp)
    assert resp.status_code == 200 and "only time the token is shown" in page
    token = re.search(r"gbx_[0-9a-f]{12}\.[A-Za-z0-9_-]{43}", page).group(0)
    name, public_id, stored, enabled = db_query(
        app, "SELECT name, public_id, token_hash, enabled FROM device")[0]
    assert (name, enabled) == ("bench-pi", True) and token.startswith(f"gbx_{public_id}.")
    assert token.split(".", 1)[1] not in stored                 # only a hash is kept
    assert client.get("/api/v1/roster", headers={"Authorization": f"Bearer {token}"}) \
        .status_code == 200
    listing = text(owner.get("/settings/devices"))
    assert "bench-pi" in listing and token not in listing and "never seen" not in listing
    # a duplicate name is refused
    resp = owner.post("/settings/devices", data={"name": "bench-pi"}, follow_redirects=True)
    assert "already has that name" in text(resp)
    # a new token: the old one stops at once
    device_id = db_query(app, "SELECT id FROM device")[0][0]
    page = text(owner.post(f"/settings/devices/{device_id}/rotate"))
    fresh = re.search(r"gbx_[0-9a-f]{12}\.[A-Za-z0-9_-]{43}", page).group(0)
    assert fresh != token
    assert client.get("/api/v1/roster", headers={"Authorization": f"Bearer {token}"}) \
        .status_code == 401
    assert client.get("/api/v1/roster", headers={"Authorization": f"Bearer {fresh}"}) \
        .status_code == 200
    # switched off, then on
    owner.post(f"/settings/devices/{device_id}/switch")
    assert client.get("/api/v1/roster", headers={"Authorization": f"Bearer {fresh}"}) \
        .status_code == 401
    owner.post(f"/settings/devices/{device_id}/switch")
    assert db_query(app, "SELECT enabled FROM device") == [(True,)]
    # the audit log never holds the hash
    logged = db_query(app, "SELECT after::text FROM audit_log WHERE table_name = 'device'")
    assert logged and all(stored not in row[0] for row in logged)


def test_only_owners_manage_devices(app, signed_in):
    tech, viewer = signed_in("tech"), signed_in("viewer")
    for who in (tech, viewer):
        assert who.get("/settings/devices").status_code == 403
        assert who.post("/settings/devices", data={"name": "x"}).status_code == 403
    assert db_query(app, "SELECT count(*) FROM device") == [(0,)]


def test_create_device_script(app):
    out = []
    rc = load("create_device").main([], ask=scripted("", "console-pi"), out=out.append)
    assert rc == 0, out
    token = next(line for line in out if line.startswith("gbx_"))
    public_id = db_query(app, "SELECT public_id FROM device WHERE name = 'console-pi'")[0][0]
    assert token.startswith(f"gbx_{public_id}.")
    out = []
    rc = load("create_device").main([], ask=scripted("console-pi", "second-pi"), out=out.append)
    assert rc == 0 and "already has that name" in out[0]
    assert load("create_device").main(["--name", "x"], out=out.append) == 2


def test_traces_on_the_job_page(app, client, bench, signed_in):          # noqa: F811
    owner = bench["owner"]
    wo_id = new_wo(owner, bench["cid"], bench["widget"])
    job_id = db_query(app, "SELECT id FROM wo_job WHERE work_order_id = :w", w=wo_id)[0][0]
    payload = request_for(bench["tag"])
    payload["items"] = payload["items"][:1]
    post(client, bench, payload)                                         # attaches itself
    link_id, session_id = db_query(app, "SELECT id, rail_session_id FROM wo_job_session")[0]
    page = text(owner.get(f"/work/jobs/{job_id}"))
    assert f"/traces/{session_id}" in page and "went over" in page
    assert "5.012 – 6.104 V, mean 5.558" in page and "50% in window" in page
    # phase
    resp = owner.post(f"/traces/links/{link_id}/phase", data={"phase": "as_found",
                                                             "back": "job"})
    assert resp.status_code == 302 and resp.location.endswith(f"/work/jobs/{job_id}#traces")
    assert db_query(app, "SELECT phase FROM wo_job_session") == [("as_found",)]
    owner.post(f"/traces/links/{link_id}/phase", data={"phase": "sideways"})
    assert db_query(app, "SELECT phase FROM wo_job_session") == [("as_found",)]
    # take it off: it waits on the asset page, and the job page offers it back
    owner.post(f"/traces/links/{link_id}/detach", data={"back": "job"})
    assert db_query(app, "SELECT count(*) FROM wo_job_session") == [(0,)]
    assert "Not on a job" in text(owner.get(f"/assets/{bench['widget']}"))
    page = text(owner.get(f"/work/jobs/{job_id}"))
    assert "1 not on a job" in page
    owner.post(f"/traces/{session_id}/attach", data={"job_id": str(job_id), "back": "job"})
    assert db_query(app, "SELECT source FROM wo_job_session") == [("manual",)]
    # a consult job (no asset) can hold it too, and the asset page still lists it
    consult_wo = new_wo(owner, bench["cid"])
    consult = db_query(app, "SELECT id FROM wo_job WHERE work_order_id = :w", w=consult_wo)[0][0]
    owner.post(f"/traces/{session_id}/attach", data={"job_id": str(consult)})
    page = owner.get(f"/assets/{bench['widget']}")
    assert page.status_code == 200
    consult_number = db_query(app, "SELECT number FROM work_order WHERE id = :w",
                              w=consult_wo)[0][0]
    assert consult_number in text(page)
    # a job on another machine can't take it
    other_wo = new_wo(owner, bench["cid"], bench["sprocket"])
    other_job = db_query(app, "SELECT id FROM wo_job WHERE work_order_id = :w",
                         w=other_wo)[0][0]
    resp = owner.post(f"/traces/{session_id}/attach", data={"job_id": str(other_job)},
                      follow_redirects=True)
    assert "different machine" in text(resp)


def test_the_trace_page(app, client, bench, signed_in):                  # noqa: F811
    post(client, bench, request_for(bench["tag"]))
    session_id = db_query(app, "SELECT id FROM rail_session")[0][0]
    tech, viewer = signed_in("tech"), signed_in("viewer")
    page = text(tech.get(f"/traces/{session_id}"))
    assert "+5V rail" in page and "4 points" in page and "rail_20260925_021402.csv" in page
    assert "data-chart=" in page and '"kind": "line"' in page and '["0.5", "0.004"]' in page
    assert "vendor/chartjs/chart.umd.min.js" in page and "Take off" in page
    assert 'class="active">Work<' in page                                # nav: under Work
    page = text(viewer.get(f"/traces/{session_id}"))
    assert "Take off" not in page and "Put on this job" not in page
    link_id = db_query(app, "SELECT id FROM wo_job_session")[0][0]
    assert viewer.post(f"/traces/links/{link_id}/detach").status_code == 403
    assert tech.get("/traces/999999").status_code == 404


def test_the_service_report_shows_rail_readings(app, client, bench, monkeypatch):  # noqa: F811
    owner = bench["owner"]
    post(client, bench, request_for(bench["tag"]))
    link_id = db_query(app, "SELECT id FROM wo_job_session")[0][0]
    owner.post(f"/traces/links/{link_id}/phase", data={"phase": "as_left"})
    wo_id = db_query(app, "SELECT id FROM work_order")[0][0]
    seen = capture(monkeypatch, __import__("app.work.docs", fromlist=["x"]))
    owner.get(f"/work/{wo_id}/service-report.pdf")
    html = seen["docs/service_report.html"]
    assert "Rail readings (GATBOX)" in html and "As left" in html
    assert "5.558" in html and "OVER" in html and "(4.75–5.25 V)" in html
