"""Settings → Accounts (Phase 9c): the names (and numbers) the general journal uses for
each kind of money, so the export lands in an accountant's chart of accounts as is. Only
changes are stored; clearing a name puts the built-in one back. Two keys with the same
name and number are one account in the trial balance."""

from flask import flash, redirect, render_template, request, url_for
from sqlalchemy import select

from app.auth.decorators import requires_role
from app.books import journal
from app.extensions import db
from app.models import AccountMap
from app.settings.routes import bp

NAME_MAX, NUMBER_MAX = 120, 20


def _rows():
    """[(group label, [(key, built-in name, what it's for, name, number)])]."""
    stored = {row.key: row for row in db.session.scalars(select(AccountMap))}
    groups = {g: [] for g, _ in journal.GROUPS}
    for key, (default, purpose) in journal.defaults().items():
        row = stored.get(key)
        groups[journal.group_of(key)].append(
            (key, default, purpose, row.name if row else default, row.number if row else ""))
    return [(label, groups[g]) for g, label in journal.GROUPS if groups[g]]


@bp.route("/accounts", methods=["GET", "POST"])
@requires_role("owner")
def accounts():
    if request.method == "POST":
        stored = {row.key: row for row in db.session.scalars(select(AccountMap))}
        changed, errors = 0, []
        for key, (default, _) in journal.defaults().items():
            if f"name-{key}" not in request.form:
                continue                                # not on the page: leave it be
            name = " ".join(request.form[f"name-{key}"].split()) or default
            number = request.form.get(f"number-{key}", "").strip() or None
            if len(name) > NAME_MAX or (number and len(number) > NUMBER_MAX):
                errors.append(f"{default}: names are up to {NAME_MAX} characters, numbers "
                              f"up to {NUMBER_MAX}.")
                continue
            row = stored.get(key)
            if name == default and number is None:
                if row is not None:
                    db.session.delete(row)
                    changed += 1
            elif row is None:
                db.session.add(AccountMap(key=key, name=name, number=number))
                changed += 1
            elif (row.name, row.number) != (name, number):
                row.name, row.number = name, number
                changed += 1
        if errors:
            db.session.rollback()
            for e in errors:
                flash(e, "error")
            return render_template("settings/accounts.html", groups=_rows()), 400
        db.session.commit()
        flash(f"{changed} account{'s' if changed != 1 else ''} changed." if changed
              else "Nothing changed.", "ok")
        return redirect(url_for("settings.accounts"))
    return render_template("settings/accounts.html", groups=_rows())
