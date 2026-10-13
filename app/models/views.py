"""A person's saved list views: a name for a list page's filters and sort, kept as the
query string the page already understands."""

from sqlalchemy import CheckConstraint, ForeignKey, Identity, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import StandardColumns


class SavedView(StandardColumns, db.Model):
    __tablename__ = "saved_view"
    __table_args__ = (
        UniqueConstraint("user_id", "page", "name"),
        CheckConstraint("page ~ '^[a-z_]{1,30}$'", name="page_format"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_user.id"), nullable=False,
                                         index=True)
    page: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    query: Mapped[str] = mapped_column(String(1000), nullable=False)
