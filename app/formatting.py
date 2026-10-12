"""Jinja filters for money, unit costs and quantities. Decimal in, text out."""

from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlsplit

from app.pricing import round_money


def money(value):
    """$1,234.50; -$3.00; blank for None."""
    if value is None:
        return ""
    v = round_money(value)
    return f"{'-' if v < 0 else ''}${abs(v):,.2f}"


def unit_cost(value):
    """Four places when they matter, two otherwise: $0.012, $12.50."""
    if value is None:
        return ""
    v = Decimal(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    whole, frac = f"{abs(v):,.4f}".split(".")
    return f"{'-' if v < 0 else ''}${whole}.{frac.rstrip('0').ljust(2, '0')}"


def qty(value):
    """5, 2.5, 0.125, -3: no trailing zeros."""
    if value is None:
        return ""
    text = f"{Decimal(value):,.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def plain(value):
    """A measured value as typed: 4.98, 0.0001, 12000 (no exponent, no trailing zeros)."""
    if value is None:
        return ""
    text = f"{Decimal(value).normalize():f}"
    return "0" if text in ("-0", "") else text


def pct(value):
    return "" if value is None else f"{value}%"


def rate_pct(value):
    """A tax rate as a percentage: 0.087500 -> 8.75%."""
    if value is None:
        return ""
    text = f"{Decimal(value) * 100:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}%"


def scope_items(value):
    """A fixed price's scope, one item per line, as a list; pasted bullets are dropped."""
    if not value:
        return []
    items = (line.strip().lstrip("-*•·").strip() for line in value.splitlines())
    return [item for item in items if item]


def site(url):
    """'https://www.example.com/p/1' -> 'example.com': where a link goes, for print."""
    try:
        host = (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


FILTERS = {"money": money, "unit_cost": unit_cost, "qty": qty, "pct": pct, "plain": plain,
           "scope_items": scope_items, "rate_pct": rate_pct, "site": site}
