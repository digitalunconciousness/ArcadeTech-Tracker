"""The audit trigger, the per-request actor, and what the app role may not do."""

import psycopg
import pytest
from sqlalchemy import select, text

from app.actor import set_actor
from app.extensions import db
from app.models import AuditLog, User
from conftest import login


def _rows(app, table="app_user"):
    with app.app_context():
        return db.session.scalars(
            select(AuditLog).where(AuditLog.table_name == table).order_by(AuditLog.id)
        ).all()


def test_insert_update_audited_with_actor_and_redaction(app, make_user):
    owner = make_user("olive", "owner")
    with app.app_context():
        set_actor(owner["id"])
        user = User(username="tess", display_name="Tess", role="tech")
        user.set_password("first-test-password")
        db.session.add(user)
        db.session.commit()
        user.display_name = "Tess T."
        user.set_password("second-test-password")
        db.session.commit()
        created_by = user.created_by
        tess_id = user.id

    rows = [r for r in _rows(app) if r.row_id == str(tess_id)]
    assert [r.action for r in rows] == ["I", "U"]
    assert {r.user_id for r in rows} == {owner["id"]}
    assert created_by == owner["id"]
    ins, upd = rows
    assert "password_hash" not in ins.after and "totp_secret" not in ins.after
    assert upd.before["display_name"] == "Tess" and upd.after["display_name"] == "Tess T."
    assert upd.after["password_hash"] == "[changed]"
    with app.app_context():
        dump = db.session.execute(text("SELECT string_agg(after::text, '') FROM audit_log")).scalar()
    assert "scrypt:" not in dump


def test_no_actor_is_null(app, make_user):
    make_user("tess")
    assert [r.user_id for r in _rows(app)] == [None]


def test_request_actor_reaches_postgres(app, make_user):
    client = app.test_client()
    owner = make_user("olive", "owner", totp=True)
    tech = make_user("tess", "tech")
    login(client, owner)
    resp = client.post(f"/settings/users/{tech['id']}", data={
        "display_name": "Tess Renamed", "role": "tech", "active": "y"})
    assert resp.status_code == 302
    rows = [r for r in _rows(app) if r.row_id == str(tech["id"]) and r.action == "U"]
    assert rows and rows[-1].user_id == owner["id"]
    assert rows[-1].after["display_name"] == "Tess Renamed"


def test_created_fields_are_frozen(app, make_user):
    user = make_user("tess")
    with app.app_context():
        before = db.session.execute(text(
            "SELECT created_at, created_by, updated_at FROM app_user WHERE id = :i"),
            {"i": user["id"]}).one()
        db.session.execute(text(
            "UPDATE app_user SET created_at = '2001-01-01', created_by = :i, display_name = 'x' "
            "WHERE id = :i"), {"i": user["id"]})
        db.session.commit()
        after = db.session.execute(text(
            "SELECT created_at, created_by, updated_at FROM app_user WHERE id = :i"),
            {"i": user["id"]}).one()
    assert after.created_at == before.created_at
    assert after.created_by == before.created_by
    assert after.updated_at >= before.updated_at


def _as_app(tdb):
    from conftest import libpq

    return psycopg.connect(libpq(tdb.url("app")), autocommit=True)


@pytest.mark.parametrize("statement", [
    "UPDATE audit_log SET action = 'I'",
    "DELETE FROM audit_log",
    "TRUNCATE audit_log",
    "INSERT INTO audit_log (table_name, action) VALUES ('x', 'I')",
    "ALTER TABLE app_user DISABLE TRIGGER ALL",
    "ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete",
    "DROP TRIGGER app_user_audit ON app_user",
    "DELETE FROM app_user",
    "CREATE TABLE sneaky (id int)",
])
def test_app_role_cannot(app, tdb, make_user, statement):
    make_user("tess")
    with _as_app(tdb) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute(statement)


@pytest.mark.parametrize("statement", [
    "UPDATE audit_log SET action = 'I'",
    "DELETE FROM audit_log",
    "TRUNCATE audit_log",
])
def test_audit_log_append_only_even_for_superuser(app, tdb, make_user, statement):
    make_user("tess")
    with tdb.admin() as c, pytest.raises(psycopg.errors.InsufficientPrivilege,
                                         match="append-only"):
        c.execute(statement)
