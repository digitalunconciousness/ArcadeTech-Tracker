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
- [x] ⏸ Owner: GitHub email privacy (deferred by owner; owner, 2026-10-27: done, or not a concern)

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
- [x] ⏸ Exit: both owners log in with 2FA from phones over the tunnel; `deploy.sh`
      round-trips; the restore drill passes
      - [x] owner signed in with 2FA over the tunnel (2026-10-08)
      - [x] `deploy.sh` round-trip: dump → pull → hash-checked install → migrate → restart →
            healthy (2026-10-08)
      - [x] restore drill PASS (5 tables, 7 triggers); backup + drill timers enabled (2026-10-08)
      - [x] partner's user created (2026-10-08); their first sign-in with 2FA (owner, 2026-10-27)

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
- [x] ⏸ Owner sets the service rates in the price book and switches them on (owner, 2026-10-13)

## Phase 3: Work (branch `phase/3-work`)

- [x] Design note → OK (owner, 2026-10-08): two PRs, 3a then 3b
- **3a: work orders**
  - [x] WOs, jobs (3C, intake), lines, timer, part issue / reserve / customer-supplied
  - [x] Manual readings; service report with as-found → as-left, spec and pass
  - [x] Photos (EXIF stripped), claim ticket and service report PDFs, work board
  - [x] Diff, tests, privacy output → OK → PR #7 (merged)
  - [x] ⏸ Owner deployed (migration 0007)
  - [x] ⏸ Claim-ticket and warranty terms written in Settings (printed on those documents;
        blank until then) (owner, 2026-10-13)
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
  - [x] ⏸ Owner deployed (migration 0009; confirmed by the 5a exit, which needs 0011)
  - [ ] ⏸ Owner writes "How customers pay you" in Settings → Business
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
  - [x] ⏸ Owner deployed (migration 0010; confirmed by the 5a exit)
- [x] ⏸ Exit: editing an issued invoice line fails in the database (4a, tested); a partial +
      final payment closes the invoice (4b, tested); numbers gapless across a forced rollback
      (4a, tested). Then enter the partner's first real invoice with its true issue date
      (owner, 2026-10-13: backdating works)

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
  - [x] Diff, tests, privacy output → OK → PR #12 (merged)
  - [x] ⏸ Owner deployed (migration 0011)
  - [x] ⏸ Exit (passed, owner, reported 2026-10-12): share a page from Chrome into the app on the phone and save it; add it to an
        estimate; the customer's page shows it tappable; after approve and convert the WO
        waits on it, and Arrived clears it
