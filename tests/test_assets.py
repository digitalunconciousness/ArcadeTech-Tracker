import re

import psycopg
import pytest

from app.assets.service import slugify
from conftest import db_query, libpq

TAG = re.compile(r"^s-[0-9]{4,}(-[a-z0-9]+)+$")


def customer(client, name="Pinball Bar Test"):
    resp = client.post("/customers/new", data={"kind": "business", "name": name,
                                               "payment_terms": "due_on_receipt"})
    return int(resp.location.rsplit("/", 1)[1])


def site(app, client, cid, name="Back room"):
    client.post(f"/customers/{cid}/sites/new", data={"name": name, "active": "y"})
    return db_query(app, "SELECT id FROM site WHERE customer_id = :c AND name = :n",
                    c=cid, n=name)[0][0]


def asset(client, cid, name="Ms. Pac-Man", **extra):
    data = {"name": name, "kind": "machine", "status": "in_service", **extra}
    resp = client.post(f"/assets/new?customer={cid}", data=data)
    assert resp.status_code == 302, resp.data[:600]
    return int(resp.location.rsplit("/", 1)[1])


def tag_of(app, aid):
    return db_query(app, "SELECT tag FROM asset WHERE id = :i", i=aid)[0][0]


@pytest.mark.parametrize("name,slug", [
    ("Ms. Pac-Man", "ms-pac-man"),
    ("Ms. Pac-Man™ (cocktail)", "ms-pac-man-cocktail"),
    ("Pac‑Man", "pac-man"),
    ("Café Racer", "cafe-racer"),
    ("Teenage Mutant Ninja Turtles 4-player", "teenage-mutant-ninja"),
    ("!!!", "asset"),
])
def test_slugify(name, slug):
    assert slugify(name) == slug


def test_create_asset_tag_and_created_event(app, signed_in):
    client = signed_in("tech")
    cid = customer(client)
    aid = asset(client, cid)
    tag = tag_of(app, aid)
    assert TAG.match(tag) and tag.endswith("-ms-pac-man") and len(tag) <= 40
    second = tag_of(app, asset(client, cid, name="Galaga"))
    assert int(second.split("-")[1]) > int(tag.split("-")[1])  # numbers only go up
    events = db_query(app, "SELECT kind, to_value FROM asset_event WHERE asset_id = :i", i=aid)
    assert events == [("created", "Pinball Bar Test")]


def test_tag_never_changes_even_in_the_database(app, tdb, signed_in):
    client = signed_in("owner")
    aid = asset(client, customer(client))
    tag = tag_of(app, aid)
    client.post(f"/assets/{aid}/edit", data={"name": "Renamed Machine", "kind": "machine"})
    assert tag_of(app, aid) == tag
    for url in (tdb.url("app"), tdb.url("owner")):
        with psycopg.connect(libpq(url), autocommit=True) as c, \
                pytest.raises(psycopg.errors.CheckViolation, match="never change"):
            c.execute("UPDATE asset SET tag = 's-9999-other' WHERE id = %s", (aid,))


@pytest.mark.parametrize("statement", [
    "DELETE FROM asset",
    "DELETE FROM asset_event",
    "UPDATE asset_event SET note = 'rewritten'",
])
def test_assets_and_history_cannot_be_deleted_or_rewritten(app, tdb, signed_in, statement):
    client = signed_in("owner")
    asset(client, customer(client))
    with psycopg.connect(libpq(tdb.url("app")), autocommit=True) as c, \
            pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute(statement)


def test_database_refuses_a_parent_loop(app, signed_in):
    client = signed_in("owner")
    cid = customer(client)
    machine = asset(client, cid, name="Robotron")
    board = asset(client, cid, name="Robotron CPU", kind="board", parent_asset_id=str(machine))
    with pytest.raises(Exception, match="inside one of its own parts"):
        db_query(app, "UPDATE asset SET parent_asset_id = :b WHERE id = :m", b=board, m=machine)


def test_parent_and_site_must_share_the_owner(app, signed_in):
    client = signed_in("owner")
    a, b = customer(client, "Owner A Test"), customer(client, "Owner B Test")
    machine_a = asset(client, a)
    site_b = site(app, client, b)
    board_b = asset(client, b, name="Loose board", kind="board")
    with pytest.raises(Exception, match="fk_asset_parent_same_customer"):
        db_query(app, "UPDATE asset SET parent_asset_id = :p WHERE id = :c", p=machine_a, c=board_b)
    with pytest.raises(Exception, match="fk_asset_site_same_customer"):
        db_query(app, "UPDATE asset SET site_id = :s WHERE id = :a", s=site_b, a=machine_a)


