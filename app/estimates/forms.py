from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, HiddenField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from app.customers.forms import opt_int
from app.forms_common import blank_to_none, cost_field, money_field, optional_text, qty_field
from app.models import LABOR_MODES, WO_KINDS
from app.work.forms import MultiCheckboxField


def text_area(label, max_len, required=False):
    first = DataRequired() if required else Optional()
    return TextAreaField(label, filters=[blank_to_none], validators=[first, Length(max=max_len)])


class EstimateForm(FlaskForm):
    summary = StringField("Summary (what it's for)", filters=[blank_to_none],
                          validators=[DataRequired(), Length(max=200)])
    site_id = SelectField("Site", coerce=opt_int)
    valid_until = DateField("Good until", validators=[Optional()])
    not_to_exceed = money_field("Not to exceed (blank: none)")
    deposit_required = money_field("Deposit before work starts (blank: none)")
    notes = text_area("Notes (printed on the estimate)", 4000)


class NewEstimateForm(EstimateForm):
    asset_id = SelectField("Machine or board", coerce=opt_int)
    complaint = text_area("What's wrong (in the customer's words)", 4000, required=True)


class ServiceLineForm(FlaskForm):
    estimate_job_id = SelectField("Job", coerce=int)
    service_id = SelectField("Service", coerce=int)
    labor_mode = SelectField("Labor hours", choices=list(LABOR_MODES.items()), default="flat")
    qty = qty_field("Quantity (hours for labor)", required=True)
    unit_price = money_field("Price each (blank: the book rate)")
    description = optional_text("Description (blank: the service name)", 200)


class PartLineForm(FlaskForm):
    estimate_job_id = SelectField("Job", coerce=int)
    part_id = SelectField("Part", coerce=int)
    qty = qty_field("Quantity", required=True)
    unit_price = money_field("Price each (blank: the part's price)")


class CustomLineForm(FlaskForm):
    estimate_job_id = SelectField("Job", coerce=int)
    kind = SelectField("Kind", choices=[("fee", "Fee"), ("sublet", "Sublet (work sent out)"),
                                        ("discount", "Discount")])
    description = StringField("Description", filters=[blank_to_none],
                              validators=[DataRequired(), Length(max=200)])
    qty = qty_field("Quantity", required=True)
    unit_price = money_field("Amount each", required=True)
    unit_cost = cost_field("What it will cost us (sublets)")
    taxable = BooleanField("Taxable")


class TemplateForm(FlaskForm):
    estimate_job_id = SelectField("Job", coerce=int)
    template_id = SelectField("Job template", coerce=int)


class LineEditForm(FlaskForm):
    description = StringField("Description", filters=[blank_to_none],
                              validators=[DataRequired(), Length(max=200)])
    qty = qty_field("Quantity", required=True)
    unit_price = money_field("Price each", required=True)
    labor_mode = SelectField("Labor hours", choices=list(LABOR_MODES.items()))
    unit_cost = cost_field("What it will cost us (sublets)")
    taxable = BooleanField("Taxable")


class SignForm(FlaskForm):
    """Approve with a finger signature: through the link, or on our phone."""
    name = StringField("Your name", filters=[blank_to_none],
                       validators=[DataRequired("Type your name."), Length(max=120)])
    signature = HiddenField()
    seen = HiddenField()


class VerbalForm(FlaskForm):
    name = StringField("Who approved", filters=[blank_to_none],
                       validators=[DataRequired(), Length(max=120)])
    note = optional_text("How and when (call, text…)", 300)


class DeclineForm(FlaskForm):
    reason = optional_text("Reason (optional)", 300)


class ConvertForm(FlaskForm):
    kind = SelectField("Kind of work", choices=list(WO_KINDS.items()))
    techs = MultiCheckboxField("Techs", coerce=int)
