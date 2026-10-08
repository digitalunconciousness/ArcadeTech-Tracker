"""Gapless document numbers (EST-2026-0001).

issue_number() must run inside the transaction that issues the document. The UPDATE
takes a row lock on (kind, year) that is held until that transaction ends: a concurrent
issuer waits, and a rollback gives the number back, so no number is skipped or reused."""

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.extensions import db
from app.models.doc_counter import DOC_KINDS, DocCounter
from app.timeutil import local_today

_counter = DocCounter.__table__


def issue_number(kind, on=None, session=None):
    """Next number for kind in the year of `on` (default: today in SHOP_TZ)."""
    if kind not in DOC_KINDS:
        raise ValueError(f"unknown document kind {kind!r}")
    session = session or db.session
    year = (on or local_today()).year
    session.execute(
        pg_insert(_counter).values(kind=kind, year=year, last=0).on_conflict_do_nothing()
    )
    last = session.execute(
        update(_counter)
        .where(_counter.c.kind == kind, _counter.c.year == year)
        .values(last=_counter.c.last + 1)
        .returning(_counter.c.last)
    ).scalar_one()
    return f"{kind}-{year}-{last:04d}"
