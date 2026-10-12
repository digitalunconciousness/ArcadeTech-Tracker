"""Saved parts links (Phase 5a): cleaning and saving a link, Android's share target, search,
"Parts you'll buy" on an estimate (frozen with it, on the customer's page), parts the
customer is buying on a work order (waiting on parts, Arrived), and sending links with a
record on the comm log."""

import json
import re
from decimal import Decimal as D

import psycopg
import pytest
from test_assets import asset, customer
from test_estimates import approve_by_link, built, est_job, send, set_terms
from test_work import first_job, new_wo, text

from app.partlinks.service import LinkError, find_url, normalize
from conftest import db_query, libpq

CAP_KIT = "https://www.example.com/cap-kits/k7000?utm_source=news&utm_medium=email&fbclid=x1&sku=K7"


def save(client, url=CAP_KIT, title="K7000 cap kit", **extra):
    resp = client.post("/links/new", data={"url": url, "title": title, "part_id": "None",
                                           **extra})
    assert resp.status_code == 302, text(resp)[-1500:]
    return int(resp.location.rsplit("/", 1)[1])


def add_vendor(client, name="Example Parts", website="https://www.example.com/"):
    resp = client.post("/vendors/new", data={"name": name, "website": website})
    assert resp.status_code == 302, text(resp)[-800:]
    return int(resp.location.rsplit("/", 1)[1])


@pytest.mark.parametrize("raw,clean", [
    (CAP_KIT, "https://www.example.com/cap-kits/k7000?sku=K7"),
    ("HTTPS://Shop.Example.NET:443/a?gclid=1", "https://shop.example.net/a"),
    ("http://example.org:8080/x", "http://example.org:8080/x"),
    ("Look at this: https://example.com/p/1). Thanks", "https://example.com/p/1"),
    ("https://example.com", "https://example.com/"),
    ("https://example.com/p?srsltid=abc&color=red#specs", "https://example.com/p?color=red#specs"),
])
def test_normalize(raw, clean):
    assert normalize(raw) == clean


@pytest.mark.parametrize("raw", ["javascript:alert(1)", "data:text/html,hi", "ftp://example.com/x",
                                 "example.com/no-scheme", "https://localhost/x", ""])
def test_normalize_refuses(raw):
    with pytest.raises(LinkError):
        normalize(raw)


def test_find_url_looks_in_every_field():
    # Android puts the shared page's URL in `text`, never in `url`
    assert find_url(None, "Cap kit https://example.com/k?a=1", "Cap kit") == \
        "https://example.com/k?a=1"
    assert find_url("", "", "no link here") is None


def test_save_matches_vendor_and_refuses_duplicates(app, signed_in):
    tech = signed_in("tech")
    vid = add_vendor(tech)
    lid = save(tech, fits="Wells-Gardner K7000", price_seen="24.95")
    row = db_query(app, "SELECT url, domain, vendor_id, fits, price_seen, price_seen_on IS NOT NULL "
                        "FROM part_link")[0]
    assert row == ("https://www.example.com/cap-kits/k7000?sku=K7", "example.com", vid,
                   "Wells-Gardner K7000", D("24.95"), True)
    # the same page again, with other tracking tags: refused, pointing at the saved one
    resp = tech.post("/links/new", data={"url": CAP_KIT.replace("news", "other"), "title": "x",
                                         "part_id": "None"}, follow_redirects=True)
    assert "already saved as “K7000 cap kit”" in text(resp)
    assert db_query(app, "SELECT count(*) FROM part_link") == [(1,)]
    page = text(tech.get(f"/links/{lid}"))
    assert "Example Parts" in page and 'rel="noopener noreferrer nofollow"' in page
    # a script URL can't be saved by the app, and the database refuses one too
    resp = tech.post("/links/new", data={"url": "javascript:alert(1)", "title": "x",
                                         "part_id": "None"})
    assert resp.status_code == 200 and "isn't a web address" in text(resp)


def test_database_refuses_non_http_links(app, tdb):
    with psycopg.connect(libpq(tdb.url("app"))) as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO part_link (url, title, domain) "
                      "VALUES ('javascript:alert(1)', 'x', 'x')")
        c.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM part_link")


