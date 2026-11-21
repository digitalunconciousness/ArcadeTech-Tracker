"""A new user's desktop look is Synthwave

Revision ID: 0028_desk_synthwave
Revises: 0027_library
Create Date: 2026-11-21

The owner, 2026-11-21: Synthwave is the default desktop look here and in cabcrawl, with Ledger,
Graphite and Follow system on Account → Desktop look. Only the column's default changes; a
user who already has a look keeps it.

Downgrade puts the default back to Ledger. Nobody's saved look changes either way.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '0028_desk_synthwave'
down_revision = '0027_library'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('app_user', 'desk_theme', existing_type=sa.String(length=10),
                    existing_nullable=False, server_default=sa.text("'synthwave'"))


def downgrade():
    op.alter_column('app_user', 'desk_theme', existing_type=sa.String(length=10),
                    existing_nullable=False, server_default=sa.text("'ledger'"))