- **5b: purchasing** (branch `phase/5b-purchasing`)
  - [x] Shopping list (reorder suggestions, Order for this job), POs (number at ordered),
        lines tied to jobs, receipts (append-only), shipments on POs and WOs
  - [x] Carrier tracking URL templates in Settings → Carriers (looked up by web search when
        seeded; the carriers' sites weren't reachable from the build VM, so check each with
        the first real number)
  - [x] Receive → lot + receive move (both carry the PO line); the job's part was set aside
        when it was ordered; WO out of `waiting_parts` (tested, incl. FIFO between jobs)
  - [x] Diff, tests, privacy output → OK → PR #13
  - [x] ⏸ Owner merges (PR #13) and deploys (migrations 0012, 0013) (owner, 2026-10-13)
  - [x] ⏸ Exit: receiving a line tied to a job reserves it and moves that WO out of
        `waiting_parts` (tested: `test_order_for_job_then_receive_releases_the_wo`). On the
        phone: Order for this job → Make draft POs → Mark ordered → add tracking → Receive
        (owner, 2026-10-27)

## Phase 6: Dashboard & books

- [x] Design note → OK (owner, 2026-10-13, all decisions as recommended except charts:
      Chart.js on screen, server-drawn for print, from one data source)
- **6a: books** (branch `phase/6a-books`, stacked on 5b until PR #13 merges)
  - [x] Expenses (categories near the Schedule C lines, receipts as photo or PDF, paid by
        the business or a partner, voids not deletes); what POs spend booked automatically
        (shipping, sales tax paid, stock not tracked); vendor receipts on POs
  - [x] Mileage: trips (from a WO too) valued at the rate on the day; dated rates, 2026's
        two IRS rates seeded (72.5¢ to June 30, 76¢ from July 1)
  - [x] Partner ledger: owed = expenses + POs on their card + own-vehicle trips − paid back;
        contributions, draws, distributions; voids
  - [x] Sales tax by period and jurisdiction (with what wasn't collected while it's off);
        filings recorded with the period's figures copied in; no overlapping periods
  - [x] Settings → Books (split, set-aside, storage days, internal labor cost, 1099
        threshold, mileage rates, categories); restore drill re-hashes receipts
  - [x] Diff, tests, privacy output → OK → PR #14 (merged)
  - [x] ⏸ Owner deploys (migrations 0014, 0015) (owner, 2026-10-13)
  - [x] ⏸ Exit: an expense, a trip and a PO paid by a partner each show on that partner's
        statement as owed; recording a reimbursement clears it (tested:
        `test_what_a_partner_is_owed_and_paying_it_back`). On the phone: mark both owners as
        partners (Settings → Users), confirm the mileage rates (Settings → Books) (owner,
        2026-10-13)
- **6b: numbers** (branch `phase/6b-numbers`)
  - [x] Design note → OK (owner, 2026-10-13, all seven decisions as recommended)
  - [x] `work_order.ready_at` (migration 0016), set the first time a WO reaches Ready
  - [x] Dashboard: MTD/QTD/YTD against last year, cash/accrual (remembered on the device),
        blur toggle, receivables and aging, money/work/parts/warranty counts; techs see
        work and parts only. Charts: Chart.js 4.5.1 (vendored, pinned by sha256) on
        screen, the same data as server-drawn SVG in print and PDFs
  - [x] Reports (owners and viewers), each a page and a CSV: P&L (and by month, and a PDF),
        aging, sales tax, expenses, mileage, partners, 1099 reconciliation, inventory value
        with lot trace, parts used, labor by person, job profitability, turnaround,
        warranty and comebacks
  - [x] Year-end package: one zip of CSVs, both P&Ls (CSV and PDF), issued invoice and
        credit memo PDFs, receipts, a README; it reconciles to the dashboard and the P&L to
        the cent on a synthetic year, both bases (tested:
        `test_synthetic_year_adds_up_everywhere`)
  - [x] Nav in six sections with a tab row: Dashboard · Work · Customers · Money · Parts ·
        Settings
  - [x] Diff, tests, privacy output → OK → PR #15 (merged)
  - [x] ⏸ Owner deploys (migration 0016) (owner, 2026-10-13)
  - [x] ⏸ Exit: the year-end package's figures reconcile to the dashboard to the cent
        (tested). On the phone: the dashboard on both bases, blur, print the P&L, download
        this year's package and open it (owner, 2026-10-13)

## Phase 7: GATBOX link, hub side (branch `phase/7-gatbox-link`)

- [x] Design note → OK (owner, 2026-10-13, all eight decisions as recommended)
- [x] Devices + tokens (Settings → Devices, `scripts/create_device.py`), contract v1
      vendored byte-identical from arcade-tracker + checksum test
      (`scripts/contract_checksums.py`, check only)
- [x] Health (tracker keys + `"app": "shop-hub"`, `"capabilities": ["jobs"]`), roster
      (`model_key`, no customer names), ingest (sessions, readings, orders → jobs, session
      tags, optional `phase`), open jobs per asset (+ `work_order`, `nte`), traces on the job
      page (phase, take off, put on), the asset page and the service report (migration 0017)
- [x] Contract examples posted twice → created, then duplicate (slug swapped for an `s-` tag
      at test time); bad token → 401; unknown slug → rejected (tested:
      `test_posting_the_example_twice_gives_created_then_duplicate` and the rest of
      `tests/test_api_v1_contract.py`)
- [x] Diff, tests, privacy output → OK → PR #16 (merged)
- [x] ⏸ Owner deploys (migration 0017) (owner, 2026-10-13)
- [x] ⏸ Mint a device token in Settings → Devices and run the smoke test (RUNBOOK §10).
      The token goes on the Pi only once GATBOX has `hubs.d` (plan Phase 8): its single
      `hub.conf` points at the barcade's hub today. No GATBOX code here (owner, 2026-10-27: GATBOX
      connected)

## Phase 8: GATBOX multi-hub (in the GATBOX repo, not here)

- [x] ⏸ Owner runs a GATBOX session (prompt handed over 2026-10-13: housekeeping for its
      Phase 6 and both bench exits, which are done; then `hubs.d`, per-hub caches and
      routing, the conflict flag, picker by hub, `model_key` → manuals, the JOB card)
- [x] ⏸ Exit: on one Pi, a barcade trace lands in the tracker and a customer trace on the
      shop job, and neither shows in the other (owner, 2026-10-27)

## Phase 9: Desktop (three PRs)

- [x] Design note → OK (owner, 2026-10-13: all eleven decisions as recommended; Ledger and
      Graphite on the desktop, synthwave on phones)
- **9a: desktop shell and speed** (branch `phase/9a-desktop-shell`)
  - [x] 1024 px and up: a sidebar (sections with the open one's tabs), pages up to 96rem;
        phones unchanged
  - [x] Desktop themes per user (Account → Desktop look): Ledger (default), Graphite,
        follow the system, or synthwave; IBM Plex Sans and Mono vendored from npm
        (sha512 checked); migration 0018 (`app_user.desk_theme`, `saved_view`)
  - [x] Split view: a list's record opens beside it (the page's content alone, by
        `X-Partial`; `?pane=` in the URL, so reload, Back and Forward work)
  - [x] Command bar (Ctrl K or `/`): global search as JSON (invoices too, for those who
        see money) and role-aware actions; `g` then a letter to jump, `n` for new
  - [x] Power tables on the eight lists: sort, filters, saved views (per user), columns
        (per device), CSV of the view, bulk status and tech on work orders; cost columns
        only for owners and viewers
  - [x] Buy links (owner, 2026-10-13): every part the customer is buying opens the
        seller's page: the job's lines table, a new panel on the work order, the send-links
        list, the service report (parts not here yet), and web addresses in the customer's
        comm log (escaped text; only http(s) becomes a link). Already linked: the estimate
        (staff, PDF, `/d/`) and the job's customer-parts panel
  - [x] Customer's parts, to order → ordered → here (owner, 2026-10-13: "no way to update
        to ordered and received"): migration 0019 (`wo_line.ordered_on`, `expected_on`);
        Ordered (dates) and Arrived on the work order page as well as the job; their own
        part can wait too ("Not here yet"); a work order waiting with nothing outstanding
        gets a one-tap Back to In progress
  - [x] Diff, tests, privacy output → OK → PR #17 (merged; the parts fix rode in with #19)
  - [x] ⏸ deploy (migrations 0018, 0019) (owner, 2026-10-13)
- **9b: work in motion** (branch `phase/9b-work-in-motion`, on top of 9a's)
  - [x] Design → OK (owner, 2026-10-13: all eight as recommended; double-booking warns,
        with a setting to refuse it)
  - [x] Work board (`/work/board`, desktop tab): a column per open status plus the last 14
        days' completed; cards by priority then promised date (late, parts in, not billed,
        what it waits on, techs); drag or the Move menu, saved through the status form,
        refused when someone changed it meanwhile; a confirm before Completed with lines
        not billed
  - [x] Dispatch calendar (`/work/dispatch`, desktop tab): day (a lane per tech, plus
        Unassigned) or week; 06:00-22:00 in 15 minutes; drag a work order from "To
        schedule" to book it (60 min), drag a booking to move it or change its tech, its
        edge to resize; double-booking shown in red, or refused (Settings → Dispatch,
        migration 0020); an appointment edit form; phones get the day's list
  - [x] Settings → Audit (owner): filters (what, who, did, dates), what changed (before →
        after, local times, values as stored), CSV; History on work orders, invoices,
        estimates, customers, parts and payments (the record and the rows under it)
  - [x] As found against as left: one chart and the numbers side by side on the job and
        trace pages, an SVG on the service report; manual readings per test point against
        the spec on the job page
  - [x] Diff, tests, privacy output → OK → PR #19 (merged)
  - [x] ⏸ deploy (migration 0020) (owner, 2026-10-13)
- **9c: money at the desk** (branch `phase/9c-money-desk`)
  - [x] Design → OK (owner, 2026-10-13: all eleven as recommended; no accountant yet, so
        the export uses generic journal columns)
  - [x] Invoice ready work (`/invoices/bulk`, owners): Completed and Ready work orders (any
        status can be ticked in), preview totals, then one invoice per work order, each
        in its own transaction; one that can't be issued says why and the numbers stay
        gapless; at most 50; then make links for the lot
  - [x] Statements (`/statements`): everyone who owes or has credit, aging, payments from
        the last 60 days; one PDF for the ticked (a page each) or a statement link per
        customer (`/d/`, live, 30 days, noted in the comm log); migration 0021
  - [x] Money → Bank: accounts (wallets too), CSV import with a remembered column mapping
        (duplicates skipped by fingerprint), suggested matches (exact amount, date window,
        deposits of 2-4 receipts) confirmed by a person, find, set aside, expense from a
        line, reconcile against the statement, undo the latest with a reason; reconciled
        lines locked by triggers; a matched record changed later is flagged; viewers
        read only; migration 0022
  - [x] For an accountant (Reports): the general journal (CSV) and trial balance for any
        period, accrual double entry; Settings → Accounts names and numbers each account
        (migration 0023); both CSVs in the year-end zip. Ties to the accrual P&L to the
        cent (tested on the synthetic year, and shown on the page)
  - [x] Diff, tests, privacy output → OK → PR #20 (merged)
  - [x] ⏸ deploy (migrations 0021-0023) (owner, 2026-10-13; `deploy.sh --check`: all
        clear, "migrations: at head 0023_accounts")
- **Fix: cost of goods** (branch `phase/9-cogs-fix`, on top of `phase/9-closeout`)
  - [x] Plan → OK (owner, 2026-10-13: all three as recommended)
  - [x] A part the shop doesn't stock is an expense when it's bought (supplies on its PO,
        or an expense) and no longer counted again as cost of goods when it's invoiced:
        one rule (`figures.is_cogs` / `cogs_line`) for the P&L, the journal and the
        year-end zip's new "Cost of goods" column. Past periods are corrected too (cost
        of goods is computed, nothing stored changes). Job profitability and the margins
        on invoices and work orders still count a non-stocked part's default cost, with a
        note
  - [x] Whether a part is stocked is fixed once it's been ordered, received, set aside,
        put on a job or invoiced (the part form says so; add a new part instead)
  - [x] The journal rounded each line's cost with Python's `round()` (a half cent to the
        even cent) where the P&L rounds half up: it now uses `round_money`, so a $0.1250
        lot cost no longer leaves the journal a cent off the P&L
  - [x] Diff, tests, privacy output → OK → PR #22 (merged)
  - [x] ⏸ deploy (no migration; first deploy with `LC_ALL=C.UTF-8`, see below) (owner, 2026-10-27)
