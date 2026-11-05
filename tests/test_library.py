"""The game library, read-only (contract/library-v1/): the pull job against a stub archive that speaks the
vendored contract, the copy it makes in Postgres, the asset page's game section, a manual's PDF, the
settings and the command. The stub (tests/library_stub.py) starts from the contract's own examples and
the first test asks it the contract's own requests, so the two ends can't drift apart unseen.
Every game, asset and byte is invented (they are the contract's)."""

import copy
import hashlib
import importlib.util
import json
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from library_stub import MANUAL, MANUAL_SHA, TOKEN, Server, canonical_hash, example, model_of
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
LEAK = "someone@example.test"


@pytest.fixture
def stub():
    server = Server()
    yield server
    server.stop()


@pytest.fixture
def shop(app, stub, tmp_path, monkeypatch):
    """The application set up to read the stub: one asset names widget-wars, one names nothing."""
    from app.extensions import db
    from app.models import Asset, Customer

    for key, value in {
        "LIBRARY_URL": stub.url,
        "LIBRARY_TOKEN": TOKEN,
        "LIBRARY_ACCESS_ID": "",
        "LIBRARY_ACCESS_SECRET": "",
        "LIBRARY_WEB_URL": "https://archive.example.test",
        "LIBRARY_MAX_FILE": 100 * 1024 * 1024,
        "SHOP_FILES_DIR": str(tmp_path / "files"),
    }.items():
        monkeypatch.setitem(app.config, key, value)
    with app.app_context():
        owner = db.session.scalar(select(Customer.id).where(Customer.is_shop))
        a = Asset(
            tag="s-1001-widget-wars",
            customer_id=owner,
            kind="machine",
            name="Widget Wars",
            status="in_service",
            model_key="widget-wars",
        )
        b = Asset(
            tag="s-1002-sprocket", customer_id=owner, kind="machine", name="Sprocket", status="in_service"
        )
        db.session.add_all([a, b])
        db.session.commit()
        ids = (a.id, b.id)
    return {"stub": stub, "archive": stub.archive, "app": app, "asset": ids[0], "other": ids[1]}


def pull(shop):
    from app.library import pull as lp

    with shop["app"].app_context():
        return lp.run(log=lambda *_: None)


def held(shop, kind=None):
    from app.models import LibraryRecord

    with shop["app"].app_context():
        from app.extensions import db

        q = select(LibraryRecord)
        if kind:
            q = q.where(LibraryRecord.kind == kind)
        return {
            (r.kind, r.rid): {"rev": r.rev, "data": r.data, "model_key": r.model_key}
            for r in db.session.scalars(q)
        }


def state(shop):
    from app.extensions import db
    from app.models import LibraryState

    with shop["app"].app_context():
        s = db.session.get(LibraryState, 1)
        return (
            None
            if s is None
            else {
                "cursor": s.cursor,
                "keys_synced": list(s.keys_synced),
                "error": s.error,
                "refused": s.refused,
                "last_ok": s.last_ok,
                "last_try": s.last_try,
                "records": s.records,
            }
        )


def set_key(shop, asset_id, key):
    from app.extensions import db
    from app.models import Asset

    with shop["app"].app_context():
        db.session.get(Asset, asset_id).model_key = key
        db.session.commit()


def part_path(shop, sha):
    from app.library import files

    with shop["app"].app_context():
        return files.part_path(sha)


def file_path(shop, sha):
    from app.library import files

    with shop["app"].app_context():
        return files.path_for(sha)


def feed_asks(archive):
    out = []
    for path, _ in archive.requests:
        u = urlsplit(path)
        if u.path == "/api/v1/library/changes":
            q = parse_qs(u.query)
            out.append(
                {
                    "since": int(q["since"][0]),
                    "limit": int(q["limit"][0]),
                    "models": q["models"][0].split(","),
                }
            )
    return out


def file_asks(archive):
    return [
        (p, {k.lower(): v for k, v in h.items()})
        for p, h in archive.requests
        if p.startswith("/api/v1/files/")
    ]


def get(stub, path, headers=None):
    req = urllib.request.Request(
        stub.url + path, headers={"Authorization": f"Bearer {TOKEN}", **(headers or {})}
    )
    with urllib.request.urlopen(req) as r:
        return r.status, r.read(), dict(r.headers)


def text(resp):
    return resp.get_data(as_text=True)


# --- the stub is the contract's examples ------------------------------------------------------------------


