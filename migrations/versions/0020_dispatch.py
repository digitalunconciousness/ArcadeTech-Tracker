"""dispatch: refuse double-booking a tech, if the owner wants

Revision ID: 0020_dispatch
Revises: 0019_customer_orders
Create Date: 2026-10-13

Phase 9b. shop_setting.dispatch_block_overlap: off (the default), the dispatch calendar
flags a tech booked twice at once; on, it refuses the second booking. shop_setting already
has its grants and audit trigger.

Downgrade drops the column: double-booking goes back to being flagged only.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0020_dispatch'
down_revision = '0019_customer_orders'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('shop_setting', sa.Column('dispatch_block_overlap', sa.Boolean(),
                                            server_default=sa.text('false'), nullable=False))


def downgrade():
    op.drop_column('shop_setting', 'dispatch_block_overlap')
