import re

from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, TextAreaField
from wtforms.validators import URL, DataRequired, Length, Optional, ValidationError

from app.customers.forms import opt_int
from app.forms_common import blank_to_none, cost_field, money_field, optional_text, qty_field

EQUIV_MAX = 20


def split_equivalents(value):
    """'74HCT245, 74ALS245; 74F245' -> ['74HCT245', '74ALS245', '74F245'], deduped."""
    if isinstance(value, list):
        return value
    seen, out = set(), []
    for item in re.split(r"[,;\n]", value or ""):
        item = item.strip()
        if item and item.upper() not in seen:
            seen.add(item.upper())
            out.append(item)
    return out


class EquivalentsField(StringField):
    def process_data(self, value):
        self.data = list(value or [])   # a list even when nothing was posted at all

    def _value(self):
        return ", ".join(self.data or [])

    def process_formdata(self, valuelist):
        self.data = split_equivalents(valuelist[0] if valuelist else "")


def check_equivalents(form, field):
    field.data = field.data or []
    if len(field.data) > EQUIV_MAX:
        raise ValidationError(f"At most {EQUIV_MAX}.")
    if any(len(e) > 80 for e in field.data):
        raise ValidationError("Each one at most 80 characters.")


# The owner sets what parts cost and sell for; routes drop these for anyone else.
PRICING_FIELDS = ("default_cost", "price_mode", "sell_price")


class PartForm(FlaskForm):
    sku = StringField("SKU (your stock number)", filters=[blank_to_none],
                      validators=[DataRequired(), Length(max=40)])
    name = StringField("Name", filters=[blank_to_none], validators=[DataRequired(), Length(max=160)])
    category = optional_text("Category (caps, ICs, flybacks…)", 60)
    manufacturer = optional_text("Manufacturer", 80)
    mpn = optional_text("Manufacturer part no.", 80)
    equivalents = EquivalentsField("Equivalents (comma-separated)", validators=[check_equivalents])
    unit = StringField("Unit", filters=[blank_to_none], default="each",
                       validators=[DataRequired(), Length(max=16)])
    default_cost = cost_field("Default cost (per unit, up to 4 decimals)")
    price_mode = SelectField("Pricing", choices=[("fixed", "Fixed sell price"),
                                                 ("markup", "Markup on cost")])
    sell_price = money_field("Sell price")
    taxable = BooleanField("Taxable", default=True)
    track_stock = BooleanField("Track stock (off for consumables: solder, flux, cleaner)",
                               default=True)
    reorder_point = qty_field("Reorder at (on hand at or below)", positive=False)
    reorder_qty = qty_field("Reorder quantity")
    preferred_vendor_id = SelectField("Preferred vendor", coerce=opt_int)
    bin = optional_text("Bin", 40)
    barcode = optional_text("Barcode", 64)
    datasheet_url = optional_text("Datasheet URL", 300, URL(message="Not a URL."))
    notes = TextAreaField("Notes", filters=[blank_to_none], validators=[Optional(), Length(max=4000)])
    active = BooleanField("Active", default=True)

    def validate_reorder_point(self, field):
        if field.data is not None and field.data < 0:
            raise ValidationError("Can't be negative.")


class ReceiveForm(FlaskForm):
    qty = qty_field("Quantity", required=True)
    unit_cost = cost_field("Unit cost")
    vendor_id = SelectField("Vendor", coerce=opt_int)
    date_code = optional_text("Date code", 20)
    vendor_lot = optional_text("Vendor lot", 40)
    note = optional_text("Note", 200)


class AdjustForm(FlaskForm):
    # A direction plus a positive quantity: a phone's decimal keypad has no minus sign.
    direction = SelectField("Stock is", choices=[("down", "Missing: take out"),
                                                 ("up", "Found: put in")])
    qty = qty_field("Quantity", required=True)
    lot_id = SelectField("Lot", coerce=opt_int)
    note = StringField("Why", filters=[blank_to_none], validators=[DataRequired(), Length(max=200)])

    @property
    def delta(self):
        return self.qty.data if self.direction.data == "up" else -self.qty.data


class ScrapForm(FlaskForm):
    qty = qty_field("Quantity", required=True)
    lot_id = SelectField("From lot", coerce=opt_int)
    note = optional_text("Why (bad date code, damaged…)", 200)


class CountForm(FlaskForm):
    """CSRF only; the counted quantities are parsed per row in the route."""


class VendorForm(FlaskForm):
    name = StringField("Name", filters=[blank_to_none], validators=[DataRequired(), Length(max=120)])
    website = optional_text("Website", 200, URL(message="Not a URL."))
    account_ref = optional_text("Our account no.", 60)
    contact = optional_text("Contact (rep, phone, email)", 200)
    resale_cert_on_file = BooleanField("Our resale certificate is on file with them")
    notes = TextAreaField("Notes", filters=[blank_to_none], validators=[Optional(), Length(max=4000)])
    active = BooleanField("Active", default=True)
