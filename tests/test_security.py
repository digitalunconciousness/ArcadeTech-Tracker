"""Regressions for the October 2026 security review: each test pins one finding's fix."""

import copy
import hashlib
import io
import re
import secrets
import time
from datetime import timedelta
from decimal import Decimal as D

import psycopg
import pytest
from PIL import Image
from test_api_v1_contract import PUBLIC_ID, bench, post, request_for  # noqa: F401
from test_assets import customer
from test_billing import billable, issue, issued_id, today
from test_money_desk import bank_csv, upload
from test_partlinks import add_vendor
from test_parts import PART, add_part, part_row
from test_planning import DAY
from test_work import first_job, new_wo, text

from conftest import TEST_PASSWORD, db_query, libpq, login, totp_code

# --- inputs -----------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["NaN", "-nan", "Infinity", "sNaN", "1e30", "12345678901"])
def test_bank_amounts_must_be_real_numbers_that_fit(raw):
    """NaN parsed as a Decimal, passed "amount <> 0", stored, and broke every bank page."""
    from app.bank.service import BankError, parse_amount
    with pytest.raises(BankError):
        parse_amount(raw)
    assert parse_amount("9999999999.99") == D("9999999999.99")


def test_a_bank_csv_with_nan_imports_nothing_and_the_database_refuses_nan(app, signed_in, tdb):
    owner = signed_in("owner")
    day = today(app)
    owner.post("/bank/", data={"name": "Checking", "kind": "checking",
                               "opening_on": str(day - timedelta(days=30)),
                               "opening_balance": "0", "last4": ""})
    account = db_query(app, "SELECT id FROM bank_account")[0][0]
    resp = upload(owner, account, bank_csv(app, [(day, "ODD LINE", "", "NaN")]))
    assert "isn't an amount" in text(resp)
    assert db_query(app, "SELECT count(*) FROM bank_line") == [(0,)]
    with psycopg.connect(libpq(tdb.url("owner"))) as c:
        imp = c.execute("INSERT INTO bank_import (bank_account_id, filename, file_sha256, added, "
                        "skipped) VALUES (%s, 'x.csv', %s, 1, 0) RETURNING id",
                        (account, "a" * 64)).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation, match="ck_bank_line_amount_finite"):
            c.execute("INSERT INTO bank_line (bank_account_id, bank_import_id, posted_on, "
                      "description, amount, fingerprint) VALUES (%s, %s, %s, 'x', 'NaN', 'f')",
                      (account, imp, day))


def test_csv_cells_that_arent_numbers_or_dates_are_escaped():
    """A report's Link (a part's SKU) skipped the formula escaping plain strings get."""
    from app.reports.service import _cell
    from app.reports.tables import Link
    assert _cell(Link('=HYPERLINK("http://x.example/")', "/parts/1")) \
        == '\'=HYPERLINK("http://x.example/")'
    assert _cell("@SUM(A1)") == "'@SUM(A1)"
    assert _cell(D("-5.00")) == "-5.00" and _cell(-3) == "-3"      # numbers stay numbers


def test_appointment_next_only_goes_back_into_the_app(app, signed_in):
    """/\\host and a tab before //host read as another site to a browser."""
    owner = signed_in("owner")
    wo = new_wo(owner, customer(owner, "Redirect Test Arcade"))
    owner.post("/work/dispatch/book", data={"wo_id": wo, "starts_at": f"{DAY}T08:00",
                                            "user": owner.user["id"]})
    appt = db_query(app, "SELECT id FROM appointment")[0][0]
    for evil in ("/\\evil.example", "/\t/evil.example", "//evil.example"):
        page = owner.get(f"/work/appointments/{appt}/edit", query_string={"next": evil})
        assert "evil.example" not in page.get_data(as_text=True)
        resp = owner.post(f"/work/appointments/{appt}/edit", data={
            "starts_at": f"{DAY}T08:00", "minutes": "60", "users": [str(owner.user["id"])],
            "site_id": "None", "note": "", "next": evil})
        assert resp.status_code == 302 and resp.location == f"/work/{wo}#appointments"


def test_web_addresses_must_be_http_or_https(app, signed_in):
    """WTForms' URL() takes javascript://...; a click would run it if the CSP ever loosened."""
    owner = signed_in("owner")
    bad = "javascript://example.com/%0Aalert(document.domain)"
    resp = owner.post("/vendors/new", data={"name": "Bad Vendor", "website": bad})
    assert resp.status_code == 200 and "http:// or https://" in text(resp)
    vid = add_vendor(owner, website="https://www.example.com/")
    pid = add_part(owner, sku="URL-1")
    resp = owner.post(f"/parts/{pid}/edit", data={**PART, "sku": "URL-1", "active": "y",
                                                  "datasheet_url": bad})
    assert resp.status_code == 200 and "http:// or https://" in text(resp)
    owner.post(f"/parts/{pid}/edit", data={**PART, "sku": "URL-1", "active": "y",
                                           "datasheet_url": "https://www.example.com/ds.pdf"})
    assert part_row(app, pid).datasheet_url == "https://www.example.com/ds.pdf"
    assert vid


