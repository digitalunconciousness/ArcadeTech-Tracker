"""The year-end package: one zip with everything a tax preparer asks for, built on demand
into a temporary file and streamed. Same tables as the report pages, same numbers as the
dashboard (figures.py)."""

import re
import tempfile
import zipfile
from datetime import date

from flask import current_app

from app.books import journal
from app.reports import tables as t
from app.reports.figures import BASES
from app.timeutil import local_today, localdt, utcnow
from app.work import files

CHUNK = 64 * 1024

README = """Year-end package: {business}, {year}{so_far}
Built {built} ({tz}).

Money is in dollars with two decimals, dates are YYYY-MM-DD. Every file comes from the
same numbers the app shows; the P&L files add up from the raw rows in this zip.

Profit and loss
  pnl-cash.csv, pnl-accrual.csv            the year's P&L on each basis
  pnl-monthly-cash.csv, -accrual.csv       the same by month (months add up to the year)
  pnl-cash.pdf, pnl-accrual.pdf            printable, with the monthly chart

  Cash basis revenue: money received in the year (receipts less reversals and refunds),
  less the sales tax inside what was applied to taxed invoices.
  Accrual basis revenue: invoices issued in the year without their tax, less credit
  memos issued in it. Voided invoices never count.
  Cost of goods (both bases): parts and sublets on invoices issued in the year, at the
  cost frozen on each line; a customer's own parts cost nothing.
  Expenses include what purchase orders spent that isn't stocked inventory (supplies,
  shipping, tax paid), on each order's date. Mileage is at the IRS standard rate for
  each trip's date.

Raw rows
  invoices.csv, invoice-lines.csv          everything issued in the year, voided included
  payments.csv                             receipts, reversals and refunds by date
  allocations.csv                          what was applied to which invoice
  credit-memos.csv
  expenses.csv                             typed-in expenses and purchase-order rows
  mileage.csv
  partner-ledger.csv, partners-cash.csv    contributions, draws, reimbursements; shares
  sales-tax.csv, sales-tax-filings.csv
  1099.csv, 1099-by-method.csv             who should send a 1099-NEC; totals to match
                                           against 1099-Ks
  inventory-{inv_date}.csv                 stock on hand at cost (first in, first out)

For the books
  general-journal.csv                      the year as double entries, accrual basis,
                                           under the names in Settings → Accounts
  trial-balance.csv                        the year's activity by account; its income and
                                           cost accounts net to the accrual P&L

Documents
  invoices/        each issued invoice PDF, exactly as the customer got it (and the
                   service report issued with it)
  credit-memos/    each credit memo PDF
  receipts/        receipts behind the year's expenses and purchase orders
{missing}"""


def _slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-")[:40] or "x"


def _add_file(zf, name, sha, suffix, missing):
    if not sha:
        return
    path = files.path_for(sha, suffix)
    if path.exists():
        zf.write(path, name, compress_type=zipfile.ZIP_STORED)
    else:
        current_app.logger.error("year-end: %s missing from the file store (%s)", name, sha)
        missing.append(name)


def build(y, now=None):
    """Write the package for calendar year y; returns the open temporary file, at 0."""
    from app.reports.routes import pnl_pdf_bytes
    from app.settings_store import get_settings

    now = now or utcnow()
    start, end = date(y, 1, 1), date(y, 12, 31)
    inv_date = min(end, local_today())
    out = tempfile.TemporaryFile()
    missing = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        def csv(name, table):
            zf.writestr(name, t.to_csv(table))

        for basis in BASES:
            csv(f"pnl-{basis}.csv", t.pnl_table(start, end, basis)[0])
            csv(f"pnl-monthly-{basis}.csv", t.pnl_monthly_table(y, basis))
            zf.writestr(f"pnl-{basis}.pdf", pnl_pdf_bytes(start, end, basis),
                        compress_type=zipfile.ZIP_STORED)
        invoices = t.invoices_table(start, end)
        csv("invoices.csv", invoices)
        csv("invoice-lines.csv", t.invoice_lines_table(start, end))
        csv("payments.csv", t.payments_table(start, end))
        csv("allocations.csv", t.allocations_table(start, end))
        memos = t.credit_memos_table(start, end)
        csv("credit-memos.csv", memos)
        expenses = t.expenses_table(start, end)
        csv("expenses.csv", expenses)
        csv("mileage.csv", t.mileage_table(start, end))
        csv("partner-ledger.csv", t.ledger_table(start, end))
        csv("partners-cash.csv", t.partners_table(start, end, "cash")[0])
        csv("sales-tax.csv", t.sales_tax_table(start, end))
        csv("sales-tax-filings.csv", t.filings_table())
        people, by_method = t.nec_tables(y)
        csv("1099.csv", people)
        csv("1099-by-method.csv", by_method)
        csv(f"inventory-{inv_date}.csv", t.inventory_table(inv_date))
        es = journal.entries(start, end)
        csv("general-journal.csv", t.journal_table(es))
        csv("trial-balance.csv", t.trial_balance_table(es, start, end))

        for inv in t.issued_documents(start, end):
            _add_file(zf, f"invoices/{_slug(inv.number)}.pdf", inv.pdf_sha256, ".pdf", missing)
            _add_file(zf, f"invoices/{_slug(inv.number)}-service-report.pdf",
                      inv.report_sha256, ".pdf", missing)
        for cm in t.issued_memos(start, end):
            _add_file(zf, f"credit-memos/{_slug(cm.number)}.pdf", cm.pdf_sha256, ".pdf",
                      missing)
        seen = set()
        for on, payee, sha, kind in t.receipts(start, end):
            if sha in seen:
                continue
            seen.add(sha)
            ext = ".pdf" if kind == "application/pdf" else ".jpg"
            _add_file(zf, f"receipts/{on}-{_slug(payee)}-{sha[:8]}{ext}", sha, ext, missing)

        settings = get_settings()
        zf.writestr("README.txt", README.format(
            business=settings.business_name, year=y,
            so_far=" (so far)" if y == local_today().year else "",
            built=localdt(now), tz=current_app.config["SHOP_TZ"], inv_date=inv_date,
            missing=("\nMISSING from the file store (see the app log):\n"
                     + "".join(f"  {m}\n" for m in missing)) if missing else ""))
    out.seek(0)
    return out


def chunks(stream):
    try:
        while block := stream.read(CHUNK):
            yield block
    finally:
        stream.close()
