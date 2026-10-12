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
      - [x] owner signed in with 2FA over the tunnel (2026-10-08)
      - [x] `deploy.sh` round-trip: dump → pull → hash-checked install → migrate → restart →
            healthy (2026-10-08)
      - [x] restore drill PASS (5 tables, 7 triggers); backup + drill timers enabled (2026-10-08)
      - [x] partner's user created (2026-10-08); ⏸ their first sign-in with 2FA is still to do

## Phase 1: Customers & assets (branch `phase/1-customers-assets`)

- [x] Design note → OK (owner, 2026-10-08)
- [x] Customers, contacts, sites, comm log (append-only); the shop seeded as its own customer
- [x] Assets (machines and boards, parent/child, `s-` tags never reused), asset events
- [x] Global search; labels built, then removed in Phase 2 (owner: no QR labels on customer
      machines; `/g/<tag>` stays as a lookup)
- [x] Diff, tests, privacy output → OK → PR #5 (merged, deployed 2026-10-08)
- [x] ⏸ Exit (revised 2026-10-08, on site, all from the phone; passed 2026-10-08): add the
      customer, a contact and a site; add the machine(s), and any board pulled with "Inside" set
      to its machine; log a comm entry; set "In the shop" + shelf on anything that leaves with
      you; back home, search the phone number typed in a different format and find it all

## Phase 2: Price book & parts (branch `phase/2-pricebook-parts`)

- [x] Design note → OK (owner, 2026-10-08)
- [x] Services (seeded off at $0.00), job templates, labor policy, markup brackets (Settings →
      Pricing)
- [x] Parts, lots, stock ledger (append-only), receive / adjust / scrap, count sheet, vendors;
      bin labels deferred (owner)
- [x] Diff, tests, privacy output → OK → PR #6 (merged, deployed 2026-10-08)
- [x] Exit: on hand = sum of moves (`test_stock.py`, 300 randomized moves); a count adjustment
      leaves an audit row with its user (`test_stock.py`, `test_parts.py` count sheet)
- [ ] ⏸ Owner sets the service rates in the price book and switches them on

## Phase 3: Work (branch `phase/3-work`)

- [x] Design note → OK (owner, 2026-10-08): two PRs, 3a then 3b
- **3a: work orders**
  - [x] WOs, jobs (3C, intake), lines, timer, part issue / reserve / customer-supplied
  - [x] Manual readings; service report with as-found → as-left, spec and pass
  - [x] Photos (EXIF stripped), claim ticket and service report PDFs, work board
  - [x] Diff, tests, privacy output → OK → PR #7 (merged)
  - [x] ⏸ Owner deployed (migration 0007)
  - [ ] ⏸ Claim-ticket and warranty terms written in Settings (printed on those documents;
        blank until then)
- **3b: estimates and links** (branch `phase/3b-estimates`, on top of 3a)
  - [x] Estimates: lines, NTE, deposit, revisions, approval via `/d/<token>` on a phone
        (finger signature canvas → PNG + typed name; time, IP, UA logged); convert to a WO
  - [x] Appointments + .ics feed (`/d/cal/<token>.ics`)
  - [x] ⏸ Cloudflare Access bypass for `/d/*` in place (RUNBOOK §7, app 3): `/d/x` shows the
        app's own "isn't available" page (owner, 2026-10-09)
  - [x] Diff, tests, privacy output → OK → PR #8 (merged)
  - [x] ⏸ Owner deployed (migration 0008) and wrote the estimate terms (links can't be sent
        without them)
  - [x] PR #9: log CSRF rejections with their reason and explain them to the user
- [x] ⏸ Exit: estimate approved on a phone through a link; converts; timer runs; report prints
      (passed, owner, reported 2026-10-12)

## Phase 4: Billing (branch `phase/4-billing`)

- [x] Design note → OK (owner, 2026-10-12): two PRs, 4a invoices then 4b money in; tax off
      with a setting (see Decisions)
