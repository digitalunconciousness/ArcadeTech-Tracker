"""Bank reconciliation (Phase 9c). The bank's own export (CSV) comes in; each line is
matched to what the app already records, set aside, or turned into an expense; a statement
that balances is reconciled and locked (triggers, migration 0022). Callers commit; a
BankError's message is shown as is.

Signs follow the bank: money in is positive, money out negative. A match's amount is the
record's share as the bank sees it, so a line's matches add up to the line."""

import csv
import hashlib
import io
import itertools
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select

from app.books import service as books
from app.extensions import db
from app.models import (
    MATCH_TARGETS,
    BankAccount,
    BankImport,
    BankLine,
    BankMatch,
    BankReconciliation,
    Customer,
    Expense,
    PartnerEntry,
    Payment,
    PurchaseOrder,
    SalesTaxFiling,
    User,
    Vendor,
)
from app.timeutil import utcnow

ZERO = Decimal("0.00")
MAX_BYTES = 2_000_000
MAX_ROWS = 5000
DATE_FORMATS = {"%m/%d/%Y": "MM/DD/YYYY", "%m/%d/%y": "MM/DD/YY", "%Y-%m-%d": "YYYY-MM-DD",
                "%d/%m/%Y": "DD/MM/YYYY", "%m-%d-%Y": "MM-DD-YYYY"}
SUGGEST_BEFORE, SUGGEST_AFTER = 5, 7     # days a record may sit before/after its bank line
PARTNER_IN = ("contribution",)


class BankError(Exception):
    pass


# --- reading a bank's CSV ---------------------------------------------------------------------

def decode(data):
    if len(data) > MAX_BYTES:
        raise BankError("That file is too big for a bank export (2 MB at most).")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BankError("That file isn't text: export a CSV from your bank.")


def rows_of(text):
    """The CSV's rows (commas, semicolons or tabs), blank rows dropped."""
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text), dialect)]
    rows = [r for r in rows if any(r)]
    if len(rows) > MAX_ROWS + 20:
        raise BankError(f"That's more than {MAX_ROWS} lines: export a shorter period.")
    return rows


def header_index(rows):
    """Banks often put a few lines of account details above the header: it's the first
    row that names a date column (else the first row)."""
    for i, row in enumerate(rows[:15]):
        if any("date" in cell.lower() for cell in row):
            return i
    return 0


def guess_mapping(header):
    """Which column is which, from the header's names."""
    low = [h.lower() for h in header]

    def find(*words):
        for word in words:
            for i, h in enumerate(low):
                if word in h:
                    return i
        return None

    mapping = {"date": find("posted", "posting date", "transaction date", "date"),
               "description": find("description", "payee", "memo", "details", "name"),
               "amount": find("amount"), "debit": find("debit", "withdrawal"),
               "credit": find("credit", "deposit"), "flip": False}
    if mapping["amount"] is not None:
        mapping["debit"] = mapping["credit"] = None
    return mapping


def parse_amount(raw):
    """'$1,234.56', '(12.00)', '-5', '12.00-' -> Decimal; '' -> None."""
    text = (raw or "").strip().replace("$", "").replace(",", "").replace(" ", "")
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")") or text.endswith("-")
    text = text.strip("()").rstrip("-")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise BankError(f"“{raw}” isn't an amount.") from None
    value = -value if negative else value
    return value.quantize(Decimal("0.01"))


def detect_format(values):
    """The first date format that reads every sample."""
    samples = [v for v in values if v][:30]
    for fmt in DATE_FORMATS:
        try:
            for v in samples:
                datetime.strptime(v, fmt)
            return fmt
        except ValueError:
            continue
    return None


