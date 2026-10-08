"""SQL emitted by migrations: triggers and grants. Revisions import this, so treat it as
append-only: changing what a helper emits changes what old revisions do. Add a new helper
instead.

Roles (deploy/sql/create_roles.psql):
  shop_owner  owns every object and runs migrations. Only an owner can DISABLE TRIGGER,
              so the app can't switch off the audit or immutability triggers.
  shop_app    group role, NOLOGIN. Gets exactly the privileges granted here.
  shop        the app's login role, a member of shop_app.
"""

from alembic import op

APP_ROLE = "shop_app"


def _ident(name):
    if not name.replace("_", "").isalnum():
        raise ValueError(f"unsafe identifier {name!r}")
    return f'"{name}"'


def attach_updated_at(table):
    op.execute(
        f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {_ident(table)} "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def detach_updated_at(table):
    op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON {_ident(table)}")


def attach_audit(table, redact=()):
    """Row-level audit into audit_log. Columns in `redact` never reach the log; a change
    to one shows as "[changed]"."""
    args = ", ".join("'" + _ident(c).strip('"') + "'" for c in redact)
    op.execute(
        f"CREATE TRIGGER {table}_audit AFTER INSERT OR UPDATE OR DELETE ON {_ident(table)} "
        f"FOR EACH ROW EXECUTE FUNCTION audit_row({args})"
    )


def detach_audit(table):
    op.execute(f"DROP TRIGGER IF EXISTS {table}_audit ON {_ident(table)}")


def grant_app(table, privileges):
    """Grant the app role exactly these privileges on a table, plus on its identity
    sequence: SELECT always (pg_dump runs as the app role and reads every sequence),
    USAGE when it may INSERT. A test compares every table's grants to an expected map."""
    allowed = {"SELECT", "INSERT", "UPDATE", "DELETE"}
    privileges = [p.upper() for p in privileges]
    if not privileges or set(privileges) - allowed:
        raise ValueError(f"bad privileges {privileges!r}")
    op.execute(f"GRANT {', '.join(privileges)} ON {_ident(table)} TO {APP_ROLE}")
    seq_privileges = "USAGE, SELECT" if "INSERT" in privileges else "SELECT"
    op.execute(
        "DO $$ DECLARE s text; BEGIN "
        f"FOR s IN SELECT pg_get_serial_sequence('public.{table}', a.attname) "
        f"FROM pg_attribute a WHERE a.attrelid = 'public.{_ident(table)}'::regclass "
        "AND a.attidentity <> '' LOOP "
        f"EXECUTE format('GRANT {seq_privileges} ON SEQUENCE %s TO {APP_ROLE}', s); "
        "END LOOP; END $$"
    )


def revoke_app(table):
    op.execute(f"REVOKE ALL ON {_ident(table)} FROM {APP_ROLE}")
