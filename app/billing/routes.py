"""Invoices, payments and credit memos. Owners issue, void, send and record money; viewers
(the accountant) read; techs never see them (owner, 2026-10-12)."""

from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import COST_ROLES, requires_role
from app.auth.forms import EmptyForm
from app.billing import docs, payments, service
from app.billing.forms import (
    ApplyForm,
    CreditMemoForm,
    CustomerPaymentForm,
    IssueForm,
    PaymentForm,
    RefundForm,
    ReverseForm,
    VoidForm,
)
from app.billing.service import InvoiceError
from app.customers.routes import flash_errors, get_or_404
from app.extensions import db
from app.formatting import money, rate_pct
from app.lists import desk_list
from app.models import (
    INVOICE_STATUSES,
    OPEN_INVOICE_STATUSES,
    PAYMENT_KINDS,
    PAYMENT_METHODS,
    PAYMENT_TERMS,
    TAX_STATUSES,
    CreditMemo,
    Customer,
    DocLink,
    Estimate,
    Invoice,
    Payment,
    TaxJurisdiction,
    User,
    WorkOrder,
)
from app.timeutil import local_today, utcnow
from app.work import files
from app.work.service import line_amount

bp = Blueprint("billing", __name__)

READ_ROLES = COST_ROLES   # owner, viewer
BILL_ROLES = ("owner",)


@bp.context_processor
def labels():
    return {"inv_statuses": INVOICE_STATUSES, "tax_statuses": TAX_STATUSES,
            "payment_terms": PAYMENT_TERMS, "methods": PAYMENT_METHODS, "kinds": PAYMENT_KINDS}


def attempt(action, ok=None):
    try:
        action()
        db.session.commit()
    except (InvoiceError, files.FileError) as e:
        db.session.rollback()
        flash(str(e), "error")
        return False
    except IntegrityError:
        db.session.rollback()
        flash("Something changed while you were working. Reload and try again.", "error")
        return False
    if ok:
        flash(ok, "ok")
    return True


def load(invoice_id):
    inv = get_or_404(Invoice, invoice_id)
    return inv, db.session.get(Customer, inv.customer_id)


# --- list ------------------------------------------------------------------------------------

@bp.route("/invoices")
@requires_role(*READ_ROLES)
def index():
    power = desk_list("invoices")
    if isinstance(power, Response):
        return power
    show_all = request.args.get("all") == "1"
    q = select(Invoice, Customer).join(Customer, Customer.id == Invoice.customer_id) \
        .where(Invoice.status != "draft")
    if not show_all:
        q = q.where(Invoice.status.in_(OPEN_INVOICE_STATUSES))
    rows = db.session.execute(q.order_by(Invoice.issue_date.desc(), Invoice.id.desc())
                              .limit(500)).all()
    today = local_today()
    owed = sum((inv.balance for inv, _ in rows if inv.status in OPEN_INVOICE_STATUSES),
               service.ZERO)
    return render_template("billing/index.html", power=power, rows=rows, show_all=show_all, owed=owed,
                           overdue={inv.id for inv, _ in rows if service.is_overdue(inv, today)})


# --- issuing ---------------------------------------------------------------------------------

def jurisdiction_choices(on=None):
    rows = db.session.scalars(select(TaxJurisdiction).order_by(
        TaxJurisdiction.name, TaxJurisdiction.effective_from.desc())).all()
    out = [(None, "—")]
    for j in rows:
        if on is not None and not service.in_effect(j, on):
            continue
        until = f" until {j.effective_to}" if j.effective_to else ""
        out.append((j.id, f"{j.name} · {rate_pct(j.combined_rate)} "
                          f"(from {j.effective_from}{until})"))
    return out