def read_lines(data, mapping):
    """[(posted_on, description, amount)] from an export, using `mapping` (column numbers
    for date, description, and amount or debit/credit; date_format; flip = money out shows
    as positive, as card exports do)."""
    rows = rows_of(decode(data))
    if not rows:
        raise BankError("That file is empty.")
    body = rows[header_index(rows) + 1:]
    width = max(len(r) for r in rows)

    def col(name):
        value = mapping.get(name)
        if value is None or value == "":
            return None
        value = int(value)
        if not 0 <= value < width:
            raise BankError("Pick columns that are in the file.")
        return value

    date_col, desc_col = col("date"), col("description")
    amount_col, debit_col, credit_col = col("amount"), col("debit"), col("credit")
    if date_col is None or desc_col is None:
        raise BankError("Say which columns hold the date and the description.")
    if amount_col is None and debit_col is None and credit_col is None:
        raise BankError("Say which column holds the amount (or the debit and credit columns).")
    fmt = mapping.get("date_format") or detect_format(
        [r[date_col] for r in body if len(r) > date_col])
    if fmt not in DATE_FORMATS:
        raise BankError("The dates aren't in a format I know: pick it.")
    out = []
    for row in body:
        row = row + [""] * (width - len(row))
        if not row[date_col]:
            continue
        try:
            posted = datetime.strptime(row[date_col], fmt).date()
        except ValueError:
            raise BankError(f"“{row[date_col]}” isn't a date in {DATE_FORMATS[fmt]}.") from None
        if amount_col is not None:
            amount = parse_amount(row[amount_col])
        else:
            debit = parse_amount(row[debit_col]) if debit_col is not None else None
            credit = parse_amount(row[credit_col]) if credit_col is not None else None
            amount = (credit or ZERO) - abs(debit or ZERO)
        if amount is None or amount == 0:
            continue
        if mapping.get("flip"):
            amount = -amount
        out.append((posted, row[desc_col][:300] or "(no description)", amount))
    return out


def fingerprint(account_id, posted, description, amount, occurrence):
    """The same line in an overlapping export gets the same print: the nth identical
    (date, amount, description) in a file is the nth one in the next file too."""
    key = f"{account_id}|{posted.isoformat()}|{amount}|{' '.join(description.lower().split())}|" \
          f"{occurrence}"
    return hashlib.sha256(key.encode()).hexdigest()


def import_lines(account, data, mapping, filename, file_sha256):
    """Add the export's lines to the account; lines already imported are skipped. Returns
    the BankImport."""
    lines = read_lines(data, mapping)
    seen, prints = {}, []
    for posted, description, amount in lines:
        key = (posted, description, amount)
        seen[key] = seen.get(key, 0) + 1
        prints.append(fingerprint(account.id, posted, description, amount, seen[key]))
    existing = set(db.session.scalars(select(BankLine.fingerprint).where(
        BankLine.bank_account_id == account.id, BankLine.fingerprint.in_(prints))))
    new = []
    for (posted, description, amount), fp in zip(lines, prints, strict=True):
        if fp not in existing and posted >= account.opening_on:
            new.append((posted, description, amount, fp))
            existing.add(fp)
    imp = BankImport(bank_account_id=account.id, filename=filename[:200],
                     file_sha256=file_sha256, added=len(new), skipped=len(lines) - len(new))
    db.session.add(imp)
    db.session.flush()
    db.session.add_all(BankLine(bank_account_id=account.id, bank_import_id=imp.id,
                                posted_on=posted, description=description, amount=amount,
                                fingerprint=fp) for posted, description, amount, fp in new)
    account.mapping = {k: mapping.get(k) for k in ("date", "description", "amount", "debit",
                                                   "credit", "date_format", "flip")}
    db.session.flush()
    return imp


# --- what a line can be matched to ------------------------------------------------------------

@dataclass
class Record:
    """Something in the app that moved money: what it is, its date, and its amount as the
    bank sees it."""
    column: str            # bank_match column: payment_id, expense_id, ...
    obj: object
    on: date
    amount: Decimal
    label: str
    hint: str = ""         # text to look for in the bank's description

    @property
    def key(self):
        return f"{self.column}:{self.obj.id}"


