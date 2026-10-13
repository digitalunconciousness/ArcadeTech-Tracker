"""The top navigation: six sections, each with a row of tabs, chosen by the request's
blueprint (or endpoint, where one blueprint serves two tabs)."""

from flask import request

from app.auth.decorators import COST_ROLES

# (key, label, roles or None for everyone, [(tab label, endpoint, matches)]).
# A match with a dot is an endpoint, without one a blueprint; endpoints win.
SECTIONS = (
    ("dashboard", "Dashboard", None, [("Dashboard", "dashboard.index", ("dashboard",))]),
    ("work", "Work", None, [("Work orders", "work.index", ("work", "traces")),
                            ("Estimates", "estimates.index", ("estimates",))]),
    ("customers", "Customers", None, [("Customers", "customers.index", ("customers",)),
                                      ("Assets", "assets.index", ("assets",))]),
    ("money", "Money", COST_ROLES, [
        ("Invoices", "billing.index", ("billing",)),
        ("Payments", "billing.payment_list", (
            "billing.payment_list", "billing.payment", "billing.receipt_pdf", "billing.reverse",
            "billing.refund_payment", "billing.apply_payment", "billing.customer_payment")),
        ("Books", "books.index", ("books",)),
        ("Reports", "reports.index", ("reports",))]),
    ("parts", "Parts", None, [("Parts", "parts.index", ("parts",)),
                              ("Links", "partlinks.index", ("partlinks",)),
                              ("Buying", "purchasing.index", ("purchasing",)),
                              ("Price book", "pricebook.index", ("pricebook",))]),
    ("settings", "Settings", ("owner",), [("Settings", "settings.index", ("settings",))]),
)


def current(role):
    """{"sections": [(label, endpoint, active)], "tabs": [(label, endpoint, active)]}: the
    sections this role sees, and the tabs of the one this page is in (when it has more
    than one)."""
    endpoint, blueprint = request.endpoint or "", request.blueprint or ""
    visible = [s for s in SECTIONS if s[2] is None or role in s[2]]
    here = None
    for match in (endpoint, blueprint):
        for key, _, _, tabs in visible:
            for label, _, matches in tabs:
                if match in matches:
                    here = here or (key, label)
    sections = [(label, tabs[0][1], here is not None and key == here[0])
                for key, label, _, tabs in visible]
    tabs = []
    if here is not None:
        section_tabs = next(tabs for key, _, _, tabs in visible if key == here[0])
        if len(section_tabs) > 1:
            tabs = [(label, ep, label == here[1]) for label, ep, _ in section_tabs]
    return {"sections": sections, "tabs": tabs}
