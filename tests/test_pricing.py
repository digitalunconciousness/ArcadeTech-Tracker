"""Labor rounding, markup brackets, margins and the money filters. Pure Decimal; no
database."""

from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.formatting import money, pct, qty, unit_cost
from app.pricing import (
    bill_hours,
    margin_pct,
    markup_price,
    multiplier_margin,
    part_price,
    round_money,
    tier_for,
)

INC, MIN = D("0.25"), D("0.50")


@pytest.mark.parametrize("actual, billed", [
    ("0.1", "0.50"),    # under the minimum
    ("0.5", "0.50"),
    ("0.51", "0.75"),
    ("0.6", "0.75"),
    ("0.75", "0.75"),   # exactly on a step stays
    ("1", "1.00"),
    ("1.01", "1.25"),   # a minute over rounds up a whole step
    ("2.26", "2.50"),
    ("0", "0.00"),      # no time, no minimum
])
def test_bill_hours_table(actual, billed):
    result = bill_hours(D(actual), INC, MIN)
    assert result == D(billed) and isinstance(result, D)
    assert str(result) == billed


def test_bill_hours_other_policies():
    assert bill_hours(D("0.2"), D("0.1"), D("0")) == D("0.20")
    assert bill_hours(D("0.21"), D("0.1"), D("1")) == D("1.00")
    assert bill_hours(D("1.34"), D("0.33"), D("0")) == D("1.65")
    with pytest.raises(ValueError):
        bill_hours(D("-0.1"), INC, MIN)


def tier(lo, hi, mult):
    return SimpleNamespace(min_cost=D(lo), max_cost=None if hi is None else D(hi),
                           multiplier=D(mult))


TIERS = [tier("0", "1", "3"), tier("1", "10", "2"), tier("10", None, "1.5")]


@pytest.mark.parametrize("cost, mult", [
    ("0", "3"), ("0.0001", "3"), ("0.9999", "3"),
    ("1", "2"),            # a bracket's end belongs to the next one
    ("9.9999", "2"),
    ("10", "1.5"), ("50000", "1.5"),
])
def test_tier_edges(cost, mult):
    assert tier_for(D(cost), TIERS).multiplier == D(mult)


def test_markup_price_rounds_half_up_and_gaps_have_no_price():
    assert markup_price(D("0.0120"), TIERS) == D("0.04")      # 0.036 -> 0.04
    assert markup_price(D("0.0050"), TIERS) == D("0.02")      # 0.015 -> 0.02 (half up)
    assert markup_price(D("1.0025"), TIERS) == D("2.01")      # 2.005 -> 2.01
    assert markup_price(D("12.34"), TIERS) == D("18.51")
    gap = [tier("0", "1", "3"), tier("2", None, "2")]
    assert markup_price(D("1.5"), gap) is None
    assert markup_price(D("1"), []) is None


def test_part_price_modes():
    fixed = SimpleNamespace(price_mode="fixed", sell_price=D("4.95"), default_cost=D("1"))
    assert part_price(fixed, TIERS) == D("4.95")
    marked = SimpleNamespace(price_mode="markup", sell_price=D("99"), default_cost=D("2.50"))
    assert part_price(marked, TIERS) == D("5.00")
    assert part_price(SimpleNamespace(price_mode="markup", sell_price=None, default_cost=None),
                      TIERS) is None
    assert part_price(SimpleNamespace(price_mode="fixed", sell_price=None, default_cost=D(1)),
                      TIERS) is None


def test_margins():
    assert margin_pct(D("10.00"), D("4")) == D("60.0")
    assert margin_pct(D("3.00"), D("2")) == D("33.3")
    assert margin_pct(D("2.00"), D("3")) == D("-50.0")
    assert margin_pct(D("0"), D("1")) is None
    assert margin_pct(None, D("1")) is None and margin_pct(D("1"), None) is None
    assert multiplier_margin(D("2")) == D("50.0")
    assert multiplier_margin(D("1.5")) == D("33.3")
    assert multiplier_margin(D("1")) == D("0.0")


def test_round_money_half_up():
    assert round_money(D("2.345")) == D("2.35")
    assert round_money(D("-2.345")) == D("-2.35")
    assert round_money(D("2.344999")) == D("2.34")


def test_filters():
    assert money(D("1234.5")) == "$1,234.50"
    assert money(D("-3")) == "-$3.00"
    assert money(None) == ""
    assert unit_cost(D("0.0120")) == "$0.012"
    assert unit_cost(D("12.5")) == "$12.50"
    assert unit_cost(D("1234.5678")) == "$1,234.5678"
    assert qty(D("5.000")) == "5"
    assert qty(D("2.500")) == "2.5"
    assert qty(D("-0.125")) == "-0.125"
    assert qty(D("0.000")) == "0"
    assert qty(D("1000")) == "1,000"
    assert pct(D("37.5")) == "37.5%"