- **4a: invoices**
  - [x] Tax jurisdictions (rates entered by the owner from the OTC; none seeded); "collect
        sales tax" switch, off; a site's jurisdiction; the shop's own as the default
  - [x] Issue from a WO: tick the lines (bill part now, the rest later), snapshot, gapless
        `INV` number in the issuing transaction, invoice PDF and service report frozen by
        sha256, warranty date per line, backdating with a reason
  - [x] Fixed-price lines on estimates and WOs (the owner's set-price quotes, asked for
        2026-10-12): one labor line, qty 1, a scope list printed under it; time still logged,
        so the job page shows the effective hourly rate
  - [x] Counter-sale WO kind
  - [x] Immutability triggers: `invoice_frozen`, `invoice_line_frozen`,
        `wo_line_billed_guard`; void (reason, nothing paid) frees the lines to bill again
  - [x] Invoice link `/d/<token>`: the invoice, balance, how to pay, the frozen PDFs
  - [x] Restore drill re-hashes every issued PDF against `pdf_sha256` / `report_sha256`
  - [x] DB tests: issued lines/invoices can't be updated or deleted (app role and schema
        owner); gapless across a rolled-back issue; tax half-up at the frozen rate; Postgres
        checks subtotals and tax at the moment of issue
  - [x] Diff, tests, privacy output → OK (incl. the fixed-price design) → PR #10 (merged)
  - [ ] ⏸ Owner deploys (migration 0009); writes "How customers pay you" in Settings → Business
- **4b: money in** (branch `phase/4b-payments`)
  - [x] Payments (receipts, reversals, refunds), applied to invoices or left as credit on
        account; deposits on estimates, applied when the job is invoiced
  - [x] Credit memos (`CM` numbers, frozen PDF, tax back at the invoice's rate), receipts,
        statements with aging, the customer's account on their page, receipts on the link
  - [x] Triggers: `payment_guard`, `payment_reversal_complete` (deferred), `allocation_guard`,
        `allocation_apply`, `credit_memo_frozen`, `credit_memo_line_frozen`; `invoice_frozen`
        now checks paid and credited against what's applied
  - [x] DB tests: partial, over and reversed payments; refunds; credit memos; balances kept by
        trigger and checked against allocations after every step of a randomized run
  - [x] Diff, tests, privacy output → OK → PR #11 (merged)
  - [ ] ⏸ Owner deploys (migration 0010)
- [ ] ⏸ Exit: editing an issued invoice line fails in the database (4a, tested); a partial +
      final payment closes the invoice (4b, tested); numbers gapless across a forced rollback
      (4a, tested). Then enter the partner's first real invoice with its true issue date

## Phase 5: Purchasing and parts links

- [x] Design note → OK (owner, 2026-10-12, all nine decisions as recommended): two PRs, 5a
      parts links then 5b purchasing; vendor RMAs deferred (see Decisions)
- **5a: parts links** (branch `phase/5a-links`)
  - [x] Saved parts links (`part_link`): paste or share a page, vendor matched by domain,
        tracking tags stripped, http(s) only (app and a CHECK), never fetched by the server;
        search by every word; where each link went (estimates, WOs, sends)
  - [x] Android: web manifest with a share target (`/links/new`), icons, installable from
        Chrome's menu; the manifest is fetched with Access's cookie
  - [x] "Parts you'll buy" on estimates (`estimate_part_link`: title, URL and price copied,
        frozen with the estimate by `estimate_child_frozen()`, in the approval hash), on the
        PDF and the customer's page, outside our total
  - [x] Convert → customer's-part lines awaiting arrival (`wo_line.awaiting`,
        `part_link_id`); the WO waits on them; Arrived puts it back In progress once
        nothing else is on its way; awaiting lines aren't offered for billing
  - [x] Send links from a WO: the phone's share sheet, then a comm-log entry
  - [ ] Diff, tests, privacy output → ⏸ OK → PR → ⏸ deploy (migration 0011)
  - [ ] ⏸ Exit: share a page from Chrome into the app on the phone and save it; add it to an
        estimate; the customer's page shows it tappable; after approve and convert the WO
        waits on it, and Arrived clears it