def matched_ids():
    """{column: {ids already matched somewhere}}."""
    out = {col: set() for col, _ in MATCH_TARGETS}
    for row in db.session.scalars(select(BankMatch)):
        for col, _ in MATCH_TARGETS:
            value = getattr(row, col)
            if value is not None:
                out[col].add(value)
    return out


def records(start, end, *, money_in):
    """Everything the app knows that moved money in (or out) of a business account between
    start and end, not matched yet."""
    taken = matched_ids()
    out = []
    names = dict(db.session.execute(select(Customer.id, Customer.name)).all())
    if money_in:
        for p in db.session.scalars(select(Payment).where(
                Payment.kind == "receipt", Payment.received_on.between(start, end))):
            if p.id not in taken["payment_id"]:
                out.append(Record("payment_id", p, p.received_on, p.amount,
                                  f"Payment #{p.id} from {names.get(p.customer_id, '?')} "
                                  f"({p.method})", p.reference or names.get(p.customer_id, "")))
    else:
        for p in db.session.scalars(select(Payment).where(
                Payment.kind.in_(("refund", "reversal")), Payment.received_on.between(start, end))):
            if p.id not in taken["payment_id"]:
                out.append(Record("payment_id", p, p.received_on, p.amount,
                                  f"{p.kind.capitalize()} #{p.id} to "
                                  f"{names.get(p.customer_id, '?')}", p.reference or ""))
        for e in db.session.scalars(select(Expense).where(
                Expense.paid_by_user_id.is_(None), Expense.voided_at.is_(None),
                Expense.spent_on.between(start, end))):
            if e.id not in taken["expense_id"]:
                out.append(Record("expense_id", e, e.spent_on, -e.amount,
                                  f"Expense: {e.payee}", e.payee))
        for po, vendor in db.session.execute(
                select(PurchaseOrder, Vendor).join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
                .where(PurchaseOrder.paid_by_user_id.is_(None),
                       PurchaseOrder.status.in_(books.SPENT_PO_STATUSES),
                       PurchaseOrder.ordered_on.between(start, end))):
            if po.id not in taken["purchase_order_id"]:
                out.append(Record("purchase_order_id", po, po.ordered_on, -books.po_total(po),
                                  f"{po.number}: {vendor.name}", vendor.name))
        for f in db.session.scalars(select(SalesTaxFiling).where(
                SalesTaxFiling.paid_on.between(start, end))):
            if f.id not in taken["sales_tax_filing_id"] and f.tax_due:
                out.append(Record("sales_tax_filing_id", f, f.paid_on, -f.tax_due,
                                  f"Sales tax {f.period_start}–{f.period_end}", "tax"))
    users = dict(db.session.execute(select(User.id, User.display_name)).all())
    kinds = PARTNER_IN if money_in else ("draw", "reimbursement", "distribution")
    for e in db.session.scalars(select(PartnerEntry).where(
            PartnerEntry.kind.in_(kinds), PartnerEntry.voided_at.is_(None),
            PartnerEntry.entry_on.between(start, end))):
        if e.id not in taken["partner_entry_id"]:
            out.append(Record("partner_entry_id", e, e.entry_on,
                              e.amount if money_in else -e.amount,
                              f"{users.get(e.user_id, '?')}: {e.kind}", users.get(e.user_id, "")))
    return out


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())}


def score(line, rec):
    """Lower is better: days apart, less a bonus when the description mentions it."""
    days = abs((line.posted_on - rec.on).days)
    bonus = 3 if _words(rec.hint) & _words(line.description) else 0
    return days - bonus


def suggestions(line, pool=None, limit=3):
    """The likeliest records for an unmatched line: the same amount within a few days, then
    (for money in) a deposit that adds up several receipts. [[Record, ...], ...]"""
    if pool is None:
        pool = records(line.posted_on - timedelta(days=SUGGEST_AFTER),
                       line.posted_on + timedelta(days=SUGGEST_BEFORE), money_in=line.amount > 0)
    near = [r for r in pool if -SUGGEST_BEFORE <= (line.posted_on - r.on).days <= SUGGEST_AFTER]
    singles = sorted((r for r in near if r.amount == line.amount), key=lambda r: score(line, r))
    out = [[r] for r in singles[:limit]]
    if not out and line.amount > 0:
        receipts = sorted((r for r in near if r.column == "payment_id" and 0 < r.amount < line.amount
                           and r.on <= line.posted_on), key=lambda r: r.on)[-12:]
        for size in (2, 3, 4):
            for combo in itertools.combinations(receipts, size):
                if sum((r.amount for r in combo), ZERO) == line.amount:
                    out.append(list(combo))
                    if len(out) >= limit:
                        return out
    return out


