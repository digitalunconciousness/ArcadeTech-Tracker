"""Invoices: issue from a work order, void, customer links. Callers commit; an
InvoiceError's message is shown as is.

Issuing is one transaction: a draft invoice and its lines are written, the work-order
lines are marked billed, the next gapless number is taken, the PDF the customer gets is
rendered and stored, and the invoice flips to issued. Postgres checks the arithmetic at
that moment and freezes the invoice and its lines from then on. Any failure rolls all of
it back, the number included.

Tax: off until the business has a permit (owner, 2026-10-12). With collect_sales_tax off
an invoice issues as "Sales tax not collected", with every taxable line still flagged.
With it on, tax is the taxable subtotal at the jurisdiction's combined rate, rounded
half-up once per invoice, and the rate is frozen on the invoice."""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app.extensions import db
from app.links import live_link, new_token, token_hash
from app.models import (
    PAYMENT_TERMS,
    Asset,
    Customer,
    DocLink,
    Invoice,
    InvoiceLine,
    ShopSetting,
    Site,
    TaxJurisdiction,
    WoJob,
    WoLine,
    WorkOrder,
)
from app.numbering import issue_number
from app.pricing import margin_pct, round_money
from app.timeutil import local_today, utcnow
from app.work import files
from app.work import service as work

TERMS_DAYS = {"due_on_receipt": 0, "net_15": 15, "net_30": 30}
assert set(TERMS_DAYS) == set(PAYMENT_TERMS)
# Once paid, an invoice's links run out 90 days on (payments.PAID_LINK_DAYS).
INVOICE_LINK_DAYS = 365
ZERO = Decimal("0.00")


class InvoiceError(Exception):
    """Something the user can fix; the message is shown as is."""


def shop():
    return db.session.get(ShopSetting, True)


# --- what can be billed ----------------------------------------------------------------------

def job_label(job, asset, wo):
    if asset is not None:
        return f"{asset.tag} · {asset.name}"
    if wo.kind == "counter":
        return "Counter sale"
    first = (job.complaint or "").strip().splitlines()
    return (first[0][:120] if first else "") or "Consultation"


def unbilled(wo):
    """[(job, asset, [lines])]: what can still go on an invoice. A line qualifies when
    it's billable, not on a live invoice, on a job that isn't cancelled, and has a
    quantity (actual-time labor with no time yet waits)."""
    jobs = db.session.execute(
        select(WoJob, Asset).outerjoin(Asset, Asset.id == WoJob.asset_id)
        .where(WoJob.work_order_id == wo.id, WoJob.status != "cancelled")
        .order_by(WoJob.id)).all()
    lines = work.job_lines([j.id for j, _ in jobs])
    out = []
    for job, asset in jobs:
        ready = [x for x in lines[job.id] if x.billable and x.invoice_id is None and x.qty > 0]
        if ready:
            out.append((job, asset, ready))
    return out


def default_jurisdiction_id(wo):
    """Destination sourcing: the site for on-site work, else the shop's own location.
    The owner can change it on the issue form."""
    if wo.site_id:
        site = db.session.get(Site, wo.site_id)
        if site is not None and site.tax_jurisdiction_id:
            return site.tax_jurisdiction_id
    return shop().default_tax_jurisdiction_id


def in_effect(jurisdiction, on):
    return jurisdiction.effective_from <= on and (jurisdiction.effective_to is None
                                                  or jurisdiction.effective_to >= on)


def jurisdiction_snapshot(j):
    """Frozen on the invoice. Rates as strings: JSON numbers would be floats."""
    return {"id": j.id, "name": j.name, "state_rate": str(j.state_rate),
            "county_rate": str(j.county_rate), "city_rate": str(j.city_rate),
            "other_rate": str(j.other_rate), "combined_rate": str(j.combined_rate)}


def party(name, address_lines, **extra):
    return {"name": name, "lines": list(address_lines),
            **{k: v for k, v in extra.items() if v}}


def row_amount(row):
    """The same arithmetic as invoice_line.line_total in Postgres."""
    amount = round_money(row["qty"] * row["unit_price"])
    return -amount if row["kind"] == "discount" else amount


@dataclass
class Draft:
    """An invoice worked out but not written: what the issue page previews and what
    issue() stores."""
    groups: list = field(default_factory=list)       # [(label, [row dicts])]
    wo_lines: list = field(default_factory=list)
    subtotal_taxable: Decimal = ZERO
    subtotal_nontaxable: Decimal = ZERO
    tax: Decimal = ZERO
    tax_status: str = "not_collected"
    jurisdiction: TaxJurisdiction | None = None
    exempt_cert_no: str | None = None

    @property
    def total(self):
        return self.subtotal_taxable + self.subtotal_nontaxable + self.tax

    @property
    def rows(self):
        return [row for _, rows in self.groups for row in rows]


