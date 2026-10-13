"""The books: expenses, mileage, the partner ledger and sales-tax filings. Callers commit;
a BooksError's message is shown to the user as is.

Money is Decimal, rounded half-up to the cent per row (a trip, a PO's supplies), then
summed. What a partner is owed is never stored: it's what they paid for the business
(expenses, purchase orders on their card, trips in their own vehicle) less what's been
paid back to them in the ledger."""

import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.billing.service import untaxed_exposure
from app.extensions import db
from app.models import (
    CreditMemo,
    Expense,
    ExpenseCategory,
    Invoice,
    MileageRate,
    Part,
    PartnerEntry,
    PoLine,
    PurchaseOrder,
    SalesTaxFiling,
    Trip,
    User,
    Vendor,
)
from app.pricing import round_money
from app.timeutil import local_today, utcnow
from app.work import files

ZERO = Decimal("0.00")
SPENT_PO_STATUSES = ("ordered", "partially_received", "received", "closed")
RECEIPT_PDF_MAX = 10 * 1024 * 1024
LIVE_INVOICE = ("issued", "partially_paid", "paid", "written_off")


class BooksError(Exception):
    """Something the user can fix; the message is shown as is."""


# --- receipts ---------------------------------------------------------------------------------

def store_receipt(stream):
    """A receipt as uploaded: a PDF kept as is, a photo re-encoded (location data and
    other metadata dropped). Returns (sha256, content type)."""
    raw = stream.read()
    if not raw:
        raise BooksError("That file is empty.")
    try:
        if raw.startswith(b"%PDF-"):
            if len(raw) > RECEIPT_PDF_MAX:
                raise BooksError("That PDF is over 10 MB.")
            return files.store_pdf(raw), "application/pdf"
        sha, *_ = files.store_photo(io.BytesIO(raw))
    except files.FileError as e:
        raise BooksError(f"{e} A receipt can be a photo or a PDF.") from None
    return sha, "image/jpeg"


def receipt_path(sha256, content_type):
    return files.path_for(sha256, ".pdf" if content_type == "application/pdf" else ".jpg")


# --- partners ---------------------------------------------------------------------------------

def partners(include=()):
    """Users marked as partners (Settings → Users), plus any ids in `include` (someone
    already on a record, even if no longer a partner)."""
    include = [i for i in include if i]
    q = select(User).where(User.is_partner.is_(True) | User.id.in_(include))
    return db.session.scalars(q.order_by(User.display_name)).all()


def _check_partner(user_id):
    if user_id is not None and user_id not in {u.id for u in partners()}:
        raise BooksError("Only a partner (Settings → Users) can be paid back.")


# --- expenses ---------------------------------------------------------------------------------

def category_by_code(code):
    return db.session.scalar(select(ExpenseCategory).where(ExpenseCategory.code == code))


def save_expense(expense, *, spent_on, payee, category_id, amount, tax_included=None,
                 paid_by_user_id=None, work_order_id=None, note=None, receipt=None):
    """Create (expense None) or edit. A voided expense stays as it was."""
    if expense is not None and expense.voided_at is not None:
        raise BooksError("That expense is voided.")
    if spent_on > local_today():
        raise BooksError("An expense can't be dated in the future.")
    tax_included = tax_included or ZERO
    if tax_included > amount:
        raise BooksError("The tax can't be more than the amount.")
    category = db.session.get(ExpenseCategory, category_id)
    if category is None:
        raise BooksError("Pick a category.")
    if expense is None or paid_by_user_id != expense.paid_by_user_id:
        _check_partner(paid_by_user_id)
    if expense is None:
        expense = Expense()
        db.session.add(expense)
    expense.spent_on, expense.payee, expense.category_id = spent_on, payee, category_id
    expense.amount, expense.tax_included = amount, tax_included
    expense.paid_by_user_id, expense.work_order_id, expense.note = \
        paid_by_user_id, work_order_id, note
    if receipt is not None:
        expense.receipt_sha256, expense.receipt_type = receipt
    db.session.flush()
    return expense