@bp.route("/work/<int:wo_id>/invoice", methods=["GET", "POST"])
@requires_role(*BILL_ROLES)
def issue(wo_id):
    wo = get_or_404(WorkOrder, wo_id)
    customer = db.session.get(Customer, wo.customer_id)
    groups = service.unbilled(wo)
    form = IssueForm()
    form.lines.choices = [(x.id, x.description) for _, _, ls in groups for x in ls]
    form.jurisdiction_id.choices = jurisdiction_choices()
    if request.method == "GET":
        form.lines.data = [x.id for _, _, ls in groups for x in ls]
        form.issue_date.data = local_today()
        form.jurisdiction_id.data = service.default_jurisdiction_id(wo)
    credit = payments.credit_available(customer.id)
    draft, error = None, None
    if form.validate_on_submit() or request.method == "GET":
        try:
            draft = service.work_out(wo, customer, form.lines.data or [],
                                     issue_date=form.issue_date.data,
                                     jurisdiction_id=form.jurisdiction_id.data) if groups else None
        except InvoiceError as e:
            error = str(e)
        if request.method == "POST" and request.form.get("action") == "issue" and not error:
            holder = {}

            def go():
                holder["inv"] = service.issue(
                    wo, form.lines.data, issue_date=form.issue_date.data,
                    jurisdiction_id=form.jurisdiction_id.data,
                    customer_note=form.customer_note.data, internal_note=form.internal_note.data,
                    backdate_reason=form.backdate_reason.data,
                    apply_credit=bool(credit) and form.apply_credit.data)

            if attempt(go):
                flash(f"Issued {holder['inv'].number}.", "ok")
                return redirect(url_for("billing.show", invoice_id=holder["inv"].id))
    else:
        flash_errors(form)
    return render_template("billing/issue.html", wo=wo, customer=customer, groups=groups,
                           form=form, draft=draft, error=error, amount=line_amount,
                           selected=set(form.lines.data or []), credit=credit)


# --- one invoice -----------------------------------------------------------------------------

@bp.route("/invoices/<int:invoice_id>")
@requires_role(*READ_ROLES)
def show(invoice_id):
    inv, customer = load(invoice_id)
    lines = service.lines_of(inv)
    links = db.session.scalars(select(DocLink).where(DocLink.invoice_id == inv.id)
                               .order_by(DocLink.id.desc())).all()
    return render_template(
        "billing/show.html", inv=inv, customer=customer, groups=service.grouped(lines),
        costs=service.costs(lines), links=links, overdue=service.is_overdue(inv),
        exposure=service.untaxed_exposure(inv),
        wo=db.session.get(WorkOrder, inv.work_order_id) if inv.work_order_id else None,
        voided_by=db.session.get(User, inv.voided_by) if inv.voided_by else None,
        void_form=VoidForm(), can_bill=current_user.role in BILL_ROLES,
        applied=payments.applied_to(inv), credit=payments.credit_available(customer.id),
        pay_form=PaymentForm(amount=inv.balance or None, received_on=local_today()),
        credit_memos=db.session.scalars(select(CreditMemo).where(
            CreditMemo.invoice_id == inv.id, CreditMemo.status == "issued")
            .order_by(CreditMemo.id)).all(),
        creditable=payments.creditable(inv) if inv.status not in ("draft", "void") else 0)


def frozen_pdf(sha256, filename):
    if not sha256:
        abort(404)
    path = files.path_for(sha256, ".pdf")
    if not path.exists():
        current_app.logger.error("frozen PDF missing from the file store: %s", sha256)
        abort(404)
    return send_file(path, mimetype="application/pdf", download_name=filename,
                     as_attachment=False, max_age=0)


@bp.route("/invoices/<int:invoice_id>/invoice.pdf")
@requires_role(*READ_ROLES)
def pdf(invoice_id):
    inv, _ = load(invoice_id)
    return frozen_pdf(inv.pdf_sha256, f"{inv.number}.pdf")


@bp.route("/invoices/<int:invoice_id>/service-report.pdf")
@requires_role(*READ_ROLES)
def report_pdf(invoice_id):
    inv, _ = load(invoice_id)
    return frozen_pdf(inv.report_sha256, f"{inv.number}-service-report.pdf")


@bp.route("/invoices/<int:invoice_id>/void", methods=["POST"])
@requires_role(*BILL_ROLES)
def void(invoice_id):
    inv, _ = load(invoice_id)
    form = VoidForm()
    if form.validate_on_submit():
        attempt(lambda: service.void(inv, reason=form.reason.data, user_id=current_user.id),
                f"{inv.number} is void. Its lines can be billed again.")
    else:
        flash_errors(form)
    return redirect(url_for("billing.show", invoice_id=inv.id))


