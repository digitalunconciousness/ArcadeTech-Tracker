"""Money in and out: payments and what they pay, credit on account, reversals, refunds,
credit memos, receipts and statements. Callers commit; an InvoiceError's message is shown
as is.

Everything here is append-only. Postgres checks every row (payment_guard,
allocation_guard) and keeps each invoice's paid, credited, balance and status equal to
what's applied to it (allocation_apply); this module picks the amounts and says what's
wrong in words before the database has to.

Credit on account is whatever a receipt or a credit memo hasn't paid on an invoice or
had refunded: a deposit, an overpayment, a credit larger than what was still owed."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select, update

from app.billing.service import ZERO, InvoiceError, _lock, shop
from app.extensions import db
from app.models import (
    OPEN_INVOICE_STATUSES,
    CreditMemo,
    CreditMemoLine,
    Customer,
    DocLink,
    Estimate,
    Invoice,
    Payment,
    PaymentAllocation,
)
from app.numbering import issue_number
from app.pricing import round_money
from app.timeutil import local_today, utcnow
from app.work import files

# Once an invoice is paid its links last this much longer (owner, 2026-10-12).
PAID_LINK_DAYS = 90
AGING = ((0, "Current"), (30, "1–30 days late"), (60, "31–60 days late"),
         (90, "61–90 days late"), (None, "Over 90 days late"))


def lock_customer(customer_id):
    """Money for one customer moves one transaction at a time (the triggers take the same
    lock; taking it first keeps the order the same everywhere)."""
    db.session.execute(select(Customer.id).where(Customer.id == customer_id).with_for_update())


def open_invoices(customer_id):
    return db.session.scalars(select(Invoice).where(
        Invoice.customer_id == customer_id, Invoice.status.in_(OPEN_INVOICE_STATUSES))
        .order_by(Invoice.issue_date, Invoice.id)).all()


# --- credit on account -----------------------------------------------------------------------

@dataclass
class Source:
    """Unapplied money: a receipt or an issued credit memo, and how much of it is left."""
    obj: object
    left: Decimal

    @property
    def is_payment(self):
        return isinstance(self.obj, Payment)

    @property
    def when(self):
        return self.obj.received_on if self.is_payment else self.obj.issue_date

    @property
    def label(self):
        if self.is_payment:
            return f"Payment #{self.obj.id} ({self.obj.received_on})"
        return f"{self.obj.number} ({self.obj.issue_date})"


def _sums(column, key, where):
    return defaultdict(lambda: ZERO, db.session.execute(
        select(key, func.sum(column)).where(where, key.is_not(None)).group_by(key)).all())


def credit_sources(customer_id):
    """What this customer has paid or been credited that isn't on an invoice yet, oldest
    first."""
    applied_p = _sums(PaymentAllocation.amount, PaymentAllocation.payment_id,
                      PaymentAllocation.payment_id.in_(
                          select(Payment.id).where(Payment.customer_id == customer_id)))
    applied_cm = _sums(PaymentAllocation.amount, PaymentAllocation.credit_memo_id,
                       PaymentAllocation.credit_memo_id.in_(
                           select(CreditMemo.id).where(CreditMemo.customer_id == customer_id)))
    refunded_p = _sums(Payment.amount, Payment.refund_of_payment_id,
                       Payment.customer_id == customer_id)
    refunded_cm = _sums(Payment.amount, Payment.refund_of_credit_memo_id,
                        Payment.customer_id == customer_id)
    reversed_ids = set(db.session.scalars(select(Payment.reverses_payment_id).where(
        Payment.customer_id == customer_id, Payment.reverses_payment_id.is_not(None))))
    out = []
    for p in db.session.scalars(select(Payment).where(Payment.customer_id == customer_id,
                                                      Payment.kind == "receipt")):
        left = ZERO if p.id in reversed_ids else p.amount - applied_p[p.id] + refunded_p[p.id]
        if left > 0:
            out.append(Source(p, left))
    for cm in db.session.scalars(select(CreditMemo).where(CreditMemo.customer_id == customer_id,
                                                          CreditMemo.status == "issued")):
        left = cm.total - applied_cm[cm.id] + refunded_cm[cm.id]
        if left > 0:
            out.append(Source(cm, left))
    return sorted(out, key=lambda s: (s.when, s.is_payment is False, s.obj.id))


def credit_available(customer_id):
    return sum((s.left for s in credit_sources(customer_id)), ZERO)


def source_left(obj):
    for s in credit_sources(obj.customer_id):
        if type(s.obj) is type(obj) and s.obj.id == obj.id:
            return s.left
    return ZERO


# --- applying money --------------------------------------------------------------------------

def _apply(inv, obj, amount):
    alloc = PaymentAllocation(invoice_id=inv.id, amount=amount,
                              payment_id=obj.id if isinstance(obj, Payment) else None,
                              credit_memo_id=obj.id if isinstance(obj, CreditMemo) else None)
    db.session.add(alloc)
    db.session.flush()
    return alloc


def _settle(invoice_ids):
    """Reload invoices the triggers changed; a paid one's links run out 90 days on."""
    for inv_id in set(invoice_ids):
        inv = db.session.get(Invoice, inv_id)
        db.session.refresh(inv)
        if inv.status == "paid":
            db.session.execute(update(DocLink).where(
                DocLink.invoice_id == inv.id, DocLink.revoked_at.is_(None),
                DocLink.expires_at > utcnow() + timedelta(days=PAID_LINK_DAYS))
                .values(expires_at=utcnow() + timedelta(days=PAID_LINK_DAYS)))
    db.session.flush()