def void_expense(expense, reason):
    if expense.voided_at is not None:
        raise BooksError("That expense is already voided.")
    if not (reason or "").strip():
        raise BooksError("Say why it's voided.")
    expense.voided_at, expense.void_reason = utcnow(), reason.strip()
    db.session.flush()


@dataclass
class ExpenseRow:
    """One line of the expense list: typed in, or booked from a purchase order."""
    on: date
    payee: str
    category: str
    amount: Decimal
    tax: Decimal
    paid_by_user_id: int | None
    expense: Expense | None = None
    po: PurchaseOrder | None = None


def po_expense_rows(start=None, end=None):
    """What purchase orders spent that isn't inventory, on each PO's order date: stock
    the shop doesn't track (supplies), shipping, and sales tax paid. Stocked parts aren't
    expenses: they're the cost of the job when they're sold."""
    q = select(PurchaseOrder, Vendor).join(Vendor, Vendor.id == PurchaseOrder.vendor_id) \
        .where(PurchaseOrder.status.in_(SPENT_PO_STATUSES))
    if start is not None:
        q = q.where(PurchaseOrder.ordered_on >= start)
    if end is not None:
        q = q.where(PurchaseOrder.ordered_on <= end)
    pos = db.session.execute(q.order_by(PurchaseOrder.ordered_on, PurchaseOrder.id)).all()
    if not pos:
        return []
    supplies = dict(db.session.execute(
        select(PoLine.po_id, func.sum(func.round(PoLine.qty * PoLine.unit_cost, 2)))
        .join(Part, Part.id == PoLine.part_id)
        .where(PoLine.po_id.in_([po.id for po, _ in pos]), PoLine.status != "cancelled",
               Part.track_stock.is_(False))
        .group_by(PoLine.po_id)).all())
    names = {c.code: c.name for c in db.session.scalars(
        select(ExpenseCategory).where(ExpenseCategory.code.isnot(None)))}
    rows = []
    for po, vendor in pos:
        for code, amount in (("supplies", supplies.get(po.id) or ZERO),
                             ("shipping", po.shipping), ("purchase_tax", po.tax_paid)):
            if amount:
                rows.append(ExpenseRow(on=po.ordered_on, payee=vendor.name,
                                       category=names.get(code, code), amount=amount,
                                       tax=amount if code == "purchase_tax" else ZERO,
                                       paid_by_user_id=po.paid_by_user_id, po=po))
    return rows


def expense_rows(start=None, end=None, category_id=None):
    """Expenses in a date range (voided ones left out), with what POs spent, by date."""
    q = select(Expense, ExpenseCategory).join(
        ExpenseCategory, ExpenseCategory.id == Expense.category_id).where(
        Expense.voided_at.is_(None))
    if start is not None:
        q = q.where(Expense.spent_on >= start)
    if end is not None:
        q = q.where(Expense.spent_on <= end)
    if category_id is not None:
        q = q.where(Expense.category_id == category_id)
    rows = [ExpenseRow(on=e.spent_on, payee=e.payee, category=c.name, amount=e.amount,
                       tax=e.tax_included, paid_by_user_id=e.paid_by_user_id, expense=e)
            for e, c in db.session.execute(q)]
    from_pos = po_expense_rows(start, end)
    if category_id is not None:
        name = db.session.get(ExpenseCategory, category_id).name
        from_pos = [r for r in from_pos if r.category == name]
    return sorted(rows + from_pos, key=lambda r: (r.on, r.payee))


def by_category(rows):
    """[(category, total)], largest first."""
    totals = {}
    for r in rows:
        totals[r.category] = totals.get(r.category, ZERO) + r.amount
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))


# --- mileage ----------------------------------------------------------------------------------

def rates():
    return db.session.scalars(select(MileageRate).order_by(MileageRate.effective_from)).all()


def rate_on(day, all_rates=None):
    """The rate in effect on a day: the latest that started on or before it."""
    best = None
    for r in all_rates if all_rates is not None else rates():
        if r.effective_from <= day:
            best = r
    return best