@bp.route("/invoices/<int:invoice_id>/send", methods=["POST"])
@requires_role(*BILL_ROLES)
def send(invoice_id):
    inv, customer = load(invoice_id)
    holder = {}
    if EmptyForm().validate_on_submit() and \
            attempt(lambda: holder.update(token=service.new_link(inv))):
        url = current_app.config["SHOP_BASE_URL"] + url_for("public.estimate",
                                                             token=holder["token"])
        return render_template("billing/link.html", inv=inv, customer=customer, url=url,
                               days=service.INVOICE_LINK_DAYS)
    return redirect(url_for("billing.show", invoice_id=inv.id))


@bp.route("/invoices/links/<int:link_id>/revoke", methods=["POST"])
@requires_role(*BILL_ROLES)
def revoke(link_id):
    link = get_or_404(DocLink, link_id)
    if link.invoice_id is None:
        abort(404)
    if EmptyForm().validate_on_submit():
        def go():
            if link.revoked_at is None:
                link.revoked_at = utcnow()

        attempt(go, "Link withdrawn: it no longer opens.")
    return redirect(url_for("billing.show", invoice_id=link.invoice_id) + "#links")


# --- money in --------------------------------------------------------------------------------

def parse_money(raw):
    """A typed amount ("120.50") as Decimal; None when blank or not dollars and cents."""
    raw = (raw or "").strip().replace("$", "").replace(",", "")
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0 or value.as_tuple().exponent < -2:
        return None
    return value


@bp.route("/invoices/<int:invoice_id>/payment", methods=["POST"])
@requires_role(*BILL_ROLES)
def pay(invoice_id):
    """A payment against this invoice; anything over its balance is credit on account."""
    inv, customer = load(invoice_id)
    form = PaymentForm()
    if inv.status not in OPEN_INVOICE_STATUSES:
        flash(f"{inv.number} is {inv.status.replace('_', ' ')}: there's nothing to pay on it. "
              "Record other money on the customer's page.", "error")
    elif form.validate_on_submit():
        applied = min(form.amount.data, inv.balance)

        def go():
            payments.record_payment(customer, amount=form.amount.data, method=form.method.data,
                                    received_on=form.received_on.data,
                                    reference=form.reference.data, note=form.note.data,
                                    applied={inv.id: applied})

        extra = form.amount.data - applied
        attempt(go, f"Recorded {money(form.amount.data)}."
                + (f" {money(extra)} is credit on the customer's account." if extra else ""))
    else:
        flash_errors(form)
    return redirect(url_for("billing.show", invoice_id=inv.id) + "#money")


@bp.route("/invoices/<int:invoice_id>/apply-credit", methods=["POST"])
@requires_role(*BILL_ROLES)
def apply_credit(invoice_id):
    inv, _ = load(invoice_id)
    holder = {}
    if EmptyForm().validate_on_submit() and \
            attempt(lambda: holder.update(amount=payments.apply_credit(inv))):
        flash(f"Applied {money(holder['amount'])} of credit.", "ok")
    return redirect(url_for("billing.show", invoice_id=inv.id) + "#money")


@bp.route("/customers/<int:customer_id>/payment", methods=["GET", "POST"])
@requires_role(*BILL_ROLES)
def customer_payment(customer_id):
    """A payment from the customer, split over their open invoices (oldest first to start
    with), the rest left as credit; or a deposit on an estimate."""
    customer = get_or_404(Customer, customer_id)
    invoices = payments.open_invoices(customer.id)
    estimates = db.session.scalars(select(Estimate).where(
        Estimate.customer_id == customer.id,
        Estimate.status.in_(("draft", "sent", "approved", "converted")))
        .order_by(Estimate.id.desc()).limit(50)).all()
    form = CustomerPaymentForm()
    form.estimate_id.choices = [(None, "—")] + [
        (e.id, f"{e.number}{f' r{e.revision}' if e.revision > 1 else ''} · {e.summary}")
        for e in estimates]
    if request.method == "GET":
        form.received_on.data = local_today()
        form.estimate_id.data = request.args.get("estimate", type=int)
        form.amount.data = parse_money(request.args.get("amount"))
    filling = request.form.get("action") == "fill"
    if form.validate_on_submit() and not filling:
        applied, bad = {}, False
        for inv in invoices:
            raw = request.form.get(f"apply-{inv.id}")
            value = parse_money(raw)
            if value is None and (raw or "").strip():
                bad = True
            applied[inv.id] = value or service.ZERO
        if bad:
            flash("Amounts to apply: dollars and cents, like 120.50.", "error")
        else:
            holder = {}

            def go():
                holder["p"] = payments.record_payment(
                    customer, amount=form.amount.data, method=form.method.data,
                    received_on=form.received_on.data, reference=form.reference.data,
                    note=form.note.data, applied=applied, estimate_id=form.estimate_id.data)

            if attempt(go, f"Recorded {money(form.amount.data)}."):
                return redirect(url_for("billing.payment", payment_id=holder["p"].id))
    elif not filling:
        flash_errors(form)
    suggested = payments.spread(customer.id, form.amount.data) \
        if form.amount.data and not form.estimate_id.data else {}
    posted = {inv.id: request.form.get(f"apply-{inv.id}") for inv in invoices} \
        if request.method == "POST" and not filling else None
    return render_template("billing/customer_payment.html", customer=customer, form=form,
                           invoices=invoices, suggested=suggested, posted=posted)


