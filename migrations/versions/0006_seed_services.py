"""seed the price book's starting services, inactive at $0.00 (data migration)

Revision ID: 0006_seed_services
Revises: 0005_pricebook_parts
Create Date: 2026-10-08

Starting rows so the price book isn't empty. They ship inactive with no rate (owner,
2026-10-08): set a rate and switch each one on in the price book before it can go on an
estimate. Existing codes are left alone, so re-running is harmless.

Downgrade deletes only these codes, and fails once anything references one (by design:
those are real records).
"""
from alembic import op

revision = "0006_seed_services"
down_revision = "0005_pricebook_parts"
branch_labels = None
depends_on = None

# code, name, kind, unit, warranty_days
SERVICES = (
    ("LAB-BENCH", "Bench labor", "labor", "hour", 365),
    ("LAB-FIELD", "Field labor", "labor", "hour", 365),
    ("LAB-AFTER", "After-hours labor", "labor", "hour", 365),
    ("FEE-DIAG", "Diagnostic fee", "diagnostic", "each", 0),
    ("FEE-TRIP", "Trip charge", "trip", "each", 0),
    ("FEE-MIN", "Minimum service charge", "fee", "each", 0),
    ("FEE-RUSH", "Rush fee", "fee", "each", 0),
)

# tests/conftest.py runs this after each test's TRUNCATE.
SEED_SQL = (
    "INSERT INTO service (code, name, kind, unit, rate, taxable, warranty_days, active) VALUES "
    + ", ".join(f"('{c}', '{n}', '{k}', '{u}', 0, false, {w}, false)" for c, n, k, u, w in SERVICES)
    + " ON CONFLICT (code) DO NOTHING"
)


def upgrade():
    op.execute(SEED_SQL)


def downgrade():
    codes = ", ".join(f"'{c}'" for c, *_ in SERVICES)
    op.execute(f"DELETE FROM service WHERE code IN ({codes})")
