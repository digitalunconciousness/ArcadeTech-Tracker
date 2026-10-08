import re

from flask import Blueprint, render_template, request
from sqlalchemy import func, or_, select

from app.auth.decorators import ALL_ROLES, requires_role
from app.extensions import db
from app.models import Asset, Contact, Customer, Part, Site, Vendor, WoJob, WorkOrder

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

        results["vendors"] = db.session.scalars(
            select(Vendor).where(or_(Vendor.name.ilike(pat), Vendor.account_ref.ilike(pat)))
            .order_by(Vendor.name).limit(LIMIT)).all()
    return render_template("search/index.html", q=q, results=results,
                           found=any(results.values()))