def test_edit_offers_no_parent_inside_itself(app, signed_in):
    client = signed_in("owner")
    cid = customer(client)
    machine = asset(client, cid, name="Joust")
    board = asset(client, cid, name="Joust CPU", kind="board", parent_asset_id=str(machine))
    resp = client.post(f"/assets/{machine}/edit", data={
        "name": "Joust", "kind": "machine", "parent_asset_id": str(board)})
    assert resp.status_code == 200 and b"Not a valid choice" in resp.data


def test_status_changes_write_events_and_track_shop_time(app, signed_in):
    client = signed_in("tech")
    aid = asset(client, customer(client))
    client.post(f"/assets/{aid}/status", data={"status": "in_shop", "note": "dropped off"})
    assert db_query(app, "SELECT in_shop_since IS NOT NULL FROM asset WHERE id = :i", i=aid)[0][0]
    client.post(f"/assets/{aid}/status", data={"status": "in_service"})
    assert db_query(app, "SELECT in_shop_since FROM asset WHERE id = :i", i=aid)[0][0] is None
    kinds = db_query(app, "SELECT kind, from_value, to_value FROM asset_event "
                          "WHERE asset_id = :i ORDER BY id", i=aid)
    assert kinds[1:] == [("intake", "in_service", "in_shop"), ("returned", "in_shop", "in_service")]


def test_transfer_moves_whats_inside_and_records_it(app, signed_in):
    client = signed_in("owner")
    seller, buyer = customer(client, "Seller Test"), customer(client, "Buyer Test")
    buyer_site = site(app, client, buyer, "Arcade floor")
    machine = asset(client, seller, name="Defender")
    board = asset(client, seller, name="Defender ROM board", kind="board",
                  parent_asset_id=str(machine))
    resp = client.post(f"/assets/{machine}/transfer", data={
        "customer_id": str(buyer), "site_id": str(buyer_site), "note": "sold"})
    assert resp.status_code == 302
    rows = db_query(app, "SELECT id, customer_id, site_id, parent_asset_id FROM asset "
                         "WHERE id IN (:m, :b) ORDER BY id", m=machine, b=board)
    assert rows == [(machine, buyer, buyer_site, None), (board, buyer, buyer_site, machine)]
    events = db_query(app, "SELECT asset_id, from_value, to_value FROM asset_event "
                           "WHERE kind = 'ownership_change' ORDER BY asset_id")
    assert events == [(machine, "Seller Test", "Buyer Test"), (board, "Seller Test", "Buyer Test")]


def test_board_sold_alone_leaves_its_machine(app, signed_in):
    client = signed_in("owner")
    seller, buyer = customer(client, "Seller Two Test"), customer(client, "Buyer Two Test")
    machine = asset(client, seller, name="Tempest")
    board = asset(client, seller, name="Tempest AVG", kind="board", parent_asset_id=str(machine))
    client.post(f"/assets/{board}/transfer", data={"customer_id": str(buyer)})
    assert db_query(app, "SELECT customer_id, parent_asset_id FROM asset WHERE id = :b",
                    b=board) == [(buyer, None)]
    assert db_query(app, "SELECT customer_id FROM asset WHERE id = :m", m=machine) == [(seller,)]


def test_transfer_refuses_another_customers_site(app, signed_in):
    client = signed_in("owner")
    a, b = customer(client, "Mine Test"), customer(client, "Theirs Test")
    their_site = site(app, client, b)
    aid = asset(client, a)
    resp = client.post(f"/assets/{aid}/transfer", data={"customer_id": str(a),
                                                        "site_id": str(their_site)})
    assert resp.status_code == 200 and b"different customer" in resp.data


def test_g_link_and_no_label_pages(app, signed_in):
    client = signed_in("tech")
    aid = asset(client, customer(client))
    tag = tag_of(app, aid)
    resp = client.get(f"/g/{tag.upper()}")
    assert resp.status_code == 302 and resp.location == f"/assets/{aid}"
    assert client.get("/g/s-0000-nope").status_code == 404
    assert app.test_client().get(f"/g/{tag}").location.startswith("/login")
    # no QR labels on customer machines (owner, 2026-10-08)
    assert client.get("/labels").status_code == 404
    page = client.get(f"/assets/{aid}").get_data(as_text=True)
    assert "<svg" not in page and tag in page


def test_viewer_cannot_change_assets(app, signed_in):
    owner = signed_in("owner")
    aid = asset(owner, customer(owner))
    viewer = signed_in("viewer")
    assert viewer.get(f"/assets/{aid}").status_code == 200
    for path in (f"/assets/{aid}/status", f"/assets/{aid}/note",
                 f"/assets/{aid}/transfer", f"/assets/{aid}/edit"):
        assert viewer.post(path, data={}).status_code == 403, path
