from app import PUBLIC_ENDPOINTS
from app.extensions import db
from app.models import User
from conftest import login


def test_unauthenticated_redirects_to_login(client):
    for path in ("/", "/settings/", "/settings/users", "/account", "/no-such-page"):
        resp = client.get(path)
        assert resp.status_code == 302 and resp.location.startswith("/login"), path


def test_settings_are_owner_only(client, make_user):
    for role in ("tech", "viewer"):
        login(client, make_user(role, role))
        assert client.get("/settings/").status_code == 403
        assert client.get("/settings/users").status_code == 403
        client.post("/logout")
    login(client, make_user("olive", "owner", totp=True))
    assert client.get("/settings/").status_code == 200
    assert client.get("/settings/users").status_code == 200


def test_every_private_view_declares_roles(app):
    """Default-deny for future phases: a new view without @requires_role fails here."""
    missing = []
    for endpoint, view in app.view_functions.items():
        if endpoint in PUBLIC_ENDPOINTS:
            continue
        if not getattr(view, "required_roles", None):
            missing.append(endpoint)
    assert missing == []


def _owner_client(app, make_user, username="olive"):
    client = app.test_client()
    owner = make_user(username, "owner", totp=True)
    login(client, owner)
    return client, owner


def _edit(client, user_id, **fields):
    data = {"display_name": "Someone", "role": "owner", "active": "y", **fields}
    data = {k: v for k, v in data.items() if v is not None}
    return client.post(f"/settings/users/{user_id}", data=data)


def test_last_owner_cannot_be_demoted_or_deactivated(app, make_user):
    client, owner = _owner_client(app, make_user)
    _edit(client, owner["id"], role="tech")
    _edit(client, owner["id"], active=None)
    with app.app_context():
        row = db.session.get(User, owner["id"])
        assert (row.role, row.active) == ("owner", True)


def test_owner_can_be_demoted_when_another_exists(app, make_user):
    client, owner = _owner_client(app, make_user)
    other = make_user("otto", "owner", totp=True)
    resp = _edit(client, other["id"], role="viewer")
    assert resp.status_code == 302
    with app.app_context():
        assert db.session.get(User, other["id"]).role == "viewer"


def test_create_user_and_duplicate_username(app, make_user):
    client, _ = _owner_client(app, make_user)
    data = {"username": "Tess", "display_name": "Tess", "role": "tech", "active": "y",
            "new_password": "a-good-test-password", "confirm_password": "a-good-test-password"}
    assert client.post("/settings/new-user", data=data).status_code == 302
    assert client.post("/settings/new-user", data=data).status_code == 200
    with app.app_context():
        assert db.session.query(User).filter_by(username="tess").count() == 1


def test_role_change_ends_that_users_sessions(app, make_user):
    client, _ = _owner_client(app, make_user)
    tech = make_user("tess", "tech")
    tech_client = app.test_client()
    login(tech_client, tech)
    assert tech_client.get("/").status_code == 200
    _edit(client, tech["id"], role="viewer")
    assert tech_client.get("/").status_code == 302


def test_reset_2fa(app, make_user):
    client, _ = _owner_client(app, make_user)
    other = make_user("otto", "owner", totp=True)
    other_client = app.test_client()
    login(other_client, other)
    assert client.post(f"/settings/users/{other['id']}/reset-2fa").status_code == 302
    with app.app_context():
        row = db.session.get(User, other["id"])
        assert row.totp_secret is None and row.totp_enabled_at is None
    assert other_client.get("/").status_code == 302


def test_owner_sets_password(app, make_user):
    client, _ = _owner_client(app, make_user)
    tech = make_user("tess", "tech")
    client.post(f"/settings/users/{tech['id']}/password",
                data={"new_password": "brand-new-test-pw", "confirm_password": "brand-new-test-pw"})
    fresh = app.test_client()
    assert login(fresh, tech, password="brand-new-test-pw").location == "/"


def test_business_settings(app, make_user):
    client, _ = _owner_client(app, make_user)
    form = {"business_name": "Neon Repair Test Co", "doc_accent_color": "#123abc", "estimate_link_days": "30",
            "email": "shop@example.com", "website": "https://shop.example.com"}
    assert client.post("/settings/", data=form).status_code == 302
    assert b"Neon Repair Test Co" in client.get("/").data
    bad = client.post("/settings/", data={**form, "doc_accent_color": "red"})
    assert bad.status_code == 200 and b"#rrggbb" in bad.data
