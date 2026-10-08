"""Photos (re-encoded: no EXIF, rotation applied, junk refused), readings (pass worked out
by Postgres), and the claim ticket and service report PDFs."""

import io
from decimal import Decimal as D
from pathlib import Path

import psycopg
import pytest
from PIL import Image
from test_parts import add_part
from test_work import activate, setup, text

from app.work import docs
from conftest import db_query, libpq

MARKER = "GPS-35.4676N-97.5164W"


def jpeg_with_exif(size=(64, 48), orientation=6):
    exif = Image.Exif()
    exif[0x010E] = MARKER          # ImageDescription
    exif[0x010F] = "PhoneMaker"    # Make
    exif[0x0112] = orientation     # rotate 90° CW when shown
    out = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(out, "JPEG", exif=exif)
    return out.getvalue()


def upload(client, job_id, data, name="photo.jpg", caption="Before"):
    return client.post(f"/work/jobs/{job_id}/photos", data={
        "photo": (io.BytesIO(data), name), "caption": caption},
        content_type="multipart/form-data", follow_redirects=True)


def test_photo_is_reencoded_without_exif(app, signed_in):
    tech = signed_in("tech")
    cid, aid, wo_id, job_id = setup(tech, app)
    raw = jpeg_with_exif()
    assert MARKER.encode() in raw
    resp = upload(tech, job_id, raw)
    assert "Photo added." in text(resp)
    sha, width, height, size = db_query(app, "SELECT sha256, width, height, size_bytes "
                                             "FROM attachment")[0]
    stored = Path(app.config["SHOP_FILES_DIR"]) / sha[:2] / f"{sha}.jpg"
    data = stored.read_bytes()
    assert MARKER.encode() not in data and b"PhoneMaker" not in data and len(data) == size
    with Image.open(stored) as img:
        assert dict(img.getexif()) == {}
        assert img.size == (48, 64) == (width, height)          # the rotation was applied
    assert (stored.parent / f"{sha}-thumb.jpg").exists()
    # served to a signed-in user, by hash, and only hashes the database knows
    got = tech.get(f"/files/{sha}.jpg")
    assert got.status_code == 200 and got.data == data and got.mimetype == "image/jpeg"
    assert tech.get(f"/files/{sha}.jpg?thumb=1").status_code == 200
    assert tech.get(f"/files/{'0' * 64}.jpg").status_code == 404
    assert tech.get("/files/..%2F..%2Fetc%2Fpasswd.jpg").status_code == 404
    anon = app.test_client()
    assert anon.get(f"/files/{sha}.jpg").status_code == 302              # to the login page
    # same photo again: same file, a second row
    upload(tech, job_id, raw, caption="Again")
    assert db_query(app, "SELECT count(DISTINCT sha256), count(*) FROM attachment") == [(1, 2)]
    first = db_query(app, "SELECT min(id) FROM attachment")[0][0]
    tech.post(f"/work/photos/{first}/delete")
    assert db_query(app, "SELECT caption FROM attachment") == [("Again",)]


@pytest.mark.parametrize("payload, name", [
    (b"%PDF-1.7 not a photo", "photo.jpg"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "photo.svg"),
    (b"\xff\xd8\xff\xe0 truncated jpeg", "photo.jpg"),
])
def test_non_images_are_refused(app, signed_in, payload, name):
    tech = signed_in("tech")
    cid, aid, wo_id, job_id = setup(tech, app)
    assert "isn't a photo this app can read" in text(upload(tech, job_id, payload, name))
    assert db_query(app, "SELECT count(*) FROM attachment") == [(0,)]


def test_decompression_bomb_and_oversize_are_refused(app, signed_in):
    tech = signed_in("tech")
    cid, aid, wo_id, job_id = setup(tech, app)
    bomb = io.BytesIO()
    Image.new("1", (12000, 12000)).save(bomb, "PNG")        # 144 MP, a few KB on disk
    assert "isn't a photo this app can read" in text(upload(tech, job_id, bomb.getvalue(),
                                                            "bomb.png"))
    big = tech.post(f"/work/jobs/{job_id}/photos", data={
        "photo": (io.BytesIO(b"\0" * (17 * 1024 * 1024)), "big.jpg")},
        content_type="multipart/form-data")
    assert big.status_code == 413
    assert db_query(app, "SELECT count(*) FROM attachment") == [(0,)]


@pytest.mark.parametrize("value, lo, hi, passed", [
    ("4.75", "4.75", "5.25", True), ("5.25", "4.75", "5.25", True),
    ("4.749", "4.75", "5.25", False), ("5.2501", "4.75", "5.25", False),
    ("12.1", "11.5", "", True), ("11.4", "11.5", "", False),
    ("0.4", "", "0.5", True), ("3", "", "", None),
])
def test_reading_pass_is_computed(app, signed_in, value, lo, hi, passed):
    tech = signed_in("tech")
    cid, aid, wo_id, job_id = setup(tech, app)
    resp = tech.post(f"/work/jobs/{job_id}/readings", data={
        "test_point": "+5 V at edge", "phase": "as_found", "value": value, "unit": "V",
        "spec_lo": lo, "spec_hi": hi})
    assert resp.status_code == 302
    assert db_query(app, "SELECT passed FROM manual_reading") == [(passed,)]


