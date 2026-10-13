"""Money → Bank (Phase 9c): the shop's accounts, their imported lines, matching, and
reconciling. The owner does it all; viewers read."""

from datetime import timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import COST_ROLES, requires_role
from app.auth.forms import EmptyForm
from app.bank import service
from app.bank.forms import AccountForm, ReconcileForm
from app.bank.service import BankError
from app.customers.routes import flash_errors, get_or_404
from app.extensions import db
from app.models import (
    BANK_KINDS,
    IGNORE_REASONS,
    MATCH_TARGETS,
    BankAccount,
    BankImport,
    BankLine,
    BankMatch,
    BankReconciliation,
    ExpenseCategory,
)
from app.timeutil import local_today
from app.work import files

bp = Blueprint("bank", __name__, url_prefix="/bank")
READ = COST_ROLES
WRITE = ("owner",)
TARGET_LABELS = dict(MATCH_TARGETS)


def attempt(action, ok=None):
    try:
        result = action()
        db.session.commit()
    except (BankError, files.FileError) as e:
        db.session.rollback()
        flash(str(e), "error")
        return False
    except IntegrityError:
        db.session.rollback()
        flash("Something changed while you were working. Reload and try again.", "error")
        return False
    if ok:
        flash(ok if isinstance(ok, str) else ok(result), "ok")
    return True


def csrf_ok():
    if not EmptyForm().validate_on_submit():
        abort(400)


# --- accounts ---------------------------------------------------------------------------------

@bp.route("/", methods=["GET", "POST"])
@requires_role(*READ)
def index():
    form = AccountForm(opening_on=local_today().replace(day=1))
    if request.method == "POST":
        if current_user.role not in WRITE:
            abort(403)
        if form.validate_on_submit():
            account = BankAccount(name=form.name.data, kind=form.kind.data,
                                  last4=form.last4.data, opening_on=form.opening_on.data,
                                  opening_balance=form.opening_balance.data, active=True)
            if attempt(lambda: db.session.add(account), f"Added {form.name.data}."):
                return redirect(url_for("bank.account", account_id=account.id))
        else:
            flash_errors(form)
    accounts = db.session.scalars(select(BankAccount).order_by(BankAccount.active.desc(),
                                                               BankAccount.name)).all()
    rows = []
    for a in accounts:
        open_count = db.session.scalar(select(db.func.count(BankLine.id)).where(
            BankLine.bank_account_id == a.id, BankLine.reconciliation_id.is_(None)))
        rows.append((a, service.balance(a), service.last_reconciliation(a), open_count))
    return render_template("bank/index.html", rows=rows, form=form, kinds=BANK_KINDS,
                           can_edit=current_user.role in WRITE)


def load(account_id):
    return get_or_404(BankAccount, account_id)


@bp.route("/<int:account_id>/edit", methods=["GET", "POST"])
@requires_role(*WRITE)
def edit(account_id):
    account = load(account_id)
    locked = service.last_reconciliation(account) is not None
    form = AccountForm(obj=account)
    if form.validate_on_submit():
        def save():
            account.name, account.kind = form.name.data, form.kind.data
            account.last4, account.active = form.last4.data, form.active.data
            if not locked:
                account.opening_on = form.opening_on.data
                account.opening_balance = form.opening_balance.data
            elif (form.opening_on.data, form.opening_balance.data) != \
                    (account.opening_on, account.opening_balance):
                raise BankError("It's been reconciled: the starting point can't move.")

        if attempt(save, "Saved."):
            return redirect(url_for("bank.account", account_id=account.id))
    else:
        flash_errors(form)
    return render_template("bank/account_form.html", account=account, form=form, locked=locked)


