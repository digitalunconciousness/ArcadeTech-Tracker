from flask_wtf import FlaskForm
from wtforms import IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, Regexp

from app.customers.forms import opt_int
from app.forms_common import blank_to_none, optional_text
from app.models.asset import ASSET_KINDS, ASSET_STATUSES

KIND_CHOICES = [(k, k) for k in ASSET_KINDS]
STATUS_CHOICES = list(ASSET_STATUSES.items())


class AssetForm(FlaskForm):
    name = StringField("Name (Ms. Pac-Man, K4600 chassis…)", filters=[blank_to_none],
                       validators=[DataRequired(), Length(max=120)])
    kind = SelectField("Kind", choices=KIND_CHOICES)
    site_id = SelectField("Site", coerce=opt_int)
    parent_asset_id = SelectField("Inside (board in a machine)", coerce=opt_int)
    manufacturer = optional_text("Manufacturer", 80)
    model = optional_text("Model", 80)
    model_key = optional_text(
        "Manuals library key (GATBOX)", 60,
        Regexp(r"^[a-z0-9]+(-[a-z0-9]+)*$", message="Lowercase letters, digits and dashes."))
    year = IntegerField("Year", validators=[Optional(), NumberRange(1950, 2100)])
    serial = optional_text("Serial", 80)
    shop_location = optional_text("Shelf or bin (in the shop)", 60)
    notes = TextAreaField("Notes", filters=[blank_to_none], validators=[Optional(), Length(max=4000)])


class NewAssetForm(AssetForm):
    status = SelectField("Status", choices=STATUS_CHOICES, default="in_service")


class StatusForm(FlaskForm):
    status = SelectField("New status", choices=STATUS_CHOICES)
    note = optional_text("Note", 500)


class TransferForm(FlaskForm):
    customer_id = SelectField("Owner", coerce=opt_int, validators=[DataRequired()])
    site_id = SelectField("Site", coerce=opt_int)
    note = optional_text("Note (sold, traded, moved to …)", 500)


class NoteForm(FlaskForm):
    note = TextAreaField("Note", filters=[blank_to_none], validators=[DataRequired(), Length(max=2000)])
