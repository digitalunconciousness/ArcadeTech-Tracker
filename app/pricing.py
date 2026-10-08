"""Prices, margins and billable hours. Decimal only. Amounts round half-up to the cent;
unit costs keep four places (NUMERIC(14,4)) until they become an amount."""

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.extensions import db

CENT = Decimal("0.01")
TENTH = Decimal("0.1")


def round_money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def load_tiers():
    from app.models import MarkupTier

    return db.session.scalars(select(MarkupTier).order_by(MarkupTier.min_cost)).all()


def tier_for(cost, tiers):
    """The bracket with min_cost <= cost < max_cost (a null max is open-ended), or None."""
    for tier in tiers:
        if tier.min_cost <= cost and (tier.max_cost is None or cost < tier.max_cost):
            return tier
    return None


def markup_price(cost, tiers):
    tier = tier_for(cost, tiers)
    return None if tier is None else round_money(cost * tier.multiplier)


def part_price(part, tiers):
    """What the customer pays per unit, or None while it can't be priced: a fixed part
    with no sell price, or a markup part with no cost or no bracket covering its cost.
    Markup works from default_cost, the part's replacement cost."""
    if part.price_mode == "fixed":
        return part.sell_price
    if part.default_cost is None:
        return None
    return markup_price(part.default_cost, tiers)


def margin_pct(price, cost):
    """Gross margin as a percent of the price, to a tenth: (price - cost) / price."""
    if price is None or cost is None or price <= 0:
        return None
    return ((price - cost) / price * 100).quantize(TENTH, rounding=ROUND_HALF_UP)


def multiplier_margin(multiplier):
    """The margin a markup multiplier gives: ×2 is 50%."""
    return ((1 - 1 / Decimal(multiplier)) * 100).quantize(TENTH, rounding=ROUND_HALF_UP)


def bill_hours(actual, increment, minimum):
    """Labor policy: actual hours rounded UP to the increment, then at least the
    minimum. No time at all bills nothing."""
    actual = Decimal(actual)
    if actual < 0:
        raise ValueError("negative hours")
    if actual == 0:
        return Decimal("0.00")
    steps = (actual / increment).to_integral_value(rounding=ROUND_CEILING)
    return max(steps * increment, minimum).quantize(CENT)
