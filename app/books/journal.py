"""The general journal for an accountant (Phase 9c): every money event in a date range as
a balanced double entry, under account names the owner can change (Settings → Accounts;
account_map keeps only the changes). Accrual basis, and built from the same figures as the
accrual P&L, so income less costs here is the P&L's net for the same dates.

Simplifications, said once:
- Customers' credit (a payment not on an invoice yet, a credit memo) stays in Accounts
  receivable as a negative balance: a payment credits receivables in full, and applying it
  to an invoice moves nothing.
- Money through a business account goes to the account it was matched to in Money → Bank,
  else to the account for its payment method (payments) or "Checking" (everything else).
- Stock is inventory when bought (its purchase order) and cost of goods when sold (on its
  invoice), as the P&L counts it. Stock received without a purchase order comes in against
  a clearing account the accountant can sort out.
- A voided invoice, expense or trip is left out entirely.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select

from app.books import service as books
from app.extensions import db
from app.models import (
    PAYMENT_METHODS,
    AccountMap,
    BankAccount,
    BankLine,
    BankMatch,
    CreditMemo,
    Customer,
    Expense,
    ExpenseCategory,
    Invoice,
    InvoiceLine,
    PartnerEntry,
    Payment,
    PurchaseOrder,
    SalesTaxFiling,
    StockLot,
    StockMove,
    Trip,
    User,
    Vendor,
)
from app.reports.figures import LIVE_INVOICE, local_date

ZERO = Decimal("0.00")

# key: (built-in name, what it's for)
FIXED = {
    "checking": ("Checking", "Money in or out of the business not matched in Money → Bank"),
    "receivables": ("Accounts receivable", "What customers owe, less their credit"),
    "sales_tax": ("Sales tax payable", "Tax on invoices, less credit memos and what was paid "
                                       "to the state"),
    "income:labor": ("Sales: labor", "Labor lines on invoices"),
    "income:part": ("Sales: parts", "Part lines on invoices"),
    "income:fee": ("Sales: fees", "Fee lines on invoices"),
    "income:sublet": ("Sales: sublet work", "Sublet lines on invoices"),
    "income:discount": ("Discounts given", "Discount lines on invoices"),
    "income:credits": ("Returns and allowances", "Credit memos, before tax"),
    "cogs": ("Cost of goods sold", "What the parts and sublets on invoices cost"),
    "mileage": ("Car and truck expenses (mileage)", "Trips at the standard mileage rate"),
    "inventory": ("Inventory", "Stocked parts: in when ordered, out when invoiced"),
    "inventory_clearing": ("Stock received without a purchase order",
                           "Parts received by hand, for the accountant to sort out"),
    "sublet_clearing": ("Sublet costs to pay", "What sublets on invoices cost, until the "
                                               "subcontractor's bill is entered"),
    "vehicle_clearing": ("Business vehicle mileage", "Miles in a business vehicle, whose real "
                                                     "costs are expenses"),
}
# what a purchase order spends that isn't stock, when no expense category has the code
PO_COSTS = {"supplies": ("Supplies", "Purchase orders: parts the shop doesn't stock"),
            "shipping": ("Shipping", "Purchase orders: shipping"),
            "purchase_tax": ("Sales tax paid", "Purchase orders: tax paid")}
WALLETS = {"cash": "Cash on hand", "venmo": "Venmo", "paypal": "PayPal",
           "cashapp": "Cash App"}
GROUPS = (("money", "Money"), ("customers", "Customers and sales tax"), ("income", "Income"),
          ("costs", "Costs"), ("stock", "Inventory and clearing"), ("people", "Partners"))


def group_of(key):
    head = key.split(":")[0]
    if head in ("checking", "method", "bank"):
        return "money"
    if head in ("receivables", "sales_tax"):
        return "customers"
    if head == "income":
        return "income"
    if head in ("cogs", "mileage", "category") or key in PO_COSTS:
        return "costs"
    if head in ("owed", "capital", "draws"):
        return "people"
    return "stock"


def _people():
    """Partners, and anyone a record says the business owes or paid."""
    ids = set()
    for column, where in ((Expense.paid_by_user_id, Expense.voided_at.is_(None)),
                          (PurchaseOrder.paid_by_user_id, PurchaseOrder.paid_by_user_id.is_not(None)),
                          (Trip.user_id, Trip.own_vehicle.is_(True)),
                          (PartnerEntry.user_id, PartnerEntry.voided_at.is_(None))):
        ids.update(db.session.scalars(select(column).where(where).distinct()))
    return books.partners(include=ids)


def defaults():
    """{key: (built-in name, what it's for)} for every account the data can use now."""
    out = dict(FIXED)
    coded = set(db.session.scalars(select(ExpenseCategory.code).where(
        ExpenseCategory.code.is_not(None))))
    out.update({k: v for k, v in PO_COSTS.items() if k not in coded})
    for method, label in PAYMENT_METHODS.items():
        out[f"method:{method}"] = (WALLETS.get(method, "Checking"),
                                   f"{label} payments not matched in Money → Bank")
    for c in db.session.scalars(select(ExpenseCategory).order_by(ExpenseCategory.position,
                                                                 ExpenseCategory.name)):
        out[f"category:{c.id}"] = (c.name, f"Expenses in {c.name}" + (
            f" (Schedule C: {c.schedule_c})" if c.schedule_c else ""))
    for a in db.session.scalars(select(BankAccount).order_by(BankAccount.name)):
        out[f"bank:{a.id}"] = (a.name, f"What's matched to {a.name} in Money → Bank")
    for u in _people():
        who = u.display_name
        out[f"owed:{u.id}"] = (f"Owed to {who}", f"Costs {who} paid, less reimbursements")
        out[f"capital:{u.id}"] = (f"{who}, capital", f"Money {who} put in")
        out[f"draws:{u.id}"] = (f"{who}, draws", f"Draws and distributions to {who}")
    return out


def chart():
    """{key: (name, number)}: the built-in names with the owner's changes on top."""
    out = {k: (name, None) for k, (name, _) in defaults().items()}
    for row in db.session.scalars(select(AccountMap)):
        out[row.key] = (row.name, row.number)
    return out


@dataclass
class Entry:
    on: object
    memo: str
    name: str = ""
    reference: str = ""
    lines: list = field(default_factory=list)     # [(key, debit, credit)]

    def debit(self, key, amount):
        if amount:
            self.lines.append((key, amount, ZERO) if amount > 0 else (key, ZERO, -amount))

    def credit(self, key, amount):
        self.debit(key, -amount)

    @property
    def balanced(self):
        return sum((d - c for _, d, c in self.lines), ZERO) == 0


def _bank_accounts(column):
    """{record id: 'bank:<account id>'} for records of this kind matched in Money → Bank."""
    rows = db.session.execute(
        select(getattr(BankMatch, column), BankLine.bank_account_id)
        .join(BankLine, BankLine.id == BankMatch.bank_line_id)
        .where(getattr(BankMatch, column).is_not(None))).all()
    return {rid: f"bank:{aid}" for rid, aid in rows}


def entries(start, end):
    """The journal for start..end (inclusive), in date order."""
    out = []
    customers = {c.id: c.display_name for c in db.session.scalars(select(Customer))}
    users = dict(db.session.execute(select(User.id, User.display_name)).all())

    # invoices: receivables against income by kind, and tax
    invoices = db.session.scalars(select(Invoice).where(
        Invoice.status.in_(LIVE_INVOICE), Invoice.issue_date.between(start, end))
        .order_by(Invoice.issue_date, Invoice.number)).all()
    lines = defaultdict(list)
    for line in db.session.scalars(select(InvoiceLine).where(
            InvoiceLine.invoice_id.in_([i.id for i in invoices]))):
        lines[line.invoice_id].append(line)
    for inv in invoices:
        e = Entry(inv.issue_date, f"Invoice {inv.number}", customers.get(inv.customer_id, ""),
                  inv.number)
        e.debit("receivables", inv.total)
        by_kind = defaultdict(lambda: ZERO)
        for line in lines[inv.id]:
            by_kind[line.kind] += line.line_total
        for kind, amount in sorted(by_kind.items()):
            e.credit(f"income:{kind}", amount)
        e.credit("sales_tax", inv.tax)
        out.append(e)
        cost_parts = sum((round(x.qty * x.unit_cost, 2) for x in lines[inv.id]
                          if x.kind == "part" and x.unit_cost is not None
                          and not x.customer_supplied), ZERO)
        cost_sublets = sum((round(x.qty * x.unit_cost, 2) for x in lines[inv.id]
                            if x.kind == "sublet" and x.unit_cost is not None
                            and not x.customer_supplied), ZERO)
        if cost_parts or cost_sublets:
            c = Entry(inv.issue_date, f"Cost of {inv.number}", customers.get(inv.customer_id, ""),
                      inv.number)
            c.debit("cogs", cost_parts + cost_sublets)
            c.credit("inventory", cost_parts)
            c.credit("sublet_clearing", cost_sublets)
            out.append(c)

    # credit memos
    for cm in db.session.scalars(select(CreditMemo).where(
            CreditMemo.status == "issued", CreditMemo.issue_date.between(start, end))
            .order_by(CreditMemo.issue_date, CreditMemo.number)):
        e = Entry(cm.issue_date, f"Credit memo {cm.number}", customers.get(cm.customer_id, ""),
                  cm.number)
        e.debit("income:credits", cm.subtotal_taxable + cm.subtotal_nontaxable)
        e.debit("sales_tax", cm.tax)
        e.credit("receivables", cm.total)
        out.append(e)

    # payments in and out
    banked = _bank_accounts("payment_id")
    for p in db.session.scalars(select(Payment).where(Payment.received_on.between(start, end))
                                .order_by(Payment.received_on, Payment.id)):
        account = banked.get(p.id, f"method:{p.method}")
        label = {"receipt": "Payment", "refund": "Refund", "reversal": "Reversal"}[p.kind]
        e = Entry(p.received_on, f"{label} #{p.id} ({PAYMENT_METHODS.get(p.method, p.method)})",
                  customers.get(p.customer_id, ""), p.reference or f"#{p.id}")
        e.debit(account, p.amount)
        e.credit("receivables", p.amount)
        out.append(e)

    # expenses (and what POs spent that isn't stock), on each one's date
    category_key = {c.name: f"category:{c.id}" for c in db.session.scalars(
        select(ExpenseCategory))}
    banked_exp = _bank_accounts("expense_id")
    for x in db.session.scalars(select(Expense).where(
            Expense.voided_at.is_(None), Expense.spent_on.between(start, end))
            .order_by(Expense.spent_on, Expense.id)):
        e = Entry(x.spent_on, f"Expense: {x.payee}", x.payee, f"expense #{x.id}")
        e.debit(f"category:{x.category_id}", x.amount)
        e.credit(f"owed:{x.paid_by_user_id}" if x.paid_by_user_id
                 else banked_exp.get(x.id, "checking"), x.amount)
        out.append(e)
    banked_po = _bank_accounts("purchase_order_id")
    po_rows = defaultdict(list)
    for row in books.po_expense_rows(start, end):
        po_rows[row.po.id].append(row)
    for po, vendor in db.session.execute(
            select(PurchaseOrder, Vendor).join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
            .where(PurchaseOrder.status.in_(books.SPENT_PO_STATUSES),
                   PurchaseOrder.ordered_on.between(start, end))
            .order_by(PurchaseOrder.ordered_on, PurchaseOrder.id)):
        total = books.po_total(po)
        e = Entry(po.ordered_on, f"{po.number}: {vendor.name}", vendor.name, po.number or "")
        spent = ZERO
        for row in po_rows[po.id]:
            e.debit(category_key.get(row.category, row.category), row.amount)
            spent += row.amount
        e.debit("inventory", total - spent)
        e.credit(f"owed:{po.paid_by_user_id}" if po.paid_by_user_id
                 else banked_po.get(po.id, "checking"), total)
        out.append(e)

    # stock received without a purchase order
    received_on = local_date(StockLot.received_at)
    for lot, on, qty in db.session.execute(
            select(StockLot, received_on, func.sum(StockMove.qty))
            .join(StockMove, StockMove.lot_id == StockLot.id)
            .where(StockLot.po_line_id.is_(None), StockMove.reason == "receive",
                   received_on.between(start, end))
            .group_by(StockLot.id).order_by(StockLot.id)):
        value = round(qty * lot.unit_cost, 2)
        if value:
            e = Entry(on, f"Stock received, lot #{lot.id}", reference=f"lot #{lot.id}")
            e.debit("inventory", value)
            e.credit("inventory_clearing", value)
            out.append(e)

    # mileage
    for trip, value in books.trips(start, end):
        if value:
            e = Entry(trip.trip_on, f"Mileage: {trip.purpose or ''}"[:200],
                      users.get(trip.user_id, ""), f"trip #{trip.id}")
            e.debit("mileage", value)
            e.credit(f"owed:{trip.user_id}" if trip.own_vehicle else "vehicle_clearing", value)
            out.append(e)

    # partners' money
    banked_partner = _bank_accounts("partner_entry_id")
    for p in db.session.scalars(select(PartnerEntry).where(
            PartnerEntry.voided_at.is_(None), PartnerEntry.entry_on.between(start, end))
            .order_by(PartnerEntry.entry_on, PartnerEntry.id)):
        account = banked_partner.get(p.id, "checking")
        e = Entry(p.entry_on, f"{users.get(p.user_id, '?')}: {p.kind}",
                  users.get(p.user_id, ""), f"ledger #{p.id}")
        if p.kind == "contribution":
            e.debit(account, p.amount)
            e.credit(f"capital:{p.user_id}", p.amount)
        elif p.kind == "reimbursement":
            e.debit(f"owed:{p.user_id}", p.amount)
            e.credit(account, p.amount)
        else:
            e.debit(f"draws:{p.user_id}", p.amount)
            e.credit(account, p.amount)
        out.append(e)

    # sales tax paid to the state
    banked_tax = _bank_accounts("sales_tax_filing_id")
    for f in db.session.scalars(select(SalesTaxFiling).where(
            SalesTaxFiling.paid_on.between(start, end)).order_by(SalesTaxFiling.paid_on)):
        if f.tax_due:
            e = Entry(f.paid_on, f"Sales tax {f.period_start} to {f.period_end}",
                      reference=f.confirmation or "")
            e.debit("sales_tax", f.tax_due)
            e.credit(banked_tax.get(f.id, "checking"), f.tax_due)
            out.append(e)

    out.sort(key=lambda e: e.on)
    return out


def lines(es):
    """The journal as rows: (date, entry no., account, number, debit, credit, memo, name,
    reference). Entries are numbered from 1 in date order; an empty side is None."""
    names = chart()
    out = []
    for n, e in enumerate(es, 1):
        for key, debit, credit in e.lines:
            name, number = names.get(key, (key, None))
            out.append((e.on, n, name, number, debit or None, credit or None, e.memo,
                        e.name, e.reference))
    return out


def trial_balance(es):
    """[(account, number, debits, credits, debits less credits)], by account name. Keys
    under the same name and number are one account (every unmatched check and Zelle
    payment is in Checking, say)."""
    names = chart()
    totals = defaultdict(lambda: [ZERO, ZERO])
    for e in es:
        for key, debit, credit in e.lines:
            account = names.get(key, (key, None))
            totals[account][0] += debit
            totals[account][1] += credit
    out = [(name, number, debit, credit, debit - credit)
           for (name, number), (debit, credit) in totals.items()]
    return sorted(out, key=lambda r: (r[0].lower(), r[1] or ""))


def on_pnl(key):
    """Is this an income or cost account (rather than an asset, liability or equity)?"""
    return key.startswith(("income:", "category:")) or key in ("cogs", "mileage", *PO_COSTS)


def net_income(es):
    """Income less costs from the journal: the accrual P&L's net, if all is well."""
    return sum((credit - debit for e in es for key, debit, credit in e.lines if on_pnl(key)),
               ZERO)
