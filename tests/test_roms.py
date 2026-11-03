"""ROM images from GATBOX (contract v1, capability roms): the resumable upload, the
rom_dump and burn items with every refusal, reading back, and what people see and may
download. The examples under contract/v1/examples/ are the spec: the first tests run them
as written; the rest bend them one field at a time."""

import copy
import hashlib
import os
import time
import zlib
from pathlib import Path

import pytest
from test_api_v1_contract import PUBLIC_ID, auth, bench, example, post, request_for  # noqa: F401
from test_work import new_wo, text

from app.gatbox import romfiles
from conftest import db_query

MIB = 1024 * 1024


def img():
    return bytes.fromhex(example("rom-image.json")["hex"])


def upload(client, bench, data, tag=None):  # noqa: F811
    """The whole upload of `data`, in parts: begin, every part, finish. Returns finish's answer."""
    sha = hashlib.sha256(data).hexdigest()
    first = client.post("/api/v1/files", json={"sha256": sha, "size": len(data)}, headers=auth(bench))
    assert first.status_code == 200, first.get_data(as_text=True)
    if first.get_json()["state"] == "stored":
        return first
    for n in range(first.get_json()["parts"]):
        chunk = data[n * romfiles.PART_SIZE:(n + 1) * romfiles.PART_SIZE]
        r = client.put(f"/api/v1/files/{sha}/parts/{n}", data=chunk, headers=auth(bench),
                       content_type="application/octet-stream")
        assert r.status_code == 200, r.get_data(as_text=True)
    return client.post(f"/api/v1/files/{sha}/finish", headers=auth(bench))


def roms_request(bench):  # noqa: F811
    """The example request for this hub's machine. A dump's and a burn's uid hash the machine's
    slug, and this hub makes its own asset tags, so the slug is substituted and the uids are
    recomputed by the contract's rule (the README says so)."""
    payload = request_for(bench["tag"], "rom-ingest-request.json")
    payload["items"] = [sealed(i) for i in payload["items"]]
    return payload


def items(bench):  # noqa: F811
    return {i["kind"]: i for i in roms_request(bench)["items"]}


def example_reply(name, bench):  # noqa: F811
    """An example reply with the uids the substituted request carries."""
    want, uids = example(name), {i["kind"]: i["uid"] for i in roms_request(bench)["items"]}
    for r in want["results"]:
        r["uid"] = uids[r["kind"]]
    return want


def count(app, table):
    return db_query(app, f"SELECT count(*) FROM {table}")[0][0]


def sealed(item, public_id=PUBLIC_ID):
    """The item with its uid recomputed by the contract's rule, after a field was changed."""
    item = copy.deepcopy(item)
    if item["kind"] == "rom_dump":
        key = f"{public_id}|rom_dump|{item['machine']}|{item['label']}|{item['sha1']}"
    else:
        key = f"{public_id}|burn|{item['machine']}|{item['image_sha1']}|{item['created']:.3f}"
    item["uid"] = hashlib.sha256(key.encode()).hexdigest()[:32]
    return item


def one(client, bench, item):  # noqa: F811
    r = post(client, bench, {"contract": "v1", "device": PUBLIC_ID, "items": [item]})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["results"][0]


# --- the examples, as written ---------------------------------------------------------------

def test_the_files_examples_run_as_written(client, bench):                # noqa: F811
    sha = example("rom-image.json")["sha256"]
    begin = client.post("/api/v1/files", json=example("files-begin-request.json"), headers=auth(bench))
    assert begin.status_code == 200 and begin.get_json() == example("files-begin-response.json")
    part = client.put(f"/api/v1/files/{sha}/parts/0", data=img(), headers=auth(bench),
                      content_type="application/octet-stream")
    assert part.get_json() == example("files-part-response.json")
    done = client.post(f"/api/v1/files/{sha}/finish", headers=auth(bench))
    assert done.status_code == 200 and done.get_json() == example("files-finish-response.json")
    again = client.post("/api/v1/files", json=example("files-begin-request.json"), headers=auth(bench))
    assert again.get_json() == example("files-begin-response-stored.json")