@bp.route("/payments")
@requires_role(*READ_ROLES)
def payment_list():
    power = desk_list("payments")
    if isinstance(power, Response):
        return power
    rows = db.session.execute(select(Payment, Customer).join(
        Customer, Customer.id == Payment.customer_id)
        .order_by(Payment.received_on.desc(), Payment.id.desc()).limit(300)).all()
    return render_template("billing/payments.html", power=power, rows=rows)


def load_payment(payment_id):
    p = get_or_404(Payment, payment_id)
    return p, db.session.get(Customer, p.customer_id)


@bp.route("/payments/<int:payment_id>")
@requires_role(*READ_ROLES)
def payment(payment_id):
    p, customer = load_payment(payment_id)
    left = payments.source_left(p) if p.kind == "receipt" else service.ZERO
    reversal = payments.reversal_of(p)
    apply_form = ApplyForm()
    apply_form.invoice_id.choices = [(inv.id, f"{inv.number} · {money(inv.balance)} owed")
                                     for inv in payments.open_invoices(customer.id)]
    return render_template(
        "billing/payment.html", p=p, customer=customer, left=left, reversal=reversal,
        refunds=payments.refunds_of(p) if p.kind == "receipt" else [],
        applications=payments.applications(p),
        original=db.session.get(Payment, p.reverses_payment_id or p.refund_of_payment_id)
        if (p.reverses_payment_id or p.refund_of_payment_id) else None,
        refunded_cm=db.session.get(CreditMemo, p.refund_of_credit_memo_id)
        if p.refund_of_credit_memo_id else None,
        estimate=db.session.get(Estimate, p.estimate_id) if p.estimate_id else None,
        reverse_form=ReverseForm(received_on=local_today()),
        refund_form=RefundForm(amount=left or None), apply_form=apply_form,
        can_bill=current_user.role in BILL_ROLES)


@bp.route("/payments/<int:payment_id>/receipt.pdf")
@requires_role(*READ_ROLES)
def receipt_pdf(payment_id):
    p, customer = load_payment(payment_id)
    from app.work.routes import pdf_response
    return pdf_response(docs.receipt_pdf(p, customer), f"payment-{p.id}.pdf")


@bp.route("/payments/<int:payment_id>/reverse", methods=["POST"])
@requires_role(*BILL_ROLES)
def reverse(payment_id):
    p, _ = load_payment(payment_id)
    form = ReverseForm()
    if form.validate_on_submit():
        attempt(lambda: payments.reverse(p, reason=form.reason.data,
                                         received_on=form.received_on.data),
                f"Payment #{p.id} reversed: what it paid is owed again.")
    else:
        flash_errors(form)
    return redirect(url_for("billing.payment", payment_id=p.id))


def refund_from(obj, back):
    form = RefundForm()
    if form.validate_on_submit():
        attempt(lambda: payments.refund(obj, amount=form.amount.data, method=form.method.data,
                                        reason=form.reason.data, reference=form.reference.data),
                f"Refund of {money(form.amount.data)} recorded.")
    else:
        flash_errors(form)
    return redirect(back)


