from test_assets import asset, customer


def setup_data(app, client):
    cid = customer(client, "Starlite Lanes Test")
    client.post(f"/customers/{cid}/edit", data={"kind": "business", "name": "Starlite Lanes Test",
                                                "phone": "555.010.0144", "payment_terms": "net_15",
                                                "active": "y"})
    client.post(f"/customers/{cid}/contacts/new", data={"name": "Morgan Owner",
                                                        "phone": "(555) 010-0199"})
    aid = asset(client, cid, name="Tron", serial="TRN-12345", manufacturer="Bally Midway")
    return cid, aid


def found(client, q):
    return client.get("/search", query_string={"q": q}).get_data(as_text=True)


def test_search_by_name_phone_tag_serial(app, signed_in):
    client = signed_in("tech")
    cid, aid = setup_data(app, client)
    assert "Starlite Lanes Test" in found(client, "starlite")
    assert "Starlite Lanes Test" in found(client, "(555) 010-0144")   # stored as 555.010.0144
    assert "Morgan Owner" in found(client, "5550100199")              # contact phone, any format
    page = found(client, "trn-123")
    assert "s-" in page and "TRN-12345" in page                      # serial
    assert f"/assets/{aid}" in found(client, "bally")                # manufacturer
    assert f"/assets/{aid}" in found(client, "-tron")                 # tag fragment


def test_search_wildcards_are_literal(app, signed_in):
    client = signed_in("tech")
    setup_data(app, client)
    assert "Nothing matches" in found(client, "%%")
    assert "Nothing matches" in found(client, "__")


def test_search_needs_two_characters_and_a_login(app, signed_in):
    client = signed_in("viewer")
    assert "at least two characters" in found(client, "x")
    assert app.test_client().get("/search?q=tron").location.startswith("/login")