def test_posting_the_rom_examples_twice_gives_created_then_duplicate(client, bench, app):   # noqa: F811
    upload(client, bench, img())
    first = post(client, bench, roms_request(bench))
    assert first.status_code == 200, first.get_data(as_text=True)
    assert first.get_json() == example_reply("rom-ingest-response.json", bench)
    assert (count(app, "rom_dump"), count(app, "rom_burn"), count(app, "rom_file")) == (1, 1, 1)
    second = post(client, bench, roms_request(bench))
    assert second.get_json() == example_reply("rom-ingest-response-duplicate.json", bench)
    assert (count(app, "rom_dump"), count(app, "rom_burn"), count(app, "rom_file")) == (1, 1, 1)


def test_the_hub_says_it_speaks_roms(client):
    body = client.get("/api/v1/health").get_json()
    assert "roms" in body["capabilities"] and "jobs" in body["capabilities"]


def test_the_stored_dump_is_exactly_what_was_sent(client, bench, app):    # noqa: F811
    upload(client, bench, img())
    post(client, bench, roms_request(bench))
    row = db_query(app, "SELECT label, part, size_bytes, crc32, sha1, reads, identical, blank, clock, "
                        "romident->>'match', extract(epoch FROM taken_at) FROM rom_dump")[0]
    d = items(bench)["rom_dump"]
    assert row[:9] == (d["label"], d["part"], d["size"], d["crc32"], d["sha1"], 2, True, False, "ntp")
    assert row[9] == "true" and float(row[10]) == d["created"]
    b = db_query(app, "SELECT state, vpp, blank_ok, error, image_sha1 FROM rom_burn")[0]
    assert b == ("verified", "13V", True, None, items(bench)["burn"]["image_sha1"])


def test_reading_back_gives_the_items_as_posted(client, bench):           # noqa: F811
    upload(client, bench, img())
    post(client, bench, roms_request(bench))
    r = client.get(f"/api/v1/machines/{bench['tag']}/dumps", headers=auth(bench))
    assert r.status_code == 200
    got = r.get_json()
    assert got["machine"] == bench["tag"]
    assert got["dumps"] == [items(bench)["rom_dump"]] and got["burns"] == [items(bench)["burn"]]
    shape = example("machine-dumps-response.json")                      # the example's keys and types
    assert set(got) == set(shape) and set(got["dumps"][0]) == set(shape["dumps"][0])
    assert set(got["burns"][0]) == set(shape["burns"][0])
    assert client.get("/api/v1/machines/nobody/dumps", headers=auth(bench)).status_code == 404


# --- the upload ----------------------------------------------------------------------------

def test_an_interrupted_upload_resumes_where_it_stopped(client, bench):   # noqa: F811
    data = os.urandom(9 * MIB)                                           # 8 MiB + 1 MiB
    sha = hashlib.sha256(data).hexdigest()
    b = client.post("/api/v1/files", json={"sha256": sha, "size": len(data)}, headers=auth(bench)).get_json()
    assert (b["state"], b["parts"], b["have"]) == ("open", 2, [])
    client.put(f"/api/v1/files/{sha}/parts/0", data=data[:romfiles.PART_SIZE], headers=auth(bench),
               content_type="application/octet-stream")
    # the connection dropped here: ask again
    b = client.post("/api/v1/files", json={"sha256": sha, "size": len(data)}, headers=auth(bench)).get_json()
    assert (b["state"], b["have"]) == ("open", [0])
    r = client.post(f"/api/v1/files/{sha}/finish", headers=auth(bench))
    assert r.status_code == 409 and "[1]" in r.get_json()["error"]
    client.put(f"/api/v1/files/{sha}/parts/1", data=data[romfiles.PART_SIZE:], headers=auth(bench),
               content_type="application/octet-stream")
    done = client.post(f"/api/v1/files/{sha}/finish", headers=auth(bench))
    assert done.status_code == 200
    assert done.get_json() == {"state": "stored", "sha256": sha, "size": len(data)}


