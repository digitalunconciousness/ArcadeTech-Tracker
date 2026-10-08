from flask_wtf import FlaskForm
from wtforms import BooleanField, DecimalField, PasswordField, SelectField, StringField
from wtforms.validators import (
    URL,
    DataRequired,
    EqualTo,
    Length,
    NumberRange,
    Optional,
    Regexp,
)

from app.auth.forms import new_password_fields
from app.forms_common import EMAIL_RE, blank_to_none, optional_text
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
    website = optional_text("Website", 200, URL(message="Not a URL."))
    doc_accent_color = StringField(
        "Document accent color", filters=[blank_to_none],
        validators=[DataRequired(), Regexp(r"^#[0-9a-fA-F]{6}$", message="Use #rrggbb.")],
    )


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