def work_out(wo, customer, line_ids, *, issue_date, jurisdiction_id):
    """The invoice these lines would make on issue_date. Raises InvoiceError when it
    can't be issued as asked."""
    line_ids = set(line_ids)
    groups = unbilled(wo)
    if not line_ids:
        raise InvoiceError("Tick at least one line to bill.")
    if line_ids - {x.id for _, _, ls in groups for x in ls}:
        raise InvoiceError("Some of those lines were billed or changed meanwhile. "
                           "Reload and try again.")
    all_lines = work.job_lines([job.id for job, _, _ in groups])
    draft = Draft()
    position = 0
    for job, asset, ready in groups:
        chosen = [x for x in ready if x.id in line_ids]
        if not chosen:
            continue
        has_customer_part = any(x.customer_supplied for x in all_lines[job.id])
        rows = []
        for x in chosen:
            position += 1
            days = work.warranty_days(x, has_customer_part)
            rows.append(dict(
                position=position, group_label=job_label(job, asset, wo), kind=x.kind,
                labor_mode=x.labor_mode, description=x.description, scope=x.scope, qty=x.qty,
                unit_price=x.unit_price, taxable=x.taxable, customer_supplied=x.customer_supplied,
                unit_cost=x.unit_cost, part_id=x.part_id, tech_user_id=x.tech_user_id,
                warranty_days=days,
                warranty_until=issue_date + timedelta(days=days) if days else None,
                wo_line_id=x.id))
            draft.wo_lines.append(x)
        draft.groups.append((job_label(job, asset, wo), rows))

    draft.subtotal_taxable = sum((row_amount(r) for r in draft.rows if r["taxable"]), ZERO)
    draft.subtotal_nontaxable = sum((row_amount(r) for r in draft.rows if not r["taxable"]), ZERO)
    if draft.subtotal_taxable < 0 or draft.subtotal_nontaxable < 0:
        raise InvoiceError("A discount is bigger than the lines it discounts. A discount on "
                           "parts should be taxable, one on labor not.")
    if draft.subtotal_taxable + draft.subtotal_nontaxable == 0:
        raise InvoiceError("Those lines come to nothing: there's nothing to bill.")

    j = db.session.get(TaxJurisdiction, jurisdiction_id) if jurisdiction_id else None
    if j is not None and not in_effect(j, issue_date):
        raise InvoiceError(f"The {j.name} rate isn't in effect on {issue_date}: pick the one "
                           "that is.")
    draft.jurisdiction = j
    if not shop().collect_sales_tax:
        draft.tax_status = "not_collected"
    elif customer.tax_exempt:
        if not customer.exempt_cert_no:
            raise InvoiceError("This customer is marked tax exempt but has no certificate "
                               "number: add it on the customer first.")
        if customer.exempt_cert_expires and customer.exempt_cert_expires < issue_date:
            raise InvoiceError(f"This customer's exemption certificate expired on "
                               f"{customer.exempt_cert_expires}: update it, or untick tax "
                               "exempt.")
        draft.tax_status, draft.exempt_cert_no = "exempt", customer.exempt_cert_no
    else:
        draft.tax_status = "collected"
        if draft.subtotal_taxable > 0:
            if j is None:
                raise InvoiceError("Some lines are taxable: pick the tax jurisdiction.")
            draft.tax = round_money(draft.subtotal_taxable * j.combined_rate)
    return draft


# --- issuing ---------------------------------------------------------------------------------

def _lock(model, row_id):
    return db.session.scalars(select(model).where(model.id == row_id).with_for_update()
                              .execution_options(populate_existing=True)).one()