def test_a_part_sent_twice_is_harmless_and_a_stored_file_ignores_parts(client, bench):   # noqa: F811
    data = img()
    sha = hashlib.sha256(data).hexdigest()
    client.post("/api/v1/files", json={"sha256": sha, "size": len(data)}, headers=auth(bench))
    for _ in range(2):
        r = client.put(f"/api/v1/files/{sha}/parts/0", data=data, headers=auth(bench),
                       content_type="application/octet-stream")
        assert r.get_json()["have"] == [0]
    assert client.post(f"/api/v1/files/{sha}/finish", headers=auth(bench)).get_json()["state"] == "stored"
    late = client.put(f"/api/v1/files/{sha}/parts/0", data=b"junk", headers=auth(bench),
                      content_type="application/octet-stream")
    assert late.status_code == 200 and late.get_json()["state"] == "stored"
    assert client.post(f"/api/v1/files/{sha}/finish", headers=auth(bench)).get_json()["state"] == "stored"


def test_a_join_that_is_not_the_announced_hash_is_dropped(client, bench, app):    # noqa: F811
    data = img()
    wrong = hashlib.sha256(b"something else").hexdigest()               # announced, never sent
    client.post("/api/v1/files", json={"sha256": wrong, "size": len(data)}, headers=auth(bench))
    client.put(f"/api/v1/files/{wrong}/parts/0", data=data, headers=auth(bench),
               content_type="application/octet-stream")
    r = client.post(f"/api/v1/files/{wrong}/finish", headers=auth(bench))
    assert r.status_code == 422 and "send the file again" in r.get_json()["error"]
    assert count(app, "rom_file") == 0
    with app.app_context():
        assert not (romfiles.files.root() / ".incoming" / wrong).exists()
        assert not romfiles.path_for(wrong).exists()
    again = client.post("/api/v1/files", json={"sha256": wrong, "size": len(data)}, headers=auth(bench))
    assert again.get_json()["have"] == []                               # begun afresh


def begin(size, sha=lambda s: s):
    """A begin call for the hash under test (`sha` bends it) and this size."""
    return lambda c, b, s: c.post("/api/v1/files", json={"sha256": sha(s), "size": size},
                                  headers=auth(b))


@pytest.mark.parametrize("what,call,code", [
    ("a part of a file never begun",
     lambda c, b, s: c.put(f"/api/v1/files/{s}/parts/0", data=b"x" * 32, headers=auth(b),
                           content_type="application/octet-stream"), 404),
    ("finish of a file never begun",
     lambda c, b, s: c.post(f"/api/v1/files/{s}/finish", headers=auth(b)), 404),
    ("a size of 0", begin(0), 400),
    ("a size that isn't a number", begin("32"), 400),
    ("a size of true", begin(True), 400),
    ("a file over 32 MiB", begin(32 * MIB + 1), 413),
    ("a hash in capitals", begin(32, str.upper), 400),
    ("a hash that is too short", begin(32, lambda s: s[:63]), 400),
    ("no body", lambda c, b, s: c.post("/api/v1/files", headers=auth(b)), 415),
    ("a read of a file no record names",
     lambda c, b, s: c.get(f"/api/v1/files/{s}", headers=auth(b)), 404),
])
def test_files_calls_refuse(client, bench, what, call, code):             # noqa: F811
    assert call(client, bench, hashlib.sha256(b"nothing sent").hexdigest()).status_code == code, what


def test_the_part_rules(client, bench):                                    # noqa: F811
    data = os.urandom(9 * MIB)
    sha = hashlib.sha256(data).hexdigest()
    client.post("/api/v1/files", json={"sha256": sha, "size": len(data)}, headers=auth(bench))
    put = lambda n, chunk, ct="application/octet-stream": client.put(          # noqa: E731
        f"/api/v1/files/{sha}/parts/{n}", data=chunk, headers=auth(bench), content_type=ct)
    assert put(0, data[:romfiles.PART_SIZE - 1]).status_code == 400            # a short first part
    assert put(1, data[romfiles.PART_SIZE:] + b"x").status_code == 400         # a long last part
    assert put(2, b"x").status_code == 400 and put(-1, b"x").status_code == 404     # outside 0..1
    assert put(2, data[romfiles.PART_SIZE:]).status_code == 400     # the last part's length, but no part 2
    assert put(0, data[:romfiles.PART_SIZE], "application/json").status_code == 415
    assert put(0, b"x" * (romfiles.PART_SIZE + 1)).status_code == 413
    assert put(0, data[:romfiles.PART_SIZE]).status_code == 200


