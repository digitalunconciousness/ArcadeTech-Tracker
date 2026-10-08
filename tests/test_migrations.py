"""The migrations build the schema from an empty database, downgrade cleanly, and
match the models."""

import secrets

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import downgrade, upgrade
from sqlalchemy import create_engine, text

from app.extensions import db


def test_upgrade_downgrade_upgrade_from_empty(app, tdb, monkeypatch):
    name = f"{tdb.name}_mig{secrets.token_hex(2)}"
    tdb.create_database(name)
    url = tdb.url("owner", database=name)
    monkeypatch.setenv("MIGRATE_DATABASE_URL", url)
    engine = create_engine(url)

    def objects():
        with engine.connect() as c:
            t = c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                               "ORDER BY 1")).scalars().all()
            f = c.execute(text("SELECT proname FROM pg_proc p JOIN pg_namespace n "
                               "ON n.oid = p.pronamespace WHERE nspname = 'public' ORDER BY 1"
                               )).scalars().all()
        return t, f

    try:
        with app.app_context():
            upgrade()
            tables, functions = objects()
            assert {"app_user", "audit_log", "doc_counter", "shop_setting"} <= set(tables)
            assert functions == ["audit_log_append_only", "audit_row", "set_updated_at"]
            with engine.connect() as c:
                assert c.execute(text("SELECT count(*) FROM shop_setting")).scalar() == 1

            downgrade(revision="base")
            assert objects() == (["alembic_version"], [])

            upgrade()
            assert objects() == (tables, functions)
    finally:
        engine.dispose()
        tdb.drop_database(name)


def test_models_match_migrations(app):
    with app.app_context(), db.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), db.metadata)
    assert diff == []


def test_migrations_refuse_a_non_utf8_database(app, tdb, monkeypatch, capfd):
    """A cluster initialised without a UTF-8 locale gives SQL_ASCII databases; psycopg
    then returns bytes and SQLAlchemy dies with a TypeError. Refuse it, clearly."""
    import pytest
    from psycopg import sql

    name = f"{tdb.name}_ascii"
    with tdb.admin("postgres") as c:
        c.execute(sql.SQL(
            "CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING 'SQL_ASCII' LOCALE 'C'"
        ).format(sql.Identifier(name), sql.Identifier(tdb.owner)))
    monkeypatch.setenv("MIGRATE_DATABASE_URL", tdb.url("owner", database=name))
    try:
        # Flask-Migrate turns the RuntimeError into a logged one-line error and exit 1.
        with app.app_context(), pytest.raises(SystemExit) as exc:
            upgrade()
        assert exc.value.code == 1
        assert "has encoding SQL_ASCII; shop-hub needs UTF8" in capfd.readouterr().err
    finally:
        tdb.drop_database(name)
