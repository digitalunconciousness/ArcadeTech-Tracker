"""The eight desktop lists: work orders, estimates, customers, assets, invoices, payments,
parts and purchase orders. Each is a query, its columns and its filters."""

from datetime import date

from flask import url_for
from sqlalchemy import exists, func, literal, or_, select

from app.auth.decorators import COST_ROLES, EDIT_ROLES
from app.lists.engine import Col, Filter, Spec
from app.models import (
    ASSET_KINDS,
    ASSET_STATUSES,
    ESTIMATE_STATUSES,
    INVOICE_STATUSES,
    OPEN_WO_STATUSES,
    PAYMENT_KINDS,
    PAYMENT_METHODS,
    PO_STATUSES,
    PRIORITIES,
    WO_KINDS,
    WO_STATUSES,
    Asset,
    Customer,
    Estimate,
    Invoice,
    Part,
    Payment,
    PoLine,
    PurchaseOrder,
    StockMove,
    User,
    Vendor,
    WorkOrder,
    WorkOrderTech,
)
from app.reports.tables import Link
from app.search.routes import like
from app.timeutil import local_today

OPEN_INVOICE = ("issued", "partially_paid")
KINDS = {k: k.upper() if k == "psu" else k.title() for k in ASSET_KINDS}
OPEN_ESTIMATE = ("draft", "sent", "approved")
OPEN_PO = ("draft", "ordered", "partially_received")
ACTIVE_ASSET = ("in_service", "in_shop", "awaiting_pickup", "shipped_back")
PRIORITY_ORDER = {"rush": 0, "high": 1, "normal": 2, "low": 3}


