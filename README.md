# ArcadeTech Tracker

The system of record for a small arcade-machine repair business. It tracks customers and
their machines, estimates, work orders, invoices, payments, parts and stock, purchasing,
expenses, mileage and the books. It's built for one shop run by two partners, on their own
server.

> The source is published for reference only. All rights reserved: see [LICENSE](LICENSE).
> It isn't open for contributions.

Inside the code the app, its service, paths and database are called `shop-hub` / `shop`.
"ArcadeTech Tracker" is the business name, a setting printed on documents.

**Using it day to day:** see the
[wiki](https://github.com/digitalunconciousness/ArcadeTech-Tracker/wiki). Its source is
[`docs/wiki/`](docs/wiki/Home.md).

## What it does

**Work**
- **Estimates:** not-to-exceed amounts, deposits and revisions. The customer approves and
  signs on a phone through a link, or you record an OK by phone or text. An approved
  estimate opens the work order.
- **Work orders:** one job per machine or board, with complaint, cause and correction, and
  intake notes. Lines cover labor (a timer, or time added by hand), parts from stock, the
  customer's own parts, fees, sublets, discounts and fixed prices.
- **Readings, photos and documents:** readings against spec, photos (location data
  stripped), and print-ready claim tickets and service reports showing as-found and
  as-left results.
- **Scheduling:** appointments with a calendar feed. On the desktop, a work board you drag
  cards across and a dispatch calendar with a lane per tech.

**Customers and machines**
- Customers, contacts, sites and a communication log.
- Machines and boards, with boards recorded inside their machine.
- Search finds a phone number however it's typed.

**Billing**
- **Invoices:** issued from work orders with gapless numbers, sales tax by jurisdiction,
  warranty dates and a PDF frozen at issue. An issued invoice can't be edited; corrections
  are voids and credit memos.
- **Payments:** deposits, credit on account, refunds, and reversals (bounced checks), with
  receipts and statements.
- **Batches:** invoice a batch of ready work in one go, and send statements to everyone who
  owes, as one PDF or a live link each.
- **Customer links** (`/d/…`) for estimates, invoices and statements: read-only, expiring,
  revocable, not indexed by search engines.

**Parts and purchasing**
- A price book with job templates, and parts priced by fixed price or markup brackets.
- A stock ledger by lot (first in, first out), with counts and adjustments.
- Saved links to parts pages, which you can also add from a phone's share menu.
- A shopping list, purchase orders with carrier tracking, and receiving that sets a part
  aside for the job waiting on it.

**Books**
- **Records:** expenses with receipts, mileage at the IRS standard rate, a partner ledger
  (what each partner put in, took out and is owed), and sales-tax filings.
- **Dashboard and reports**, on cash or accrual basis, each with a CSV: P&L, receivables
  aging, sales tax, 1099 reconciliation, job profitability, labor, turnaround, warranty
  comebacks, inventory value.
- **Bank and wallet reconciliation** from CSV exports: the app suggests matches, a person
  confirms them, and reconciled lines are locked.
- **For an accountant:** a general journal and trial balance that agree with the accrual
  P&L, and a year-end zip with every figure and the documents behind it.

**GATBOX link**: the bench test rig, a Raspberry Pi, posts power-rail traces over a small
versioned API (contract v1). Traces land on the job and on the service report.

**On any screen**
- Phones get a dark synthwave look.
- Desktops (1024 px and up) add a sidebar, light or dark business themes, split view, a
  command bar (Ctrl K), and tables you can sort, filter and save views of.

## How it's built

| | |
|---|---|
| App | Python 3.13, Flask 3.1 (app factory), Jinja templates rendered on the server, plain JavaScript, no front-end framework |
| Data | PostgreSQL 17 only, in dev, tests and production; SQLAlchemy 2 and psycopg 3; Alembic migrations through Flask-Migrate |
| Documents | WeasyPrint PDFs from print-first templates |
| Serving | waitress under systemd, reached through a tunnel behind an access proxy |
| Assets | everything vendored under `app/static/` (fonts, Chart.js): no CDN |

Rules the code keeps:

- Money is `NUMERIC(12,2)` and Python `Decimal` end to end. A test looks for floats near
  money.
- Document numbers are gapless: a counter row locked in the issuing transaction, never a
  database sequence.
- Issued financial records are immutable, enforced by Postgres triggers. An audit trigger
  on every financial table records who changed what.
- The app signs in as a database role that can't change the schema, so it can't switch a
  trigger off. Migrations run as the owner role.
- Times are stored in UTC and shown in the shop's time zone.
- **Access control:**
  - sign-in needs a password and an authenticator code;
  - three roles: owner, tech (sees no money figures) and viewer (read-only, for a
    bookkeeper);
  - CSRF protection on every form and a strict content security policy.

## Repository

```
app/            the Flask app: models/, a package per area, templates/, static/
migrations/     the schema from an empty database, triggers included
contract/v1/    the GATBOX API contract, byte-identical to the client's copy (checksum-tested)
deploy/         deploy.sh, RUNBOOK.md, systemd units, config examples
scripts/        create_owner.py, devdb.sh, backup.sh, restore_drill.sh, privacy_check.sh,
                publish_wiki.sh, ...
tests/          pytest, against a throwaway PostgreSQL database
docs/wiki/      the wiki's pages: using it day to day
docs/PLAN.md    the plan it was built from
PROGRESS.md     what's done, the decisions, notes from each session
CLAUDE.md       the working agreement for AI-assisted sessions
```

## Running it locally

You need Python 3.13, and podman or docker for the dev database.

```
python3.13 -m venv ~/.venvs/shop-hub
source ~/.venvs/shop-hub/bin/activate
pip install --require-hashes -r requirements.txt -r requirements-dev.txt

scripts/devdb.sh start      # PostgreSQL 17 on 127.0.0.1:5433, data under ~/.local/share/shop-hub
scripts/devdb.sh init       # roles shop_owner / shop_app / shop, database shop
cp .env.example .env        # fill it in; scripts/devdb.sh env prints the database lines

flask db upgrade            # runs as the owner role (MIGRATE_DATABASE_URL)
python scripts/create_owner.py
flask run
```

The first owner sets up their authenticator at first sign-in. Everyone else is added in
Settings → Users.

### Tests

```
scripts/devdb.sh start
pytest
```

Each run creates a `shop_test_…` database, runs the real migrations into it (so the
triggers are tested too) and drops it at the end. It refuses to run against any other
database. The backup and restore test needs PostgreSQL 17 client tools (on `PATH`, or
`SHOP_PG_BIN`) and says why when it skips. `ruff check app tests` is the linter.

### Before pushing

```
git config core.hooksPath .githooks
```

The pre-push hook runs `scripts/privacy_check.sh` over every commit being pushed. It looks
for:
- customer data, databases and dumps, `.env` files;
- secrets and private addresses;
- owner-specific patterns, kept outside the repository.

Tests and seed data are synthetic.

### The wiki

The wiki's pages are in `docs/wiki/`, so they're reviewed and privacy-checked like
everything else. Change them through a pull request. Once it's merged, publish them:

```
scripts/publish_wiki.sh
```

It copies `docs/wiki/` exactly as it is on `origin/main` into the wiki repository,
replacing pages edited on GitHub and removing pages that are gone. The first time, save
any page on the Wiki tab so the wiki repository exists.

## Deploying

Production runs in a Debian 13 container, with PostgreSQL on a separate host.
[`deploy/RUNBOOK.md`](deploy/RUNBOOK.md) covers setup end to end: database roles, the
service, the tunnel and access policy, backups, the restore drill and GATBOX devices.
After that, a release is:

```
deploy/deploy.sh --check    # read-only preflight
deploy/deploy.sh            # dump, fast-forward main, install pinned requirements, migrate, restart, health check
```

If any step fails, it prints the exact rollback commands. Backups run nightly, and the
restore drill monthly.

## License

All rights reserved. See [LICENSE](LICENSE).
