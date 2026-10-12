from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import StandardColumns
from app.models.customer import PAYMENT_TERMS
from app.models.parts import UNIT_COST
from app.models.work import ALL_LABOR_MODES, LINE_KINDS

TAX_RATE = Numeric(7, 6)
INVOICE_STATUSES = {
    "draft": "Draft",
    "issued": "Issued",
    "partially_paid": "Partly paid",
    "paid": "Paid",
    "void": "Void",
    "written_off": "Written off",
}
OPEN_INVOICE_STATUSES = ("issued", "partially_paid")
# Frozen at issue. not_collected: the shop isn't collecting sales tax (no permit yet;
# owner, 2026-10-12), so the invoice says so and taxable lines stay flagged.
TAX_STATUSES = {"collected": "Sales tax collected", "not_collected": "Sales tax not collected",
                "exempt": "Tax exempt"}


def _in(column, values):
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class TaxJurisdiction(StandardColumns, db.Model):
    """A sales-tax rate where the customer receives the work (destination sourcing). The
    owner enters rates from the OTC; none are seeded. An invoice copies the name and
    rates when it's issued, so changing a row never changes an issued invoice."""

    __tablename__ = "tax_jurisdiction"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("name", "effective_from", name="uq_tax_jurisdiction_name_from"),
        CheckConstraint("state_rate >= 0 AND county_rate >= 0 AND city_rate >= 0 "
                        "AND other_rate >= 0", name="rates_nonnegative"),
        CheckConstraint("state_rate + county_rate + city_rate + other_rate < 0.25",
                        name="combined_below_25pct"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from",
                        name="effective_order"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    state_rate: Mapped[Decimal] = mapped_column(TAX_RATE, nullable=False, server_default=text("0"))
    county_rate: Mapped[Decimal] = mapped_column(TAX_RATE, nullable=False,
                                                 server_default=text("0"))
    city_rate: Mapped[Decimal] = mapped_column(TAX_RATE, nullable=False, server_default=text("0"))
    other_rate: Mapped[Decimal] = mapped_column(TAX_RATE, nullable=False, server_default=text("0"))
    combined_rate: Mapped[Decimal] = mapped_column(TAX_RATE, Computed(
        "state_rate + county_rate + city_rate + other_rate", persisted=True))
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_note: Mapped[str | None] = mapped_column(String(300))


class Invoice(StandardColumns, db.Model):
    """A bill. Built as a draft and issued in the same transaction: issuing assigns the
    gapless number, freezes bill-to, tax and terms, and stores the PDF the customer gets.
    Once issued, a trigger lets only status, amount_paid, balance and the void fields
    change; it also checks, at issue, that the subtotals and tax match the lines."""

    __tablename__ = "invoice"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("number"),
        CheckConstraint("number ~ '^INV-[0-9]{4}-[0-9]{4,}$'", name="number_format"),
        CheckConstraint(_in("status", INVOICE_STATUSES), name="status"),
        CheckConstraint(_in("tax_status", TAX_STATUSES), name="tax_status"),
        CheckConstraint(_in("payment_terms", PAYMENT_TERMS), name="payment_terms"),
        CheckConstraint("(status = 'draft') = (number IS NULL)", name="number_iff_issued"),
        CheckConstraint("status = 'draft' OR (issued_at IS NOT NULL AND issue_date IS NOT NULL "
                        "AND due_date IS NOT NULL AND pdf_sha256 IS NOT NULL)",
                        name="issued_complete"),
        CheckConstraint("due_date >= issue_date", name="due_on_or_after_issue"),
        CheckConstraint("subtotal_taxable >= 0 AND subtotal_nontaxable >= 0 AND tax >= 0",
                        name="amounts_nonnegative"),
        CheckConstraint("total = subtotal_taxable + subtotal_nontaxable + tax",
                        name="total_adds_up"),
        CheckConstraint("tax_status = 'collected' OR tax = 0", name="tax_only_when_collected"),
        CheckConstraint("tax_status <> 'collected' OR subtotal_taxable = 0 "
                        "OR tax_jurisdiction IS NOT NULL", name="collected_has_jurisdiction"),
        CheckConstraint("tax_status <> 'exempt' OR exempt_cert_no IS NOT NULL",
                        name="exempt_has_certificate"),
        CheckConstraint("amount_paid >= 0", name="paid_nonnegative"),
        CheckConstraint("status IN ('draft', 'void') OR balance = total - amount_paid",
                        name="balance_adds_up"),
        CheckConstraint("status <> 'void' OR balance = 0", name="void_owes_nothing"),
        CheckConstraint("(status = 'void') = (voided_at IS NOT NULL) AND "
                        "(voided_at IS NULL) = (void_reason IS NULL)", name="void_complete"),
        CheckConstraint("pdf_sha256 ~ '^[0-9a-f]{64}$'", name="pdf_sha256_hex"),
        CheckConstraint("report_sha256 ~ '^[0-9a-f]{64}$'", name="report_sha256_hex"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    number: Mapped[str | None] = mapped_column(String(20))
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customer.id"), nullable=False,
                                             index=True)
    work_order_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("work_order.id"),
                                                      index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'draft'"))
    # The date printed (and the number's year). Earlier than issued_at's date only with a
    # reason: the first real invoices are entered after the fact (owner, 2026-10-12).
    issue_date: Mapped[date | None] = mapped_column(Date)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backdate_reason: Mapped[str | None] = mapped_column(String(300))
    due_date: Mapped[date | None] = mapped_column(Date)
    payment_terms: Mapped[str] = mapped_column(String(16), nullable=False)
    bill_to: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ship_to: Mapped[dict | None] = mapped_column(JSONB)
    tax_status: Mapped[str] = mapped_column(String(16), nullable=False)
    tax_jurisdiction_id: Mapped[int | None] = mapped_column(Integer,
                                                            ForeignKey("tax_jurisdiction.id"))
    tax_jurisdiction: Mapped[dict | None] = mapped_column(JSONB)
    exempt_cert_no: Mapped[str | None] = mapped_column(String(60))
    subtotal_taxable: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False,
                                                      server_default=text("0"))
    subtotal_nontaxable: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False,
                                                         server_default=text("0"))
    tax: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text("0"))
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False,
                                                 server_default=text("0"))
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False,
                                             server_default=text("0"))
    terms_snapshot: Mapped[str | None] = mapped_column(Text)
    customer_note: Mapped[str | None] = mapped_column(Text)
    internal_note: Mapped[str | None] = mapped_column(Text)
    # The exact documents the customer got, in the file store by sha256.
    pdf_sha256: Mapped[str | None] = mapped_column(String(64))
    report_sha256: Mapped[str | None] = mapped_column(String(64))
    void_reason: Mapped[str | None] = mapped_column(String(300))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.id"))


