"""Config refusals, response headers, and repo-wide rules (no floats, no external assets)."""

import re
from datetime import timedelta
from pathlib import Path

import pytest

from app.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parent.parent
GOOD = {
    "DATABASE_URL": "postgresql+psycopg://shop@127.0.0.1:5433/shop",
    "SECRET_KEY": "k" * 64,
    "SHOP_TZ": "Europe/Lisbon",
    "SHOP_BASE_URL": "https://shop.example.com/",
}


@pytest.mark.parametrize("key,value", [
    ("DATABASE_URL", ""),
    ("DATABASE_URL", "sqlite:///shop.db"),
    ("DATABASE_URL", "postgresql://shop@127.0.0.1/shop"),
    ("SECRET_KEY", ""),
    ("SECRET_KEY", "short"),
    ("SHOP_TZ", ""),
    ("SHOP_TZ", "Mars/Olympus_Mons"),
    ("SHOP_BASE_URL", ""),
    ("SHOP_BASE_URL", "shop.example.com"),
])
def test_config_refuses(key, value):
    with pytest.raises(ConfigError):
        load_config({**GOOD, key: value})


def test_config_cookie_and_session_defaults():
    cfg = load_config(GOOD)
    assert cfg["SESSION_COOKIE_SECURE"] is True
    assert cfg["SESSION_COOKIE_HTTPONLY"] is True
    assert cfg["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert cfg["PERMANENT_SESSION_LIFETIME"] == timedelta(hours=12)
    assert cfg["SHOP_BASE_URL"] == "https://shop.example.com"


def test_healthz_and_headers(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200 and resp.json == {"ok": True, "db": True}
    page = client.get("/login")
    csp = page.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp
    assert page.headers["X-Frame-Options"] == "DENY"
    assert page.headers["X-Content-Type-Options"] == "nosniff"
    assert page.headers["Cache-Control"] == "no-store"
    assert "noindex" in page.headers["X-Robots-Tag"]


def test_static_fonts_served(client):
    for font in ("ChakraPetch-Regular.woff2", "ChakraPetch-Bold.woff2", "ShareTechMono-Regular.woff2"):
        assert client.get(f"/static/fonts/{font}").status_code == 200


# --- repo-wide greps ----------------------------------------------------------------

FLOAT_RE = re.compile(r"\bfloat\b|\bFloat\b|\bREAL\b|DOUBLE PRECISION|\bDouble\b")


def _python_sources():
    for base in ("app", "migrations", "scripts"):
        yield from (ROOT / base).rglob("*.py")


def test_no_float_near_money():
    """Money is Decimal end to end. Any float in app code is flagged; a deliberate one
    carries `# float-ok: <reason>` on the same line."""
    hits = []
    for path in _python_sources():
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if FLOAT_RE.search(line) and "float-ok:" not in line:
                hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert hits == []


EXTERNAL_RE = re.compile(r"""(https?:)?//[a-z0-9.-]+\.[a-z]{2,}""", re.I)


def test_no_external_assets():
    """No CDN: templates, CSS and JS reference nothing off-site (license texts aside).
    Vendored libraries (app/static/vendor/) carry URLs in their comments and code but load
    nothing; they're pinned by hash instead (below)."""
    hits = []
    vendor = ROOT / "app/static/vendor"
    for base, patterns in (("app/templates", ["*.html"]), ("app/static", ["*.css", "*.js", "*.html"])):
        for pattern in patterns:
            for path in (ROOT / base).rglob(pattern):
                if vendor in path.parents:
                    continue
                for n, line in enumerate(path.read_text().splitlines(), 1):
                    if EXTERNAL_RE.search(line):
                        hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert hits == []


def test_vendored_files_are_pinned():
    """Every file under app/static/vendor/ is listed in VENDORED.txt and hashes to what's
    recorded there: the exact upstream release, nothing edited or swapped."""
    import hashlib

    vendor = ROOT / "app/static/vendor"
    pinned = {}
    for line in (vendor / "VENDORED.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            path, sha, *_ = line.split()
            pinned[path] = sha
    present = {str(p.relative_to(vendor)) for p in vendor.rglob("*")
               if p.is_file() and p.name != "VENDORED.txt"}
    assert present == set(pinned)
    for path, sha in pinned.items():
        assert hashlib.sha256((vendor / path).read_bytes()).hexdigest() == sha, path


def test_no_inline_scripts_or_styles():
    """The CSP forbids them; catching them here beats a silently broken page."""
    hits = []
    for path in (ROOT / "app/templates").rglob("*.html"):
        text = path.read_text()
        if re.search(r"<script(?![^>]*\bsrc=)", text) or re.search(r"\sstyle=", text) \
                or re.search(r"\son[a-z]+=", text):
            hits.append(str(path.relative_to(ROOT)))
    assert hits == []


def test_weasyprint_renders(tmp_path):
    from weasyprint import HTML

    pdf = HTML(string="<h1>Claim ticket</h1><p>Synthetic.</p>").write_pdf()
    assert pdf[:5] == b"%PDF-" and len(pdf) > 500


def test_nothing_the_app_or_deploy_needs_is_git_ignored():
    """deploy/sql/create_roles.sql once existed only on disk: *.sql is git-ignored (dumps),
    so it never reached the repo and the runbook broke on a fresh clone."""
    import subprocess

    out = subprocess.run(
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--",
         "app", "deploy", "migrations", "scripts", "tests", ".github"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert [p for p in out if "__pycache__" not in p] == []


def test_alembic_reads_its_ini_in_any_locale():
    """Alembic reads alembic.ini with encoding="locale", which ignores Python's UTF-8 mode
    and is ASCII under LC_ALL=C or an LC_ALL naming a missing locale. A "..." written as
    one character there broke deploy.sh --check on the LXC (2026-10-14). The file stays
    ASCII, and deploy.sh pins LC_ALL=C.UTF-8 for good measure."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k not in ("LANG", "LC_CTYPE")}
    out = subprocess.run(
        [sys.executable, "-c", "from alembic.config import Config; "
         "print(Config('migrations/alembic.ini').get_main_option('script_location'))"],
        cwd=ROOT, env={**env, "LC_ALL": "C"}, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-500:]
    deploy = (ROOT / "deploy" / "deploy.sh").read_text()
    assert "\nexport LC_ALL=C.UTF-8\n" in deploy
    assert deploy.index("export LC_ALL=C.UTF-8") < deploy.index("flask_cmd()")


def test_scripts_keep_database_passwords_off_command_lines(tmp_path):
    """deploy.sh, backup.sh and restore_drill.sh hand libpq tools the URL without its
    password and the password in PGPASSWORD: a command line is readable by every user on
    the box, and the schema owner's password would let the app role switch triggers off."""
    import os
    import subprocess

    out = subprocess.run(
        ["bash", "-c", '. scripts/libpq.sh && pg_split "$U" && printf "%s|%s" "$PG_NOPW" "$PG_PW"'],
        cwd=ROOT, env={**os.environ, "U": "postgresql+psycopg://shop_owner:test%40pw@db.example.test:5432"
                                          "/shop?sslmode=require"},
        capture_output=True, text=True, check=True).stdout
    assert out == "postgresql://shop_owner@db.example.test:5432/shop?sslmode=require|test@pw"
    tool_with_url = re.compile(r"(psql|pg_dump|pg_restore)\b[^\n]*\$\{?[A-Z_]*(DATABASE_)?URL\b")
    for script in ("deploy/deploy.sh", "scripts/backup.sh", "scripts/restore_drill.sh"):
        text = (ROOT / script).read_text()
        assert "libpq.sh" in text, script
        assert not tool_with_url.search(text), (script, tool_with_url.search(text).group(0))


def test_restore_drill_refuses_a_tampered_counts_file(tmp_path):
    """counts.tsv is written by the service user and its names go into the drill's SQL as
    the schema owner: anything but public.<name><tab><count> stops the drill before it
    connects."""
    import os
    import subprocess

    run = tmp_path / "daily" / "20261027T000000Z"
    run.mkdir(parents=True)
    for name, body in (("shop.dump", "x"), ("files.tar", ""), ("toc.txt", ""),
                       ("counts.tsv", "public.customer\t1\npublic.x; DROP TABLE y\t1\n")):
        (run / name).write_text(body)
    subprocess.run("sha256sum shop.dump files.tar toc.txt counts.tsv > SHA256SUMS", shell=True,
                   cwd=run, check=True)
    out = subprocess.run(["bash", str(ROOT / "scripts" / "restore_drill.sh"), str(run)],
                         env={**os.environ, "RESTORE_DATABASE_URL":
                              "postgresql://nobody@127.0.0.1:1/never_restore_test"},
                         capture_output=True, text=True)
    assert out.returncode != 0 and "counts.tsv has a line" in out.stderr, out.stderr
