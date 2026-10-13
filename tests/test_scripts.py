import importlib.util
import os
import subprocess
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


# --- publish_wiki.sh --------------------------------------------------------------------------

def git(cwd, *args):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def repo_with(path, files, branch="main", email="someone@example.com"):
    """A bare repository at path whose branch holds files {relpath: text}."""
    git(path.parent, "init", "-q", "--bare", "-b", branch, path.name)
    work = path.parent / (path.name + "-work")
    git(path.parent, "clone", "-q", str(path), work.name)
    git(work, "config", "user.name", "Someone")
    git(work, "config", "user.email", email)
    git(work, "checkout", "-q", "-b", branch)
    for rel, text in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "seed")
    git(work, "push", "-q", "origin", branch)
    return work


def publish(checkout, wiki):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "SHOP_WIKI_URL": str(wiki)}
    return subprocess.run(["bash", str(ROOT / "scripts" / "publish_wiki.sh")], cwd=checkout,
                          capture_output=True, text=True, env=env)


def test_publish_wiki_copies_main_exactly(tmp_path):
    """The wiki gets docs/wiki/ as it is on origin/main: new pages added, changed ones
    replaced, pages gone from docs/wiki/ removed; a second run changes nothing."""
    origin = tmp_path / "shop.git"
    checkout = repo_with(origin, {"docs/wiki/Home.md": "# Home, new\n",
                                  "docs/wiki/The-Bank.md": "# The bank\n",
                                  "app/secret_sauce.py": "x = 1\n"})
    wiki = tmp_path / "shop.wiki.git"
    repo_with(wiki, {"Home.md": "# Home, old\n", "Stale.md": "# gone\n"}, branch="master")
    (checkout / "docs/wiki/The-Bank.md").write_text("# not merged yet\n")   # local only
    result = publish(checkout, wiki)
    assert result.returncode == 0, result.stderr
    assert "published docs/wiki/ from main at" in result.stdout
    view = tmp_path / "view"
    git(tmp_path, "clone", "-q", str(wiki), view.name)
    assert sorted(p.name for p in view.iterdir() if p.name != ".git") == ["Home.md",
                                                                          "The-Bank.md"]
    assert (view / "Home.md").read_text() == "# Home, new\n"
    assert (view / "The-Bank.md").read_text() == "# The bank\n"   # main's, not the local edit
    sha = git(origin, "rev-parse", "--short", "main")
    assert git(view, "log", "-1", "--format=%s|%ae|%ad", "--date=iso") .startswith(
        f"Publish docs/wiki/ from main at {sha}|someone@example.com|")
    assert git(view, "log", "-1", "--format=%ad", "--date=iso").endswith("+0000")
    again = publish(checkout, wiki)
    assert again.returncode == 0 and "already matches" in again.stdout


def test_publish_wiki_needs_the_pages_on_main(tmp_path):
    checkout = repo_with(tmp_path / "shop.git", {"README.md": "# hi\n"})
    wiki = tmp_path / "shop.wiki.git"
    repo_with(wiki, {"Home.md": "# Home\n"}, branch="master")
    result = publish(checkout, wiki)
    assert result.returncode != 0 and "merge the wiki pages first" in result.stderr
