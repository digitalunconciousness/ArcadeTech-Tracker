"""The file store: SHOP_FILES_DIR/<aa>/<sha256>.<ext>, content-addressed, written
atomically, never served without a login. backup.sh archives the whole directory.

Photos are re-encoded with Pillow before anything is stored. That drops every EXIF
tag (a phone photo's GPS would give away a customer's address), applies the camera's
rotation, caps the size, and refuses anything that isn't really an image."""

import hashlib
import io
import os
import tempfile
from pathlib import Path

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 2560
THUMB_SIDE = 480
JPEG_QUALITY = 85
# Pillow raises DecompressionBombError at twice this; a 50 MP phone photo still fits.
Image.MAX_IMAGE_PIXELS = 60_000_000
ACCEPTED = {"JPEG", "PNG", "WEBP", "GIF", "MPO"}


class FileError(Exception):
    """Something the user can fix; the message is shown as is."""


def root():
    path = Path(current_app.config["SHOP_FILES_DIR"])
    path.mkdir(mode=0o750, parents=True, exist_ok=True)
    return path


def path_for(sha256, suffix=".jpg"):
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError("not a sha256")
    return root() / sha256[:2] / f"{sha256}{suffix}"


def thumb_path(sha256):
    return path_for(sha256, "-thumb.jpg")


def _write(path, data):
    """Atomic: a crash leaves the old file or none, never half of one."""
    path.parent.mkdir(mode=0o750, exist_ok=True)
    if path.exists():
        return
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _jpeg(img, side):
    img = img.copy()
    img.thumbnail((side, side))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)  # no exif= argument: none kept
    return out.getvalue(), img.size


def store_photo(stream):
    """Re-encode an uploaded image and store it and its thumbnail. Returns
    (sha256, size_bytes, width, height)."""
    raw = stream.read()
    if not raw:
        raise FileError("That file is empty.")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            probe.verify()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError):
        fmt = None
    if fmt not in ACCEPTED:
        raise FileError("That isn't a photo this app can read (JPEG, PNG, WebP or GIF).")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.load()
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            data, (width, height) = _jpeg(img, MAX_SIDE)
            thumb, _ = _jpeg(img, THUMB_SIDE)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError):
        raise FileError("That isn't a photo this app can read (JPEG, PNG, WebP or GIF).") \
            from None
    sha = hashlib.sha256(data).hexdigest()
    _write(path_for(sha), data)
    _write(thumb_path(sha), thumb)
    return sha, len(data), width, height
