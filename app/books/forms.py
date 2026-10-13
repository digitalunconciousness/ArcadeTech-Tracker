import re
from decimal import Decimal

from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import BooleanField, DateField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError

from app.customers.forms import opt_int
from app.forms_common import (
    MONEY_MAX,
    blank_to_none,
    decimal_field,
    money_field,
    optional_text,
)
from app.models import LEDGER_KINDS, SPLIT_MODES

WO_RE = re.compile(r"^WO-\d{4}-\d{4,}$")


def wo_number(form, field):
    if field.data:
        field.data = field.data.strip().upper()
        if not WO_RE.match(field.data):
            raise ValidationError("A work order number looks like WO-2026-0012.")


class ExpenseForm(FlaskForm):
    spent_on = DateField("Date", validators=[DataRequired()])
    payee = StringField("Paid to", filters=[blank_to_none],
                        validators=[DataRequired(), Length(max=120)])
    category_id = SelectField("Category", coerce=opt_int, validate_choice=False,
                              validators=[DataRequired()])
    amount = decimal_field("Amount", 2, Decimal(0), MONEY_MAX, required=True,
                           min_exclusive=True)
    tax_included = money_field("Sales tax in that")
    paid_by_user_id = SelectField("Paid with", coerce=opt_int, validate_choice=False)
    wo = optional_text("For work order (optional)", 20, wo_number)
    note = TextAreaField("Note", filters=[blank_to_none], validators=[Optional(), Length(max=2000)])
    receipt = FileField("Receipt (photo or PDF)")


class VoidForm(FlaskForm):
    reason = StringField("Why", filters=[blank_to_none],
                         validators=[DataRequired(), Length(max=300)])


class TripForm(FlaskForm):
    trip_on = DateField("Date", validators=[DataRequired()])
    user_id = SelectField("Driver", coerce=opt_int, validate_choice=False,
                          validators=[DataRequired()])
    miles = decimal_field("Miles", 1, Decimal(0), Decimal("9999.9"), required=True,
                          min_exclusive=True)
    from_place = optional_text("From", 120)
    to_place = optional_text("To", 120)
    purpose = StringField("What for", filters=[blank_to_none],
                          validators=[DataRequired(), Length(max=200)])
    own_vehicle = BooleanField("In the driver's own vehicle", default=True)
    wo = optional_text("For work order (optional)", 20, wo_number)


class EntryForm(FlaskForm):
    user_id = SelectField("Partner", coerce=opt_int, validate_choice=False,
                          validators=[DataRequired()])
    kind = SelectField("What", choices=list(LEDGER_KINDS.items()))
    amount = decimal_field("Amount", 2, Decimal(0), MONEY_MAX, required=True,
                           min_exclusive=True)
    entry_on = DateField("Date", validators=[DataRequired()])
    note = optional_text("Note", 300)


class FilingForm(FlaskForm):
    period_start = DateField("Period from", validators=[DataRequired()])
    period_end = DateField("to", validators=[DataRequired()])
    tax_due = money_field("Tax due on the return", required=True)
    filed_on = DateField("Filed on", validators=[Optional()])
    paid_on = DateField("Paid on", validators=[Optional()])
    confirmation = optional_text("Confirmation number", 60)
    note = optional_text("Note", 300)


class BooksSettingsForm(FlaskForm):
    set_aside_pct = decimal_field("Set aside for taxes (% of net)", 2, Decimal(0), Decimal(100))
    storage_days = IntegerField("Flag boards in the shop after (days)",
                                validators=[Optional(), NumberRange(min=1, max=3650)])
    split_mode = SelectField("Profit split", choices=list(SPLIT_MODES.items()))
    labor_cost_rate = money_field("Internal labor cost per hour (job profitability)")
    nec_threshold = money_field("1099-NEC threshold", required=True)


class RateForm(FlaskForm):
    effective_from = DateField("From", validators=[DataRequired()])
    rate = decimal_field("Dollars per mile", 4, Decimal(0), Decimal(10), required=True,
                         min_exclusive=True)
    source = optional_text("Source", 200)


class CategoryForm(FlaskForm):
    name = StringField("Name", filters=[blank_to_none], validators=[DataRequired(),
                                                                   Length(max=80)])
    schedule_c = optional_text("Schedule C line (for the CPA)", 80)
    position = IntegerField("Order", default=200, validators=[NumberRange(min=0, max=10000)])
    active = BooleanField("Active", default=True)


class ReceiptForm(FlaskForm):
    receipt = FileField("Receipt (photo or PDF)")
