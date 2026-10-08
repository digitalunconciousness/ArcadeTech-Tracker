"""Columns every table carries. Postgres fills them, so hand-run SQL gets them too:
created_at/updated_at default to now(), the set_updated_at() trigger maintains updated_at
and freezes created_at/created_by, and created_by defaults to the request's shop.user_id."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func, text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

ACTOR_DEFAULT = text("NULLIF(current_setting('shop.user_id', true), '')::integer")


class StandardColumns:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @declared_attr
    def created_by(cls) -> Mapped[int | None]:
        return mapped_column(
            Integer, ForeignKey("app_user.id"), nullable=True, server_default=ACTOR_DEFAULT
        )