def test_the_stub_answers_the_contracts_own_requests_with_the_contracts_own_examples(stub):
    asked = {
        "changes-response.json": "/api/v1/library/changes?since=0&limit=4&models=widget-wars",
        "changes-response-filtered.json": "/api/v1/library/changes?since=8&limit=1&models=widget-wars",
        "changes-response-last.json": "/api/v1/library/changes?since=8&limit=4&models=widget-wars",
        "changes-response-empty.json": "/api/v1/library/changes?since=11&limit=4&models=widget-wars",
        "changes-response-all.json": (
            "/api/v1/library/changes?since=0&limit=500&models=widget-wars,pin-comet-run"
        ),
    }
    for name, path in asked.items():
        status, body, _ = get(stub, path)
        assert (status, json.loads(body)) == (200, example(name)), name
    status, body, headers = get(stub, f"/api/v1/files/{MANUAL_SHA}", {"Range": "bytes=0-127"})
    assert (status, body) == (206, MANUAL[:128]) and headers["Content-Range"] == "bytes 0-127/590"


# --- what the job asks for and sends ----------------------------------------------------------------------


def test_it_asks_for_its_own_assets_games_only_and_sends_nothing_but_the_token(shop):
    out = pull(shop)
    assert out["ok"] and not out["refused"] and out["error"] is None
    asked = feed_asks(shop["archive"])
    assert asked and all(a["models"] == ["widget-wars"] and a["limit"] <= 500 for a in asked)
    assert set(held(shop)) == {
        ("game", "widget-wars"),
        ("document", "widget-wars/service-manual"),
        ("mame", "widget-wars"),
        ("rom_list", "widget-wars"),
        ("rail_limit", "c0ffee00c0ffee00c0ffee00c0ffee00"),
        ("fact", "f1f1f1f1f1f1f1f1f1f1f1f1f1f1f1f1"),
        ("rom_image", "b" * 64),
    }
    allowed = {"host", "user-agent", "authorization", "accept-encoding", "connection", "range"}
    for _, headers in shop["archive"].requests:
        lower = {k.lower(): v for k, v in headers.items()}
        assert lower["authorization"] == f"Bearer {TOKEN}" and set(lower) <= allowed, set(lower) - allowed


def test_the_access_service_token_is_sent_when_it_is_set_and_not_otherwise(shop, monkeypatch):
    pull(shop)
    assert all("cf-access-client-id" not in {k.lower() for k in h} for _, h in shop["archive"].requests)
    shop["archive"].requests.clear()
    monkeypatch.setitem(shop["app"].config, "LIBRARY_ACCESS_ID", "abc.access")
    monkeypatch.setitem(shop["app"].config, "LIBRARY_ACCESS_SECRET", "s3cret")
    pull(shop)
    assert shop["archive"].requests
    for _, h in shop["archive"].requests:
        lower = {k.lower(): v for k, v in h.items()}
        assert (lower["cf-access-client-id"], lower["cf-access-client-secret"]) == ("abc.access", "s3cret")


def test_an_unconfigured_application_does_nothing(shop, monkeypatch):
    monkeypatch.setitem(shop["app"].config, "LIBRARY_URL", "")
    out = pull(shop)
    assert out["configured"] is False and out["ok"] is False
    assert shop["archive"].requests == [] and held(shop) == {} and state(shop) is None


def test_keys_that_arent_keys_are_never_asked_for(shop):
    from app.extensions import db
    from app.models import Asset, Customer

    with shop["app"].app_context():
        owner = db.session.scalar(select(Customer.id).where(Customer.is_shop))
        # The column's own CHECK keeps a bad slug out; "unassigned" is a valid slug that is never a game.
        db.session.add(
            Asset(
                tag="s-1003-x",
                customer_id=owner,
                kind="machine",
                name="x",
                status="in_service",
                model_key="unassigned",
            )
        )
        db.session.commit()
    pull(shop)
    assert {k for a in feed_asks(shop["archive"]) for k in a["models"]} == {"widget-wars"}


def test_a_shop_with_no_linked_asset_asks_for_nothing(shop):
    set_key(shop, shop["asset"], None)
    out = pull(shop)
    assert out["ok"] and shop["archive"].requests == [] and held(shop) == {}


# --- records: replaced, removed, followed -----------------------------------------------------------------


