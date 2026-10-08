from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import (
    BooleanField,
    DateField,
    DateTimeLocalField,
    IntegerField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    ValidationError,
)
from wtforms.widgets import CheckboxInput, ListWidget

from app.customers.forms import opt_int
from app.forms_common import (
    blank_to_none,
    cost_field,
    decimal_field,
    money_field,
    optional_text,
    qty_field,
)
from app.models import (
    JOB_STATUSES,
    LABOR_MODES,
    PRIORITIES,
    READING_PHASES,
    RECEIVED_VIA,
    WO_KINDS,
    WO_STATUSES,
)

# value="2026-10-08T14:30": the form the browser's datetime-local input sends and expects.
LOCAL_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"]
ACCESSORIES = ("Harness", "Power supply", "Controls / joystick", "ROMs / chips", "Monitor chassis",
               "Manual / schematics", "Box / anti-static bag", "Cables")
READING_UNITS = ("V", "mV", "A", "mA", "Ω", "kΩ", "MΩ", "Hz", "kHz", "MHz", "µF", "nF", "pF",
                 "°C", "%")


class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


def text_area(label, max_len, required=False):
    first = DataRequired() if required else Optional()
    return TextAreaField(label, filters=[blank_to_none], validators=[first, Length(max=max_len)])


class WorkOrderForm(FlaskForm):
    kind = SelectField("Kind", choices=list(WO_KINDS.items()))
    summary = StringField("Summary (what the visit is for)", filters=[blank_to_none],
                          validators=[DataRequired(), Length(max=200)])
    site_id = SelectField("Site", coerce=opt_int)
    priority = SelectField("Priority", choices=list(PRIORITIES.items()), default="normal")
    promised_date = DateField("Promised by", validators=[Optional()])
    not_to_exceed = money_field("Not to exceed (blank: no limit)")
    techs = MultiCheckboxField("Techs", coerce=int)
    notes = text_area("Notes", 4000)


class NewWorkOrderForm(WorkOrderForm):
    asset_id = SelectField("Machine or board", coerce=opt_int)
    complaint = text_area("Complaint (in the customer's words)", 4000, required=True)


class StatusForm(FlaskForm):
    status = SelectField("Status", choices=list(WO_STATUSES.items()))


class AddJobForm(FlaskForm):
    asset_id = SelectField("Machine or board", coerce=opt_int)
    complaint = text_area("Complaint", 4000, required=True)


class JobForm(FlaskForm):
    status = SelectField("Status", choices=list(JOB_STATUSES.items()))
    complaint = text_area("Complaint (what the customer says)", 4000, required=True)
    cause = text_area("Cause (what you found)", 4000)
    correction = text_area("Correction (what you did)", 4000)
    received_at = DateTimeLocalField("Received", format=LOCAL_FORMATS, validators=[Optional()])
    received_via = SelectField("Came in by", coerce=lambda v: v or None,
                               choices=[("", "—")] + list(RECEIVED_VIA.items()))
    condition = text_area("Condition on arrival", 2000)
    accessories = MultiCheckboxField("Came with", choices=[(a, a) for a in ACCESSORIES])
    accessories_note = optional_text("Anything else that came with it", 200)
    inbound_tracking = optional_text("Inbound tracking no.", 60)


class ServiceLineForm(FlaskForm):
    service_id = SelectField("Service", coerce=int)
    labor_mode = SelectField("Labor hours", choices=list(LABOR_MODES.items()), default="actual")
    qty = qty_field("Quantity (hours for flat labor)")
    unit_price = money_field("Price each (blank: the book rate)")
    description = optional_text("Description (blank: the service name)", 200)
    tech_user_id = SelectField("Tech", coerce=opt_int)
    billable = BooleanField("Billable", default=True)


class PartLineForm(FlaskForm):
    part_id = SelectField("Part", coerce=int)
    qty = qty_field("Quantity", required=True)
    unit_price = money_field("Price each (blank: the part's price)")
    billable = BooleanField("Billable", default=True)


class CustomerPartForm(FlaskForm):
    description = StringField("What they brought", filters=[blank_to_none],
                              validators=[DataRequired(), Length(max=200)])
    qty = qty_field("Quantity", required=True)


class CustomLineForm(FlaskForm):
    kind = SelectField("Kind", choices=[("fee", "Fee"), ("sublet", "Sublet (work sent out)"),
                                        ("discount", "Discount")])
    description = StringField("Description", filters=[blank_to_none],
                              validators=[DataRequired(), Length(max=200)])
    qty = qty_field("Quantity", required=True)
    unit_price = money_field("Amount each", required=True)
    unit_cost = cost_field("What it cost us (sublets)")
    taxable = BooleanField("Taxable")
    billable = BooleanField("Billable", default=True)


class LineEditForm(FlaskForm):
    description = StringField("Description", filters=[blank_to_none],
                              validators=[DataRequired(), Length(max=200)])
    qty = qty_field("Quantity")
    unit_price = money_field("Price each", required=True)
    unit_cost = cost_field("What it cost us (sublets)")
    tech_user_id = SelectField("Tech", coerce=opt_int)
    taxable = BooleanField("Taxable")
    billable = BooleanField("Billable")


class TimeForm(FlaskForm):
    started_at = DateTimeLocalField("Started", format=LOCAL_FORMATS, validators=[InputRequired()])
    minutes = IntegerField("Minutes", validators=[InputRequired(), NumberRange(min=1, max=1440)])
    billable = BooleanField("Billable", default=True)
    note = optional_text("Note", 200)


class ReserveForm(FlaskForm):
    part_id = SelectField("Part", coerce=int)
    qty = qty_field("Quantity", required=True)
    note = optional_text("Note", 200)


class ReadingForm(FlaskForm):
    test_point = StringField("Test point (+5 V at edge, R34…)", filters=[blank_to_none],
                             validators=[DataRequired(), Length(max=80)])
    phase = SelectField("When", choices=list(READING_PHASES.items()))
    value = decimal_field("Reading", 6, required=True, minimum=-(10 ** 9), maximum=10 ** 9)
    unit = StringField("Unit", filters=[blank_to_none], validators=[DataRequired(), Length(max=12)])
    spec_lo = decimal_field("Spec low", 6, minimum=-(10 ** 9), maximum=10 ** 9)
    spec_hi = decimal_field("Spec high", 6, minimum=-(10 ** 9), maximum=10 ** 9)
    note = optional_text("Note", 200)

    def validate_spec_hi(self, field):
        if field.data is not None and self.spec_lo.data is not None \
                and field.data < self.spec_lo.data:
            raise ValidationError("Below the low end of the spec.")


class PhotoForm(FlaskForm):
    photo = FileField("Photo", validators=[FileRequired("Pick a photo.")])
    caption = optional_text("Caption", 200)


class AppointmentForm(FlaskForm):
    starts_at = DateTimeLocalField("Starts", format=LOCAL_FORMATS, validators=[InputRequired()])
    minutes = IntegerField("How long (minutes)", default=60,
                           validators=[InputRequired(), NumberRange(min=5, max=1440)])
    site_id = SelectField("Where", coerce=opt_int)
    users = MultiCheckboxField("Who", coerce=int)
    note = optional_text("Note", 300)
