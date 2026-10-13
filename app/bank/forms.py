from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, SelectField, StringField
from wtforms.validators import DataRequired, InputRequired, Length, Optional, Regexp

from app.forms_common import MONEY_MAX, blank_to_none, decimal_field
from app.models import BANK_KINDS


def balance_field(label):
    """A balance can be below zero (a card that's owed money, an overdrawn account)."""
    return decimal_field(label, 2, -MONEY_MAX, MONEY_MAX, required=True)


class AccountForm(FlaskForm):
    name = StringField("Name (as you call it)", filters=[blank_to_none],
                       validators=[DataRequired(), Length(max=80)])
    kind = SelectField("Kind", choices=list(BANK_KINDS.items()))
    last4 = StringField("Last 4 digits (optional)", filters=[blank_to_none],
                        validators=[Optional(), Regexp(r"^[0-9]{4}$", message="Four digits.")])
    opening_on = DateField("Start reconciling from", validators=[InputRequired()])
    opening_balance = balance_field("Balance at the end of the day before that")
    active = BooleanField("In use", default=True)


class ReconcileForm(FlaskForm):
    statement_end = DateField("Statement ends", validators=[InputRequired()])
    ending_balance = balance_field("Ending balance on the statement")

