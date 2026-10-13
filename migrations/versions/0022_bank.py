"""bank: the shop's accounts, imported lines, matches, reconciliations

Revision ID: 0022_bank
Revises: 0021_statement_links
Create Date: 2026-10-13

Phase 9c. Tables written to match the models (app/models/bank.py), hand-reviewed. Written
by hand:

  bank_line_guard()            what the bank said (date, amount, description) never
                               changes; a reconciled line is locked (it only comes off an
                               undone reconciliation) and can't be deleted; a matched one
                               can't be deleted; a line can only be reconciled into its own
                               account's live reconciliation, on or before its end, and only
                               when it's set aside or its matches add up to it
  bank_match_guard()           a match is never edited; a reconciled line's matches can't
                               be added or removed; a set-aside line takes no matches; a
                               match goes the same way (in or out) as its line
  bank_reconciliation_guard()  never deleted; nothing changes but undoing it, and only the
                               latest live one of its account can be undone; a new one must
                               end after the account's latest live one
  updated_at and audit triggers on every table.
  grants: bank_account         SELECT, INSERT, UPDATE
          bank_import          SELECT, INSERT
          bank_line            SELECT, INSERT, UPDATE, DELETE (the guard decides)
          bank_match           SELECT, INSERT, DELETE (unmatching; the guard decides)
          bank_reconciliation  SELECT, INSERT, UPDATE (undo only)

Downgrade drops all five tables: every imported line, match and reconciliation goes (the
imported files stay in the file store).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from sqlhelpers import attach_audit, attach_updated_at, grant_app

TABLES = ("bank_account", "bank_reconciliation", "bank_import", "bank_line", "bank_match")
TARGETS = ("payment_id", "expense_id", "purchase_order_id", "partner_entry_id",
           "sales_tax_filing_id")

BANK_LINE_GUARD = """
CREATE FUNCTION bank_line_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    keep text[] := ARRAY['ignored', 'ignored_note', 'reconciliation_id', 'updated_at'];
    matched numeric;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.reconciliation_id IS NOT NULL THEN
            RAISE EXCEPTION 'bank line % is reconciled and can''t be deleted', OLD.id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM bank_match WHERE bank_line_id = OLD.id) THEN
            RAISE EXCEPTION 'bank line % is matched: unmatch it before deleting it', OLD.id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF (to_jsonb(NEW) - keep) IS DISTINCT FROM (to_jsonb(OLD) - keep) THEN
        RAISE EXCEPTION 'what the bank said about line % (date, amount, description) '
                        'doesn''t change', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.reconciliation_id IS NOT NULL THEN
        IF NEW.reconciliation_id IS NULL
                AND NEW.ignored IS NOT DISTINCT FROM OLD.ignored
                AND NEW.ignored_note IS NOT DISTINCT FROM OLD.ignored_note
                AND EXISTS (SELECT 1 FROM bank_reconciliation r
                            WHERE r.id = OLD.reconciliation_id AND r.undone_at IS NOT NULL) THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'bank line % is reconciled and can''t change', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.reconciliation_id IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM bank_reconciliation r
                       WHERE r.id = NEW.reconciliation_id AND r.undone_at IS NULL
                         AND r.bank_account_id = NEW.bank_account_id
                         AND NEW.posted_on <= r.statement_end) THEN
            RAISE EXCEPTION 'bank line % doesn''t belong in reconciliation %', NEW.id,
                NEW.reconciliation_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        SELECT coalesce(sum(amount), 0) INTO matched FROM bank_match WHERE bank_line_id = NEW.id;
        IF NEW.ignored IS NULL AND matched <> NEW.amount THEN
            RAISE EXCEPTION 'bank line % isn''t matched in full or set aside', NEW.id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$
"""

BANK_MATCH_GUARD = """
CREATE FUNCTION bank_match_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    line bank_line%ROWTYPE;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'a bank match isn''t edited: unmatch it and match again'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT * INTO line FROM bank_line
        WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.bank_line_id ELSE NEW.bank_line_id END
        FOR UPDATE;
    IF line.reconciliation_id IS NOT NULL THEN
        RAISE EXCEPTION 'bank line % is reconciled: its matches are locked', line.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF line.ignored IS NOT NULL THEN
        RAISE EXCEPTION 'bank line % is set aside: it takes no matches', line.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF sign(NEW.amount) <> sign(line.amount) THEN
        RAISE EXCEPTION 'a match goes the same way (in or out) as its bank line'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$