def _date(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def date_from(column):
    def apply(q, value):
        d = _date(value)
        return q.where(column >= d) if d else q
    return apply


def date_to(column):
    def apply(q, value):
        d = _date(value)
        return q.where(column <= d) if d else q
    return apply


def status_filter(column, open_values, labels, open_label="Open"):
    def apply(q, value):
        if value == "open":
            return q.where(column.in_(open_values))
        if value == "all":
            return q
        return q.where(column == value) if value in labels else q
    return Filter("status", "Status", apply, [("open", open_label), ("all", "All")]
                  + list(labels.items()), default="open")


def choice_filter(key, label, column, labels):
    return Filter(key, label, lambda q, v: q.where(column == v) if v in labels else q,
                  [("", "Any")] + list(labels.items()))


def techs():
    from app.extensions import db
    return [("", "Anyone")] + [(str(u.id), u.display_name) for u in db.session.scalars(
        select(User).where(User.active.is_(True), User.role.in_(EDIT_ROLES))
        .order_by(User.display_name))]


def badge(text, tone=""):
    return (text, tone)


# --- work orders ------------------------------------------------------------------------------

def _work_query():
    names = (select(func.string_agg(User.display_name, literal(", ")))
             .join(WorkOrderTech, WorkOrderTech.user_id == User.id)
             .where(WorkOrderTech.work_order_id == WorkOrder.id).scalar_subquery())
    return select(WorkOrder, Customer, names.label("techs")) \
        .join(Customer, Customer.id == WorkOrder.customer_id)


def _work_text(q, value):
    pat = like(value)
    return q.where(or_(WorkOrder.number.ilike(pat), WorkOrder.summary.ilike(pat),
                       Customer.name.ilike(pat)))


def _work_tech(q, value):
    if not value.isdigit():
        return q
    return q.where(exists().where(WorkOrderTech.work_order_id == WorkOrder.id,
                                  WorkOrderTech.user_id == int(value)))


def _mine(q, value):
    from flask_login import current_user
    return _work_tech(q, str(current_user.id)) if value == "1" else q


def _promised(row):
    wo = row[0]
    if wo.promised_date is None:
        return None
    late = wo.promised_date < local_today() and wo.status in OPEN_WO_STATUSES
    return badge(str(wo.promised_date), "danger") if late else wo.promised_date


WORK = Spec(
    page="work", title="Work orders", query=_work_query,
    columns=[
        Col("number", "WO", lambda r: Link(r[0].number, url_for("work.show", wo_id=r[0].id)),
            sort=WorkOrder.number, narrow=True),
        Col("customer", "Customer", lambda r: r[1].display_name, sort=func.lower(Customer.name),
            narrow=True),
        Col("summary", "Summary", lambda r: r[0].summary, sort=func.lower(WorkOrder.summary)),
        Col("kind", "Kind", lambda r: WO_KINDS.get(r[0].kind, r[0].kind), sort=WorkOrder.kind),
        Col("status", "Status", lambda r: badge(WO_STATUSES.get(r[0].status, r[0].status)),
            kind="badge", sort=WorkOrder.status, narrow=True),
        Col("priority", "Priority", lambda r: PRIORITIES.get(r[0].priority, r[0].priority),
            sort=func.array_position(literal(list(PRIORITY_ORDER)), WorkOrder.priority)),
        Col("promised", "Promised", _promised, kind="date", sort=WorkOrder.promised_date),
        Col("techs", "Techs", lambda r: r[2] or ""),
        Col("opened", "Opened", lambda r: local_today_of(r[0].created_at), kind="date",
            sort=WorkOrder.created_at),
    ],
    filters=[
        status_filter(WorkOrder.status, OPEN_WO_STATUSES, WO_STATUSES),
        choice_filter("kind", "Kind", WorkOrder.kind, WO_KINDS),
        choice_filter("priority", "Priority", WorkOrder.priority, PRIORITIES),
        Filter("tech", "Tech", _work_tech, techs),
        Filter("mine", "Whose", _mine, [("", "Everyone's"), ("1", "Mine")]),
        Filter("q", "WO, summary or customer", _work_text, kind="text"),
    ],
    sort=("opened", "desc"), tiebreak=WorkOrder.id.desc(),
    link=lambda r: url_for("work.show", wo_id=r[0].id),
    new=("New work order", "work.new", EDIT_ROLES),
    bulk=[("status", "Set status to", list(WO_STATUSES.items())),
          ("tech", "Add tech", lambda: techs()[1:])],
    bulk_endpoint="lists.bulk_work", bulk_roles=EDIT_ROLES,
)


def local_today_of(moment):
    from app.timeutil import shop_tz
    return moment.astimezone(shop_tz()).date() if moment else None


# --- estimates --------------------------------------------------------------------------------

def _estimate_text(q, value):
    pat = like(value)
    return q.where(or_(Estimate.number.ilike(pat), Estimate.summary.ilike(pat),
                       Customer.name.ilike(pat)))


ESTIMATES = Spec(
    page="estimates", title="Estimates",
    query=lambda: select(Estimate, Customer).join(Customer, Customer.id == Estimate.customer_id),
    columns=[
        Col("number", "Estimate", lambda r: Link(
            r[0].number if r[0].revision == 1 else f"{r[0].number} r{r[0].revision}",
            url_for("estimates.show", est_id=r[0].id)), sort=Estimate.number, narrow=True),
        Col("customer", "Customer", lambda r: r[1].display_name, sort=func.lower(Customer.name),
            narrow=True),
        Col("summary", "Summary", lambda r: r[0].summary or ""),
        Col("status", "Status", lambda r: badge(ESTIMATE_STATUSES.get(r[0].status, r[0].status)),
            kind="badge", sort=Estimate.status, narrow=True),
        Col("sent", "Sent", lambda r: local_today_of(r[0].sent_at), kind="date",
            sort=Estimate.sent_at),
        Col("valid", "Valid until", lambda r: r[0].valid_until, kind="date",
            sort=Estimate.valid_until),
        Col("nte", "Not to exceed", lambda r: r[0].not_to_exceed, kind="money",
            sort=Estimate.not_to_exceed),
        Col("created", "Created", lambda r: local_today_of(r[0].created_at), kind="date",
            sort=Estimate.created_at),
    ],
    filters=[
        status_filter(Estimate.status, OPEN_ESTIMATE, ESTIMATE_STATUSES),
        Filter("q", "Estimate, summary or customer", _estimate_text, kind="text"),
    ],
    sort=("created", "desc"), tiebreak=Estimate.id.desc(),
    link=lambda r: url_for("estimates.show", est_id=r[0].id),
    new=("New estimate", "estimates.new", EDIT_ROLES),
)


# --- customers --------------------------------------------------------------------------------

MACHINES = select(func.count(Asset.id)).where(Asset.customer_id == Customer.id) \
    .scalar_subquery()
OPEN_WORK = select(func.count(WorkOrder.id)).where(
    WorkOrder.customer_id == Customer.id, WorkOrder.status.in_(OPEN_WO_STATUSES)) \
    .scalar_subquery()


def _customer_query():
    return select(Customer, MACHINES.label("machines"), OPEN_WORK.label("open_work"))


def _customer_text(q, value):
    pat = like(value)
    terms = [Customer.name.ilike(pat), Customer.dba.ilike(pat), Customer.billing_email.ilike(pat)]
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) >= 3:
        terms.append(Customer.phone_digits.like(like(digits)))
    return q.where(or_(*terms))


