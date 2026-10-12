"""Saved parts links: the shop's own list of where parts are bought, built as parts are
looked up. The server never fetches a link (no crawling, and nothing for a typed URL to
steer); the title comes from the phone's share sheet or is typed."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func, or_, select

from app.extensions import db
from app.models import (
    CommLog,
    Customer,
    Estimate,
    EstimatePartLink,
    PartLink,
    Vendor,
    WoJob,
    WoLine,
    WorkOrder,
)
from app.search.routes import like
from app.timeutil import local_today

URL_MAX = 2000
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
# Click and campaign tags that only tell a store where you came from. Dropping them keeps
# one saved link per page and nothing about us in a link we send on.
TRACKING_PARAMS = {"fbclid", "gclid", "gbraid", "wbraid", "msclkid", "dclid", "yclid",
                   "igshid", "mc_cid", "mc_eid", "srsltid", "_ga", "_gl"}
SEARCH_WORDS = 6
LIMIT = 50


class LinkError(Exception):
    """A link that can't be saved, with a message for the user."""


def find_url(*texts):
    """The first http(s) URL in any of the texts. Android's share sheet puts a shared
    page's URL in `text` (sometimes `title`), never in `url`, so all three are looked at."""
    for t in texts:
        m = URL_RE.search(t or "")
        if m:
            return m.group(0).rstrip(".,;:!?)]}")
    return None


def normalize(raw):
    """A clean, comparable http(s) URL: scheme and host lowercased, default port and
    tracking tags dropped. Raises LinkError for anything else (a javascript: or data:
    link printed on a customer document would run in their browser)."""
    url = find_url(raw)
    if url is None:
        raise LinkError("That isn't a web address (it must start with http:// or https://).")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        raise LinkError("That web address can't be read.") from None
    if not host or "." not in host:
        raise LinkError("That web address has no site in it.")
    scheme = parts.scheme.lower()
    netloc = host if port in (None, 80 if scheme == "http" else 443) else f"{host}:{port}"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS])
    path = parts.path or "/"
    clean = urlunsplit((scheme, netloc, path, query, parts.fragment))
    if len(clean) > URL_MAX:
        raise LinkError(f"That web address is longer than {URL_MAX} characters.")
    return clean


def domain_of(url):
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def vendor_domain(website):
    """'arcadeparts.com', 'https://www.arcadeparts.com/' -> 'arcadeparts.com'."""
    website = (website or "").strip()
    if not website:
        return None
    if "://" not in website:
        website = "https://" + website
    try:
        return domain_of(website) or None
    except ValueError:
        return None


def match_vendor(domain):
    """The vendor whose website is this domain (or a parent of it), active first."""
    vendors = db.session.scalars(select(Vendor).where(Vendor.website.isnot(None))
                                 .order_by(Vendor.active.desc(), Vendor.id)).all()
    for v in vendors:
        vd = vendor_domain(v.website)
        if vd and (domain == vd or domain.endswith("." + vd)):
            return v
    return None


def by_url(url):
    return db.session.scalar(select(PartLink).where(PartLink.url == url))


def save(link, *, url, title, fits=None, notes=None, price_seen=None, price_seen_on=None,
         part_id=None, active=True):
    """Create (link None) or update a saved link. The vendor follows the domain."""
    url = normalize(url)
    other = by_url(url)
    if other is not None and (link is None or other.id != link.id):
        raise LinkError(f"That page is already saved as “{other.title}”.")
    domain = domain_of(url)
    vendor = match_vendor(domain)
    if link is None:
        link = PartLink()
        db.session.add(link)
    link.url, link.title, link.domain = url, title.strip(), domain
    link.vendor_id = vendor.id if vendor else None
    link.fits, link.notes, link.part_id, link.active = fits, notes, part_id, active
    link.price_seen = price_seen
    link.price_seen_on = (price_seen_on or local_today()) if price_seen is not None else None
    db.session.flush()
    return link


def search(q, include_inactive=False, limit=LIMIT):
    """Every word must appear in the title, what it fits, the notes, the site or the
    vendor's name."""
    stmt = select(PartLink, Vendor).outerjoin(Vendor, Vendor.id == PartLink.vendor_id)
    for word in (q or "").split()[:SEARCH_WORDS]:
        pat = like(word)
        stmt = stmt.where(or_(PartLink.title.ilike(pat), PartLink.fits.ilike(pat),
                              PartLink.notes.ilike(pat), PartLink.domain.ilike(pat),
                              Vendor.name.ilike(pat)))
    if not include_inactive:
        stmt = stmt.where(PartLink.active.is_(True))
    return db.session.execute(stmt.order_by(PartLink.active.desc(), func.lower(PartLink.title))
                              .limit(limit)).all()


def find(q, include_inactive=False):
    """For a box that takes either a search or a pasted URL: (url, saved link or None,
    matches). A URL that isn't saved yet comes back with no link, ready to save."""
    url = find_url(q)
    if url:
        try:
            url = normalize(url)
        except LinkError:
            url = None
    if url:
        link = by_url(url)
        if link is not None:
            vendor = db.session.get(Vendor, link.vendor_id) if link.vendor_id else None
            return url, link, [(link, vendor)]
        return url, None, []
    return None, None, search(q, include_inactive) if (q or "").strip() else []


def usage(link):
    """Everywhere the link went: estimates, work orders, and comm-log entries that
    carry its URL."""
    estimates = db.session.execute(
        select(EstimatePartLink, Estimate, Customer)
        .join(Estimate, Estimate.id == EstimatePartLink.estimate_id)
        .join(Customer, Customer.id == Estimate.customer_id)
        .where(EstimatePartLink.part_link_id == link.id)
        .order_by(Estimate.id.desc())).all()
    work = db.session.execute(
        select(WoLine, WorkOrder, Customer)
        .join(WoJob, WoJob.id == WoLine.wo_job_id)
        .join(WorkOrder, WorkOrder.id == WoJob.work_order_id)
        .join(Customer, Customer.id == WorkOrder.customer_id)
        .where(WoLine.part_link_id == link.id)
        .order_by(WorkOrder.id.desc())).all()
    sent = db.session.execute(
        select(CommLog, Customer).join(Customer, Customer.id == CommLog.customer_id)
        .where(CommLog.summary.ilike(like(link.url)))
        .order_by(CommLog.at.desc())).all()
    return {"estimates": estimates, "work": work, "sent": sent}


def message(links):
    """The text sent to a customer: one part per line, title then URL."""
    return "\n".join(f"{title}: {url}" for title, url in links)