def test_a_tombstone_removes_the_copy_and_a_changed_record_replaces_it(shop):
    from app.extensions import db
    from app.models import LibraryRecord

    with shop["app"].app_context():
        db.session.add(
            LibraryRecord(
                kind="fact",
                rid="e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1",
                model_key="widget-wars",
                rev=1,
                hash="x",
                data={"what": "old"},
            )
        )
        db.session.commit()
    pull(shop)
    assert ("fact", "e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1") not in held(shop)
    game = copy.deepcopy(example("changes-response.json")["records"][0])
    game["rev"], game["data"]["title"] = 2, "Widget Wars II"
    shop["archive"].put(game)
    assert pull(shop)["changed"] == 1
    row = held(shop, "game")[("game", "widget-wars")]
    assert (row["rev"], row["data"]["title"]) == (2, "Widget Wars II")


def test_the_second_run_reads_from_where_the_first_stopped(shop):
    pull(shop)
    first = len(feed_asks(shop["archive"]))
    pull(shop)
    assert [a["since"] for a in feed_asks(shop["archive"])[first:]] == [shop["archive"].head]


def test_a_key_an_asset_gains_is_read_from_the_start_once_and_the_old_keys_from_the_cursor(shop):
    pull(shop)
    shop["archive"].requests.clear()
    set_key(shop, shop["other"], "pin-comet-run")
    pull(shop)
    asked = feed_asks(shop["archive"])
    assert asked[0] == {"since": 0, "limit": 500, "models": ["pin-comet-run"]}
    assert all(a["models"] == ["widget-wars"] and a["since"] == shop["archive"].head for a in asked[1:])
    assert ("game", "pin-comet-run") in held(shop) and ("fact", "abababababababababababababababab") in held(
        shop
    )
    shop["archive"].requests.clear()
    pull(shop)
    assert [a["since"] for a in feed_asks(shop["archive"])] == [shop["archive"].head]


def test_a_retired_game_is_followed_to_its_survivor(shop):
    from app.library import service

    old = copy.deepcopy(example("changes-response.json")["records"][0])
    old["id"], old["data"]["replaced_by"] = "widget-wars-old", "widget-wars"
    shop["archive"].put(old)
    set_key(shop, shop["asset"], "widget-wars-old")
    pull(shop)
    assert ("game", "widget-wars-old") in held(shop) and ("game", "widget-wars") in held(shop)
    with shop["app"].app_context():
        assert service.game("widget-wars-old")["key"] == "widget-wars"
    assert {k for a in feed_asks(shop["archive"]) for k in a["models"]} == {"widget-wars-old", "widget-wars"}


def test_records_of_a_kind_it_doesnt_know_or_a_malformed_one_are_left_out(shop):
    from app.library import pull as lp

    good = {
        "kind": "fact",
        "id": "f" * 32,
        "rev": 1,
        "hash": "h",
        "data": {"model_key": "widget-wars", "what": "x"},
    }
    with shop["app"].app_context():
        n = lp.apply_records(
            [
                good,
                {
                    "kind": "cartridge",
                    "id": "z",
                    "rev": 1,
                    "hash": "h",
                    "data": {},
                },  # a kind from a later edition
                {
                    "kind": "fact",
                    "id": "g" * 32,
                    "rev": "one",
                    "hash": "h",
                    "data": {},
                },  # revision isn't a number
                {"kind": "fact", "id": "i" * 32, "rev": 0, "hash": "h", "data": {}},  # revisions start at 1
                {
                    "kind": "fact",
                    "id": "h" * 32,
                    "rev": 1,
                    "hash": "h",
                    "data": "text",
                },  # data isn't an object
                "garbage",
            ]
        )
    assert n == 1 and set(held(shop)) == {("fact", "f" * 32)}


# --- the archive is restored from an older copy -----------------------------------------------------------


def test_a_restored_archive_is_read_again_from_the_start_and_the_copy_trimmed_to_it(shop):
    pull(shop)
    assert len(held(shop)) == 7 and state(shop)["cursor"] == shop["archive"].head == 11
    arch = shop["archive"]
    arch.rows = [(s, r) for s, r in arch.rows if s <= 4]  # the older copy: game, document, MAME set, ROM list
    arch.head = 4
    older = arch.rows[0][1]
    older["data"]["title"] = "Widget Wars (older copy)"
    older["hash"] = canonical_hash(older["data"])
    out = pull(shop)
    assert out["restored"] and out["ok"]
    assert set(held(shop)) == {
        ("game", "widget-wars"),
        ("document", "widget-wars/service-manual"),
        ("mame", "widget-wars"),
        ("rom_list", "widget-wars"),
    }
    assert held(shop, "game")[("game", "widget-wars")]["data"]["title"] == "Widget Wars (older copy)"
    assert state(shop)["cursor"] == 4


