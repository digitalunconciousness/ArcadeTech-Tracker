from decimal import Decimal

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DecimalField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    EqualTo,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    Regexp,
    ValidationError,
)

from app.auth.forms import new_password_fields
from app.customers.forms import opt_int
from app.forms_common import (
    EMAIL_RE,
    WEB_URL,
    blank_to_none,
    cost_field,
    decimal_field,
    optional_text,
)
from app.models.user import PASSWORD_MAX, PASSWORD_MIN, ROLE_LABELS, USERNAME_RE


class BusinessForm(FlaskForm):
    business_name = StringField(
        "Business name (printed on documents)", filters=[blank_to_none],
        validators=[DataRequired(), Length(max=120)],
    )
    legal_name = optional_text("Legal name", 160)
    address_line1 = optional_text("Address", 160)
    address_line2 = optional_text("Address line 2", 160)
    city = optional_text("City", 80)
    region = optional_text("State", 40)
    postal_code = optional_text("ZIP", 20)
    phone = optional_text("Phone", 40)
    email = optional_text("Email", 254, Regexp(EMAIL_RE, message="Not an email address."))
    website = optional_text("Website", 200, *WEB_URL)
    doc_accent_color = StringField(
        "Document accent color", filters=[blank_to_none],
        validators=[DataRequired(), Regexp(r"^#[0-9a-fA-F]{6}$", message="Use #rrggbb.")],
    )
    claim_terms = TextAreaField(
        "Claim ticket terms (storage fees, abandonment, what we're not responsible for)",
        filters=[blank_to_none], validators=[Optional(), Length(max=6000)])
    warranty_terms = TextAreaField(
        "Warranty terms (printed on service reports)", filters=[blank_to_none],
        validators=[Optional(), Length(max=6000)])
    estimate_terms = TextAreaField(
        "Estimate terms (authorization, the NTE rule, payment; estimates can't be sent "
        "without them)", filters=[blank_to_none], validators=[Optional(), Length(max=6000)])
    estimate_link_days = IntegerField("Estimates are good for (days)",
                                      validators=[InputRequired(), NumberRange(min=1, max=365)])
    payment_instructions = TextAreaField(
        "How customers pay you (Zelle, Venmo, checks payable to…; printed on invoices)",
        filters=[blank_to_none], validators=[Optional(), Length(max=2000)])
    dispatch_block_overlap = BooleanField(
        "Refuse to book a tech twice at the same time (otherwise it's flagged in red)")


ROLE_CHOICES = [(role, label) for role, label in ROLE_LABELS.items()]


class UserEditForm(FlaskForm):
    display_name = StringField(
        "Display name", filters=[blank_to_none], validators=[DataRequired(), Length(max=120)]
    )
    email = optional_text("Email", 254, Regexp(EMAIL_RE, message="Not an email address."))
    role = SelectField("Role", choices=ROLE_CHOICES)
    active = BooleanField("Active")
    is_partner = BooleanField("Partner")
    split_pct = DecimalField(
        "Profit split %", places=2, validators=[Optional(), NumberRange(min=0, max=100)]
    )


class UserCreateForm(UserEditForm):
    username = StringField(
        "Username", filters=[lambda v: v.strip().lower() if isinstance(v, str) else v],
        validators=[DataRequired(), Regexp(USERNAME_RE, message=(
            "2 to 64 characters: lowercase letters, digits, dot, dash, underscore."))],
    )
    new_password = PasswordField(
        "Password", validators=[DataRequired(), Length(min=PASSWORD_MIN, max=PASSWORD_MAX)]
    )
    confirm_password = PasswordField(
        "Repeat password",
        validators=[DataRequired(), EqualTo("new_password", message="Doesn't match.")],
    )


class SetPasswordForm(FlaskForm):
    new_password, confirm_password = new_password_fields()
    # The acting owner's own password: a session left open (or stolen) can't take over an
    # account, theirs or anyone's, without it.
    your_password = PasswordField("Your own password", validators=[DataRequired()])


class ResetTwoFactorForm(FlaskForm):
    your_password = PasswordField("Your own password", validators=[DataRequired()])


class TwoFactorPolicyForm(FlaskForm):
    require_2fa_all = BooleanField("Require 2FA for everyone: techs and viewers enroll at "
                                   "their next page, as owners do")
    your_password = PasswordField("Your own password", validators=[DataRequired()])


class LaborPolicyForm(FlaskForm):
    labor_increment_hours = decimal_field("Bill labor in steps of (hours)", 2, Decimal("0.01"),
                                          Decimal(8), required=True)
    labor_minimum_hours = decimal_field("Minimum per job (hours)", 2, Decimal(0), Decimal(24),
                                        required=True)


class MarkupTierForm(FlaskForm):
    min_cost = cost_field("Unit cost from", required=True)
    max_cost = cost_field("up to (blank: no limit)")
    multiplier = decimal_field("Multiplier (×)", 3, Decimal("1"), Decimal("100"), required=True)

    def validate_max_cost(self, field):
        if field.data is not None and self.min_cost.data is not None \
                and field.data <= self.min_cost.data:
            raise ValidationError("Must be more than the start of the bracket.")


class DeviceForm(FlaskForm):
    name = StringField("Name (the Pi it goes on)", filters=[blank_to_none],
                       validators=[DataRequired(), Length(max=60)])
    notes = TextAreaField("Notes", filters=[blank_to_none], validators=[Optional(), Length(max=2000)])


class DeviceAccessForm(FlaskForm):
    """Who a device acts as, and whether it may reach jobs. Choices: no one, or an active
    user (the route fills them)."""
    acts_as_user_id = SelectField("Acts as", coerce=opt_int)
    jobs = BooleanField("Jobs: reads each machine's open jobs, sends bench orders and "
                        "session tags")
