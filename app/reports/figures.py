"""Every number the dashboard, reports, CSVs and the year-end package show comes from here,
so they can't disagree.

Definitions (owner, 2026-10-13):
  collected  money in during the period: receipts, less reversals and refunds, by the date
             received; less the sales tax inside any part applied to a taxed invoice (that
             invoice's tax share of each allocation, rounded half-up). Deposits and credit on
             account count when received. Cash basis revenue.
  invoiced   invoices issued in the period, without their tax, less credit memos issued in
             it. Voided invoices never count. Accrual basis revenue.
  cogs       parts and sublets on invoices issued in the period, at each line's frozen
             cost; the customer's own parts cost nothing. Both bases.
  gross      revenue (by the basis) − cogs.
  net        gross − expenses (purchase-order rows included) − mileage.
Money is Decimal, rounded half-up per row, then summed."""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import and_, func, or_, select

from app.books import service as books
from app.extensions import db
from app.models import (
    CreditMemo,
    Customer,
    Invoice,
    InvoiceLine,
    Part,
    Payment,
    PaymentAllocation,
    ShopSetting,
    TimeEntry,
    User,
    WorkOrder,
)
from app.pricing import round_money
from app.timeutil import local_today, shop_tz

ZERO = Decimal("0.00")
LIVE_INVOICE = ("issued", "partially_paid", "paid", "written_off")
OPEN_INVOICE = ("issued", "partially_paid")
BASES = {"cash": "Cash basis", "accrual": "Accrual basis"}
AGING = ((0, "Not yet due"), (30, "1–30 days late"), (60, "31–60 days late"),
         (90, "61–90 days late"), (None, "Over 90 days late"))


# --- periods ----------------------------------------------------------------------------------

def local_date(column):
    """A timestamptz column as the shop's calendar date (SHOP_TZ), for comparing to dates."""
    return func.date(func.timezone(str(shop_tz()), column))


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str

    def last_year(self):
        return Period(_year_back(self.start), _year_back(self.end), f"{self.label}, last year")


def _year_back(d):
    try:
        return d.replace(year=d.year - 1)
    except ValueError:            # February 29
        return d.replace(year=d.year - 1, day=28)


def to_date(today=None):
    """Month, quarter and year to date."""
    today = today or local_today()
    q_month = (today.month - 1) // 3 * 3 + 1
    return [Period(today.replace(day=1), today, "Month to date"),
            Period(date(today.year, q_month, 1), today, "Quarter to date"),
            Period(date(today.year, 1, 1), today, "Year to date")]


def year(y):
    return Period(date(y, 1, 1), date(y, 12, 31), str(y))


def months(y):
    out = []
    for m in range(1, 13):
        nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        out.append(Period(date(y, m, 1), nxt - timedelta(days=1), date(y, m, 1).strftime("%b")))
    return out


def last_months(n=12, today=None):
    """The last n calendar months, oldest first, this one included."""
    today = today or local_today()
    y, m, out = today.year, today.month, []
    for _ in range(n):
        start = date(y, m, 1)
        nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        out.append(Period(start, nxt - timedelta(days=1), start.strftime("%b %Y")))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


# --- revenue ----------------------------------------------------------------------------------

def _half_up(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def cash_in(start, end):
    """Receipts less reversals and refunds, by the date received."""
    return db.session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.received_on >= start, Payment.received_on <= end)) + ZERO


def tax_in_payments(start, end):
    """The sales tax inside money applied to taxed invoices, by the payment's date."""
    rows = db.session.execute(
        select(PaymentAllocation.amount, Invoice.tax, Invoice.total)
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
        .where(Payment.received_on >= start, Payment.received_on <= end, Invoice.tax > 0))
    return sum((_half_up(amount * tax / total) for amount, tax, total in rows), ZERO)


def collected(start, end):
    return cash_in(start, end) - tax_in_payments(start, end)


def invoiced(start, end):
    """Invoices issued in the period without their tax, less credit memos issued in it."""
    billed = db.session.scalar(select(func.coalesce(func.sum(
        Invoice.subtotal_taxable + Invoice.subtotal_nontaxable), 0)).where(
        Invoice.status.in_(LIVE_INVOICE), Invoice.issue_date >= start,
        Invoice.issue_date <= end))
    credited = db.session.scalar(select(func.coalesce(func.sum(
        CreditMemo.subtotal_taxable + CreditMemo.subtotal_nontaxable), 0)).where(
        CreditMemo.status == "issued", CreditMemo.issue_date >= start,
        CreditMemo.issue_date <= end))
    return billed - credited + ZERO


