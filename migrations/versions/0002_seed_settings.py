"""seed the shop_setting row (data migration)

Revision ID: 0002_seed_settings
Revises: 0001_foundation
Create Date: 2026-10-08 03:00:00

The single settings row, with the column defaults. Downgrade deletes it.
"""
from alembic import op

revision = "0002_seed_settings"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("INSERT INTO shop_setting (id) VALUES (true) ON CONFLICT (id) DO NOTHING")


def downgrade():
    op.execute("DELETE FROM shop_setting WHERE id")