def test_stale_half_sent_uploads_are_swept(client, bench, app):           # noqa: F811
    with app.app_context():
        stale = romfiles.files.root() / ".incoming" / ("ab" * 32)
        stale.mkdir(parents=True)
        old = time.time() - 8 * 24 * 3600
        os.utime(stale, (old, old))
    client.post("/api/v1/files", json={"sha256": hashlib.sha256(b"y").hexdigest(), "size": 1},
                headers=auth(bench))
    with app.app_context():
        assert not stale.exists()


def test_a_device_reads_back_only_files_a_record_names(client, bench):    # noqa: F811
    data = img()
    sha = example("rom-image.json")["sha256"]
    upload(client, bench, data)
    assert client.get(f"/api/v1/files/{sha}", headers=auth(bench)).status_code == 404     # not named yet
    post(client, bench, roms_request(bench))
    r = client.get(f"/api/v1/files/{sha}", headers=auth(bench))
    assert r.status_code == 200 and r.get_data() == data and "no-store" in r.headers["Cache-Control"]
    part = client.get(f"/api/v1/files/{sha}", headers={**auth(bench), "Range": "bytes=4-7"})
    assert part.status_code == 206 and part.get_data() == data[4:8]
    assert client.get(f"/api/v1/files/{sha}").status_code == 401


# --- the items: every refusal --------------------------------------------------------------

@pytest.fixture
def uploaded(client, bench):                                               # noqa: F811
    upload(client, bench, img())
    return bench


def refused(client, bench, item, text_in_reason):  # noqa: F811
    r = one(client, bench, item)
    assert r["status"] == "rejected" and text_in_reason in r["reason"], r
    return r


def test_a_dump_is_rejected_for(client, uploaded, app):
    bench = uploaded  # noqa: F811
    d = items(bench)["rom_dump"]
    refused(client, bench, {**d, "machine": "nobody"}, "no machine with slug")
    refused(client, bench, sealed({**d, "label": ""}), "label is required")
    refused(client, bench, sealed({**d, "label": "L" * 41}), "label is longer than 40")
    refused(client, bench, {**d, "uid": "0" * 32}, "uid doesn't follow")
    refused(client, bench, {**d, "uid": "ABC"}, "32 lowercase hex")
    refused(client, bench, sealed({**d, "sha1": "0" * 40}), "sha1 isn't the stored file's")
    refused(client, bench, {**d, "crc32": "00000000"}, "crc32 isn't the stored file's")
    refused(client, bench, {**d, "size": 31}, "size 31 isn't the stored file's")
    refused(client, bench, {**d, "size": 0}, "size must be 1 or more")
    refused(client, bench, {**d, "sha256": "f" * 64}, "send the file first")
    refused(client, bench, {**d, "identical": False}, "identical")
    refused(client, bench, {**d, "reads": 1}, "reads must be 2 or more")
    refused(client, bench, {**d, "blank": "no"}, "blank must be true or false")
    refused(client, bench, {**d, "created": None}, "created is required")
    refused(client, bench, {**d, "clock": {"source": "sundial"}}, "clock.source")
    refused(client, bench, {**d, "romident": {"match": "yes"}}, "romident must be")
    refused(client, bench, {**d, "romident": {"match": True, "matches": [{"set": "x"}]}},
            "romident rom is required")
    refused(client, bench, {**d, "crc32": "ABCDEF01"}, "crc32 must be 8 lowercase hex")
    assert count(app, "rom_dump") == 0


def test_a_dump_without_its_file_is_rejected_and_then_accepted(client, bench, app):   # noqa: F811
    d = items(bench)["rom_dump"]
    refused(client, bench, d, "send the file first")
    upload(client, bench, img())
    assert one(client, bench, d)["status"] == "created"


def test_a_dump_may_say_mame_did_not_run_or_found_nothing(client, uploaded, app):
    d = items(uploaded)["rom_dump"]
    assert one(client, uploaded, {**d, "romident": None})["status"] == "created"
    assert db_query(app, "SELECT romident IS NULL OR romident = 'null'::jsonb FROM rom_dump") == [(True,)]
    other = sealed({**d, "label": "U11", "romident": {"match": False}})
    assert one(client, uploaded, other)["status"] == "created"
    assert db_query(app, "SELECT romident->>'match' FROM rom_dump WHERE label = 'U11'") == [("false",)]