def stocked_part_ids():
    return set(db.session.scalars(select(Part.id).where(Part.track_stock.is_(True))))


def is_cogs(line, stocked):
    """Does an invoice line's cost count as cost of goods? A stocked part's does (its lot
    cost: inventory until it's sold), and a sublet's. A part the shop doesn't stock was an
    expense when it was bought (supplies on its purchase order, or an expense typed in),
    so it isn't counted again; nor is a customer's own part. `stocked` is
    stocked_part_ids(); cogs_line() is the same rule in SQL. Whether a part is stocked
    can't change once it's used (parts.routes.save_part), so past periods stay put."""
    return (line.unit_cost is not None and not line.customer_supplied
            and (line.kind == "sublet" or (line.kind == "part" and line.part_id in stocked)))


def cogs_line():
    """is_cogs() as a condition on InvoiceLine."""
    return and_(InvoiceLine.unit_cost.isnot(None), InvoiceLine.customer_supplied.is_(False),
                or_(InvoiceLine.kind == "sublet",
                    and_(InvoiceLine.kind == "part", InvoiceLine.part_id.in_(
                        select(Part.id).where(Part.track_stock.is_(True))))))


def cogs(start, end):
    """What the stocked parts and sublets on the period's invoices cost, per line to the
    cent (see is_cogs)."""
    return db.session.scalar(select(func.coalesce(func.sum(
        func.round(InvoiceLine.qty * InvoiceLine.unit_cost, 2)), 0))
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(Invoice.status.in_(LIVE_INVOICE), Invoice.issue_date >= start,
               Invoice.issue_date <= end, cogs_line())) + ZERO


def revenue(start, end, basis):
    return collected(start, end) if basis == "cash" else invoiced(start, end)


# --- the P&L ----------------------------------------------------------------------------------

def mileage_total(start, end):
    return sum((v for _, v in books.trips(start, end) if v is not None), ZERO)


def pnl(start, end, basis="cash"):
    rows = books.expense_rows(start, end)
    rev, cost = revenue(start, end, basis), cogs(start, end)
    spent = sum((r.amount for r in rows), ZERO)
    miles = mileage_total(start, end)
    gross = rev - cost
    return {"start": start, "end": end, "basis": basis, "revenue": rev, "cogs": cost,
            "gross": gross, "expenses": spent, "by_category": books.by_category(rows),
            "mileage": miles, "net": gross - spent - miles}


# --- balances ---------------------------------------------------------------------------------

def receivables(today=None):
    """Open invoices by how late they are: (rows, {bucket: total}, total)."""
    today = today or local_today()
    buckets = {label: ZERO for _, label in AGING}
    rows = []
    for inv, customer in db.session.execute(
            select(Invoice, Customer).join(Customer, Customer.id == Invoice.customer_id)
            .where(Invoice.status.in_(OPEN_INVOICE), Invoice.balance > 0)
            .order_by(Invoice.due_date, Invoice.id)):
        late = (today - inv.due_date).days
        label = next(lbl for limit, lbl in AGING if limit is None or late <= limit)
        buckets[label] += inv.balance
        rows.append({"invoice": inv, "customer": customer, "late": late, "bucket": label})
    return rows, buckets, sum(buckets.values(), ZERO)


def unapplied_credit():
    """Money customers have with you that isn't on an invoice: everything paid in (less
    reversals and refunds) and every issued credit memo, less what's been applied."""
    paid = db.session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)))
    credits = db.session.scalar(select(func.coalesce(func.sum(CreditMemo.total), 0)).where(
        CreditMemo.status == "issued"))
    applied = db.session.scalar(select(func.coalesce(func.sum(PaymentAllocation.amount), 0)))
    return paid + credits - applied + ZERO


def reimbursements_owed():
    return sum((books.owed(u.id)["owed"] for u in books.partners()), ZERO)


def settings():
    return db.session.get(ShopSetting, True)


def set_aside(net):
    pct = settings().set_aside_pct
    if pct is None or net <= 0:
        return None
    return round_money(net * pct / 100)


