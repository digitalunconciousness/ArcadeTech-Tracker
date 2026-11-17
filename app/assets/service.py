"""Asset rules that touch more than one row: tags, status changes, transfers. Every change
writes an asset_event, so an asset's history follows it across owners."""

import re
import unicodedata

from sqlalchemy import Sequence, case, select, text, update

from app.extensions import db
from app.models import Asset, AssetEvent, Customer, Site
from app.timeutil import utcnow

# Created by migration 0003. Never reset: tag numbers are never reused.
ASSET_TAG_SEQ = Sequence("asset_tag_seq")
SLUG_MAX = 24

ACTIVE_STATUSES = ("in_service", "in_shop", "awaiting_pickup", "shipped_back")
AWAY_FROM_SHOP = ("in_service", "shipped_back")


def slugify(name):
    """"Ms. Pac-Man™ (cocktail)" -> "ms-pac-man-cocktail": ASCII, lowercase, single
    dashes, at most SLUG_MAX characters, cut at a dash where possible."""
    name = re.sub(r"[\u2122\u00ae\u00a9]", "", name or "")          # ™ ® ©
    name = re.sub(r"[\u2010-\u2015\u2212]", "-", name)             # unicode dashes
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if len(slug) > SLUG_MAX:
        cut = slug[:SLUG_MAX]
        slug = cut.rsplit("-", 1)[0] if "-" in cut[8:] else cut
        slug = slug.strip("-")
    return slug or "asset"


def new_tag(name):
    number = db.session.execute(select(ASSET_TAG_SEQ.next_value())).scalar_one()
    return f"s-{number:04d}-{slugify(name)}"


def log(asset_id, kind, from_value=None, to_value=None, note=None):
    db.session.add(AssetEvent(asset_id=asset_id, kind=kind, from_value=from_value,
                              to_value=to_value, note=note))


def key_from_name(name):
    """A game key from a machine's model or name, by GATBOX's library rule (lower-case words joined by hyphens, apostrophes
    dropped, "&" read as "and", accents folded to ASCII), a cabinet number on the end ("#2") left off. None when nothing usable
    is left. GATBOX makes the game from the key if its library hasn't got it, and asks the owner first when it reads like a game
    it has (the owner, 2026-11-17: new games should reach the archive without her going to it)."""
    base = re.sub(r"\s*#\s*\d+\s*$", "", name or "")
    s = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"['’`]", "", s.replace("&", " and "))
    key = "-".join(re.findall(r"[a-z0-9]+", s))[:60].rstrip("-")
    return key if key and key != "unassigned" else None


def create(customer, **fields):
    # A machine with no library key gets one from its model (or its name), so its game reaches GATBOX's library and the archive.
    # Boards, monitors and the rest don't: their manuals are the machine's. Editable on the asset's page like any field.
    if fields.get("kind") == "machine" and not fields.get("model_key"):
        fields["model_key"] = key_from_name(fields.get("model") or fields.get("name"))
    asset = Asset(tag=new_tag(fields["name"]), customer_id=customer.id, **fields)
    if asset.status == "in_shop":
        asset.in_shop_since = utcnow()
    db.session.add(asset)
    db.session.flush()
    log(asset.id, "created", to_value=customer.display_name, note=fields.get("notes"))
    return asset


def set_status(asset, status, note=None):
    old = asset.status
    if status == old:
        if note:
            log(asset.id, "note", note=note)
        return
    if status == "in_shop":
        kind = "intake"
        asset.in_shop_since = utcnow()
    elif old in ("in_shop", "awaiting_pickup") and status in AWAY_FROM_SHOP:
        kind = "returned"
        asset.in_shop_since = None
    else:
        kind = "status"
        if status != "awaiting_pickup":
            asset.in_shop_since = None
    asset.status = status
    log(asset.id, kind, from_value=old, to_value=status, note=note)


def subtree_ids(asset_id):
    """The asset and everything inside it (boards in a machine, parts in a board)."""
    rows = db.session.execute(text("""
        WITH RECURSIVE down (id) AS (
            SELECT id FROM asset WHERE id = :root
            UNION
            SELECT a.id FROM asset a JOIN down ON a.parent_asset_id = down.id
        ) SELECT id FROM down"""), {"root": asset_id})
    return [r[0] for r in rows]


def transfer(asset, customer, site, note=None):
    """New owner and/or site. What's inside the asset goes with it. An asset that moves to
    another owner on its own leaves its parent (a board sold out of a machine)."""
    ids = subtree_ids(asset.id)
    old_customer = db.session.get(Customer, asset.customer_id)
    old_site = db.session.get(Site, asset.site_id) if asset.site_id else None
    owner_changes = customer.id != asset.customer_id
    old_tags = dict(db.session.execute(select(Asset.id, Asset.tag).where(Asset.id.in_(ids))).all())

    values = {"customer_id": customer.id, "site_id": site.id if site else None}
    stmt = update(Asset).where(Asset.id.in_(ids))
    if owner_changes and asset.parent_asset_id:
        values["parent_asset_id"] = case((Asset.id == asset.id, None), else_=Asset.parent_asset_id)
    db.session.execute(stmt.values(**values).execution_options(synchronize_session=False))
    db.session.expire_all()

    for asset_id in ids:
        with_note = note if asset_id == asset.id else f"with {old_tags[asset.id]}"
        if owner_changes:
            log(asset_id, "ownership_change", old_customer.display_name, customer.display_name,
                with_note)
        else:
            log(asset_id, "moved", old_site.name if old_site else None,
                site.name if site else None, with_note)
    return ids
