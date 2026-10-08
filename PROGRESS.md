# shop-hub progress

Spec: `docs/PLAN.md` (phases table) and `CLAUDE.md` (decisions and hard rules).

Resume from the first unchecked box. ⏸ = waiting on the owner (a host, Cloudflare, a phone, a
decision). Each phase: design note → OK → code + tests → diff, tests, privacy output → OK →
push/PR → owner deploys (⏸).

## Session 1: set up (no app code)

- [x] Repo: `git init -b main`, remote, plan copied to `docs/PLAN.md` (it came as an upload,
      not `~/shop-hub-plan.md`), `.gitignore`, local identity (noreply), `git c` alias,
      `core.hooksPath .githooks`
- [x] `scripts/privacy_check.sh` + `.githooks/pre-push`, tested against a scratch repo
      seeded with each violation
- [x] CLAUDE.md, PROGRESS.md
- [x] `.githooks/patterns.local`: owner/partner names, the barcade, plus GATBOX's 9 values
      (17 patterns; local only, mode 600)
- [x] GitHub: SSH already works; the repo is `ArcadeTech-Tracker` (remote repointed), empty
- [x] `ArcadeTech-Tracker` stays **public** (owner, 2026-10-07); LICENSE: all rights reserved
- [x] Prod Postgres: **17.9** (Debian 13 package, `17.9-0+deb13u1`). Debian 13's own
      `postgresql-client` is 17, so the shop LXC needs no PGDG repo for pg_dump
- [x] Debian 13 LXC: Python **3.13.5**, pg_dump 17.11 (owner, 2026-10-08, on the shop LXC)
- [x] Dev Postgres: rootless podman (owner installed 6.1.2), `postgres:17`, on 127.0.0.1:5433;
      `scripts/devdb.sh` (Phase 0) also drives docker for cloud sessions
