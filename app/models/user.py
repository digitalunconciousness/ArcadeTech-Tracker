from datetime import datetime
from decimal import Decimal

from flask_login import UserMixin
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Identity,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models.base import StandardColumns

ROLES = ("owner", "tech", "viewer")
ROLE_LABELS = {
    "owner": "Owner: everything, including money and settings",
    "tech": "Tech: work orders, time, parts; no costs, margins or money, no voids",
    "viewer": "Viewer: read-only, money, costs and exports (the accountant)",
}
PASSWORD_MIN = 12
PASSWORD_MAX = 256
USERNAME_RE = r"^[a-z0-9][a-z0-9._-]{1,63}$"


class User(StandardColumns, UserMixin, db.Model):
    # Not "user": that's a reserved word, and `SELECT * FROM user` in psql quietly
    # returns the current role instead of the table.
    __tablename__ = "app_user"
    # Audited; these columns are redacted from audit_log (a change shows as "[changed]").
    __audited__ = ("password_hash", "totp_secret")
    __table_args__ = (
        UniqueConstraint("username"),
        CheckConstraint(f"username ~ '{USERNAME_RE}'", name="username_format"),
        CheckConstraint("role IN ('owner', 'tech', 'viewer')", name="role"),
        CheckConstraint("split_pct >= 0 AND split_pct <= 100", name="split_pct_range"),
        CheckConstraint("session_version >= 1", name="session_version_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    is_partner: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    split_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Part of the login cookie's id; bumping it logs this user out everywhere.
    session_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    def get_id(self):
        return f"{self.id}:{self.session_version}"

    @property
    def is_active(self):
        return self.active

    @property
    def totp_enabled(self):
        return self.totp_enabled_at is not None

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method="scrypt")

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def end_sessions(self):
        self.session_version = (self.session_version or 1) + 1

    def __repr__(self):
        return f"<User {self.id} {self.username} {self.role}>"
