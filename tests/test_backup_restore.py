"""backup.sh and restore_drill.sh against the test database, end to end.

Needs pg_dump / pg_restore / psql at least as new as the server (17), from PATH or
SHOP_PG_BIN; skipped otherwise, with the reason shown."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.extensions import db
from conftest import login

ROOT = Path(__file__).resolve().parent.parent


def _pg_tool(name):
    base = os.environ.get("SHOP_PG_BIN")
    return str(Path(base) / name) if base else shutil.which(name)


def _pg_major_ok(app):
    tool = _pg_tool("pg_dump")
    if not tool:
        return "pg_dump not found (set SHOP_PG_BIN)"
    out = subprocess.run([tool, "--version"], capture_output=True, text=True).stdout
    client = int(re.search(r"(\d+)\.", out).group(1))
    with app.app_context():
        server = int(db.session.execute(db.text("SHOW server_version_num")).scalar()) // 10000
    if client < server:
        return f"pg_dump {client} is older than the server ({server})"
    return None


@pytest.fixture
def pg_tools(app):
    reason = _pg_major_ok(app)
    if reason:
        pytest.skip(reason)


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items()}
    env.update({
        "SHOP_BACKUP_DIR": str(tmp_path / "backups"),
        "SHOP_FILES_DIR": str(tmp_path / "files"),
        "SHOP_BACKUP_HOOK": str(tmp_path / "no-hook"),
        **extra,
    })
    return env


def _run(script, env, *args):
    return subprocess.run([str(ROOT / "scripts" / script), *args], env=env,
                          capture_output=True, text=True, timeout=180)


def test_backup_then_restore_drill(app, tdb, make_user, pg_tools, tmp_path):
    client = app.test_client()
    owner = make_user("olive", "owner", totp=True)
    make_user("tess", "tech")
    login(client, owner)
    (tmp_path / "files" / "sig").mkdir(parents=True)
    (tmp_path / "files" / "sig" / "est-1.png").write_bytes(b"\x89PNG synthetic")

    env = _env(tmp_path)
    backup = _run("backup.sh", env)  # as the app role, like the nightly timer
    assert backup.returncode == 0, backup.stderr
    runs = list((tmp_path / "backups" / "daily").iterdir())
    assert len(runs) == 1
    run = runs[0]
    assert {p.name for p in run.iterdir()} == {
        "shop.dump", "files.tar", "toc.txt", "counts.tsv", "SHA256SUMS"}
    counts = dict(line.split("\t") for line in (run / "counts.tsv").read_text().splitlines())
    assert counts["public.app_user"] == "2"
    assert int(counts["public.audit_log"]) >= 2
    assert (tmp_path / "backups" / "monthly" / run.name).exists()
    assert (tmp_path / "backups" / "yearly" / run.name).exists()

    restore_db = f"{tdb.name}_restore"
    tdb.create_database(restore_db)
    drill = _run("restore_drill.sh", {**env, "RESTORE_DATABASE_URL": tdb.url("owner", restore_db)})
    assert drill.returncode == 0, drill.stdout + drill.stderr
    assert "restore drill: PASS" in drill.stdout
    assert "row counts match" in drill.stdout

    # And again over the previous restore (the monthly timer re-uses the database).
    drill = _run("restore_drill.sh", {**env, "RESTORE_DATABASE_URL": tdb.url("owner", restore_db)})
    assert drill.returncode == 0, drill.stdout + drill.stderr

    # A corrupted backup fails the drill.
    with open(run / "shop.dump", "r+b") as f:
        f.seek(100)
        f.write(b"\0\0\0\0")
    drill = _run("restore_drill.sh", {**env, "RESTORE_DATABASE_URL": tdb.url("owner", restore_db)})
    assert drill.returncode != 0 and "checksum" in drill.stderr


def test_drill_refuses_live_looking_database(app, tdb, pg_tools, tmp_path):
    env = _env(tmp_path)
    assert _run("backup.sh", env).returncode == 0
    with tdb.admin("postgres") as c:
        c.execute(f'CREATE DATABASE "shop_live_{tdb.name[-8:]}" OWNER "{tdb.owner}"')
    try:
        url = tdb.url("owner", f"shop_live_{tdb.name[-8:]}")
        drill = _run("restore_drill.sh", {**env, "RESTORE_DATABASE_URL": url})
        assert drill.returncode != 0 and "refusing" in drill.stderr
    finally:
        tdb.drop_database(f"shop_live_{tdb.name[-8:]}")


def test_retention(app, pg_tools, tmp_path):
    daily = tmp_path / "backups" / "daily"
    for day in range(1, 21):
        (daily / f"202601{day:02d}T030000Z").mkdir(parents=True)
    monthly = tmp_path / "backups" / "monthly"
    for month in range(1, 13):
        (monthly / f"2025{month:02d}01T030000Z").mkdir(parents=True)
    env = _env(tmp_path)
    assert _run("backup.sh", env).returncode == 0
    kept = sorted(p.name for p in daily.iterdir())
    assert len(kept) == 14 and kept[0] == "20260108T030000Z"
    assert len(list(monthly.iterdir())) == 12  # this month's run added, the oldest pruned
    assert not (monthly / "20250101T030000Z").exists()
