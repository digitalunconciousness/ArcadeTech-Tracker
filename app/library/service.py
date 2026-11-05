"""Reading the library copy: what the asset page shows (contract/library-v1/ describes the records).

Everything here reads ``library_record``; nothing writes it (the pull job, app/library/pull.py,
does, and ``clear`` for the owner at the console). An asset's game comes from ``Asset.model_key``; a
game the archive retired (``replaced_by``) is followed to its survivor, as the contract asks.
Who wrote or confirmed a record (``by``) is never shown: it can be a person's address at the archive.
"""

import re
from datetime import UTC, datetime

from flask import current_app
from sqlalchemy import delete, select

from app.extensions import db
from app.library import files
from app.models import Asset, LibraryRecord, LibraryState

KEY = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
KEY_MAX = 60
DOC_ORDER = ("manual", "schematics", "parts", "kit", "bulletin", "other", "yours")
GROUP_ORDER = ("fuse", "monitor", "psu", "power", "other")
GROUP_LABEL = {
    "fuse": "Fuses",
    "monitor": "Monitor",
    "psu": "Power supply",
    "power": "Power",
    "other": "Other",
}


def configured():
    """Whether an archive is set up for this application. Without one there is no library section."""
    return bool(current_app.config.get("LIBRARY_URL") and current_app.config.get("LIBRARY_TOKEN"))


def valid_key(key):
    return bool(key) and len(key) <= KEY_MAX and bool(KEY.match(key)) and key != "unassigned"


def state():
    return db.session.get(LibraryState, 1)


def refreshed():
    s = state()
    return s.last_ok if s else None


def ago(moment):
    """'12 minutes ago', for a timestamptz."""
    if moment is None:
        return "not yet"
    secs = max(0, int((datetime.now(UTC) - moment).total_seconds()))
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{round(secs / 60)} minutes ago"
    if secs < 129600:
        return f"{round(secs / 3600)} hours ago"
    return f"{round(secs / 86400)} days ago"


def edit_url(key):
    """Where people open this game in the archive, or None when no browser address is set."""
    base = current_app.config.get("LIBRARY_WEB_URL")
    return f"{base}/games/{key}" if base and valid_key(key) else None


def _held(key):
    out = {}
    for r in db.session.scalars(
        select(LibraryRecord).where(LibraryRecord.model_key == key).order_by(LibraryRecord.rid)
    ):
        out.setdefault(r.kind, []).append(r)
    return out


def resolve(key):
    """The key an asset's key stands for now: a retired game's survivor (a few hops at most), else the key."""
    for _ in range(8):
        rec = db.session.scalar(
            select(LibraryRecord).where(LibraryRecord.kind == "game", LibraryRecord.rid == key)
        )
        if rec is None:
            return None
        nxt = (rec.data or {}).get("replaced_by")
        if not nxt:
            return key
        key = nxt
    return None


def game(key):
    """Everything the asset page shows for a game; None when the library hasn't this key (yet)."""
    live = resolve(key) if key else None
    if live is None:
        return None
    held = _held(live)
    head = next(iter(held.get("game", [])), None)
    if head is None:
        return None
    d = head.data
    docs = []
    for r in held.get("document", []):
        x = r.data
        docs.append(
            {
                "id": x["doc_id"],
                "title": x["title"],
                "kind": x["kind"],
                "pages": x.get("pages"),
                "bytes": x.get("bytes"),
                "source": x.get("source") or "",
                "sha256": x.get("sha256"),
                "file": bool(x.get("sha256")) and files.held(x["sha256"]),
            }
        )
    docs.sort(
        key=lambda x: (
            DOC_ORDER.index(x["kind"]) if x["kind"] in DOC_ORDER else len(DOC_ORDER),
            x["title"].lower(),
        )
    )
    titles = {x["id"]: x for x in docs}

    def source(x):
        doc = titles.get(x.get("doc_id"))
        return {"doc": doc, "page": x.get("page")} if doc else None

    limits = [
        {
            "rail": r.data["rail"],
            "lo": r.data["lo"],
            "hi": r.data["hi"],
            "quote": r.data.get("quote") or "",
            "confirmed": bool(r.data.get("confirmed")),
            "source": source(r.data),
        }
        for r in held.get("rail_limit", [])
    ]
    limits.sort(key=lambda x: x["rail"])
    facts = [
        {
            "group": r.data["group"],
            "what": r.data["what"],
            "value": r.data.get("value") or "",
            "quote": r.data.get("quote") or "",
            "source": source(r.data),
        }
        for r in held.get("fact", [])
    ]
    facts.sort(
        key=lambda x: (
            GROUP_ORDER.index(x["group"]) if x["group"] in GROUP_ORDER else len(GROUP_ORDER),
            x["what"].lower(),
        )
    )
    return {
        "key": live,
        "asked": key,
        "title": d["title"],
        "mfr": d.get("mfr") or "",
        "year": d.get("year"),
        "kind": d.get("kind"),
        "notes": d.get("notes") or "",
        "documents": docs,
        "limits": limits,
        "facts": facts,
        "refreshed": refreshed(),
        "edit_url": edit_url(live),
    }