def test_photos_over_the_pixel_limit_are_refused_before_decoding(app):
    """Pillow only refuses at twice MAX_IMAGE_PIXELS: a ~9 KB PNG can claim 72 MP."""
    from app.work import files
    big = Image.new("1", (9000, 8000))
    buf = io.BytesIO()
    big.save(buf, "PNG", optimize=True)
    assert len(buf.getvalue()) < 50_000
    with app.app_context(), pytest.warns(Image.DecompressionBombWarning), \
            pytest.raises(files.FileError, match=re.escape("too large")):
        files.store_photo(io.BytesIO(buf.getvalue()))


# --- links, approvals and devices -------------------------------------------------------------

def test_a_tech_cant_withdraw_invoice_links_through_the_estimates_route(app, signed_in):
    """estimates.revoke took any link id: techs (who never see invoices) could kill them."""
    owner, tech = signed_in("owner"), signed_in("tech")
    b = billable(owner, app)
    inv = issued_id(issue(owner, app, b["wo"]))
    token = re.search(r"/d/([A-Za-z0-9_-]{43})",
                      text(owner.post(f"/invoices/{inv}/send"))).group(1)
    link = db_query(app, "SELECT id FROM doc_link WHERE invoice_id = :i", i=inv)[0][0]
    assert tech.post(f"/estimates/links/{link}/revoke").status_code == 404
    assert db_query(app, "SELECT revoked_at FROM doc_link WHERE id = :l", l=link) == [(None,)]
    assert app.test_client().get(f"/d/{token}").status_code == 200


def test_statement_links_are_listed_and_can_be_withdrawn(app, signed_in):
    """A statement link went out for 30 days with no way to take it back."""
    owner, tech, viewer = signed_in("owner"), signed_in("tech"), signed_in("viewer")
    b = billable(owner, app)
    issued_id(issue(owner, app, b["wo"]))
    page = text(owner.post("/statements", data={"c": [b["cid"]], "action": "links"}))
    path = re.search(r"https?://[^/\s]+(/d/[A-Za-z0-9_-]{43})", page).group(1)
    link = db_query(app, "SELECT id FROM doc_link WHERE customer_id = :c", c=b["cid"])[0][0]
    shown = text(owner.get(f"/customers/{b['cid']}"))
    assert "Statement links that still open" in shown and f"/invoices/links/{link}/revoke" in shown
    assert f"/invoices/links/{link}/revoke" not in text(viewer.get(f"/customers/{b['cid']}"))
    assert tech.post(f"/invoices/links/{link}/revoke").status_code == 403
    assert viewer.post(f"/invoices/links/{link}/revoke").status_code == 403
    resp = owner.post(f"/invoices/links/{link}/revoke")
    assert resp.status_code == 302 and resp.location.endswith(f"/customers/{b['cid']}#account")
    anon = app.test_client()
    assert anon.get(path).status_code == 404
    assert anon.get(path + "/statement.pdf").status_code == 404
    assert "Statement links that still open" not in text(owner.get(f"/customers/{b['cid']}"))


def test_a_signature_must_carry_the_page_it_was_for(app, signed_in):
    """Leaving out "seen" skipped the changed-since-opened check; notes weren't hashed."""
    from test_estimates import approve_by_link, built, send, set_terms, signature_png
    owner = signed_in("owner")
    set_terms(owner)
    _, _, _, est = built(owner, app)
    token = send(owner, est)
    anon = app.test_client()
    resp = anon.post(f"/d/{token}/approve", data={"name": "Morgan Owner",
                                                  "signature": signature_png()},
                     follow_redirects=True)
    assert "changed since it was opened" in text(resp)
    assert db_query(app, "SELECT approved_at FROM estimate WHERE id = :e", e=est) == [(None,)]
    # the notes are on the estimate the customer reads: changing them after the page was
    # opened is a change too
    page = text(anon.get(f"/d/{token}"))
    seen = re.search(r'name="seen" type="hidden" value="([0-9a-f]{64})"', page).group(1)
    with app.app_context():
        from app.extensions import db
        from app.models import Estimate
        db.session.get(Estimate, est).notes = "Price includes a new flyback."
        db.session.commit()
    assert "changed since it was opened" in text(approve_by_link(anon, token, seen=seen))
    assert "approved" in text(approve_by_link(anon, token)).lower()
    assert db_query(app, "SELECT approval_method FROM estimate WHERE id = :e", e=est) == [("link",)]


