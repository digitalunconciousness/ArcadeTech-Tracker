"""Settings → Audit (Phase 9b): the audit log, read back. Every change to a record the
triggers watch, who made it and when, what changed (before → after). Owner only: the log
holds every amount and cost. A record's History link shows the record and the rows that
hang off it directly (a work order's jobs, an invoice's lines and payments)."""

import json
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from flask import Response, render_template, request, url_for
from sqlalchemy import Text, cast, or_, select

from app.auth.decorators import requires_role
from app.extensions import db
from app.models import AuditLog, User
from app.reports.service import csv_text
from app.settings.routes import bp
from app.timeutil import localdt, shop_tz

PAGE = 100
CSV_LIMIT = 5000
# Bookkeeping columns every table has: not worth a line in a diff.
QUIET = frozenset({"updated_at", "created_at", "created_by"})

LABELS = {
    "app_user": "User", "appointment": "Appointment", "appointment_user": "Appointment tech",
    "asset": "Asset", "comm_log": "Comm log", "contact": "Contact",
    "credit_memo": "Credit memo", "credit_memo_line": "Credit memo line",
    "customer": "Customer", "device": "GATBOX device", "estimate": "Estimate",
    "estimate_job": "Estimate job", "estimate_line": "Estimate line",
    "estimate_part_link": "Estimate part to buy", "expense": "Expense", "invoice": "Invoice",
    "invoice_line": "Invoice line", "manual_reading": "Reading", "part": "Part",
    "part_link": "Saved link", "payment": "Payment", "payment_allocation": "Payment applied",
    "po_line": "PO line", "purchase_order": "Purchase order", "reservation": "Set aside",
    "shop_setting": "Settings", "site": "Site", "stock_lot": "Stock lot",
    "stock_move": "Stock move", "time_entry": "Time", "trip": "Trip", "vendor": "Vendor",
    "wo_job": "Job", "wo_job_session": "Trace on a job", "wo_line": "Line",
    "work_order": "Work order", "work_order_tech": "Work order tech",
}
ACTIONS = {"I": "added", "U": "changed", "D": "deleted"}
# Where a record lives in the app: table -> (endpoint, its id argument).
RECORD_PAGES = {
    "work_order": ("work.show", "wo_id"), "wo_job": ("work.job", "job_id"),
    "invoice": ("billing.show", "invoice_id"), "payment": ("billing.payment", "payment_id"),
    "credit_memo": ("billing.credit_memo", "cm_id"), "customer": ("customers.show", "customer_id"),
    "asset": ("assets.show", "asset_id"), "estimate": ("estimates.show", "est_id"),
    "part": ("parts.show", "part_id"), "vendor": ("parts.vendor_show", "vendor_id"),
    "part_link": ("partlinks.show", "link_id"), "purchase_order": ("purchasing.order", "po_id"),
    "expense": ("books.expense", "expense_id"), "app_user": ("settings.user_edit", "user_id"),
}


def label(table):
    return LABELS.get(table, table.replace("_", " ").capitalize())


def record_url(table, row_id):
    page = RECORD_PAGES.get(table)
    if page is None or not (row_id or "").isdigit():
        return None
    return url_for(page[0], **{page[1]: int(row_id)})


def children(table):
    """[(child table, its column pointing at `table`)] from the models' foreign keys (who
    created a row doesn't count)."""
    out = []
    for child in db.Model.metadata.sorted_tables:
        for fk in child.foreign_keys:
            if fk.column.table.name == table and fk.column.name == "id" \
                    and fk.parent.name != "created_by" and child.name != table:
                out.append((child.name, fk.parent.name))
    return out


def load(text):
    """JSONB as text -> dict, numbers as Decimal, exactly as stored."""
    return None if text is None else json.loads(text, parse_float=Decimal)  # float-ok: Decimal


STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}$")


def show(value):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str) and STAMP.match(value):     # a timestamptz: the shop's clock
        return localdt(datetime.fromisoformat(value), "%Y-%m-%d %H:%M:%S")
    text = str(value) if not isinstance(value, (dict, list)) else json.dumps(value, default=str)
    return text if len(text) <= 160 else text[:157] + "…"


def changes(action, before, after):
    """[(column, before, after)] as text: what an entry did."""
    if action == "U":
        keys = [k for k in after if k not in QUIET and before.get(k) != after.get(k)]
        return [(k, show(before.get(k)), show(after.get(k))) for k in keys]
    row = after if action == "I" else before
    return [(k, "" if action == "I" else show(v), show(v) if action == "I" else "")
            for k, v in row.items() if k not in QUIET | {"id"} and v is not None]


