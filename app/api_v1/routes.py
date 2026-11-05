"""The v1 machine API: GATBOX pushes here, the hub never calls out. The wire format is
contract/v1/README.md (vendored byte-identical from arcade-tracker); its examples are the
spec, and tests/test_api_v1_contract.py posts them.

Different from the rest of the app, on purpose:
  * No login. The app's login gate lets /api/v1 through; requires_device is the whole
    check, and a failure is a 401 in JSON, never a redirect to a login form.
  * CSRF-exempt: a device has no session to carry a token.
  * Rate limited before authentication, keyed on the presented public id (everything
    through the tunnel shares one address): checking a token runs scrypt, which is slow on
    purpose, so an unauthenticated flood would otherwise burn CPU.
  * Every error is {"error": ...} JSON (app/__init__.py's handlers, for /api/v1 paths).
  * The body is parsed with parse_float=Decimal: measurements are stored exactly as sent
    and binary floating point never touches them.
  * Once its token checks out, a device's request runs as the user it acts as (if she's
    active) and names the device (shop.user_id and shop.device_id, app/actor.py), so what
    it writes is audited as "<her> via <device>".
  * A device's jobs switch gates a machine's jobs (orders) and the order and session_tag
    items; rail sessions, the roster and health don't need it."""

import json
import time
from decimal import Decimal
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request, send_file
from sqlalchemy import select

from app.actor import set_actor
from app.extensions import client_ip, csrf, db, limiter
from app.gatbox import romfiles, service
from app.gatbox.romfiles import FileProblem
from app.gatbox.service import ApiError
from app.models import Device

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")
csrf.exempt(bp)


def device_rate_key():
    """A quota bucket per presented public id: not a security boundary (the header is
    unverified); brute force shows in the auth-failure log."""
    _, _, token = request.headers.get("Authorization", "").partition(" ")
    parts = Device.split_token(token.strip())
    return f"device:{parts[0]}" if parts else f"anon:{client_ip()}"