def partner_shares(net, start, end):
    """[(partner, share %, amount)] by the split in Settings → Books. Amounts add up to
    net exactly: the last partner takes the rounding."""
    people = books.partners()
    if not people:
        return []
    mode = settings().split_mode
    if mode == "custom":
        weights = {u.id: u.split_pct or ZERO for u in people}
    elif mode == "hours":
        minutes = dict(db.session.execute(
            select(TimeEntry.user_id, func.sum(TimeEntry.minutes))
            .where(TimeEntry.user_id.in_([u.id for u in people]),
                   TimeEntry.ended_at.isnot(None),
                   local_date(TimeEntry.started_at) >= start,
                   local_date(TimeEntry.started_at) <= end)
            .group_by(TimeEntry.user_id)).all())
        weights = {u.id: Decimal(minutes.get(u.id) or 0) for u in people}
    else:
        weights = {u.id: Decimal(1) for u in people}
    total = sum(weights.values(), Decimal(0))
    if not total:
        weights, total = {u.id: Decimal(1) for u in people}, Decimal(len(people))
    out, left = [], net
    for i, u in enumerate(people):
        pct = weights[u.id] / total * 100
        amount = left if i == len(people) - 1 else round_money(net * weights[u.id] / total)
        left -= amount
        out.append((u, pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), amount))
    return out


def top_figures(period, basis):
    """The dashboard's row for one period."""
    p = pnl(period.start, period.end, basis)
    return {"period": period, "collected": collected(period.start, period.end),
            "invoiced": invoiced(period.start, period.end), "cogs": p["cogs"],
            "gross": p["gross"], "expenses": p["expenses"], "mileage": p["mileage"],
            "net": p["net"], "set_aside": set_aside(p["net"])}


# --- chart data -------------------------------------------------------------------------------
# One function per chart: Chart.js draws it on screen, the SVG drawer for print and PDFs.

def chart_monthly(today=None):
    """Collected vs invoiced for the last 12 months."""
    periods = last_months(12, today)
    return {"labels": [p.label for p in periods],
            "series": [("Collected", [collected(p.start, p.end) for p in periods]),
                       ("Invoiced", [invoiced(p.start, p.end) for p in periods])]}


def chart_customers(start, end, basis="accrual", top=10):
    """Revenue by customer, largest first."""
    totals = defaultdict(Decimal)
    if basis == "cash":
        for cid, amount in db.session.execute(
                select(Payment.customer_id, func.sum(Payment.amount))
                .where(Payment.received_on >= start, Payment.received_on <= end)
                .group_by(Payment.customer_id)):
            totals[cid] += amount
    else:
        for cid, amount in db.session.execute(
                select(Invoice.customer_id,
                       func.sum(Invoice.subtotal_taxable + Invoice.subtotal_nontaxable))
                .where(Invoice.status.in_(LIVE_INVOICE), Invoice.issue_date >= start,
                       Invoice.issue_date <= end).group_by(Invoice.customer_id)):
            totals[cid] += amount
        for cid, amount in db.session.execute(
                select(CreditMemo.customer_id,
                       func.sum(CreditMemo.subtotal_taxable + CreditMemo.subtotal_nontaxable))
                .where(CreditMemo.status == "issued", CreditMemo.issue_date >= start,
                       CreditMemo.issue_date <= end).group_by(CreditMemo.customer_id)):
            totals[cid] -= amount
    names = dict(db.session.execute(select(Customer.id, Customer.name).where(
        Customer.id.in_(list(totals)))).all()) if totals else {}
    ranked = sorted(((names.get(c, "?"), v + ZERO) for c, v in totals.items() if v),
                    key=lambda kv: (-kv[1], kv[0]))[:top]
    return {"labels": [n for n, _ in ranked], "series": [("Revenue", [v for _, v in ranked])]}


def chart_wo_mix(start, end):
    """Work orders opened in the period, by kind."""
    from app.models import WO_KINDS
    counts = dict(db.session.execute(
        select(WorkOrder.kind, func.count(WorkOrder.id))
        .where(local_date(WorkOrder.created_at) >= start, local_date(WorkOrder.created_at) <= end)
        .group_by(WorkOrder.kind)).all())
    kinds = [k for k in WO_KINDS if counts.get(k)]
    return {"labels": [WO_KINDS[k] for k in kinds],
            "series": [("Work orders", [Decimal(counts[k]) for k in kinds])]}


def chart_json(data, money=True, horizontal=False):
    """Chart data for Chart.js, in a data- attribute (the CSP allows no inline scripts).
    Amounts go as strings: they're only drawn, never added up in the browser."""
    return json.dumps({"labels": data["labels"], "money": money, "horizontal": horizontal,
                       "series": [{"name": n, "data": [str(v) for v in vals]}
                                  for n, vals in data["series"]]})


def users_by_id():
    return {u.id: u for u in db.session.scalars(select(User))}