def trip_value(trip, all_rates=None):
    """Miles × the rate on the trip's date, to the cent; None with no rate yet."""
    rate = rate_on(trip.trip_on, all_rates)
    return round_money(trip.miles * rate.rate) if rate else None


def save_trip(trip, *, trip_on, user_id, miles, purpose, from_place=None, to_place=None,
              own_vehicle=True, work_order_id=None):
    if trip is not None and trip.voided_at is not None:
        raise BooksError("That trip is voided.")
    if db.session.get(User, user_id) is None:
        raise BooksError("Pick who drove.")
    if trip_on > local_today():
        raise BooksError("A trip can't be dated in the future.")
    if trip is None:
        trip = Trip()
        db.session.add(trip)
    trip.trip_on, trip.user_id, trip.miles, trip.purpose = trip_on, user_id, miles, purpose
    trip.from_place, trip.to_place, trip.own_vehicle, trip.work_order_id = \
        from_place, to_place, own_vehicle, work_order_id
    db.session.flush()
    return trip


def void_trip(trip, reason):
    if trip.voided_at is not None:
        raise BooksError("That trip is already voided.")
    if not (reason or "").strip():
        raise BooksError("Say why it's voided.")
    trip.voided_at, trip.void_reason = utcnow(), reason.strip()
    db.session.flush()


def trips(start=None, end=None, user_id=None):
    """[(trip, value)] in a date range, voided ones left out."""
    q = select(Trip).where(Trip.voided_at.is_(None))
    if start is not None:
        q = q.where(Trip.trip_on >= start)
    if end is not None:
        q = q.where(Trip.trip_on <= end)
    if user_id is not None:
        q = q.where(Trip.user_id == user_id)
    all_rates = rates()
    return [(t, trip_value(t, all_rates))
            for t in db.session.scalars(q.order_by(Trip.trip_on.desc(), Trip.id.desc()))]


# --- the partner ledger -----------------------------------------------------------------------

def po_total(po):
    from app.purchasing import service as purchasing
    return purchasing.totals(po)["total"]


def owed(user_id):
    """What the business owes a partner for costs they paid: expenses with their money,
    POs on their card, trips in their own vehicle, less reimbursements recorded. Each
    part comes with its rows."""
    expenses = db.session.scalars(select(Expense).where(
        Expense.paid_by_user_id == user_id, Expense.voided_at.is_(None))
        .order_by(Expense.spent_on)).all()
    pos = db.session.scalars(select(PurchaseOrder).where(
        PurchaseOrder.paid_by_user_id == user_id,
        PurchaseOrder.status.in_(SPENT_PO_STATUSES)).order_by(PurchaseOrder.ordered_on)).all()
    drives = [(t, v) for t, v in trips(user_id=user_id) if t.own_vehicle]
    paid = db.session.scalars(select(PartnerEntry).where(
        PartnerEntry.user_id == user_id, PartnerEntry.kind == "reimbursement",
        PartnerEntry.voided_at.is_(None)).order_by(PartnerEntry.entry_on)).all()
    totals = {
        "expenses": sum((e.amount for e in expenses), ZERO),
        "purchases": sum((po_total(po) for po in pos), ZERO),
        "mileage": sum((v for _, v in drives if v is not None), ZERO),
        "reimbursed": sum((p.amount for p in paid), ZERO),
    }
    totals["owed"] = (totals["expenses"] + totals["purchases"] + totals["mileage"]
                      - totals["reimbursed"])
    return {**totals, "expense_rows": expenses, "po_rows": [(po, po_total(po)) for po in pos],
            "trip_rows": drives, "unpriced_trips": sum(1 for _, v in drives if v is None)}


def entries(user_id=None, start=None, end=None, include_voided=False):
    q = select(PartnerEntry)
    if user_id is not None:
        q = q.where(PartnerEntry.user_id == user_id)
    if start is not None:
        q = q.where(PartnerEntry.entry_on >= start)
    if end is not None:
        q = q.where(PartnerEntry.entry_on <= end)
    if not include_voided:
        q = q.where(PartnerEntry.voided_at.is_(None))
    return db.session.scalars(q.order_by(PartnerEntry.entry_on.desc(),
                                         PartnerEntry.id.desc())).all()


