from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField
from wtforms.validators import DataRequired, EqualTo, Length, Regexp

from app.models.user import PASSWORD_MAX, PASSWORD_MIN


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=64)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=PASSWORD_MAX)])


class TotpForm(FlaskForm):
    code = StringField(
        "6-digit code",
        validators=[DataRequired(), Regexp(r"^\s*\d{3}\s?\d{3}\s*$", message="Six digits.")],
    )


def new_password_fields():
    return (
        PasswordField(
            "New password",
            validators=[DataRequired(), Length(min=PASSWORD_MIN, max=PASSWORD_MAX)],
        ),
        PasswordField(
            "Repeat new password",
            validators=[DataRequired(), EqualTo("new_password", message="Doesn't match.")],
        ),
    )


class PasswordChangeForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    new_password, confirm_password = new_password_fields()


class EmptyForm(FlaskForm):
    """A POST with nothing but the CSRF token (logout, reset buttons)."""