def _check_day(received_on):
    if received_on > local_today():
        raise InvoiceError("The date can't be in the future.")


def record_payment(customer, *, amount, method, received_on, reference=None, note=None,
                   applied=None, estimate_id=None):
    """Money received. `applied` is {invoice_id: amount}; whatever isn't applied stays on
    the customer's account (a deposit, an overpayment). Returns the payment."""
    lock_customer(customer.id)
    _check_day(received_on)
    applied = {k: v for k, v in (applied or {}).items() if v}
    if amount is None or amount <= 0:
        raise InvoiceError("Enter the amount received.")
    if sum(applied.values(), ZERO) > amount:
        raise InvoiceError("That applies more than was received.")
    invoices = {inv.id: inv for inv in open_invoices(customer.id)}
    for inv_id, value in applied.items():
        inv = invoices.get(inv_id)
        if inv is None:
            raise InvoiceError("One of those invoices is paid, void or someone else's. Reload.")
        if value < 0 or value > inv.balance:
            raise InvoiceError(f"{inv.number} has {inv.balance} left to pay.")
    if estimate_id is not None:
        est = db.session.get(Estimate, estimate_id)
        if est is None or est.customer_id != customer.id:
            raise InvoiceError("That estimate is another customer's.")
    payment = Payment(customer_id=customer.id, kind="receipt", received_on=received_on,
                      amount=amount, method=method, reference=reference, note=note,
                      estimate_id=estimate_id)
    db.session.add(payment)
    db.session.flush()
    for inv_id, value in applied.items():
        _apply(invoices[inv_id], payment, value)
    _settle(applied)
    return payment


def spread(customer_id, amount):
    """Oldest invoice first: how `amount` would pay down the open invoices."""
    out, left = {}, amount or ZERO
    for inv in open_invoices(customer_id):
        if left <= 0:
            break
        out[inv.id] = min(left, inv.balance)
        left -= out[inv.id]
    return out


def deposit_first(inv):
    """Sort key for credit sources: deposits taken on this job's estimates first."""
    estimates = set()
    if inv.work_order_id:
        estimates = set(db.session.scalars(select(Estimate.id).where(
            Estimate.work_order_id == inv.work_order_id)))

    def key(source):
        is_deposit = source.is_payment and source.obj.estimate_id in estimates
        return (not is_deposit, source.when, source.obj.id)
    return key


def apply_credit(inv, limit=None):
    """Put the customer's credit on this invoice, deposits for this job first, up to the
    balance (or `limit`). Returns the amount applied."""
    lock_customer(inv.customer_id)
    inv = _lock(Invoice, inv.id)
    if inv.status not in OPEN_INVOICE_STATUSES:
        raise InvoiceError(f"{inv.number} is {inv.status.replace('_', ' ')}: nothing to pay.")
    want = inv.balance if limit is None else min(limit, inv.balance)
    applied = ZERO
    for source in sorted(credit_sources(inv.customer_id), key=deposit_first(inv)):
        if applied >= want:
            break
        value = min(source.left, want - applied)
        _apply(inv, source.obj, value)
        applied += value
    if applied == 0:
        raise InvoiceError("This customer has no credit to apply.")
    _settle([inv.id])
    return applied


