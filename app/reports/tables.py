"""Every report as a table: the page draws it, ?format=csv writes it, and the year-end
package writes the same tables to its CSVs. Cells are raw values (Decimal, int, date,
str) or a Link; the column kind says how the page shows them."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import NamedTuple

from flask import url_for
from sqlalchemy import select

from app.books import journal
from app.books import service as books
from app.extensions import db
from app.models import (
    LEDGER_KINDS,
    PAYMENT_KINDS,
    PAYMENT_METHODS,
    CreditMemo,
    Customer,
    Invoice,
    InvoiceLine,
    Part,
    Payment,
    PaymentAllocation,
    StockLot,
    Vendor,
)
from app.reports import figures
from app.reports import service as reports
from app.reports.figures import BASES, ZERO
from app.timeutil import local_today, localdt


class Link(NamedTuple):
    text: object
    url: str

    def __str__(self):
        return str(self.text)


@dataclass
class Table:
    title: str
    cols: list                      # [(label, kind)]: text, money, qty, pct, num
    rows: list
    totals: list | None = None
    empty: str = "Nothing in this period."
    note: str | None = None
    row_classes: list = field(default_factory=list)


def _sum(rows, i):
    return sum((r[i] for r in rows if r[i] is not None), ZERO)


def _users():
    return figures.users_by_id()


def _who(users, user_id):
    return users[user_id].display_name if user_id in users else ""


# --- the P&L ----------------------------------------------------------------------------------

def pnl_table(start, end, basis):
    p = figures.pnl(start, end, basis)
    rows = [("Revenue", BASES[basis] + (": collected, less sales tax" if basis == "cash"
                                        else ": invoiced, less tax and credit memos"),
             p["revenue"]),
            ("Cost of goods", "Parts and sublets on invoices issued", p["cogs"]),
            ("Gross profit", "", p["gross"])]
    rows += [("Expenses", name, total) for name, total in p["by_category"]]
    rows += [("Expenses", "Total expenses", p["expenses"]),
             ("Mileage", "Standard mileage rate", p["mileage"]),
             ("Net profit", "", p["net"])]
    classes = ["basis" if r[0] in ("Gross profit", "Net profit") else
               "sub" if r[0] == "Expenses" and r[1] != "Total expenses" else "" for r in rows]
    return Table(f"Profit and loss, {start} to {end}", [("Section", "text"), ("Line", "text"),
                                                         ("Amount", "money")], rows,
                 row_classes=classes), p


def pnl_monthly_table(y, basis):
    rows = []
    for m in figures.months(y):
        p = figures.pnl(m.start, m.end, basis)
        rows.append([m.start.strftime("%Y-%m"), figures.collected(m.start, m.end),
                     figures.invoiced(m.start, m.end), p["revenue"], p["cogs"], p["gross"],
                     p["expenses"], p["mileage"], p["net"]])
    cols = [("Month", "text"), ("Collected", "money"), ("Invoiced", "money"),
            (f"Revenue ({basis})", "money"), ("Parts cost", "money"), ("Gross", "money"),
            ("Expenses", "money"), ("Mileage", "money"), ("Net", "money")]
    return Table(f"Profit and loss by month, {y} ({BASES[basis].lower()})", cols, rows,
                 totals=["Total"] + [_sum(rows, i) for i in range(1, 9)])


# --- money ------------------------------------------------------------------------------------

def aging_table(today):
    rows, buckets, total = figures.receivables(today)
    out = [[Link(r["invoice"].number, url_for("billing.show", invoice_id=r["invoice"].id)),
            r["customer"].display_name, r["invoice"].issue_date, r["invoice"].due_date,
            max(r["late"], 0), r["bucket"], r["invoice"].balance] for r in rows]
    return Table(f"Receivables aging, {today}", [
        ("Invoice", "text"), ("Customer", "text"), ("Issued", "text"), ("Due", "text"),
        ("Days late", "num"), ("Bucket", "text"), ("Owed", "money")], out,
        totals=["Total", "", "", "", "", "", total], empty="Nothing owed."), buckets


def sales_tax_table(start, end):
    rows, totals = books.tax_summary(start, end)
    out = [[r["jurisdiction"], r["invoices"], r["taxable"], r["nontaxable"], r["tax"],
            r["untaxed"]] for r in rows]
    return Table(f"Sales tax, {start} to {end}", [
        ("Jurisdiction", "text"), ("Invoices", "num"), ("Taxable sales", "money"),
        ("Non-taxable sales", "money"), ("Tax", "money"), ("Tax not charged", "money")], out,
        totals=["Total", sum(r["invoices"] for r in rows), totals["taxable"],
                totals["nontaxable"], totals["tax"], totals["untaxed"]],
        note="Invoices issued in the period less credit memos issued in it. \"Tax not "
             "charged\" is what invoices would have carried at their recorded rate while "
             "tax isn't collected.")


def nec_tables(y):
    rows, methods, threshold = reports.nec_1099(y)
    people = Table(f"1099 reconciliation, {y}", [
        ("Customer", "text"), ("Sends 1099s", "text"), ("Cash, check, Zelle, bank", "money"),
        ("Payment apps (1099-K)", "money"), ("Total", "money"), ("Check", "text")],
        [[r["customer"].display_name, "yes" if r["customer"].sends_1099 else "",
          r["nec"], r["k"], r["total"],
          ("at or over the threshold" if r["over"] else "sends 1099s") if r["flag"] else ""]
         for r in rows],
        totals=["Total", "", sum((r["nec"] for r in rows), ZERO),
                sum((r["k"] for r in rows), ZERO), sum((r["total"] for r in rows), ZERO), ""],
        empty="No payments that year.",
        note=f"Threshold {threshold:,.2f} (Settings → Books). Expect a 1099-NEC from flagged "
             "customers for the first column; payment apps report the second on a 1099-K. "
             "Have your tax preparer confirm.")
    by_method = Table("By payment method (match against 1099-Ks you receive)",
                      [("Method", "text"), ("Received", "money")], [list(m) for m in methods],
                      totals=["Total", sum((v for _, v in methods), ZERO)],
                      empty="No payments that year.")
    return people, by_method


def partners_table(start, end, basis):
    rows, net = reports.partner_statement(start, end, basis)
    kinds = list(LEDGER_KINDS)
    out = [[r["user"].display_name, r["pct"], r["share"]]
           + [r["ledger"].get(k, ZERO) for k in kinds] + [r["owed"]] for r in rows]
    cols = [("Partner", "text"), ("Share %", "pct"), ("Share of net", "money")] \
        + [(LEDGER_KINDS[k], "money") for k in kinds] + [("Owed to them now", "money")]
    return Table(f"Partners, {start} to {end} ({BASES[basis].lower()})", cols, out,
                 totals=["Total", "", _sum(out, 2)] + [_sum(out, 3 + i) for i in range(len(kinds))]
                 + [_sum(out, 3 + len(kinds))],
                 empty="No partners yet: mark each owner as a partner in Settings → Users.",
                 note=f"Net for the period {net:,.2f}, split by Settings → Books. Owed now is "
                      "every unpaid business cost they covered, whatever its date."), net


def expenses_table(start, end):
    users = _users()
    rows = books.expense_rows(start, end)
    out = [[r.on, r.payee, r.category, r.amount, r.tax, _who(users, r.paid_by_user_id),
            Link(r.po.number, url_for("purchasing.order", po_id=r.po.id)) if r.po else
            Link(f"Expense {r.expense.id}", url_for("books.expense", expense_id=r.expense.id)),
            (r.expense.receipt_sha256 if r.expense else r.po.receipt_sha256) or ""]
           for r in rows]
    return Table(f"Expenses, {start} to {end}", [
        ("Date", "text"), ("Payee", "text"), ("Category", "text"), ("Amount", "money"),
        ("Tax included", "money"), ("Paid by a partner", "text"), ("From", "text"),
        ("Receipt", "text")], out,
        totals=["Total", "", "", _sum(out, 3), _sum(out, 4), "", "", ""],
        note="Purchase orders count their unstocked items, shipping and tax paid on the "
             "order date; stocked parts are a cost when they're sold.")


def mileage_table(start, end):
    users = _users()
    rows = sorted(books.trips(start, end), key=lambda tv: (tv[0].trip_on, tv[0].id))
    out = [[t.trip_on, _who(users, t.user_id), t.miles, t.from_place or "", t.to_place or "",
            t.purpose, "yes" if t.own_vehicle else "", v] for t, v in rows]
    return Table(f"Mileage, {start} to {end}", [
        ("Date", "text"), ("Who", "text"), ("Miles", "qty"), ("From", "text"), ("To", "text"),
        ("Purpose", "text"), ("Own vehicle", "text"), ("Value", "money")], out,
        totals=["Total", "", sum((r[2] for r in out), Decimal(0)), "", "", "", "",
                _sum(out, 7)])


# --- parts ------------------------------------------------------------------------------------

def inventory_table(as_of):
    rows = reports.inventory_value(as_of)
    vendors = dict(db.session.execute(select(Vendor.id, Vendor.name)).all())
    out = [[Link(part.sku, url_for("parts.show", part_id=part.id)), part.name,
            Link(f"Lot {lot.id}", url_for("reports.lot", lot_id=lot.id)),
            localdt(lot.received_at, "%Y-%m-%d"), vendors.get(lot.vendor_id, ""), qty,
            lot.unit_cost, value] for lot, part, qty, value in rows]
    return Table(f"Inventory value at the end of {as_of}", [
        ("SKU", "text"), ("Part", "text"), ("Lot", "text"), ("Received", "text"),
        ("Vendor", "text"), ("On hand", "qty"), ("Unit cost", "cost"), ("Value", "money")], out,
        totals=["Total", "", "", "", "", "", "", _sum(out, 7)],
        empty="Nothing in stock on that day.",
        note="Stocked parts at what each lot cost (first in, first out).")


def lot_table(lot):
    rows = reports.lot_trace(lot)
    out = [[localdt(m.at), m.reason, m.qty,
            Link(wo.number, url_for("work.show", wo_id=wo.id)) if wo else "", m.note or ""]
           for m, wo in rows]
    part = db.session.get(Part, lot.part_id)
    return Table(f"Lot {lot.id}: {part.sku} {part.name}", [
        ("When", "text"), ("Move", "text"), ("Qty", "qty"), ("Work order", "text"),
        ("Note", "text")], out, totals=["On hand", "", sum((r[2] for r in out), Decimal(0)),
                                       "", ""], empty="No moves.")


def usage_table(start, end):
    out = [[Link(p.sku, url_for("parts.show", part_id=p.id)), p.name, qty, cost]
           for p, qty, cost in reports.parts_usage(start, end)]
    return Table(f"Parts used, {start} to {end}", [
        ("SKU", "text"), ("Part", "text"), ("Used", "qty"), ("Cost", "money")], out,
        totals=["Total", "", "", _sum(out, 3)],
        note="Issued to jobs less returned, at each lot's cost.")


# --- work -------------------------------------------------------------------------------------

def labor_table(start, end):
    out = [[u.display_name, b, o, b + o] for u, b, o in reports.labor_by_tech(start, end)]
    return Table(f"Labor by person, {start} to {end}", [
        ("Person", "text"), ("Billable hours", "qty"), ("Other hours", "qty"),
        ("Total hours", "qty")], out,
        totals=["Total", _sum(out, 1), _sum(out, 2), _sum(out, 3)],
        note="Finished timer entries, by the day they started.")


def jobs_table(start, end):
    rows, rate = reports.job_profitability(start, end)
    out = [[Link(r["wo"].number, url_for("work.show", wo_id=r["wo"].id)),
            r["customer"].display_name, ", ".join(r["invoices"]), r["billed"], r["parts"],
            r["hours"], r["labor"], r["profit"]] for r in rows]
    note = (f"Labor at the internal rate of {rate:,.2f} an hour (Settings → Books)."
            if rate is not None else
            "No internal labor rate set (Settings → Books), so profit is before labor.")
    return Table(f"Job profitability, invoiced {start} to {end}", [
        ("Work order", "text"), ("Customer", "text"), ("Invoices", "text"),
        ("Billed (no tax)", "money"), ("Parts cost", "money"), ("Hours", "qty"),
        ("Labor cost", "money"), ("Profit", "money")], out,
        totals=["Total", "", "", _sum(out, 3), _sum(out, 4), _sum(out, 5),
                _sum(out, 6) if rate is not None else None, _sum(out, 7)], note=note)


def turnaround_table(start, end):
    rows, mid = reports.turnaround(start, end)
    out = [[Link(r["wo"].number, url_for("work.show", wo_id=r["wo"].id)),
            r["customer"].display_name, localdt(r["from"], "%Y-%m-%d"),
            localdt(r["to"], "%Y-%m-%d") + ("" if r["ready"] else " (completed)"), r["days"]]
           for r in rows]
    return Table(f"Turnaround, ready {start} to {end}", [
        ("Work order", "text"), ("Customer", "text"), ("In", "text"), ("Ready", "text"),
        ("Days", "qty")], out, totals=["Median", "", "", "", mid] if rows else None,
        note="From intake (the first job received, else when the work order was opened) to "
             "Ready. Older work orders without a ready date use their completion date."), mid


def warranty_tables(today):
    claims, jobs, rate, start = reports.warranty(90, today)
    out = [[Link(wo.number, url_for("work.show", wo_id=wo.id)), customer.display_name,
            localdt(wo.created_at, "%Y-%m-%d"), wo.summary or ""] for wo, customer in claims]
    comebacks = Table(f"Warranty work orders, {start} to {today}", [
        ("Work order", "text"), ("Customer", "text"), ("Opened", "text"), ("Summary", "text")],
        out, empty="No warranty work in the last 90 days.",
        note=f"Comeback rate: {len(claims)} warranty work orders ÷ {jobs} jobs invoiced in "
             f"the same 90 days" + (f" = {rate}%." if rate is not None else "."))
    expiring = Table("Warranties running out this month", [
        ("Until", "text"), ("Invoice", "text"), ("Customer", "text"), ("Line", "text")],
        [[line.warranty_until, Link(inv.number, url_for("billing.show", invoice_id=inv.id)),
          customer.display_name, line.description]
         for line, inv, customer in reports.expiring_warranties(today)],
        empty="None this month.")
    return comebacks, expiring, rate


# --- the year-end package's raw rows ----------------------------------------------------------

def invoices_table(start, end):
    rows = db.session.execute(
        select(Invoice, Customer).join(Customer, Customer.id == Invoice.customer_id)
        .where(Invoice.status != "draft", Invoice.issue_date >= start,
               Invoice.issue_date <= end).order_by(Invoice.number)).all()
    return Table("Invoices", [
        ("Number", "text"), ("Issued", "text"), ("Due", "text"), ("Customer", "text"),
        ("Status", "text"), ("Taxable", "money"), ("Non-taxable", "money"), ("Tax", "money"),
        ("Total", "money"), ("Paid", "money"), ("Credited", "money"), ("Balance", "money"),
        ("Jurisdiction", "text"), ("PDF sha256", "text"), ("Void reason", "text")],
        [[i.number, i.issue_date, i.due_date, c.display_name, i.status, i.subtotal_taxable,
          i.subtotal_nontaxable, i.tax, i.total, i.amount_paid, i.amount_credited, i.balance,
          (i.tax_jurisdiction or {}).get("name", ""), i.pdf_sha256 or "", i.void_reason or ""]
         for i, c in rows])


def invoice_lines_table(start, end):
    rows = db.session.execute(
        select(InvoiceLine, Invoice).join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(Invoice.status != "draft", Invoice.issue_date >= start,
               Invoice.issue_date <= end).order_by(Invoice.number, InvoiceLine.position)).all()
    return Table("Invoice lines", [
        ("Invoice", "text"), ("Invoice status", "text"), ("Kind", "text"),
        ("Description", "text"), ("Qty", "qty"), ("Unit price", "money"),
        ("Line total", "money"), ("Taxable", "text"), ("Customer's own part", "text"),
        ("Unit cost", "cost"), ("Warranty until", "text")],
        [[inv.number, inv.status, ln.kind, ln.description, ln.qty, ln.unit_price,
          ln.line_total, "yes" if ln.taxable else "", "yes" if ln.customer_supplied else "",
          ln.unit_cost, ln.warranty_until or ""] for ln, inv in rows])


def payments_table(start, end):
    rows = db.session.execute(
        select(Payment, Customer).join(Customer, Customer.id == Payment.customer_id)
        .where(Payment.received_on >= start, Payment.received_on <= end)
        .order_by(Payment.received_on, Payment.id)).all()
    return Table("Payments", [
        ("Payment", "num"), ("Date", "text"), ("Customer", "text"), ("Kind", "text"),
        ("Method", "text"), ("Reference", "text"), ("Amount", "money"), ("Undoes", "text"),
        ("Note", "text")],
        [[p.id, p.received_on, c.display_name, PAYMENT_KINDS.get(p.kind, p.kind),
          PAYMENT_METHODS.get(p.method, p.method), p.reference or "", p.amount,
          p.reverses_payment_id or p.refund_of_payment_id or
          (f"credit memo {p.refund_of_credit_memo_id}" if p.refund_of_credit_memo_id else ""),
          p.note or ""] for p, c in rows])


def allocations_table(start, end):
    """Money applied to invoices, for payments dated in the period and credit memos
    issued in it, with each invoice's tax and total (to work out the tax inside it)."""
    pay = db.session.execute(
        select(PaymentAllocation, Payment, Invoice)
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
        .where(Payment.received_on >= start, Payment.received_on <= end)
        .order_by(Payment.received_on, PaymentAllocation.id)).all()
    memo = db.session.execute(
        select(PaymentAllocation, CreditMemo, Invoice)
        .join(CreditMemo, CreditMemo.id == PaymentAllocation.credit_memo_id)
        .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
        .where(CreditMemo.issue_date >= start, CreditMemo.issue_date <= end)
        .order_by(CreditMemo.issue_date, PaymentAllocation.id)).all()
    rows = [[a.id, "payment", p.id, p.received_on, inv.number, inv.tax, inv.total, a.amount]
            for a, p, inv in pay]
    rows += [[a.id, "credit memo", cm.number, cm.issue_date, inv.number, inv.tax, inv.total,
              a.amount] for a, cm, inv in memo]
    return Table("Allocations", [
        ("Allocation", "num"), ("From", "text"), ("Payment or memo", "text"), ("Date", "text"),
        ("Invoice", "text"), ("Invoice tax", "money"), ("Invoice total", "money"),
        ("Applied", "money")], rows)


