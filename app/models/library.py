"""A read-only copy of the games this shop's assets name, from the GATBOX archive.

The archive owns the library (contract/library-v1/). This application never edits it:
``scripts/pull_library.py`` replaces rows with what the archive sends, and a game no asset
names any more keeps its last copy until it is cleared. One row per library record, whatever
its kind (game, document, MAME set, ROM list, ROM image mark, rail limit, fact), with its
data as the archive sent it, so a field the archive adds later needs no migration here.

The documents' PDFs are not in the database: they are files in the file store
(SHOP_FILES_DIR/<aa>/<sha256>.pdf), behind the login (app/library/files.py).

Not audited: the pull job writes these rows, nothing else does, and what the archive sent is
on the row (`rev`, `hash`, `at`, `by`). The app role may change and delete them, because a
newer record replaces an older one and a tombstone removes one.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import StandardColumns


class LibraryRecord(StandardColumns, db.Model):
    """One record of the archive's, as last read: ``(kind, rid)`` names it, ``rev`` is its revision."""

    __tablename__ = "library_record"
    __table_args__ = (
        UniqueConstraint("kind", "rid"),
        CheckConstraint(
            "kind IN ('game', 'document', 'mame', 'rom_list', 'rom_image', 'rail_limit', 'fact')", name="kind"
        ),
        CheckConstraint("rev >= 1", name="rev_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    rid: Mapped[str] = mapped_column(String(200), nullable=False)
    # The game a record belongs to (a game is its own key). A ROM image may name none.
    model_key: Mapped[str | None] = mapped_column(String(60), index=True)
    rev: Mapped[int] = mapped_column(Integer, nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )  # when the archive's last write was made
    by: Mapped[str | None] = mapped_column(String(60))
    data = mapped_column(JSONB, nullable=False)
    fetched: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LibraryState(StandardColumns, db.Model):
    """The pull job's own bookkeeping: one row, ``id`` 1. Where it has read to, and how the last run went."""

    __tablename__ = "library_state"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    cursor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    # The keys read from cursor 0 already: a key an asset gains later is read from the start, once.
    keys_synced = mapped_column(JSONB, nullable=False, server_default="[]")
    last_try: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    refused: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    records: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