def test_a_restore_that_dies_half_way_trims_nothing_and_is_detected_again(shop):
    pull(shop)
    arch = shop["archive"]
    arch.rows = [(s, r) for s, r in arch.rows if s <= 4]
    arch.head = 4
    arch.refuse = True
    out = pull(shop)
    assert out["refused"] and len(held(shop)) == 7 and state(shop)["cursor"] == 11
    arch.refuse = False
    out = pull(shop)
    assert out["restored"] and len(held(shop)) == 4


# --- manuals ----------------------------------------------------------------------------------------------


def test_a_manual_is_fetched_in_ranges_checked_and_kept_by_its_hash(shop):
    out = pull(shop)
    assert out["files"] == 1 and out["files_failed"] == []
    assert file_path(shop, MANUAL_SHA).read_bytes() == MANUAL and not part_path(shop, MANUAL_SHA).exists()
    asks = file_asks(shop["archive"])
    assert [p for p, _ in asks] == [f"/api/v1/files/{MANUAL_SHA}"] and asks[0][1]["range"].startswith(
        "bytes=0-"
    )
    shop["archive"].requests.clear()
    pull(shop)
    assert not file_asks(shop["archive"])  # held: not fetched again


def test_a_download_cut_off_part_way_resumes_from_the_byte_it_reached(shop):
    shop["archive"].cut[MANUAL_SHA] = 100
    out = pull(shop)
    assert out["ok"] is False and "cut off" in out["error"] and out["files"] == 0
    assert part_path(shop, MANUAL_SHA).stat().st_size == 100 and not file_path(shop, MANUAL_SHA).exists()
    shop["archive"].requests.clear()
    out = pull(shop)
    assert out["ok"] and out["files"] == 1 and file_path(shop, MANUAL_SHA).read_bytes() == MANUAL
    assert file_asks(shop["archive"])[0][1]["range"].startswith("bytes=100-")


def test_a_part_that_already_holds_the_whole_file_is_checked_and_joined(shop):
    part_path(shop, MANUAL_SHA).write_bytes(MANUAL)
    out = pull(shop)
    assert out["files"] == 1 and file_path(shop, MANUAL_SHA).read_bytes() == MANUAL


def test_bytes_that_dont_hash_to_their_name_are_dropped_and_fetched_again_next_run(shop):
    shop["archive"].lie[MANUAL_SHA] = b"%PDF-1.4\n" + b"x" * 100
    out = pull(shop)
    assert out["files"] == 0 and len(out["files_failed"]) == 1 and "hash" in out["files_failed"][0]["why"]
    assert not file_path(shop, MANUAL_SHA).exists() and not part_path(shop, MANUAL_SHA).exists()
    shop["archive"].lie.clear()
    assert pull(shop)["files"] == 1


def test_a_file_that_isnt_a_pdf_is_dropped_even_when_its_hash_is_right(shop):
    body = b"this is a text file, not a PDF"
    sha = hashlib.sha256(body).hexdigest()
    shop["archive"].files[sha] = body
    doc = copy.deepcopy(example("changes-response.json")["records"][1])
    doc["id"], doc["data"]["doc_id"], doc["data"]["sha256"] = "widget-wars/notes", "notes", sha
    shop["archive"].put(doc)
    out = pull(shop)
    assert [f["sha256"] for f in out["files_failed"] if "PDF" in f["why"]] == [sha]
    assert not file_path(shop, sha).exists() and file_path(shop, MANUAL_SHA).exists()


def test_a_manual_the_archive_doesnt_hold_yet_is_waited_for_not_an_error(shop):
    doc = copy.deepcopy(example("changes-response.json")["records"][1])
    doc["id"], doc["data"]["doc_id"], doc["data"]["sha256"] = "widget-wars/later", "later", "ab" * 32
    shop["archive"].put(doc)
    out = pull(shop)
    assert out["ok"] and out["files_absent"] == 1 and out["files"] == 1


def test_a_manual_over_the_limit_is_not_kept(shop, monkeypatch):
    monkeypatch.setitem(shop["app"].config, "LIBRARY_MAX_FILE", 100)
    out = pull(shop)
    assert out["files"] == 0 and "over this application's limit" in out["files_failed"][0]["why"]
    assert not file_path(shop, MANUAL_SHA).exists() and not part_path(shop, MANUAL_SHA).exists()


def test_the_time_budget_leaves_the_rest_for_the_next_run(shop, monkeypatch):
    from app.library import pull as lp

    monkeypatch.setattr(lp, "BUDGET", -1.0)
    out = pull(shop)
    assert out["ok"] and out["files"] == 0 and out["files_later"] == 1 and out["files_waiting"] == 1
    monkeypatch.setattr(lp, "BUDGET", 240.0)
    assert pull(shop)["files"] == 1


