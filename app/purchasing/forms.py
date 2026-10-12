import re

from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, IntegerField, SelectField, StringField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, Regexp, ValidationError

from app.customers.forms import opt_int
from app.forms_common import blank_to_none, cost_field, money_field, optional_text, qty_field
from app.models import SHIPMENT_STATUSES


class AddToListForm(FlaskForm):
    part_id = SelectField("Part", coerce=opt_int, validate_choice=False,
                          validators=[DataRequired()])
    qty = qty_field("Qty", required=True)
    vendor_id = SelectField("Buy from", coerce=opt_int, validate_choice=False)
    note = optional_text("Note", 200)


class SuggestionForm(FlaskForm):
    qty = qty_field("Qty", required=True)


class ListLineForm(FlaskForm):
    qty = qty_field("Qty", required=True)
    vendor_id = SelectField("Buy from", coerce=opt_int, validate_choice=False)
    unit_cost = cost_field("Unit cost")
    note = optional_text("Note", 200)


class OrderForJobForm(FlaskForm):
    part_id = SelectField("Part", coerce=opt_int, validate_choice=False,
                          validators=[DataRequired()])
    qty = qty_field("Qty", required=True)
    note = optional_text("Note", 200)
    wait = BooleanField("Put the work order on Waiting on parts", default=True)


class OrderedForm(FlaskForm):
    ordered_on = DateField("Ordered on", validators=[DataRequired()])
    vendor_order_ref = optional_text("Their order number", 60)
    paid_by_user_id = SelectField("Paid with", coerce=opt_int, validate_choice=False)


class PoLineForm(FlaskForm):
    qty = qty_field("Qty", required=True)
    unit_cost = cost_field("Unit cost", required=True)
    expected_on = DateField("Expected", validators=[Optional()])
    backordered = BooleanField("Backordered")


class AmountsForm(FlaskForm):
    shipping = money_field("Shipping", required=True)
    tax_paid = money_field("Sales tax paid", required=True)
    notes = optional_text("Notes", 2000)


class ShipmentForm(FlaskForm):
    direction = SelectField("Which way", choices=[
        ("inbound_customer", "Coming from the customer"),
        ("outbound_customer", "Going to the customer")])
    tracking_number = StringField("Tracking number", filters=[blank_to_none],
                                  validators=[DataRequired(), Length(max=60)])
    carrier_id = SelectField("Carrier", coerce=opt_int, validate_choice=False)
    shipped_on = DateField("Shipped", validators=[Optional()])
    eta = DateField("Expected", validators=[Optional()])
    cost = money_field("What we paid to ship")
    note = optional_text("Note", 200)


class ShipmentEditForm(FlaskForm):
    status = SelectField("Status", choices=list(SHIPMENT_STATUSES.items()))
    carrier_id = SelectField("Carrier", coerce=opt_int, validate_choice=False)
    eta = DateField("Expected", validators=[Optional()])
    delivered_on = DateField("Delivered on", validators=[Optional()])
    cost = money_field("What we paid to ship")
    note = optional_text("Note", 200)


def valid_regex(form, field):
    if field.data:
        try:
            re.compile(field.data)
        except re.error as e:
            raise ValidationError(f"Not a pattern: {e}.") from None


def has_number(form, field):
    if field.data and "{number}" not in field.data:
        raise ValidationError("Put {number} where the tracking number goes.")


class CarrierForm(FlaskForm):
    code = StringField("Short code", filters=[lambda v: (v or "").strip().lower() or None],
                       validators=[DataRequired(), Regexp(r"^[a-z0-9_]{2,16}$",
                                                          message="2–16 of a–z, 0–9, _.")])
    name = StringField("Name", filters=[blank_to_none], validators=[DataRequired(),
                                                                   Length(max=60)])
    url_template = StringField("Tracking page", filters=[blank_to_none], validators=[
        DataRequired(), Length(max=300), Regexp(r"^https://\S+$", message="An https:// address."),
        has_number])
    number_pattern = optional_text("Tracking-number pattern (to guess the carrier)", 200,
                                   valid_regex)
    position = IntegerField("Order", default=0, validators=[NumberRange(min=0, max=1000)])
    active = BooleanField("Active", default=True)
