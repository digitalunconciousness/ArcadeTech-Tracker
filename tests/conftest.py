"""Test session: a fresh Postgres database per run, built by the real migrations, dropped
at the end. Never SQLite.

SHOP_TEST_ADMIN_URL (default: scripts/devdb.sh's server) must be a superuser connection.
The session creates database shop_test_<random> owned by a throwaway owner role, plus a
throwaway app login role in the shop_app group, exactly like prod's shop_owner / shop.
The app under test connects as the app role; migrations run as the owner."""

import os
import secrets
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

DEVDB_PGPASS = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "shop-hub/pgpass"
ADMIN_URL = os.environ.get(
    "SHOP_TEST_ADMIN_URL", "postgresql+psycopg://postgres@127.0.0.1:5433/postgres"
)
TEST_PASSWORD = "correct-horse-test-pw"  # synthetic; >= 12 chars
TEST_TZ = "Pacific/Kiritimati"  # UTC+14: a year boundary differs from UTC's by a day

if DEVDB_PGPASS.exists():
    os.environ.setdefault("PGPASSFILE", str(DEVDB_PGPASS))


def libpq(url):
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _refuse_non_test_database():
    url = os.environ.get("DATABASE_URL")
    if url and not (make_url(url).database or "").startswith("shop_test_"):
        pytest.exit(
            f"refusing to run: DATABASE_URL names {make_url(url).database!r}, not a shop_test_ "
            "database. Unset it; the test session makes its own.", returncode=2,
        )


_refuse_non_test_database()


class TestDB:
    def __init__(self):
        suffix = secrets.token_hex(4)
        self.name = f"shop_test_{suffix}"
        self.owner = f"{self.name}_owner"
        self.app_role = f"{self.name}_app"
        self.owner_pw = secrets.token_hex(16)
        self.app_pw = secrets.token_hex(16)

    def url(self, role="app", database=None):
        user, pw = (self.app_role, self.app_pw) if role == "app" else (self.owner, self.owner_pw)
        return make_url(ADMIN_URL).set(
            username=user, password=pw, database=database or self.name
        ).render_as_string(hide_password=False)

    def admin(self, database=None):
        """Superuser connection (autocommit) to the test database or another."""
        url = make_url(ADMIN_URL).set(database=database or self.name)
        return psycopg.connect(libpq(url.render_as_string(hide_password=False)), autocommit=True)

    def create_database(self, name):
        with psycopg.connect(libpq(ADMIN_URL), autocommit=True) as c:
            c.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(self.owner)))
            c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
            c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, shop_app").format(
                sql.Identifier(name), sql.Identifier(self.owner)))
        with self.admin(name) as c:
            c.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            c.execute("GRANT USAGE ON SCHEMA public TO shop_app")

    def drop_database(self, name):
        with psycopg.connect(libpq(ADMIN_URL), autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))

    def setup(self):
        with psycopg.connect(libpq(ADMIN_URL), autocommit=True) as c:
            try:
                c.execute("CREATE ROLE shop_app NOLOGIN")
            except (psycopg.errors.DuplicateObject, psycopg.errors.UniqueViolation):
                pass
            c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(self.owner), sql.Literal(self.owner_pw)))
            c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE shop_app").format(
                sql.Identifier(self.app_role), sql.Literal(self.app_pw)))
        self.create_database(self.name)

    def teardown(self):
        with psycopg.connect(libpq(ADMIN_URL), autocommit=True) as c:
            rows = c.execute(
                "SELECT datname FROM pg_database WHERE datname LIKE %s", (self.name + "%",)
            ).fetchall()
            for (name,) in rows:
                c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            for role in (self.app_role, self.owner):
                c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def test_env(tdb):
    return {
        "DATABASE_URL": tdb.url("app"),
        "MIGRATE_DATABASE_URL": tdb.url("owner"),
        "SECRET_KEY": "test-secret-key-" + "x" * 32,
        "SHOP_TZ": TEST_TZ,
        "SHOP_BASE_URL": "https://shop.example.test",
    }


test_env.__test__ = False  # not a test


@pytest.fixture(scope="session")
def tdb():
    try:
        psycopg.connect(libpq(ADMIN_URL), connect_timeout=5).close()
    except psycopg.OperationalError as e:
        pytest.exit(f"no test Postgres at SHOP_TEST_ADMIN_URL ({e}). scripts/devdb.sh start",
                    returncode=2)
    db_ = TestDB()
    db_.setup()
    yield db_
    db_.teardown()


@pytest.fixture(scope="session")
def app(tdb):
    os.environ.update(test_env(tdb))
    from flask_migrate import upgrade

    from app import create_app
    from app.extensions import limiter

    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SESSION_COOKIE_SECURE": False,
    })
    with app.app_context():
        upgrade()
    limiter.enabled = False
    return app


@pytest.fixture(autouse=True)
def _clean(request, tdb):
    """Every test starts from the migrated, seeded state."""
    yield
    if "app" not in request.fixturenames:
        return
    from app.extensions import db

    app = request.getfixturevalue("app")
    with app.app_context():
        db.session.remove()
    with tdb.admin() as c:
        tables = [r[0] for r in c.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename NOT IN ('alembic_version', 'shop_setting')"
        )]
        # replica role: skips triggers, including audit_log's no-TRUNCATE guard
        c.execute("SET session_replication_role = replica")
        c.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
            sql.SQL(", ").join(sql.Identifier(t) for t in tables)))
        c.execute("DELETE FROM shop_setting")
        c.execute("INSERT INTO shop_setting (id) VALUES (true)")
        # migration 0004's row: the shop as its own customer
        c.execute("INSERT INTO customer (kind, name, is_shop) "
                  "SELECT 'business', business_name, true FROM shop_setting")


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def make_user(app):
    from app.extensions import db
    from app.models import User
    from app.timeutil import utcnow

    def make(username="tess", role="tech", password=TEST_PASSWORD, totp=False, **kw):
        from app.auth.totp import new_secret

        with app.app_context():
            user = User(username=username, display_name=kw.pop("display_name", username.title()),
                        role=role, **kw)
            user.set_password(password)
            if totp:
                user.totp_secret = new_secret()
                user.totp_enabled_at = utcnow()
            db.session.add(user)
            db.session.commit()
            return {"id": user.id, "username": user.username, "secret": user.totp_secret}

    return make


def totp_code(secret, steps_ahead=0):
    import time

    import pyotp

    return pyotp.TOTP(secret).at(time.time() + 30 * steps_ahead)


def login(client, user, password=TEST_PASSWORD, steps_ahead=0):
    resp = client.post("/login", data={"username": user["username"], "password": password})
    if user.get("secret") and resp.status_code == 302 and "/login/totp" in resp.location:
        resp = client.post("/login/totp", data={"code": totp_code(user["secret"], steps_ahead)})
    return resp


@pytest.fixture
def signed_in(app, make_user):
    """signed_in("tech") -> a test client logged in as a fresh user with that role."""
    counter = {"n": 0}

    def make(role="owner"):
        counter["n"] += 1
        user = make_user(f"{role}{counter['n']}", role, totp=(role == "owner"))
        client = app.test_client()
        resp = login(client, user)
        assert resp.status_code == 302 and resp.location == "/", resp.location
        client.user = user
        return client

    return make


def db_query(app, sql, **params):
    from sqlalchemy import text

    from app.extensions import db

    with app.app_context():
        return db.session.execute(text(sql), params).all()
