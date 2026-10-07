# Shop hub (ArcadeTech Tracker): the side business (customers, work orders, invoices, parts)

**GDD · 2026-10-07 · written against GATBOX `9686bfa` and arcade-tracker `341197a`**

## Bottom line

Build the side business as a **third app, the shop hub**. It is a private Flask + PostgreSQL app in its own LXC, a sibling of the tracker. It is not part of the tracker and not part of gatbox-web.

- **The shop hub is the system of record** for customers, their machines and boards, estimates, work orders, invoices, payments, parts, purchasing, expenses and mileage. It runs on the homelab, always on and backed up. You and your partner each log in from a phone over the tunnel.
- **GATBOX stays a field instrument.** It learns to talk to **more than one hub**. A trace taken on a customer's machine syncs to the shop. A trace taken on a barcade machine syncs to the tracker, exactly as today.
- **The shop speaks contract v1**, the same health / roster / ingest / open-orders API the tracker serves. gatbox-sync needs a hub list, not a new protocol.
- **Nothing has to be stripped from other GATBOX units.** GATBOX's code gains only generic "several hubs" support, and hub names live in config, never in the public repo. A unit with no shop hub configured behaves exactly as it does today.
- **The barcade's data and yours never share an app or a database.** The tracker is the barcade's work record. The shop is your business. Separate repos, LXCs, databases, roles and logins.

Critical path to invoicing your partner's first job: **Phases 0 → 4** (see Phases). Everything after that is the rest of "fully featured".

---

## Decided (2026-10-07)

| | Decision |
|---|---|
| Where it runs | Homelab, its own LXC (not on the Pi) |
| Database | PostgreSQL: its own database and role on the existing Postgres host |
| Users | You and your partner each get a login (owner role). A read-only role for whoever does your taxes |
| Payments | Recorded only: cash, check, Zelle, Venmo, Cash App, PayPal, bank transfer. No card processing yet. Card payment links come later as an add-on |
| Tax state | Oklahoma (see "Oklahoma sales tax") |
| Tracker | Stays barcade-only. No shop data in it |

---

## Architecture

```
            ┌──────────────── GATBOX (Pi, field instrument) ────────────────┐
            │ gatbox-raillog, gatbox-web, gatbox-dump      (unchanged)      │
            │ gatbox-sync  → iterates /etc/gatbox/hubs.d/*.conf             │
            │   per hub: health → roster cache → push items for its slugs   │
            └───────────────┬───────────────────────────────┬───────────────┘
                 push/pull  │ contract v1                   │ contract v1
                            ▼                               ▼
   ┌──── arcade-tracker (LXC 131) ────┐       ┌──────── shop-hub (new LXC) ─────────┐
   │ barcade machines, orders, rails  │       │ customers · sites · assets          │
   │ its Postgres DB (existing)       │       │ estimates · work orders · time      │
   └──────────────────────────────────┘       │ invoices · payments · credit memos  │
                                              │ parts · lots · stock ledger         │
                                              │ vendors · POs · shipments           │
                                              │ expenses · mileage · partner ledger │
                                              │ Postgres DB "shop" (new, own role)  │
                                              └──────────────┬──────────────────────┘
                                                             │ Cloudflare tunnel (separate hostname)
                                         your phone · partner's phone · customer document links
```

**Why not on the Pi:**
- Your integration already decided the field box isn't the system of record: GATBOX pushes and hubs own the data.
- gatbox-web is stdlib-only, sandboxed, unauthenticated and hardware-owning. A business app needs logins, money math, PDFs, pip packages and migrations, which breaks CLAUDE.md hard rule 9 (apt before pip, no venv without asking) and the "grow gatbox-web, stdlib" convention.
- The Pi spends nights in cabinets, takes power cuts and runs from an SD card. Business records have to outlive all of that for years.
- Your partner needs to open orders and invoice without your box being on.
- At a customer site the Pi's hotspot has no internet, but phones do. The hub is reachable from either phone on cell data.

**Why not inside the tracker:**
- It's the barcade's system. Your side business, its customers and its money shouldn't sit in your employer's work record, even on hardware you own.
- A shop release can never break the barcade's work queue, and the reverse holds too.
- What's worth reusing is the patterns: the device-token model, `api_v1`, `deploy.sh`, the runbook and the listen config. Port them; don't share code.

---

## What GATBOX gains, and why nothing has to be stripped

All generic, all in the public repo, with no business logic and no hub names in code.

