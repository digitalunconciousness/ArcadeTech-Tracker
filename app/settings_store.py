from flask import g

from app.extensions import db
from app.models.settings import DEFAULT_ACCENT, DEFAULT_BUSINESS_NAME, ShopSetting


def get_settings():
    """The shop_setting row, once per request. The row is seeded by migration 0002;
    should it ever be missing, pages still render with the defaults."""
    if "shop_settings" not in g:
        row = db.session.get(ShopSetting, True)
        g.shop_settings = row or ShopSetting(
            id=True, business_name=DEFAULT_BUSINESS_NAME, doc_accent_color=DEFAULT_ACCENT
        )
    return g.shop_settings
