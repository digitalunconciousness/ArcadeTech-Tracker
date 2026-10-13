"""customer orders: when the customer ordered a part they're buying, and when it's due

Revision ID: 0019_customer_orders
Revises: 0018_desktop
Create Date: 2026-10-13

A customer's part on its way was one state (awaiting) until it arrived; now it can be
marked ordered first. Written by hand (CHECKs on an existing table):

  wo_line.ordered_on   the day the customer ordered it (null: not ordered yet)
  wo_line.expected_on  when they expect it (only once ordered)

Both only on a customer's part. wo_line already has its grants, audit and billed-line guard.

Downgrade drops both columns: the ordered and expected dates go; the parts stay on their
way (awaiting) or here as they were.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0019_customer_orders'
down_revision = '0018_desktop'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('wo_line', sa.Column('ordered_on', sa.Date(), nullable=True))
    op.add_column('wo_line', sa.Column('expected_on', sa.Date(), nullable=True))
    op.create_check_constraint(op.f('ck_wo_line_ordered_customer_part'), 'wo_line',
                               'ordered_on IS NULL OR customer_supplied')
    op.create_check_constraint(op.f('ck_wo_line_expected_after_ordered'), 'wo_line',
                               'expected_on IS NULL OR ordered_on IS NOT NULL')


def downgrade():
    op.drop_constraint(op.f('ck_wo_line_expected_after_ordered'), 'wo_line', type_='check')
    op.drop_constraint(op.f('ck_wo_line_ordered_customer_part'), 'wo_line', type_='check')
    op.drop_column('wo_line', 'expected_on')
    op.drop_column('wo_line', 'ordered_on')
