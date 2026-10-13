"""statement links: a customer link can open a customer's statement

Revision ID: 0021_statement_links
Revises: 0020_dispatch
Create Date: 2026-10-13

Phase 9c. doc_link.customer_id: a /d/ link to a customer's live statement (what's open,
how late, credit, recent payments). One document per link still: the CHECK widens from
estimate-or-invoice to estimate-or-invoice-or-statement. Written by hand.

Downgrade deletes the statement links (they stop opening) and narrows the CHECK back.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0021_statement_links'
down_revision = '0020_dispatch'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('doc_link', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_doc_link_customer_id'), 'doc_link', ['customer_id'], unique=False)
    op.create_foreign_key(op.f('fk_doc_link_customer_id_customer'), 'doc_link', 'customer',
                          ['customer_id'], ['id'])
    op.drop_constraint(op.f('ck_doc_link_one_document'), 'doc_link', type_='check')
    op.create_check_constraint(op.f('ck_doc_link_one_document'), 'doc_link',
                               'num_nonnulls(estimate_id, invoice_id, customer_id) = 1')


def downgrade():
    op.execute("DELETE FROM doc_link WHERE customer_id IS NOT NULL")
    op.drop_constraint(op.f('ck_doc_link_one_document'), 'doc_link', type_='check')
    op.create_check_constraint(op.f('ck_doc_link_one_document'), 'doc_link',
                               'num_nonnulls(estimate_id, invoice_id) = 1')
    op.drop_constraint(op.f('fk_doc_link_customer_id_customer'), 'doc_link', type_='foreignkey')
    op.drop_index(op.f('ix_doc_link_customer_id'), table_name='doc_link')
    op.drop_column('doc_link', 'customer_id')