def test_clearing_a_game_removes_its_records_and_the_manual_only_it_held(shop):
    from app.extensions import db
    from app.library import service

    pull(shop)
    with shop["app"].app_context():
        n = service.clear("widget-wars")
        db.session.commit()
    assert n == 7 and held(shop) == {} and not file_path(shop, MANUAL_SHA).exists()
    assert "widget-wars" not in state(shop)["keys_synced"]
    pull(shop)
    assert len(held(shop)) == 7 and file_path(shop, MANUAL_SHA).exists()


# --- out of reach, refused --------------------------------------------------------------------------------


def test_an_archive_out_of_reach_keeps_the_copy_and_says_so(shop, monkeypatch):
    pull(shop)
    before = {k: v["rev"] for k, v in held(shop).items()}
    monkeypatch.setitem(shop["app"].config, "LIBRARY_URL", "http://127.0.0.1:1")  # nothing listens there
    out = pull(shop)
    assert out["ok"] is False and out["refused"] is False and out["error"]
    assert {k: v["rev"] for k, v in held(shop).items()} == before
    st = state(shop)
    assert st["error"] and not st["refused"] and st["last_try"] is not None and st["last_ok"] is not None


def test_a_token_the_archive_refuses_is_the_one_answer_for_a_person(shop):
    shop["archive"].token = "gbx_a1b2c3d4e5f6.someone-elses"
    out = pull(shop)
    assert out["refused"] and not out["ok"] and "refused the token" in out["error"]
    assert state(shop)["refused"] is True and held(shop) == {}
    shop["archive"].token = TOKEN
    out = pull(shop)
    assert out["ok"] and state(shop)["refused"] is False and state(shop)["error"] is None


def test_a_feed_that_is_not_the_contracts_shape_is_unreachable_not_a_crash():
    from app.library import pull as lp

    class Odd(lp.Client):
        def get(self, path, extra=None):
            return 200, b'{"nothing": true}', {}

    with pytest.raises(lp.Unreachable):
        Odd("http://x", TOKEN).changes(0, ["widget-wars"])


# --- the command ------------------------------------------------------------------------------------------