def test_a_burn_is_rejected_for(client, uploaded, app):
    bench = uploaded  # noqa: F811
    b = items(bench)["burn"]
    refused(client, bench, {**b, "machine": "nobody"}, "no machine with slug")
    refused(client, bench, {**b, "uid": "0" * 32}, "uid doesn't follow")
    refused(client, bench, {**b, "created": b["created"] + 0.001}, "uid doesn't follow")
    refused(client, bench, sealed({**b, "image_sha1": "0" * 40}), "sha1 isn't the stored file's")
    refused(client, bench, {**b, "image": "e" * 64}, "send the file first")
    refused(client, bench, {**b, "size": 31}, "isn't the stored file's")
    refused(client, bench, {**b, "state": "done"}, "state must be one of")
    refused(client, bench, {**b, "blank_ok": "yes"}, "blank_ok")
    refused(client, bench, {**b, "error": "x" * 2001}, "longer than 2000")
    refused(client, bench, {**b, "vpp": "x" * 21}, "vpp is longer")
    refused(client, bench, {**b, "created": None}, "created is required")
    assert count(app, "rom_burn") == 0


def test_a_stopped_burn_is_kept_with_its_reason(client, uploaded, app):
    b = items(uploaded)["burn"]
    stopped = sealed({**b, "state": "stopped", "created": b["created"] + 60, "blank_ok": None,
                      "vpp": None, "error": "blank check failed: the chip isn't blank"})
    assert one(client, uploaded, stopped)["status"] == "created"
    assert db_query(app, "SELECT state, error, blank_ok, vpp FROM rom_burn") == [
        ("stopped", "blank check failed: the chip isn't blank", None, None)]


def test_a_uid_is_for_the_device_that_made_it(client, uploaded, app):
    from app.extensions import db
    from app.models import Device
    with app.app_context():
        mine = db.session.query(Device).one()
        other = Device(name="other-bench", public_id="b1b2b3b4b5b6", jobs=True)
        token = other.issue_token()
        db.session.add(other)
        db.session.commit()
        assert mine.public_id == PUBLIC_ID
    post(client, uploaded, roms_request(uploaded))
    d = items(uploaded)["rom_dump"]
    stolen = client.post("/api/v1/ingest", json={"contract": "v1", "device": "b1b2b3b4b5b6",
                                                 "items": [sealed(d, "b1b2b3b4b5b6")]},
                         headers={"Authorization": f"Bearer {token}"})
    # another device, its own uid (the rule hashes the public id): its own record, same file
    assert stolen.get_json()["results"][0]["status"] == "created"
    assert count(app, "rom_dump") == 2 and count(app, "rom_file") == 1
    steal = client.post("/api/v1/ingest", json={"contract": "v1", "device": "b1b2b3b4b5b6", "items": [d]},
                        headers={"Authorization": f"Bearer {token}"})
    refusal = steal.get_json()["results"][0]                             # my uid, someone else's token
    assert refusal["status"] == "rejected" and "uid doesn't follow" in refusal["reason"]


def test_a_rejected_item_leaves_the_rest_of_the_request_alone(client, uploaded, app):
    good, bad = items(uploaded)["rom_dump"], items(uploaded)["burn"]
    r = post(client, uploaded, {"contract": "v1", "device": PUBLIC_ID,
                                "items": [{**bad, "state": "x"}, good]})
    assert [x["status"] for x in r.get_json()["results"]] == ["rejected", "created"]
    assert (count(app, "rom_dump"), count(app, "rom_burn")) == (1, 0)


# --- the order link ------------------------------------------------------------------------

def open_job(bench, app, machine="widget"):  # noqa: F811
    wo = new_wo(bench["owner"], bench["cid"], bench[machine])
    return db_query(app, "SELECT id FROM wo_job WHERE work_order_id = :w", w=wo)[0][0]


def test_a_dump_for_an_order_shows_on_the_job_and_reads_back_with_it(client, uploaded, app):
    bench = uploaded  # noqa: F811
    job = open_job(bench, app)
    d = items(bench)["rom_dump"]
    assert one(client, bench, {**d, "order": job})["status"] == "created"
    assert db_query(app, "SELECT wo_job_id FROM rom_dump") == [(job,)]
    assert one(client, bench, {**items(bench)["burn"], "order": job})["status"] == "created"
    page = text(bench["owner"].get(f"/work/jobs/{job}"))
    assert "ROM dumps and burns" in page and "U10" in page and "burn verified" in page
    back = client.get(f"/api/v1/machines/{bench['tag']}/dumps", headers=auth(bench)).get_json()
    assert back["dumps"][0]["order"] == job and back["burns"][0]["order"] == job