def _active(column):
    def apply(q, value):
        return q.where(column.is_(value == "yes")) if value in ("yes", "no") else q
    return apply


CUSTOMERS = Spec(
    page="customers", title="Customers", query=_customer_query,
    columns=[
        Col("name", "Customer", lambda r: Link(r[0].display_name,
                                                url_for("customers.show", customer_id=r[0].id)),
            sort=func.lower(Customer.name), narrow=True),
        Col("kind", "Kind", lambda r: r[0].kind.title(), sort=Customer.kind),
        Col("phone", "Phone", lambda r: r[0].phone or "", narrow=True),
        Col("email", "Email", lambda r: r[0].billing_email or ""),
        Col("machines", "Machines", lambda r: r[1], kind="num", sort=MACHINES),
        Col("open", "Open work", lambda r: r[2], kind="num", sort=OPEN_WORK),
        Col("added", "Added", lambda r: local_today_of(r[0].created_at), kind="date",
            sort=Customer.created_at),
    ],
    filters=[
        Filter("active", "Active", _active(Customer.active),
               [("yes", "Active"), ("no", "Inactive"), ("", "Both")], default="yes"),
        Filter("kind", "Kind", lambda q, v: q.where(Customer.kind == v) if v else q,
               [("", "Any"), ("business", "Business"), ("person", "Person")]),
        Filter("q", "Name, phone or email", _customer_text, kind="text"),
    ],
    sort=("name", "asc"), tiebreak=Customer.id,
    link=lambda r: url_for("customers.show", customer_id=r[0].id),
    new=("New customer", "customers.new", EDIT_ROLES),
)


# --- assets -----------------------------------------------------------------------------------

def _asset_text(q, value):
    pat = like(value)
    return q.where(or_(Asset.tag.ilike(pat), Asset.name.ilike(pat), Asset.serial.ilike(pat),
                       Asset.model.ilike(pat), Customer.name.ilike(pat)))


def _in_shop_days(row):
    asset = row[0]
    if asset.status != "in_shop" or asset.in_shop_since is None:
        return None
    return (local_today() - local_today_of(asset.in_shop_since)).days


ASSETS = Spec(
    page="assets", title="Assets",
    query=lambda: select(Asset, Customer).join(Customer, Customer.id == Asset.customer_id),
    columns=[
        Col("tag", "Tag", lambda r: Link(r[0].tag, url_for("assets.show", asset_id=r[0].id)),
            sort=Asset.tag, narrow=True),
        Col("name", "Name", lambda r: r[0].name, sort=func.lower(Asset.name), narrow=True),
        Col("customer", "Customer", lambda r: r[1].display_name, sort=func.lower(Customer.name)),
        Col("kind", "Kind", lambda r: KINDS.get(r[0].kind, r[0].kind), sort=Asset.kind),
        Col("status", "Status", lambda r: badge(ASSET_STATUSES.get(r[0].status, r[0].status)),
            kind="badge", sort=Asset.status),
        Col("make", "Make and model", lambda r: " ".join(x for x in (r[0].manufacturer,
                                                                    r[0].model) if x)),
        Col("serial", "Serial", lambda r: r[0].serial or "", sort=Asset.serial),
        Col("days", "Days in shop", _in_shop_days, kind="num", sort=Asset.in_shop_since),
    ],
    filters=[
        status_filter(Asset.status, ACTIVE_ASSET, ASSET_STATUSES, open_label="Active"),
        choice_filter("kind", "Kind", Asset.kind, KINDS),
        Filter("q", "Tag, name, serial or customer", _asset_text, kind="text"),
    ],
    sort=("tag", "asc"), tiebreak=Asset.id,
    link=lambda r: url_for("assets.show", asset_id=r[0].id),
    new=("New asset", "assets.new", EDIT_ROLES),
)


# --- invoices ---------------------------------------------------------------------------------

def _invoice_text(q, value):
    pat = like(value)
    return q.where(or_(Invoice.number.ilike(pat), Customer.name.ilike(pat)))


def _overdue(q, value):
    return q.where(Invoice.status.in_(OPEN_INVOICE), Invoice.balance > 0,
                   Invoice.due_date < local_today()) if value == "yes" else q


