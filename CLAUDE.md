# shop-hub (ArcadeTech Tracker)

The system of record for the owner's side arcade-repair business, run with a partner: customers,
assets, estimates, work orders, invoices, payments, parts, purchasing, expenses, mileage. A
private Flask + PostgreSQL app in its own LXC. GATBOX (the bench Pi) pushes traces here over
contract v1, exactly as it does to the barcade's arcade-tracker.

"ArcadeTech Tracker" is the business/display name (a setting, printed on documents). The
GitHub repo is `digitalunconciousness/ArcadeTech-Tracker` (**public**, the owner's choice 2026-10-07; all rights reserved, see LICENSE). The local checkout,
service, paths and database stay `shop-hub` / `shop`, so nothing collides with the barcade's
`arcade-tracker` repo, unit or `/opt` path.

**Resume from PROGRESS.md.** Every session: read this file, PROGRESS.md and docs/PLAN.md,
`git pull`, then carry on from the first unchecked box.

The owner starts sessions with
`cd ~/shop-hub && claude --add-dir ~/arcade-tracker --add-dir ~/gatbox`.

## How to work here

- The owner is a professional arcade tech, fluent in Arch, Proxmox, systemd, Postgres and git.
  Be direct, flag tradeoffs, skip preamble.
- **One phase per session.** Before code: a short design note (models, migrations, routes,
  templates, risks), then wait for the owner's OK.
- **Before every push:** show the diff summary, the test results and the privacy-check output
  (`scripts/privacy_check.sh`), even when clean. The owner says when to push.
- Unsure whether something is safe for real data or real money records? Stop and ask.
- **You never touch a host.** Anything that runs in an LXC, on the Postgres host or on
  Cloudflare is written as exact commands, marked ⏸, and then you stop.
- Where this file and docs/PLAN.md differ, this file wins. Where the reference repos disagree
  with either, say what you found.
- Phase flow: design note → OK → code + tests → diff, tests, privacy output → OK → push/PR →
  owner deploys (⏸). Tick PROGRESS.md as you go.

## Reference repos (READ-ONLY)

`~/arcade-tracker` and `~/gatbox` are references. Never edit, commit, branch or push in them.
Port patterns by copying and adapting; never import from them.

- arcade-tracker: `app/models/device.py` (gbx_ tokens), `app/routes/api_v1.py`,
  `app/utils/decorators.py` (requires_role, requires_device), `app/extensions.py` (limiter),
  `deploy/deploy.sh`, `deploy/RUNBOOK.md`, `deploy/listen.conf.example`,
  `arcade-tracker.service`, `contract/v1/` + `scripts/contract_checksums.py` +
  `tests/test_api_v1_contract.py`. Its live DB is PostgreSQL on another host. **Never point
  anything at the tracker's database.**
- gatbox: `docs/contract/v1/` (the client's copy), `backend/gatbox-sync` (what the hub must
  satisfy), design tokens in its `CLAUDE.md`, `BRINGUP.md` (the PROGRESS.md style).

## Decided (challenge only with evidence)

- Flask app factory + Jinja, server-rendered, mobile-first, plain JS, no framework, **no
  CDN**: every asset (Chart.js, fonts) is vendored under `app/static/vendor/`.
- **PostgreSQL only**, in dev, tests, CI and prod. Prod: database `shop` on the owner's
  existing Postgres host. Roles (owner's OK, 2026-10-08): `shop_owner` owns every object and
  runs migrations (`MIGRATE_DATABASE_URL`); the app logs in as `shop`, a member of the NOLOGIN
  group `shop_app`, which gets only the grants the migrations give it (no DDL, so it can't
  disable a trigger). Every new table needs `grant_app()` in its migration and an entry in
  `tests/test_schema.py`'s `EXPECTED_GRANTS`.
- The users table is `app_user` (`user` is reserved and `SELECT * FROM user` lies in psql).
- SQLAlchemy 2 + psycopg 3 (`postgresql+psycopg://`), Flask-Migrate, Flask-Login, Flask-WTF
  (CSRF everywhere except token routes), Flask-Limiter, pyotp (TOTP), segno (QR), WeasyPrint
  (PDF), waitress.
- Python 3.13 (Debian 13 LXC).
- **Money is `NUMERIC(12,2)` and `Decimal` end to end.** Quantities `NUMERIC(12,3)`, tax
  rates `NUMERIC(7,6)`. A float near money is a bug; a test greps for it.
- `timestamptz`, stored in UTC. Display timezone from settings (`SHOP_TZ`), never hardcoded.
  (Deliberately unlike the tracker's naive-UTC columns.)
- Every table: `created_at`, `updated_at`, `created_by`.
- **Gapless document numbers** via a `doc_counter` row locked in the issuing transaction
  (`UPDATE … SET last = last + 1 RETURNING last`). Never a Postgres sequence for a document
  number.
- **Issued financial records are immutable, enforced by Postgres triggers** created in
  migrations. Corrections are voids, credit memos or reversing payments.
- **An audit trigger on every financial table** writes `audit_log`. The app runs
  `SET LOCAL shop.user_id = …` in each request's transaction.
- Customer documents (estimate, claim ticket, service report, invoice, receipt, statement,
  credit memo, packing slip) are **print-first**: white page, black text, house fonts, one
  accent. The app UI uses GATBOX's synthwave tokens (below).
- Customer links `/d/<token>`: 32 random bytes, read-only, revocable, expiring,
  `X-Robots-Tag: noindex`, rate-limited.
- The GATBOX API is **contract v1**, vendored byte-identical from arcade-tracker with the same
  checksum test. Additions (`capabilities: ["jobs"]`, roster `model_key`, `phase`) are
  additive and optional. **Never change v1's request shapes.**

## Hard rules

1. Never commit real customer data, a database, a dump, `.env`, uploaded files, tokens or
   secrets. Tests and seed data are synthetic: made-up names, `555-01xx` phone numbers,
   reserved domains (`example.com`, `.test`) for email.
2. Schema changes only through Flask-Migrate. Hand-review every autogenerated migration. Put
   triggers and functions in migrations explicitly. Data migrations are separate from schema
   migrations. Every migration downgrades cleanly or says in its docstring why it can't.
   The migrations build the schema from an empty database (unlike the tracker's).
3. Tests run against a real throwaway Postgres: a fresh database per test session, dropped
   after. **Never SQLite**: locking, triggers and NUMERIC behave differently.
4. Run as the owner's normal user. Ask before installing anything (pacman, podman, uv
   packages, pip into a new env) and before any sudo.
5. One branch per phase (`phase/N-name`), PR into `main`, CI green before merge. Never
   force-push `main`.
6. Commit with `git c` (alias for `TZ=UTC git commit`), as `digitalunconciousness` with the
   GitHub noreply email (set in this repo's local git config, not global).
7. Before every push, run `scripts/privacy_check.sh` and show the output. The same check runs
   in `.githooks/pre-push` (`git config core.hooksPath .githooks`). Owner-specific patterns
   live in the git-ignored `.githooks/patterns.local`; never copy its values anywhere.
8. The barcade's name and data never appear in this repo. Refer to it as "the barcade".
9. Library versions are pinned in `requirements.in` and compiled with hashes into
   `requirements.txt` (`uv pip compile --generate-hashes`; header has the exact command);
   deploy installs with `--require-hashes`. When pinning, look up current releases rather
   than trusting memory, and say what was pinned and why.

## Design tokens (app UI only, not customer documents)

From GATBOX: synthwave, neon on a deep violet gradient, scanlines, mono type, glow only on
badges / active states / headline edges. bg `#150a28`, bg2 `#1f0f3d`, panel `#1f1240`,
border `#3d1e6b`, magenta (primary) `#ff2e9f`, cyan (secondary) `#7dfaff`, lime (OK)
`#5ef2b0`, red (danger) `#ff4d6d`, amber (warn) `#ffb74d`, text `#ece3ff`, dim `#9080b0`.
Body `linear-gradient(180deg, bg2, bg)`. Fonts: Chakra Petch (display), Share Tech Mono
(body), served locally from `app/static/fonts/`.

## Repo layout (target; built from Phase 0)

```
app/                    Flask app: create_app() in __init__.py, config, extensions
  models/               SQLAlchemy 2 models, one module per area
  <area>/               blueprints: auth, settings, customers, assets, work, billing, parts,
                        purchasing, books, public (/d/), api_v1 (GATBOX)
  templates/            app UI (synthwave) and templates/docs/ (print-first documents)
  static/               css, js, fonts, vendor/
migrations/             Flask-Migrate (Alembic); triggers and functions live here
contract/v1/            vendored from arcade-tracker, byte-identical, checksum-tested
deploy/                 deploy.sh, RUNBOOK.md, listen.conf.example, shop-hub.service,
                        backup units, cloudflared ingress snippet
scripts/                create_owner.py, backup.sh, restore_drill.sh, privacy_check.sh,
                        contract_checksums.py, devdb.sh
tests/                  pytest, against a throwaway Postgres database
docs/PLAN.md            the agreed plan
PROGRESS.md             where we are; resume from the first unchecked box
.githooks/              pre-push (committed), patterns.local (git-ignored)
```

## Dev environment

- Python: uv-managed 3.13 venv at `~/.venvs/shop-hub` (outside the checkout).
  `source ~/.venvs/shop-hub/bin/activate`.
- Dev Postgres: see PROGRESS.md "Decisions" for which one was chosen. `scripts/devdb.sh
  start|stop|status|psql` (Phase 0) wraps it, listening on `127.0.0.1:5433` only, data under
  `~/.local/share/shop-hub/`.
- Config comes from the environment (`.env` locally, git-ignored; `.env.example` committed):
  `DATABASE_URL`, `MIGRATE_DATABASE_URL`, `SECRET_KEY`, `SHOP_BASE_URL`, `SHOP_TZ`
  (display zone, env only; settings shows it read-only). `scripts/devdb.sh start && init`
  makes the dev roles; `scripts/devdb.sh env` prints the lines for `.env` (passwords go
  through `PGPASSFILE`, never a URL).

## Tests

```
source ~/.venvs/shop-hub/bin/activate
scripts/devdb.sh start
pytest
```

The test session connects with `SHOP_TEST_ADMIN_URL` (default: the dev server's `postgres`
database), creates `shop_test_<random>`, runs the real migrations into it (so triggers are
tested), and drops it at the end. It refuses to run if `DATABASE_URL` names anything that
is not a `shop_test_` database. CI does the same against a `postgres` service container of
the prod major version. The backup/restore test needs pg 17+ client tools (PATH or
`SHOP_PG_BIN`) and skips, saying why, without them.

## Cloud sessions (claude.ai/code)

A cloud session gets a fresh Ubuntu 24.04 VM with only this repo cloned. The environment's
setup script is `docs/cloud-setup.sh` (pasted into the environment settings, with the
`SHOP_PATTERNS` variable). Differences from the owner's machine:

- Reference repos are at `/opt/refs/arcade-tracker` and `/opt/refs/gatbox` (read-only
  clones, not attached to the session). Same rules as `~/arcade-tracker` / `~/gatbox`.
- First thing each session, set what a fresh clone lacks:
  ```
  git config core.hooksPath .githooks
  git config user.name digitalunconciousness
  git config user.email 240414701+digitalunconciousness@users.noreply.github.com
  git config alias.c '!TZ=UTC git commit'
  ```
- `scripts/privacy_check.sh` reads `~/.config/shop-hub/patterns.local` (written by the
  setup script). If it reports the file MISSING, stop and tell the owner; never push without it.
- Python 3.13 is `python3.13` (deadsnakes); make the venv with
  `python3.13 -m venv ~/.venvs/shop-hub`. Postgres 17 runs in docker via `scripts/devdb.sh`
  (start `dockerd` first if `docker info` fails); the VM's preinstalled PostgreSQL 16 is not
  used, and its pg_dump 16 can't dump a 17 server, so point `SHOP_PG_BIN` at wrappers that
  `docker run --rm -i --network host postgres:17 <tool>` (kept in the scratchpad).
- `scripts/privacy_check.sh` skips the login-name check when run as root (the cloud user),
  since "root" is a generic word; home paths and every other check still run.
- Nothing local carries over: no `.env`, no dev database, no files. Data in the cloud VM is
  synthetic only.
