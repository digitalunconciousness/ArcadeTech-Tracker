from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import COST_ROLES, requires_role
from app.books import service
from app.books.forms import EntryForm, ExpenseForm, FilingForm, TripForm, VoidForm
from app.books.service import BooksError
from app.customers.routes import flash_errors, get_or_404
from app.extensions import db
from app.models import (
    LEDGER_KINDS,
    Expense,
    ExpenseCategory,
    PartnerEntry,
    PurchaseOrder,
    Trip,
    User,
    WorkOrder,
)
from app.timeutil import local_today

bp = Blueprint("books", __name__)

# Owners keep the books (owner, 2026-10-12); viewers (the accountant, later) read them;
# techs see none of it.
BOOK_ROLES = ("owner",)


@bp.context_processor
def labels():
    return {"ledger_kinds": LEDGER_KINDS}


def keeps_books():
    return current_user.role in BOOK_ROLES


def attempt(action, ok=None):
    try:
        action()
        db.session.commit()
    except BooksError as e:
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


def period(default="month"):
    """start/end from the query string; by default this month (or this year)."""
    today = local_today()
    start = date(today.year, 1 if default == "year" else today.month, 1)
    end = today
    try:
        if request.args.get("start"):
            start = date.fromisoformat(request.args["start"])
        if request.args.get("end"):
            end = date.fromisoformat(request.args["end"])
    except ValueError:
        flash("Dates are YYYY-MM-DD.", "error")
    return start, end


def quarter_bounds(today):
    q = (today.month - 1) // 3
    start = date(today.year, q * 3 + 1, 1)
    end = date(today.year + (q == 3), 1 if q == 3 else q * 3 + 4, 1)
    return start, date.fromordinal(end.toordinal() - 1)


def category_choices(keep=None):
    cats = db.session.scalars(select(ExpenseCategory).where(
        or_(ExpenseCategory.active.is_(True), ExpenseCategory.id == keep))
        .order_by(ExpenseCategory.position, ExpenseCategory.name)).all()
    return [(None, "—")] + [(c.id, c.name) for c in cats]


def payer_choices(keep=None):
    return [(None, "Business account")] + [(u.id, f"{u.display_name}'s own money")
                                           for u in service.partners(include=[keep])]


def driver_choices(keep=None):
    users = db.session.scalars(select(User).where(or_(User.active.is_(True), User.id == keep))
                               .order_by(User.display_name)).all()
    return [(u.id, u.display_name) for u in users]


def wo_by_number(number):
    """The work order a typed number names, or None; raises BooksError if there's none."""
    if not number:
        return None
    wo = db.session.scalar(select(WorkOrder).where(WorkOrder.number == number))
    if wo is None:
        raise BooksError(f"There's no {number}.")
    return wo


@bp.route("/books")
@requires_role(*COST_ROLES)
def index():
    start, end = period()
    rows = service.expense_rows(start, end)
    month_trips = service.trips(start, end)
    year_start = date(end.year, 1, 1)
    return render_template(
        "books/index.html", start=start, end=end,
        owed=[(u, service.owed(u.id)) for u in service.partners()],
        spent=sum((r.amount for r in rows), service.ZERO),
        by_category=service.by_category(rows)[:6],
        miles=sum((t.miles for t, _ in month_trips), 0),
        mileage=sum((v for _, v in month_trips if v is not None), service.ZERO),
        year_spent=sum((r.amount for r in service.expense_rows(year_start, end)),
                       service.ZERO),
        unfiled=service.unfiled_tax(), keeps=keeps_books())


# --- expenses ---------------------------------------------------------------------------------

@bp.route("/books/expenses")
@requires_role(*COST_ROLES)
def expenses():
    start, end = period()
    category_id = request.args.get("category", type=int)
    rows = service.expense_rows(start, end, category_id)
    users = {u.id: u for u in db.session.scalars(select(User))}
    return render_template(
        "books/expenses.html", rows=rows, start=start, end=end, category_id=category_id,
        categories=category_choices()[1:], users=users, keeps=keeps_books(),
        total=sum((r.amount for r in rows), service.ZERO),
        by_category=service.by_category(rows))


def expense_form(expense=None):
    form = ExpenseForm(obj=expense)
    form.category_id.choices = category_choices(keep=expense.category_id if expense else None)
    form.paid_by_user_id.choices = payer_choices(keep=expense.paid_by_user_id if expense else None)
    return form


def save_from(form, expense):
    def go():
        wo = wo_by_number(form.wo.data)
        receipt = None
        if form.receipt.data and getattr(form.receipt.data, "filename", ""):
            receipt = service.store_receipt(form.receipt.data.stream)
        holder["expense"] = service.save_expense(
            expense, spent_on=form.spent_on.data, payee=form.payee.data,
            category_id=form.category_id.data, amount=form.amount.data,
            tax_included=form.tax_included.data, paid_by_user_id=form.paid_by_user_id.data,
            work_order_id=wo.id if wo else None, note=form.note.data, receipt=receipt)

    holder = {}
    return holder["expense"] if attempt(go, "Saved.") else None