def apply_from(obj, back):
    form = ApplyForm()
    form.invoice_id.choices = [(inv.id, inv.number)
                               for inv in payments.open_invoices(obj.customer_id)]
    if form.validate_on_submit():
        holder = {}
        inv = db.session.get(Invoice, form.invoice_id.data)
        if attempt(lambda: holder.update(v=payments.apply_source(obj, inv, form.amount.data))):
            flash(f"Applied {money(holder['v'])} to {inv.number}.", "ok")
    else:
        flash_errors(form)
    return redirect(back)


@bp.route("/payments/<int:payment_id>/refund", methods=["POST"])
@requires_role(*BILL_ROLES)
def refund_payment(payment_id):
    p, _ = load_payment(payment_id)
    return refund_from(p, url_for("billing.payment", payment_id=p.id))


@bp.route("/payments/<int:payment_id>/apply", methods=["POST"])
@requires_role(*BILL_ROLES)
def apply_payment(payment_id):
    p, _ = load_payment(payment_id)
    return apply_from(p, url_for("billing.payment", payment_id=p.id))


# --- credit memos ----------------------------------------------------------------------------

@bp.route("/invoices/<int:invoice_id>/credit-memo", methods=["GET", "POST"])
@requires_role(*BILL_ROLES)
def credit_memo_new(invoice_id):
    inv, customer = load(invoice_id)
    if inv.status in ("draft", "void"):
        abort(404)
    form = CreditMemoForm()
    if form.validate_on_submit():
        lines = [(row.form.description.data, row.form.amount.data, row.form.taxable.data)
                 for row in form.lines]
        holder = {}

        def go():
            holder["cm"] = payments.issue_credit_memo(inv, reason=form.reason.data, lines=lines)

        if attempt(go):
            flash(f"Issued {holder['cm'].number}.", "ok")
            return redirect(url_for("billing.credit_memo", cm_id=holder["cm"].id))
    else:
        flash_errors(form)
    return render_template("billing/credit_memo_form.html", inv=inv, customer=customer,
                           form=form, creditable=payments.creditable(inv))


def load_cm(cm_id):
    cm = get_or_404(CreditMemo, cm_id)
    return cm, db.session.get(Invoice, cm.invoice_id), db.session.get(Customer, cm.customer_id)


@bp.route("/credit-memos/<int:cm_id>")
@requires_role(*READ_ROLES)
def credit_memo(cm_id):
    cm, inv, customer = load_cm(cm_id)
    left = payments.source_left(cm)
    apply_form = ApplyForm()
    apply_form.invoice_id.choices = [(i.id, f"{i.number} · {money(i.balance)} owed")
                                     for i in payments.open_invoices(customer.id)]
    return render_template("billing/credit_memo.html", cm=cm, inv=inv, customer=customer,
                           lines=payments.credit_memo_lines(cm), left=left,
                           applications=payments.applications(cm),
                           refunds=payments.refunds_of(cm), refund_form=RefundForm(
                               amount=left or None), apply_form=apply_form,
                           can_bill=current_user.role in BILL_ROLES)


@bp.route("/credit-memos/<int:cm_id>/credit-memo.pdf")
@requires_role(*READ_ROLES)
def credit_memo_pdf(cm_id):
    cm, _, _ = load_cm(cm_id)
    return frozen_pdf(cm.pdf_sha256, f"{cm.number}.pdf")


@bp.route("/credit-memos/<int:cm_id>/refund", methods=["POST"])
@requires_role(*BILL_ROLES)
def refund_credit_memo(cm_id):
    cm, _, _ = load_cm(cm_id)
    return refund_from(cm, url_for("billing.credit_memo", cm_id=cm.id))


@bp.route("/credit-memos/<int:cm_id>/apply", methods=["POST"])
@requires_role(*BILL_ROLES)
def apply_credit_memo(cm_id):
    cm, _, _ = load_cm(cm_id)
    return apply_from(cm, url_for("billing.credit_memo", cm_id=cm.id))


# --- statements ------------------------------------------------------------------------------

@bp.route("/customers/<int:customer_id>/statement.pdf")
@requires_role(*READ_ROLES)
def statement_pdf(customer_id):
    customer = get_or_404(Customer, customer_id)
    from app.work.routes import pdf_response
    return pdf_response(docs.statement_pdf(customer),
                        f"statement-{local_today()}.pdf")