def test_an_order_that_is_not_the_machines_is_refused(client, uploaded, app):
    bench = uploaded  # noqa: F811
    other_job = open_job(bench, app, "sprocket")
    refused(client, bench, {**items(bench)["rom_dump"], "order": other_job}, "different machine")
    refused(client, bench, {**items(bench)["burn"], "order": other_job}, "different machine")
    refused(client, bench, {**items(bench)["rom_dump"], "order": 999999}, "no order with id")
    refused(client, bench, {**items(bench)["rom_dump"], "order": "7"}, "integer id")
    refused(client, bench, {**items(bench)["rom_dump"], "order": True}, "integer id")
    assert count(app, "rom_dump") == 0


def test_a_device_without_the_jobs_switch_may_not_name_an_order(client, uploaded, app):
    bench = uploaded  # noqa: F811
    job = open_job(bench, app)
    from sqlalchemy import text as sql

    from app.extensions import db
    with app.app_context():
        db.session.execute(sql("UPDATE device SET jobs = false"))
        db.session.commit()
    refused(client, bench, {**items(bench)["rom_dump"], "order": job}, "jobs are switched off")
    assert one(client, bench, items(bench)["rom_dump"])["status"] == "created"       # no order: fine
    back = client.get(f"/api/v1/machines/{bench['tag']}/dumps", headers=auth(bench)).get_json()
    assert "order" not in back["dumps"][0]


# --- what people see, and may download -----------------------------------------------------

def test_the_asset_page_lists_them_and_the_image_downloads_behind_the_login(client, uploaded, app, signed_in):
    bench = uploaded  # noqa: F811
    post(client, bench, roms_request(bench))
    page = text(bench["owner"].get(f"/assets/{bench['widget']}"))
    assert "ROM dumps and burns" in page and "U10" in page and "MAME match" in page
    assert "widgetw/ww-u10.bin" in page and "burn verified" in page and "13V" in page
    dump_id = db_query(app, "SELECT id FROM rom_dump")[0][0]
    burn_id = db_query(app, "SELECT id FROM rom_burn")[0][0]
    assert f"/roms/dumps/{dump_id}/image" in page and f"/roms/burns/{burn_id}/image" in page
    for who in ("owner", "tech", "viewer"):
        r = signed_in(who).get(f"/roms/dumps/{dump_id}/image")
        assert r.status_code == 200 and r.get_data() == img(), who
        assert "attachment" in r.headers["Content-Disposition"] and ".bin" in r.headers["Content-Disposition"]
        assert "no-store" in r.headers["Cache-Control"] and r.headers["X-Content-Type-Options"] == "nosniff"
    assert signed_in("viewer").get(f"/roms/burns/{burn_id}/image").get_data() == img()
    assert signed_in("viewer").get("/roms/dumps/999999/image").status_code == 404


def test_nobody_without_a_login_gets_an_image(client, uploaded, app):
    post(client, uploaded, roms_request(uploaded))
    dump_id = db_query(app, "SELECT id FROM rom_dump")[0][0]
    for headers in ({}, auth(uploaded)):                      # a device's token is not a login
        r = client.get(f"/roms/dumps/{dump_id}/image", headers=headers)
        assert r.status_code in (302, 401) and img() not in r.get_data()


def test_the_images_are_outside_anything_served(app, client, uploaded):
    sha = example("rom-image.json")["sha256"]
    with app.app_context():
        path = romfiles.path_for(sha)
        static = Path(app.static_folder).resolve()
        assert path.is_file() and static not in path.resolve().parents
        assert path.read_bytes() == img() and path.suffix == ".rom"
    assert client.get(f"/static/{sha}.rom").status_code == 404


def test_the_example_image_is_what_its_hashes_say():
    i = example("rom-image.json")
    b = bytes.fromhex(i["hex"])
    assert hashlib.sha1(b).hexdigest() == i["sha1"] and zlib.crc32(b) == int(i["crc32"], 16)