def test_share_target_prefills_the_form(app, signed_in):
    tech = signed_in("tech")
    resp = tech.get("/links/new", query_string={"title": "Cap Kit for K7000 – Example Parts",
                                                "text": CAP_KIT})
    page = text(resp)
    assert resp.status_code == 200
    assert 'value="https://www.example.com/cap-kits/k7000?sku=K7"' in page
    assert 'value="Cap Kit for K7000 – Example Parts"' in page
    # sharing a page that's already saved opens it
    lid = save(tech)
    resp = tech.get("/links/new", query_string={"text": f"look {CAP_KIT}"})
    assert resp.status_code == 302 and resp.location.endswith(f"/links/{lid}")


def test_manifest_is_public_and_shares_to_the_save_form(app, signed_in):
    resp = app.test_client().get("/manifest.webmanifest")
    assert resp.status_code == 200 and resp.mimetype == "application/manifest+json"
    body = json.loads(resp.data)
    assert body["share_target"] == {"action": "/links/new", "method": "GET",
                                    "params": {"title": "title", "text": "text", "url": "url"}}
    assert body["display"] == "standalone" and {i["sizes"] for i in body["icons"]} == \
        {"192x192", "512x512"}
    for icon in body["icons"]:
        assert app.test_client().get(icon["src"]).status_code == 200
    page = text(signed_in("viewer").get("/links"))
    assert 'rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials"' in page


def test_search_and_roles(app, signed_in):
    tech = signed_in("tech")
    add_vendor(tech, "Arcade Bits", "https://arcadebits.example.org")
    save(tech, fits="Wells-Gardner K7000")
    save(tech, "https://arcadebits.example.org/t-molding/3-4-red", "T-molding 3/4 red")
    off = save(tech, "https://example.net/old", "Old flyback")
    tech.post(f"/links/{off}/edit", data={"url": "https://example.net/old", "title": "Old flyback",
                                          "part_id": "None"})          # active unticked: off
    viewer = signed_in("viewer")

    def found(q, **extra):
        return re.findall(r'<strong>([^<]+)</strong>', text(viewer.get("/links", query_string={
            "q": q, **extra})))

    assert found("k7000 cap") == ["K7000 cap kit"]
    assert found("wells gardner") == ["K7000 cap kit"]     # each word on its own
    assert found("k7000 molding") == []                    # but every word
    assert found("arcade bits") == ["T-molding 3/4 red"]     # by vendor name
    assert found("flyback") == []
    assert found("flyback", all="1") == ["Old flyback"]
    assert found(CAP_KIT) == ["K7000 cap kit"]               # paste a saved URL
    assert "Not saved yet" in text(viewer.get("/links", query_string={"q": "https://example.com/new"}))
    # viewers read, never save
    assert viewer.get("/links/new").status_code == 403
    assert viewer.post("/links/new", data={"url": CAP_KIT, "title": "x"}).status_code == 403
    # global search finds links
    assert "T-molding 3/4 red" in text(viewer.get("/search", query_string={"q": "molding"}))