@bp.route("/<int:account_id>")
@requires_role(*READ)
def account(account_id):
    account = load(account_id)
    show = request.args.get("show", "open")
    q = select(BankLine).where(BankLine.bank_account_id == account.id)
    if show == "open":
        q = q.where(BankLine.reconciliation_id.is_(None))
    elif show == "todo":
        q = q.where(BankLine.reconciliation_id.is_(None), BankLine.ignored.is_(None))
    lines = db.session.scalars(q.order_by(BankLine.posted_on.desc(), BankLine.id.desc())
                               .limit(500)).all()
    matches = {}
    for m in db.session.scalars(select(BankMatch).where(
            BankMatch.bank_line_id.in_([x.id for x in lines]))):
        matches.setdefault(m.bank_line_id, []).append(m)
    left = {x.id: x.amount - sum((m.amount for m in matches.get(x.id, [])), service.ZERO)
            for x in lines}
    if show == "todo":
        lines = [x for x in lines if left[x.id] != 0]
    edit = current_user.role in WRITE
    ideas = {}
    pending = [x for x in lines if edit and not x.reconciliation_id and not x.ignored
               and left[x.id] != 0]
    if pending:
        start = min(x.posted_on for x in pending) - timedelta(days=service.SUGGEST_AFTER)
        end = max(x.posted_on for x in pending) + timedelta(days=service.SUGGEST_BEFORE)
        pools = {True: service.records(start, end, money_in=True),
                 False: service.records(start, end, money_in=False)}
        used = set()
        for x in sorted(pending, key=lambda x: x.posted_on):
            if x.id in matches:
                continue                        # partly matched: finish it by hand
            pool = [r for r in pools[x.amount > 0] if r.key not in used]
            found = service.suggestions(x, pool)
            ideas[x.id] = found
            if found and len(found[0]) == 1:
                used.add(found[0][0].key)        # the same record isn't offered twice
    labels = {m.id: describe(m) for ms in matches.values() for m in ms}
    drift = {m.id: service.changed_since(m) for x in lines if x.reconciliation_id
             for m in matches.get(x.id, [])}
    categories = db.session.scalars(select(ExpenseCategory).where(
        ExpenseCategory.active.is_(True)).order_by(ExpenseCategory.position,
                                                    ExpenseCategory.name)).all()
    imports = db.session.scalars(select(BankImport).where(
        BankImport.bank_account_id == account.id).order_by(BankImport.id.desc()).limit(5)).all()
    reconciliations = db.session.scalars(select(BankReconciliation).where(
        BankReconciliation.bank_account_id == account.id)
        .order_by(BankReconciliation.statement_end.desc()).limit(6)).all()
    return render_template(
        "bank/account.html", account=account, lines=lines, matches=matches, left=left,
        ideas=ideas, labels=labels, drift=drift, show=show, edit=edit, categories=categories,
        reasons=IGNORE_REASONS, imports=imports, reconciliations=reconciliations,
        latest=service.last_reconciliation(account), balance=service.balance(account),
        kinds=BANK_KINDS)


def describe(m):
    """A match, as a label and (where there is one) a link."""
    for column, label in MATCH_TARGETS:
        row_id = getattr(m, column)
        if row_id is None:
            continue
        try:
            rec = service.find_record(f"{column}:{row_id}")
            text = rec.label
        except BankError:
            text = f"{label} #{row_id}"
        url = {"payment_id": url_for("billing.payment", payment_id=row_id),
               "expense_id": url_for("books.expense", expense_id=row_id),
               "purchase_order_id": url_for("purchasing.order", po_id=row_id)}.get(column)
        return text, url
    return "?", None


# --- importing --------------------------------------------------------------------------------

@bp.route("/<int:account_id>/import", methods=["GET", "POST"])
@requires_role(*WRITE)
def import_csv(account_id):
    """Step 1: upload the export. Step 2: say which column is which, then import."""
    account = load(account_id)
    if request.method == "GET":
        return render_template("bank/import.html", account=account, step=1)
    csrf_ok()
    sha = request.form.get("file_sha256", "")
    filename = request.form.get("filename", "")
    if not sha:
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            flash("Pick the CSV file your bank gave you.", "error")
            return redirect(url_for("bank.import_csv", account_id=account.id))
        data = upload.read(service.MAX_BYTES + 1)
        try:
            text = service.decode(data)
            rows = service.rows_of(text)
            sha = files.store_csv(data)
        except (BankError, files.FileError) as e:
            flash(str(e), "error")
            return redirect(url_for("bank.import_csv", account_id=account.id))
        filename = upload.filename
    else:
        try:
            data = files.read_csv(sha)
            rows = service.rows_of(service.decode(data))
        except (BankError, files.FileError, ValueError) as e:
            flash(str(e), "error")
            return redirect(url_for("bank.import_csv", account_id=account.id))
    if not rows:
        flash("That file is empty.", "error")
        return redirect(url_for("bank.import_csv", account_id=account.id))
    head = service.header_index(rows)
    header = rows[head]
    sample = rows[head + 1:head + 6]
    mapping = dict(account.mapping or {}) or service.guess_mapping(header)
    if request.form.get("step") == "2":
        mapping = {k: request.form.get(k) or None for k in
                   ("date", "description", "amount", "debit", "credit", "date_format")}
        mapping["flip"] = request.form.get("flip") == "y"
        holder = {}
        if attempt(lambda: holder.update(imp=service.import_lines(account, data, mapping,
                                                                  filename, sha)),
                   lambda _: f"Added {holder['imp'].added} line(s); skipped "
                             f"{holder['imp'].skipped} already here (or before the start)."):
            return redirect(url_for("bank.account", account_id=account.id))
    return render_template("bank/import.html", account=account, step=2, header=header,
                           sample=sample, mapping=mapping, sha=sha, filename=filename,
                           formats=service.DATE_FORMATS)


# --- matching ---------------------------------------------------------------------------------

def load_line(line_id):
    line = get_or_404(BankLine, line_id)
    return line, db.session.get(BankAccount, line.bank_account_id)