def add_entry(*, user_id, kind, amount, entry_on, note=None):
    _check_partner(user_id)
    if kind == "reimbursement" and amount > owed(user_id)["owed"]:
        raise BooksError("That's more than they're owed; record the rest as a draw.")
    entry = PartnerEntry(user_id=user_id, kind=kind, amount=amount, entry_on=entry_on,
                         note=note)
    db.session.add(entry)
    db.session.flush()
    return entry


def void_entry(entry, reason):
    if entry.voided_at is not None:
        raise BooksError("That entry is already voided.")
    if not (reason or "").strip():
        raise BooksError("Say why it's voided.")
    entry.voided_at, entry.void_reason = utcnow(), reason.strip()
    db.session.flush()


def ledger_totals(user_id, start=None, end=None):
    """{kind: total} for a partner over a period."""
    out = {}
    for e in entries(user_id, start, end):
        out[e.kind] = out.get(e.kind, ZERO) + e.amount
    return out


# --- sales tax --------------------------------------------------------------------------------

def tax_summary(start, end):
    """Taxable sales and tax by jurisdiction for invoices issued in the period, less
    credit memos issued in it. While tax isn't collected, `untaxed` is what it would have
    been at each invoice's recorded rate."""
    rows = {}

    def row(name):
        return rows.setdefault(name, {"jurisdiction": name, "invoices": 0, "taxable": ZERO,
                                      "nontaxable": ZERO, "tax": ZERO, "untaxed": ZERO})

    for inv in db.session.scalars(select(Invoice).where(
            Invoice.status.in_(LIVE_INVOICE), Invoice.issue_date >= start,
            Invoice.issue_date <= end)):
        r = row((inv.tax_jurisdiction or {}).get("name") or "No jurisdiction recorded")
        r["invoices"] += 1
        r["taxable"] += inv.subtotal_taxable
        r["nontaxable"] += inv.subtotal_nontaxable
        r["tax"] += inv.tax
        r["untaxed"] += untaxed_exposure(inv) or ZERO
    for cm, inv in db.session.execute(
            select(CreditMemo, Invoice).join(Invoice, Invoice.id == CreditMemo.invoice_id)
            .where(CreditMemo.status == "issued", CreditMemo.issue_date >= start,
                   CreditMemo.issue_date <= end)):
        r = row((inv.tax_jurisdiction or {}).get("name") or "No jurisdiction recorded")
        r["taxable"] -= cm.subtotal_taxable
        r["nontaxable"] -= cm.subtotal_nontaxable
        r["tax"] -= cm.tax
    out = sorted(rows.values(), key=lambda r: r["jurisdiction"])
    totals = {k: sum((r[k] for r in out), ZERO) for k in ("taxable", "nontaxable", "tax",
                                                          "untaxed")}
    return out, totals


def record_filing(*, period_start, period_end, tax_due, filed_on=None, paid_on=None,
                  confirmation=None, note=None):
    """A return as filed. What the invoices said for the period is copied in now."""
    if period_end < period_start:
        raise BooksError("The period ends before it starts.")
    _, totals = tax_summary(period_start, period_end)
    filing = SalesTaxFiling(period_start=period_start, period_end=period_end,
                            taxable_sales=max(totals["taxable"], ZERO),
                            tax_collected=max(totals["tax"], ZERO), tax_due=tax_due,
                            filed_on=filed_on, paid_on=paid_on, confirmation=confirmation,
                            note=note)
    db.session.add(filing)
    db.session.flush()
    return filing


def filings():
    return db.session.scalars(select(SalesTaxFiling)
                              .order_by(SalesTaxFiling.period_start.desc())).all()


def unfiled_tax(today=None):
    """Tax collected since the last filed period: a liability, not revenue."""
    last = db.session.scalar(select(func.max(SalesTaxFiling.period_end)))
    start = date.fromordinal(last.toordinal() + 1) if last else date(2000, 1, 1)
    end = today or local_today()
    if end < start:
        return ZERO
    _, totals = tax_summary(start, end)
    return totals["tax"]
