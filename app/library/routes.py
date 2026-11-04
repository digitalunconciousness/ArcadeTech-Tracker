"""A manual's PDF, behind the login. The bytes are in the file store, which no URL reaches; this
is the only way to them. Nothing here edits the library (that happens at the archive)."""

from flask import Blueprint, abort, send_file

from app.auth.decorators import ALL_ROLES, requires_role
from app.library import files, service

bp = Blueprint("library", __name__)


@bp.route("/library/file/<sha>")
@requires_role(*ALL_ROLES)
def manual(sha):
    if not service.configured() or not files.valid(sha) or not files.held(sha):
        abort(404)
    doc = service.document_for(sha)
    if doc is None:
        abort(404)
    name = (
        "".join(
            c if c.isalnum() or c in " ._-" else "_" for c in str(doc.data.get("title") or "manual")
        ).strip()
        or "manual"
    )
    response = send_file(
        files.path_for(sha),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"{name}.pdf",
        conditional=True,
        etag=sha,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # The page-wide policy says object-src 'none', which can stop a browser's own PDF viewer.
    # This answer is a PDF, not a page, so it carries the one thing that matters for it.
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    return response
