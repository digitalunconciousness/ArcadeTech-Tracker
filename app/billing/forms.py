from decimal import Decimal

from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, InputRequired, Length, Optional, ValidationError

from app.customers.forms import opt_int
from app.forms_common import blank_to_none, decimal_field, optional_text
from app.work.forms import MultiCheckboxField


def text_area(label, max_len):
    return TextAreaField(label, filters=[blank_to_none], validators=[Optional(),
                                                                     Length(max=max_len)])


class IssueForm(FlaskForm):
    # Checked by the service, which says what happened ("billed meanwhile") in words.
    lines = MultiCheckboxField("Lines to bill", coerce=int, validate_choice=False)
    issue_date = DateField("Issue date", validators=[InputRequired()])
    backdate_reason = optional_text("Why it's dated earlier (needed for an earlier date)", 300)
    jurisdiction_id = SelectField("Tax jurisdiction", coerce=opt_int)
    customer_note = text_area("Note to the customer (printed)", 2000)
    internal_note = text_area("Internal note (not printed)", 2000)


class VoidForm(FlaskForm):
    reason = StringField("Why it's void", filters=[blank_to_none],
                         validators=[DataRequired("Say why."), Length(max=300)])


def rate_field(label):
    """A rate as a fraction, six places (NUMERIC(7,6)); blank is 0."""
    return decimal_field(label, 6, Decimal(0), Decimal("0.249999"))


class JurisdictionForm(FlaskForm):
    name = StringField("Name (city / county)", filters=[blank_to_none],
                       validators=[DataRequired(), Length(max=120)])
    state_rate = rate_field("State rate (0.045 = 4.5%)")
    county_rate = rate_field("County rate")
    city_rate = rate_field("City rate")
    other_rate = rate_field("Other rate")
    effective_from = DateField("In effect from", validators=[InputRequired()])
    effective_to = DateField("Until (blank: still in effect)", validators=[Optional()])
    source_note = optional_text("Source (where the rate came from)", 300)

    def validate_effective_to(self, field):
        if field.data and self.effective_from.data and field.data < self.effective_from.data:
            raise ValidationError("Must be on or after the start date.")

    def validate(self, extra_validators=None):
        """Blank rates are 0; together they must stay under 25% (a CHECK says so too)."""
        if not super().validate(extra_validators):
            return False
        rates = (self.state_rate, self.county_rate, self.city_rate, self.other_rate)
        if sum((f.data or Decimal(0)) for f in rates) >= Decimal("0.25"):
            self.state_rate.errors.append("The rates add up to 25% or more: check them.")
            return False
        return True


class TaxSettingsForm(FlaskForm):
    collect_sales_tax = BooleanField("Collect sales tax on invoices (needs an Oklahoma sales "
                                     "tax permit)")
    default_tax_jurisdiction_id = SelectField("The shop's own location (counter sales, and "
                                              "the default)", coerce=opt_int)
