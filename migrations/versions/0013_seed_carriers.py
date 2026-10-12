"""seed the carriers and their tracking links (data migration)

Revision ID: 0013_seed_carriers
Revises: 0012_purchasing
Create Date: 2026-10-12

USPS, UPS, FedEx and DHL Express, each with its tracking-page template and a pattern for
guessing the carrier from a tracking number. Templates looked up by web search on
2026-10-12 (community-documented link formats; none of the carriers publishes one, and
the carriers' sites weren't reachable from the build machine), so check each with the
first real number and fix it in Settings → Carriers if a carrier has moved its page.
Existing codes are left alone, so re-running is harmless.

Downgrade deletes only these codes, and fails once a shipment uses one (by design: those
are real records).
"""
from alembic import op

revision = "0013_seed_carriers"
down_revision = "0012_purchasing"
branch_labels = None
depends_on = None

# code, name, url template, number pattern (spaces removed, upper case), position
CARRIERS = (
    ("ups", "UPS", "https://www.ups.com/track?loc=en_US&tracknum={number}",
     "^1Z[0-9A-Z]{16}$", 10),
    ("usps", "USPS", "https://tools.usps.com/go/TrackConfirmAction?qtc_tLabels1={number}",
     "^(9[1-5][0-9]{20}|9[1-5][0-9]{24}|[A-Z]{2}[0-9]{9}US)$", 20),
    ("fedex", "FedEx", "https://www.fedex.com/fedextrack/?trknbr={number}",
     "^([0-9]{12}|[0-9]{15}|[0-9]{20})$", 30),
    ("dhl", "DHL Express", "https://www.dhl.com/en/express/tracking.html?AWB={number}",
     "^([0-9]{10}|JJD[0-9]{10,18})$", 40),
)

# tests/conftest.py runs this after each test's TRUNCATE.
SEED_SQL = (
    "INSERT INTO carrier (code, name, url_template, number_pattern, position) VALUES "
    + ", ".join(f"('{c}', '{n}', '{u}', '{p}', {pos})" for c, n, u, p, pos in CARRIERS)
    + " ON CONFLICT (code) DO NOTHING"
)


def upgrade():
    op.execute(SEED_SQL)


def downgrade():
    codes = ", ".join(f"'{c}'" for c, *_ in CARRIERS)
    op.execute(f"DELETE FROM carrier WHERE code IN ({codes})")