class InvoiceLine(StandardColumns, db.Model):
    """A copy of a work-order line as it was billed. line_total is computed by Postgres
    (qty × unit_price, rounded half-up; a discount is negative), so it can't disagree
    with the numbers. unit_cost is for margins and never printed."""

    __tablename__ = "invoice_line"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("invoice_id", "position", name="uq_invoice_line_position"),
        CheckConstraint(_in("kind", LINE_KINDS), name="kind"),
        CheckConstraint("(kind = 'labor') = (labor_mode IS NOT NULL)", name="labor_mode_iff_labor"),
        CheckConstraint("labor_mode IS NULL OR " + _in("labor_mode", ALL_LABOR_MODES),
                        name="labor_mode"),
        CheckConstraint("labor_mode IS DISTINCT FROM 'fixed' OR qty = 1", name="fixed_qty_one"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("unit_price >= 0", name="price_nonnegative"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="cost_nonnegative"),
        CheckConstraint("warranty_days >= 0", name="warranty_nonnegative"),
        CheckConstraint("(warranty_until IS NULL) = (warranty_days = 0)", name="warranty_until"),
        CheckConstraint("NOT customer_supplied OR (kind = 'part' AND unit_price = 0 AND "
                        "warranty_days = 0)", name="customer_supplied_part"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    invoice_id: Mapped[int] = mapped_column(Integer, ForeignKey("invoice.id"), nullable=False,
                                            index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # The job heading it printed under: "s-0042 · Galaga — Dead screen".
    group_label: Mapped[str | None] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    labor_mode: Mapped[str | None] = mapped_column(String(8))
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    scope: Mapped[str | None] = mapped_column(Text)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), Computed(
        "CASE WHEN kind = 'discount' THEN -round(qty * unit_price, 2) "
        "ELSE round(qty * unit_price, 2) END", persisted=True))
    taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    customer_supplied: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                                    server_default=false())
    unit_cost: Mapped[Decimal | None] = mapped_column(UNIT_COST)
    part_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("part.id"))
    tech_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.id"))
    warranty_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    warranty_until: Mapped[date | None] = mapped_column(Date)
    # Where it came from. No foreign key: after a void, the work-order line may be edited
    # or removed, and this frozen row can't follow it (stock_move.wo_job_id is the same).
    wo_line_id: Mapped[int | None] = mapped_column(Integer, index=True)