- **5b: purchasing** (branch `phase/5b-purchasing`)
  - [ ] Shopping list (reorder suggestions, Order for this job), POs (number at ordered),
        lines tied to jobs, receipts (append-only), shipments
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
| 2026-10-08 | Tunnel is locally managed (`cloudflared tunnel create`, `/etc/cloudflared/config.yml`), cloudflared in the shop LXC | Owner's setup; loopback peer keeps per-client rate limits. RUNBOOK step 7 matches |
| 2026-10-08 | Asset tags `s-NNNN-name-slug`: number from `asset_tag_seq` (gaps fine, never reused), name part frozen at creation, ≤ 24 chars, tag ≤ 40; a trigger refuses tag changes | Owner. Printed labels stay valid forever. Contract v1's slug limits not yet checked (refs unavailable): verify in Phase 7 |
| 2026-10-08 | Labels: a copy-to-clipboard list (URL for the QR, tag for the text), unprinted first, "mark printed" | Owner types labels into the Katasymbol phone app; no bulk printing available. **Superseded below** |
| 2026-10-08 | Customers, contacts, sites, assets audited; comm log and asset events append-only (INSERT/SELECT); nothing deletable | Owner |
| 2026-10-08 | Techs create and edit customers and assets (`EDIT_ROLES`); viewers read only | Owner. Intake is tech work |
| 2026-10-08 | **No QR labels on customer machines.** Label page, QR panel and `asset.label_printed_at` removed (migration 0005); tags and `/g/<tag>` stay. Inventory (bin) labels maybe later | Owner. Labelled machines are the barcade's way, not the shop's |
| 2026-10-08 | `unit_cost` is NUMERIC(14,4) on parts and lots only; prices and every document amount stay NUMERIC(12,2), rounded half-up | Owner. A $0.012 resistor in cents is a 17% error |
| 2026-10-08 | Seven services seeded **inactive at $0.00**; the owner sets rates in the price book. A labor/fee/trip/diagnostic service can't be switched on at $0.00 (sublet and discount can: priced per job) | Owner. No invented prices |
| 2026-10-08 | Costs and margins: owner and viewer see them (`COST_ROLES`); techs see sell prices and stock only, can't edit cost or price, and their receipts are costed at the part's default cost | Owner |
| 2026-10-08 | No markup brackets seeded. Brackets are [from, up to); no overlaps (a DB exclusion constraint); markup applies to the part's default cost | Owner |
| 2026-10-08 | On hand is only ever the sum of moves (no `qty_received`/`qty_remaining` columns as the plan had). A trigger refuses a move that takes a lot below zero, serialized per lot by an advisory lock (the app role can't `SELECT … FOR UPDATE` a table it may not UPDATE) | Nothing stored can drift from the ledger |
| 2026-10-08 | Stock found goes into the newest lot at its cost (or a new lot at the default cost if none); missing comes out oldest-first (FIFO) unless a lot is picked | FIFO cost and recall tracing stay true |
| 2026-10-08 | Phase 3 ships as two PRs: 3a work orders, 3b estimates and `/d/` links | Owner. Smaller reviews; 3a is usable on its own |
| 2026-10-08 | No QR on the claim ticket: WO number and asset tag in large text | Owner (same reason as no QR labels on machines) |
| 2026-10-08 | Over the NTE: a red banner and one-tap "Hold for approval" (status waiting_approval); no hard block | Owner. A tech can always record what was actually done |
| 2026-10-08 | Calendar feed (3b) carries the customer's name and the site address | Owner. Needed to drive there; Google will hold it |
| 2026-10-08 | Terms are written in Settings; "Send link" (3b) stays off until the estimate terms exist | Owner. Placeholder text never reaches a customer |
| 2026-10-08 | Labor on a job: flat hours, or actual = `bill_hours(the job's billable timer minutes)`; the minimum applies once per job; one actual-time labor line per job (DB index) | Owner's labor policy, applied per job not per tech |
| 2026-10-08 | Issued parts: one line per lot (FIFO), each at that lot's cost; `stock_move.wo_job_id` links issue and return moves to the job (the plan said `wo_line_id`, but lines can be removed and the ledger can't change) | Recall tracing survives a removed line |
| 2026-10-08 | Warranty printed per line: 365 d on parts we supply and labor; labor on a job with a customer-supplied part 90 d; the customer's part none | Owner's warranty decision (2026-10-07) |
| 2026-10-08 | Estimates: revisions keep the number (`EST-2026-0004 r2`); a new revision replaces an unapproved one (status `superseded`, its links stop). After approval, triggers freeze the estimate, its jobs and lines; only approved → converted may change | Plan: "an approved estimate is frozen", enforced in the database |
| 2026-10-08 | What the customer approves is pinned: the approval form carries a hash of the estimate as shown; a change since is refused ("reload") | A price edited while the customer reads can't be approved unseen |
| 2026-10-08 | Customer links: `secrets.token_urlsafe(32)`, only its sha256 stored; each "Send link" mints a new one; expiry = the estimate's link days (default 30); revocable; view count; bad/expired/withdrawn all give the same 404 | CLAUDE.md `/d/<token>` rules |
| 2026-10-08 | Converting: jobs, labor, fees, sublets and discounts carry over; quoted stocked parts become reservations at the quoted price (issued when used; shortfalls flagged to order); actual-time labor starts at 0 | Parts are issued when used, not when quoted |
| 2026-10-08 | Change orders: a revision of a converted estimate, once approved, raises the WO's NTE and takes it out of waiting_approval | The plan's over-NTE flow |
| 2026-10-08 | Calendar feeds: one live per user, token hashed, made on the Account page; events for appointments the user is on, from 60 days back | Owner: name + address in Google is fine |
| 2026-10-12 | Plain `http://` can't hold a session: the cookie is `Secure`, so a browser drops it and the login form's CSRF check fails ("session token is missing"). Cloudflare's Always Use HTTPS redirect covers it; **no app-side redirect** | Owner. Found when login failed on an `http://` address; works on `https://` |
| 2026-10-12 | Sales tax: a "collect sales tax" setting, **off**. Invoices then say "Sales tax not collected"; taxable lines stay flagged and each invoice shows the untaxed amount at its recorded jurisdiction. Turning it on changes only new invoices | Owner (Phase 4 decision 1). CPA question stands: may parts be sold at retail without the permit? |
| 2026-10-12 | Phase 4 in two PRs: 4a invoices, 4b payments and credit memos | Owner |
| 2026-10-12 | Backdating: an owner may date an invoice earlier with a reason (kept, audited); never in the future. The number is the next in sequence, its year from the issue date, so numbers are gapless but not always in date order | Owner (decision 3): the first real invoices are entered after the fact |
| 2026-10-12 | Owners issue, void and send invoices; viewers read them (with costs); techs get no invoice pages, only a "billed" badge on a WO line | Owner (decision 4) |
| 2026-10-12 | The service report is rendered and frozen (sha256) with the invoice and offered on the invoice link; counter sales have none | Owner (decision 5). It would otherwise change with later WO edits |
| 2026-10-12 | Void: owner only, with a reason, only while nothing is paid; the number stays used and the invoice stays on file; its WO lines are freed to bill again. Otherwise a credit memo or a reversing payment (4b) | Owner (decision 6) |
| 2026-10-12 | Counter sales are a WO kind (`counter`), so stock issue and invoicing are the same path | Owner (decision 7) |
| 2026-10-12 | Payment instructions are typed in Settings → Business by the owner; never in the repo | Owner (decision 8) |
| 2026-10-12 | **Fixed price** (`labor_mode = 'fixed'`, qty 1 by CHECK) with a `scope` text printed as a list. Labor only: parts we sell stay their own lines, so the plan's "never a mixed lump sum" tax rule holds. Labor warranty rules apply (90 d when the customer's parts are on the job) | Owner asked for set-price quotes (a two-cab restoration, customer buys the parts). Built in 4a for the owner's review |
| 2026-10-12 | No saved invoice drafts: the issue page previews from the WO, and a draft row exists only inside the issuing transaction. Corrections happen on the WO lines, which a trigger freezes once billed (`wo_line.invoice_id`) | One place to edit; no draft drifting from the WO. The plan's draft status stays in the CHECK for later |
| 2026-10-12 | `invoice_line.line_total` is a generated column; at issue a trigger checks subtotals = line sums and tax = round(taxable × frozen rate, 2); the app role has only SELECT/INSERT on `invoice_line` | The arithmetic can't disagree with the lines, whoever writes them |
| 2026-10-12 | `invoice_line.wo_line_id` has no foreign key | After a void the WO line may change or go; the frozen copy can't follow (like `stock_move.wo_job_id`) |
| 2026-10-12 | Tax rates frozen on the invoice as text in `tax_jurisdiction` jsonb; tax rounds once per invoice, half-up | JSON numbers would be floats; the plan's rounding rule |
| 2026-10-12 | Invoice links last 365 days (4b: 90 days after the invoice is paid) | Owner (design note): a customer may pay late |
| 2026-10-12 | Payments are append-only, three kinds: receipt (money in), reversal (a receipt undone: the whole amount, once, taking back everything it paid; checked at commit by a deferred trigger), refund (credit paid back out of one receipt or credit memo, never more than is left of it). A receipt partly refunded can't be reversed | Plan: "a bounced check or a refund is a new row; the original is never edited" |
| 2026-10-12 | One `payment_allocation` table for money applied to invoices from receipts, credit memos and (negative) reversals. A trigger sets each invoice's paid, credited, balance and status from it; `invoice_frozen()` refuses any other values; a CHECK ties status to the money | The plan's balance trigger; nothing stored can drift from what's applied |
| 2026-10-12 | Money for one customer moves one transaction at a time (the triggers and the service lock the customer row first) | Two payments at once can't both spend the same credit |
| 2026-10-12 | Credit on account = what receipts and credit memos haven't paid on invoices or had refunded. A deposit is a receipt tied to an estimate. Issuing an invoice applies credit on account by default, deposits for that job first, and the frozen PDF shows it paid | The plan's deposits, "offered pre-ticked" |
| 2026-10-12 | Credit memos go against an issued invoice, with a reason; all credits on an invoice ≤ what it billed (trigger); tax comes back at the invoice's frozen rate when it collected tax; the credit pays down the balance and any rest is credit on account. Frozen PDF, like invoices | Plan; the restore drill re-hashes credit memo PDFs too |
| 2026-10-12 | Receipts and statements are drawn when asked, from append-only rows; a receipt's "still owed after" counts allocations up to its own, so it never changes | Nothing they show can change, so freezing them adds nothing |
| 2026-10-12 | Not in 4b: write-offs (the status is reserved), a bank-deposit date on payments (Phase 6 reconciliation), statement links for customers | Smallest useful 4b; say so if any is needed sooner |
| 2026-10-12 | Phase 5 in two PRs: 5a parts links (customer buys), 5b purchasing. Vendor RMAs deferred: a scrap move with a note until a part actually goes back | Owner (decisions 1 and 8) |
| 2026-10-12 | No crawling of parts sites, and the server never fetches a saved URL; the title comes from Android's share sheet or is typed. Only http(s) links (app and CHECK); utm_*, fbclid, gclid and similar are stripped; one row per URL | Store terms, stale prices and upkeep; no SSRF; a `javascript:` link printed on a customer page would run there |
| 2026-10-12 | "Parts you'll buy" print with the price seen and its date, and an "about" total outside ours ("Prices change, so check before you order") | Owner (decision 2) |
| 2026-10-12 | An estimate's parts-you'll-buy rows copy the link's title, URL and price; frozen with the estimate; in the approval hash only when there are any (older estimates keep the hash they were sent with) | What the customer approved can't change under them |
| 2026-10-12 | Converting turns them into customer's-part lines awaiting arrival (`wo_line.awaiting`); the WO starts on Waiting on parts (a box on the convert form, ticked). Arrived moves a waiting WO back to In progress when nothing else is on its way; the only status the app changes by itself | Owner (decision 3) |
| 2026-10-12 | Send links from a WO: the share sheet first, then a comm-log entry carrying the URLs, so each link's page lists who it was sent to | Owner (decision 4); the comm log is already append-only |
| 2026-10-12 | Purchasing roles (5b): owners order and see costs; techs Order for this job, see what's on order and its tracking, receive, and use the link library; viewers read | Owner (decision 9) |
| 2026-10-12 | 5b: PO number at "mark ordered"; every PO line a catalog part (quick-add); lot cost = the line's unit cost, shipping and tax paid stay on the PO (Phase 6 books them); Order for this job reserves at once | Owner (decisions 5, 6, 7) |
| 2026-10-08 | Photos re-encoded by Pillow (already a WeasyPrint dependency): EXIF incl. GPS dropped, rotation applied, ≤ 2560 px; stored by sha256 under `SHOP_FILES_DIR`, served only to signed-in users | A phone photo's GPS would give away a customer's house |

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
  selling parts at all needs the permit first. **Settled 2026-10-12: a setting, off; issue
  with tax 0, "Sales tax not collected", lines flagged** (see Decisions).

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
- First deploy (2026-10-08) surfaced, all fixed in PRs #2 and #3: the role SQL never
  committed; the SQL_ASCII database; a 2FA QR without the spec quiet zone (Google
  Authenticator couldn't scan it); `flask db heads/current` failing outside `upgrade`
  (`import sqlhelpers`), which also broke `deploy.sh` preflight and could have made its
  rollback text say `downgrade base`. Each now has a test.
- The shop LXC lacks the en_US.UTF-8 locale that `pct enter` passes in, so perl (pg_dump's
  wrapper) warns; harmless. RUNBOOK step 3 has the optional `locale-gen`.

### Session 2, Phase 1 (2026-10-08, same cloud session at the owner's request)

- New customers, contacts and sites were first saved inactive (the new forms have no Active
  box, and an absent checkbox submits False); caught by the tests, fixed, asserted.
- Tests: 125 passed. Phone-viewport walk-through (Playwright): add customer → site → asset,
  label page copy button, search by formatted phone; no CSP or console errors.

### Session 2, Phase 2 (2026-10-08, same cloud session)

- Migrations: 0005 (tables, `stock_move_lot_guard()`, the markup overlap exclusion, labor
  CHECKs, audit and exact grants on all eight tables; drops `asset.label_printed_at`) and
  0006 (seed services, data only). Round-tripped upgrade → downgrade → upgrade; autogenerate
  finds no drift. 0006's downgrade fails once a template line uses a seeded service: by design.
- The plan's `stock_move.wo_line_id` / `po_line_id` and `stock_lot.po_line_id` arrive with
  Phases 3 and 5 as nullable columns; "user" on a move is `created_by`.
- Count sheet: blank boxes are skipped; a part whose stock moved after the sheet loaded is
  skipped and listed; one bad entry saves nothing.
- Phone walk-through (Playwright, iPhone 13 viewport): set a rate, add brackets, vendor, part,
  receive, adjust, count, template; no horizontal scroll, no CSP or console errors. It caught
  two layout problems, both fixed: the signed "change" box (iOS's decimal keypad has no minus;
  Adjust is now Missing/Found + quantity) and a five-column count sheet (now three).

### Session 2, Phase 3a (2026-10-08, same cloud session)

- Migration 0007: eight tables (work_order, wo_job, work_order_tech, wo_line, time_entry,
  reservation, manual_reading, attachment), `stock_move.wo_job_id`, claim and warranty terms
  on shop_setting. The work_order ↔ wo_job foreign keys point at each other, so the warranty
  link is added after both tables. No new trigger functions: one running timer per user and
  one actual-time labor line per job are partial unique indexes; a reading's pass/fail is a
  generated column.
- Customer documents: WeasyPrint, `templates/docs/` + `static/css/doc.css`, no inline CSS
  (3b serves the same HTML under the CSP); the accent colour goes in as its own stylesheet.
  Checked: the override reaches the PDF.
- Tests: 217 passed. Phone walk-through (iPhone 13 viewport): open a WO, timer, 3C + intake,
  issue a part, actual labor, a reading, a photo, stop from the timer bar, both PDFs; no
  horizontal scroll, no CSP or console errors. The lines and readings tables were folded
  to fit a phone after the first pass.

### Session 2, Phase 3b (2026-10-08, same cloud session)

- Migration 0008: estimate, estimate_job, estimate_line, doc_link, appointment,
  appointment_user, calendar_feed; `reservation.unit_price`; estimate terms and link days
  in settings. Trigger functions `estimate_frozen()` and `estimate_child_frozen()` (the
  latter takes a share lock on the estimate, so an edit and an approval can't interleave).
  doc_link isn't audited: every view updates it.
- `/d/` pages share the print-first template with the PDF; no inline CSS or script (the
  accent colour comes from `/d/style/accent.css`, the signature pad is `static/js/sign.js`).
  Rate-limited (60/min views, 10/min decisions), `Referrer-Policy: no-referrer`, noindex.
- Phone walk-through of the exit path (two iPhone 13 contexts, the customer signed out):
  build an estimate, send the link, sign with a finger, approve, convert, timer, service
  report. It caught two real problems, both fixed: the signature pad cleared itself on
  every `resize` (phones fire one when the address bar hides) and a refresh after approving
  re-posted the form (now redirects). Tests: 237 passed.

### Session 3 (2026-10-12): Phase 3 close-out

- Phase 3 exit passed on the owner's phone. Login over `http://` failed with "The CSRF session
  token is missing": the session cookie is `Secure`, so plain http can't keep one. Works on
  https; Cloudflare's Always Use HTTPS redirect is the fix, no app change.
- Intermittent "CSRF token missing" on a second photo upload: not reproduced (repeated uploads,
  Back-then-upload, a 6 MB photo, 40 uploads with parallel page and image requests; an
  over-limit photo gives a plain 413, not CSRF). PR #9 logs the reason and the cookie state on
  every rejection (`journalctl -u shop-hub | grep "csrf rejected"`); the first recurrence on
  https will say whether it's a lost session or a form without its token.

### Session 3, Phase 4a (2026-10-12, same cloud session)

- Owner OK'd the Phase 4 design (all eight decisions as recommended) and asked for set-price
  quotes: the first customer's two cabs are quoted at one price, labor only, parts bought by
  the customer. Built as fixed-price lines (Decisions).
- 4a on `phase/4-billing`: migration 0009 (tax_jurisdiction, invoice, invoice_line; fixed
  labor mode and scope on estimate and WO lines; `wo_line.invoice_id`; counter kind; doc_link
  for invoices; three triggers), `app/billing/`, the invoice template, Settings → Tax, the
  invoice link, the restore drill's PDF re-hash. 255 tests green, none skipped (pg 17 tools
  via docker for the backup tests).
- Phone pass (iPhone 13 viewport): issue page, invoice page, link, customer page and PDF; no
  page scrolls sideways. A too-wide table now scrolls inside its panel (the job page's Cost
  column was already running past it).

### Session 3, Phase 4b (2026-10-12, same cloud session)

- PR #10 (4a) merged. 4b on `phase/4b-payments` from main: migration 0010 (payment,
  payment_allocation, credit_memo, credit_memo_line, invoice.amount_credited; six new
  trigger functions and a replaced `invoice_frozen`), `app/billing/payments.py`, payment and
  credit memo pages, receipt / credit memo / statement documents, the account panel on the
  customer page, the deposit on the estimate page, receipts on the invoice link.
