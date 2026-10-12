"""The invoice as a document. Rendered once, at issue, into the PDF the customer gets
(kept in the file store by sha256); the customer's /d/ page renders the same template
from the frozen invoice rows."""

from app.billing import service
from app.models import PAYMENT_TERMS, TAX_STATUSES
from app.work.docs import render_pdf


def context(inv, lines, customer):
    return dict(inv=inv, lines=lines, groups=service.grouped(lines), customer=customer,
                terms_label=PAYMENT_TERMS[inv.payment_terms], tax_statuses=TAX_STATUSES,
                rate=inv.tax_jurisdiction["combined_rate"] if inv.tax_jurisdiction else None)


def invoice_pdf(inv, lines, customer):
    return render_pdf("docs/invoice.html", **context(inv, lines, customer))


def filename(inv):
    return f"{inv.number}.pdf"