def test_a_device_cant_claim_or_tag_another_devices_traces(app, client, bench):  # noqa: F811
    """Session uids are predictable, and nothing tied a stored session to the device that
    made it: a second device could take over a trace or put one on any job."""
    from app.extensions import db
    from app.models import Device
    payload = request_for(bench["tag"])
    payload["items"] = payload["items"][:1]
    assert post(client, bench, payload).get_json()["results"][0]["status"] == "created"
    session = payload["items"][0]
    with app.app_context():
        other = Device(name="second-bench", public_id="0f0e0d0c0b0a", jobs=True)
        other_token = other.issue_token()
        db.session.add(other)
        db.session.commit()

    def as_other(items):
        body = {"contract": "v1", "device": "0f0e0d0c0b0a", "items": items}
        return client.post("/api/v1/ingest", json=body,
                           headers={"Authorization": f"Bearer {other_token}"}).get_json()

    # the first device's session, or a guess at a future one, sent by the second device
    for uid in (session["uid"],
                hashlib.sha256(f"{PUBLIC_ID}|rail_session|rail_next.csv".encode())
                .hexdigest()[:32]):
        item = {**copy.deepcopy(session), "uid": uid, "file": session["file"]
                if uid == session["uid"] else "rail_next.csv"}
        result = as_other([item])["results"][0]
        assert result["status"] == "rejected" and "rule" in result["reason"], result
    # its own session is fine
    own = copy.deepcopy(session)
    own["file"] = "rail_own.csv"
    own["uid"] = hashlib.sha256(b"0f0e0d0c0b0a|rail_session|rail_own.csv").hexdigest()[:32]
    for r in own["readings"]:
        r["uid"] = secrets.token_hex(16)
    assert as_other([own])["results"][0]["status"] == "created"
    # tagging the first device's session onto a job: refused for the second device
    wo = new_wo(bench["owner"], bench["cid"], bench["widget"])
    job = first_job(app, wo)
    tag = {"kind": "session_tag", "uid": secrets.token_hex(16), "order": job,
           "rail_session": session["uid"], "created": 1790000500.0}
    result = as_other([tag])["results"][0]
    assert result["status"] == "rejected" and "another device" in result["reason"]
    # and a job with no machine takes no trace, from anyone
    consult = first_job(app, new_wo(bench["owner"], bench["cid"]))
    body = {**request_for(bench["tag"]), "items": [{**tag, "order": consult}]}
    result = post(client, bench, body).get_json()["results"][0]
    assert result["status"] == "rejected" and "no machine" in result["reason"]


# --- signing in -------------------------------------------------------------------------------

@pytest.fixture
def limits_on():
    from app.extensions import limiter
    limiter.enabled = True
    limiter.reset()
    yield
    limiter.reset()
    limiter.enabled = False


def test_codes_are_limited_per_account_as_well_as_per_address(app, make_user, limits_on):
    """Five tries a minute per address only: from many addresses, a user's codes could be
    guessed at speed once their password was known."""
    user = make_user("otto", "owner", totp=True)
    client = app.test_client()
    resp = client.post("/login", data={"username": "otto", "password": TEST_PASSWORD},
                       environ_base={"REMOTE_ADDR": "198.51.100.20"})
    assert resp.location.endswith("/login/totp")
    wrong = f"{(int(totp_code(user['secret'])) + 500_000) % 1_000_000:06d}"
    codes = [client.post("/login/totp", data={"code": wrong},
                         environ_base={"REMOTE_ADDR": f"203.0.113.{i + 1}"}).status_code
             for i in range(6)]
    assert codes == [200] * 5 + [429]


def test_passwords_are_limited_per_username_as_well_as_per_address(app, make_user, limits_on):
    make_user("tess")
    codes = [app.test_client().post("/login", data={"username": "Tess", "password": "wrong-test-pw-x"},
                                    environ_base={"REMOTE_ADDR": f"203.0.113.{i + 1}"}).status_code
             for i in range(31)]
    assert codes == [200] * 30 + [429]
    # another username from the same addresses is still fine
    assert app.test_client().post("/login", data={"username": "otto", "password": "wrong-test-pw-x"},
                                  environ_base={"REMOTE_ADDR": "203.0.113.1"}).status_code == 200


@pytest.mark.parametrize("peer, key", [
    ("198.51.100.7", "198.51.100.7"),
    ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
    ("::ffff:198.51.100.7", "198.51.100.7"),
])
def test_rate_limits_count_ipv6_by_its_64(app, peer, key):
    """One IPv6 connection holds a whole /64: per address, a limit never trips."""
    from app.extensions import limit_key
    with app.test_request_context(environ_base={"REMOTE_ADDR": peer}):
        assert limit_key() == key


