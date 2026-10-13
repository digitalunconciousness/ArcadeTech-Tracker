"""Power tables for the desktop: a list page described once (its columns, filters and
sort), so the table, its CSV and saved views agree. Filtering and sorting happen in the
database, from the query string, so a link or a saved view reproduces the page exactly.
Phones keep each page's own list markup."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from flask import Response, request, url_for
from flask_login import current_user

from app.extensions import db
from app.reports.service import csv_text
from app.reports.tables import Link

CSV_LIMIT = 5000


@dataclass
class Col:
    key: str
    label: str
    value: Callable[[Any], Any]          # the row -> a cell: text, number, date or Link
    kind: str = "text"                   # text, money, qty, date, num, badge
    sort: Any = None                     # SQL expression; None = not sortable
    narrow: bool = False                 # still shown when the record pane is open
    roles: tuple | None = None           # only these roles see it (costs)


@dataclass
class Filter:
    key: str
    label: str
    apply: Callable[[Any, str], Any]     # (query, value) -> query
    choices: Any = None                  # [(value, label)] or a callable returning them
    kind: str = "select"                 # select, text, date
    default: str = ""

    def options(self):
        return self.choices() if callable(self.choices) else (self.choices or [])


@dataclass
class Spec:
    page: str                            # saved_view.page
    title: str
    query: Callable[[], Any]
    columns: list
    filters: list
    sort: tuple                          # default (column key, "asc" | "desc")
    link: Callable[[Any], str]           # the record's URL, for the split pane
    tiebreak: Any = None
    limit: int = 500
    new: tuple | None = None             # (label, endpoint, roles)
    bulk: list = field(default_factory=list)   # [(action, label, choices or None)]
    bulk_endpoint: str | None = None
    bulk_roles: tuple = ()


@dataclass
class Result:
    spec: Spec
    rows: list
    columns: list
    values: dict
    sort: str
    direction: str
    truncated: bool

    def cells(self, row):
        return [(c, c.value(row)) for c in self.columns]

    def link(self, row):
        return self.spec.link(row)

    def sort_url(self, col):
        direction = "desc" if self.sort == col.key and self.direction == "asc" else "asc"
        return here(sort=col.key, dir=direction)

    def can_bulk(self):
        return bool(self.spec.bulk) and current_user.role in self.spec.bulk_roles

    def bulk_actions(self):
        return [(action, label, choices() if callable(choices) else choices)
                for action, label, choices in self.spec.bulk]

    def can_new(self):
        return self.spec.new is not None and current_user.role in self.spec.new[2]

    def query_string(self):
        """What a saved view keeps: this page's filters and sort, nothing else."""
        keep = {k: v for k, v in request.args.items() if k not in ("format", "pane", "page")}
        from urllib.parse import urlencode
        return urlencode(keep)


def here(**changes):
    args = {**request.args.to_dict(), **changes}
    return url_for(request.endpoint, **(request.view_args or {}),
                   **{k: v for k, v in args.items() if v is not None})


def visible_columns(spec):
    return [c for c in spec.columns if c.roles is None or current_user.role in c.roles]


def run(spec, args=None, limit=None):
    args = request.args if args is None else args
    q = spec.query()
    values = {}
    legacy = args.get("all") == "1"              # the phone lists' "show all" switch
    for f in spec.filters:
        if f.key in args:
            value = args.get(f.key)
        elif legacy and f.key in ("status", "active"):
            value = "all" if f.key == "status" else ""
        else:
            value = f.default
        value = value or ""
        value = value.strip()[:100]
        values[f.key] = value
        if value:
            q = f.apply(q, value)
    columns = visible_columns(spec)
    sortable = {c.key: c for c in columns if c.sort is not None}
    sort = args.get("sort") if args.get("sort") in sortable else spec.sort[0]
    direction = args.get("dir") if args.get("dir") in ("asc", "desc") else spec.sort[1]
    expr = sortable[sort].sort
    q = q.order_by(expr.asc().nulls_last() if direction == "asc" else expr.desc().nulls_last())
    if spec.tiebreak is not None:
        q = q.order_by(spec.tiebreak)
    limit = limit or spec.limit
    rows = db.session.execute(q.limit(limit + 1)).all()
    return Result(spec, rows[:limit], columns, values, sort, direction, len(rows) > limit)


def as_csv(spec):
    result = run(spec, limit=CSV_LIMIT)
    header = [c.label for c in result.columns]
    rows = [[_plain(c.value(row)) for c in result.columns] for row in result.rows]
    resp = Response(csv_text(header, rows), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="{spec.page}.csv"'
    return resp


def _plain(value):
    if isinstance(value, Link):
        return value.text
    if isinstance(value, tuple):            # a badge: (text, tone)
        return value[0]
    return value


def view(spec):
    """For a list route: the CSV when asked for, else the table's rows for the page."""
    if request.args.get("format") == "csv":
        return as_csv(spec)
    return run(spec)
