"""Customer links and calendar feeds: /d/... The only pages anyone can open without
signing in, so: token only (32 random bytes; the database keeps a hash), one document
per link, read-only except approve and decline, rate-limited, never indexed. A bad,
expired or withdrawn token gets the same 404, so a guess learns nothing."""

from datetime import timedelta

from flask import (
    Blueprint,
    current_app,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from sqlalchemy import select

from app.calendar_ics import calendar
from app.estimates import docs, service
from app.estimates.forms import DeclineForm, SignForm
from app.estimates.service import EstimateError
from app.extensions import client_ip, db, limiter
from app.models import (
    Appointment,
    AppointmentUser,
    CalendarFeed,
    Customer,
    Site,
    User,
    WorkOrder,
)
from app.settings_store import get_settings
from app.timeutil import utcnow
from app.work import files

bp = Blueprint("public", __name__, url_prefix="/d")

VIEW_LIMIT = "60 per minute; 600 per hour"
DECIDE_LIMIT = "10 per minute; 40 per hour"


@bp.after_request
def private(response):
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


def gone():
    return render_template("public/gone.html"), 404


def page(link_est, token, form=None, status=200, error=None):
    link, est = link_est
    customer = db.session.get(Customer, est.customer_id)
    ctx = docs.context(est, customer)
    newer = service.latest_revision(est) != est.revision
    can_decide = est.approved_at is None and est.status in service.OPEN_STATUSES and \
        not service.is_expired(est) and not newer
    return render_template(
        "public/estimate.html", **ctx, token=token, can_decide=can_decide, error=error,
        expired=service.is_expired(est), newer=newer,
        form=form or SignForm(seen=service.content_hash(est)), decline_form=DeclineForm(),
        doc_css=url_for("static", filename="css/doc.css"),
        signature_src=url_for("public.signature", token=token) if est.signature_sha256 else None,
    ), status


@bp.route("/<token>")
@limiter.limit(VIEW_LIMIT)
def estimate(token):
    found = service.find_link(token)
    if found is None:
        return gone()
    link, _ = found
    link.view_count += 1
    link.last_viewed_at = utcnow()
    db.session.commit()
    return page(found, token)


@bp.route("/<token>/approve", methods=["POST"])
@limiter.limit(DECIDE_LIMIT)
def approve(token):
    found = service.find_link(token)
    if found is None:
        return gone()
    _, est = found
    form = SignForm()
    if not form.validate_on_submit():
        return page(found, token, form, 422, error="Type your name and sign in the box.")
    try:
        service.approve(est, name=form.name.data, method="link", signature=form.signature.data,
                        ip=client_ip(), ua=request.headers.get("User-Agent"),
                        seen_hash=form.seen.data)
        db.session.commit()
    except EstimateError as e:
        db.session.rollback()
        return page(service.find_link(token), token, form, 409, error=str(e))
    return redirect(url_for("public.estimate", token=token))   # a refresh won't post again


@bp.route("/<token>/decline", methods=["POST"])
@limiter.limit(DECIDE_LIMIT)
def decline(token):
    found = service.find_link(token)
    if found is None:
        return gone()
    form = DeclineForm()
    try:
        if form.validate_on_submit():
            service.decline(found[1], form.reason.data)
            db.session.commit()
    except EstimateError as e:
        db.session.rollback()
        return page(service.find_link(token), token, status=409, error=str(e))
    return redirect(url_for("public.estimate", token=token))


@bp.route("/<token>/estimate.pdf")
@limiter.limit(VIEW_LIMIT)
def estimate_pdf(token):
    found = service.find_link(token)
    if found is None:
        return gone()
    est = found[1]
    response = make_response(docs.pdf(est, db.session.get(Customer, est.customer_id)))
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f'inline; filename="{docs.filename(est)}"'
    return response


@bp.route("/<token>/signature.png")
@limiter.limit(VIEW_LIMIT)
def signature(token):
    found = service.find_link(token)
    if found is None or not found[1].signature_sha256:
        return gone()
    return send_file(files.path_for(found[1].signature_sha256, ".png"), mimetype="image/png",
                     max_age=0)


@bp.route("/style/accent.css")
def accent_css():
    """The documents' one accent colour, from Settings, as a stylesheet: the CSP allows no
    inline styles. Validated #rrggbb by a CHECK, so it can't inject anything."""
    response = make_response(f":root {{ --accent: {get_settings().doc_accent_color}; }}\n")
    response.headers["Content-Type"] = "text/css; charset=utf-8"
    return response


# --- calendar feeds ---------------------------------------------------------------------------

def site_address(site, customer):
    src = site or customer
    parts = [src.address_line1, src.address_line2,
             " ".join(x for x in (src.city, src.region, src.postal_code) if x)]
    return ", ".join(p for p in parts if p)


@bp.route("/cal/<token>.ics")
@limiter.limit(VIEW_LIMIT)
def calendar_feed(token):
    if len(token) != 43:
        return gone()
    feed = db.session.scalar(select(CalendarFeed).where(
        CalendarFeed.token_sha256 == service.token_hash(token), CalendarFeed.revoked_at.is_(None)))
    user = db.session.get(User, feed.user_id) if feed else None
    if user is None or not user.active:
        return gone()
    rows = db.session.execute(
        select(Appointment, WorkOrder, Customer)
        .join(AppointmentUser, AppointmentUser.appointment_id == Appointment.id)
        .join(WorkOrder, WorkOrder.id == Appointment.work_order_id)
        .join(Customer, Customer.id == WorkOrder.customer_id)
        .where(AppointmentUser.user_id == user.id,
               Appointment.starts_at >= utcnow() - timedelta(days=60))
        .order_by(Appointment.starts_at)).all()
    base = current_app.config["SHOP_BASE_URL"]
    events = []
    for appt, wo, customer in rows:
        site = db.session.get(Site, appt.site_id or wo.site_id) if (appt.site_id or wo.site_id) \
            else None
        events.append({
            "uid": f"appointment-{appt.id}@shop-hub",
            "start": appt.starts_at, "end": appt.ends_at,
            "summary": f"{wo.number} · {customer.display_name}",
            "location": site_address(site, customer),
            "description": "\n".join(x for x in (wo.summary, appt.note) if x),
            "url": f"{base}/work/{wo.id}",
        })
    response = make_response(calendar(get_settings().business_name, events, utcnow()))
    response.headers["Content-Type"] = "text/calendar; charset=utf-8"
    return response