def test_reading_validation_and_append_only(app, signed_in, tdb):
    tech = signed_in("tech")
    cid, aid, wo_id, job_id = setup(tech, app)
    resp = tech.post(f"/work/jobs/{job_id}/readings", data={
        "test_point": "+5 V", "phase": "as_found", "value": "5", "unit": "V",
        "spec_lo": "5.25", "spec_hi": "4.75"}, follow_redirects=True)
    assert "Below the low end" in text(resp)
    tech.post(f"/work/jobs/{job_id}/readings", data={
        "test_point": "+5 V", "phase": "as_found", "value": "4.61", "unit": "V"})
    with psycopg.connect(libpq(tdb.url("app"))) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE manual_reading SET value = 5")
    reading = db_query(app, "SELECT id FROM manual_reading")[0][0]
    tech.post(f"/work/readings/{reading}/delete")
    assert db_query(app, "SELECT count(*) FROM manual_reading") == [(0,)]


def test_reading_rows_pivot():
    from types import SimpleNamespace as N

    def r(i, point, phase, value, lo=None, hi=None):
        return N(id=i, taken_at=i, test_point=point, phase=phase, value=D(value), unit="V",
                 spec_lo=None if lo is None else D(lo), spec_hi=None if hi is None else D(hi))

    rows = docs.reading_rows([
        r(1, "+5 V", "as_found", "4.61", "4.75", "5.25"),
        r(2, "+5 V", "during", "4.90"),
        r(3, "+5 V", "as_left", "5.02", "4.750", "5.250"),
        r(4, "-5 V", "as_found", "-5.1", None, "-4.75"),
        r(5, "+12 V", "as_left", "12.1", "11.5"),
    ])
    by = {row["test_point"]: row for row in rows}
    assert by["+5 V"]["as_found"].value == D("4.61") and by["+5 V"]["as_left"].value == D("5.02")
    assert by["+5 V"]["spec"] == "4.75–5.25 V"
    assert by["-5 V"]["spec"] == "≤ -4.75 V" and "as_left" not in by["-5 V"]
    assert by["+12 V"]["spec"] == "≥ 11.5 V"


def full_job(owner, app):
    cid, aid, wo_id, job_id = setup(owner, app)
    bench = activate(owner, app, "LAB-BENCH", "80")
    pid = add_part(owner)
    owner.post(f"/parts/{pid}/receive", data={"qty": "10", "unit_cost": "0.40", "vendor_id": "None",
                                              "date_code": "2219"})
    owner.post(f"/work/jobs/{job_id}/edit", data={
        "status": "done", "complaint": "Dead screen", "cause": "Dried-out caps on the chassis",
        "correction": "Cap kit; adjusted focus", "received_at": "2026-10-08T14:30",
        "received_via": "drop_off", "condition": "Scratched side",
        "accessories": ["Harness"], "accessories_note": "Spare fuse"})
    owner.post(f"/work/jobs/{job_id}/lines/part", data={"part_id": pid, "qty": "2", "billable": "y"})
    owner.post(f"/work/jobs/{job_id}/lines/service", data={
        "service_id": bench, "labor_mode": "flat", "qty": "1.5", "tech_user_id": "None",
        "billable": "y"})
    owner.post(f"/work/jobs/{job_id}/time", data={"started_at": "2026-10-08T15:00", "minutes": "84",
                                                  "billable": "y"})
    for phase, value in (("as_found", "4.61"), ("as_left", "5.02")):
        owner.post(f"/work/jobs/{job_id}/readings", data={
            "test_point": "+5 V at edge", "phase": phase, "value": value, "unit": "V",
            "spec_lo": "4.75", "spec_hi": "5.25"})
    return wo_id


def test_documents_render(app, signed_in, monkeypatch):
    owner, tech = signed_in("owner"), signed_in("tech")
    owner.post("/settings/", data={"business_name": "ArcadeTech Tracker", "doc_accent_color":
                                   "#b0126f", "claim_terms": "Units left 90 days are abandoned.",
                                   "warranty_terms": "Warranty void if opened by others."})
    wo_id = full_job(owner, app)
    for path in (f"/work/{wo_id}/claim-ticket.pdf", f"/work/{wo_id}/service-report.pdf"):
        resp = tech.get(path)
        assert resp.status_code == 200 and resp.mimetype == "application/pdf", path
        assert resp.data[:5] == b"%PDF-" and len(resp.data) > 2000

    seen = {}
    monkeypatch.setattr(docs, "render_pdf", lambda template, **ctx: seen.setdefault(
        template, docs.render_template(template, doc_css="css/doc.css", wo_kinds=docs.WO_KINDS,
                                       received_via=docs.RECEIVED_VIA, **ctx)).encode())
    tech.get(f"/work/{wo_id}/claim-ticket.pdf")
    tech.get(f"/work/{wo_id}/service-report.pdf")
    claim, report = seen["docs/claim_ticket.html"], seen["docs/service_report.html"]
    assert "Scratched side" in claim and "Harness; Spare fuse" in claim
    assert "Units left 90 days are abandoned." in claim and "dropped off" in claim
    assert "Dried-out caps" in report and "4.75–5.25 V" in report
    assert "4.61 V" in report and "OUT" in report and "5.02 V" in report and "OK" in report
    assert "2219" in report and "365 days" in report and "1.40" in report   # 84 min of labor
    assert "Warranty void if opened by others." in report
    for doc in (claim, report):                                 # no prices, no costs
        assert "$" not in doc and "0.40" not in doc
