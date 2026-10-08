from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import StandardColumns

CUSTOMER_KINDS = ("business", "individual")
PAYMENT_TERMS = {
    "due_on_receipt": "Due on receipt",
    "net_15": "Net 15",
    "net_30": "Net 30",
}
COMM_KINDS = ("call", "text", "email", "in_person", "note")

# Digits only, so "(555) 010-0123" and "555.010.0123" find each other.
PHONE_DIGITS = "regexp_replace(coalesce(phone, ''), '[^0-9]', '', 'g')"


class AddressColumns:
    address_line1: Mapped[str | None] = mapped_column(String(160))
    address_line2: Mapped[str | None] = mapped_column(String(160))
    city: Mapped[str | None] = mapped_column(String(80))
    region: Mapped[str | None] = mapped_column(String(40))
    postal_code: Mapped[str | None] = mapped_column(String(20))

    @property
    def address_lines(self):
        last = " ".join(p for p in (
            ", ".join(p for p in (self.city, self.region) if p), self.postal_code) if p)
        return [line for line in (self.address_line1, self.address_line2, last) if line]


class Customer(StandardColumns, AddressColumns, db.Model):
    __tablename__ = "customer"
    __audited__ = ()
    __table_args__ = (
        CheckConstraint("kind IN ('business', 'individual')", name="kind"),
        CheckConstraint("payment_terms IN ('due_on_receipt', 'net_15', 'net_30')",
                        name="payment_terms"),
        # The shop itself is one customer row (it owns exchange units); only one.
        Index("uq_customer_is_shop", "is_shop", unique=True, postgresql_where=text("is_shop")),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    dba: Mapped[str | None] = mapped_column(String(160))
    billing_email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(40))
    phone_digits: Mapped[str] = mapped_column(String(40), Computed(PHONE_DIGITS, persisted=True))
    payment_terms: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'due_on_receipt'")
    )
    tax_exempt: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    exempt_cert_no: Mapped[str | None] = mapped_column(String(60))
    exempt_cert_expires: Mapped[date | None] = mapped_column(Date)
    sends_1099: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    referral_source: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    is_shop: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())

    @property
    def display_name(self):
        return f"{self.name} ({self.dba})" if self.dba else self.name


class Contact(StandardColumns, db.Model):
    __tablename__ = "contact"
    __audited__ = ()
    __table_args__ = (
        # Target of composite FKs that keep a site's contact within the same customer.
        UniqueConstraint("id", "customer_id", name="uq_contact_id_customer"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customer.id"), nullable=False,
                                             index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str | None] = mapped_column(String(60))
    phone: Mapped[str | None] = mapped_column(String(40))
    phone_digits: Mapped[str] = mapped_column(String(40), Computed(PHONE_DIGITS, persisted=True))
    email: Mapped[str | None] = mapped_column(String(254))
    is_billing: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    is_site: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class Site(StandardColumns, AddressColumns, db.Model):
    __tablename__ = "site"
    __audited__ = ()
    __table_args__ = (
        UniqueConstraint("id", "customer_id", name="uq_site_id_customer"),
        ForeignKeyConstraint(
            ["site_contact_id", "customer_id"], ["contact.id", "contact.customer_id"],
            name="fk_site_contact_same_customer",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customer.id"), nullable=False,
                                             index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    site_contact_id: Mapped[int | None] = mapped_column(Integer)
    # Hours and who lets you in. Never alarm codes.
    access_notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())


class CommLog(StandardColumns, db.Model):
    """Calls, texts, emails, visits. Append-only: the app role may INSERT and SELECT only;
    a correction is a new entry. created_by is who logged it; `at` is when it happened."""

    __tablename__ = "comm_log"
    __table_args__ = (
        CheckConstraint("kind IN ('call', 'text', 'email', 'in_person', 'note')", name="kind"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customer.id"), nullable=False,
                                             index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                         server_default=func.now())
    summary: Mapped[str] = mapped_column(Text, nullable=False)