def load_script():
    spec = importlib.util.spec_from_file_location("pull_library", ROOT / "scripts" / "pull_library.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_command_prints_one_line_and_exits_0(shop, monkeypatch):
    mod, lines = load_script(), []
    assert mod.main(shop["app"], [], out=lines.append) == 0
    assert (
        len(lines) == 1
        and lines[0].startswith("library: ")
        and "ok=True" in lines[0]
        and "files=1" in lines[0]
    )
    lines.clear()
    assert mod.main(shop["app"], ["--status"], out=lines.append) == 0
    assert "7 records held" in lines[0]
    shop["archive"].refuse = True
    assert mod.main(shop["app"], [], out=lambda *_: None) == 1
    monkeypatch.setitem(shop["app"].config, "LIBRARY_URL", "")
    lines.clear()
    assert mod.main(shop["app"], [], out=lines.append) == 0 and "no archive configured" in lines[0]


def test_the_command_clears_a_game_only_when_no_asset_names_it(shop):
    mod, lines = load_script(), []
    pull(shop)
    assert mod.main(shop["app"], ["--clear", "widget-wars"], out=lines.append) == 2
    assert "still names" in lines[-1] and len(held(shop)) == 7
    set_key(shop, shop["asset"], None)
    assert mod.main(shop["app"], ["--clear", "widget-wars"], out=lines.append) == 0
    assert "7 records removed" in lines[-1] and held(shop) == {}
    assert mod.main(shop["app"], ["--clear", "widget-wars"], out=lines.append) == 2


def add_asset(shop, tag, name, key):
    from app.extensions import db
    from app.models import Asset, Customer

    with shop["app"].app_context():
        owner = db.session.scalar(select(Customer.id).where(Customer.is_shop))
        db.session.add(
            Asset(tag=tag, customer_id=owner, kind="machine", name=name, status="in_service", model_key=key)
        )
        db.session.commit()


def test_status_names_the_keys_that_are_held_missing_unread_or_not_game_keys(shop):
    mod, lines = load_script(), []
    set_key(shop, shop["other"], "sprocket-blaster")  # the archive has no game with this key
    add_asset(shop, "s-1003-odd-board", "Odd Board", "unassigned")  # a key no pull ever asks for
    pull(shop)
    add_asset(shop, "s-1004-late-board", "Late Board", "later-game")  # named after the pull
    assert mod.main(shop["app"], ["--status"], out=lines.append) == 0
    assert "7 records held" in lines[0]  # the first line is what it always was
    text = "\n".join(lines[1:])
    assert "keys: 4 named by assets: 1 held, 1 not at the archive, 1 not read yet, 1 not game keys" in text
    assert "NOT AT THE ARCHIVE" in text and "sprocket-blaster  <-  Sprocket (s-1002-sprocket)" in text
    assert "not read yet (the next pull asks for these)" in text
    assert "later-game  <-  Late Board (s-1004-late-board)" in text
    assert "not game keys" in text and "unassigned  <-  Odd Board (s-1003-odd-board)" in text
    assert "widget-wars" not in text  # a held key is counted, not listed


def test_status_stops_naming_a_key_once_the_game_is_added_at_the_archive(shop):
    mod, lines = load_script(), []
    set_key(shop, shop["other"], "sprocket-blaster")
    pull(shop)
    assert mod.main(shop["app"], ["--status"], out=lines.append) == 0
    assert "sprocket-blaster  <-  Sprocket (s-1002-sprocket)" in "\n".join(lines)
    game = copy.deepcopy(example("changes-response-all.json")["records"][0])
    assert game["kind"] == "game"
    game["id"], game["data"]["title"] = "sprocket-blaster", "Sprocket Blaster"
    shop["archive"].put(game)
    pull(shop)
    lines.clear()
    assert mod.main(shop["app"], ["--status"], out=lines.append) == 0
    text = "\n".join(lines)
    assert "keys: 2 named by assets: 2 held, 0 not at the archive, 0 not read yet" in text
    assert "NOT AT THE ARCHIVE" not in text and "sprocket-blaster" not in text


def test_status_with_no_asset_naming_a_game_says_so(shop):
    mod, lines = load_script(), []
    set_key(shop, shop["asset"], None)
    assert mod.main(shop["app"], ["--status"], out=lines.append) == 0
    assert "no asset names a game yet" in lines[-1]


# --- the settings -----------------------------------------------------------------------------------------


def settings(**env):
    from app.config import library_settings

    return library_settings(env)


def test_settings_default_to_no_library():
    s = settings()
    assert (s["LIBRARY_URL"], s["LIBRARY_TOKEN"], s["LIBRARY_WEB_URL"]) == ("", "", "")


def test_settings_accept_an_address_and_a_reader_token():
    s = settings(
        LIBRARY_ARCHIVE_URL="http://archive.example.test:8081/",
        LIBRARY_ARCHIVE_TOKEN=TOKEN,
        LIBRARY_ARCHIVE_WEB_URL="https://archive.example.test/",
    )
    assert (s["LIBRARY_URL"], s["LIBRARY_TOKEN"], s["LIBRARY_WEB_URL"]) == (
        "http://archive.example.test:8081",
        TOKEN,
        "https://archive.example.test",
    )


@pytest.mark.parametrize("name", ["LIBRARY_ARCHIVE_URL", "LIBRARY_ARCHIVE_WEB_URL"])
@pytest.mark.parametrize(
    "url",
    [
        "archive.example.org",
        "ftp://archive.example.org",
        "https://archive.example.org/api",
        "https://a.example.org/?x=1",
    ],
)
def test_settings_refuse_a_url_that_isnt_just_an_address(name, url):
    from app.config import ConfigError

    with pytest.raises(ConfigError):
        settings(
            **{
                name: url,
                "LIBRARY_ARCHIVE_URL": url if name == "LIBRARY_ARCHIVE_URL" else "https://a.example.org",
                "LIBRARY_ARCHIVE_TOKEN": TOKEN,
            }
        )


def test_settings_refuse_a_token_that_isnt_a_reader_token_and_half_an_access_pair():
    from app.config import ConfigError

    with pytest.raises(ConfigError):
        settings(LIBRARY_ARCHIVE_URL="https://archive.example.org", LIBRARY_ARCHIVE_TOKEN="hunter2")
    with pytest.raises(ConfigError):
        settings(
            LIBRARY_ARCHIVE_URL="https://archive.example.org",
            LIBRARY_ARCHIVE_TOKEN=TOKEN,
            LIBRARY_ACCESS_CLIENT_ID="only-the-id",
        )
    with pytest.raises(ConfigError):
        settings(LIBRARY_MAX_FILE_MB="lots")


def test_half_a_setting_is_no_setting():
    s = settings(LIBRARY_ARCHIVE_URL="https://archive.example.org")
    assert (s["LIBRARY_URL"], s["LIBRARY_TOKEN"]) == ("", "")
    s = settings(LIBRARY_ARCHIVE_TOKEN=TOKEN)
    assert s["LIBRARY_URL"] == ""


# --- the asset page ---------------------------------------------------------------------------------------


def test_without_an_archive_the_asset_page_has_no_game_section(app, signed_in):
    from app.extensions import db
    from app.models import Asset, Customer

    with app.app_context():
        owner = db.session.scalar(select(Customer.id).where(Customer.is_shop))
        a = Asset(
            tag="s-1001-widget-wars",
            customer_id=owner,
            kind="machine",
            name="Widget Wars",
            status="in_service",
            model_key="widget-wars",
        )
        db.session.add(a)
        db.session.commit()
        aid = a.id
    client = signed_in("tech")
    page = text(client.get(f"/assets/{aid}"))
    assert "Manual and limits" not in page and client.get(f"/library/file/{MANUAL_SHA}").status_code == 404


def test_the_asset_page_shows_the_games_manual_limits_and_facts_to_every_role(shop, signed_in):
    pull(shop)
    for role in ("viewer", "tech"):
        page = text(signed_in(role).get(f"/assets/{shop['asset']}"))
        assert (
            "Manual and limits" in page and "Service manual" in page and f"/library/file/{MANUAL_SHA}" in page
        )
        assert "+5V" in page and "4.75 V" in page and "5.25 V" in page and "confirmed" in page
        assert "#page=1" in page and "Fuse F1" in page and "3 A slow-blow" in page


def test_the_edit_link_is_for_owners_and_techs_and_needs_the_archives_address(shop, signed_in, monkeypatch):
    pull(shop)
    assert 'href="https://archive.example.test/games/widget-wars"' in text(
        signed_in("tech").get(f"/assets/{shop['asset']}")
    )
    assert "Edit it in the library" not in text(signed_in("viewer").get(f"/assets/{shop['asset']}"))
    monkeypatch.setitem(shop["app"].config, "LIBRARY_WEB_URL", "")
    assert "Edit it in the library" not in text(signed_in("tech").get(f"/assets/{shop['asset']}"))


def test_a_limit_nobody_has_confirmed_says_so(shop, signed_in):
    limit = copy.deepcopy(example("changes-response-all.json")["records"][4])
    limit["rev"], limit["data"]["confirmed"] = 2, None
    shop["archive"].put(limit)
    pull(shop)
    page = text(signed_in("tech").get(f"/assets/{shop['asset']}"))
    assert (
        "unconfirmed" in page
        and "nobody has checked it against the page" in page
        and ">confirmed<" not in page
    )


def test_who_wrote_or_confirmed_a_record_is_never_shown(shop, signed_in):
    for i in (0, 1, 4, 5):
        rec = copy.deepcopy(example("changes-response-all.json")["records"][i])
        rec["rev"], rec["by"] = 2, LEAK
        if rec["kind"] == "rail_limit":
            rec["data"]["confirmed"] = {"by": LEAK, "at": 1.0}
        shop["archive"].put(rec)
    pull(shop)
    assert LEAK not in text(signed_in("owner").get(f"/assets/{shop['asset']}"))


def test_an_asset_whose_game_isnt_read_yet_says_which_game_and_why_nothing_shows(
    shop, signed_in, monkeypatch
):
    client = signed_in("tech")
    assert "has not" not in text(client.get(f"/assets/{shop['asset']}"))
    assert "The library sends that game on the next pull" in text(client.get(f"/assets/{shop['asset']}"))
    shop["archive"].token = "gbx_a1b2c3d4e5f6.someone-elses"
    pull(shop)
    assert "refused the reader token" in text(client.get(f"/assets/{shop['asset']}"))
    shop["stub"].stop()
    shop["archive"].token = None
    pull(shop)
    assert "could not be reached" in text(client.get(f"/assets/{shop['asset']}"))


def test_an_asset_with_no_game_shows_no_section(shop, signed_in):
    pull(shop)
    assert "Manual and limits" not in text(signed_in("tech").get(f"/assets/{shop['other']}"))


def test_an_asset_linked_to_a_retired_game_shows_the_survivor(shop, signed_in):
    old = copy.deepcopy(example("changes-response.json")["records"][0])
    old["id"], old["data"]["replaced_by"], old["data"]["title"] = (
        "widget-wars-old",
        "widget-wars",
        "Widget Wars (old key)",
    )
    shop["archive"].put(old)
    set_key(shop, shop["asset"], "widget-wars-old")
    pull(shop)
    page = text(signed_in("tech").get(f"/assets/{shop['asset']}"))
    assert "Service manual" in page and "(old key)" not in page and "games/widget-wars" in page


# --- a manual's PDF ---------------------------------------------------------------------------------------


def test_a_manual_opens_for_anyone_signed_in_and_only_for_a_held_document(shop, signed_in, client):
    pull(shop)
    assert client.get(f"/library/file/{MANUAL_SHA}").status_code in (302, 401)  # signed out: the login page
    c = signed_in("viewer")
    resp = c.get(f"/library/file/{MANUAL_SHA}")
    assert resp.status_code == 200 and resp.data == MANUAL and resp.mimetype == "application/pdf"
    assert (
        resp.headers["Content-Disposition"].startswith("inline")
        and "Service manual.pdf" in resp.headers["Content-Disposition"]
    )
    assert (
        resp.headers["X-Content-Type-Options"] == "nosniff"
        and "object-src" not in resp.headers["Content-Security-Policy"]
    )
    assert c.get(f"/library/file/{MANUAL_SHA}", headers={"Range": "bytes=0-9"}).status_code == 206
    assert c.get("/library/file/" + "0" * 64).status_code == 404
    assert c.get("/library/file/not-a-hash").status_code == 404
    assert c.get("/library/file/" + MANUAL_SHA.upper()).status_code == 404


def test_a_file_in_the_folder_that_no_document_names_is_not_served(shop, signed_in):
    pull(shop)
    body = b"%PDF-1.4 orphan"
    sha = hashlib.sha256(body).hexdigest()
    path = file_path(shop, sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    assert signed_in("owner").get(f"/library/file/{sha}").status_code == 404


# --- the tables -------------------------------------------------------------------------------------------


def test_library_state_is_one_row_and_the_app_role_cannot_delete_it(app, tdb):
    import psycopg

    with tdb.admin() as c:
        c.execute("INSERT INTO library_state (id) VALUES (1)")
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO library_state (id) VALUES (2)")
    from sqlalchemy import create_engine
    from sqlalchemy import text as sql_text

    engine = create_engine(tdb.url("app"))
    try:
        with engine.connect() as c:
            from sqlalchemy.exc import ProgrammingError

            with pytest.raises(ProgrammingError):
                c.execute(sql_text("DELETE FROM library_state"))
    finally:
        engine.dispose()


def test_a_record_kind_the_contract_doesnt_have_is_refused_by_the_database(app, tdb):
    import psycopg

    with tdb.admin() as c, pytest.raises(psycopg.errors.CheckViolation):
        c.execute(
            "INSERT INTO library_record (kind, rid, rev, hash, data) VALUES ('cartridge', 'x', 1, 'h', '{}')"
        )


# --- a second look at the same records, and a redirect ----------------------------------------


def test_a_record_read_again_unchanged_is_not_rewritten(shop):
    from app.library import pull as lp

    pull(shop)
    with shop["app"].app_context():
        everything = copy.deepcopy(example("changes-response-all.json")["records"])
        mine = [r for r in everything if model_of(r) == "widget-wars"]  # the example also has pin-comet-run
        assert lp.apply_records(mine) == 0


def test_a_redirect_is_not_followed_with_the_token(shop):
    other = Server()
    try:
        shop["archive"].redirect = other.url
        out = pull(shop)
    finally:
        other.stop()
    assert out["ok"] is False and out["refused"] is False and "instead of answering" in out["error"]
    assert other.archive.requests == [] and held(shop) == {}


def test_removing_the_archive_settings_takes_the_manual_route_away_at_once(shop, signed_in, monkeypatch):
    pull(shop)
    client = signed_in("tech")
    assert client.get(f"/library/file/{MANUAL_SHA}").status_code == 200
    monkeypatch.setitem(shop["app"].config, "LIBRARY_URL", "")
    assert client.get(f"/library/file/{MANUAL_SHA}").status_code == 404


def test_a_document_listed_but_not_downloaded_has_no_file_to_open(shop, signed_in):
    doc = copy.deepcopy(example("changes-response.json")["records"][1])
    doc["id"], doc["data"]["doc_id"], doc["data"]["sha256"] = "widget-wars/later", "later", "ab" * 32
    shop["archive"].put(doc)
    pull(shop)
    assert signed_in("tech").get("/library/file/" + "ab" * 32).status_code == 404