# --- matching, setting aside, reconciling -----------------------------------------------------

def find_record(key):
    """'expense_id:7' -> its Record (whatever its date), or a BankError."""
    try:
        column, raw = key.split(":")
        row_id = int(raw)
    except ValueError:
        raise BankError("Pick something to match.") from None
    model = {"payment_id": Payment, "expense_id": Expense, "purchase_order_id": PurchaseOrder,
             "partner_entry_id": PartnerEntry, "sales_tax_filing_id": SalesTaxFiling}.get(column)
    obj = db.session.get(model, row_id) if model else None
    if obj is None:
        raise BankError("That isn't there any more.")
    on = {"payment_id": "received_on", "expense_id": "spent_on", "purchase_order_id": "ordered_on",
          "partner_entry_id": "entry_on", "sales_tax_filing_id": "paid_on"}[column]
    for rec in records(getattr(obj, on), getattr(obj, on), money_in=True) + \
            records(getattr(obj, on), getattr(obj, on), money_in=False):
        if rec.column == column and rec.obj.id == obj.id:
            return rec
    raise BankError("That's matched already, or isn't money through a business account.")


def line_matches(line):
    return db.session.scalars(select(BankMatch).where(BankMatch.bank_line_id == line.id)
                              .order_by(BankMatch.id)).all()


def left_to_match(line):
    return line.amount - sum((m.amount for m in line_matches(line)), ZERO)


def match(line, keys):
    """Match the line to these records ('payment_id:3', ...). Together with what's already
    matched they mustn't come to more than the line."""
    if line.reconciliation_id is not None:
        raise BankError("That line is reconciled: it can't change.")
    if line.ignored:
        raise BankError("That line is set aside: put it back first.")
    recs = [find_record(k) for k in dict.fromkeys(keys)]
    if not recs:
        raise BankError("Pick something to match.")
    total = sum((r.amount for r in recs), ZERO)
    left = left_to_match(line)
    if any((r.amount > 0) != (line.amount > 0) for r in recs):
        raise BankError("Money in matches money in, and out matches out.")
    if abs(total) > abs(left):
        raise BankError(f"Those come to {abs(total)}, more than the {abs(left)} left on the line.")
    for r in recs:
        db.session.add(BankMatch(bank_line_id=line.id, amount=r.amount, **{r.column: r.obj.id}))
    db.session.flush()


def unmatch(match_row):
    line = db.session.get(BankLine, match_row.bank_line_id)
    if line.reconciliation_id is not None:
        raise BankError("That line is reconciled: it can't change.")
    db.session.delete(match_row)
    db.session.flush()


def set_aside(line, reason, note=None):
    if line.reconciliation_id is not None:
        raise BankError("That line is reconciled: it can't change.")
    if line_matches(line):
        raise BankError("That line is matched: unmatch it first.")
    line.ignored, line.ignored_note = reason, note
    db.session.flush()


def put_back(line):
    if line.reconciliation_id is not None:
        raise BankError("That line is reconciled: it can't change.")
    line.ignored = line.ignored_note = None
    db.session.flush()


def expense_from(line, *, category_id, payee):
    """Money out with no record yet (a card purchase, a bank fee): an expense paid from
    the business, matched to the line."""
    if line.amount >= 0:
        raise BankError("Only money out can be an expense.")
    left = left_to_match(line)
    if left == 0:
        raise BankError("That line is matched in full already.")
    expense = Expense(spent_on=line.posted_on, payee=payee[:120] or line.description[:120],
                      category_id=category_id, amount=-left, tax_included=ZERO,
                      paid_by_user_id=None, note=f"From the bank: {line.description}"[:2000])
    db.session.add(expense)
    db.session.flush()
    match(line, [f"expense_id:{expense.id}"])
    return expense


