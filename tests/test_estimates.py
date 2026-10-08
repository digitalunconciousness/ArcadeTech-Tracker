"""Estimates: building, links (hash only, one document, 404 for anything else), approval
through a link with a signature, the freeze in the database, revisions, conversion to a
work order, and change orders."""

import base64
import io
import re
from decimal import Decimal as D

import psycopg
import pytest
from PIL import Image, ImageDraw
from test_assets import asset, customer
from test_parts import add_part
from test_work import activate, text

from app.extensions import db, limiter
from app.models import Estimate
from app.timeutil import local_today
from conftest import db_query, libpq

TERMS = "Approval authorizes the work described. We won't exceed the limit without your OK."


def signature_png(blank=False):
    img = Image.new("RGBA", (600, 180), (0, 0, 0, 0))
    if not blank:
        ImageDraw.Draw(img).line([(20, 120), (120, 40), (220, 140), (380, 50), (560, 110)],
                                 fill=(0, 0, 0, 255), width=5)
    out = io.BytesIO()
    img.save(out, "PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def set_terms(owner, terms=TERMS):
    resp = owner.post("/settings/", data={"business_name": "ArcadeTech Tracker",
                                          "doc_accent_color": "#b0126f", "estimate_terms": terms,
                                          "estimate_link_days": "30"})
    assert resp.status_code == 302, text(resp)[-800:]


def new_estimate(client, cid, aid=None, **extra):
    data = {"summary": "Monitor rebuild", "site_id": "None", "asset_id": str(aid) if aid else "None",
            "complaint": "Picture rolls; smells hot", **extra}
    resp = client.post(f"/estimates/new?customer={cid}", data=data)
    assert resp.status_code == 302, text(resp)[-1500:]
    return int(resp.location.rsplit("/", 1)[1])


def est_job(app, est_id):
    return db_query(app, "SELECT id FROM estimate_job WHERE estimate_id = :e ORDER BY id",
                    e=est_id)[0][0]


def built(owner, app, nte=None):
    """An estimate with labor, a stocked part and a fee, ready to send."""
    cid = customer(owner, "Starlite Lanes Test")
    aid = asset(owner, cid, "Galaga")
    bench = activate(owner, app, "LAB-BENCH", "80")
    pid = add_part(owner)                      # CAP-470-25 at $1.95
    est_id = new_estimate(owner, cid, aid, not_to_exceed=nte or "", deposit_required="50")
    job = est_job(app, est_id)
    owner.post(f"/estimates/{est_id}/lines/service", data={
        "estimate_job_id": job, "service_id": bench, "labor_mode": "flat", "qty": "1.5"})
    owner.post(f"/estimates/{est_id}/lines/part", data={"estimate_job_id": job, "part_id": pid,
                                                        "qty": "10"})
    owner.post(f"/estimates/{est_id}/lines/other", data={
        "estimate_job_id": job, "kind": "fee", "description": "Shop supplies", "qty": "1",
        "unit_price": "12.50"})
    return cid, aid, pid, est_id


def send(client, est_id):
    resp = client.post(f"/estimates/{est_id}/send")
    page = text(resp)
    match = re.search(r"/d/([A-Za-z0-9_-]{43})", page)
    assert resp.status_code == 200 and match, page[-1500:]
    return match.group(1)


def approve_by_link(anon, token, name="Morgan Owner", sig=None, seen=None):
    page = text(anon.get(f"/d/{token}"))
    seen = seen or re.search(r'name="seen" type="hidden" value="([0-9a-f]{64})"', page).group(1)
    return anon.post(f"/d/{token}/approve", data={"name": name, "signature": sig or signature_png(),
                                                  "seen": seen}, follow_redirects=True)


def test_build_number_and_totals(app, signed_in):
    owner = signed_in("owner")
    cid, aid, pid, est_id = built(owner, app)
    year = None
    with app.app_context():
        year = local_today().year
        est = db.session.get(Estimate, est_id)
        assert est.number == f"EST-{year}-0001" and est.revision == 1 and est.status == "draft"
    page = text(owner.get(f"/estimates/{est_id}"))
    # 1.5 × 80 + 10 × 1.95 + 12.50 = 152.00
    assert "$152.00" in page and "1.5 h labor" in page and "Send link" not in page
    assert "Write your estimate terms" in page
    pdf = owner.get(f"/estimates/{est_id}/estimate.pdf")
    assert pdf.status_code == 200 and pdf.data[:5] == b"%PDF-"


def test_sending_needs_terms_and_stores_only_a_hash(app, signed_in):
    owner = signed_in("owner")
    cid, aid, pid, est_id = built(owner, app)
    resp = owner.post(f"/estimates/{est_id}/send", follow_redirects=True)
    assert "Write your estimate terms" in text(resp)
    assert db_query(app, "SELECT count(*) FROM doc_link") == [(0,)]
    set_terms(owner)
    token = send(owner, est_id)
    assert db_query(app, "SELECT status, terms_snapshot FROM estimate") == [("sent", TERMS)]
    dump = db_query(app, "SELECT string_agg(t::text, '') FROM (SELECT * FROM doc_link) t")[0][0]
    dump += db_query(app, "SELECT coalesce(string_agg(a::text, ''), '') FROM audit_log a")[0][0]
    assert token not in dump
    import hashlib
    assert hashlib.sha256(token.encode()).hexdigest() in dump


def test_link_page_is_public_private_and_counted(app, signed_in):
    owner = signed_in("owner")
    cid, aid, pid, est_id = built(owner, app)
    set_terms(owner)
    token = send(owner, est_id)
    anon = app.test_client()
    resp = anon.get(f"/d/{token}")
    page = text(resp)
    assert resp.status_code == 200 and "Monitor rebuild" in page and "$152.00" in page
    assert TERMS in page and "Deposit due before work starts" in page and "$50.00" in page
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert "noindex" in resp.headers["X-Robots-Tag"] and resp.headers["Cache-Control"] == "no-store"
    assert "Starlite Lanes Test" in page and "/work/" not in page and "/customers/" not in page
    assert db_query(app, "SELECT view_count FROM doc_link") == [(1,)]
    assert anon.get(f"/d/{token}/estimate.pdf").data[:5] == b"%PDF-"
    css = anon.get("/d/style/accent.css")
    assert css.mimetype == "text/css" and "#b0126f" in css.get_data(as_text=True)
    # anything else: the same 404, nothing about the estimate
    for bad in ("x", token[:-1] + ("A" if token[-1] != "A" else "B"), token + "x", "%00" * 43):
        resp = anon.get(f"/d/{bad}")
        assert resp.status_code == 404 and "Monitor rebuild" not in text(resp), bad
    link = db_query(app, "SELECT id FROM doc_link")[0][0]
    owner.post(f"/estimates/links/{link}/revoke")
    assert anon.get(f"/d/{token}").status_code == 404
    token2 = send(owner, est_id)
    with app.app_context():
        db.session.execute(db.text("SELECT 1"))
    db_query(app, "SELECT 1")
    # expired links 404 too
    with psycopg.connect(libpq(app.config["SQLALCHEMY_DATABASE_URI"]), autocommit=True) as c:
        c.execute("UPDATE doc_link SET expires_at = now() - interval '1 second' "
                  "WHERE revoked_at IS NULL")
    assert anon.get(f"/d/{token2}").status_code == 404


def test_approve_through_the_link(app, signed_in):
    owner = signed_in("owner")
    cid, aid, pid, est_id = built(owner, app)
    set_terms(owner)
    token = send(owner, est_id)
    anon = app.test_client()
    # no name, no signature, a blank signature: refused
    assert anon.post(f"/d/{token}/approve", data={"name": "", "signature": ""}).status_code == 422
    resp = approve_by_link(anon, token, sig=signature_png(blank=True))
    assert resp.status_code == 409 and "Sign in the box first" in text(resp)
    resp = approve_by_link(anon, token, sig="data:image/png;base64,bm90IGEgcG5n")
    assert resp.status_code == 409 and "couldn't be read" in text(resp)
    resp = anon.post(f"/d/{token}/approve", data={"name": "M", "signature": signature_png(),
                                                  "seen": "0" * 64})
    assert resp.status_code == 409 and "changed since it was opened" in text(resp)
    resp = approve_by_link(anon, token, name="Morgan Owner")
    page = text(resp)
    assert resp.status_code == 200 and "Approved" in page and "Morgan Owner" in page
    row = db_query(app, "SELECT status, approval_method, approved_name, approval_ip, "
                        "signature_sha256 FROM estimate")[0]
    assert row[:4] == ("approved", "link", "Morgan Owner", "127.0.0.1") and len(row[4]) == 64
    sig = anon.get(f"/d/{token}/signature.png")
    assert sig.status_code == 200 and sig.mimetype == "image/png"
    with Image.open(io.BytesIO(sig.data)) as img:
        assert img.mode == "RGB"                       # flattened on white
    assert owner.get(f"/signatures/{row[4]}.png").status_code == 200
    assert app.test_client().get(f"/signatures/{row[4]}.png").status_code == 302
    # once is enough
    again = approve_by_link(anon, token, seen="0" * 64)
    assert again.status_code in (409, 200) and "I approve" not in text(again)
    assert anon.get(f"/d/{token}/estimate.pdf").data[:5] == b"%PDF-"


def test_a_change_after_opening_blocks_approval(app, signed_in):
    owner = signed_in("owner")
    cid, aid, pid, est_id = built(owner, app)
    set_terms(owner)
    token = send(owner, est_id)
    anon = app.test_client()
    seen = re.search(r'name="seen" type="hidden" value="([0-9a-f]{64})"',
                     text(anon.get(f"/d/{token}"))).group(1)
    line = db_query(app, "SELECT id FROM estimate_line WHERE kind = 'fee'")[0][0]
    owner.post(f"/estimates/lines/{line}/edit", data={"description": "Shop supplies", "qty": "1",
                                                      "unit_price": "99"})
    resp = approve_by_link(anon, token, seen=seen)
    assert resp.status_code == 409 and "changed since it was opened" in text(resp)
    assert approve_by_link(anon, token).status_code == 200      # after a reload: fine


def test_frozen_in_the_database_after_approval(app, signed_in, tdb):
    owner = signed_in("owner")
    cid, aid, pid, est_id = built(owner, app)
    set_terms(owner)
    approve_by_link(app.test_client(), send(owner, est_id))
    with psycopg.connect(libpq(tdb.url("app"))) as c:
        for sql in ("UPDATE estimate_line SET unit_price = 0",
                    "DELETE FROM estimate_line",
                    "UPDATE estimate_job SET complaint = 'x'",
                    "INSERT INTO estimate_line (estimate_id, estimate_job_id, kind, description, "
                    "qty, unit_price) SELECT estimate_id, id, 'fee', 'x', 1, 1 FROM estimate_job",
                    "UPDATE estimate SET not_to_exceed = 1",
                    "UPDATE estimate SET approved_name = 'Someone else'",
                    "UPDATE estimate SET status = 'sent'",
                    "DELETE FROM estimate"):
            with pytest.raises((psycopg.errors.IntegrityConstraintViolation,
                                psycopg.errors.InsufficientPrivilege)):
                c.execute(sql)
            c.rollback()
    # the app can't change it either
    line = db_query(app, "SELECT id FROM estimate_line LIMIT 1")[0][0]
    resp = owner.post(f"/estimates/lines/{line}/edit", data={"description": "x", "qty": "1",
                                                             "unit_price": "1"},
                      follow_redirects=True)
    assert "make a revision" in text(resp)


def test_on_screen_and_verbal_approval_and_decline(app, signed_in):
    owner, tech = signed_in("owner"), signed_in("tech")
    cid, aid, pid, est_id = built(owner, app)
    page = text(tech.get(f"/estimates/{est_id}/sign"))
    seen = re.search(r'id="seen" name="seen" type="hidden" value="([0-9a-f]{64})"', page).group(1)
    resp = tech.post(f"/estimates/{est_id}/sign", data={"name": "Morgan Owner",
                                                         "signature": signature_png(), "seen": seen})
    assert resp.status_code == 302
    assert db_query(app, "SELECT approval_method, status FROM estimate") == [("on_screen", "approved")]
    # verbal on another one
    est2 = new_estimate(owner, cid, aid)
    owner.post(f"/estimates/{est2}/lines/other", data={
        "estimate_job_id": est_job(app, est2), "kind": "fee", "description": "Diagnosis",
        "qty": "1", "unit_price": "60"})
    owner.post(f"/estimates/{est2}/verbal", data={"name": "Morgan Owner", "note": "by phone 3pm"})
    row = db_query(app, "SELECT approval_method, approval_note, signature_sha256 FROM estimate "
                        "WHERE id = :i", i=est2)[0]
    assert row[0] == "verbal" and "by phone 3pm" in row[1] and row[2] is None
    est3 = new_estimate(owner, cid, aid)
    owner.post(f"/estimates/{est3}/decline", data={"reason": "Too much"})
    assert db_query(app, "SELECT status, decline_reason FROM estimate WHERE id = :i", i=est3) == \
        [("declined", "Too much")]


def test_decline_through_the_link_and_expiry(app, signed_in):
    owner = signed_in("owner")
    cid, aid, pid, est_id = built(owner, app)
    set_terms(owner)
    token = send(owner, est_id)
    anon = app.test_client()
    resp = anon.post(f"/d/{token}/decline", data={"reason": "Going elsewhere"})
    assert resp.status_code == 302 and resp.location == f"/d/{token}"
    page = text(anon.get(f"/d/{token}"))
    assert "Declined" in page and "I approve" not in page
    # an estimate past its date can't be approved
    est2 = new_estimate(owner, cid, aid)
    owner.post(f"/estimates/{est2}/lines/other", data={
        "estimate_job_id": est_job(app, est2), "kind": "fee", "description": "Diag", "qty": "1",
        "unit_price": "60"})
    token2 = send(owner, est2)
    db_query(app, "SELECT 1")
    with psycopg.connect(libpq(app.config["SQLALCHEMY_DATABASE_URI"]), autocommit=True) as c:
        c.execute("UPDATE estimate SET valid_until = current_date - 400 WHERE id = %s", (est2,))
    page = text(anon.get(f"/d/{token2}"))
    assert "was good until" in page and "I approve" not in page
    assert approve_by_link(anon, token2, seen="0" * 64).status_code == 409


def test_revisions(app, signed_in):
    owner = signed_in("owner")
    cid, aid, pid, est_id = built(owner, app)
    set_terms(owner)
    token = send(owner, est_id)
    owner.post(f"/estimates/{est_id}/revise")
    rows = db_query(app, "SELECT id, number, revision, status, supersedes_id FROM estimate "
                         "ORDER BY id")
    (r1, number, _, s1, _), (r2, number2, rev2, s2, sup) = rows
    assert (number2, rev2, s2, sup, s1) == (number, 2, "draft", r1, "superseded")
    assert db_query(app, "SELECT count(*) FROM estimate_line WHERE estimate_id = :e", e=r2) == \
        [(3,)]
    assert app.test_client().get(f"/d/{token}").status_code == 404      # old links stop
    page = text(owner.get(f"/estimates/{r2}"))
    assert f"{number} r2" in page
    resp = owner.post(f"/estimates/{r1}/revise", follow_redirects=True)
    assert "newer revision already exists" in text(resp)


def test_convert_to_a_work_order(app, signed_in):
    owner = signed_in("owner")
    cid, aid, pid, est_id = built(owner, app)
    owner.post(f"/parts/{pid}/receive", data={"qty": "4", "unit_cost": "0.40", "vendor_id": "None"})
    actual = activate(owner, app, "LAB-FIELD", "95")
    owner.post(f"/estimates/{est_id}/lines/service", data={
        "estimate_job_id": est_job(app, est_id), "service_id": actual, "labor_mode": "actual",
        "qty": "2"})
    set_terms(owner)
    approve_by_link(app.test_client(), send(owner, est_id))
    resp = owner.post(f"/estimates/{est_id}/convert", data={"kind": "bench",
                                                            "techs": [str(owner.user["id"])]},
                      follow_redirects=True)
    page = text(resp)
    assert "Opened WO-" in page and "order 6" in page          # 10 quoted, 4 on hand
    wo_id = db_query(app, "SELECT work_order_id FROM estimate")[0][0]
    assert db_query(app, "SELECT status FROM estimate") == [("converted",)]
    assert db_query(app, "SELECT not_to_exceed, kind, summary FROM work_order") == \
        [(None, "bench", "Monitor rebuild")]
    lines = db_query(app, "SELECT kind, labor_mode, qty, unit_price FROM wo_line ORDER BY id")
    assert lines == [("labor", "flat", D("1.5"), D(80)), ("fee", None, D(1), D("12.5")),
                     ("labor", "actual", D(0), D(95))]
    res = db_query(app, "SELECT id, qty, unit_price, status FROM reservation")
    assert [r[1:] for r in res] == [(D(10), D("1.95"), "reserved")]
    # issuing it uses the quoted price; only 4 are there
    resp = owner.post(f"/work/reservations/{res[0][0]}/issue", follow_redirects=True)
    assert "Only 4 on hand" in text(resp)
    # converted once only
    resp = owner.post(f"/estimates/{est_id}/convert", data={"kind": "bench"}, follow_redirects=True)
    assert "only an approved estimate converts" in text(resp)
    assert f"/work/{wo_id}" in text(owner.get(f"/estimates/{est_id}"))


def test_change_order_raises_the_limit(app, signed_in):
    owner = signed_in("owner")
    cid, aid, pid, est_id = built(owner, app, nte="200")
    set_terms(owner)
    approve_by_link(app.test_client(), send(owner, est_id))
    owner.post(f"/estimates/{est_id}/convert", data={"kind": "bench"})
    wo_id = db_query(app, "SELECT work_order_id FROM estimate")[0][0]
    owner.post(f"/work/{wo_id}/hold")
    owner.post(f"/estimates/{est_id}/revise")
    r2 = db_query(app, "SELECT id FROM estimate WHERE revision = 2")[0][0]
    with app.app_context():
        today = local_today()
    owner.post(f"/estimates/{r2}/edit", data={"summary": "Monitor rebuild + flyback",
                                              "site_id": "None", "not_to_exceed": "350",
                                              "valid_until": str(today)})
    approve_by_link(app.test_client(), send(owner, r2))
    assert db_query(app, "SELECT revision, status FROM estimate ORDER BY revision") == \
        [(1, "converted"), (2, "converted")]
    assert db_query(app, "SELECT not_to_exceed, status FROM work_order") == [(350, "in_progress")]


def test_techs_see_no_costs_and_viewers_cannot_act(app, signed_in):
    owner, tech, viewer = signed_in("owner"), signed_in("tech"), signed_in("viewer")
    cid, aid, pid, est_id = built(owner, app)
    assert "cost $" not in text(tech.get(f"/estimates/{est_id}"))
    assert "cost $" in text(owner.get(f"/estimates/{est_id}"))
    for path in (f"/estimates/{est_id}/send", f"/estimates/{est_id}/verbal",
                 f"/estimates/{est_id}/sign", f"/estimates/{est_id}/convert",
                 f"/estimates/{est_id}/revise", f"/estimates/{est_id}/lines/part"):
        assert viewer.post(path, data={}).status_code == 403, path


def test_public_pages_are_rate_limited(app, signed_in):
    owner = signed_in("owner")
    cid, aid, pid, est_id = built(owner, app)
    set_terms(owner)
    token = send(owner, est_id)
    anon = app.test_client()
    limiter.enabled = True
    limiter.reset()
    try:
        codes = [anon.get(f"/d/{token}").status_code for _ in range(61)]
        assert codes[:60] == [200] * 60 and codes[60] == 429
        decide = [anon.post(f"/d/{'A' * 43}/decline").status_code for _ in range(11)]
        assert decide[:10] == [404] * 10 and decide[10] == 429
    finally:
        limiter.reset()
        limiter.enabled = False
