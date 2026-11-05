"""GATBOX traces for people: one trace with its chart, putting it on a job or taking it
off, and its phase there. Everyone signed in can look; owners and techs can change."""

import json

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user
from sqlalchemy import func, select

from app.auth.decorators import ALL_ROLES, EDIT_ROLES, requires_role
from app.auth.forms import EmptyForm
from app.extensions import db
from app.gatbox import charts, romfiles, service
from app.gatbox.service import TraceError
from app.models import (
    LINK_SOURCES,
    READING_PHASES,
    Asset,
    Device,
    RailSession,
    Reading,
    RomBurn,
    RomDump,
    RomFile,
    WoJob,
    WoJobSession,
    WorkOrder,
)

bp = Blueprint("traces", __name__)

VERDICT_BADGES = {"held": ("ok", "held the window"), "left": ("warn", "left the window"),
                  "over": ("danger", "went over")}


@bp.app_context_processor
def trace_labels():
    return {"trace_phases": READING_PHASES, "verdict_badges": VERDICT_BADGES,
            "link_sources": LINK_SOURCES}


def back(session_id, job_id=None):
    if request.form.get("back") == "job" and job_id:
        return redirect(url_for("work.job", job_id=job_id) + "#traces")
    return redirect(url_for("traces.show", session_id=session_id))


def chart_json(session):
    points = [[str(t), None if v is None else str(v)] for t, v in service.trace(session.id)]
    return json.dumps({"kind": "line", "points": points, "unit": session.window_unit or "V",
                       "lo": None if session.window_lo is None else str(session.window_lo),
                       "hi": None if session.window_hi is None else str(session.window_hi),
                       "alarm": None if session.alarm_hi is None else str(session.alarm_hi)})


@bp.route("/traces/<int:session_id>")
@requires_role(*ALL_ROLES)
def show(session_id):
    session = db.session.get(RailSession, session_id) or abort(404)
    asset = db.session.get(Asset, session.asset_id)
    device = db.session.get(Device, session.device_id)
    links = db.session.execute(
        select(WoJobSession, WoJob, WorkOrder).join(WoJob, WoJob.id == WoJobSession.wo_job_id)
        .join(WorkOrder, WorkOrder.id == WoJob.work_order_id)
        .where(WoJobSession.rail_session_id == session.id).order_by(WoJob.id)).all()
    on = {job.id for _, job, _ in links}
    jobs = db.session.execute(
        select(WoJob, WorkOrder).join(WorkOrder, WorkOrder.id == WoJob.work_order_id)
        .where(WoJob.asset_id == asset.id).order_by(WoJob.id.desc())).all()
    attachable = [(job, wo) for job, wo in jobs if job.id not in on]
    count = db.session.scalar(select(func.count(Reading.id)).where(
        Reading.rail_session_id == session.id))
    edit = current_user.role in EDIT_ROLES
    # This trace against its other half (as found / as left) on a job it's on.
    cmp, cmp_wo = None, None
    for _, job, wo in links:
        found = service.compare(job.id)
        if found and session.id in (found["found"].id, found["left"].id):
            cmp, cmp_wo = found, wo
            break
    return render_template("traces/show.html", session=session, asset=asset, device=device,
                           links=links, attachable=attachable if edit else [], count=count,
                           chart=chart_json(session), action=EmptyForm(), edit=edit,
                           cmp=cmp, cmp_wo=cmp_wo, overlay=charts.overlay_json(cmp))


@bp.route("/traces/<int:session_id>/attach", methods=["POST"])
@requires_role(*EDIT_ROLES)
def attach(session_id):
    session = db.session.get(RailSession, session_id) or abort(404)
    try:
        job_id = int(request.form.get("job_id", ""))
    except ValueError:
        abort(400)
    job = db.session.get(WoJob, job_id) or abort(404)
    if EmptyForm().validate_on_submit():
        try:
            service.attach(job, session, "manual")
            db.session.commit()
            flash("Trace put on the job.", "ok")
        except TraceError as e:
            db.session.rollback()
            flash(str(e), "error")
    return back(session.id, job.id)


@bp.route("/traces/links/<int:link_id>/<action>", methods=["POST"])
@requires_role(*EDIT_ROLES)
def link_action(link_id, action):
    link = db.session.get(WoJobSession, link_id) or abort(404)
    session_id, job_id = link.rail_session_id, link.wo_job_id
    if action not in ("phase", "detach") or not EmptyForm().validate_on_submit():
        abort(400)
    try:
        if action == "phase":
            service.set_phase(link, request.form.get("phase") or None)
            message = "Phase saved."
        else:
            service.detach(link)
            message = "Trace taken off the job. It's still on the machine's page."
        db.session.commit()
        flash(message, "ok")
    except TraceError as e:
        db.session.rollback()
        flash(str(e), "error")
    return back(session_id, job_id)


# --- ROM images from GATBOX: the bytes behind the login, never a public address ----------------

def _image(record, name):
    """An image as a download. Everyone signed in may take one; the file store is outside
    anything the web server serves, and a device's token can't reach this route."""
    file = db.session.get(RomFile, record.rom_file_id) or abort(404)
    try:
        response = send_file(romfiles.path_for(file.sha256), mimetype="application/octet-stream",
                             as_attachment=True, download_name=name, conditional=True,
                             etag=file.sha256, max_age=0)
    except FileNotFoundError:
        abort(404)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.route("/roms/dumps/<int:dump_id>/image")
@requires_role(*ALL_ROLES)
def dump_image(dump_id):
    dump = db.session.get(RomDump, dump_id) or abort(404)
    asset = db.session.get(Asset, dump.asset_id)
    return _image(dump, f"{asset.tag}_{dump.label}_{dump.sha1[:8]}.bin")


@bp.route("/roms/burns/<int:burn_id>/image")
@requires_role(*ALL_ROLES)
def burn_image(burn_id):
    burn = db.session.get(RomBurn, burn_id) or abort(404)
    asset = db.session.get(Asset, burn.asset_id)
    return _image(burn, f"{asset.tag}_burned_{burn.image_sha1[:8]}.bin")