def last_reconciliation(account):
    return db.session.scalar(select(BankReconciliation).where(
        BankReconciliation.bank_account_id == account.id, BankReconciliation.undone_at.is_(None))
        .order_by(BankReconciliation.statement_end.desc()).limit(1))


def open_lines(account, through):
    """Lines not reconciled yet, up to `through`."""
    return db.session.scalars(select(BankLine).where(
        BankLine.bank_account_id == account.id, BankLine.reconciliation_id.is_(None),
        BankLine.posted_on <= through).order_by(BankLine.posted_on, BankLine.id)).all()


def reconcile_check(account, statement_end, ending_balance):
    """What reconciling would do: {opening, lines, computed, unresolved, difference}."""
    last = last_reconciliation(account)
    opening = last.ending_balance if last else account.opening_balance
    if last and statement_end <= last.statement_end:
        raise BankError(f"Reconciled through {last.statement_end} already: pick a later date.")
    lines = open_lines(account, statement_end)
    unresolved = [x for x in lines if not x.ignored and left_to_match(x) != 0]
    computed = opening + sum((x.amount for x in lines), ZERO)
    return {"opening": opening, "lines": lines, "computed": computed, "unresolved": unresolved,
            "difference": ending_balance - computed, "last": last}


def reconcile(account, statement_end, ending_balance):
    check = reconcile_check(account, statement_end, ending_balance)
    if check["unresolved"]:
        raise BankError(f"{len(check['unresolved'])} line(s) aren't matched or set aside yet.")
    if check["difference"] != 0:
        raise BankError(f"It doesn't balance: the lines come to {check['computed']}, the "
                        f"statement says {ending_balance} ({check['difference']} apart).")
    rec = BankReconciliation(bank_account_id=account.id, statement_end=statement_end,
                             opening_balance=check["opening"], ending_balance=ending_balance)
    db.session.add(rec)
    db.session.flush()
    for line in check["lines"]:
        line.reconciliation_id = rec.id
    db.session.flush()
    return rec


def undo(rec, reason):
    """Undo the account's latest reconciliation: its lines open up again."""
    if not reason:
        raise BankError("Say why it's being undone.")
    latest = last_reconciliation(db.session.get(BankAccount, rec.bank_account_id))
    if rec.undone_at is not None or latest is None or latest.id != rec.id:
        raise BankError("Only the latest reconciliation can be undone.")
    rec.undone_at, rec.undo_reason = utcnow(), reason[:300]
    db.session.flush()
    for line in db.session.scalars(select(BankLine).where(BankLine.reconciliation_id == rec.id)):
        line.reconciliation_id = None
    db.session.flush()


def balance(account):
    """Opening balance plus every line since."""
    return account.opening_balance + (db.session.scalar(
        select(func.coalesce(func.sum(BankLine.amount), 0)).where(
            BankLine.bank_account_id == account.id)) or ZERO)


def changed_since(match_row):
    """For a reconciled match: the record's amount now, when it differs from what was
    matched (an expense edited or voided, a purchase order's total changed), else None."""
    for column, model, now in (
            ("expense_id", Expense, lambda o: ZERO if o.voided_at else -o.amount),
            ("purchase_order_id", PurchaseOrder, lambda o: -books.po_total(o)),
            ("partner_entry_id", PartnerEntry,
             lambda o: ZERO if o.voided_at else (o.amount if o.kind in PARTNER_IN else -o.amount)),
            ("sales_tax_filing_id", SalesTaxFiling, lambda o: -o.tax_due)):
        row_id = getattr(match_row, column)
        if row_id is not None:
            value = now(db.session.get(model, row_id))
            return value if value != match_row.amount else None
    return None
