"""Extension instances, bound to the app in create_app()."""

import ipaddress

from flask import request
from flask_limiter import Limiter
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import MetaData

# Stable constraint names, so migrations can drop what they created.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# cloudflared runs in the same LXC and connects from loopback. Only then is
# CF-Connecting-IP the client's address; from anywhere else the header is ignored.
TUNNEL_PEERS = frozenset({"127.0.0.1", "::1"})


def client_ip():
    peer = request.remote_addr or ""
    if peer in TUNNEL_PEERS:
        forwarded = request.headers.get("CF-Connecting-IP", "").strip()
        if forwarded:
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
    return peer


db = SQLAlchemy(metadata=MetaData(naming_convention=NAMING_CONVENTION))
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
limiter = Limiter(client_ip)
