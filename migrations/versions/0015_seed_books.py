"""seed expense categories and the 2026 mileage rates (data migration)

Revision ID: 0015_seed_books
Revises: 0014_books
Create Date: 2026-10-13

Categories close to the Schedule C expense lines (the plan's list, editable in Books; a
partnership files Form 1065, so the line numbers are a guide for the CPA, not a form).
Three have codes because the app books purchase orders into them: supplies (stock the
shop doesn't track), shipping, and sales tax paid on purchases.

Mileage: the IRS business standard rate for 2026, 72.5 cents from January 1 (Notice
2026-10, IR-2025-128) and 76 cents from July 1 (Announcement 2026-11, the midyear
increase), both checked 2026-10-12. The owner confirms them in Settings → Books.
Existing rows are left alone, so re-running is harmless.

Downgrade deletes only these rows, and fails once an expense uses a category (by design:
those are real records).
"""
from alembic import op

revision = "0015_seed_books"
down_revision = "0014_books"
branch_labels = None
depends_on = None

# name, code, Schedule C line, position
CATEGORIES = (
    ("Supplies", "supplies", "Line 22: Supplies", 10),
    ("Tools and equipment", None, "Line 22 (small tools) or 13: ask your CPA", 20),
    ("Shipping", "shipping", "Line 27a: Other expenses", 30),
    ("Sales tax on purchases", "purchase_tax", "Ask your CPA: part of cost, or line 23", 40),
    ("Software and subscriptions", None, "Line 18 or 27a", 50),
    ("Education and manuals", None, "Line 27a: Other expenses", 60),
    ("Bank and payment fees", None, "Line 27a: Other expenses", 70),
    ("Parking and tolls", None, "Line 9: Car and truck (with the mileage rate)", 80),
    ("Advertising", None, "Line 8: Advertising", 90),
    ("Insurance", None, "Line 15: Insurance", 100),
    ("Licenses and permits", None, "Line 23: Taxes and licenses", 110),
    ("Legal and professional", None, "Line 17: Legal and professional", 120),
    ("Office", None, "Line 18: Office expense", 130),
    ("Contract labor", None, "Line 11: Contract labor", 140),
    ("Repairs to shop equipment", None, "Line 21: Repairs and maintenance", 150),
    ("Other", None, "Line 27a: Other expenses", 160),
)

# effective from, dollars per mile, source
RATES = (
    ("2026-01-01", "0.7250", "IRS Notice 2026-10 (IR-2025-128)"),
    ("2026-07-01", "0.7600", "IRS Announcement 2026-11 (midyear increase)"),
)


def _q(value):
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


# tests/conftest.py runs these after each test's TRUNCATE.
SEED_SQL = (
    "INSERT INTO expense_category (name, code, schedule_c, position) VALUES "
    + ", ".join(f"({_q(n)}, {_q(c)}, {_q(s)}, {p})" for n, c, s, p in CATEGORIES)
    + " ON CONFLICT (name) DO NOTHING; "
    "INSERT INTO mileage_rate (effective_from, rate, source) VALUES "
    + ", ".join(f"('{d}', {r}, {_q(s)})" for d, r, s in RATES)
    + " ON CONFLICT (effective_from) DO NOTHING"
)


def upgrade():
    op.execute(SEED_SQL)


def downgrade():
    dates = ", ".join(f"'{d}'" for d, *_ in RATES)
    names = ", ".join(_q(n) for n, *_ in CATEGORIES)
    op.execute(f"DELETE FROM mileage_rate WHERE effective_from IN ({dates})")
    op.execute(f"DELETE FROM expense_category WHERE name IN ({names})")
