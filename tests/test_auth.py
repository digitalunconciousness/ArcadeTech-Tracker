import time

from app.extensions import db, limiter
from app.models import User
from conftest import TEST_PASSWORD, login, totp_code


def test_login_logout_tech_without_2fa(client, make_user):
    user = make_user("tess", "tech")
    resp = login(client, user)
    assert resp.status_code == 302 and resp.location == "/"
    assert client.get("/").status_code == 200
    assert client.post("/logout").status_code == 302
    assert client.get("/").status_code == 302


def test_wrong_password_and_unknown_user_look_the_same(client, make_user):
    make_user("tess")
    a = client.post("/login", data={"username": "tess", "password": "wrong-test-pw-x"})
    b = client.post("/login", data={"username": "nobody", "password": "wrong-test-pw-x"})
    assert a.status_code == b.status_code == 200
    assert b"Wrong username or password." in a.data
    assert b"Wrong username or password." in b.data
    assert client.get("/").status_code == 302


def test_username_is_case_insensitive(client, make_user):
    make_user("tess")
    resp = client.post("/login", data={"username": " TESS ", "password": TEST_PASSWORD})
    assert resp.status_code == 302 and resp.location == "/"


def test_inactive_user_refused(client, make_user):
    user = make_user("gone", active=False)
    resp = login(client, user)
    assert resp.status_code == 200
    assert client.get("/").status_code == 302


def test_owner_without_2fa_is_held_at_enrollment(app, client, make_user):
    owner = make_user("olive", "owner")
    resp = login(client, owner)
    assert resp.status_code == 302
    for path in ("/", "/settings/", "/account"):
        r = client.get(path)
        assert r.status_code == 302 and r.location == "/account/2fa", path
    page = client.get("/account/2fa")
    assert page.status_code == 200 and b"<svg" in page.data

    with app.app_context():
        secret = db.session.get(User, owner["id"]).totp_secret
    assert client.post("/account/2fa", data={"code": "000000"}).status_code == 200
    resp = client.post("/account/2fa", data={"code": totp_code(secret)})
    assert resp.status_code == 302 and resp.location == "/"
    assert client.get("/settings/").status_code == 200
    with app.app_context():
        assert db.session.get(User, owner["id"]).totp_enabled


def test_2fa_login_and_replay_refused(client, make_user):
    owner = make_user("olive", "owner", totp=True)
    resp = client.post("/login", data={"username": "olive", "password": TEST_PASSWORD})
    assert resp.status_code == 302 and resp.location == "/login/totp"
    assert client.get("/").status_code == 302  # password alone isn't a login
    code = totp_code(owner["secret"])
    resp = client.post("/login/totp", data={"code": code})
    assert resp.status_code == 302 and resp.location == "/"
    client.post("/logout")

    client.post("/login", data={"username": "olive", "password": TEST_PASSWORD})
    resp = client.post("/login/totp", data={"code": code})
    assert resp.status_code == 200 and b"didn" in resp.data
    resp = client.post("/login/totp", data={"code": totp_code(owner["secret"], 1)})
    assert resp.status_code == 302 and resp.location == "/"


def test_pending_2fa_expires(client, make_user):
    owner = make_user("olive", "owner", totp=True)
    client.post("/login", data={"username": "olive", "password": TEST_PASSWORD})
    with client.session_transaction() as s:
        s["pending_login"] = {**s["pending_login"], "at": time.time() - 301}
    resp = client.post("/login/totp", data={"code": totp_code(owner["secret"])})
    assert resp.status_code == 302 and resp.location == "/login"


def test_totp_step_page_needs_password_first(client):
    assert client.get("/login/totp").location == "/login"


def test_next_param_stays_on_site(client, make_user):
    user = make_user("tess")
    for bad in ("//evil.example/x", "https://evil.example/", "/\\evil.example"):
        resp = client.post(f"/login?next={bad}", data={"username": "tess", "password": TEST_PASSWORD})
        assert resp.location == "/", bad
        client.post("/logout")
    resp = client.post("/login?next=/account", data={"username": "tess", "password": TEST_PASSWORD})
    assert resp.location == "/account"
    assert user


def test_password_change_signs_out_other_sessions(app, make_user):
    user = make_user("tess")
    phone, laptop = app.test_client(), app.test_client()
    login(phone, user)
    login(laptop, user)
    resp = phone.post("/account/password", data={
        "current_password": TEST_PASSWORD,
        "new_password": "another-test-password", "confirm_password": "another-test-password",
    })
    assert resp.status_code == 302
    assert phone.get("/").status_code == 200
    assert laptop.get("/").status_code == 302


def test_password_change_needs_current_password(client, make_user):
    login(client, make_user("tess"))
    resp = client.post("/account/password", data={
        "current_password": "not-my-test-pw",
        "new_password": "another-test-password", "confirm_password": "another-test-password",
    })
    assert resp.status_code == 400


def test_short_password_rejected(client, make_user):
    login(client, make_user("tess"))
    resp = client.post("/account/password", data={
        "current_password": TEST_PASSWORD, "new_password": "short", "confirm_password": "short",
    })
    assert resp.status_code == 400


def test_password_hash_is_scrypt(app, make_user):
    user = make_user("tess")
    with app.app_context():
        assert db.session.get(User, user["id"]).password_hash.startswith("scrypt:")


def test_login_is_rate_limited(client, make_user):
    make_user("tess")
    limiter.enabled = True
    limiter.reset()
    try:
        codes = [client.post("/login", data={"username": "tess", "password": "wrong-test-pw-x"},
                             environ_base={"REMOTE_ADDR": "198.51.100.7"}).status_code
                 for _ in range(6)]
        assert codes == [200] * 5 + [429]
        other = client.post("/login", data={"username": "tess", "password": "wrong-test-pw-x"},
                            environ_base={"REMOTE_ADDR": "198.51.100.8"})
        assert other.status_code == 200
    finally:
        limiter.reset()
        limiter.enabled = False


def test_tunnel_client_ip_comes_from_cloudflare_header(client, make_user):
    make_user("tess")
    limiter.enabled = True
    limiter.reset()
    try:
        def attempt(peer, cf=None):
            headers = {"CF-Connecting-IP": cf} if cf else {}
            return client.post("/login", data={"username": "tess", "password": "wrong-test-pw-x"},
                               headers=headers, environ_base={"REMOTE_ADDR": peer}).status_code

        # Through the tunnel (loopback peer): each real client gets its own bucket.
        assert [attempt("127.0.0.1", "203.0.113.1") for _ in range(6)][-1] == 429
        assert attempt("127.0.0.1", "203.0.113.2") == 200
        # From the LAN, the header is ignored: spoofing it doesn't buy a fresh bucket.
        assert [attempt("198.51.100.9", f"203.0.113.{i + 10}") for i in range(6)][-1] == 429
    finally:
        limiter.reset()
        limiter.enabled = False


def test_csrf_enforced(app, client, make_user):
    make_user("tess")
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post("/login", data={"username": "tess", "password": TEST_PASSWORD})
        assert resp.status_code == 400
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_last_login_recorded(app, client, make_user):
    user = make_user("tess")
    login(client, user)
    with app.app_context():
        assert db.session.get(User, user["id"]).last_login_at is not None