- 274 tests green, none skipped. The randomized test runs 60 random money operations for
  each of three seeds and checks every invariant after each.
- Phone pass: invoice with a payment, credit memo form and page, customer account, a payment
  split over open invoices, the payment page and list; no page scrolls sideways.

### Session 3, Phase 5a (2026-10-12, same cloud session)

- PR #11 (4b) merged; the owner is deploying 0009/0010. Phase 5 design OK'd (all nine
  decisions as recommended); 5a on `phase/5a-links`.
- The VM had been recycled: the dev container's layer was gone and Docker Hub answered 429,
  so `postgres:17` came from `mirror.gcr.io` (tagged locally); the data directory survived.
- Migration 0011: `part_link`, `estimate_part_link`, `wo_line.part_link_id` and `awaiting`
  (CHECKs: only a customer's part); `estimate_child_frozen()` attached to the new estimate
  table. Round-tripped; autogenerate finds no drift. Downgrade refuses while a WO waits on a
  customer's part.
- The package is `app/partlinks/` (`app/links.py` already holds the customer-link tokens).
- 296 tests green, none skipped. Phone pass (Pixel 7 viewport): share target → save, search,
  pick for an estimate and a WO, the customer's estimate page, send links, Arrived; no page
  scrolls sideways, no console errors.
