"""Bank reconciliation (Phase 9c): the shop's own accounts (a wallet like Venmo counts),
lines imported from their CSV exports, each line matched to what the app already knows
(a payment, a refund, an expense, a purchase order, a partner's money, a sales-tax
payment) or set aside, and reconciliations: once a statement balances, its lines and
matches are locked by triggers (migration 0022). Only the latest reconciliation of an
account can be undone, by the owner, with a reason.

And the accountant's names for things (account_map): what each kind of money is called in
a chart of accounts, for the general-journal export. Defaults live in app/books/journal.py;
a row here is an owner's override."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import StandardColumns

BANK_KINDS = {"checking": "Checking", "savings": "Savings", "card": "Credit card",
              "wallet": "Wallet (Venmo, PayPal, Cash App)"}
IGNORE_REASONS = {"transfer": "A transfer between our own accounts", "interest": "Interest",
                  "fee": "A bank fee (no expense for it)", "other": "Something else"}
# What a bank line can be matched to: (column, label).
MATCH_TARGETS = (("payment_id", "Payment"), ("expense_id", "Expense"),
                 ("purchase_order_id", "Purchase order"), ("partner_entry_id", "Partner money"),
                 ("sales_tax_filing_id", "Sales tax payment"))


def _in(column, values):
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class BankAccount(StandardColumns, db.Model):
    """One of the shop's own accounts. Reconciling starts from opening_balance on
    opening_on; `mapping` is how its CSV export's columns were last read."""

    __tablename__ = "bank_account"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("name"),
        CheckConstraint(_in("kind", BANK_KINDS), name="kind"),
        CheckConstraint("last4 IS NULL OR last4 ~ '^[0-9]{4}$'", name="last4_digits"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    last4: Mapped[str | None] = mapped_column(String(4))
    opening_on: Mapped[date] = mapped_column(Date, nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False,
                                                     server_default=text("0"))
    mapping: Mapped[dict | None] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class BankImport(StandardColumns, db.Model):
    """One CSV file imported into an account; the file itself is kept in the file store."""

    __tablename__ = "bank_import"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint("file_sha256 ~ '^[0-9a-f]{64}$'", name="file_sha256_hex"),
        CheckConstraint("added >= 0 AND skipped >= 0", name="counts_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(Integer, ForeignKey("bank_account.id"),
                                                 nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    added: Mapped[int] = mapped_column(Integer, nullable=False)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False)


class BankLine(StandardColumns, db.Model):
    """A line from a bank export: money in (positive) or out (negative). Its date, amount
    and description never change; `fingerprint` keeps a re-import from adding it twice."""

    __tablename__ = "bank_line"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("bank_account_id", "fingerprint"),
        CheckConstraint("amount <> 0", name="amount_nonzero"),
        CheckConstraint("amount <> 'NaN'", name="amount_finite"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_hex"),
        CheckConstraint(f"ignored IS NULL OR {_in('ignored', IGNORE_REASONS)}", name="ignored"),
        Index("ix_bank_line_account_posted", "bank_account_id", "posted_on"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(Integer, ForeignKey("bank_account.id"),
                                                 nullable=False)
    bank_import_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("bank_import.id"),
                                                       index=True)
    posted_on: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    ignored: Mapped[str | None] = mapped_column(String(10))
    ignored_note: Mapped[str | None] = mapped_column(String(200))
    reconciliation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("bank_reconciliation.id"), index=True)


class BankMatch(StandardColumns, db.Model):
    """A bank line is (part of) this app record. One line can carry several (a deposit of
    three checks); each record is matched once, in one account. `amount` is the record's
    share as the bank sees it (signed like the line)."""

    __tablename__ = "bank_match"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint("num_nonnulls(payment_id, expense_id, purchase_order_id, "
                        "partner_entry_id, sales_tax_filing_id) = 1", name="one_target"),
        CheckConstraint("amount <> 0", name="amount_nonzero"),
        CheckConstraint("amount <> 'NaN'", name="amount_finite"),
        *(Index(f"uq_bank_match_{col}", col, unique=True,
                postgresql_where=text(f"{col} IS NOT NULL")) for col, _ in MATCH_TARGETS),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    bank_line_id: Mapped[int] = mapped_column(Integer, ForeignKey("bank_line.id"),
                                              nullable=False, index=True)
    payment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("payment.id"))
    expense_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("expense.id"))
    purchase_order_id: Mapped[int | None] = mapped_column(Integer,
                                                          ForeignKey("purchase_order.id"))
    partner_entry_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("partner_entry.id"))
    sales_tax_filing_id: Mapped[int | None] = mapped_column(Integer,
                                                            ForeignKey("sales_tax_filing.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class BankReconciliation(StandardColumns, db.Model):
    """A statement that balanced: opening balance + its lines = ending_balance. Its lines
    point here and are locked. Undone (the latest only) keeps the row, marked."""

    __tablename__ = "bank_reconciliation"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint("(undone_at IS NULL) = (undo_reason IS NULL)", name="undo_has_reason"),
        Index("uq_bank_reconciliation_live", "bank_account_id", "statement_end", unique=True,
              postgresql_where=text("undone_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(Integer, ForeignKey("bank_account.id"),
                                                 nullable=False, index=True)
    statement_end: Mapped[date] = mapped_column(Date, nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    ending_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undo_reason: Mapped[str | None] = mapped_column(String(300))


class AccountMap(StandardColumns, db.Model):
    """The owner's (or an accountant's) name for one kind of money in the general journal:
    key is e.g. 'income:labor', 'category:7', 'bank:2', 'method:cash', 'capital:3'."""

    __tablename__ = "account_map"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("key"),
        CheckConstraint("key ~ '^[a-z_]+(:[a-z0-9_]+)?$'", name="key_format"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    number: Mapped[str | None] = mapped_column(String(20))
