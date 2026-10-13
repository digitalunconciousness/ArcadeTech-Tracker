import re

from flask import Blueprint, jsonify, render_template, request, url_for
from sqlalchemy import func, or_, select

from app.auth.decorators import ALL_ROLES, requires_role
from app.extensions import db
from app.models import (
    Asset,
    Contact,
    Customer,
    Estimate,
    Part,
    PartLink,
    Site,
    Vendor,
    WoJob,
    WorkOrder,
)

bp = Blueprint("search", __name__)

LIMIT = 25


def like(term):
    """ILIKE pattern for a literal substring: %, _ and \\ in the query match themselves."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@bp.route("/search")
@requires_role(*ALL_ROLES)
def index():
    q = (request.args.get("q") or "").strip()[:100]
    results = {}
    if len(q) >= 2:
        pat = like(q)
        digits = re.sub(r"\D", "", q)
        phone = like(digits) if len(digits) >= 3 else None

        cust_terms = [Customer.name.ilike(pat), Customer.dba.ilike(pat),
                      Customer.billing_email.ilike(pat)]
        if phone:
            cust_terms.append(Customer.phone_digits.like(phone))
        results["customers"] = db.session.scalars(
            select(Customer).where(or_(*cust_terms)).order_by(Customer.name).limit(LIMIT)).all()

        contact_terms = [Contact.name.ilike(pat), Contact.email.ilike(pat)]
        if phone:
            contact_terms.append(Contact.phone_digits.like(phone))
        results["contacts"] = db.session.execute(
            select(Contact, Customer).join(Customer, Customer.id == Contact.customer_id)
            .where(or_(*contact_terms)).order_by(Contact.name).limit(LIMIT)).all()

        results["assets"] = db.session.execute(
            select(Asset, Customer).join(Customer, Customer.id == Asset.customer_id)
            .where(or_(Asset.tag.ilike(pat), Asset.serial.ilike(pat), Asset.name.ilike(pat),
                       Asset.model.ilike(pat), Asset.manufacturer.ilike(pat)))
            .order_by(Asset.tag).limit(LIMIT)).all()

        results["sites"] = db.session.execute(
            select(Site, Customer).join(Customer, Customer.id == Site.customer_id)
            .where(or_(Site.name.ilike(pat), Site.city.ilike(pat),
                       Site.address_line1.ilike(pat)))
            .order_by(Site.name).limit(LIMIT)).all()

        results["parts"] = db.session.scalars(
            select(Part).where(or_(Part.sku.ilike(pat), Part.name.ilike(pat), Part.mpn.ilike(pat),
                                   func.array_to_string(Part.equivalents, " ").ilike(pat)))
            .order_by(Part.active.desc(), Part.sku).limit(LIMIT)).all()

        results["work"] = db.session.execute(
            select(WorkOrder, Customer).join(Customer, Customer.id == WorkOrder.customer_id)
            .where(or_(WorkOrder.number.ilike(pat), WorkOrder.summary.ilike(pat),
                       WorkOrder.id.in_(select(WoJob.work_order_id)
                                        .where(WoJob.inbound_tracking.ilike(pat)))))
            .order_by(WorkOrder.id.desc()).limit(LIMIT)).all()

        results["estimates"] = db.session.execute(
            select(Estimate, Customer).join(Customer, Customer.id == Estimate.customer_id)
            .where(or_(Estimate.number.ilike(pat), Estimate.summary.ilike(pat)))
            .order_by(Estimate.id.desc()).limit(LIMIT)).all()

        results["links"] = db.session.scalars(
            select(PartLink).where(or_(PartLink.title.ilike(pat), PartLink.fits.ilike(pat),
                                       PartLink.domain.ilike(pat)))
            .order_by(PartLink.active.desc(), func.lower(PartLink.title)).limit(LIMIT)).all()

        from app.purchasing.routes import search as purchasing_search
        results["orders"], results["shipments"] = purchasing_search(pat)

        results["vendors"] = db.session.scalars(
            select(Vendor).where(or_(Vendor.name.ilike(pat), Vendor.account_ref.ilike(pat)))
            .order_by(Vendor.name).limit(LIMIT)).all()
    if request.args.get("format") == "json":
        return jsonify(items=json_items(q, results))
    return render_template("search/index.html", q=q, results=results,
                           found=any(results.values()))


def json_items(q, results, per_kind=6):
    """The same results, flattened for the desktop command bar (Ctrl K). Invoices too, for
    those who see money."""
    from flask_login import current_user

    from app.auth.decorators import COST_ROLES
    from app.models import Invoice

    def take(key, make):
        return [make(r) for r in results.get(key, [])[:per_kind]]

    items = []
    items += take("work", lambda r: {"kind": "Work order", "label": f"{r[0].number} {r[0].summary}",
                                     "sub": r[1].display_name,
                                     "url": url_for("work.show", wo_id=r[0].id)})
    items += take("customers", lambda c: {"kind": "Customer", "label": c.display_name,
                                          "sub": c.phone or "",
                                          "url": url_for("customers.show", customer_id=c.id)})
    items += take("assets", lambda r: {"kind": "Asset", "label": f"{r[0].tag} {r[0].name}",
                                       "sub": r[1].display_name,
                                       "url": url_for("assets.show", asset_id=r[0].id)})
    items += take("estimates", lambda r: {"kind": "Estimate",
                                          "label": f"{r[0].number} {r[0].summary or ''}",
                                          "sub": r[1].display_name,
                                          "url": url_for("estimates.show", est_id=r[0].id)})
    if len(q) >= 2 and current_user.role in COST_ROLES:
        for inv, c in db.session.execute(
                select(Invoice, Customer).join(Customer, Customer.id == Invoice.customer_id)
                .where(Invoice.status != "draft", Invoice.number.ilike(like(q)))
                .order_by(Invoice.id.desc()).limit(per_kind)):
            items.append({"kind": "Invoice", "label": inv.number, "sub": c.display_name,
                          "url": url_for("billing.show", invoice_id=inv.id)})
    items += take("parts", lambda p: {"kind": "Part", "label": f"{p.sku} {p.name}",
                                      "sub": p.bin or "",
                                      "url": url_for("parts.show", part_id=p.id)})
    items += take("orders", lambda r: {"kind": "Purchase order",
                                       "label": r[0].number or f"Draft PO #{r[0].id}",
                                       "sub": r[1].name,
                                       "url": url_for("purchasing.order", po_id=r[0].id)})
    items += take("links", lambda link: {"kind": "Parts link", "label": link.title,
                                         "sub": link.domain or "",
                                         "url": url_for("partlinks.show", link_id=link.id)})
    items += take("vendors", lambda v: {"kind": "Vendor", "label": v.name, "sub": "",
                                        "url": url_for("parts.vendor_show", vendor_id=v.id)})
    return items
