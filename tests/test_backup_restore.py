"""backup.sh and restore_drill.sh against the test database, end to end.

Needs pg_dump / pg_restore / psql at least as new as the server (17), from PATH or
SHOP_PG_BIN; skipped otherwise, with the reason shown."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import psycopg
import pytest

from app.extensions import db
from conftest import libpq, login

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
    tdb.create_database(restore_db, owner=tdb.drill_role)
    drill = _run("restore_drill.sh", {**env, "RESTORE_DATABASE_URL": tdb.url("drill", restore_db)})
    assert drill.returncode == 0, drill.stdout + drill.stderr
    assert "restore drill: PASS" in drill.stdout
    assert "row counts match" in drill.stdout
    assert "photo re-hash: no photos yet" in drill.stdout
    # The drill's role owns the scratch database and can't so much as connect to the live one.
    with pytest.raises(psycopg.OperationalError, match="permission denied"):
        psycopg.connect(libpq(tdb.url("drill"))).close()

    # And again over the previous restore (the monthly timer re-uses the database).
    drill = _run("restore_drill.sh", {**env, "RESTORE_DATABASE_URL": tdb.url("drill", restore_db)})
    assert drill.returncode == 0, drill.stdout + drill.stderr

    # A corrupted backup fails the drill.
    with open(run / "shop.dump", "r+b") as f:
        f.seek(100)
        f.write(b"\0\0\0\0")
    drill = _run("restore_drill.sh", {**env, "RESTORE_DATABASE_URL": tdb.url("drill", restore_db)})
    assert drill.returncode != 0 and "checksum" in drill.stderr


def test_drill_refuses_live_looking_database(app, tdb, pg_tools, tmp_path):
    env = _env(tmp_path)
    assert _run("backup.sh", env).returncode == 0
    with tdb.admin("postgres") as c:
        c.execute(f'CREATE DATABASE "shop_live_{tdb.name[-8:]}" OWNER "{tdb.drill_role}"')
    try:
        url = tdb.url("drill", f"shop_live_{tdb.name[-8:]}")
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


def test_drill_rehashes_issued_invoice_pdfs(app, tdb, signed_in, pg_tools, tmp_path):
    """The drill proves every issued invoice PDF (with the service report frozen with it)
    and credit memo PDF is still the exact file the customer got, even when the archive's
    checksums agree."""
    from test_billing import billable, issue, issued_id

    from conftest import db_query

    owner = signed_in("owner")
    inv = issued_id(issue(owner, app, billable(owner, app)["wo"]))
    owner.post(f"/invoices/{inv}/credit-memo", data={
        "reason": "Part returned", "lines-0-description": "Caps", "lines-0-amount": "3.90"})
    shas = list(db_query(app, "SELECT pdf_sha256, report_sha256 FROM invoice")[0]) + \
        [db_query(app, "SELECT pdf_sha256 FROM credit_memo")[0][0]]
    store, files_dir = Path(app.config["SHOP_FILES_DIR"]), tmp_path / "files"
    for sha in shas:
        (files_dir / sha[:2]).mkdir(parents=True, exist_ok=True)
        shutil.copy(store / sha[:2] / f"{sha}.pdf", files_dir / sha[:2] / f"{sha}.pdf")
    restore_db = f"{tdb.name}_rehash_restore"
    tdb.create_database(restore_db, owner=tdb.drill_role)
    url = tdb.url("drill", restore_db)

    def drill(run):
        env = _env(tmp_path, SHOP_BACKUP_DIR=str(tmp_path / run))
        assert _run("backup.sh", env).returncode == 0
        return _run("restore_drill.sh", {**env, "RESTORE_DATABASE_URL": url})

    ok = drill("good")
    assert ok.returncode == 0 and "pdf re-hash ok (3 files)" in ok.stdout, ok.stdout + ok.stderr
    invoice_pdf = files_dir / shas[0][:2] / f"{shas[0]}.pdf"
    invoice_pdf.write_bytes(b"%PDF-1.7 altered after issue")
    changed = drill("changed")
    assert changed.returncode != 0 and f"{shas[0]}.pdf: hashes to" in changed.stderr
    invoice_pdf.unlink()
    missing = drill("missing")
    assert missing.returncode != 0 and f"{shas[0]}.pdf: not in the archive" in missing.stderr


def test_drill_rehashes_receipts(app, tdb, signed_in, pg_tools, tmp_path):
    """Receipts behind expenses (a photo, a PDF) are checked like issued documents."""
    import io

    from test_books import add_expense, png

    from conftest import db_query

    owner = signed_in("owner")
    add_expense(owner, app, receipt=(png(), "r.png", "image/png"))
    add_expense(owner, app, receipt=(io.BytesIO(b"%PDF-1.4 a receipt"), "r.pdf",
                                     "application/pdf"))
    rows = db_query(app, "SELECT receipt_sha256, receipt_type FROM expense ORDER BY id")
    store, files_dir = Path(app.config["SHOP_FILES_DIR"]), tmp_path / "files"
    names = [f"{sha}{'.pdf' if kind == 'application/pdf' else '.jpg'}" for sha, kind in rows]
    for name in names:
        (files_dir / name[:2]).mkdir(parents=True, exist_ok=True)
        shutil.copy(store / name[:2] / name, files_dir / name[:2] / name)
    restore_db = f"{tdb.name}_receipt_restore"
    tdb.create_database(restore_db, owner=tdb.drill_role)
    url = tdb.url("drill", restore_db)

    def drill(run):
        env = _env(tmp_path, SHOP_BACKUP_DIR=str(tmp_path / run))
        assert _run("backup.sh", env).returncode == 0
        return _run("restore_drill.sh", {**env, "RESTORE_DATABASE_URL": url})

    ok = drill("good")
    assert ok.returncode == 0 and "receipt re-hash ok (2 files)" in ok.stdout, \
        ok.stdout + ok.stderr
    (files_dir / names[0][:2] / names[0]).write_bytes(b"swapped photo")
    bad = drill("changed")
    assert bad.returncode != 0 and f"{names[0]}: hashes to" in bad.stderr


def test_drill_rehashes_photos_and_finds_their_thumbnails(app, tdb, signed_in, pg_tools,
                                                          tmp_path):
    """Job photos: each file must hash to its name, with its thumbnail beside it. The
    thumbnail is named after the photo, not its own bytes, so it's checked for being there
    and being a JPEG."""
    from test_work import setup
    from test_work_files_docs import jpeg_with_exif, upload

    from conftest import db_query

    tech = signed_in("tech")
    job_id = setup(tech, app)[3]
    upload(tech, job_id, jpeg_with_exif())
    upload(tech, job_id, jpeg_with_exif(size=(80, 60)), caption="After")
    upload(tech, job_id, jpeg_with_exif(), caption="Again")    # one file, a second row
    shas = [r[0] for r in db_query(app, "SELECT DISTINCT sha256 FROM attachment ORDER BY 1")]
    assert len(shas) == 2
    store, files_dir = Path(app.config["SHOP_FILES_DIR"]), tmp_path / "files"
    for sha in shas:
        (files_dir / sha[:2]).mkdir(parents=True, exist_ok=True)
        for name in (f"{sha}.jpg", f"{sha}-thumb.jpg"):
            shutil.copy(store / sha[:2] / name, files_dir / sha[:2] / name)
    restore_db = f"{tdb.name}_photo_restore"
    tdb.create_database(restore_db, owner=tdb.drill_role)
    url = tdb.url("drill", restore_db)

    def drill(run):
        env = _env(tmp_path, SHOP_BACKUP_DIR=str(tmp_path / run))
        assert _run("backup.sh", env).returncode == 0
        return _run("restore_drill.sh", {**env, "RESTORE_DATABASE_URL": url})

    ok = drill("good")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "photo re-hash ok (2 photos, each with its thumbnail)" in ok.stdout
    photo = files_dir / shas[0][:2] / f"{shas[0]}.jpg"
    original = photo.read_bytes()
    photo.write_bytes(b"\xff\xd8\xff a different photo")
    changed = drill("changed")
    assert changed.returncode != 0 and f"{shas[0]}.jpg: hashes to" in changed.stderr
    photo.unlink()
    missing = drill("missing")
    assert missing.returncode != 0 and f"{shas[0]}.jpg: not in the archive" in missing.stderr
    photo.write_bytes(original)
    thumb = files_dir / shas[1][:2] / f"{shas[1]}-thumb.jpg"
    thumb.write_bytes(b"GIF89a not a thumbnail")
    garbled = drill("garbled")
    assert garbled.returncode != 0 and f"{shas[1]}-thumb.jpg: not a JPEG" in garbled.stderr
    thumb.unlink()
    no_thumb = drill("no-thumb")
    assert no_thumb.returncode != 0
    assert f"{shas[1]}-thumb.jpg: not in the archive" in no_thumb.stderr
    assert f"{shas[0]}.jpg" not in no_thumb.stderr           # the restored photo is fine
