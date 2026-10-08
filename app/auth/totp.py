"""TOTP (RFC 6238, 30 s steps, 6 digits) with replay protection: a code is accepted for
the previous, current or next step, and only for a step later than the last one used."""

import hmac
import time

import pyotp
import segno

STEP = 30
WINDOW = 1


def new_secret():
    return pyotp.random_base32()


def provisioning_uri(secret, username, issuer):
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def qr_svg(uri):
    return segno.make(uri, error="m").svg_inline(scale=5, dark="#000", light="#fff", border=2)


def matching_step(secret, code, last_step=None, now=None):
    """The step `code` belongs to, or None if it's wrong, stale or already used."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    current = int((time.time() if now is None else now) // STEP)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(totp.at(step * STEP), code):
            return step
    return None