ISSUED = {k: v for k, v in INVOICE_STATUSES.items() if k != "draft"}
INVOICES = Spec(
    page="invoices", title="Invoices",
    query=lambda: select(Invoice, Customer).join(Customer, Customer.id == Invoice.customer_id)
    .where(Invoice.status != "draft"),
    columns=[
        Col("number", "Invoice", lambda r: Link(r[0].number,
                                                 url_for("billing.show", invoice_id=r[0].id)),
            sort=Invoice.number, narrow=True),
        Col("customer", "Customer", lambda r: r[1].display_name, sort=func.lower(Customer.name),
            narrow=True),
        Col("issued", "Issued", lambda r: r[0].issue_date, kind="date", sort=Invoice.issue_date),
        Col("due", "Due", lambda r: badge(str(r[0].due_date), "danger")
            if r[0].status in OPEN_INVOICE and r[0].balance > 0 and r[0].due_date < local_today()
            else r[0].due_date, kind="date", sort=Invoice.due_date),
        Col("status", "Status", lambda r: badge(ISSUED.get(r[0].status, r[0].status)),
            kind="badge", sort=Invoice.status),
        Col("total", "Total", lambda r: r[0].total, kind="money", sort=Invoice.total),
        Col("balance", "Owed", lambda r: r[0].balance, kind="money", sort=Invoice.balance,
            narrow=True),
    ],
    filters=[
        status_filter(Invoice.status, OPEN_INVOICE, ISSUED),
        Filter("overdue", "Overdue", _overdue, [("", "Either"), ("yes", "Overdue only")]),
        Filter("from", "Issued from", date_from(Invoice.issue_date), kind="date"),
        Filter("to", "Issued to", date_to(Invoice.issue_date), kind="date"),
        Filter("q", "Invoice or customer", _invoice_text, kind="text"),
    ],
    sort=("issued", "desc"), tiebreak=Invoice.id.desc(),
    link=lambda r: url_for("billing.show", invoice_id=r[0].id),
)


# --- payments ---------------------------------------------------------------------------------

def _payment_text(q, value):
    pat = like(value)
    return q.where(or_(Payment.reference.ilike(pat), Customer.name.ilike(pat)))


PAYMENTS = Spec(
    page="payments", title="Payments",
    query=lambda: select(Payment, Customer).join(Customer, Customer.id == Payment.customer_id),
    columns=[
        Col("id", "Payment", lambda r: Link(f"#{r[0].id}", url_for("billing.payment",
                                                                   payment_id=r[0].id)),
            sort=Payment.id, narrow=True),
        Col("date", "Date", lambda r: r[0].received_on, kind="date", sort=Payment.received_on,
            narrow=True),
        Col("customer", "Customer", lambda r: r[1].display_name, sort=func.lower(Customer.name)),
        Col("kind", "Kind", lambda r: PAYMENT_KINDS.get(r[0].kind, r[0].kind), sort=Payment.kind),
        Col("method", "Method", lambda r: PAYMENT_METHODS.get(r[0].method, r[0].method),
            sort=Payment.method),
        Col("reference", "Reference", lambda r: r[0].reference or ""),
        Col("amount", "Amount", lambda r: r[0].amount, kind="money", sort=Payment.amount,
            narrow=True),
    ],
    filters=[
        choice_filter("kind", "Kind", Payment.kind, PAYMENT_KINDS),
        choice_filter("method", "Method", Payment.method, PAYMENT_METHODS),
        Filter("from", "From", date_from(Payment.received_on), kind="date"),
        Filter("to", "To", date_to(Payment.received_on), kind="date"),
        Filter("q", "Reference or customer", _payment_text, kind="text"),
    ],
    sort=("date", "desc"), tiebreak=Payment.id.desc(),
    link=lambda r: url_for("billing.payment", payment_id=r[0].id),
)


# --- parts ------------------------------------------------------------------------------------

ON_HAND = select(func.coalesce(func.sum(StockMove.qty), 0)) \
    .where(StockMove.part_id == Part.id).scalar_subquery()


def _parts_query():
    return select(Part, ON_HAND.label("on_hand"), Vendor.name.label("vendor")) \
        .outerjoin(Vendor, Vendor.id == Part.preferred_vendor_id)


def _part_text(q, value):
    pat = like(value)
    return q.where(or_(Part.sku.ilike(pat), Part.name.ilike(pat), Part.mpn.ilike(pat),
                       Part.category.ilike(pat), Part.bin.ilike(pat),
                       func.array_to_string(Part.equivalents, " ").ilike(pat)))