def test_parts_they_buy_on_an_estimate(app, signed_in, tdb):
    owner = signed_in("owner")
    cid, aid, pid, est_id = built(owner, app)
    lid = save(owner, price_seen="24.95")
    nolink = save(owner, "https://example.org/lock", "Coin door lock")
    job = est_job(app, est_id)
    pick = text(owner.get("/links/pick", query_string={"for": f"est:{job}", "q": "cap kit"}))
    assert "K7000 cap kit" in pick and f'action="/estimates/jobs/{job}/part-links"' in pick
    for link, qty in ((lid, "2"), (nolink, "1")):
        resp = owner.post(f"/estimates/jobs/{job}/part-links",
                          data={"part_link_id": str(link), "qty": qty})
        assert resp.status_code == 302 and resp.location.endswith(f"/estimates/{est_id}#lines")
    # editing the saved link later doesn't change what the estimate says
    owner.post(f"/links/{lid}/edit", data={"url": CAP_KIT, "title": "Renamed", "part_id": "None",
                                           "active": "y", "price_seen": "30"})
    rows = db_query(app, "SELECT title, qty, price_seen FROM estimate_part_link ORDER BY id")
    assert rows == [("K7000 cap kit", D(2), D("24.95")), ("Coin door lock", D(1), None)]
    set_terms(owner)
    token = send(owner, est_id)
    page = text(app.test_client().get(f"/d/{token}"))
    assert "Parts you'll buy" in page and "K7000 cap kit" in page
    assert 'href="https://www.example.com/cap-kits/k7000?sku=K7"' in page
    assert "about $49.90, plus 1 without a price" in page
    assert "$152.00" in page                                 # our total leaves them out
    assert app.test_client().get(f"/d/{token}/estimate.pdf").data[:5] == b"%PDF-"
    approve_by_link(app.test_client(), token)
    # frozen with the approval
    with psycopg.connect(libpq(tdb.url("app"))) as c:
        for sql in ("UPDATE estimate_part_link SET qty = 5", "DELETE FROM estimate_part_link",
                    "INSERT INTO estimate_part_link (estimate_id, estimate_job_id, part_link_id, "
                    "title, url) SELECT estimate_id, estimate_job_id, part_link_id, title, url "
                    "FROM estimate_part_link"):
            with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
                c.execute(sql)
            c.rollback()
    # a revision carries them over
    resp = owner.post(f"/estimates/{est_id}/revise")
    new_id = int(resp.location.rsplit("/", 1)[1])
    assert db_query(app, "SELECT title FROM estimate_part_link WHERE estimate_id = :e ORDER BY id",
                    e=new_id) == [("K7000 cap kit",), ("Coin door lock",)]


def test_convert_waits_on_the_customers_parts_until_they_arrive(app, signed_in):
    owner = signed_in("owner")
    cid, aid, pid, est_id = built(owner, app)
    lid = save(owner)
    owner.post(f"/estimates/jobs/{est_job(app, est_id)}/part-links",
               data={"part_link_id": str(lid), "qty": "1"})
    set_terms(owner)
    approve_by_link(app.test_client(), send(owner, est_id))
    resp = owner.post(f"/estimates/{est_id}/convert",
                      data={"kind": "bench", "techs": [str(owner.user["id"])], "wait": "y"})
    assert resp.status_code == 302
    wo_id = db_query(app, "SELECT work_order_id FROM estimate WHERE id = :e", e=est_id)[0][0]
    assert db_query(app, "SELECT status FROM work_order") == [("waiting_parts",)]
    line = db_query(app, "SELECT id, description, customer_supplied, awaiting, part_link_id, "
                         "unit_price FROM wo_line WHERE part_link_id IS NOT NULL")[0]
    assert line[1:] == ("K7000 cap kit", True, True, lid, D(0))
    job_page = text(owner.get(f"/work/jobs/{first_job(app, wo_id)}"))
    assert "on its way" in job_page and f"/work/lines/{line[0]}/arrived" in job_page
    assert "warranty 90 d" in job_page             # labor with the customer's part
    # not offered for billing while it's on its way
    invoice_page = text(owner.get(f"/work/{wo_id}/invoice"))
    assert "K7000 cap kit" not in invoice_page
    resp = owner.post(f"/work/lines/{line[0]}/arrived", follow_redirects=True)
    # the 10 caps quoted on the estimate are set aside but not in stock: still waiting
    assert "back In progress" not in text(resp)
    assert "CAP-470-25 × 10 set aside, not in stock" in text(owner.get(f"/work/{wo_id}"))
    assert db_query(app, "SELECT status FROM work_order") == [("waiting_parts",)]
    resp = owner.post(f"/parts/{pid}/receive", data={"qty": "10", "unit_cost": "0.40",
                                                     "vendor_id": "None"}, follow_redirects=True)
    assert "back In progress" in text(resp)
    assert db_query(app, "SELECT status FROM work_order") == [("in_progress",)]
    assert db_query(app, "SELECT awaiting FROM wo_line WHERE id = :i", i=line[0]) == [(False,)]
    assert "K7000 cap kit" in text(owner.get(f"/work/{wo_id}/invoice"))
    resp = owner.post(f"/work/lines/{line[0]}/arrived", follow_redirects=True)
    assert "already here" in text(resp)