def credit_memos_table(start, end):
    rows = db.session.execute(
        select(CreditMemo, Customer, Invoice).join(Customer, Customer.id == CreditMemo.customer_id)
        .join(Invoice, Invoice.id == CreditMemo.invoice_id)
        .where(CreditMemo.status != "draft", CreditMemo.issue_date >= start,
               CreditMemo.issue_date <= end).order_by(CreditMemo.number)).all()
    return Table("Credit memos", [
        ("Number", "text"), ("Issued", "text"), ("Customer", "text"), ("Invoice", "text"),
        ("Status", "text"), ("Taxable", "money"), ("Non-taxable", "money"), ("Tax", "money"),
        ("Total", "money"), ("Reason", "text"), ("PDF sha256", "text")],
        [[cm.number, cm.issue_date, c.display_name, inv.number, cm.status, cm.subtotal_taxable,
          cm.subtotal_nontaxable, cm.tax, cm.total, cm.reason or "", cm.pdf_sha256 or ""]
         for cm, c, inv in rows])


def ledger_table(start, end):
    users = _users()
    rows = sorted(books.entries(start=start, end=end, include_voided=True),
                  key=lambda e: (e.entry_on, e.id))
    return Table("Partner ledger", [
        ("Date", "text"), ("Partner", "text"), ("Kind", "text"), ("Amount", "money"),
        ("Note", "text"), ("Voided", "text")],
        [[e.entry_on, _who(users, e.user_id), LEDGER_KINDS.get(e.kind, e.kind), e.amount,
          e.note or "", e.void_reason or ""] for e in rows])


