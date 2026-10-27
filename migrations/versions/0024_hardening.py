"""hardening: audit_row's search_path ends with pg_temp; bank amounts can't be NaN

Revision ID: 0024_hardening
Revises: 0023_accounts
Create Date: 2026-10-27

From the October security review.

- audit_row() is SECURITY DEFINER with search_path = pg_catalog, public. Postgres looks
  in the session's temporary schema first unless pg_temp is named, so a role allowed to
  create a temporary table called audit_log could catch its own audit rows. The app role
  has no TEMP today (deploy/sql/create_roles.psql); naming pg_temp last makes it not
  matter.
- bank_line.amount and bank_match.amount: NUMERIC accepts 'NaN', and NaN <> 0 passes the
  existing check. A bank CSV with "NaN" in it imported, and the row broke every bank page.
  The import refuses it now; the checks make sure nothing else can store one.

Downgrade puts both back as they were.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '0024_hardening'
down_revision = '0023_accounts'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER FUNCTION audit_row() SET search_path = pg_catalog, public, pg_temp")
    op.create_check_constraint(op.f('ck_bank_line_amount_finite'), 'bank_line',
                               "amount <> 'NaN'")
    op.create_check_constraint(op.f('ck_bank_match_amount_finite'), 'bank_match',
                               "amount <> 'NaN'")


def downgrade():
    op.drop_constraint(op.f('ck_bank_match_amount_finite'), 'bank_match', type_='check')
    op.drop_constraint(op.f('ck_bank_line_amount_finite'), 'bank_line', type_='check')
    op.execute("ALTER FUNCTION audit_row() SET search_path = pg_catalog, public")
