import importlib.util
from pathlib import Path

from app.extensions import db
from app.models import AuditLog, User
from conftest import TEST_PASSWORD

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def scripted(*answers):
    it = iter(answers)
    return lambda prompt="": next(it)


def test_create_owner(app):
    out = []
    rc = load("create_owner").main(
        [], ask=scripted("Olive", "bad name!", "olive", "Olive Owner", "olive@example.com"),
        ask_secret=scripted("short", TEST_PASSWORD, "mismatch-test-pw", TEST_PASSWORD, TEST_PASSWORD),
        out=out.append,
    )
    assert rc == 0, out
    with app.app_context():
        user = db.session.query(User).filter_by(username="olive").one()
        assert user.role == "owner" and user.check_password(TEST_PASSWORD)
        assert not user.totp_enabled


def test_create_owner_refuses_when_owner_exists(app, make_user):
    make_user("olive", "owner")
    out = []
    assert load("create_owner").main([], ask=scripted(), ask_secret=scripted(), out=out.append) == 1


def test_create_owner_takes_no_arguments(app):
    out = []
    assert load("create_owner").main(["olive", TEST_PASSWORD], out=out.append) == 2


def test_reset_2fa(app, make_user):
    user = make_user("olive", "owner", totp=True)
    out = []
    assert load("reset_2fa").main([], ask=scripted("olive", "yes"), out=out.append) == 0
    with app.app_context():
        row = db.session.get(User, user["id"])
        assert row.totp_secret is None and not row.totp_enabled and row.session_version == 2
        last = db.session.query(AuditLog).order_by(AuditLog.id.desc()).first()
        assert last.action == "U" and last.user_id is None
        assert last.after["totp_secret"] == "[changed]"
