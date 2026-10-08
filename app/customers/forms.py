from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, Regexp

from app.forms_common import EMAIL_RE, blank_to_none, optional_text
from app.models.customer import COMM_KINDS, PAYMENT_TERMS


def opt_int(value):
    """SelectField coerce for an optional id: "", "None" and None mean no choice."""
    if value in (None, "", "None"):
        return None
    return int(value)


def email_field(label):
    return optional_text(label, 254, Regexp(EMAIL_RE, message="Not an email address."))


class AddressFields:
    address_line1 = optional_text("Address", 160)
    address_line2 = optional_text("Address line 2", 160)
    city = optional_text("City", 80)
    region = optional_text("State", 40)
    postal_code = optional_text("ZIP", 20)


class CustomerForm(AddressFields, FlaskForm):
    kind = SelectField("Kind", choices=[("business", "Business"), ("individual", "Individual")])
    name = StringField("Name", filters=[blank_to_none], validators=[DataRequired(), Length(max=160)])
    dba = optional_text("Doing business as", 160)
    phone = optional_text("Phone", 40)
    billing_email = email_field("Billing email")
    payment_terms = SelectField("Payment terms", choices=list(PAYMENT_TERMS.items()))
    tax_exempt = BooleanField("Tax exempt")
    exempt_cert_no = optional_text("Exemption certificate no.", 60)
    exempt_cert_expires = DateField("Certificate expires", validators=[Optional()])
    sends_1099 = BooleanField("Sends us a 1099-NEC")
    referral_source = optional_text("How they found us", 120)
    notes = TextAreaField("Notes", filters=[blank_to_none], validators=[Optional(), Length(max=4000)])
    active = BooleanField("Active", default=True)


class ContactForm(FlaskForm):
    name = StringField("Name", filters=[blank_to_none], validators=[DataRequired(), Length(max=120)])
    role = optional_text("Role (owner, manager…)", 60)
    phone = optional_text("Phone", 40)
    email = email_field("Email")
    is_billing = BooleanField("Billing contact")
    is_site = BooleanField("On-site contact")
    notes = TextAreaField("Notes", filters=[blank_to_none], validators=[Optional(), Length(max=2000)])
    active = BooleanField("Active", default=True)


class SiteForm(AddressFields, FlaskForm):
    name = StringField("Site name", filters=[blank_to_none],
                       validators=[DataRequired(), Length(max=120)])
    site_contact_id = SelectField("Site contact", coerce=opt_int, validate_choice=True)
    access_notes = TextAreaField(
        "Access: hours, who lets you in (never alarm codes)", filters=[blank_to_none],
        validators=[Optional(), Length(max=2000)])
    active = BooleanField("Active", default=True)


class CommLogForm(FlaskForm):
    kind = SelectField("Kind", choices=[(k, k.replace("_", " ")) for k in COMM_KINDS])
    summary = TextAreaField("What was said", filters=[blank_to_none],
                            validators=[DataRequired(), Length(max=4000)])