"""

BANK_RECONCILIATION_GUARD = """
CREATE FUNCTION bank_reconciliation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    keep text[] := ARRAY['undone_at', 'undo_reason', 'updated_at'];
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'reconciliation % is kept: undo it instead', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF EXISTS (SELECT 1 FROM bank_reconciliation r
                   WHERE r.bank_account_id = NEW.bank_account_id AND r.undone_at IS NULL
                     AND r.statement_end >= NEW.statement_end) THEN
            RAISE EXCEPTION 'a reconciliation must end after the account''s latest one'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.undone_at IS NOT NULL THEN
        RAISE EXCEPTION 'reconciliation % was undone and doesn''t change', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF (to_jsonb(NEW) - keep) IS DISTINCT FROM (to_jsonb(OLD) - keep) THEN
        RAISE EXCEPTION 'reconciliation % doesn''t change; the latest one can be undone',
            OLD.id USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.undone_at IS NOT NULL AND EXISTS (
            SELECT 1 FROM bank_reconciliation r
            WHERE r.bank_account_id = OLD.bank_account_id AND r.undone_at IS NULL
              AND r.statement_end > OLD.statement_end) THEN
        RAISE EXCEPTION 'only the latest reconciliation of an account can be undone'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END $$
"""

FUNCTIONS = ("bank_line_guard", "bank_match_guard", "bank_reconciliation_guard")

# revision identifiers, used by Alembic.
revision = '0022_bank'
down_revision = '0021_statement_links'
branch_labels = None
depends_on = None


def standard():
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.Integer(), server_default=sa.text("NULLIF(current_setting('shop.user_id', true), '')::integer"), nullable=True),
    ]


def upgrade():
    op.create_table('bank_account',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('last4', sa.String(length=4), nullable=True),
    sa.Column('opening_on', sa.Date(), nullable=False),
    sa.Column('opening_balance', sa.Numeric(precision=12, scale=2), server_default=sa.text('0'), nullable=False),
    sa.Column('mapping', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    *standard(),
    sa.CheckConstraint("kind IN ('checking', 'savings', 'card', 'wallet')", name=op.f('ck_bank_account_kind')),
    sa.CheckConstraint("last4 IS NULL OR last4 ~ '^[0-9]{4}$'", name=op.f('ck_bank_account_last4_digits')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_bank_account_created_by_app_user')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bank_account')),
    sa.UniqueConstraint('name', name=op.f('uq_bank_account_name'))
    )
    op.create_table('bank_reconciliation',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('bank_account_id', sa.Integer(), nullable=False),
    sa.Column('statement_end', sa.Date(), nullable=False),
    sa.Column('opening_balance', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('ending_balance', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('undone_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('undo_reason', sa.String(length=300), nullable=True),
    *standard(),
    sa.CheckConstraint('(undone_at IS NULL) = (undo_reason IS NULL)', name=op.f('ck_bank_reconciliation_undo_has_reason')),
    sa.ForeignKeyConstraint(['bank_account_id'], ['bank_account.id'], name=op.f('fk_bank_reconciliation_bank_account_id_bank_account')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_bank_reconciliation_created_by_app_user')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bank_reconciliation'))
    )
    op.create_index(op.f('ix_bank_reconciliation_bank_account_id'), 'bank_reconciliation', ['bank_account_id'], unique=False)
    op.create_index('uq_bank_reconciliation_live', 'bank_reconciliation', ['bank_account_id', 'statement_end'], unique=True, postgresql_where=sa.text('undone_at IS NULL'))
    op.create_table('bank_import',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('bank_account_id', sa.Integer(), nullable=False),
    sa.Column('filename', sa.String(length=200), nullable=False),
    sa.Column('file_sha256', sa.String(length=64), nullable=False),
    sa.Column('added', sa.Integer(), nullable=False),
    sa.Column('skipped', sa.Integer(), nullable=False),
    *standard(),
    sa.CheckConstraint("file_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_bank_import_file_sha256_hex')),
    sa.CheckConstraint('added >= 0 AND skipped >= 0', name=op.f('ck_bank_import_counts_nonnegative')),
    sa.ForeignKeyConstraint(['bank_account_id'], ['bank_account.id'], name=op.f('fk_bank_import_bank_account_id_bank_account')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_bank_import_created_by_app_user')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bank_import'))
    )
    op.create_index(op.f('ix_bank_import_bank_account_id'), 'bank_import', ['bank_account_id'], unique=False)
    op.create_table('bank_line',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('bank_account_id', sa.Integer(), nullable=False),
    sa.Column('bank_import_id', sa.Integer(), nullable=True),
    sa.Column('posted_on', sa.Date(), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=False),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('ignored', sa.String(length=10), nullable=True),
    sa.Column('ignored_note', sa.String(length=200), nullable=True),
    sa.Column('reconciliation_id', sa.Integer(), nullable=True),
    *standard(),
    sa.CheckConstraint('amount <> 0', name=op.f('ck_bank_line_amount_nonzero')),
    sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name=op.f('ck_bank_line_fingerprint_hex')),
    sa.CheckConstraint("ignored IS NULL OR ignored IN ('transfer', 'interest', 'fee', 'other')", name=op.f('ck_bank_line_ignored')),
    sa.ForeignKeyConstraint(['bank_account_id'], ['bank_account.id'], name=op.f('fk_bank_line_bank_account_id_bank_account')),
    sa.ForeignKeyConstraint(['bank_import_id'], ['bank_import.id'], name=op.f('fk_bank_line_bank_import_id_bank_import')),
    sa.ForeignKeyConstraint(['reconciliation_id'], ['bank_reconciliation.id'], name=op.f('fk_bank_line_reconciliation_id_bank_reconciliation')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_bank_line_created_by_app_user')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bank_line')),
    sa.UniqueConstraint('bank_account_id', 'fingerprint', name=op.f('uq_bank_line_bank_account_id'))
    )
    op.create_index('ix_bank_line_account_posted', 'bank_line', ['bank_account_id', 'posted_on'], unique=False)
    op.create_index(op.f('ix_bank_line_bank_import_id'), 'bank_line', ['bank_import_id'], unique=False)
    op.create_index(op.f('ix_bank_line_reconciliation_id'), 'bank_line', ['reconciliation_id'], unique=False)
    op.create_table('bank_match',
    sa.Column('id', sa.Integer(), sa.Identity(always=False), nullable=False),
    sa.Column('bank_line_id', sa.Integer(), nullable=False),
    sa.Column('payment_id', sa.Integer(), nullable=True),
    sa.Column('expense_id', sa.Integer(), nullable=True),
    sa.Column('purchase_order_id', sa.Integer(), nullable=True),
    sa.Column('partner_entry_id', sa.Integer(), nullable=True),
    sa.Column('sales_tax_filing_id', sa.Integer(), nullable=True),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    *standard(),
    sa.CheckConstraint('num_nonnulls(payment_id, expense_id, purchase_order_id, partner_entry_id, sales_tax_filing_id) = 1', name=op.f('ck_bank_match_one_target')),
    sa.CheckConstraint('amount <> 0', name=op.f('ck_bank_match_amount_nonzero')),
    sa.ForeignKeyConstraint(['bank_line_id'], ['bank_line.id'], name=op.f('fk_bank_match_bank_line_id_bank_line')),
    sa.ForeignKeyConstraint(['payment_id'], ['payment.id'], name=op.f('fk_bank_match_payment_id_payment')),
    sa.ForeignKeyConstraint(['expense_id'], ['expense.id'], name=op.f('fk_bank_match_expense_id_expense')),
    sa.ForeignKeyConstraint(['purchase_order_id'], ['purchase_order.id'], name=op.f('fk_bank_match_purchase_order_id_purchase_order')),
    sa.ForeignKeyConstraint(['partner_entry_id'], ['partner_entry.id'], name=op.f('fk_bank_match_partner_entry_id_partner_entry')),
    sa.ForeignKeyConstraint(['sales_tax_filing_id'], ['sales_tax_filing.id'], name=op.f('fk_bank_match_sales_tax_filing_id_sales_tax_filing')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_bank_match_created_by_app_user')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bank_match'))
    )
    op.create_index(op.f('ix_bank_match_bank_line_id'), 'bank_match', ['bank_line_id'], unique=False)
    for col in TARGETS:
        op.create_index(f'uq_bank_match_{col}', 'bank_match', [col], unique=True,
                        postgresql_where=sa.text(f'{col} IS NOT NULL'))

    for sql in (BANK_LINE_GUARD, BANK_MATCH_GUARD, BANK_RECONCILIATION_GUARD):
        op.execute(sql)
    for fn in FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {fn}() FROM PUBLIC")
    op.execute("CREATE TRIGGER bank_line_guard BEFORE UPDATE OR DELETE ON bank_line "
               "FOR EACH ROW EXECUTE FUNCTION bank_line_guard()")
    op.execute("CREATE TRIGGER bank_match_guard BEFORE INSERT OR UPDATE OR DELETE ON bank_match "
               "FOR EACH ROW EXECUTE FUNCTION bank_match_guard()")
    op.execute("CREATE TRIGGER bank_reconciliation_guard BEFORE INSERT OR UPDATE OR DELETE "
               "ON bank_reconciliation FOR EACH ROW EXECUTE FUNCTION bank_reconciliation_guard()")

    for table in TABLES:
        attach_updated_at(table)
        attach_audit(table)
    grant_app('bank_account', ['SELECT', 'INSERT', 'UPDATE'])
    grant_app('bank_reconciliation', ['SELECT', 'INSERT', 'UPDATE'])
    grant_app('bank_import', ['SELECT', 'INSERT'])
    grant_app('bank_line', ['SELECT', 'INSERT', 'UPDATE', 'DELETE'])
    grant_app('bank_match', ['SELECT', 'INSERT', 'DELETE'])


def downgrade():
    op.drop_table('bank_match')
    op.drop_table('bank_line')
    op.drop_table('bank_import')
    op.drop_table('bank_reconciliation')
    op.drop_table('bank_account')
    for fn in FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