def test_a_session_ends_a_fixed_time_after_sign_in(app, make_user):
    """The cookie's lifetime renews on every request, so a copied cookie in daily use never
    expired. Now sign-in time is in the session and checked against the lifetime."""
    user = make_user("tess")
    limit = app.config["PERMANENT_SESSION_LIFETIME"].total_seconds()
    for stale in (time.time() - limit - 60, None, "yesterday", time.time() + 3600):
        client = app.test_client()
        assert login(client, user).location == "/"
        assert client.get("/").status_code == 200
        with client.session_transaction() as s:
            if stale is None:
                del s["signed_in_at"]
            else:
                s["signed_in_at"] = stale
        resp = client.get("/")
        assert resp.status_code == 302 and "/login" in resp.location, stale


# --- owner's follow-ups (2026-10-27) ----------------------------------------------------------

def test_2fa_can_be_required_of_everyone(app, signed_in, make_user):
    owner = signed_in("owner")
    tess = make_user("tess", "tech")
    tech = app.test_client()
    assert login(tech, tess).location == "/"
    assert tech.get("/").status_code == 200
    # needs the owner's own password, and only an owner can change it
    owner.post("/settings/2fa-policy", data={"require_2fa_all": "y", "your_password": "wrong-test-pw"})
    assert db_query(app, "SELECT require_2fa_all FROM shop_setting") == [(False,)]
    assert tech.post("/settings/2fa-policy", data={"require_2fa_all": "y",
                                                  "your_password": TEST_PASSWORD}).status_code == 403
    resp = owner.post("/settings/2fa-policy", data={"require_2fa_all": "y",
                                                   "your_password": TEST_PASSWORD})
    assert resp.status_code == 302 and resp.location.endswith("/settings/users#2fa")
    assert db_query(app, "SELECT require_2fa_all FROM shop_setting") == [(True,)]
    # the tech, already signed in, is sent to set it up at their next page
    resp = tech.get("/")
    assert resp.status_code == 302 and resp.location == "/account/2fa"
    assert "Required" in text(tech.get("/account/2fa"))
    owner.post("/settings/2fa-policy", data={"your_password": TEST_PASSWORD})
    assert db_query(app, "SELECT require_2fa_all FROM shop_setting") == [(False,)]
    assert tech.get("/").status_code == 200


def test_signing_out_ends_every_session_and_the_calendar_link(app, make_user):
    tess = make_user("tess", "tech")
    phone, desk = app.test_client(), app.test_client()
    login(phone, tess)
    login(desk, tess)
    page = text(phone.post("/account/calendar"))
    token = re.search(r"/d/cal/([A-Za-z0-9_-]{43})\.ics", page).group(1)
    anon = app.test_client()
    assert anon.get(f"/d/cal/{token}.ics").status_code == 200
    assert phone.post("/logout").status_code == 302
    resp = desk.get("/")
    assert resp.status_code == 302 and "/login" in resp.location
    assert anon.get(f"/d/cal/{token}.ics").status_code == 404
    # a new sign-in and a new link work as before
    assert login(desk, tess).location == "/"
    token = re.search(r"/d/cal/([A-Za-z0-9_-]{43})\.ics",
                      text(desk.post("/account/calendar"))).group(1)
    assert anon.get(f"/d/cal/{token}.ics").status_code == 200


def test_a_voided_invoice_reads_void_with_nothing_owed(app, signed_in, monkeypatch):
    """The PDF frozen at issue said "Balance due"; on a void invoice's link it still did."""
    from flask import render_template

    from app.work import docs as work_docs
    owner = signed_in("owner")
    b = billable(owner, app)
    inv = issued_id(issue(owner, app, b["wo"]))
    token = re.search(r"/d/([A-Za-z0-9_-]{43})",
                      text(owner.post(f"/invoices/{inv}/send"))).group(1)
    owner.post(f"/invoices/{inv}/void", data={"reason": "Wrong customer"})
    frozen = db_query(app, "SELECT pdf_sha256 FROM invoice WHERE id = :i", i=inv)[0][0]

    def as_html(template, **ctx):   # the PDF's HTML, so the test can read what it says
        return b"%PDF-1.7 " + render_template(
            template, doc_css="css/doc.css", wo_kinds=work_docs.WO_KINDS,
            received_via=work_docs.RECEIVED_VIA, **ctx).encode()

    monkeypatch.setattr("app.billing.docs.render_pdf", as_html)
    for resp in (owner.get(f"/invoices/{inv}/invoice.pdf"),
                 app.test_client().get(f"/d/{token}/invoice.pdf")):
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200 and body.startswith("%PDF")
        assert "was voided" in body and "Nothing is owed" in body and "Balance due" not in body
    issued = owner.get(f"/invoices/{inv}/invoice.pdf", query_string={"as_issued": "1"})
    assert hashlib.sha256(issued.data).hexdigest() == frozen
    assert "Invoice as issued" in text(owner.get(f"/invoices/{inv}"))
