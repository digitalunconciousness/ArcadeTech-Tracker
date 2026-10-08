from functools import wraps

from flask import abort
from flask_login import current_user

from app.extensions import login_manager

ALL_ROLES = ("owner", "tech", "viewer")


def requires_role(*roles):
    """Allow only these roles. Roles don't nest (a viewer sees money, a tech doesn't),
    so every view lists exactly who may use it. A test checks that every view outside
    the public allowlist carries this decorator."""
    unknown = set(roles) - set(ALL_ROLES)
    if not roles or unknown:
        raise ValueError(f"requires_role needs known roles, got {roles!r}")

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        wrapper.required_roles = frozenset(roles)
        return wrapper

    return decorator
