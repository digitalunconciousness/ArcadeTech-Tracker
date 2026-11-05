"""The library's read side (contract/library-v1/), vendored from GATBOX, byte-identical.

GATBOX owns it (docs/contract/library-v1/read/) and its archive reproduces every example in its own
tests; this repository's pull job reads the same examples through a stub (test_library.py). So
neither end can drift from the other without a test saying so. This file only checks the copy itself:
that it matches its CHECKSUMS and is the same edition as in any sibling checkout."""

import importlib.util
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contract" / "library-v1"
SIBLINGS = ("gatbox", "Gatbox", "cabcrawl", "arcade-tracker")


def checksums():
    spec = importlib.util.spec_from_file_location(
        "contract_checksums", ROOT / "scripts" / "contract_checksums.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tree(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


def test_the_read_side_matches_its_checksums():
    problems = checksums().compare(CONTRACT)
    assert not problems, "contract/library-v1 doesn't match CHECKSUMS:\n  " + "\n  ".join(problems)


def test_the_read_side_is_the_same_as_in_sibling_checkouts():
    """A copy that matches its own CHECKSUMS can still be an older edition than GATBOX's."""
    env = os.environ.get("CONTRACT_SIBLINGS")
    repos = (
        [pathlib.Path(p) for p in env.split()]
        if env is not None
        else [ROOT.parent / n for n in SIBLINGS] + [pathlib.Path("/opt/refs") / n for n in SIBLINGS]
    )
    found = []
    for repo in repos:
        for sub in ("docs/contract/library-v1/read", "contract/library-v1"):
            path = repo / sub
            if (path / "CHECKSUMS").is_file() and path.resolve() != CONTRACT.resolve():
                found.append((str(repo), path))
                break
    if not found:
        pytest.skip("no sibling checkout has the library's read side")
    mine = tree(CONTRACT)
    differ = [repo for repo, path in found if tree(path) != mine]
    assert not differ, "contract/library-v1 differs from the copy in: " + ", ".join(differ)
