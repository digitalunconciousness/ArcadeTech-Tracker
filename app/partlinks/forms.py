from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, HiddenField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from app.customers.forms import opt_int
from app.forms_common import blank_to_none, money_field, optional_text, qty_field
from app.partlinks.service import URL_MAX


class LinkForm(FlaskForm):
    url = StringField("Web address", filters=[blank_to_none],
                      validators=[DataRequired(), Length(max=URL_MAX + 200)])
    title = StringField("Name it", filters=[blank_to_none],
                        validators=[DataRequired(), Length(max=160)])
    fits = optional_text("Fits (chassis, game, board)", 200)
    price_seen = money_field("Price seen")
    price_seen_on = DateField("Seen on", validators=[Optional()])
    notes = TextAreaField("Notes", filters=[blank_to_none], validators=[Optional(), Length(max=2000)])
    part_id = SelectField("Buy our stock part here", coerce=opt_int, validate_choice=False)
    active = BooleanField("Active")


class AddLinkForm(FlaskForm):
    """Add a saved link to a job (an estimate's "Parts you'll buy", or a WO's parts the
    customer is buying)."""

    part_link_id = HiddenField(validators=[DataRequired()])
    qty = qty_field("Qty", required=True)
    wait = BooleanField("Put the work order on Waiting on parts", default=True)


class SendLinksForm(FlaskForm):
    how = SelectField("Sent by", choices=[("text", "Text"), ("email", "Email")])
    message = TextAreaField("Message", filters=[blank_to_none],
                            validators=[DataRequired(), Length(max=4000)])
