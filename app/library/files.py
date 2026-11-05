"""The library's manuals: PDFs read from the archive, kept by SHA-256 in the file store
(SHOP_FILES_DIR/<aa>/<sha256>.pdf), so the backup that already archives that directory carries
them. No URL reaches the folder: the pages open a manual through /library/file/<sha256>, behind
the login, and only for a hash a held document names.

A download in progress is SHOP_FILES_DIR/.incoming/library/<sha256>.part: what has arrived so
far, so a cut-off one resumes from the byte it reached. Nothing in it is a manual until the
joined bytes have been checked against their name."""

import hashlib
import os
import re
from pathlib import Path

from app.work import files

HEX64 = re.compile(r"^[0-9a-f]{64}$")
SUFFIX = ".pdf"


def valid(sha):
    return isinstance(sha, str) and bool(HEX64.match(sha))


def path_for(sha):
    return files.path_for(sha, SUFFIX)


def held(sha):
    return valid(sha) and path_for(sha).is_file()


def part_path(sha):
    folder = files.root() / ".incoming" / "library"
    folder.mkdir(mode=0o750, parents=True, exist_ok=True)
    return folder / f"{sha}.part"


def finish(sha):
    """Move a complete download into place after checking it. Raises ValueError (and drops the
    part) when the bytes don't hash to their name or aren't a PDF."""
    part = part_path(sha)
    digest = hashlib.sha256()
    with open(part, "rb") as f:
        head = f.read(5)
        digest.update(head)
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest() != sha:
        part.unlink(missing_ok=True)
        raise ValueError("the downloaded bytes don't hash to their name: dropped, fetched again next run")
    if head != b"%PDF-":
        part.unlink(missing_ok=True)
        raise ValueError("a document that isn't a PDF: dropped")
    final: Path = path_for(sha)
    final.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.replace(part, final)
    return final


def forget(sha):
    """Remove a manual no held document names any more."""
    if valid(sha):
        path_for(sha).unlink(missing_ok=True)