- **Docs: README and a wiki** (branch `docs/readme`)
  - [x] README.md, and the wiki's pages in `docs/wiki/`, published by
        `scripts/publish_wiki.sh` (owner-run; copies origin/main's `docs/wiki/`) → PR #23
        (merged)
  - [ ] ⏸ Repo Settings → Features: Wikis on, "Restrict editing to collaborators only";
        save a first page on the Wiki tab; then `scripts/publish_wiki.sh`
- **Fix: deploy under a missing locale** (branch `phase/9-locale-fix`)
  - [x] `deploy.sh --check` failed "flask db heads" with an ASCII UnicodeDecodeError on the
        LXC (2026-10-14): Alembic reads `alembic.ini` with the locale's own encoding
        (`encoding="locale"`, not UTF-8 mode), ASCII when `LC_ALL` names a locale the
        container lacks, and the file had a one-character "...". Now the file is ASCII
        (a test reads it under `LC_ALL=C`), `deploy.sh` exports `LC_ALL=C.UTF-8`, and
        RUNBOOK §3 says what to do when running flask by hand
  - [x] Diff, tests, privacy output → OK → PR #24 (merged)
  - [x] ⏸ deploy (no migration): a plain `deploy/deploy.sh --check` (no prefix) all clear
        confirms #22-#24 are live and the locale fix works (owner, 2026-10-27)

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
| 2026-10-08 | Asset tags `s-NNNN-name-slug`: number from `asset_tag_seq` (gaps fine, never reused), name part frozen at creation, ≤ 24 chars, tag ≤ 40; a trigger refuses tag changes | Owner. Printed labels stay valid forever. Contract v1's slug limits checked in Phase 7: see 2026-10-13 |
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
| 2026-10-12 | A WO leaves Waiting on parts by itself when nothing is outstanding: no customer's part on its way, no open PO line for its jobs, and every part set aside for it covered by stock (the oldest reservation of a part gets the stock first). Checked after Arrived, receiving (on a PO or on the part's page), closing or cancelling a PO, and taking a line off the list; `work_order.parts_arrived_at` flags it on the board until someone sets the status by hand | Plan's "moves the WO from waiting_parts to in_progress once everything it waits on is in"; FIFO keeps two jobs from claiming one box |
| 2026-10-12 | Order for this job sets the part aside at once (even before it's in stock, like converting an estimate), so receiving needs no new reservation; taking the line off the list releases it; cancelling an ordered PO keeps it (the job still needs the part, so it shows as short) | One path for job parts; no double count on the shopping list (projected = on hand + on order − set aside) |
| 2026-10-12 | POs: draft → ordered (number from `doc_counter` at that moment, never in the future) → partly received → received, or closed short / cancelled. Lines to order may be deleted (trigger `po_line_guard`); after that only cancelled or closed. Receipts append-only; received = sum of receipts. Subtotal and total computed; shipping and tax paid entered from the receipt | Owner decisions 5–7; the stock ledger's "never a stored count" rule |
| 2026-10-12 | Carriers (USPS, UPS, FedEx, DHL Express) seeded by a data migration with link templates from community-documented formats (none publishes one; DHL's `AWB=` parameter is the older form). Edited in Settings → Carriers; carrier guessed from the number by pattern; status set by hand | Plan: "a carrier changing its site is a settings edit, not a deploy" |
| 2026-10-12 | Shipments sit on a PO (from the vendor) or a WO (to or from the customer). Techs add and update tracking; only owners and viewers see shipping cost. The job's older `inbound_tracking` field stays as it was | Owner decision 9 |
| 2026-10-13 | Phase 6 in two PRs: 6a books (expenses, mileage, partner ledger, sales-tax filings), 6b numbers (dashboard, reports, CSV, year-end package) | Owner |
| 2026-10-13 | Charts: Chart.js 4.5.1 (vendored) on screen; the same data drawn as server-side SVG for PDFs, the year-end package and the browser's print. One function per chart feeds both | Owner: Chart.js's interaction, without its poor printing |
| 2026-10-13 | Purchase orders feed the books with no second entry: shipping, sales tax paid and lines for parts not stocked show as expenses on the order date; a PO on a partner's card is owed to them in full. Stocked parts are cost of the job when sold | Owner (decision 3); the plan's "inventory parts are not expenses" |
| 2026-10-13 | A partner's trips in their own vehicle are owed to them at the IRS rate in effect on the day; rates are dated rows (the IRS changed 2026's mid-year) | Owner (decision 4; CPA to confirm the arrangement) |
| 2026-10-13 | Nothing in the books is deleted: expenses, trips and ledger entries are voided with a reason; categories switched off; rates edited (audited). Reimbursing more than is owed is refused (record the rest as a draw) | Owner (decision 5) |
| 2026-10-13 | Owners keep the books; viewers (the accountant, later) read them; techs see none of it | Owner (decision 6) |
| 2026-10-13 | 2026 mileage rates seeded from the IRS (Notice 2026-10: 72.5¢; Announcement 2026-11: 76¢ from July 1), labelled with their source; the 1099-NEC threshold is a setting, $2,000 for 2026 payments | Owner (decisions 7, 8); checked 2026-10-12 |
| 2026-10-13 | Sales-tax filings copy the period's taxable sales and tax collected when recorded; periods can't overlap (exclusion constraint). "Collected and not yet filed" counts from the day after the last filed period | A later void can't change what was filed |
| 2026-10-13 | Every figure comes from `app/reports/figures.py`. Cash basis revenue = money received (receipts less reversals and refunds) by date, less the sales tax inside what was applied to taxed invoices (each allocation's share, half-up); deposits and credit count when received. Accrual = invoices issued without tax, less credit memos issued; voids never count | Owner (decision 2); CPA to confirm the basis filed on |
| 2026-10-13 | Cost of goods = parts and sublets on invoices issued in the period at the line's frozen cost, under both bases; a customer's own parts cost nothing | Owner (decision 3) |
| 2026-10-13 | Turnaround = intake (first job received, else WO opened) → `ready_at`; older WOs use `completed_at`. Comeback rate = warranty WOs opened in the last 90 days ÷ jobs invoiced in the same window | Owner (decisions 4, 5) |
| 2026-10-13 | Year-end package built on demand into a temp file and streamed, never stored; files missing from the store are listed in its README rather than failing the build | Owner (decision 6) |
| 2026-10-13 | Dashboard basis and blur are remembered per device (localStorage), blur applied before the page draws. Reports and the year-end package: owners and viewers | Owner (decision 7) |
| 2026-10-13 | CSVs: plain numbers, ISO dates, a totals row last; text that a spreadsheet would run as a formula (`= + - @`) gets a leading apostrophe | Payees and descriptions are typed in |
| 2026-10-13 | Asset tags are valid GATBOX slugs: GATBOX's roster rule is `^[a-z0-9]+(-[a-z0-9]+)*$` (gatboxweb/roster.py), and `^s-[0-9]{4,}(-[a-z0-9]+)+$` is a subset; no length limit on the Pi | Checked against gatbox `31b7344` |
| 2026-10-13 | GATBOX orders become jobs: on the asset's open work order if it has one, else a new one (bench if in the shop, else on site); priorities Low→low, Medium→normal, High→high, Critical/Urgent→rush, for a new WO only. The job keeps the order's uid (`wo_job.external_id`) and when it was raised (`reported_at`) | Owner (Phase 7 decision 1) |
| 2026-10-13 | A trace attaches itself when its asset has exactly one open job, else waits on the asset page; `wo_job_session` links traces to jobs (many to many) with a phase per link; detaching deletes the link, never the trace | Owner (decision 2) |
| 2026-10-13 | The roster sends active assets (in service, in the shop, awaiting pickup, shipped back) by tag, name, status and shelf or "On site", never a customer's name: gatbox-web has no login | Owner (decision 3) |
| 2026-10-13 | Orders endpoint lists the asset's jobs: id = job id, issue = complaint, the WO's priority and status labels; adds `work_order` and `nte` (money as a string) for the Phase 8 JOB card | Owner (decision 4); additive, as CLAUDE.md allows |
| 2026-10-13 | `phase` (as_found, during, as_left) is optional on rail_session and session_tag items; a bad value rejects the item. Settable on the job page anytime | Owner (decision 5) |
| 2026-10-13 | GATBOX measurements are NUMERIC without a scale, exactly as sent (the body is parsed with `parse_float=Decimal`); times timestamptz; traces insert-only for the app role; readings and sessions not audited (machine-written, up to 2,000 rows), devices and links audited | Owner (decision 6); unlike the tracker's floats and naive times |
| 2026-10-13 | Devices minted in Settings → Devices (token shown once on the page, never in a flash or the session) or `scripts/create_device.py`; switched off, never deleted; New token invalidates the old at once. "Last seen" is written at most every 15 minutes (or on a new address), because the device row is audited and gatbox-sync polls every couple of minutes | Owner (decision 7) |
| 2026-10-13 | The service report lists a job's GATBOX traces (phase, rail, min/mean/max, % in window, verdict); the trace chart is on screen only | Owner (decision 8) |
| 2026-10-13 | Desktop at 1024 px and up (a tablet held sideways included): sidebar, wider pages, the user's desktop theme. Below that nothing changes. Everything desktop is CSS in `desk.css` behind the media query, plus `desk.js`; every feature has a no-JS path | Owner (Phase 9 decisions 1, 4) |
| 2026-10-13 | Desktop themes per user: Ledger (light, default), Graphite (dark), follow the system, or synthwave; phones always synthwave. IBM Plex Sans/Mono for both business themes. Customer documents unchanged | Owner (decisions 2, 3) |
| 2026-10-13 | Split view loads a record's page content alone (`X-Partial: 1`) into a pane beside its list; only record URLs (a fixed pattern) are fetched; `?pane=` keeps the state in the URL | Owner (decision 4): no single-page app |
| 2026-10-13 | Power tables: one spec per list (`app/lists/specs.py`) drives the desktop table, its CSV and saved views; filters and sort run in SQL from the query string; phones keep their own list markup, and the old `all=1`/`mine=1` switches still work | Saved views per user (DB), columns per device (localStorage) — decision 5 |
| 2026-10-13 | Nav regrouped: Dashboard · Work (Work orders, Estimates) · Customers (Customers, Assets) · Money (Invoices, Payments, Books, Reports; owners and viewers) · Parts (Parts, Links, Buying, Price book) · Settings (owners) | Owner (decision 1) |
| 2026-10-13 | Bulk invoicing commits each invoice on its own: one refused doesn't stop the rest, and the gapless counter only moves for invoices that exist | Owner (9c design) |
| 2026-10-13 | Bank: wallets are accounts; only the latest reconciliation can be undone, with a reason; reconciled lines and their matches are locked by triggers (even for the schema owner); a matched record edited later is flagged, not blocked | Owner (9c design) |
| 2026-10-13 | Accountant export: accrual double entry, generic general-journal CSV and a trial balance; account names in `account_map` (only changes stored). Customers' credit stays in receivables; unmatched money goes to its payment method's account or Checking; stock is inventory when ordered and cost of goods when invoiced, as the P&L counts it | Owner (9c design); no accountant yet |
| 2026-10-13 | Cost of goods is stocked parts (at their lot cost) and sublets. A part the shop doesn't stock is an expense when it's bought and isn't counted again when it's sold; whether a part is stocked is fixed once it's used. Job profitability and margins still count a non-stocked part's default cost | Owner (cost-of-goods fix, as recommended). It had been counted twice since 6b |
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

### Session 3, Phase 5b (2026-10-12, same cloud session)

- PR #12 (5a) merged. 5b on `phase/5b-purchasing`: migrations 0012 (carrier,
  purchase_order, po_line, po_receipt, shipment; `po_line_id` on stock_lot and stock_move;
  `work_order.parts_arrived_at`; `po_line_guard()`) and 0013 (seed carriers, data only).
  Round-tripped; no drift.
- `app/purchasing/`: the Buying page (running low, the shopping list by vendor, drafts, on
  order), PO pages, phone receiving, tracking on POs and WOs, Settings → Carriers; Order for
  this job on the job page; on order and "buy it here" links on the part page; POs and
  tracking numbers in global search. `work.waiting_on()` / `release_if_ready()` now decide
  every automatic move out of Waiting on parts (5a's Arrived included).
- The carriers' tracking pages couldn't be fetched from the VM (proxy), so the templates
  rest on web searches; flagged for the owner to check with real numbers.
- The phone pass caught the settings tabs running off a phone screen once Carriers made
  five; the tabs wrap now.

### Session 3, Phase 6a (2026-10-13, same cloud session)

- Phase 6 design OK'd. The 2026 IRS mileage rate changed mid-year (72.5¢ → 76¢ from July 1,
  Announcement 2026-11), so rates are dated rows; the 1099-NEC threshold is $2,000 for 2026
  (OBBBA) and indexed from 2027, so it's a setting. Chart.js 4.5.1 is current on npm.
- 6a on `phase/6a-books`, branched from `phase/5b-purchasing` (PR #13 not merged yet):
  migrations 0014 (six tables, books settings, PO receipts, the filings exclusion
  constraint) and 0015 (categories, rates; data only). Round-tripped; no drift.
- `app/books/`: Books (overview, expenses, mileage, partners, sales tax), Settings → Books,
  receipts on POs, a costs panel and "Log a trip" on work orders. The restore drill checks
  receipt files too.
- Phone pass: expense with a receipt photo, a trip from a WO, the partner page, expenses,
  sales tax, settings; no page scrolls sideways, no console errors.

### Session 3, Phase 6b (2026-10-13, same cloud session)

- PR #14 merged; 6b on `phase/6b-numbers` from `main`. Migration 0016 (`work_order.ready_at`);
  round-tripped, no drift.
- `app/reports/`: `figures.py` (every number), `service.py` and `tables.py` (each report as a
  table: page, CSV and year-end file from the same rows), `svg.py` (print charts),
  `dashboard.py` (the counts), `yearend.py`. Chart.js 4.5.1 from the npm tarball, checked
  against npm's sha512 and pinned in `app/static/vendor/VENDORED.txt` (a test checks every
  vendored file's hash; the external-assets test now skips `vendor/`).
- The exit test builds a synthetic year (taxed and untaxed invoices, a void, partial,
  bounced and over payments, a refund, a credit memo, expenses with a receipt, a PO, trips),
  recomputes the P&L from the zip's raw CSVs alone, and checks it equals the P&L CSV, the
  monthly totals and the dashboard to the cent on both bases.
- Phone pass: dashboard (both bases, blur across a reload, basis remembered), every report,
  invoices/buying/work with the new tabs, print emulation (SVG charts shown, canvas and nav
  hidden), the P&L PDF and this year's zip; no page scrolls sideways, no console or CSP
  errors.
- Not done (say if wanted): the dashboard's "to order" is a count, not grouped by vendor
  (the shopping list groups them); aging is as of today only (balances aren't kept by
  date).

### Session 3, Phase 7 (2026-10-13, same cloud session)

- PR #15 merged; Phase 7 on `phase/7-gatbox-link` from `main`. `/opt/refs` was absent (the
  setup script's clone failed); both reference repos are public, so they were cloned
  read-only into `/opt/refs` (arcade-tracker `65fe4b6`, gatbox `31b7344`) and not attached.
  Their contract/v1 copies are identical and match CHECKSUMS.
- Findings: gatbox-sync is still single-hub (`/etc/gatbox/hub.conf`), so the Pi isn't
  pointed here until plan Phase 8; GATBOX's priorities are Low/Medium/High/Critical/Urgent;
  its screen shows only `#id issue` for a hub's orders.
- Migration 0017 (`device`, `rail_session`, `reading`, `wo_job_session`,
  `wo_job.external_id`/`reported_at`); round-tripped, no drift. `app/api_v1/` (the API),
  `app/gatbox/` (ingest, roster, orders; trace pages), Settings → Devices.
- Phone pass: mint a device, the example posted twice (created, then duplicate), roster and
  orders, the asset page, the trace page with its chart, phase on the job; no page scrolls
  sideways, no console errors.

### Session 3, Phase 9a (2026-10-13, same cloud session)

- PR #16 merged. Phase 9 design OK'd; 9a on `phase/9a-desktop-shell` from `main`.
- Migration 0018 (`app_user.desk_theme`, `saved_view`); round-tripped, no drift. IBM Plex
  Sans 1.1.0 and Mono 2.5.0 from npm, checked against npm's sha512, in `app/static/fonts/`
  (OFL.txt updated).
- `app/static/css/desk.css` (themes, sidebar, split view, power tables, command bar),
  `app/static/js/desk.js`, `app/lists/` (engine, specs, saved views, bulk work orders),
  the command bar in `base.html`, `/search?format=json`.
- Passes: phone (Pixel 7) unchanged: synthwave, no sidebar, no desktop table, no page
  scrolls sideways; desktop at 1440×900 in all three themes and at 1024×768; the pane, the
  command bar, `g c`, a saved view and hidden columns surviving a reload; no console errors.
- GATBOX Phase 8 prompt handed to the owner (run it locally in `~/gatbox`, where its
  pre-push privacy hook lives).
- Buy links added to the 9a PR as their own commit (no migration), comm-log addresses as
  links included (owner). Stored service reports keep what they had.
- PR #17 opened (9a + buy links). The owner couldn't find how to move a customer's part
  past "waiting": Arrived lived only on the job page, there was no ordered step, and a
  hand-set wait had no way out but the status form at the bottom. Fixed as a third commit
  on #17 (migration 0019).
- 9b design OK'd (all eight as recommended; double-booking warns, with a setting to block).

### Session 3, Phase 9b (2026-10-13, same cloud session)

- `phase/9b-work-in-motion` from 9a's head (`fa5d5b6`); its PR waits for #17.
- `app/work/planning.py` (board and dispatch, on the work blueprint), `board.js`,
  `dispatch.js` (HTML5 drag for cards and bookings, pointer events for resizing; positions
  set from JS, since the CSP refuses `style=`), `app/settings/audit.py`,
  `app/gatbox/charts.py` (overlay JSON and print SVG), `traces/_compare.html`.
- The WO page's booking form now goes through `service.book` (the same overlap rule).
- Passes: drag a card (saved, survives a reload, Move puts it back); book, move to another
  lane, resize on the calendar; History from a WO; the overlay and readings charts; the
  service report SVG; all three desktop themes; phones (the board stacks with Move menus,
  dispatch is a list); no console errors, nothing wider than the screen.
- Shortcuts: `g o` board, `g l` dispatch (`g b` and `g d` were taken).
- PR #19 merged; it carried the parts fix (`fa5d5b6`) too, so #18 has nothing left.
- 9c design OK'd: all eleven as recommended; no accountant yet (maybe after the new year).

### Session 3, Phase 9c (2026-10-13, same cloud session)

- `phase/9c-money-desk` from main after #19.
- `app/billing/desk.py` (bulk invoicing, statements, statement links), `app/bank/`
  (service, forms, routes), `app/books/journal.py` (entries, chart, trial balance),
  `app/settings/accounts.py`; migrations 0021 (doc_link.customer_id), 0022 (bank tables
  and three guard triggers), 0023 (account_map).
- Passes: bulk preview and issue, statements PDF and links, the public statement page;
  bank import → suggestions → match, set aside, expense from a line → reconcile; the
  export (ties) and Settings → Accounts; all three desktop themes; phones; no console
  errors, nothing wider than the screen.
- Found on the way: a settings field missing from a POST would have reset that account
  to its built-in name (now left alone); PDF cells styled as flex rows broke the table
  borders (fixed in the two new tables).
- The worker restart lost the docker dev database (synthetic only); `devdb.sh start`
  makes a fresh one.
- PR #20 merged. The owner reports everything merged and deployed, the price book's
  rates set and the claim-ticket and warranty terms written; the host's
  `deploy.sh --check` confirms the database at head 0023_accounts. Open: the phone exits
  (0, 4, 5b, 6a, 6b), "How customers pay you", the device token and smoke test (7),
  GATBOX's `hubs.d` and the Phase 8 exit.
- Found while building the journal, not fixed (owner to decide): a part the shop doesn't
  stock takes its default cost onto job lines, so its cost counts as supplies on its PO
  and again as cost of goods when invoiced. In the P&L since 6b; the journal follows it.
- Phone exits passed (owner): Phase 4 (the first real invoice, backdated), 6a and 6b.
  Still open: the partner's 2FA sign-in (0) and order → receive on the phone (5b).
- Cost-of-goods fix planned and OK'd (all three as recommended); built on
  `phase/9-cogs-fix`, stacked on #21. The new test fails on the old code (cost of goods
  $40.80 where it should be $0.80); a half-cent test caught the journal's rounding (a
  cent off the P&L on the old code).
- #22 and #23 merged. The README and the wiki went in as `docs/wiki/` plus an owner-run
  publish script, not edits on GitHub: wiki pages are a separate repository that skips the
  pre-push privacy check, and a public repo's wiki is editable by anyone unless
  restricted.
- Session 4 (2026-10-14, same cloud session): the owner's `deploy.sh --check` for the six
  new commits failed in `flask db heads` (ASCII decode of `alembic.ini`). Reproduced here:
  only `LC_ALL` set to a missing locale (or `C`) does it. With `LANG` alone Python falls
  back to UTF-8 and it works; `PYTHONUTF8=1` doesn't help. Workaround until the fix is
  deployed: `LC_ALL=C.UTF-8 deploy/deploy.sh`.
- 2026-10-27 (owner): the partner signed in with 2FA (Phase 0 exit), order → receive works
  on the phone (5b exit), GATBOX is connected (Phase 7 token and smoke test), the Phase 8
  exit passed, and a plain `deploy.sh --check` is all clear (#22-#24 live). Every phase
  exit has now passed. Open: the wiki's first publish, and "How customers pay you" (the
  owner will write it later). Next work, when wanted, is PLAN.md's parked list.
