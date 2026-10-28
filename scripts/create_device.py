#!/usr/bin/env python3
"""Add a GATBOX device from the console and print its token, once. The same as
Settings → Devices (the usual way, from the phone); this is for when the web UI isn't
handy. The name is prompted, not taken from argv; the token is printed and never stored.

    dev:  python scripts/create_device.py                (reads .env)
    LXC:  see deploy/RUNBOOK.md (runs with /etc/shop-hub/shop-hub.env)"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None, ask=input, out=print):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        out("create_device.py takes no arguments; it prompts for the name.")
        return 2

    from dotenv import load_dotenv
    from sqlalchemy import select

    load_dotenv(ROOT / ".env")
    from app import create_app
    from app.actor import set_actor
    from app.extensions import db
    from app.models import Device

    app = create_app()
    with app.app_context():
        set_actor(None)
        while True:
            name = ask("Device name (the Pi it goes on): ").strip()[:60]
            if not name:
                continue
            if db.session.scalar(select(Device.id).where(Device.name == name)):
                out("A device already has that name.")
                continue
            break
        device = Device(name=name)
        token = device.issue_token()
        db.session.add(device)
        db.session.commit()
        out(f"Device {name} added. Its token, shown this once (only a hash is kept):")
        out(token)
        out(f"On the Pi: HUB_URLS={app.config['SHOP_BASE_URL']} and HUB_TOKEN=<the token>.")
        out("It starts without jobs and acts as no one: set both in Settings → Devices.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