def filings_table():
    return Table("Sales tax filings", [
        ("From", "text"), ("To", "text"), ("Taxable sales", "money"),
        ("Tax collected", "money"), ("Tax due", "money"), ("Filed", "text"), ("Paid", "text"),
        ("Confirmation", "text")],
        [[f.period_start, f.period_end, f.taxable_sales, f.tax_collected, f.tax_due,
          f.filed_on or "", f.paid_on or "", f.confirmation or ""]
         for f in books.filings()])


def journal_table(es):
    return Table("General journal", [("Date", "text"), ("Entry", "num"), ("Account", "text"),
                                     ("Number", "text"), ("Debit", "money"),
                                     ("Credit", "money"), ("Memo", "text"), ("Name", "text"),
                                     ("Reference", "text")], journal.lines(es))


def trial_balance_table(es, start, end):
    rows = journal.trial_balance(es)
    return Table(f"Trial balance, {start} to {end}",
                 [("Account", "text"), ("Number", "text"), ("Debits", "money"),
                  ("Credits", "money"), ("Debits less credits", "money")], rows,
                 totals=["Total", "", _sum(rows, 2), _sum(rows, 3), _sum(rows, 4)],
                 note="Activity in the period only, not opening balances: it starts from "
                      "nothing on the first day.")