def requires_device(view):
    """Bearer gbx_<public_id>.<secret>. Every failure gets the same answer: saying whether
    the device existed, was switched off, or had the wrong secret only helps a guesser."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        def refuse(why):
            current_app.logger.warning("device auth failed: %s; path=%s; ip=%s", why,
                                       request.path, client_ip())
            return jsonify(error="a valid device token is required"), 401

        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return refuse("no bearer token")
        parts = Device.split_token(token.strip())
        if parts is None:
            return refuse("malformed token")
        public_id, secret = parts
        device = db.session.scalar(select(Device).where(Device.public_id == public_id))
        if device is None:
            return refuse(f"no device with public id {public_id!r}")
        if not device.enabled:
            return refuse(f"device {device.name!r} is switched off")
        if not device.check_secret(secret):
            return refuse(f"wrong secret for device {device.name!r}")
        g.device = device
        acting = service.acting_user(device)
        set_actor(acting.id if acting is not None else None, device.id)
        return view(*args, **kwargs)

    # What the default-deny test (tests/test_roles.py) looks for: a device token, never a
    # person's role.
    wrapped.required_roles = ("device",)
    return wrapped


def _no_constant(name):
    raise ValueError(f"{name} is not a number JSON allows")


def json_body():
    if request.mimetype != "application/json":
        raise ApiError("send application/json", 415)
    if (request.content_length or 0) > service.MAX_BODY:
        raise ApiError(f"the body is over {service.MAX_BODY // (1024 * 1024)} MB", 413)
    raw = request.get_data(cache=False)
    if len(raw) > service.MAX_BODY:
        raise ApiError(f"the body is over {service.MAX_BODY // (1024 * 1024)} MB", 413)
    try:
        return json.loads(raw, parse_float=Decimal, parse_constant=_no_constant)
    except (ValueError, UnicodeDecodeError):
        raise ApiError("body must be JSON") from None


@bp.errorhandler(ApiError)
def api_error(error):
    db.session.rollback()
    return jsonify(error=str(error)), error.code


@bp.errorhandler(FileProblem)
def file_problem(error):
    """A refused files call (capability roms). Nothing is half-written: the upload's own
    state is on disk, and a finish that fails the check drops it."""
    db.session.rollback()
    return jsonify(error=str(error)), error.code


@bp.route("/health", methods=["GET"])
def health():
    """Reachability, with no token: GATBOX picks the LAN address or the tunnel with it
    before committing to a request. It says only that the app is up and what it speaks."""
    return jsonify(ok=True, contract=service.CONTRACT, time=time.time(),  # float-ok: epoch
                   app="shop-hub", capabilities=service.CAPABILITIES)


@bp.route("/ingest", methods=["POST"])
@limiter.limit("120 per minute", key_func=device_rate_key)
@requires_device
def ingest():
    body = json_body()
    try:
        result = service.ingest(g.device, body, client_ip())
        db.session.commit()
    except ApiError:
        raise
    except Exception:
        # Nothing half-written: the next attempt is a clean retry, which the uids make safe.
        db.session.rollback()
        raise
    return jsonify(result)


@bp.route("/roster", methods=["GET"])
@limiter.limit("60 per minute", key_func=device_rate_key)
@requires_device
def roster():
    result = service.roster()
    service.seen(g.device, client_ip())
    db.session.commit()
    return jsonify(result)


@bp.route("/machines/<slug>/orders", methods=["GET"])
@limiter.limit("60 per minute", key_func=device_rate_key)
@requires_device
def machine_orders(slug):
    result = service.orders(slug, request.args.get("status", "open").lower(), g.device)
    service.seen(g.device, client_ip())
    db.session.commit()
    return jsonify(result)


# --- ROM images (capability roms): the resumable upload, and reading back -------------------

@bp.route("/files", methods=["POST"])
@limiter.limit("120 per minute", key_func=device_rate_key)
@requires_device
def files_begin():
    answer = romfiles.begin(json_body())
    service.seen(g.device, client_ip())
    db.session.commit()
    return jsonify(answer)


@bp.route("/files/<sha256>/parts/<int:n>", methods=["PUT"])
@limiter.limit("240 per minute", key_func=device_rate_key)
@requires_device
def files_part(sha256, n):
    if request.mimetype != "application/octet-stream":
        raise ApiError("send application/octet-stream", 415)
    if (request.content_length or 0) > romfiles.PART_SIZE:
        raise ApiError(f"a part is at most {romfiles.PART_SIZE} bytes", 413)
    data = request.get_data(cache=False)
    if len(data) > romfiles.PART_SIZE:
        raise ApiError(f"a part is at most {romfiles.PART_SIZE} bytes", 413)
    return jsonify(romfiles.put_part(sha256, n, data))


@bp.route("/files/<sha256>/finish", methods=["POST"])
@limiter.limit("120 per minute", key_func=device_rate_key)
@requires_device
def files_finish(sha256):
    answer = romfiles.finish(sha256, g.device)
    db.session.commit()
    return jsonify(answer)


@bp.route("/files/<sha256>", methods=["GET"])
@limiter.limit("120 per minute", key_func=device_rate_key)
@requires_device
def files_read(sha256):
    """A device reads back only an image a dump or a burn here names (Range supported)."""
    romfiles.check_hash(sha256)
    if romfiles.named_by_a_record(sha256) is None:
        raise ApiError("no such file", 404)
    try:
        response = send_file(romfiles.path_for(sha256), mimetype="application/octet-stream",
                             conditional=True, etag=sha256, max_age=0)
    except FileNotFoundError:
        raise ApiError("no such file", 404) from None
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/machines/<slug>/dumps", methods=["GET"])
@limiter.limit("60 per minute", key_func=device_rate_key)
@requires_device
def machine_dumps(slug):
    result = service.machine_dumps(slug, g.device)
    service.seen(g.device, client_ip())
    db.session.commit()
    return jsonify(result)