@bp.route("/books/expenses/new", methods=["GET", "POST"])
@requires_role(*BOOK_ROLES)
def expense_new():
    form = expense_form()
    if request.method == "GET":
        form.spent_on.data = local_today()
        form.wo.data = request.args.get("wo")
    elif form.validate_on_submit():
        expense = save_from(form, None)
        if expense is not None:
            return redirect(url_for("books.expense", expense_id=expense.id))
    else:
        flash_errors(form)
    return render_template("books/expense_form.html", form=form, expense=None)


@bp.route("/books/expenses/<int:expense_id>")
@requires_role(*COST_ROLES)
def expense(expense_id):
    e = get_or_404(Expense, expense_id)
    return render_template(
        "books/expense.html", e=e, category=db.session.get(ExpenseCategory, e.category_id),
        payer=db.session.get(User, e.paid_by_user_id) if e.paid_by_user_id else None,
        wo=db.session.get(WorkOrder, e.work_order_id) if e.work_order_id else None,
        void_form=VoidForm(), keeps=keeps_books())


@bp.route("/books/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
@requires_role(*BOOK_ROLES)
def expense_edit(expense_id):
    e = get_or_404(Expense, expense_id)
    form = expense_form(e)
    if request.method == "GET" and e.work_order_id:
        form.wo.data = db.session.get(WorkOrder, e.work_order_id).number
    if form.validate_on_submit():
        if save_from(form, e) is not None:
            return redirect(url_for("books.expense", expense_id=e.id))
    else:
        flash_errors(form)
    return render_template("books/expense_form.html", form=form, expense=e)


@bp.route("/books/expenses/<int:expense_id>/void", methods=["POST"])
@requires_role(*BOOK_ROLES)
def expense_void(expense_id):
    e = get_or_404(Expense, expense_id)
    form = VoidForm()
    if form.validate_on_submit():
        attempt(lambda: service.void_expense(e, form.reason.data), "Voided.")
    else:
        flash_errors(form)
    return redirect(url_for("books.expense", expense_id=e.id))


@bp.route("/books/receipts/<sha256>")
@requires_role(*COST_ROLES)
def receipt(sha256):
    """A stored receipt: only one an expense or a PO points at, only for those who see
    the books."""
    row = db.session.execute(select(Expense.receipt_type).where(
        Expense.receipt_sha256 == sha256)).first() or db.session.execute(
        select(PurchaseOrder.receipt_type).where(PurchaseOrder.receipt_sha256 == sha256)).first()
    if row is None:
        abort(404)
    path = service.receipt_path(sha256, row[0])
    if not path.exists():
        abort(404)
    return send_file(path, mimetype=row[0], max_age=0)


# --- mileage ----------------------------------------------------------------------------------

@bp.route("/books/mileage")
@requires_role(*COST_ROLES)
def mileage():
    start, end = period(default="year")
    rows = service.trips(start, end)
    users = {u.id: u for u in db.session.scalars(select(User))}
    wos = {w.id: w for w in db.session.scalars(select(WorkOrder).where(
        WorkOrder.id.in_([t.work_order_id for t, _ in rows if t.work_order_id])))}
    return render_template(
        "books/mileage.html", rows=rows, start=start, end=end, users=users, wos=wos,
        miles=sum((t.miles for t, _ in rows), 0),
        value=sum((v for _, v in rows if v is not None), service.ZERO),
        unpriced=sum(1 for _, v in rows if v is None), rates=service.rates(),
        keeps=keeps_books(), void_form=VoidForm())


def trip_form(trip=None):
    form = TripForm(obj=trip)
    form.user_id.choices = driver_choices(keep=trip.user_id if trip else None)
    return form


def save_trip_from(form, trip):
    def go():
        wo = wo_by_number(form.wo.data)
        holder["trip"] = service.save_trip(
            trip, trip_on=form.trip_on.data, user_id=form.user_id.data, miles=form.miles.data,
            purpose=form.purpose.data, from_place=form.from_place.data,
            to_place=form.to_place.data, own_vehicle=form.own_vehicle.data,
            work_order_id=wo.id if wo else None)

    holder = {}
    return holder["trip"] if attempt(go, "Trip saved.") else None


@bp.route("/books/trips/new", methods=["GET", "POST"])
@requires_role(*BOOK_ROLES)
def trip_new():
    form = trip_form()
    if request.method == "GET":
        form.trip_on.data = local_today()
        form.user_id.data = current_user.id
        number = request.args.get("wo")
        wo = db.session.scalar(select(WorkOrder).where(WorkOrder.number == number)) \
            if number else None
        if wo is not None:
            form.wo.data = wo.number
            form.purpose.data = f"{wo.number}: {wo.summary}"[:200]
            if wo.site_id:
                from app.models import Site
                site = db.session.get(Site, wo.site_id)
                form.to_place.data = ", ".join(x for x in (site.name, site.city) if x)[:120]
    elif form.validate_on_submit():
        if save_trip_from(form, None) is not None:
            return redirect(url_for("books.mileage"))
    else:
        flash_errors(form)
    return render_template("books/trip_form.html", form=form, trip=None)


@bp.route("/books/trips/<int:trip_id>/edit", methods=["GET", "POST"])
@requires_role(*BOOK_ROLES)
def trip_edit(trip_id):
    trip = get_or_404(Trip, trip_id)
    form = trip_form(trip)
    if request.method == "GET" and trip.work_order_id:
        form.wo.data = db.session.get(WorkOrder, trip.work_order_id).number
    if form.validate_on_submit():
        if save_trip_from(form, trip) is not None:
            return redirect(url_for("books.mileage"))
    else:
        flash_errors(form)
    return render_template("books/trip_form.html", form=form, trip=trip)


@bp.route("/books/trips/<int:trip_id>/void", methods=["POST"])
@requires_role(*BOOK_ROLES)
def trip_void(trip_id):
    trip = get_or_404(Trip, trip_id)
    form = VoidForm()
    if form.validate_on_submit():
        attempt(lambda: service.void_trip(trip, form.reason.data), "Trip voided.")
    else:
        flash_errors(form)
    return redirect(url_for("books.mileage"))


# --- partners ---------------------------------------------------------------------------------

def entry_form():
    form = EntryForm(entry_on=local_today())
    form.user_id.choices = [(u.id, u.display_name) for u in service.partners()]
    return form


@bp.route("/books/partners")
@requires_role(*COST_ROLES)
def partners():
    people = service.partners()
    return render_template(
        "books/partners.html", rows=[(u, service.owed(u.id)) for u in people],
        recent=service.entries()[:20], users={u.id: u for u in people},
        form=entry_form() if keeps_books() and people else None)


@bp.route("/books/partners/<int:user_id>")
@requires_role(*COST_ROLES)
def partner(user_id):
    user = get_or_404(User, user_id)
    start, end = period(default="year")
    return render_template(
        "books/partner.html", user=user, owed=service.owed(user.id), start=start, end=end,
        entries=service.entries(user.id, include_voided=True),
        totals=service.ledger_totals(user.id, start, end), keeps=keeps_books(),
        void_form=VoidForm())


@bp.route("/books/ledger", methods=["POST"])
@requires_role(*BOOK_ROLES)
def entry_new():
    form = entry_form()
    if form.validate_on_submit():
        attempt(lambda: service.add_entry(user_id=form.user_id.data, kind=form.kind.data,
                                          amount=form.amount.data, entry_on=form.entry_on.data,
                                          note=form.note.data), "Recorded.")
    else:
        flash_errors(form)
    return redirect(url_for("books.partners"))


@bp.route("/books/ledger/<int:entry_id>/void", methods=["POST"])
@requires_role(*BOOK_ROLES)
def entry_void(entry_id):
    entry = get_or_404(PartnerEntry, entry_id)
    form = VoidForm()
    if form.validate_on_submit():
        attempt(lambda: service.void_entry(entry, form.reason.data), "Voided.")
    else:
        flash_errors(form)
    return redirect(url_for("books.partner", user_id=entry.user_id))


# --- sales tax --------------------------------------------------------------------------------

@bp.route("/books/sales-tax", methods=["GET", "POST"])
@requires_role(*COST_ROLES)
def sales_tax():
    default_start, default_end = quarter_bounds(local_today())
    start, end = default_start, default_end
    try:
        if request.args.get("start"):
            start = date.fromisoformat(request.args["start"])
        if request.args.get("end"):
            end = date.fromisoformat(request.args["end"])
    except ValueError:
        flash("Dates are YYYY-MM-DD.", "error")
    form = FilingForm(period_start=start, period_end=end)
    if request.method == "POST":
        if not keeps_books():
            abort(403)
        if form.validate_on_submit():
            def go():
                try:
                    service.record_filing(
                        period_start=form.period_start.data, period_end=form.period_end.data,
                        tax_due=form.tax_due.data, filed_on=form.filed_on.data,
                        paid_on=form.paid_on.data, confirmation=form.confirmation.data,
                        note=form.note.data)
                    db.session.flush()
                except IntegrityError:
                    db.session.rollback()
                    raise BooksError("A filing already covers part of that period.") from None

            if attempt(go, "Filing recorded."):
                return redirect(url_for("books.sales_tax", start=form.period_start.data,
                                        end=form.period_end.data))
        else:
            flash_errors(form)
    rows, totals = service.tax_summary(start, end)
    return render_template("books/sales_tax.html", rows=rows, totals=totals, start=start,
                           end=end, filings=service.filings(), form=form, keeps=keeps_books(),
                           unfiled=service.unfiled_tax())
