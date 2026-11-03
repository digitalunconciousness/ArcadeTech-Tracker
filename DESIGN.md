# shop-hub (ArcadeTech Tracker): design direction

The owner's direction for the app's screens, which are server-rendered and mobile-first. Agents read it, with antislop's UI, copy and accessibility skills, as the
direction to build toward. It was written from the owner's approved design notes (`CLAUDE.md`,
RULES.md §5 and the four-themes decision of 2026-10-28), not invented.

## Identity

The system of record for an arcade-repair business: customers, assets, estimates, work orders,
invoices, payments, parts, purchasing, expenses and mileage.

## Personality

- The reading comes first: one big number, its state as a word beside it, and the action next to it.
- Colour means status and only status. A state always has a word, never colour alone.
- Quiet by default. Glow and scanlines belong to Synthwave alone, and even there only on badges,
  active states and headline edges.
- Anything copied or compared (tags, numbers, hashes) is monospace.

## Themes

Four, chosen per user, at every width (decided 2026-10-28, being built): **Ledger** (light,
professional, the default), **Graphite** (dark, professional), **Follow system** (Ledger or Graphite,
as the device says) and **Synthwave**. Components use roles, never a raw colour.

- **Synthwave is deliberate.** The deep violet gradient, the neon palette (magenta `#ff2e9f`, cyan
  `#7dfaff`, lime `#5ef2b0`, amber `#ffb74d`, red `#ff4d6d` on bg `#150a28`), scanlines and glow on
  badges, active states and headline edges are the owner's chosen identity, shared with the
  bench deck, not defaults. Chakra Petch for display, Share Tech Mono for the body and numbers.
- **Ledger and Graphite are not exempt.** antislop applies to them in full.
  Both use IBM Plex Sans, with IBM Plex Mono for numbers, and neither has glow or scanlines.

## Customer documents

Estimates, claim tickets, service reports, invoices, receipts, statements, credit memos and packing
slips are print-first, whatever the screen theme: a white page, black text, the house fonts and one
accent.

## Mood and use

At least 16 px body text and 44 px touch targets on a phone. Plain HTML, CSS and JS, with every asset
(Chart.js, fonts) vendored: no framework, no build step, no CDN.
