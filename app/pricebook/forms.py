from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import (
    DataRequired,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    Regexp,
    ValidationError,
)

from app.forms_common import blank_to_none, decimal_field, money_field, optional_text, qty_field
from app.models.pricebook import SERVICE_KINDS, SERVICE_UNITS

KIND_LABELS = {
    "labor": "Labor", "fee": "Fee", "trip": "Trip", "diagnostic": "Diagnostic",
    "shop_supplies": "Shop supplies", "sublet": "Sublet", "discount": "Discount",
}
# Kinds whose amount is set per job, so a $0.00 book rate is normal for them.
RATE_PER_JOB = ("sublet", "discount")


def upper_code(value):
    return value.strip().upper() if isinstance(value, str) else value


class ServiceForm(FlaskForm):
    code = StringField("Code", filters=[upper_code], validators=[
        DataRequired(), Regexp(r"^[A-Z0-9][A-Z0-9-]{0,19}$",
                               message="Up to 20 letters, digits and dashes, e.g. LAB-BENCH.")])
    name = StringField("Name", filters=[blank_to_none], validators=[DataRequired(), Length(max=120)])
    kind = SelectField("Kind", choices=[(k, KIND_LABELS[k]) for k in SERVICE_KINDS])
    unit = SelectField("Per", choices=[(u, u) for u in SERVICE_UNITS])
    rate = money_field("Rate", required=True)
    taxable = BooleanField("Taxable (separately stated labor isn't)")
    warranty_days = IntegerField("Warranty (days)", default=365,
                                 validators=[InputRequired(), NumberRange(min=0, max=3650)])
    description = TextAreaField("Description (printed on estimates)", filters=[blank_to_none],
                                validators=[Optional(), Length(max=2000)])
    active = BooleanField("Active: can go on estimates")

    def validate_active(self, field):
        if field.data and self.rate.data == 0 and self.kind.data not in RATE_PER_JOB:
            raise ValidationError("Set a rate before switching it on.")


class TemplateForm(FlaskForm):
    name = StringField("Name", filters=[blank_to_none], validators=[DataRequired(), Length(max=120)])
    description = TextAreaField("Description", filters=[blank_to_none],
                                validators=[Optional(), Length(max=2000)])
    est_hours = decimal_field("Estimated hours", 2, minimum=0, maximum=9999)
    active = BooleanField("Active", default=True)


class TemplateLineForm(FlaskForm):
    item = SelectField("Service or part")  # "s-<id>" or "p-<id>"; choices set per request
    qty = qty_field("Quantity", required=True)
    note = optional_text("Note", 200)
