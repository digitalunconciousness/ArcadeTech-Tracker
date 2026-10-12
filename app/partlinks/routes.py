from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import ALL_ROLES, EDIT_ROLES, requires_role
from app.customers.routes import flash_errors, get_or_404
from app.estimates import service as estimates
from app.extensions import db
from app.models import Estimate, EstimateJob, Part, PartLink, Vendor, WoJob, WorkOrder
from app.partlinks import service
from app.partlinks.forms import AddLinkForm, LinkForm
from app.partlinks.service import LinkError
from app.settings_store import get_settings
from app.work import service as work

bp = Blueprint("partlinks", __name__)

Q_MAX = 2200
ICON_SIZES = (192, 512)


def part_choices(keep=None):
    parts = db.session.scalars(select(Part).where(or_(Part.active.is_(True), Part.id == keep))
                               .order_by(func.lower(Part.sku))).all()
    return [(None, "—")] + [(p.id, f"{p.sku} · {p.name}") for p in parts]


def shared_title(title, text, url):
    """What to call a shared page: its title, else whatever came with the URL."""
    for candidate in (title, (text or "").replace(url or "", " ")):
        candidate = " ".join((candidate or "").split())
        if candidate and candidate != url and not candidate.startswith(("http://", "https://")):
            return candidate[:160]
    return None


def target_of(spec):
    """Where a picked link goes: 'est:<estimate job id>' or 'wo:<WO job id>'. Returns
    (kind, job, page to go back to, the add form's action)."""
    kind, _, ident = (spec or "").partition(":")
    if not ident.isdigit():
        abort(404)
    if kind == "est":
        job = get_or_404(EstimateJob, int(ident))
        est = db.session.get(Estimate, job.estimate_id)
        back = url_for("estimates.show", est_id=est.id) + "#lines"
        if not estimates.editable(est):
            return kind, None, back, None
        return kind, job, back, url_for("estimates.add_part_link", job_id=job.id)
    if kind == "wo":
        job = get_or_404(WoJob, int(ident))
        wo = db.session.get(WorkOrder, job.work_order_id)
        back = url_for("work.job", job_id=job.id) + "#customer-parts"
        if not work.is_open(wo):
            return kind, None, back, None
        return kind, job, back, url_for("work.add_part_link", job_id=job.id)
    abort(404)


def save_from(form, link):
    try:
        link = service.save(link, url=form.url.data, title=form.title.data, fits=form.fits.data,
                            notes=form.notes.data, price_seen=form.price_seen.data,
                            price_seen_on=form.price_seen_on.data, part_id=form.part_id.data,
                            active=True if link is None else form.active.data)
        db.session.commit()
    except LinkError as e:
        db.session.rollback()
        flash(str(e), "error")
        return None
    except IntegrityError:
        db.session.rollback()
        flash("That page was saved by someone else a moment ago. Search for it.", "error")
        return None
    return link


@bp.route("/links")
@requires_role(*ALL_ROLES)
def index():
    q = (request.args.get("q") or "").strip()[:Q_MAX]
    show_all = request.args.get("all") == "1"
    url, link, rows = service.find(q, include_inactive=show_all)
    if not q:
        rows = service.search("", include_inactive=show_all)
    return render_template("partlinks/index.html", q=q, url=url, link=link, rows=rows,
                           show_all=show_all)


@bp.route("/links/new", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def new():
    """Also Android's share target: Chrome → Share → this app opens it with the page's
    title and URL in the query string (the URL usually in `text`)."""
    target = request.args.get("for")
    form = LinkForm()
    form.part_id.choices = part_choices()
    if request.method == "GET":
        args = request.args
        found = service.find_url(args.get("url"), args.get("text"), args.get("title"))
        if found:
            try:
                found = service.normalize(found)
            except LinkError:
                pass
            existing = service.by_url(found)
            if existing is not None:
                flash(f"Already saved as “{existing.title}”.", "ok")
                if target:
                    return redirect(url_for("partlinks.pick", q=existing.url, **{"for": target}))
                return redirect(url_for("partlinks.show", link_id=existing.id))
            form.url.data = found
        form.title.data = form.title.data or shared_title(args.get("title"), args.get("text"),
                                                          found)
    elif form.validate_on_submit():
        link = save_from(form, None)
        if link is not None:
            flash(f"Saved “{link.title}”.", "ok")
            if target:
                return redirect(url_for("partlinks.pick", q=link.url, **{"for": target}))
            return redirect(url_for("partlinks.show", link_id=link.id))
    else:
        flash_errors(form)
    return render_template("partlinks/form.html", form=form, link=None, target=target)


@bp.route("/links/<int:link_id>")
@requires_role(*ALL_ROLES)
def show(link_id):
    link = get_or_404(PartLink, link_id)
    return render_template(
        "partlinks/show.html", link=link,
        vendor=db.session.get(Vendor, link.vendor_id) if link.vendor_id else None,
        part=db.session.get(Part, link.part_id) if link.part_id else None,
        usage=service.usage(link))


@bp.route("/links/<int:link_id>/edit", methods=["GET", "POST"])
@requires_role(*EDIT_ROLES)
def edit(link_id):
    link = get_or_404(PartLink, link_id)
    form = LinkForm(obj=link)
    form.part_id.choices = part_choices(keep=link.part_id)
    if form.validate_on_submit():
        if save_from(form, link) is not None:
            flash("Saved.", "ok")
            return redirect(url_for("partlinks.show", link_id=link.id))
    else:
        flash_errors(form)
    return render_template("partlinks/form.html", form=form, link=link, target=None)


@bp.route("/links/pick")
@requires_role(*EDIT_ROLES)
def pick():
    """Search the saved links, or paste a new URL, and add one to a job."""
    target = request.args.get("for")
    kind, job, back, action = target_of(target)
    if job is None:
        flash("That can't change any more.", "error")
        return redirect(back)
    q = (request.args.get("q") or "").strip()[:Q_MAX]
    url, link, rows = service.find(q)
    return render_template("partlinks/pick.html", q=q, url=url, link=link, rows=rows,
                           target=target, kind=kind, back=back, action=action,
                           form=AddLinkForm(qty=1))


@bp.route("/manifest.webmanifest")
def manifest():
    """Makes the app installable from Chrome's menu, which puts it in Android's share
    sheet. Public (the login page links it too) and holds nothing private."""
    name = get_settings().business_name
    body = {
        "name": name, "short_name": name[:12], "id": "/", "start_url": "/", "scope": "/",
        "display": "standalone", "background_color": "#150a28", "theme_color": "#150a28",
        "icons": [{"src": url_for("static", filename=f"icons/icon-{n}.png"),
                   "sizes": f"{n}x{n}", "type": "image/png", "purpose": "any maskable"}
                  for n in ICON_SIZES],
        "share_target": {"action": url_for("partlinks.new"), "method": "GET",
                         "params": {"title": "title", "text": "text", "url": "url"}},
    }
    response = jsonify(body)
    response.mimetype = "application/manifest+json"
    return response
