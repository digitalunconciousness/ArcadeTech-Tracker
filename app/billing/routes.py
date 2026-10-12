"""Invoices. Owners issue, void and send them; viewers (the accountant) read them; techs
never see them (owner, 2026-10-12)."""

from flask import (
    Blueprint,
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
from app.billing import service
from app.billing.forms import IssueForm, VoidForm
from app.billing.service import InvoiceError
from app.customers.routes import flash_errors, get_or_404
from app.extensions import db
from app.formatting import rate_pct
from app.models import (
    INVOICE_STATUSES,
    OPEN_INVOICE_STATUSES,
    PAYMENT_TERMS,
    TAX_STATUSES,
    Customer,
    DocLink,
    Invoice,
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
            "payment_terms": PAYMENT_TERMS}


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
    return render_template("billing/index.html", rows=rows, show_all=show_all, owed=owed,
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
                    backdate_reason=form.backdate_reason.data)

            if attempt(go):
                flash(f"Issued {holder['inv'].number}.", "ok")
                return redirect(url_for("billing.show", invoice_id=holder["inv"].id))
    else:
        flash_errors(form)
    return render_template("billing/issue.html", wo=wo, customer=customer, groups=groups,
                           form=form, draft=draft, error=error, amount=line_amount,
                           selected=set(form.lines.data or []))


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
        void_form=VoidForm(), can_bill=current_user.role in BILL_ROLES)


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
