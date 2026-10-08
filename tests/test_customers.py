import psycopg
import pytest

from conftest import db_query, libpq


def new_customer(client, **extra):
    data = {"kind": "business", "name": "Neon Tavern Test", "phone": "(555) 010-0123",
            "payment_terms": "net_15", **extra}
    resp = client.post("/customers/new", data=data)
    assert resp.status_code == 302, resp.data[:400]
    return int(resp.location.rsplit("/", 1)[1])


def test_shop_customer_seeded(app):
    rows = db_query(app, "SELECT name, is_shop FROM customer WHERE is_shop")
    assert rows == [("ArcadeTech Tracker", True)]


def test_only_one_shop_customer(app):
    with pytest.raises(Exception, match="uq_customer_is_shop"):
        db_query(app, "INSERT INTO customer (kind, name, is_shop) VALUES ('business', 'x', true)")


def test_create_view_edit_customer(signed_in):
    client = signed_in("tech")
    cid = new_customer(client)
    assert "Neon Tavern Test" in client.get("/customers").get_data(as_text=True)  # new = active
    page = client.get(f"/customers/{cid}")
    assert page.status_code == 200 and b"Neon Tavern Test" in page.data
    resp = client.post(f"/customers/{cid}/edit", data={
        "kind": "business", "name": "Neon Tavern Test", "dba": "The Neon",
        "payment_terms": "net_30"})  # active unticked: deactivated
    assert resp.status_code == 302
    listing = client.get("/customers").get_data(as_text=True)
    assert "Neon Tavern Test" not in listing
    assert "Neon Tavern Test (The Neon)" in client.get("/customers?all=1").get_data(as_text=True)


def test_phone_digits_generated(app, signed_in):
    cid = new_customer(signed_in("owner"), phone="555.010.0123")
    assert db_query(app, "SELECT phone_digits FROM customer WHERE id = :i", i=cid) == [("5550100123",)]


def test_viewer_reads_but_cannot_write(signed_in):
    owner = signed_in("owner")
    cid = new_customer(owner)
    viewer = signed_in("viewer")
    assert viewer.get(f"/customers/{cid}").status_code == 200
    assert b"Add customer" not in viewer.get("/customers").data
    assert viewer.get("/customers/new").status_code == 403
    assert viewer.post(f"/customers/{cid}/log", data={"kind": "call", "summary": "x"}).status_code == 403


def test_contacts_sites_and_log(app, signed_in):
    client = signed_in("owner")
    cid = new_customer(client)
    assert client.post(f"/customers/{cid}/contacts/new", data={
        "name": "Dana Manager", "role": "manager", "phone": "555-010-0177",
        "email": "dana@example.com", "is_site": "y"}).status_code == 302
    (contact_id,) = db_query(app, "SELECT id FROM contact WHERE customer_id = :c", c=cid)[0]
    assert client.post(f"/customers/{cid}/sites/new", data={
        "name": "Main St location", "city": "Testville", "site_contact_id": str(contact_id),
        "access_notes": "Open 4pm; bartender has the key"}).status_code == 302
    assert client.post(f"/customers/{cid}/log", data={
        "kind": "call", "summary": "Galaga won't coin up"}).status_code == 302
    page = client.get(f"/customers/{cid}").get_data(as_text=True)
    assert "Dana Manager" in page and "Main St location" in page and "coin up" in page
    assert "Contact: Dana Manager" in page
    # new contacts and sites are active (the new forms have no Active box)
    assert db_query(app, "SELECT bool_and(active) FROM contact WHERE customer_id = :c", c=cid)[0][0]
    assert db_query(app, "SELECT bool_and(active) FROM site WHERE customer_id = :c", c=cid)[0][0]


def test_site_contact_must_belong_to_the_customer(app, signed_in):
    client = signed_in("owner")
    a, b = new_customer(client, name="A Test"), new_customer(client, name="B Test")
    client.post(f"/customers/{b}/contacts/new", data={"name": "Other Person", "active": "y"})
    (other_contact,) = db_query(app, "SELECT id FROM contact WHERE customer_id = :c", c=b)[0]
    # the form doesn't offer it...
    resp = client.post(f"/customers/{a}/sites/new", data={
        "name": "Wrong", "site_contact_id": str(other_contact), "active": "y"})
    assert resp.status_code == 200 and b"Not a valid choice" in resp.data
    # ...and the database refuses it anyway (composite FK)
    with pytest.raises(Exception, match="fk_site_contact_same_customer"):
        db_query(app, "INSERT INTO site (customer_id, name, site_contact_id) VALUES (:a, 'x', :c)",
                 a=a, c=other_contact)


@pytest.mark.parametrize("statement", [
    "UPDATE comm_log SET summary = 'rewritten'",
    "DELETE FROM comm_log",
    "DELETE FROM customer",
    "DELETE FROM contact",
    "DELETE FROM site",
])
def test_app_role_cannot_rewrite_history_or_delete(app, tdb, signed_in, statement):
    client = signed_in("owner")
    cid = new_customer(client)
    client.post(f"/customers/{cid}/log", data={"kind": "note", "summary": "keep me"})
    with psycopg.connect(libpq(tdb.url("app")), autocommit=True) as c, \
            pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute(statement)


def test_customer_changes_are_audited(app, signed_in):
    client = signed_in("owner")
    cid = new_customer(client)
    client.post(f"/customers/{cid}/edit", data={"kind": "business", "name": "Renamed Test",
                                                "payment_terms": "net_15", "active": "y"})
    rows = db_query(app, "SELECT action, user_id, after->>'name' FROM audit_log "
                         "WHERE table_name = 'customer' AND row_id = :r ORDER BY id", r=str(cid))
    assert [r[0] for r in rows] == ["I", "U"]
    assert rows[1][2] == "Renamed Test" and rows[1][1] == client.user["id"]