def issue(wo, line_ids, *, issue_date, jurisdiction_id=None, customer_note=None,
          internal_note=None, backdate_reason=None, apply_credit=False):
    """Issue an invoice for these work-order lines on issue_date. With apply_credit, the
    customer's credit on account (a deposit first) goes on it at once, and the PDF shows
    it paid. Returns the invoice."""
    from app.billing import docs, payments
    from app.work import docs as work_docs

    wo = _lock(WorkOrder, wo.id)   # one issue at a time per work order
    customer = db.session.get(Customer, wo.customer_id)
    today = local_today()
    if issue_date > today:
        raise InvoiceError("The issue date can't be in the future.")
    if issue_date < today and not backdate_reason:
        raise InvoiceError("It's dated before today: say why (for example, the date it was "
                           "billed on paper).")
    db.session.execute(select(WoLine.id).where(WoLine.id.in_(list(line_ids)))
                       .with_for_update())
    draft = work_out(wo, customer, line_ids, issue_date=issue_date,
                     jurisdiction_id=jurisdiction_id)
    site = db.session.get(Site, wo.site_id) if wo.site_id else None
    j = draft.jurisdiction
    inv = Invoice(
        customer_id=customer.id, work_order_id=wo.id, status="draft",
        issue_date=issue_date, due_date=issue_date + timedelta(days=TERMS_DAYS[
            customer.payment_terms]),
        backdate_reason=backdate_reason if issue_date < today else None,
        payment_terms=customer.payment_terms,
        bill_to=party(customer.display_name, customer.address_lines,
                      email=customer.billing_email, phone=customer.phone),
        ship_to=party(site.name, site.address_lines) if site else None,
        tax_status=draft.tax_status, tax_jurisdiction_id=j.id if j else None,
        tax_jurisdiction=jurisdiction_snapshot(j) if j else None,
        exempt_cert_no=draft.exempt_cert_no, subtotal_taxable=draft.subtotal_taxable,
        subtotal_nontaxable=draft.subtotal_nontaxable, tax=draft.tax, total=draft.total,
        amount_paid=ZERO, balance=ZERO, terms_snapshot=shop().warranty_terms,
        customer_note=customer_note, internal_note=internal_note)
    db.session.add(inv)
    db.session.flush()
    lines = [InvoiceLine(invoice_id=inv.id, **row) for row in draft.rows]
    db.session.add_all(lines)
    db.session.flush()
    for x in draft.wo_lines:
        x.invoice_id = inv.id
    db.session.flush()

    credit = ZERO
    if apply_credit:   # locked first, so what the PDF says is what gets applied
        payments.lock_customer(customer.id)
        credit = min(draft.total, payments.credit_available(customer.id))
    number = issue_number("INV", on=issue_date)
    with db.session.no_autoflush:   # the number and the issued status land together
        inv.number, inv.status, inv.issued_at = number, "issued", utcnow()
        inv.balance = inv.total - inv.amount_paid
        inv.pdf_sha256 = files.store_pdf(docs.invoice_pdf(inv, lines, customer,
                                                          applied=credit))
        if wo.kind != "counter":
            inv.report_sha256 = files.store_pdf(work_docs.service_report(wo, customer))
    db.session.flush()   # invoice_frozen() checks the subtotals and tax here
    if credit:
        payments.apply_credit(inv, limit=credit)
    return inv


def void(inv, *, reason, user_id):
    """Void an issued invoice. Its work-order lines are freed to be billed again; the
    number stays used, and the invoice stays on file marked void."""
    inv = _lock(Invoice, inv.id)
    if inv.status == "void":
        raise InvoiceError(f"{inv.number} is already void.")
    if inv.status != "issued" or inv.amount_paid or inv.amount_credited:
        raise InvoiceError(f"{inv.number} has payments or credits applied: it can't be voided. "
                           "Issue a credit memo, or reverse the payment first.")
    inv.status, inv.voided_at, inv.voided_by, inv.void_reason = "void", utcnow(), user_id, reason
    inv.balance = ZERO
    db.session.flush()
    for x in db.session.scalars(select(WoLine).where(WoLine.invoice_id == inv.id)):
        x.invoice_id = None
    db.session.flush()
    return inv


# --- reading ---------------------------------------------------------------------------------

def lines_of(inv):
    return db.session.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)
                              .order_by(InvoiceLine.position)).all()


def grouped(lines):
    """[(label, [lines])] in printed order."""
    groups = []
    for line in lines:
        if not groups or groups[-1][0] != line.group_label:
            groups.append((line.group_label, []))
        groups[-1][1].append(line)
    return groups


def is_overdue(inv, today=None):
    return inv.status in ("issued", "partially_paid") and inv.balance > 0 and \
        inv.due_date < (today or local_today())


def untaxed_exposure(inv):
    """Tax not collected on an invoice's taxable goods, at the jurisdiction recorded on it
    (None without one). For the CPA, while the shop has no permit."""
    if inv.tax_status != "not_collected" or not inv.tax_jurisdiction:
        return None
    return round_money(inv.subtotal_taxable * Decimal(inv.tax_jurisdiction["combined_rate"]))


def costs(lines):
    """What the billed lines cost us, and the margin on parts and sublets (owners and
    viewers only)."""
    costed = [x for x in lines if x.unit_cost is not None and not x.customer_supplied]
    cost = round_money(sum((x.qty * x.unit_cost for x in costed), Decimal(0)))
    goods = sum((x.line_total for x in lines if x.kind in ("part", "sublet")), ZERO)
    return {"cost": cost, "margin": margin_pct(goods, cost) if costed else None}


# --- links -----------------------------------------------------------------------------------

def new_link(inv):
    """A customer link to the issued invoice. Returns the token: shown once."""
    if inv.status in ("draft", "void"):
        raise InvoiceError(f"{inv.number or 'A draft'} is {inv.status}: there's nothing to send.")
    token = new_token()
    db.session.add(DocLink(invoice_id=inv.id, token_sha256=token_hash(token),
                           expires_at=utcnow() + timedelta(days=INVOICE_LINK_DAYS)))
    db.session.flush()
    return token


def find_link(token):
    """(link, invoice) for a live invoice link, else None."""
    link = live_link(token)
    if link is None or link.invoice_id is None:
        return None
    return link, db.session.get(Invoice, link.invoice_id)
