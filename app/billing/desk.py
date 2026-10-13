"""Money at the desk (Phase 9c), the billing half: invoicing many work orders in one go,
and statements for every customer who owes or has credit.

Bulk invoicing is the single-invoice issue, repeated: each work order's unbilled lines go
on one invoice through service.issue(), each in its own transaction, so a refusal partway
leaves the others issued and the numbers gapless (a refused one's number is never taken).
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from flask import (
    abort,
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import requires_role
from app.auth.forms import EmptyForm
from app.billing import docs, payments, service
from app.billing.routes import BILL_ROLES, READ_ROLES, bp
from app.billing.service import ZERO, InvoiceError
from app.extensions import db
from app.links import new_token, token_hash
from app.models import (
    OPEN_INVOICE_STATUSES,
    WO_STATUSES,
    CommLog,
    CreditMemo,
    Customer,
    DocLink,
    Invoice,
    Payment,
    PaymentAllocation,
    WorkOrder,
)
from app.timeutil import local_today, utcnow
from app.work import files
from app.work import service as work

BULK_LIMIT = 50                       # invoices per run: each renders two PDFs
BULK_STATUSES = ("completed", "ready")
STATEMENT_LINK_DAYS = 30


# --- bulk invoicing ---------------------------------------------------------------------------

@dataclass
class Candidate:
    """A work order that has something to bill, and what one invoice for all of it would
    be (or why it can't be issued)."""
    wo: WorkOrder
    customer: Customer
    line_ids: list = field(default_factory=list)
    draft: object = None
    error: str | None = None
    credit: Decimal = ZERO

    @property
    def due(self):
        return self.draft.total - self.credit if self.draft else ZERO


def candidates(statuses, text="", issue_date=None, limit=200):
    """Work orders in these statuses with unbilled lines, each worked out as one invoice on
    issue_date. Credit on account is shared out in list order, as issuing would apply it."""
    issue_date = issue_date or local_today()
    q = (select(WorkOrder, Customer).join(Customer, Customer.id == WorkOrder.customer_id)
         .where(WorkOrder.status.in_(statuses)))
    if text:
        pat = f"%{text}%"
        q = q.where(or_(WorkOrder.number.ilike(pat), WorkOrder.summary.ilike(pat),
                        Customer.name.ilike(pat), Customer.dba.ilike(pat)))
    credit_left = {}
    out = []
    for wo, customer in db.session.execute(q.order_by(func.lower(Customer.name), WorkOrder.id)):
        groups = service.unbilled(wo)
        if not groups:
            continue
        c = Candidate(wo, customer, [x.id for _, _, ls in groups for x in ls])
        waiting = work.awaiting_lines(wo)
        if waiting:
            c.error = ("The customer's part isn't here yet: "
                       + ", ".join(x.description for x in waiting[:3]) + ".")
        else:
            try:
                c.draft = service.work_out(wo, customer, c.line_ids, issue_date=issue_date,
                                           jurisdiction_id=service.default_jurisdiction_id(wo))
            except InvoiceError as e:
                c.error = str(e)
        if c.draft is not None:
            if customer.id not in credit_left:
                credit_left[customer.id] = payments.credit_available(customer.id)
            c.credit = min(c.draft.total, credit_left[customer.id])
            credit_left[customer.id] -= c.credit
        out.append(c)
        if len(out) >= limit:
            break
    return out


def issue_many(wo_ids, *, issue_date, backdate_reason=None):
    """Issue one invoice per work order, each in its own transaction. Returns (invoice
    ids issued, ["WO-…: why not"])."""
    issued, failed = [], []
    for wo_id in wo_ids:
        wo = db.session.get(WorkOrder, wo_id)
        if wo is None:
            continue
        number = wo.number
        try:
            if work.awaiting_lines(wo):
                raise InvoiceError("the customer's part isn't here yet.")
            line_ids = [x.id for _, _, ls in service.unbilled(wo) for x in ls]
            if not line_ids:
                raise InvoiceError("nothing left to bill (billed meanwhile?).")
            inv = service.issue(wo, line_ids, issue_date=issue_date,
                                jurisdiction_id=service.default_jurisdiction_id(wo),
                                backdate_reason=backdate_reason,
                                apply_credit=bool(payments.credit_available(wo.customer_id)))
            db.session.commit()
            issued.append(inv.id)
        except (InvoiceError, files.FileError) as e:
            db.session.rollback()
            failed.append(f"{number}: {e}")
        except IntegrityError:
            db.session.rollback()
            failed.append(f"{number}: something changed meanwhile; issue it on its own.")
    return issued, failed


def _statuses():
    chosen = [s for s in request.values.getlist("status") if s in WO_STATUSES
              and s != "cancelled"]
    return chosen or list(BULK_STATUSES)


def _date(raw, default):
    try:
        return date.fromisoformat(raw) if raw else default
    except ValueError:
        return default


@bp.route("/invoices/bulk", methods=["GET", "POST"])
@requires_role(*BILL_ROLES)
def bulk():
    """Invoice ready work: tick work orders, preview the invoices, issue them."""
    today = local_today()
    statuses, text = _statuses(), (request.values.get("q") or "").strip()[:100]
    issue_date = _date(request.values.get("issue_date"), today)
    reason = (request.form.get("backdate_reason") or "").strip()[:300] or None
    rows = candidates(statuses, text, issue_date)
    issuable = {c.wo.id for c in rows if c.draft is not None}
    if request.method == "GET":
        chosen = issuable
    else:
        if not EmptyForm().validate_on_submit():
            abort(400)
        chosen = {int(i) for i in request.form.getlist("wo") if i.isdigit()} & issuable
        problem = None
        if not chosen:
            problem = "Tick at least one work order that can be issued."
        elif len(chosen) > BULK_LIMIT:
            problem = f"That's {len(chosen)}: issue at most {BULK_LIMIT} at a time."
        elif issue_date > today:
            problem = "The issue date can't be in the future."
        elif issue_date < today and not reason:
            problem = "They're dated before today: say why."
        if problem:
            flash(problem, "error")
        elif request.form.get("action") == "issue":
            order = [c.wo.id for c in rows if c.wo.id in chosen]
            issued, failed = issue_many(order, issue_date=issue_date, backdate_reason=reason)
            for message in failed:
                flash(message, "error")
            if issued:
                flash(f"Issued {len(issued)} invoice{'s' if len(issued) != 1 else ''}.", "ok")
                return redirect(url_for("billing.bulk_done",
                                        ids=",".join(str(i) for i in issued)))
    picked = [c for c in rows if c.wo.id in chosen]
    totals = {"total": sum((c.draft.total for c in picked), ZERO),
              "credit": sum((c.credit for c in picked), ZERO),
              "due": sum((c.due for c in picked), ZERO)}
    return render_template("billing/bulk.html", rows=rows, chosen=chosen, statuses=statuses,
                           all_statuses=[(k, v) for k, v in WO_STATUSES.items()
                                         if k != "cancelled"],
                           q=text, issue_date=issue_date, reason=reason or "", today=today,
                           totals=totals, picked=picked, limit=BULK_LIMIT,
                           wo_statuses=WO_STATUSES,
                           previewing=request.method == "POST")


def _ids(raw):
    return [int(i) for i in (raw or "").split(",") if i.isdigit()][:BULK_LIMIT * 4]


@bp.route("/invoices/bulk/done")
@requires_role(*BILL_ROLES)
def bulk_done():
    ids = _ids(request.args.get("ids"))
    rows = db.session.execute(
        select(Invoice, Customer).join(Customer, Customer.id == Invoice.customer_id)
        .where(Invoice.id.in_(ids)).order_by(Invoice.number)).all()
    return render_template("billing/bulk_done.html", rows=rows,
                           ids=",".join(str(inv.id) for inv, _ in rows))


@bp.route("/invoices/bulk/links", methods=["POST"])
@requires_role(*BILL_ROLES)
def bulk_links():
    """A customer link for each invoice just issued; shown once, like a single one."""
    if not EmptyForm().validate_on_submit():
        abort(400)
    out = []
    base = current_app.config["SHOP_BASE_URL"]
    for inv, customer in db.session.execute(
            select(Invoice, Customer).join(Customer, Customer.id == Invoice.customer_id)
            .where(Invoice.id.in_(_ids(request.form.get("ids")))).order_by(Invoice.number)):
        try:
            token = service.new_link(inv)
        except InvoiceError:
            continue
        out.append((inv, customer, base + url_for("public.estimate", token=token)))
    db.session.commit()
    return render_template("billing/bulk_links.html", rows=out,
                           days=service.INVOICE_LINK_DAYS)


# --- statements -------------------------------------------------------------------------------

def statement_customers(overdue_only=False):
    """[(customer, statement)] for everyone who owes or has credit, most owed first."""
    owing = select(Invoice.customer_id).where(Invoice.status.in_(OPEN_INVOICE_STATUSES),
                                              Invoice.balance > 0)
    paid = select(Payment.customer_id.label("cid"), Payment.amount.label("amount"))
    credited = select(CreditMemo.customer_id.label("cid"), CreditMemo.total.label("amount")) \
        .where(CreditMemo.status == "issued")
    applied = select(Invoice.customer_id.label("cid"), (-PaymentAllocation.amount).label("amount")) \
        .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
    money = paid.union_all(credited, applied).subquery()
    with_credit = select(money.c.cid).group_by(money.c.cid).having(func.sum(money.c.amount) > 0)
    customers = db.session.scalars(select(Customer).where(or_(
        Customer.id.in_(owing), Customer.id.in_(with_credit)))
        .order_by(func.lower(Customer.name))).all()
    today = local_today()
    out = []
    for customer in customers:
        st = payments.statement(customer, today)
        if not st["owed"] and not st["credit"]:
            continue
        st["oldest_late"] = max((r["late"] for r in st["rows"]), default=0)
        if overdue_only and not st["oldest_late"]:
            continue
        out.append((customer, st))
    return sorted(out, key=lambda pair: pair[1]["due"], reverse=True)


def new_statement_link(customer):
    token = new_token()
    db.session.add(DocLink(customer_id=customer.id, token_sha256=token_hash(token),
                           expires_at=utcnow() + timedelta(days=STATEMENT_LINK_DAYS)))
    db.session.flush()
    return token


@bp.route("/statements", methods=["GET", "POST"])
@requires_role(*READ_ROLES)
def statements():
    """Everyone who owes or has credit: one PDF for those ticked, or a link each."""
    overdue = request.values.get("overdue") == "1"
    rows = statement_customers(overdue)
    if request.method == "POST":
        if not EmptyForm().validate_on_submit():
            abort(400)
        chosen = {int(i) for i in request.form.getlist("c") if i.isdigit()}
        picked = [c for c, _ in rows if c.id in chosen]
        if not picked:
            flash("Tick at least one customer.", "error")
        elif request.form.get("action") == "links":
            if current_user.role not in BILL_ROLES:
                abort(403)
            base = current_app.config["SHOP_BASE_URL"]
            made = []
            for customer in picked:
                url = base + url_for("public.estimate", token=new_statement_link(customer))
                db.session.add(CommLog(customer_id=customer.id, kind="note",
                                       summary="Statement link made (good for "
                                               f"{STATEMENT_LINK_DAYS} days)."))
                made.append((customer, url))
            db.session.commit()
            due = {c.id: st["due"] for c, st in rows}
            return render_template("billing/statement_links.html", days=STATEMENT_LINK_DAYS,
                                   rows=[(c, due[c.id], url) for c, url in made])
        else:
            response = make_response(docs.statements_pdf(picked))
            response.headers["Content-Type"] = "application/pdf"
            response.headers["Content-Disposition"] = \
                f'attachment; filename="statements-{local_today()}.pdf"'
            return response
    totals = {"owed": sum((st["owed"] for _, st in rows), ZERO),
              "credit": sum((st["credit"] for _, st in rows), ZERO)}
    return render_template("billing/statements.html", rows=rows, overdue=overdue,
                           totals=totals, can_bill=current_user.role in BILL_ROLES,
                           days=STATEMENT_LINK_DAYS)
