#!/usr/bin/env python3
"""Read the game library's changes for this shop's assets from GATBOX's archive (a read-only copy).

Run every ten minutes by deploy/systemd/shop-hub-library.timer, with the application's
environment (/etc/shop-hub/shop-hub.env); in dev, from the checkout (it reads .env):

    python scripts/pull_library.py

    --status        print how the last run went and what is held, and read nothing
    --clear KEY     remove a game no asset names any more (its records, and manuals only it held)

Needs LIBRARY_ARCHIVE_URL and LIBRARY_ARCHIVE_TOKEN (a reader token); without them it says so and
does nothing. It prints one line of counts, which the journal keeps. Exit codes: 0 for everything,
including an archive that is out of reach (the copy is kept, the next run tries again, and the
asset page says when it last refreshed); 1 when the archive refuses the token, the one answer only
a person can fix; 2 for a command it can't carry out.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(app=None, argv=None, out=print):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true", help="print the last run's outcome and what is held")
    ap.add_argument("--clear", metavar="KEY", help="remove a game no asset names any more")
    args = ap.parse_args(argv)
    if app is None:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        from app import create_app

        app = create_app()
    from sqlalchemy import select

    from app.actor import set_actor
    from app.extensions import db
    from app.library import pull, service
    from app.models import Asset, LibraryRecord

    with app.app_context():
        set_actor(None)
        if not service.configured():
            out(
                "library: no archive configured (LIBRARY_ARCHIVE_URL, LIBRARY_ARCHIVE_TOKEN): nothing to do"
            )
            return 0
        if args.clear:
            live = service.resolve(args.clear) or args.clear
            names = {args.clear, live}
            if db.session.scalar(select(Asset.id).where(Asset.model_key.in_(names))):
                out(f"library: an asset still names {args.clear}, so it stays")
                return 2
            n = service.clear(args.clear)
            db.session.commit()
            out(f"library: cleared {args.clear}: {n} record{'' if n == 1 else 's'} removed")
            return 0 if n else 2
        if args.status:
            st = service.state()
            held = db.session.query(LibraryRecord).count()
            out(
                f"library: {held} records held; last tried {service.ago(st.last_try) if st else 'never'}, "
                f"last read {service.ago(st.last_ok) if st else 'never'}"
                + (f"; {st.error}" if st and st.error else "")
            )
            return 0
        result = pull.run()
        out(
            "library: "
            + " ".join(
                f"{k}={len(v) if isinstance(v, list) else v}" for k, v in result.items() if k != "configured"
            )
        )
        return 1 if result["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
