"""Conventions, checked against the migrated database itself."""

import pytest
from sqlalchemy import text

from app.extensions import db

EXEMPT_STANDARD = {"alembic_version", "audit_log"}  # see AuditLog's docstring

# Exactly what the app role may do, per table. A new table must be added here, on
# purpose, with the least it needs.
EXPECTED_GRANTS = {
    "alembic_version": {"SELECT"},
    "app_user": {"SELECT", "INSERT", "UPDATE"},
    "audit_log": {"SELECT"},
    "doc_counter": {"SELECT", "INSERT", "UPDATE"},
    "shop_setting": {"SELECT", "UPDATE"},
    "customer": {"SELECT", "INSERT", "UPDATE"},
    "contact": {"SELECT", "INSERT", "UPDATE"},
    "site": {"SELECT", "INSERT", "UPDATE"},
    "asset": {"SELECT", "INSERT", "UPDATE"},
    "comm_log": {"SELECT", "INSERT"},
    "asset_event": {"SELECT", "INSERT"},
    "service": {"SELECT", "INSERT", "UPDATE"},
    "job_template": {"SELECT", "INSERT", "UPDATE"},
    "job_template_line": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "markup_tier": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "vendor": {"SELECT", "INSERT", "UPDATE"},
    "part": {"SELECT", "INSERT", "UPDATE"},
    "stock_lot": {"SELECT", "INSERT"},
    "stock_move": {"SELECT", "INSERT"},
}

AUDITED = {"app_user", "shop_setting", "customer", "contact", "site", "asset", "service",
           "job_template", "job_template_line", "markup_tier", "vendor", "part", "stock_lot",
           "stock_move"}


def q(app, sql, **params):
    with app.app_context():
        return db.session.execute(text(sql), params).all()


def tables(app):
    return {r[0] for r in q(app, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}


def test_standard_columns_everywhere(app):
    for table in tables(app) - EXEMPT_STANDARD:
        cols = {r.column_name: r for r in q(app, """
            SELECT column_name, data_type, is_nullable FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t""", t=table)}
        for name in ("created_at", "updated_at"):
            assert cols[name].data_type == "timestamp with time zone", (table, name)
            assert cols[name].is_nullable == "NO", (table, name)
        assert cols["created_by"].data_type == "integer", table
        fk = q(app, """
            SELECT 1 FROM pg_constraint c JOIN pg_attribute a
              ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.contype = 'f' AND c.conrelid = CAST(:t AS regclass)
              AND c.confrelid = 'app_user'::regclass AND a.attname = 'created_by'""",
               t=f'public."{table}"')
        assert fk, f"{table}.created_by has no FK to app_user"


def test_updated_at_trigger_everywhere(app):
    for table in tables(app) - EXEMPT_STANDARD:
        assert q(app, "SELECT 1 FROM pg_trigger WHERE tgname = :n AND NOT tgisinternal",
                 n=f"{table}_set_updated_at"), table


def test_audited_tables(app):
    from app import models  # noqa: F401

    marked = {m.class_.__tablename__ for m in db.Model.registry.mappers
              if hasattr(m.class_, "__audited__")}
    assert marked == AUDITED
    for table in AUDITED:
        assert q(app, "SELECT 1 FROM pg_trigger WHERE tgname = :n AND NOT tgisinternal",
                 n=f"{table}_audit"), table


def test_no_naive_timestamps_or_floats(app):
    bad = q(app, """
        SELECT table_name, column_name, data_type FROM information_schema.columns
        WHERE table_schema = 'public'
          AND data_type IN ('timestamp without time zone', 'real', 'double precision', 'money')""")
    assert bad == []


def test_app_role_grants_are_exact(app):
    rows = q(app, """
        SELECT table_name, privilege_type FROM information_schema.role_table_grants
        WHERE grantee = 'shop_app' AND table_schema = 'public'""")
    actual = {}
    for table, priv in rows:
        actual.setdefault(table, set()).add(priv)
    assert tables(app) == set(EXPECTED_GRANTS), "add new tables to EXPECTED_GRANTS"
    assert actual == EXPECTED_GRANTS


def test_app_role_sequence_privileges(app):
    """SELECT on every sequence (for pg_dump); USAGE only where the table takes INSERTs;
    never UPDATE (setval)."""
    rows = q(app, """
        SELECT c.relname, has_sequence_privilege('shop_app', c.oid, 'SELECT'),
               has_sequence_privilege('shop_app', c.oid, 'USAGE'),
               has_sequence_privilege('shop_app', c.oid, 'UPDATE')
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'S' AND n.nspname = 'public'""")
    insertable = ("app_user", "customer", "contact", "site", "comm_log", "asset", "asset_event",
                  "service", "job_template", "job_template_line", "markup_tier", "vendor", "part",
                  "stock_lot", "stock_move")
    expected = {f"{t}_id_seq": (True, True, False) for t in insertable}
    expected["audit_log_id_seq"] = (True, False, False)
    expected["asset_tag_seq"] = (True, True, False)
    assert {r[0]: r[1:] for r in rows} == expected


@pytest.mark.parametrize("fn", ["audit_row", "set_updated_at", "audit_log_append_only",
                                "asset_tag_immutable", "asset_no_cycle", "stock_move_lot_guard"])
def test_trigger_functions_not_public(app, fn):
    rows = q(app, "SELECT has_function_privilege('shop_app', :f, 'EXECUTE')", f=f"{fn}()")
    assert rows == [(False,)]


def test_audit_function_pins_search_path(app):
    rows = q(app, "SELECT proconfig FROM pg_proc WHERE proname = 'audit_row'")
    assert rows and any("search_path=" in c for c in rows[0][0])