def test_customer_buys_on_a_work_order_and_send_links(app, signed_in):
    tech = signed_in("tech")
    cid = customer(tech, "Starlite Lanes Test")
    wo_id = new_wo(tech, cid, asset(tech, cid, "Galaga"))
    job = first_job(app, wo_id)
    cap, lock = save(tech), save(tech, "https://example.org/lock", "Coin door lock")
    for link, wait in ((cap, "y"), (lock, None)):
        data = {"part_link_id": str(link), "qty": "1"}
        if wait:
            data["wait"] = wait
        resp = tech.post(f"/work/jobs/{job}/part-links", data=data)
        assert resp.status_code == 302 and resp.location.endswith("#customer-parts")
    assert db_query(app, "SELECT status FROM work_order") == [("waiting_parts",)]
    # one arrives: still waiting on the other
    first = db_query(app, "SELECT id FROM wo_line WHERE part_link_id = :l", l=cap)[0][0]
    resp = tech.post(f"/work/lines/{first}/arrived", follow_redirects=True)
    assert "back In progress" not in text(resp)
    assert db_query(app, "SELECT status FROM work_order") == [("waiting_parts",)]
    # send links: the message lists what's still on its way, and the send is logged
    page = text(tech.get(f"/work/{wo_id}/send-links"))
    assert "Coin door lock: https://example.org/lock" in page
    assert "K7000 cap kit: https://" not in page
    message = "Here's the lock to order: Coin door lock: https://example.org/lock"
    resp = tech.post(f"/work/{wo_id}/send-links", data={"how": "text", "message": message})
    assert resp.status_code == 302
    entry = db_query(app, "SELECT customer_id, kind, summary FROM comm_log")[0]
    assert entry[:2] == (cid, "text") and message in entry[2] and "WO-" in entry[2]
    usage = text(tech.get(f"/links/{lock}"))
    assert "Starlite Lanes Test" in usage and "Sent" in usage and "on its way" in usage
    # viewers can't send or add
    viewer = signed_in("viewer")
    assert viewer.post(f"/work/{wo_id}/send-links", data={"how": "text", "message": "x"}) \
        .status_code == 403
    assert viewer.post(f"/work/jobs/{job}/part-links", data={"part_link_id": str(cap),
                                                             "qty": "1"}).status_code == 403


def test_switched_off_links_and_closed_targets(app, signed_in):
    owner = signed_in("owner")
    cid, aid, pid, est_id = built(owner, app)
    lid = save(owner)
    owner.post(f"/links/{lid}/edit", data={"url": CAP_KIT, "title": "K7000 cap kit",
                                           "part_id": "None"})          # switched off
    job = est_job(app, est_id)
    resp = owner.post(f"/estimates/jobs/{job}/part-links", data={"part_link_id": str(lid),
                                                                 "qty": "1"},
                      follow_redirects=True)
    assert "switched off" in text(resp)
    assert db_query(app, "SELECT count(*) FROM estimate_part_link") == [(0,)]
    # a job with links can still be removed while the estimate is a draft
    owner.post(f"/links/{lid}/edit", data={"url": CAP_KIT, "title": "K7000 cap kit",
                                           "part_id": "None", "active": "y"})
    owner.post(f"/estimates/{est_id}/jobs", data={"asset_id": "None", "complaint": "Second"})
    second = db_query(app, "SELECT max(id) FROM estimate_job")[0][0]
    owner.post(f"/estimates/jobs/{second}/part-links", data={"part_link_id": str(lid),
                                                             "qty": "1"})
    owner.post(f"/estimates/jobs/{second}/delete")
    assert db_query(app, "SELECT count(*) FROM estimate_part_link") == [(0,)]
    # an approved estimate's pick page sends you back
    set_terms(owner)
    approve_by_link(app.test_client(), send(owner, est_id))
    resp = owner.get("/links/pick", query_string={"for": f"est:{job}"}, follow_redirects=True)
    assert "can't change any more" in text(resp)
    assert owner.get("/links/pick", query_string={"for": "nope:1"}).status_code == 404
