"""Field helpers shared by every form."""

from wtforms import StringField
from wtforms.validators import Length, Optional

EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def optional_text(label, max_len, *extra):
    return StringField(label, filters=[blank_to_none], validators=[Optional(), Length(max=max_len), *extra])
