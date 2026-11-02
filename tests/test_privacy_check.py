"""scripts/privacy_check.sh and .githooks/pre-push: the shared check, byte-identical in all three
repositories (owned by GATBOX, whose tests/test-hooks.sh covers it in full). Here: it runs
against a throwaway repository with this repository's own paths (.githooks/privacy.conf), and
it is the same as any sibling checkout's. Nothing here reads the owner's real patterns file:
SHOP_HOOK_PATTERNS and HOME point into the temporary folder. No database is used."""
import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "privacy_check.sh"
HOOK = ROOT / ".githooks" / "pre-push"
NOREPLY = "240414701+digitalunconciousness@users.noreply.github.com"

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None,
                                reason="needs git and bash")


def git(repo, *args, env):
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture()
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    (tmp_path / "home").mkdir()
    patterns = tmp_path / "patterns.local"
    patterns.write_text("ZEBRA[0-9]+FROG\n")       # invented
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path / "home"),
           "TZ": "UTC", "GIT_CONFIG_NOSYSTEM": "1", "SHOP_HOOK_PATTERNS": str(patterns),
           "GIT_AUTHOR_NAME": "digitalunconciousness", "GIT_COMMITTER_NAME": "digitalunconciousness",
           "GIT_AUTHOR_EMAIL": NOREPLY, "GIT_COMMITTER_EMAIL": NOREPLY}
    git(path, "init", "-q", "-b", "main", env=env)
    add(path, env, "README.md", "An invented shop.\n")
    return path, env


def add(path, env, name, text):
    target = path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    git(path, "add", "-f", name, env=env)
    git(path, "commit", "-q", "-m", f"Add {name}", env=env)


def check(path, env):
    return subprocess.run(["bash", str(SCRIPT)], cwd=path, env=env, capture_output=True,
                          text=True, check=False)


def test_a_clean_commit_passes(repo):
    path, env = repo
    add(path, env, "app/note.txt", "Widget Wars needs a belt.\n")
    result = check(path, env)
    assert result.returncode == 0, result.stdout
    assert "privacy check: clean" in result.stdout


@pytest.mark.parametrize("name", ["files/receipts/r1.jpg", "backups/shop.dump", "instance/x.cfg",
                                  ".env", "estimate.pdf"])
def test_this_repositorys_paths_are_refused(repo, name):
    path, env = repo
    add(path, env, name, "synthetic\n")
    result = check(path, env)
    assert result.returncode == 1 and name in result.stdout


def test_an_owner_pattern_is_refused_and_never_printed(repo):
    path, env = repo
    add(path, env, "a.txt", "ZEBRA42FROG\n")
    result = check(path, env)
    assert result.returncode == 1 and "pattern #1 in a.txt:1" in result.stdout
    assert "ZEBRA" not in result.stdout


def test_no_patterns_file_refuses(repo):
    path, env = repo
    env = {k: v for k, v in env.items() if k != "SHOP_HOOK_PATTERNS"}
    add(path, env, "a.txt", "fine\n")
    result = check(path, env)
    assert result.returncode == 1 and "MISSING" in result.stdout


def test_ci_skips_the_owner_patterns(repo):
    path, env = repo
    env = {k: v for k, v in env.items() if k != "SHOP_HOOK_PATTERNS"}
    add(path, env, "a.txt", "fine\n")
    result = check(path, {**env, "SHOP_PRIVACY_CI": "1"})
    assert result.returncode == 0 and "skipped (CI" in result.stdout


SIBLINGS = ("gatbox", "Gatbox", "cabcrawl", "arcade-tracker")


def test_the_check_and_hook_are_the_same_as_the_siblings():
    """One check for the three repositories: any sibling checkout's copy must match this one.
    CONTRACT_SIBLINGS overrides where to look, as for the contract."""
    env = os.environ.get("CONTRACT_SIBLINGS")
    repos = ([pathlib.Path(p) for p in env.split()] if env is not None else
             [ROOT.parent / n for n in SIBLINGS] + [pathlib.Path("/opt/refs") / n for n in SIBLINGS])
    found = [r for r in repos if (r / "scripts" / "privacy_check.sh").is_file()
             and r.resolve() != ROOT.resolve()]
    if not found:
        pytest.skip("no sibling checkout found")
    mine = (SCRIPT.read_bytes(), HOOK.read_bytes())
    differ = [str(r) for r in found
              if ((r / "scripts" / "privacy_check.sh").read_bytes(),
                  (r / ".githooks" / "pre-push").read_bytes()) != mine]
    assert not differ, "scripts/privacy_check.sh or .githooks/pre-push differs from: " + ", ".join(differ)
