from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import StandardColumns

LEDGER_KINDS = {
    "contribution": "Put money in",
    "draw": "Took money out (draw)",
    "reimbursement": "Paid back for business costs",
    "distribution": "Profit paid out (distribution)",
}
SPLIT_MODES = {"equal": "Equal shares", "hours": "By labor hours", "custom": "Custom percentages"}
RECEIPT_TYPES = ("image/jpeg", "application/pdf")

_VOID = ("(voided_at IS NULL) = (void_reason IS NULL)", "void_has_reason")
_RECEIPT = ("(receipt_sha256 IS NULL) = (receipt_type IS NULL) AND "
            "(receipt_sha256 IS NULL OR receipt_sha256 ~ '^[0-9a-f]{64}$') AND "
            "(receipt_type IS NULL OR receipt_type IN ('image/jpeg', 'application/pdf'))",
            "receipt_complete")


class ExpenseCategory(StandardColumns, db.Model):
    """Where an expense goes, close to the Schedule C lines; editable. `code` marks the
    few the app books into itself (from purchase orders)."""

    __tablename__ = "expense_category"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("name", name="uq_expense_category_name"),
        UniqueConstraint("code", name="uq_expense_category_code"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    code: Mapped[str | None] = mapped_column(String(20))
    schedule_c: Mapped[str | None] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class Expense(StandardColumns, db.Model):
    """Money out that isn't inventory. Paid by a partner: they're owed it back. Edited
    in the open (audited) and never deleted; a wrong one is voided with a reason."""

    __tablename__ = "expense"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("tax_included >= 0 AND tax_included <= amount", name="tax_within_amount"),
        CheckConstraint(_VOID[0], name=_VOID[1]),
        CheckConstraint(_RECEIPT[0], name=_RECEIPT[1]),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    spent_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payee: Mapped[str] = mapped_column(String(120), nullable=False)
    category_id: Mapped[int] = mapped_column(Integer, ForeignKey("expense_category.id"),
                                             nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    tax_included: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False,
                                                  server_default=text("0"))
    # Null: the business account. A partner's id: their own money, owed back.
    paid_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.id"),
                                                        index=True)
    work_order_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("work_order.id"),
                                                      index=True)
    note: Mapped[str | None] = mapped_column(Text)
    receipt_sha256: Mapped[str | None] = mapped_column(String(64))
    receipt_type: Mapped[str | None] = mapped_column(String(40))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(String(300))


class MileageRate(StandardColumns, db.Model):
    """The standard mileage rate from a date on (the IRS can change it mid-year, as it did
    on 2026-07-01). A trip is valued at the rate in effect on its date."""

    __tablename__ = "mileage_rate"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("effective_from", name="uq_mileage_rate_effective_from"),
        CheckConstraint("rate > 0 AND rate < 10", name="rate_range"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    # Dollars per mile: 0.7250 is 72.5 cents.
    rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    source: Mapped[str | None] = mapped_column(String(200))


class Trip(StandardColumns, db.Model):
    """Business miles. In a partner's own vehicle, the business owes them the miles at
    the rate (CPA to confirm the arrangement)."""

    __tablename__ = "trip"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint("miles > 0 AND miles < 10000", name="miles_range"),
        CheckConstraint(_VOID[0], name=_VOID[1]),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    trip_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_user.id"), nullable=False,
                                         index=True)
    miles: Mapped[Decimal] = mapped_column(Numeric(8, 1), nullable=False)
    from_place: Mapped[str | None] = mapped_column(String(120))
    to_place: Mapped[str | None] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(200), nullable=False)
    own_vehicle: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    work_order_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("work_order.id"),
                                                      index=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(String(300))


class PartnerEntry(StandardColumns, db.Model):
    """The partner ledger: money a partner put in or took out, and business costs paid
    back to them. What they're owed is worked out from expenses, POs and trips, never
    typed. Corrections are voids."""

    __tablename__ = "partner_entry"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint("kind IN ('contribution', 'draw', 'reimbursement', 'distribution')",
                        name="kind"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(_VOID[0], name=_VOID[1]),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_user.id"), nullable=False,
                                         index=True)
    entry_on: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(14), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(String(300))


class SalesTaxFiling(StandardColumns, db.Model):
    """A sales-tax return as filed. Taxable sales and tax collected are copied from the
    invoices when it's recorded, so later voids can't change what was filed. Periods
    can't overlap (an exclusion constraint)."""

    __tablename__ = "sales_tax_filing"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint("period_end >= period_start", name="period_order"),
        CheckConstraint("taxable_sales >= 0 AND tax_collected >= 0 AND tax_due >= 0",
                        name="amounts_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    taxable_sales: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    tax_collected: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    tax_due: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    filed_on: Mapped[date | None] = mapped_column(Date)
    paid_on: Mapped[date | None] = mapped_column(Date)
    confirmation: Mapped[str | None] = mapped_column(String(60))
    note: Mapped[str | None] = mapped_column(String(300))
