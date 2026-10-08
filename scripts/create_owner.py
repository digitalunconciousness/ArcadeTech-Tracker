#!/usr/bin/env python3
"""Create the first owner. Everything is prompted; nothing goes on argv (it would land
in shell history and the process list). Refuses if an active owner already exists:
further users, the partner included, are added in Settings → Users.

    dev:  python scripts/create_owner.py                 (reads .env)
    LXC:  see deploy/RUNBOOK.md (runs with /etc/shop-hub/shop-hub.env)

The new owner sets up 2FA at first sign-in; the app holds them there until they do."""

import getpass
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None, ask=input, ask_secret=getpass.getpass, out=print):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        out("create_owner.py takes no arguments; it prompts for everything.")
        return 2

    from dotenv import load_dotenv
    from sqlalchemy import select

    load_dotenv(ROOT / ".env")
    from app import create_app
    from app.actor import set_actor
    from app.extensions import db
    from app.models import User
    from app.models.user import PASSWORD_MAX, PASSWORD_MIN, USERNAME_RE

    app = create_app()
    with app.app_context():
        set_actor(None)
        if db.session.scalar(select(User.id).where(User.role == "owner", User.active.is_(True))):
            out("An active owner already exists. Add users in Settings → Users.")
            return 1

        while True:
            username = ask("Username (lowercase): ").strip().lower()
            if not re.fullmatch(USERNAME_RE, username):
                out("2 to 64 characters: lowercase letters, digits, dot, dash, underscore.")
            elif db.session.scalar(select(User.id).where(User.username == username)):
                out("That username is taken.")
            else:
                break
        display_name = ""
        while not display_name:
            display_name = ask("Display name: ").strip()[:120]
        email = ask("Email (optional): ").strip() or None
        while True:
            password = ask_secret(f"Password ({PASSWORD_MIN}+ characters): ")
            if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
                out(f"Between {PASSWORD_MIN} and {PASSWORD_MAX} characters.")
            elif password != ask_secret("Repeat password: "):
                out("Doesn't match.")
            else:
                break

        user = User(username=username, display_name=display_name, email=email, role="owner",
                    is_partner=True)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        out(f"Owner {username} created. Sign in at {app.config['SHOP_BASE_URL']}/login "
            "and set up 2FA when asked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