def apply_source(obj, inv, amount=None):
    """Apply what's left of one receipt or credit memo to an invoice."""
    lock_customer(obj.customer_id)
    inv = _lock(Invoice, inv.id)
    if inv.customer_id != obj.customer_id or inv.status not in OPEN_INVOICE_STATUSES:
        raise InvoiceError("Pick an open invoice of the same customer.")
    left = source_left(obj)
    value = min(left, inv.balance) if amount is None else amount
    if value <= 0 or value > left or value > inv.balance:
        raise InvoiceError(f"Up to {min(left, inv.balance)} can go on {inv.number}.")
    _apply(inv, obj, value)
    _settle([inv.id])
    return value


# --- undoing and paying back -----------------------------------------------------------------

def reversal_of(payment):
    return db.session.scalar(select(Payment).where(Payment.reverses_payment_id == payment.id))


def refunds_of(obj):
    column = Payment.refund_of_payment_id if isinstance(obj, Payment) \
        else Payment.refund_of_credit_memo_id
    return db.session.scalars(select(Payment).where(column == obj.id).order_by(Payment.id)).all()


def reverse(payment, *, reason, received_on=None):
    """Undo a receipt (a bounced check, a payment entered by mistake): a reversal for the
    whole amount takes back everything it paid on invoices. Returns the reversal."""
    lock_customer(payment.customer_id)
    if payment.kind != "receipt":
        raise InvoiceError("Only a payment received can be reversed.")
    if reversal_of(payment) is not None:
        raise InvoiceError(f"Payment #{payment.id} is already reversed.")
    if refunds_of(payment):
        raise InvoiceError(f"Part of payment #{payment.id} was refunded, so it can't be "
                           "reversed.")
    if not reason:
        raise InvoiceError("Say why it's reversed.")
    day = received_on or local_today()
    _check_day(day)
    rev = Payment(customer_id=payment.customer_id, kind="reversal", received_on=day,
                  amount=-payment.amount, method=payment.method, reference=payment.reference,
                  note=reason, reverses_payment_id=payment.id)
    db.session.add(rev)
    db.session.flush()
    nets = db.session.execute(
        select(PaymentAllocation.invoice_id, func.sum(PaymentAllocation.amount))
        .where(PaymentAllocation.payment_id == payment.id)
        .group_by(PaymentAllocation.invoice_id)
        .having(func.sum(PaymentAllocation.amount) != 0)).all()
    for inv_id, net in nets:
        _apply(db.session.get(Invoice, inv_id), rev, -net)
    _settle([inv_id for inv_id, _ in nets])
    return rev


def refund(obj, *, amount, method, reason, reference=None, received_on=None):
    """Pay credit back out of one receipt or credit memo."""
    lock_customer(obj.customer_id)
    left = source_left(obj)
    if amount is None or amount <= 0:
        raise InvoiceError("Enter the amount refunded.")
    if amount > left:
        raise InvoiceError(f"Only {left} of it is left to refund.")
    if not reason:
        raise InvoiceError("Say why it's refunded.")
    day = received_on or local_today()
    _check_day(day)
    out = Payment(customer_id=obj.customer_id, kind="refund", received_on=day, amount=-amount,
                  method=method, reference=reference, note=reason,
                  refund_of_payment_id=obj.id if isinstance(obj, Payment) else None,
                  refund_of_credit_memo_id=obj.id if isinstance(obj, CreditMemo) else None)
    db.session.add(out)
    db.session.flush()
    return out


# --- credit memos ----------------------------------------------------------------------------

def creditable(inv):
    """How much more can be credited on an invoice: what it billed less earlier credits."""
    done = db.session.scalar(select(func.coalesce(func.sum(CreditMemo.total), 0)).where(
        CreditMemo.invoice_id == inv.id, CreditMemo.status == "issued"))
    return inv.total - done