def for_asset(asset):
    """The asset page's game section: None when there is nothing to show (no archive, no key, not held)."""
    if not configured() or not asset.model_key:
        return None
    return game(asset.model_key)


def document_for(sha):
    """The held document record that names this hash, or None."""
    for r in db.session.scalars(select(LibraryRecord).where(LibraryRecord.kind == "document")):
        if (r.data or {}).get("sha256") == sha:
            return r
    return None


def forget_unnamed(shas):
    """Remove the manuals among these hashes that no held document names any more."""
    shas = {s for s in shas if s}
    if shas:
        still = {
            (r.data or {}).get("sha256")
            for r in db.session.scalars(select(LibraryRecord).where(LibraryRecord.kind == "document"))
        }
        for sha in shas - still:
            files.forget(sha)


def key_report():
    """Every game key an asset names, sorted by what the archive says about it, for a person at the console.

    held: the archive sent the game. missing: read from the start and the archive has no game with that
    key (a typo, or a game nobody has added there). unread: not read yet; the next pull asks. invalid: not
    a game key at all, so never asked for. Each is {key: [the assets that name it, as "name (tag)"]}.
    """
    st = state()
    read = set(st.keys_synced or []) if st else set()
    named = {}
    keyed = select(Asset).where(Asset.model_key.isnot(None)).order_by(Asset.name, Asset.tag)
    for a in db.session.scalars(keyed):
        named.setdefault(a.model_key, []).append(f"{a.name} ({a.tag})")
    out = {"held": {}, "missing": {}, "unread": {}, "invalid": {}}
    for key, names in sorted(named.items()):
        if not valid_key(key):
            out["invalid"][key] = names
        elif game(key) is not None:
            out["held"][key] = names
        elif key in read:
            out["missing"][key] = names
        else:
            out["unread"][key] = names
    return out


def key_report_lines(report, limit=20):
    """key_report() as the lines `pull_library.py --status` prints under its first line."""
    total = sum(len(v) for v in report.values())
    if not total:
        return ["no asset names a game yet (set its game key on the asset's page)"]

    def group(title, rows):
        if not rows:
            return []
        lines = [f"  {title}"]
        for key, names in list(rows.items())[:limit]:
            more = f" and {len(names) - 4} more" if len(names) > 4 else ""
            lines.append(f"    {key}  <-  {', '.join(names[:4])}{more}")
        if len(rows) > limit:
            lines.append(f"    ... and {len(rows) - limit} more")
        return lines

    head = (
        f"keys: {total} named by assets: {len(report['held'])} held, "
        f"{len(report['missing'])} not at the archive, {len(report['unread'])} not read yet"
        + (f", {len(report['invalid'])} not game keys" if report["invalid"] else "")
    )
    return (
        [head]
        + group(
            "NOT AT THE ARCHIVE (read in full, and it has no game with that key: fix the key here, "
            "or add the game there)",
            report["missing"],
        )
        + group("not read yet (the next pull asks for these)", report["unread"])
        + group(
            "not game keys (lowercase words joined by hyphens; these are never asked for)",
            report["invalid"],
        )
    )


def clear(key):
    """The owner clears a game no asset names any more: its records, and the manuals only it held. -> rows."""
    docs = db.session.scalars(
        select(LibraryRecord).where(LibraryRecord.model_key == key, LibraryRecord.kind == "document")
    ).all()
    shas = {(r.data or {}).get("sha256") for r in docs}
    n = db.session.execute(delete(LibraryRecord).where(LibraryRecord.model_key == key)).rowcount
    forget_unnamed(shas)
    st = state()
    if st is not None and key in (st.keys_synced or []):
        # An asset that names it later has it read from the start again.
        st.keys_synced = [k for k in st.keys_synced if k != key]
    return n