def _day(raw):
    try:
        return date.fromisoformat(raw or "")
    except ValueError:
        return None


def filtered(args):
    """The audit_log query for these filters (newest first), and the filters as read."""
    f = {"table": (args.get("table") or "")[:63], "row": (args.get("row") or "")[:40],
         "user": args.get("user", ""), "action": args.get("action", ""),
         "from": _day(args.get("from")), "to": _day(args.get("to")),
         "related": args.get("related") == "1"}
    q = select(AuditLog.id, AuditLog.at, AuditLog.user_id, AuditLog.table_name,
               AuditLog.row_id, AuditLog.action, cast(AuditLog.before, Text),
               cast(AuditLog.after, Text))
    if f["table"] and f["row"]:
        here = (AuditLog.table_name == f["table"]) & (AuditLog.row_id == f["row"])
        if f["related"]:
            parts = [here]
            for child, column in children(f["table"]):
                parts.append((AuditLog.table_name == child) & or_(
                    AuditLog.after[column].astext == f["row"],
                    AuditLog.before[column].astext == f["row"]))
            here = or_(*parts)
        q = q.where(here)
    elif f["table"]:
        q = q.where(AuditLog.table_name == f["table"])
    if f["user"] == "none":
        q = q.where(AuditLog.user_id.is_(None))
    elif f["user"].isdigit():
        q = q.where(AuditLog.user_id == int(f["user"]))
    if f["action"] in ACTIONS:
        q = q.where(AuditLog.action == f["action"])
    tz = shop_tz()
    if f["from"]:
        q = q.where(AuditLog.at >= datetime.combine(f["from"], time.min, tzinfo=tz))
    if f["to"]:
        q = q.where(AuditLog.at < datetime.combine(f["to"] + timedelta(days=1), time.min,
                                                   tzinfo=tz))
    return q.order_by(AuditLog.id.desc()), f


def entries(rows):
    names = dict(db.session.execute(select(User.id, User.display_name).where(
        User.id.in_({r.user_id for r in rows if r.user_id is not None}))).all())
    out = []
    for r in rows:
        before, after = load(r[6]) or {}, load(r[7]) or {}
        out.append({"id": r.id, "at": r.at, "who": names.get(r.user_id, f"user #{r.user_id}")
                    if r.user_id is not None else "system", "table": r.table_name,
                    "label": label(r.table_name), "row": r.row_id,
                    "url": record_url(r.table_name, r.row_id) if r.action != "D" else None,
                    "action": ACTIONS[r.action], "changes": changes(r.action, before, after)})
    return out


@bp.route("/audit")
@requires_role("owner")
def audit():
    q, f = filtered(request.args)
    if request.args.get("format") == "csv":
        rows = entries(db.session.execute(q.limit(CSV_LIMIT)).all())
        body = csv_text(["When", "Who", "What", "Row", "Action", "Changes"], [
            [localdt(e["at"], "%Y-%m-%d %H:%M:%S"), e["who"], e["label"], e["row"], e["action"],
             "; ".join(f"{k}: {b} → {a}" if b and a else f"{k}: {a or b}"
                       for k, b, a in e["changes"])] for e in rows])
        resp = Response(body, mimetype="text/csv")
        resp.headers["Content-Disposition"] = 'attachment; filename="audit.csv"'
        return resp
    before = request.args.get("before", type=int)
    page_q = q.where(AuditLog.id < before) if before else q
    rows = db.session.execute(page_q.limit(PAGE + 1)).all()
    more = len(rows) > PAGE
    items = entries(rows[:PAGE])
    tables = sorted(db.session.scalars(select(AuditLog.table_name).distinct()), key=label)
    users = db.session.execute(select(User.id, User.display_name)
                               .order_by(User.display_name)).all()
    args = {k: v for k, v in request.args.items() if k not in ("before", "format")}
    return render_template(
        "settings/audit.html", items=items, f=f, tables=[(t, label(t)) for t in tables],
        users=users, actions=ACTIONS, args=args,
        older=url_for("settings.audit", **args, before=items[-1]["id"]) if more else None,
        record=record_url(f["table"], f["row"]) if f["table"] and f["row"] else None,
        record_label=f"{label(f['table'])} {f['row']}" if f["table"] and f["row"] else None)
