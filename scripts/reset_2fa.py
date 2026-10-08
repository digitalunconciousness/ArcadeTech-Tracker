#!/usr/bin/env python3
"""Last-resort 2FA reset, for when no other owner can do it from Settings → Users.
Run on the LXC (see deploy/RUNBOOK.md). Prompts for the username; clears the user's TOTP
and signs them out everywhere. They enroll again at next sign-in. The audit log records
the change with no user (it came from the console)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None, ask=input, out=print):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        out("reset_2fa.py takes no arguments; it prompts.")
        return 2

    from dotenv import load_dotenv
    from sqlalchemy import select

    load_dotenv(ROOT / ".env")
    from app import create_app
    from app.actor import set_actor
    from app.extensions import db
    from app.models import User

    app = create_app()
    with app.app_context():
        set_actor(None)
        username = ask("Username to reset: ").strip().lower()
        user = db.session.scalar(select(User).where(User.username == username))
        if user is None:
            out("No such user.")
            return 1
        if ask(f"Reset 2FA for {user.username} ({user.display_name}, {user.role})? Type yes: ") != "yes":
            out("Nothing changed.")
            return 1
        user.totp_secret = None
        user.totp_enabled_at = None
        user.totp_last_step = None
        user.end_sessions()
        db.session.commit()
        out(f"2FA reset for {user.username}. They enroll again at next sign-in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