def _low(q, value):
    if value not in ("yes", "1"):
        return q
    return q.where(Part.track_stock.is_(True), Part.reorder_point.isnot(None),
                   ON_HAND <= Part.reorder_point)


def _on_hand(row):
    part, qty = row[0], row[1]
    if not part.track_stock:
        return None
    low = part.reorder_point is not None and qty <= part.reorder_point
    return badge(f"{qty.normalize():f} low", "warn") if low else qty


PARTS = Spec(
    page="parts", title="Parts", query=_parts_query,
    columns=[
        Col("sku", "SKU", lambda r: Link(r[0].sku, url_for("parts.show", part_id=r[0].id)),
            sort=func.lower(Part.sku), narrow=True),
        Col("name", "Name", lambda r: r[0].name, sort=func.lower(Part.name), narrow=True),
        Col("category", "Category", lambda r: r[0].category or "", sort=Part.category),
        Col("bin", "Bin", lambda r: r[0].bin or "", sort=Part.bin),
        Col("on_hand", "On hand", _on_hand, kind="qty", sort=ON_HAND),
        Col("reorder", "Reorder at", lambda r: r[0].reorder_point, kind="qty",
            sort=Part.reorder_point),
        Col("vendor", "Vendor", lambda r: r[2] or ""),
        Col("cost", "Cost", lambda r: r[0].default_cost, kind="cost", sort=Part.default_cost,
            roles=COST_ROLES),
    ],
    filters=[
        Filter("active", "Active", _active(Part.active),
               [("yes", "Active"), ("no", "Inactive"), ("", "Both")], default="yes"),
        Filter("low", "Low stock", _low, [("", "Any"), ("yes", "Low only")]),
        Filter("q", "SKU, name, MPN, equivalent or bin", _part_text, kind="text"),
    ],
    sort=("sku", "asc"), tiebreak=Part.id,
    link=lambda r: url_for("parts.show", part_id=r[0].id),
    new=("Add part", "parts.new", EDIT_ROLES),
)


# --- purchase orders --------------------------------------------------------------------------

PO_TOTAL = select(func.coalesce(func.sum(func.round(PoLine.qty * PoLine.unit_cost, 2)), 0)) \
    .where(PoLine.po_id == PurchaseOrder.id, PoLine.status != "cancelled").scalar_subquery() \
    + func.coalesce(PurchaseOrder.shipping, 0) + func.coalesce(PurchaseOrder.tax_paid, 0)


def _po_query():
    return select(PurchaseOrder, Vendor, PO_TOTAL.label("total")) \
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)


def _vendors():
    from app.extensions import db
    return [("", "Any")] + [(str(v.id), v.name) for v in db.session.scalars(
        select(Vendor).order_by(func.lower(Vendor.name)))]


ORDERS = Spec(
    page="orders", title="Purchase orders", query=_po_query,
    columns=[
        Col("number", "PO", lambda r: Link(r[0].number or f"Draft PO #{r[0].id}",
                                           url_for("purchasing.order", po_id=r[0].id)),
            sort=PurchaseOrder.number, narrow=True),
        Col("vendor", "Vendor", lambda r: r[1].name, sort=func.lower(Vendor.name), narrow=True),
        Col("status", "Status", lambda r: badge(PO_STATUSES.get(r[0].status, r[0].status)),
            kind="badge", sort=PurchaseOrder.status, narrow=True),
        Col("ordered", "Ordered", lambda r: r[0].ordered_on, kind="date",
            sort=PurchaseOrder.ordered_on),
        Col("ref", "Their order no.", lambda r: r[0].vendor_order_ref or ""),
        Col("total", "Total", lambda r: r[2], kind="money", sort=PO_TOTAL, roles=COST_ROLES),
    ],
    filters=[
        status_filter(PurchaseOrder.status, OPEN_PO, PO_STATUSES),
        Filter("vendor", "Vendor", lambda q, v: q.where(PurchaseOrder.vendor_id == int(v))
               if v.isdigit() else q, _vendors),
        Filter("from", "Ordered from", date_from(PurchaseOrder.ordered_on), kind="date"),
        Filter("to", "Ordered to", date_to(PurchaseOrder.ordered_on), kind="date"),
    ],
    sort=("ordered", "desc"), tiebreak=PurchaseOrder.id.desc(),
    link=lambda r: url_for("purchasing.order", po_id=r[0].id),
)

SPECS = {s.page: s for s in (WORK, ESTIMATES, CUSTOMERS, ASSETS, INVOICES, PAYMENTS, PARTS,
                             ORDERS)}