1. **Several hubs.** `/etc/gatbox/hubs.d/<name>.conf`, each holding `HUB_URLS`, `HUB_TOKEN` and `HUB_LABEL` (the label shown on the 7″, from config, so the barcade's name and "SHOP" never appear in git). The existing `/etc/gatbox/hub.conf` keeps working as one implicit entry, for backward compatibility (CLAUDE.md rule 14).
2. **Per-hub caches.** The roster and open orders are cached per hub. Every slug knows which hub owns it.
3. **Routing by slug.** A session, reading, order or tag goes to the hub whose roster holds that machine. A slug in no roster is parked, as today. **A slug in two rosters is a conflict**: it's flagged on the SYSTEM tile and sent nowhere until fixed.
4. **The machine picker groups by hub**, so customer machines and the barcade's don't mix.
5. **Capability-gated JOB card.** If a hub's `/api/v1/health` lists `"capabilities": ["jobs"]`, the MACHINE card shows that machine's open job: complaint, status and NTE. It also offers AS-FOUND / AS-LEFT on SAVE READING and on session tags. The tracker never advertises `jobs`, so nothing changes for the barcade.
6. **Manuals for customer machines.** A shop roster entry may carry `model_key`, a slug from the Pi's manuals library. When it's present, MANUALS, the spec sheet and the MAME ROM checklist work for a customer's Ms. Pac-Man because the barcade's is already in the library.

**Campaign Edition:** a unit ships with no `hubs.d`, so it's standalone, exactly as now. A buyer who runs their own repair business could point it at their own hub. If you ever want to, the shop hub can become a product rather than something you hide.

**Contract ownership:** v1 stays owned by the tracker and unchanged. The shop vendors it with the same checksum test. The JOB-card fields (`model_key`, `phase`, the `jobs` capability) are **additive and optional**. **Recommended:** when the contract next changes shape, v2 moves to the GATBOX repo, because GATBOX is the one client that every hub serves.

---

## Data model

PostgreSQL, through SQLAlchemy 2 + Flask-Migrate.

**Conventions:**
- Money is `NUMERIC(12,2)`.
- Quantities are `NUMERIC(12,3)`.
- Tax rates are `NUMERIC(7,6)`.
- Python uses `Decimal` everywhere, never `float`.
- Timestamps are `timestamptz`, stored as UTC. Display uses a timezone set in Settings. This is deliberately not the tracker's naive-UTC convention: a new app gets the right type from day one.
- Every table gets `created_at`, `updated_at` and `created_by`.

### People and access

- **`user`**
  - `username`, `display_name`, `email`
  - `password_hash` (Werkzeug scrypt)
  - `totp_secret`
  - `role`: `owner` | `tech` | `viewer`
  - `is_partner`, `split_pct`, `default_labor_service_id`, `active`
  - Owners can see everything, void and issue invoices, and change settings. Techs can do work orders, time and parts, but see no P&L and can't void. Viewers get the read-only accountant view and exports.
- **`device`**: port the tracker's model as is. Token `gbx_<public_id>.<secret>`, secret hashed, shown once.
- **`audit_log`**
  - `at`, `user_id`, `table`, `row_id`, `action`, `before` jsonb, `after` jsonb
  - Written by a **Postgres trigger** on every financial table.
  - The app runs `SET LOCAL shop.user_id = …` in each request's transaction, so even a hand-run SQL fix shows who did it.

### Customers

- **`customer`**
  - `kind` (`business` | `individual`), `name`, `dba`
  - `billing_email`, `phone`, billing address
  - `payment_terms` (`due_on_receipt` | `net_15` | `net_30`)
  - `tax_exempt`, `exempt_cert_no`, `exempt_cert_file`, `exempt_cert_expires`
  - `default_tax_jurisdiction_id`
  - `sends_1099` (a business that will send you a 1099-NEC)
  - `referral_source`, `notes`, `active`
- **`contact`**
  - `customer_id`, `name`, `role` (owner, manager, …), `phone`, `email`
  - `is_billing`, `is_site`, `notes`
- **`site`**
  - `customer_id`, `name` ("Main St location"), address, `tax_jurisdiction_id`, `site_contact_id`
  - `access_notes`: hours and who lets you in. Not alarm codes; keep those out of the database.
- **`comm_log`**
  - `customer_id`, `work_order_id?`, `kind` (call | text | email | in_person | note), `at`, `user_id`, `summary`

### Assets: machines *and* boards

- **`asset`**
  - `tag`: a GATBOX-safe slug, `[a-z0-9-]`, globally prefixed `s-`, for example `s-0042-ms-pac-man`. **Never reused.**
  - `customer_id`: the current owner. The shop itself is a customer row for shop-owned exchange units.
  - `site_id`, `parent_asset_id` (a board inside a machine)
  - `kind`: machine | board | monitor | chassis | psu | other
  - `name`, `manufacturer`, `model`, `model_key` (manuals library slug, optional), `year`, `serial`
  - `status`: in_service | in_shop | awaiting_pickup | shipped_back | sold | scrapped
  - `in_shop_since`, `shop_location` (shelf or bin), `notes`
- **`asset_event`**
  - `asset_id`, `at`, `kind` (intake | returned | ownership_change | moved | note), `from`/`to`, `note`
  - Warranty and history follow the asset when a machine changes hands.
- **Labels:** each asset gets a QR of `{SHOP_BASE_URL}/g/<tag>`, the same `/g/` shape as the tracker's. GATBOX's scanner already pulls the slug out of `…/g/<slug>`. The Katasymbol workflow carries over: the shop gives you a label list exactly like the dashboard's.

### Price book

- **`service`**: the labor and fee catalog.
  - `code`, `name`
  - `kind`: labor | fee | trip | diagnostic | shop_supplies | sublet | discount
  - `unit`: hour | each | mile
  - `rate`, `taxable` (default false for labor), `warranty_days`, `description`, `active`
  - Seed examples: Bench labor, Field labor, After-hours labor, Diagnostic fee, Trip charge, Minimum service charge, Rush fee.
- **`job_template`** (canned jobs)
  - `name`, `description`, `est_hours`, plus `job_template_line` (service or part, qty)
  - Example: "Monitor cap kit, K4600 chassis" = kit + 1.5 h bench labor. One tap fills an estimate.
- **`labor_policy`** (settings)
  - Billing increment (e.g. 0.25 h), minimum per job (e.g. 0.5 h).
  - Mode per line: `actual` (from time entries, rounded) or `flat` (book hours from the template).

### Work: estimate → work order → jobs → lines

- **`estimate`**
  - `number` (`EST-2026-0001`), `customer_id`, `site_id`
  - `status`: draft | sent | approved | declined | expired | converted
  - `valid_until`, `not_to_exceed`, `deposit_required`, `terms_snapshot`
  - `approved_at`, `approved_name`, `approval_method` (on_screen | link | verbal), `signature_file`, `approval_ip`, `approval_ua`
  - Lines like a work order's.
  - A **revision** is a new estimate pointing at its predecessor (`supersedes_id`). An approved estimate is frozen.
- **`work_order`**
  - `number` (`WO-2026-0001`), `customer_id`, `site_id`, `estimate_id?`
  - `kind`: on_site | bench | mail_in | warranty | consult
  - `status`: new | scheduled | in_progress | waiting_parts | waiting_approval | ready | completed | cancelled
  - `priority`, `promised_date`, `not_to_exceed`
  - `warranty_of_job_id`: for a warranty claim, the original job
  - `assigned_user_ids`
- **`wo_job`**: one per concern per asset. A visit to a bar with three machines is one WO and three jobs.
  - `work_order_id`, `asset_id`, `status`
  - **`complaint`** (what the customer says), **`cause`** (what you found), **`correction`** (what you did). This is the mechanic's "3C" format, and it's what makes the service record read professionally.
  - Intake fields for bench and mail-in: `received_at`, `received_via` (drop_off | pickup | mail), `condition`, `accessories_received` (checklist plus free text), photos, and the inbound shipment.
- **`wo_line`**
  - `wo_job_id`
  - `kind`: labor | part | fee | sublet | discount
  - `service_id?` / `part_id?`, `description`, `qty`, `unit_price`, `unit_cost` (snapshot from the lot), `taxable`
  - `tech_user_id` (labor), `warranty_days`
  - `billable` (false for warranty work, so it's still costed and counted)
  - `customer_supplied` (no stock move, price 0, excluded from warranty)
  - `stock_lot_id?`
- **`time_entry`**
  - `wo_job_id`, `user_id`, `started_at`, `ended_at`, `minutes`, `billable`, `source` (timer | manual), `note`
  - A start/stop timer on the phone. Unbilled time is a dashboard number.
- **`appointment`**
  - `work_order_id`, `starts_at`, `ends_at`, `site_id`, `user_ids`, `note`
  - Each user gets a private **.ics feed URL** (a secret token) that Google Calendar subscribes to. No connector needed.
- **`trip`** (mileage)
  - `date`, `user_id`, `work_order_ids`, `miles` (or odometer start and end), `purpose`, `vehicle`, `billable_trip_line_id?`
  - Deduction rate per year from `mileage_rate(year, rate)`, seeded **2026 = $0.725/mile**. Check the IRS rate each January.

### Readings and evidence

- **`rail_session`** and **`reading`**: port the tracker's `api_v1` storage (summary plus ≤2,000-point min/max series, SI-normalized values with sigrok's raw text).
  - Shop additions: `asset_id`, `wo_job_id?`, and **`phase`**: `as_found` | `during` | `as_left`.
  - Phase can be set at the bench (once the JOB card exists) or afterwards on the job page. **v1 of the contract doesn't need to change for this.**
- **`manual_reading`**
  - `wo_job_id`, `test_point` ("+5 V at edge", "R34"), `value`, `unit`, `spec_lo`, `spec_hi`, `pass`, `phase`, `taken_at`, `user_id`
  - For readings taken without GATBOX.
- **`attachment`**
  - `owner_type`, `owner_id`, `kind` (photo | pdf | receipt | signature | rail_report | other), `file`, `sha256`, `caption`, `by`, `at`
  - Files live in `/var/lib/shop-hub/files/`, never in git.
- **Later:** a `rom_dump` item kind, carrying chip, SHA-1 and romident, for "here's what I read and burned on your board". That needs a contract addition, so it's v2.

### Money

- **`invoice`**
  - `number`: assigned **at issue**; drafts have none
  - `customer_id`, `work_order_id?`: a counter sale (a part, a rebuilt board) needs no work order
  - `status`: draft | issued | partially_paid | paid | void | written_off
  - `issued_at`, `due_at`, `terms_snapshot`
  - `bill_to` jsonb (frozen), `ship_to` jsonb (frozen)
  - `tax_jurisdiction` jsonb (frozen name and rate)
  - `subtotal_taxable`, `subtotal_nontaxable`, `tax`, `total`, `amount_paid`, `balance`
  - `customer_note`, `internal_note`
  - `pdf_file`, `pdf_sha256`: the exact document the customer got, kept
  - `void_reason`, `voided_at`, `voided_by`
- **`invoice_line`**
  - A snapshot of the WO lines at issue: `kind`, `description`, `qty`, `unit_price`, `taxable`, `line_total`, `unit_cost`, `tech_user_id`
  - `warranty_until` (a date, computed at issue), `wo_line_id`
- **`credit_memo`** (`CM-2026-0001`) and `credit_memo_line`, linked to the invoice, with a reason.
- **`payment`**
  - `customer_id`, `received_at`, `amount`
  - `method`: cash | check | zelle | venmo | cashapp | paypal | bank_transfer | other
  - `reference` (check no. or transaction id), `received_by`, `deposited_at`, `note`
  - `reverses_payment_id`: a bounced check or a refund is a new row; the original is never edited.
- **`payment_allocation`**
  - `payment_id`, `invoice_id`, `amount`
  - Covers partial payments, one payment across several invoices, deposits taken on an estimate (left unallocated, then applied at issue), and overpayment, which becomes customer credit.
- **`doc_counter`** (`kind`, `year`, `last`)
  - Gapless numbering per kind per year: `UPDATE … SET last = last + 1 RETURNING last` inside the issuing transaction.
  - Postgres sequences are **not** gapless (a rolled-back transaction burns a value), so don't use them for document numbers.
- **`tax_jurisdiction`**
  - `name`, `state_rate`, `county_rate`, `city_rate`, `other_rate`, `combined_rate`
  - `effective_from`, `effective_to`, `source_note`
- **`sales_tax_filing`**: `period_start`, `period_end`, `taxable_sales`, `tax_due`, `filed_at`, `paid_at`, `confirmation`.
- **`public_link`**
  - `token` (32 random bytes, URL-safe), `doc_kind`, `doc_id`, `expires_at`, `revoked_at`, `views`, `last_viewed_at`

**Immutability is enforced by the database, not just the app.** A Postgres trigger rejects any `UPDATE`/`DELETE` of an `invoice_line`, `credit_memo_line` or `payment` whose parent isn't a draft. On an issued invoice, only `status`, `amount_paid`, `balance` and the void fields may change. Corrections are a void (with a reason) or a credit memo, never an edit. That is what makes these records hold up to anyone who ever looks at them.

### Parts, stock and purchasing

- **`part`**
  - `sku`, `name`, `category`, `manufacturer`, `mpn`, `equivalents` (text[]: 74LS245 ↔ 74HCT245 …)
  - `unit`, `default_cost`, `price_mode` (fixed | markup), `sell_price`
  - `taxable` (default true)
  - `track_stock`: false for consumables, which are expenses rather than inventory
  - `reorder_point`, `reorder_qty`, `preferred_vendor_id`, `bin`
  - `barcode`: the EY-H2 and phone can scan bin labels
  - `datasheet_url`, `notes`, `active`
- **`markup_tier`**: cost-bracket markup rules (settings). The UI always shows margin.
- **`stock_lot`**
  - `part_id`, `qty_received`, `qty_remaining`, `unit_cost`, `received_at`, `po_line_id`
  - `date_code` / `vendor_lot`: for caps, the date code matters
  - Lots give FIFO cost **and** recalls: "which jobs got caps from that lot?"
- **`stock_move`**
  - Append-only ledger: `part_id`, `lot_id`, `qty` (±)
  - `reason`: receive | issue | return | adjust | count | scrap | rma_out
  - `wo_line_id?`, `po_line_id?`, `user_id`, `at`, `note`
  - **On-hand = the sum of moves.** Nothing is ever overwritten.
- **`reservation`**
  - `part_id`, `wo_job_id`, `qty`, `status` (reserved | issued | released)
  - Available = on hand − reserved.
- **`vendor`**
  - `name`, `website`, `account_ref`, `contact`
  - `resale_cert_on_file`: you gave them your Oklahoma permit, so parts for resale come in untaxed
  - `notes`
- **`purchase_order`**
  - `number` (`PO-2026-0001`), `vendor_id`
  - `status`: draft | ordered | partially_received | received | closed | cancelled
  - `vendor_order_ref` (their order number), `ordered_at`, `ordered_by`
  - `paid_with`: business account, or a partner's personal card, which then shows up as a reimbursement owed
  - `subtotal`, `shipping`, `tax_paid`, `total`, `receipt_file`, `notes`
- **`po_line`**: one row per part on the order.
  - `po_id`, `part_id?` (or a free-text `description` for a one-off), `qty`, `unit_cost`
  - `wo_job_id?`: **tied to a job**
  - `status`: **to_order → ordered → shipped → received**, plus backordered | cancelled | returned
  - `qty_received`, `expected_at`
- **`shipment`**: one table for every box moving in either direction.
  - `direction`: inbound_purchase | inbound_customer | outbound_customer | outbound_rma
  - `po_id?`, `work_order_id?`
  - `carrier` (usps | ups | fedex | dhl | other), `tracking_number`, `tracking_url`
  - `status`: label_created | in_transit | out_for_delivery | delivered | exception | returned
  - `shipped_at`, `eta`, `delivered_at`, `cost`, `insured_value`, `status_updated_at`
  - `status_source`: manual now; an API later
  - The carrier is guessed from the tracking-number format and can be overridden. Tracking links come from per-carrier URL templates kept in Settings, so a carrier changing its site is a settings edit, not a deploy.
- **"Delivered" isn't "received".** Receiving means someone opened the box and counted it, and that's the step that touches stock. Receiving a PO line:
  1. creates a `stock_lot` and a `receive` move (with the date code);
  2. if the line is tied to a job, creates a `reservation` for that job;
  3. moves the WO from `waiting_parts` to `in_progress` once everything it waits on is in;
  4. shows the dashboard alert "parts for WO-0012 arrived".

---

## How the work flows

1. **New customer.** Quick-add a customer, site and assets from the work-order form, or do it properly on the Customers page.
2. **Estimate.** Lines come from the price book and job templates. **Send link** opens your phone's share sheet (text, email or copy) with a `/d/<token>` link. The customer sees the estimate and terms and taps **Approve**, then signs with a finger and types their name; the page logs the time, IP and browser. On-site, they sign on your phone instead. Phone approval also works: you log it as verbal, with who and when.
3. **Work order.** An approved estimate converts with its lines, NTE and deposit.
   - **On-site:** start the timer, take GATBOX readings (AS-FOUND), write the cause, issue parts from van stock (FIFO lot), take AS-LEFT readings, write the correction, and get the customer's sign-off on the phone.
   - **Bench / mail-in:** intake with photos, condition and accessories, then print the **claim ticket**: a PDF with the asset's QR, stuck on the board bag. Log the inbound tracking.
   - **Over NTE:** status goes to `waiting_approval`, you send a revised estimate, and the clock stops on the promise date.
   - **Needs parts:** from the job, "Order for this job" puts a `to_order` PO line on the shopping list. The WO goes to `waiting_parts`.
4. **Invoice.** Issue it from the WO: lines are snapshotted, the number and tax are applied, the PDF is frozen and the warranty dates are set. Send the link. The same link shows payment instructions (Zelle, Venmo and so on, from Settings).
5. **Payment.** Record the method and reference. Partial payments are fine. The receipt PDF and link are automatic.
6. **Warranty claim.** On the asset page, **Warranty claim** opens a `warranty` WO linked to the original job. It shows which lines are still under warranty, by date. That work is non-billable but costed, and it feeds the comeback rate.
7. **Restock.** Below reorder point → shopping list, grouped by vendor → "Create POs" → mark ordered (with the vendor order number) → add tracking → delivered → **receive** → stock.
8. **Month-end.** The sales-tax report gives taxable sales and tax by jurisdiction. File with the OTC, then record the filing so the dashboard drops the liability.
9. **Year-end.** One button builds the accountant package: a zip of CSVs (invoices, payments, expenses, mileage, the partner ledger), the P&L, every issued invoice PDF and the receipts. It also holds the 1099 reconciliation: what each customer paid you this year, flagging `sends_1099` customers and anyone at or over **$2,000**, the 1099-NEC threshold for 2026 payments.

---

## What customers get

All of these are PDFs made with WeasyPrint from HTML/CSS templates, and the same HTML serves the `/d/<token>` page. Customer documents are **print-first: a white page and black text**, with house fonts and one accent color. Neon on violet stays in the app; a bar owner's printer shouldn't pay for it.

| Document | Contents |
|---|---|
| **Estimate** | Lines, NTE, deposit, valid-until, terms, approval block (signature, name, date) |
| **Claim ticket** | Intake details, condition, accessories, asset QR, storage and abandonment terms |
| **Service report** | The mechanic-style printout. Per job: complaint / cause / correction, the **as-found → as-left readings table** with spec and pass, a rail trace thumbnail, parts used (with date code), labor by tech, warranty terms and dates |
| **Invoice** | Itemized; labor always separate from parts (see tax); tax by jurisdiction; payments applied; balance; payment instructions; the service report attached for repair work |
| **Receipt** | The payment, what it applied to, the remaining balance |
| **Statement** | Open invoices and aging, for a customer with several |
| **Credit memo** | Against an invoice, with a reason |
| **Packing slip** | For a mail-in return |

**Terms you'll want written once and printed every time** (draft them yourself, then have a lawyer look):
- authorization and the NTE rule;
- that vintage boards can reveal further faults once powered;
- warranty scope (what voids it: someone else working on it, a bad power supply, …);
- storage fees after N days, and abandonment after M days with notice;
- that customer-supplied parts carry no warranty;
- your payment terms.

---

## Oklahoma sales tax

What the sources say (Oklahoma Administrative Code and a rate summary, checked 2026-10-07):

- **Separately stated labor isn't taxed; parts are.** The automotive repair rule (OAC 710:65-19-11) and the shoe repair rule (710:65-19-310) both tax the property sold and not separately stated labor.
- **Lump-sum billing is where the trades differ.** Under the automotive rule, a shop that charges one combined price collects no tax and instead pays tax when it buys the materials. Under the shoe repair rule, a combined price is taxed in full. The app sidesteps this: **every line is labor, part or fee, never a mixed lump sum.**
- **Consumables used in the work are taxed when you buy them.** Solder, flux, cleaners and adhesives aren't resold, so `track_stock = false` parts are booked as expenses.
- **Rates:** state 4.5%, plus county and city, so combined rates run up to about 11.5%.
- **Sourcing is destination-based:** the rate is set by where the customer receives the goods.
  - For on-site work, that's the site.
  - For a mail-in, it's the ship-to address.
  - For a counter pickup, it's your location.
  - The invoice picks the jurisdiction from that, and freezes the rate at issue, because rates change.
- **You need an Oklahoma sales tax permit before collecting tax.** With it, you can also buy resale parts tax-free from vendors (`vendor.resale_cert_on_file`).

**Ask your CPA or the OTC before you price, and the answers go straight into `service.taxable`:**
- trip charge, diagnostic fee, minimum service charge;
- a shop-supplies fee;
- shipping and handling charged to a customer;
- the right `taxable` default for a refurbished board sold outright (a sale of goods, not a repair).

I'm not a tax professional; these are the questions, not the answers.

---

## Dashboard (owners)

**Top row: figures for MTD, QTD and YTD**, each against the same period last year. Cash basis by default, with an accrual toggle; your CPA should confirm which you file on.
- **Collected:** payments.
- **Invoiced:** issued totals.
- **Gross profit:** invoiced minus parts cost on those invoices.
- **Net:** gross profit minus expenses and mileage.
- **A/R outstanding,** with aging 0–30 / 31–60 / 61–90 / 90+.
- **Sales tax collected and not yet filed:** a liability, not revenue.
- **Set-aside:** net × a percentage you choose with your CPA. Blank until you set it.

**Work, each a count that links to its list:**
- estimates awaiting approval;
- work orders by status;
- boards in the shop, with days since intake, flagged past your storage threshold;
- unbilled time;
- jobs completed but not invoiced;
- appointments today and this week.

**Money:**
- invoices overdue and due this week;
- unapplied payments and credits;
- reimbursements owed to a partner.

**Parts:**
- to order, grouped by vendor;
- POs in transit, with tracking links and ETAs;
- delivered but not yet received;
- low stock;
- parts that arrived for a waiting job.

**Warranty:** active warranties expiring this month; comeback rate over 90 days.

**Charts** (Chart.js, vendored like the tracker's): revenue by month for 12 months (collected vs invoiced), revenue by customer, and work-order mix by kind.

**Privacy:** a "blur figures" toggle, plus the `tech` role never sees money.

## Reports

- **P&L** by month, quarter or year.
- **A/R aging.**
- **Sales tax by period and jurisdiction.**
- **Customer history and asset history:** the "what have we done for them / to this machine" view, with every job, reading, part, invoice and warranty.
- **Parts usage, lot trace and inventory valuation** (FIFO lots).
- **Labor hours by tech.**
- **Job profitability:** price minus parts minus labor at an internal cost rate.
- **Turnaround:** intake to ready.
- **Warranty claims and comeback rate.**
- **Mileage log:** annual miles × rate.
- **1099 reconciliation.**
- **Partner statement.**

Every report exports to CSV.

---

## Expenses, mileage and the partner ledger

- **`expense`**
  - `date`, `payee`, `category`, `amount`, `tax_paid`
  - `paid_with`: business, or a partner's personal money, which creates a reimbursement
  - `receipt_file`, `wo_id?` (job cost), `note`
  - Categories are seeded close to Schedule C lines (supplies, tools/equipment, car/truck, shipping, software/subscriptions, education/manuals, fees, other) and are editable.
  - **Inventory parts are not expenses.** They hit the books as COGS when used, so nothing is counted twice.
- **`partner_ledger`**
  - `user_id`, `date`
  - `kind`: contribution | draw | reimbursement | distribution
  - `amount`, `note`
  - Split mode lives in Settings: equal, by labor hours, or a custom percentage. The partner statement shows each of you your share and your balance.
- **How you're set up** (a partnership return, or one of you paying the other as a contractor) is a question for a CPA. The ledger works either way. If one of you pays the other as a contractor, the $2,000 1099-NEC threshold applies to you as a payer too.

---

## Security, backups and retention

- **Logins:**
  - Flask-Login, scrypt hashes.
  - **TOTP 2FA required for owners**, recommended for everyone (pyotp; the QR shown once at setup).
  - Flask-Limiter on login.
  - Secure, HttpOnly, SameSite cookies; 12-hour sessions; CSRF on every form.
- **Exposure:**
  - The app listens on 127.0.0.1 (for the tunnel) and the LXC's LAN address only, never 0.0.0.0. Same `listen.conf` pattern as the tracker.
  - Cloudflare tunnel on its own hostname.
  - **Optional:** Cloudflare Access (email one-time code) in front of everything except `/api/v1/*` (devices) and `/d/*` (customer links). gatbox-sync already explains a 401/403 from something in front of a hub.
- **Customer links:** 32-byte random tokens, read-only, revocable, expiring.
  - Defaults: estimates 30 days; invoices until paid + 90 days.
  - `X-Robots-Tag: noindex`, rate-limited.
  - A link exposes that one document and nothing else.
- **Database:** its own `shop` database and role on the existing Postgres host. No grants between `shop` and the tracker's role. `pg_hba` admits the shop role only from the shop LXC's address, over TLS if the host offers it.
- **Backups:**
  - `deploy.sh` takes a `pg_dump` before every migration, as the tracker's does.
  - A nightly `pg_dump -Fc`, kept as **14 daily, 12 monthly and 7 yearly** copies.
  - **The file store (`/var/lib/shop-hub/files`) is backed up with the database.** A database whose signatures and frozen PDFs are gone isn't a complete record.
  - **One encrypted copy leaves the house** (restic to object storage, or a rotated USB drive). A cluster is not a backup against a house fire.
  - A **monthly restore drill** (`scripts/restore_drill.sh`): restore the latest dump into `shop_restore_test`, check row counts, and re-hash every issued invoice PDF against `pdf_sha256`.
- **Retention:** IRS record-keeping periods run from 3 to 7 years depending on the situation. The yearly archives cover 7. Don't delete a customer who has invoices; deactivate them.
- **Repo:** **private**, with no real customer data in it, ever. Tests use synthetic fixtures. `.env`, dumps and files stay out of git. Keep the `TZ=UTC` commit habit anyway.

## Deploy

Mirror the tracker's runbook (`deploy/RUNBOOK.md`), which you already run:
- a new **unprivileged Debian 13 LXC**;
- the code at `/opt/shop-hub`, owned by root;
- the venv outside it at `/opt/shop-hub-venv`;
- a service user owning only its writable paths (`files/`, `backups/`, `logs/`);
- waitress behind `shop-hub.service` with `ProtectSystem=strict`;
- `.env` (`DATABASE_URL`, `SECRET_KEY`, `SHOP_BASE_URL`, `SHOP_TZ`) owned by root, mode 640;
- `deploy/deploy.sh --check | (run)`: `pg_dump` → `git pull --ff-only` → `pip install` → `flask db upgrade` → restart → health check, printing the rollback commands on failure;
- WeasyPrint's system libraries (Pango and friends) from apt, listed in the runbook.

---

## UI

- **The phone is the main screen.** A bar owner is standing next to you, and your partner is on their own phone. Mobile-first, server-rendered Flask/Jinja, plain JS, no CDN, vendored libraries.
- **House style** in the app: the synthwave tokens from GATBOX's CLAUDE.md, Chakra Petch / Share Tech Mono served locally.
- **Navigation:** Dashboard · Work · Customers · Assets · Billing · Parts · Purchasing · Expenses · Reports · Settings.
- **Global search** across customer, phone, asset tag, serial, any document number and tracking number.
- **Scanning:** an asset QR opens the asset. A bin label opens the part, with **+1 / −1 / count**. The EY-H2 works anywhere a field has focus.
- **Work board:** WOs as columns by status. Tap through to job → 3C, timer, parts, readings, photos, sign-off.

---

## Phases

Each phase runs: design note → your OK → code + tests → diff, tests and privacy output → your OK → PR → you deploy (⏸).

| # | Where | What | Exit test |
|---|---|---|---|
| **0 Foundation** | shop-hub | Repo, app factory, Postgres + Flask-Migrate, users/roles/TOTP, settings, audit trigger, `doc_counter`, LXC runbook, `deploy.sh`, nightly backup, restore drill, CI against a real Postgres | ⏸ You and your partner log in with 2FA from phones over the tunnel; `deploy.sh` round-trips; the restore drill passes |
| **1 Customers & assets** | shop-hub | Customers, contacts, sites, assets (machines and boards, parent/child), asset events, labels, comm log, global search | ⏸ A printed asset label opens the asset on a phone |
| **2 Price book & parts** | shop-hub | Services, job templates, labor policy, parts, lots, the stock ledger, counts, vendors, markup tiers, bin labels | Stock on hand = sum of moves, under test; a count adjustment leaves an audit row |
| **3 Work** | shop-hub | Estimates (approval, signature, revisions, NTE, deposit), WOs, jobs (3C, intake), lines, timer, part issue / reserve / customer-supplied, manual readings, photos, claim ticket and service report PDFs, `/d/` links, appointments with .ics | ⏸ An estimate is approved on a phone through a link; it converts; the timer runs; the service report prints |
| **4 Billing** | shop-hub | Tax jurisdictions, invoice issue (snapshot, number, frozen PDF, warranty dates), payments and allocations, deposits, credit memos, voids, reversals, statements, receipts, immutability triggers | Editing an issued invoice line fails *in the database*; a partial + final payment closes the invoice; numbers stay gapless across a forced rollback |
| **5 Purchasing** | shop-hub | Shopping list, POs, lines tied to jobs, shipments + tracking links, receive → lot + reservation + WO status, vendor RMAs | Receiving a line tied to a job reserves it and moves that WO out of `waiting_parts` |
| **6 Dashboard & books** | shop-hub | Dashboard, reports, expenses, mileage, partner ledger, sales-tax filings, year-end package, 1099 reconciliation | Figures in the year-end package reconcile to the dashboard to the cent on synthetic data |
| **7 GATBOX link (hub side)** | shop-hub | Devices + tokens, vendored contract v1 with the checksum test, health (`capabilities: ["jobs"]`), roster of active assets (with `model_key`), ingest (sessions, readings, orders → a new job, session tags), open jobs per asset, phase tagging on the job page | Posting the contract examples twice gives created, then duplicate; an unknown slug is rejected |
| **8 GATBOX multi-hub** | Gatbox (public) | `hubs.d`, per-hub caches and routing, the conflict flag, picker grouped by hub, `model_key` → manuals, capability-gated JOB card with AS-FOUND / AS-LEFT | ⏸ The same Pi, in one session each: a barcade trace lands in the tracker, a customer trace lands on the shop job, and neither shows up in the other |

**Before Phase 4 ships:** invoice your partner's current job however you have to, and keep the paperwork. Enter it after Phase 4 as the first real invoice, with its true issue date. Counters start at 1 for 2026.

## Parked: not in v1, and the schema leaves room

- **Card payments** (Square or Stripe links on invoices). This only adds a `payment.method` value and a link field.
- **Automatic tracking status** through an aggregator API. Carriers' own APIs each want a developer account. The `status_source` field is ready.
- **SMTP email and SMS reminders.** v1 uses your phone's share sheet.
- **Board exchange / cores:** a rebuilt unit goes out of shop stock, the customer's core comes in, and there's a refundable core charge. The asset model already allows shop-owned assets.
- **A customer portal** (every document for that customer behind one link).
- **ROM dump evidence** from GATBOX (needs contract v2).
- **Recurring service agreements:** a monthly PM visit for a bar, invoiced on a schedule.

---

## Decisions for you

Recommendations first.

1. **Repo and app name.** `shop-hub` is a working name. The business name, address and logo on documents are settings either way. Is the side business under the GDD brand or its own name?
2. **Cash or accrual on the dashboard.** Recommended: cash by default, with an accrual toggle. Confirm with your CPA.
3. **Labor billing default.** Recommended: actual time rounded to 0.25 h with a 0.5 h minimum per job, with flat rate per template where you've priced the job.
4. **Default warranty.** Your call, per service and part. I'd rather you pick the number than me.
5. **Profit split.** Equal, by labor hours, or a custom percentage.
6. **Cloudflare Access in front of the app.** Recommended yes, with bypasses for `/api/v1` and `/d/`. It's cheap insurance for a site that holds customer and money records.
7. **Contract v2 ownership.** Recommended: GATBOX, when v2 is needed.
8. **Sales tax permit.** Do you already have one? Until you do, invoices can't show tax, and the app should refuse to issue a taxable line with tax switched off.

---

**Sources:**
- Both repositories at the commits above
- [IRS: 2026 business standard mileage rate, 72.5 cents per mile](https://www.irs.gov/newsroom/irs-sets-2026-business-standard-mileage-rate-at-725-cents-per-mile-up-25-cents)
- [Patriot Software: 1099 reporting threshold rises from $600 to $2,000 for 2026 (OBBBA)](https://www.patriotsoftware.com/blog/accounting/1099-reporting-threshold/)
- [OAC 710:65-19-11, Automotive repair (Cornell LII)](https://law.cornell.edu/regulations/oklahoma/OAC-710-65-19-11)
- [OAC 710:65-19-310, Shoe repairs (Justia)](https://regulations.justia.com/states/oklahoma/title-710/chapter-65/subchapter-19/part-37/section-710-65-19-310)
- [Stripe: Oklahoma sales tax rates and sourcing](https://stripe.com/au/resources/more/oklahoma-sales-tax-rate)