def issue_credit_memo(inv, *, reason, lines):
    """A credit against an issued invoice. `lines` is [(description, amount, taxable)].
    Tax comes back at the invoice's frozen rate when the invoice collected it. The credit
    pays down the invoice; any rest is credit on the customer's account."""
    from app.billing import docs

    lock_customer(inv.customer_id)
    inv = _lock(Invoice, inv.id)
    if inv.status in ("draft", "void"):
        raise InvoiceError(f"{inv.number or 'A draft'} is {inv.status}: there's nothing to credit.")
    if not reason:
        raise InvoiceError("Say why it's credited.")
    lines = [(d, a, t) for d, a, t in lines if d or a]
    if not lines or any(not d or not a or a <= 0 for d, a, _ in lines):
        raise InvoiceError("Each line needs a description and an amount.")
    taxable = sum((a for _, a, t in lines if t), ZERO)
    other = sum((a for _, a, t in lines if not t), ZERO)
    rate = Decimal(inv.tax_jurisdiction["combined_rate"]) \
        if inv.tax_status == "collected" and inv.tax_jurisdiction else None
    tax = round_money(taxable * rate) if rate is not None else ZERO
    total = taxable + other + tax
    if total > creditable(inv):
        raise InvoiceError(f"Only {creditable(inv)} of {inv.number} is left to credit.")
    cm = CreditMemo(customer_id=inv.customer_id, invoice_id=inv.id, status="draft", reason=reason,
                    subtotal_taxable=taxable, subtotal_nontaxable=other, tax=tax, total=total,
                    tax_rate=rate)
    db.session.add(cm)
    db.session.flush()
    rows = [CreditMemoLine(credit_memo_id=cm.id, position=i, description=d, qty=Decimal(1),
                           unit_price=a, taxable=t) for i, (d, a, t) in enumerate(lines, 1)]
    db.session.add_all(rows)
    db.session.flush()
    customer = db.session.get(Customer, inv.customer_id)
    number = issue_number("CM")
    with db.session.no_autoflush:   # the number and the issued status land together
        cm.number, cm.status, cm.issued_at, cm.issue_date = number, "issued", utcnow(), \
            local_today()
        cm.pdf_sha256 = files.store_pdf(docs.credit_memo_pdf(cm, rows, inv, customer))
    db.session.flush()   # credit_memo_frozen() checks it here
    if inv.status in OPEN_INVOICE_STATUSES:
        _apply(inv, cm, min(cm.total, inv.balance))
        _settle([inv.id])
    return cm


def credit_memo_lines(cm):
    return db.session.scalars(select(CreditMemoLine).where(CreditMemoLine.credit_memo_id == cm.id)
                              .order_by(CreditMemoLine.position)).all()


# --- reading ---------------------------------------------------------------------------------

def applied_to(inv):
    """[(allocation, payment or None, credit memo or None)] on an invoice, in order."""
    return db.session.execute(
        select(PaymentAllocation, Payment, CreditMemo)
        .outerjoin(Payment, Payment.id == PaymentAllocation.payment_id)
        .outerjoin(CreditMemo, CreditMemo.id == PaymentAllocation.credit_memo_id)
        .where(PaymentAllocation.invoice_id == inv.id).order_by(PaymentAllocation.id)).all()


def applications(obj):
    """[(allocation, invoice)] for one payment or credit memo."""
    column = PaymentAllocation.payment_id if isinstance(obj, Payment) \
        else PaymentAllocation.credit_memo_id
    return db.session.execute(
        select(PaymentAllocation, Invoice).join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
        .where(column == obj.id).order_by(PaymentAllocation.id)).all()


def receipt_rows(payment):
    """What a receipt shows: each invoice it paid, and what was still owed on it right
    after (from the allocations up to this one, so it never changes later)."""
    rows = []
    for alloc, inv in applications(payment):
        before_and_this = db.session.scalar(select(func.coalesce(func.sum(
            PaymentAllocation.amount), 0)).where(PaymentAllocation.invoice_id == inv.id,
                                                PaymentAllocation.id <= alloc.id))
        rows.append({"invoice": inv, "amount": alloc.amount,
                     "balance_after": inv.total - before_and_this})
    return rows


def deposit_on(est):
    """(received, still unapplied) for deposits taken on an estimate, net of reversals."""
    received = ZERO
    for p in db.session.scalars(select(Payment).where(Payment.estimate_id == est.id)):
        if reversal_of(p) is None:
            received += p.amount
    left = sum((s.left for s in credit_sources(est.customer_id)
                if s.is_payment and s.obj.estimate_id == est.id), ZERO)
    return received, left


def statement(customer, today=None):
    """Open invoices with how late each is, totals by age, credit, and what's owed."""
    today = today or local_today()
    rows, buckets = [], {label: ZERO for _, label in AGING}
    for inv in open_invoices(customer.id):
        late = (today - inv.due_date).days
        label = next(lbl for limit, lbl in AGING if limit is None or late <= limit)
        rows.append({"invoice": inv, "late": max(late, 0), "bucket": label})
        buckets[label] += inv.balance
    owed = sum(buckets.values(), ZERO)
    credit = credit_available(customer.id)
    return {"rows": rows, "buckets": buckets, "owed": owed, "credit": credit,
            "due": owed - credit, "today": today, "shop": shop()}
