from sqlalchemy import CheckConstraint, Integer, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import StandardColumns

# Document kinds with gapless numbers. Adding one is a migration (the CHECK below).
DOC_KINDS = ("EST", "WO", "INV", "CM", "PO")


class DocCounter(StandardColumns, db.Model):
    """Last number issued per kind per year. Never a Postgres sequence: a rolled-back
    transaction burns a sequence value, and document numbers must be gapless."""

    __tablename__ = "doc_counter"
    __table_args__ = (
        CheckConstraint("kind IN ('EST', 'WO', 'INV', 'CM', 'PO')", name="kind"),
        CheckConstraint("year BETWEEN 2000 AND 2999", name="year_range"),
        CheckConstraint("last >= 0", name="last_nonnegative"),
    )

    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    last: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