- [x] uv venv at `~/.venvs/shop-hub` (Python 3.13.15), empty until Phase 0 pins
- [x] "Decisions for you" (PLAN.md) answered (table below)
- [x] First commits pushed to `main` (owner's go)
- [x] Cloud handoff: `docs/cloud-setup.sh` + CLAUDE.md "Cloud sessions"
- [ ] ⏸ Owner: GitHub email privacy (deferred by owner)

## Phase 0: Foundation (branch `phase/0-foundation`)

- [x] Design note → OK (owner, 2026-10-08; cloud session 2)
- [x] Pin library versions (looked up on PyPI 2026-10-08), `requirements.in` → hash-pinned
      `requirements.txt` / `requirements-dev.txt`
- [x] App factory, config from env, extensions (limiter keyed on `CF-Connecting-IP` only from loopback)
- [x] Postgres + Flask-Migrate; first migration builds from empty; downgrade tested
- [x] `doc_counter` + gapless issue helper (test: rollback leaves no gap; concurrent issuers serialize)
- [x] `audit_log` + trigger function + `set_config('shop.user_id', …, true)` per transaction
- [x] Users, roles (owner / tech / viewer), scrypt, TOTP required for owners, login rate limit
- [x] Settings (business details; `SHOP_TZ` from env, shown read-only)
- [x] Float-near-money grep test; no-external-assets test (+ no inline script/style for the CSP)
- [x] `scripts/create_owner.py` (prompts; nothing on argv) + `scripts/reset_2fa.py`
- [x] `deploy/`: RUNBOOK.md, deploy.sh with `--check`, shop-hub.service, listen.conf.example,
      cloudflared ingress example, `sql/create_roles.psql`
- [x] `scripts/backup.sh` (pg_dump -Fc + files; 14 daily / 12 monthly / 7 yearly) + timer
- [x] `scripts/restore_drill.sh` + monthly timer
- [x] GitHub Actions with a postgres service container (manual-only since 2026-10-08; see Decisions)
- [x] Diff, tests, privacy output → OK → PR #1 (privacy check clean with 17 patterns)
- [ ] ⏸ Exit: both owners log in with 2FA from phones over the tunnel; `deploy.sh`
      round-trips; the restore drill passes

## Phase 1: Customers & assets (branch `phase/1-customers-assets`)

- [ ] Design note → ⏸ OK
- [ ] Customers, contacts, sites, comm log
- [ ] Assets (machines and boards, parent/child, `s-` tags never reused), asset events
- [ ] Labels (`/g/<tag>` QR, label list), global search
- [ ] Diff, tests, privacy output → ⏸ OK → PR
- [ ] ⏸ Exit: a printed asset label opens the asset on a phone

## Phase 2: Price book & parts (branch `phase/2-pricebook-parts`)

- [ ] Design note → ⏸ OK
- [ ] Services, job templates, labor policy, markup tiers
- [ ] Parts, lots, stock ledger (append-only), counts, vendors, bin labels
- [ ] Diff, tests, privacy output → ⏸ OK → PR
- [ ] Exit: on hand = sum of moves (tested); a count adjustment leaves an audit row

## Phase 3: Work (branch `phase/3-work`)

- [ ] Design note → ⏸ OK
- [ ] Estimates: lines, NTE, deposit, revisions, approval via `/d/<token>` on a phone
      (finger signature canvas → PNG + typed name; time, IP, UA logged)
- [ ] WOs, jobs (3C, intake), lines, timer, part issue / reserve / customer-supplied
- [ ] Manual readings; service report with as-found → as-left, spec and pass
- [ ] Photos, claim ticket and service report PDFs, appointments + .ics feed
- [ ] Diff, tests, privacy output → ⏸ OK → PR
- [ ] ⏸ Exit: estimate approved on a phone through a link; converts; timer runs; report prints

## Phase 4: Billing (branch `phase/4-billing`)

- [ ] Design note → ⏸ OK (must settle: invoicing taxable parts with no permit; see Notes)
- [ ] Tax jurisdictions; invoice issue (snapshot, number, frozen PDF + sha256, warranty dates)
- [ ] Payments, allocations, deposits, credit memos, voids, reversals, statements, receipts
- [ ] Immutability triggers
- [ ] DB tests: issued lines can't be updated/deleted; gapless across a rolled-back issue;
      tax half-up on taxable subtotal at the frozen rate; partial / over / reversed payments
- [ ] Diff, tests, privacy output → ⏸ OK → PR
- [ ] ⏸ Exit, then enter the partner's first real invoice with its true issue date

## Phase 5: Purchasing (branch `phase/5-purchasing`)

- [ ] Design note → ⏸ OK
- [ ] Shopping list, POs, lines tied to jobs, shipments, vendor RMAs
- [ ] Carrier tracking URL templates in settings (each verified by web search when seeded)
- [ ] Receive → lot + receive move + reservation + WO out of `waiting_parts` (tested)
- [ ] Diff, tests, privacy output → ⏸ OK → PR → ⏸ deploy

## Phase 6: Dashboard & books (branch `phase/6-dashboard-books`)

- [ ] Design note → ⏸ OK
- [ ] Dashboard, reports, CSV exports, expenses, mileage, partner ledger, sales-tax filings
- [ ] Year-end package + 1099 reconciliation; reconciles to the dashboard to the cent on a
      synthetic year (tested)
- [ ] Diff, tests, privacy output → ⏸ OK → PR → ⏸ deploy

## Phase 7: GATBOX link, hub side (branch `phase/7-gatbox-link`)

- [ ] Design note → ⏸ OK
- [ ] Devices + tokens, contract v1 vendored byte-identical + checksum test
- [ ] Health (tracker keys + `"app": "shop-hub"`, `"capabilities": ["jobs"]`), roster
      (`model_key`), ingest, open jobs per asset, phase tagging on the job page
- [ ] Contract examples posted twice → created, then duplicate (slugs swapped for `s-` tags
      at test time); bad token → 401; unknown slug → rejected
- [ ] Diff, tests, privacy output → ⏸ OK → PR → ⏸ deploy
- [ ] ⏸ Mint a device token; `/etc/gatbox/hubs.d/shop.conf` on the Pi. No GATBOX code here
      (that's Prompt B / plan Phase 8)

## Decisions

| Date | Decision | Why |
|---|---|---|
| 2026-10-07 | Business/display name **ArcadeTech Tracker**; repo, unit, paths, DB stay `shop-hub` / `shop` | Owner. Keeps clear of the barcade's `arcade-tracker` repo, unit and `/opt` path |
| 2026-10-07 | The barcade is referred to as "the barcade" in the repo; its name lives only in `patterns.local` | Workplace data stays out of git; PLAN.md's "Uptown" (wrong anyway) replaced |
| 2026-10-07 | Privacy check checks every commit in the range (`git log -p`), not only the net diff | A secret added then removed is still in history |
| 2026-10-07 | Synthetic emails allowed only on reserved domains (example.com/.org/.net, .test, .invalid) plus the two noreply addresses | Hard rule allows only the noreply email; reserved names can't belong to anyone |
| 2026-10-07 | Repo stays **public**; LICENSE is all rights reserved, holder `digitalunconciousness` | Owner. Public means the privacy check is the only barrier, so it runs on every push |
| 2026-10-07 | GitHub repo is `ArcadeTech-Tracker`; local dir, unit, paths, DB stay `shop-hub` / `shop` | Owner created it under that name |
| 2026-10-07 | Dashboard: cash basis by default, accrual toggle | Owner (confirm with CPA) |
| 2026-10-07 | Labor: actual time rounded up to 0.25 h, 0.5 h minimum per job; flat rate where a template prices the job | Owner |
| 2026-10-07 | Warranty defaults: **365 days** on parts we supply and on labor; **90 days on labor** when the customer supplied the part (the customer's part itself: none). Per service/part, editable | Owner |
| 2026-10-07 | Profit split **50/50** (equal) | Owner |
| 2026-10-07 | Cloudflare Access (email one-time code) in front of everything except `/api/v1/*` and `/d/*` | Owner. Runbook covers it in Phase 0 |
| 2026-10-07 | Contract v2, when needed, is owned by GATBOX | Owner |
| 2026-10-07 | Synthetic test emails on reserved domains (example.com, .test) allowed | Owner |
| 2026-10-07 | Dev Postgres: podman `postgres:17`; Python env: uv venv at `~/.venvs/shop-hub` | Owner; matches prod 17.9 |
| 2026-10-07 | Phase 0 onward may run in claude.ai/code cloud sessions | Owner. Setup in `docs/cloud-setup.sh` |
| 2026-10-07 | **No Oklahoma sales tax permit yet**; tax switched off until the business picks up | Owner. Phase 4 must decide how parts lines behave with tax off |
| 2026-10-08 | Two DB roles: `shop_owner` (owns objects, migrations) and `shop` in group `shop_app` (DML only) | Owner. A table owner can DISABLE TRIGGER; the app must not be able to |
| 2026-10-08 | Display time zone from `SHOP_TZ` (env) only; settings page shows it read-only | Owner. One source of truth |
| 2026-10-08 | No 2FA recovery codes: owners reset each other; `scripts/reset_2fa.py` on the LXC as last resort | Owner |
| 2026-10-08 | Requirements hash-pinned (`uv pip compile --generate-hashes`), installed with `--require-hashes` | Owner. A swapped PyPI file can't land on the LXC |
| 2026-10-08 | SQLAlchemy 2.0.54, not 2.1.x | 2.1.0 went GA 2026-09-24 with four patches since; Flask-SQLAlchemy 3.1.1 predates 2.1 |
| 2026-10-08 | Users table named `app_user` | `user` is reserved; `SELECT * FROM user` returns the current role in psql |
| 2026-10-08 | `audit_log` exempt from created_at/updated_at/created_by (`at`, `user_id` instead) | Append-only; never updated |
| 2026-10-08 | Green CI is not a merge requirement; `ci.yml` is manual-only (`workflow_dispatch`) | Owner. Actions won't start jobs on this account (runner never assigned); the local pytest + privacy check before each push is the gate |

## Notes

### Session 1 (2026-10-07)

- Plan written against GATBOX `9686bfa`; GATBOX is now at `9054206` (one commit later).
  Tracker is at `341197a`, as written. Contract v1: the tracker's copy and GATBOX's
  `docs/contract/v1/` are byte-identical today.
- Workstation: Python 3.13.15 (uv-managed) available; uv 0.12.19; pacman `postgresql` 18.6
  already installed (service inactive); podman and docker not installed; shellcheck not
  installed.
- Prod Postgres confirmed **17.9** on Debian 13 (it lives in its own container on the Proxmox host).
- GitHub web-UI merges in both reference repos are authored with the owner's personal email
  and a local offset (already public). Fix: GitHub Settings → Emails → keep private + block
  pushes that expose it. Check the first shop merge's timestamp; if not UTC, merge locally
  with `git c` and push.
- Tax with no permit (Phase 4 design question): the plan's rule "refuse to issue a taxable
  line with tax off" would block every invoice with parts, since parts default to taxable.
  Options to decide then: block; or issue with tax 0 and flag the lines. Ask the CPA whether
  selling parts at all needs the permit first.

### Session 2 (2026-10-08, cloud)

- Phase 0 code written on `phase/0-foundation`: 84 tests green against Postgres 17 (docker),
  ruff and shellcheck clean.
- Cloud environment gaps (owner to fix in the environment settings):
  `SHOP_PATTERNS` unset, so `privacy_check.sh` reports patterns.local MISSING and nothing
  can be pushed from this session; `/opt/refs` absent (the clones of the private reference
  repos fail silently). `docs/cloud-setup.sh` now takes an optional `REFS_TOKEN` and warns
  instead of failing silently.
- Ported files (role decorator, limiter, deploy.sh, RUNBOOK, unit, listen.conf) were written
  from scratch, not adapted from arcade-tracker: diff them against the tracker's.
- CI pins `actions/checkout@v7` / `actions/setup-python@v7` by tag, not SHA: this session
  can't read other repos. Pin to SHAs with `git ls-remote --tags` on the workstation.
- The VM's pg_dump is 16; backups were tested through `docker run postgres:17` wrappers
  (`SHOP_PG_BIN`); CI does the same.
- First deploy attempt: `deploy/sql/create_roles.sql` was never committed (`*.sql` is
  git-ignored to keep dumps out), so the runbook's copy step failed on a fresh clone.
  Renamed to `create_roles.psql`; a test now fails if anything under app/, deploy/,
  migrations/, scripts/, tests/ or .github/ exists on disk but is git-ignored. The runbook
  uses `runuser -u postgres` (Debian LXCs have no sudo) and reads the passwords with
  `read -rs` instead of putting them on the command line.
- The prod Postgres cluster was initialised without a UTF-8 locale, so `CREATE DATABASE
  shop` came out SQL_ASCII (psycopg then returns bytes and SQLAlchemy fails to connect).
  The empty shop databases were recreated UTF8 (`TEMPLATE template0 ENCODING 'UTF8'
  LOCALE 'C.UTF-8'`); `create_roles.psql` now does that explicitly, and migrations refuse
  a non-UTF8 database with a one-line error.
