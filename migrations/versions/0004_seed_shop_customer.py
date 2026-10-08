"""seed the shop's own customer row (data migration)

Revision ID: 0004_seed_shop_customer
Revises: 0003_customers_assets
Create Date: 2026-10-08 06:40:00

The shop is a customer too: it owns exchange units and stock machines. One row,
is_shop = true (a partial unique index allows only one), named after the business
name setting at the time this runs; rename it like any customer afterwards.

Downgrade deletes the row, and fails if assets still belong to it (by design: those
are real records).
"""
from alembic import op

revision = "0004_seed_shop_customer"
down_revision = "0003_customers_assets"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "INSERT INTO customer (kind, name, is_shop) "
        "SELECT 'business', business_name, true FROM shop_setting "
        "WHERE NOT EXISTS (SELECT FROM customer WHERE is_shop)"
    )


def downgrade():
    op.execute("DELETE FROM customer WHERE is_shop")
