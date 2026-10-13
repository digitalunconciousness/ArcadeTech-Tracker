"""accounts: the owner's names for each kind of money, for the general-journal export

Revision ID: 0023_accounts
Revises: 0022_bank
Create Date: 2026-10-13

Phase 9c. account_map: one row per name the owner (or, later, an accountant) has changed
from the built-in default (app/books/journal.py), e.g. key 'income:labor' -> 'Sales:Labor'.
Written to match the model; updated_at and audit triggers; grants SELECT, INSERT, UPDATE,
DELETE (deleting a row puts the default name back).

Downgrade drops the table: the export goes back to the default names.
"""
from alembic import op
import sqlalchemy as sa

from sqlhelpers import attach_audit, attach_updated_at, grant_app

# revision identifiers, used by Alembic.
revision = '0023_accounts'
down_revision = '0022_bank'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('account_map',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('key', sa.String(length=60), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('number', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.Integer(), server_default=sa.text("NULLIF(current_setting('shop.user_id', true), '')::integer"), nullable=True),
    sa.CheckConstraint("key ~ '^[a-z_]+(:[a-z0-9_]+)?$'", name=op.f('ck_account_map_key_format')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_account_map_created_by_app_user')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_account_map')),
    sa.UniqueConstraint('key', name=op.f('uq_account_map_key'))
    )
    attach_updated_at('account_map')
    attach_audit('account_map')
    grant_app('account_map', ['SELECT', 'INSERT', 'UPDATE', 'DELETE'])


def downgrade():
    op.drop_table('account_map')
