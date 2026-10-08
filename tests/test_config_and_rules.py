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
    """No CDN: templates, CSS and JS reference nothing off-site (license texts aside)."""
    hits = []
    for base, patterns in (("app/templates", ["*.html"]), ("app/static", ["*.css", "*.js", "*.html"])):
        for pattern in patterns:
            for path in (ROOT / base).rglob(pattern):
                for n, line in enumerate(path.read_text().splitlines(), 1):
                    if EXTERNAL_RE.search(line):
                        hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert hits == []


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
