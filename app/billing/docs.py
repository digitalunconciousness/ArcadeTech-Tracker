"""The invoice as a document. Rendered once, at issue, into the PDF the customer gets
(kept in the file store by sha256); the customer's /d/ page renders the same template
from the frozen invoice rows."""

from app.billing import service
from app.billing.service import ZERO
from app.models import PAYMENT_KINDS, PAYMENT_METHODS, PAYMENT_TERMS, TAX_STATUSES
from app.work.docs import render_pdf


def context(inv, lines, customer, applied=None):
    """`applied` is credit about to go on the invoice as it's issued (the PDF is drawn
    first); otherwise paid, credited and balance are the invoice's own."""
    if applied is None:
        paid, credited, balance = inv.amount_paid, inv.amount_credited, inv.balance
    else:
        paid, credited, balance = applied, ZERO, inv.total - applied
    return dict(inv=inv, lines=lines, groups=service.grouped(lines), customer=customer,
                terms_label=PAYMENT_TERMS[inv.payment_terms], tax_statuses=TAX_STATUSES,
                rate=inv.tax_jurisdiction["combined_rate"] if inv.tax_jurisdiction else None,
                paid=paid, credited=credited, balance_due=balance)


def invoice_pdf(inv, lines, customer, applied=None):
    return render_pdf("docs/invoice.html", **context(inv, lines, customer, applied))


def credit_memo_pdf(cm, lines, inv, customer):
    return render_pdf("docs/credit_memo.html", cm=cm, lines=lines, inv=inv, customer=customer)


def receipt_pdf(payment, customer):
    from app.billing import payments
    return render_pdf("docs/receipt.html", payment=payment, customer=customer,
                      rows=payments.receipt_rows(payment), methods=PAYMENT_METHODS,
                      left=payments.source_left(payment) if payment.kind == "receipt" else None,
                      kinds=PAYMENT_KINDS)


def statement_pdf(customer):
    from app.billing import payments
    return render_pdf("docs/statement.html", customer=customer, **payments.statement(customer))


def filename(inv):
    return f"{inv.number}.pdf"
