from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class AuditLog(db.Model):
    """Written only by the audit_row() trigger; append-only (a trigger refuses UPDATE,
    DELETE and TRUNCATE, and the app role has SELECT only).

    The one table without created_at/updated_at/created_by: `at` and `user_id` are those,
    and an append-only row is never updated. user_id has no foreign key, so the log never
    blocks or cascades with app_user."""

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint("action IN ('I', 'U', 'D')", name="action"),
        Index("ix_audit_log_table_row", "table_name", "row_id"),
        Index("ix_audit_log_at", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    user_id: Mapped[int | None] = mapped_column(Integer)
    txid: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("txid_current()")
    )
    table_name: Mapped[str] = mapped_column(String(63), nullable=False)
    row_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(1), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
