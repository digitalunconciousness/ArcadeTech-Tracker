"""Reports beyond the P&L: aging, inventory value and lot trace, parts usage, labor by
tech, job profitability, turnaround, warranty and comebacks, the 1099 reconciliation,
partner statements. Each returns plain rows; the routes render them or write CSV."""

import csv
import io
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from sqlalchemy import func, select

from app.books import service as books
from app.extensions import db
from app.models import (
    PAYMENT_METHODS,
    Customer,
    Invoice,
    InvoiceLine,
    Part,
    Payment,
    StockLot,
    StockMove,
    TimeEntry,
    User,
    WoJob,
    WorkOrder,
)
from app.pricing import round_money
from app.reports import figures
from app.reports.figures import LIVE_INVOICE, ZERO, local_date
from app.timeutil import local_today

# Payments the payer reports on a 1099-NEC, and those a card company or payment app
# reports on a 1099-K instead (IRS 1099-NEC instructions; CPA to confirm).
NEC_METHODS = ("cash", "check", "zelle", "bank_transfer", "other")
K_METHODS = ("venmo", "cashapp", "paypal")


# --- CSV --------------------------------------------------------------------------------------

def _cell(v):
    if v is None:
        return ""
    text = str(v)
    # Text a spreadsheet would run as a formula (a payee typed as "=HYPERLINK(...)") gets
    # a leading apostrophe. Numbers aren't str, so a negative amount stays a number.
    if isinstance(v, str) and text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def csv_text(header, rows):
    """CSV with plain numbers (no $ or thousands commas) and ISO dates."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header)
    for row in rows:
        w.writerow([_cell(v) for v in row])
    return out.getvalue()


# --- inventory --------------------------------------------------------------------------------

def inventory_value(as_of=None):
    """Each lot with stock left at the end of a day, at its cost. [(lot, part, qty, value)]."""
    as_of = as_of or local_today()
    left = func.sum(StockMove.qty)
    rows = db.session.execute(
        select(StockLot, Part, left).join(Part, Part.id == StockLot.part_id)
        .join(StockMove, StockMove.lot_id == StockLot.id)
        .where(local_date(StockMove.at) <= as_of)
        .group_by(StockLot.id, Part.id).having(left > 0)
        .order_by(func.lower(Part.sku), StockLot.received_at, StockLot.id)).all()
    return [(lot, part, qty, round_money(qty * lot.unit_cost)) for lot, part, qty in rows]


def lot_trace(lot):
    """Where a lot went: every move, with the job and work order it went to."""
    return db.session.execute(
        select(StockMove, WorkOrder).outerjoin(WoJob, WoJob.id == StockMove.wo_job_id)
        .outerjoin(WorkOrder, WorkOrder.id == WoJob.work_order_id)
        .where(StockMove.lot_id == lot.id).order_by(StockMove.at, StockMove.id)).all()


def parts_usage(start, end):
    """Parts used on jobs in the period (issued less returned), with their FIFO cost.
    [(part, qty, cost)]."""
    rows = db.session.execute(
        select(Part, func.sum(-StockMove.qty),
               func.sum(func.round(-StockMove.qty * StockLot.unit_cost, 2)))
        .join(StockMove, StockMove.part_id == Part.id)
        .join(StockLot, StockLot.id == StockMove.lot_id)
        .where(StockMove.reason.in_(("issue", "return")),
               local_date(StockMove.at) >= start, local_date(StockMove.at) <= end)
        .group_by(Part.id).order_by(func.lower(Part.sku))).all()
    return [(p, qty, cost + ZERO) for p, qty, cost in rows if qty]


# --- labor and jobs ---------------------------------------------------------------------------

def labor_by_tech(start, end):
    """Hours logged per person: [(user, billable hours, other hours)]."""
    rows = db.session.execute(
        select(User, TimeEntry.billable, func.sum(TimeEntry.minutes))
        .join(TimeEntry, TimeEntry.user_id == User.id)
        .where(TimeEntry.ended_at.isnot(None), local_date(TimeEntry.started_at) >= start,
               local_date(TimeEntry.started_at) <= end)
        .group_by(User.id, TimeEntry.billable)).all()
    hours = defaultdict(lambda: [Decimal(0), Decimal(0)])
    users = {}
    for user, billable, minutes in rows:
        users[user.id] = user
        hours[user.id][0 if billable else 1] += Decimal(minutes or 0) / 60
    return sorted(((users[i], h[0].quantize(Decimal("0.01")), h[1].quantize(Decimal("0.01")))
                   for i, h in hours.items()), key=lambda r: r[0].display_name)


def job_profitability(start, end):
    """Work orders invoiced in the period: billed (without tax), parts cost, hours, labor
    at the internal rate (blank without one), profit."""
    rate = figures.settings().labor_cost_rate
    invoices = db.session.execute(
        select(Invoice, WorkOrder, Customer).join(WorkOrder, WorkOrder.id == Invoice.work_order_id)
        .join(Customer, Customer.id == Invoice.customer_id)
        .where(Invoice.status.in_(LIVE_INVOICE), Invoice.issue_date >= start,
               Invoice.issue_date <= end).order_by(Invoice.issue_date, Invoice.id)).all()
    by_wo = {}
    for inv, wo, customer in invoices:
        row = by_wo.setdefault(wo.id, {"wo": wo, "customer": customer, "billed": ZERO,
                                       "parts": ZERO, "invoices": []})
        row["billed"] += inv.subtotal_taxable + inv.subtotal_nontaxable
        row["invoices"].append(inv.number)
        row["parts"] += db.session.scalar(select(func.coalesce(func.sum(
            func.round(InvoiceLine.qty * InvoiceLine.unit_cost, 2)), 0)).where(
            InvoiceLine.invoice_id == inv.id, InvoiceLine.kind.in_(("part", "sublet")),
            InvoiceLine.unit_cost.isnot(None), InvoiceLine.customer_supplied.is_(False)))
    for wo_id, row in by_wo.items():
        minutes = db.session.scalar(select(func.coalesce(func.sum(TimeEntry.minutes), 0))
                                    .join(WoJob, WoJob.id == TimeEntry.wo_job_id)
                                    .where(WoJob.work_order_id == wo_id,
                                           TimeEntry.ended_at.isnot(None)))
        row["hours"] = (Decimal(minutes) / 60).quantize(Decimal("0.01"))
        row["labor"] = round_money(row["hours"] * rate) if rate is not None else None
        row["profit"] = row["billed"] - row["parts"] - (row["labor"] or ZERO)
    return list(by_wo.values()), rate


def turnaround(start, end):
    """Work orders that reached Ready (or were completed, for those from before ready
    dates were kept) in the period: days from intake (the first job received, else when
    the WO was opened). Returns (rows, median days)."""
    rows = []
    for wo, customer in db.session.execute(
            select(WorkOrder, Customer).join(Customer, Customer.id == WorkOrder.customer_id)
            .where(func.coalesce(WorkOrder.ready_at, WorkOrder.completed_at).isnot(None))):
        done = wo.ready_at or wo.completed_at
        done_day = done.astimezone(figures.shop_tz()).date()
        if not (start <= done_day <= end):
            continue
        received = db.session.scalar(select(func.min(WoJob.received_at)).where(
            WoJob.work_order_id == wo.id))
        began = received or wo.created_at
        days = (done - began).total_seconds() / 86400  # float-ok: elapsed days, not money
        rows.append({"wo": wo, "customer": customer, "from": began, "to": done,
                     "days": Decimal(str(round(days, 1))), "ready": wo.ready_at is not None})
    rows.sort(key=lambda r: r["to"])
    return rows, (Decimal(str(median([r["days"] for r in rows]))) if rows else None)


def warranty(days=90, today=None):
    """Warranty work orders opened in the last `days`, and the comeback rate: those ÷ jobs
    invoiced in the same window."""
    today = today or local_today()
    start = today - timedelta(days=days)
    claims = db.session.execute(
        select(WorkOrder, Customer).join(Customer, Customer.id == WorkOrder.customer_id)
        .where(WorkOrder.kind == "warranty", local_date(WorkOrder.created_at) >= start,
               local_date(WorkOrder.created_at) <= today)
        .order_by(WorkOrder.created_at)).all()
    jobs = db.session.scalar(select(func.count(func.distinct(WoJob.id)))
                             .join(WorkOrder, WorkOrder.id == WoJob.work_order_id)
                             .join(Invoice, Invoice.work_order_id == WorkOrder.id)
                             .where(Invoice.status.in_(LIVE_INVOICE), Invoice.issue_date >= start,
                                    Invoice.issue_date <= today))
    rate = (Decimal(len(claims)) / jobs * 100).quantize(Decimal("0.1")) if jobs else None
    return claims, jobs, rate, start


def expiring_warranties(today=None):
    """Invoice lines whose warranty runs out between today and the end of the month."""
    today = today or local_today()
    nxt = date(today.year + (today.month == 12), 1 if today.month == 12 else today.month + 1, 1)
    return db.session.execute(
        select(InvoiceLine, Invoice, Customer).join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .join(Customer, Customer.id == Invoice.customer_id)
        .where(Invoice.status.in_(LIVE_INVOICE), InvoiceLine.warranty_until >= today,
               InvoiceLine.warranty_until < nxt)
        .order_by(InvoiceLine.warranty_until)).all()


# --- 1099 -------------------------------------------------------------------------------------

def nec_1099(year):
    """What each customer paid in a calendar year (receipts less reversals and refunds),
    split by who reports it: the payer on a 1099-NEC (cash, check, Zelle, bank transfer)
    or a payment app on a 1099-K. Flags customers who send 1099s and anyone at or over
    the threshold. Also totals by method, to match against 1099-Ks you receive."""
    start, end = date(year, 1, 1), date(year, 12, 31)
    threshold = figures.settings().nec_threshold
    rows = db.session.execute(
        select(Customer, Payment.method, func.sum(Payment.amount))
        .join(Payment, Payment.customer_id == Customer.id)
        .where(Payment.received_on >= start, Payment.received_on <= end)
        .group_by(Customer.id, Payment.method)).all()
    people, by_method = {}, defaultdict(Decimal)
    for customer, method, amount in rows:
        r = people.setdefault(customer.id, {"customer": customer, "nec": ZERO, "k": ZERO})
        r["k" if method in K_METHODS else "nec"] += amount
        by_method[method] += amount
    out = []
    for r in people.values():
        r["total"] = r["nec"] + r["k"]
        r["over"] = r["nec"] >= threshold
        r["flag"] = r["customer"].sends_1099 or r["over"]
        out.append(r)
    out.sort(key=lambda r: (-r["total"], r["customer"].name))
    methods = [(PAYMENT_METHODS.get(m, m), v + ZERO) for m, v in sorted(by_method.items())]
    return out, methods, threshold


# --- partners ---------------------------------------------------------------------------------

def partner_statement(start, end, basis="cash"):
    """Each partner's share of the period's net, the ledger for the period, and what
    they're owed now."""
    net = figures.pnl(start, end, basis)["net"]
    shares = figures.partner_shares(net, start, end)
    out = []
    for user, pct, amount in shares:
        totals = books.ledger_totals(user.id, start, end)
        out.append({"user": user, "pct": pct, "share": amount, "ledger": totals,
                    "owed": books.owed(user.id)["owed"]})
    return out, net
