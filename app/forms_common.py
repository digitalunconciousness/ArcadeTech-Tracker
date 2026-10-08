"""Field helpers shared by every form."""

from decimal import Decimal

from wtforms import DecimalField, StringField
from wtforms.validators import (
    InputRequired,
    Length,
    NumberRange,
    Optional,
    StopValidation,
    ValidationError,
)

EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def optional_text(label, max_len, *extra):
    return StringField(label, filters=[blank_to_none], validators=[Optional(), Length(max=max_len), *extra])


def max_places(places):
    """Refuse more decimal places than the column keeps, rather than let Postgres round
    the value silently."""
    def check(form, field):
        if field.data is None:
            return
        if not field.data.is_finite():  # Decimal() accepts "NaN" and "Infinity"
            raise StopValidation("Not a number.")
        if field.data.as_tuple().exponent < -places:
            raise StopValidation(f"At most {places} decimal places.")
    return check


def decimal_field(label, places, minimum=None, maximum=None, required=False, min_exclusive=False):
    """A Decimal input, never binary floating point. The bounds keep it inside the
    column's NUMERIC precision, so a typo is a form error instead of a database error."""
    checks = [max_places(places)]
    if minimum is not None or maximum is not None:
        checks.append(NumberRange(min=minimum, max=maximum))
    if min_exclusive:
        def above(form, field):
            if field.data is not None and field.data <= minimum:
                raise ValidationError(f"Must be more than {minimum}.")
        checks.append(above)
    first = InputRequired() if required else Optional()
    return DecimalField(label, places=None, validators=[first, *checks])


MONEY_MAX = Decimal("9999999.99")
COST_MAX = Decimal("999999.9999")
QTY_MAX = Decimal("99999999.999")


def money_field(label, required=False):
    return decimal_field(label, 2, Decimal(0), MONEY_MAX, required)


def cost_field(label, required=False):
    return decimal_field(label, 4, Decimal(0), COST_MAX, required)


def qty_field(label, required=False, positive=True):
    if positive:
        return decimal_field(label, 3, Decimal(0), QTY_MAX, required, min_exclusive=True)
    return decimal_field(label, 3, -QTY_MAX, QTY_MAX, required)