def back(account, line=None):
    show = request.form.get("show") or "open"
    return redirect(url_for("bank.account", account_id=account.id, show=show)
                    + (f"#line-{line.id}" if line else ""))


@bp.route("/lines/<int:line_id>/match", methods=["POST"])
@requires_role(*WRITE)
def match(line_id):
    line, account = load_line(line_id)
    csrf_ok()
    keys = [k for k in request.form.getlist("key") if k] or \
        [k for k in (request.form.get("keys") or "").split(",") if k]
    attempt(lambda: service.match(line, keys), "Matched.")
    if request.form.get("from") == "find" and service.left_to_match(line) != 0 and \
            not line.reconciliation_id:
        return redirect(url_for("bank.find", line_id=line.id))
    return back(account, line)


@bp.route("/matches/<int:match_id>/unmatch", methods=["POST"])
@requires_role(*WRITE)
def unmatch(match_id):
    m = get_or_404(BankMatch, match_id)
    line, account = load_line(m.bank_line_id)
    csrf_ok()
    attempt(lambda: service.unmatch(m), "Unmatched.")
    return back(account, line)


@bp.route("/lines/<int:line_id>/aside", methods=["POST"])
@requires_role(*WRITE)
def aside(line_id):
    line, account = load_line(line_id)
    csrf_ok()
    reason = request.form.get("reason", "")
    if reason not in IGNORE_REASONS:
        abort(400)
    attempt(lambda: service.set_aside(line, reason, (request.form.get("note") or "")[:200]
                                      or None), "Set aside.")
    return back(account, line)


@bp.route("/lines/<int:line_id>/back", methods=["POST"])
@requires_role(*WRITE)
def put_back(line_id):
    line, account = load_line(line_id)
    csrf_ok()
    attempt(lambda: service.put_back(line), "Put back.")
    return back(account, line)


@bp.route("/lines/<int:line_id>/expense", methods=["POST"])
@requires_role(*WRITE)
def expense(line_id):
    line, account = load_line(line_id)
    csrf_ok()
    category = request.form.get("category_id", "")
    if not category.isdigit() or db.session.get(ExpenseCategory, int(category)) is None:
        flash("Pick a category for the expense.", "error")
        return back(account, line)
    attempt(lambda: service.expense_from(line, category_id=int(category),
                                         payee=(request.form.get("payee") or "").strip()),
            "Expense added and matched.")
    return back(account, line)


@bp.route("/lines/<int:line_id>/find")
@requires_role(*WRITE)
def find(line_id):
    """Match by hand: everything going the same way within a month either side."""
    line, account = load_line(line_id)
    days = min(max(request.args.get("days", 30, type=int), 1), 365)
    text = (request.args.get("q") or "").strip().lower()
    pool = service.records(line.posted_on - timedelta(days=days),
                           line.posted_on + timedelta(days=days), money_in=line.amount > 0)
    if text:
        pool = [r for r in pool if text in r.label.lower() or text in str(abs(r.amount))]
    pool.sort(key=lambda r: (r.amount != service.left_to_match(line),
                             abs((line.posted_on - r.on).days)))
    return render_template("bank/find.html", line=line, account=account, pool=pool[:200],
                           left=service.left_to_match(line), days=days, q=text,
                           matched=[(m, describe(m)) for m in service.line_matches(line)])


# --- reconciling ------------------------------------------------------------------------------

@bp.route("/<int:account_id>/reconcile", methods=["GET", "POST"])
@requires_role(*READ)
def reconcile(account_id):
    account = load(account_id)
    form = ReconcileForm()
    check, error = None, None
    if request.method == "GET" and not form.statement_end.data:
        form.statement_end.data = local_today()
    if request.method == "POST" and form.validate_on_submit():
        try:
            check = service.reconcile_check(account, form.statement_end.data,
                                            form.ending_balance.data)
        except BankError as e:
            error = str(e)
        if check and request.form.get("action") == "reconcile":
            if current_user.role not in WRITE:
                abort(403)
            if attempt(lambda: service.reconcile(account, form.statement_end.data,
                                                 form.ending_balance.data),
                       f"Reconciled through {form.statement_end.data}: those lines are "
                       "locked."):
                return redirect(url_for("bank.account", account_id=account.id, show="all"))
    elif request.method == "POST":
        flash_errors(form)
    return render_template("bank/reconcile.html", account=account, form=form, check=check,
                           error=error, latest=service.last_reconciliation(account),
                           can_edit=current_user.role in WRITE)


@bp.route("/reconciliations/<int:rec_id>/undo", methods=["POST"])
@requires_role(*WRITE)
def undo(rec_id):
    rec = get_or_404(BankReconciliation, rec_id)
    csrf_ok()
    attempt(lambda: service.undo(rec, (request.form.get("reason") or "").strip()),
            f"Undid the reconciliation through {rec.statement_end}: its lines are open again.")
    return redirect(url_for("bank.account", account_id=rec.bank_account_id, show="open"))