def lot_for(lot_id):
    return db.session.get(StockLot, lot_id)


def first_year():
    """The earliest year with anything in the books, for the year-end package list."""
    from sqlalchemy import func

    from app.models import Expense, Trip
    years = [db.session.scalar(select(func.min(col))) for col in (
        Invoice.issue_date, Payment.received_on, Expense.spent_on, Trip.trip_on)]
    years = [d.year for d in years if d is not None]
    return min(years) if years else local_today().year


def to_csv(table):
    """The table as CSV, its totals row last."""
    return reports.csv_text([label for label, _ in table.cols],
                            list(table.rows) + ([table.totals] if table.totals else []))


def issued_documents(start, end):
    return db.session.scalars(select(Invoice).where(
        Invoice.status != "draft", Invoice.issue_date >= start, Invoice.issue_date <= end)
        .order_by(Invoice.number)).all()


def issued_memos(start, end):
    return db.session.scalars(select(CreditMemo).where(
        CreditMemo.status == "issued", CreditMemo.issue_date >= start,
        CreditMemo.issue_date <= end).order_by(CreditMemo.number)).all()


def receipts(start, end):
    """[(date, payee, sha256, type)] behind the period's expense rows."""
    out = []
    for r in books.expense_rows(start, end):
        src = r.expense or r.po
        if src.receipt_sha256:
            out.append((r.on, r.payee, src.receipt_sha256, src.receipt_type))
    return out
