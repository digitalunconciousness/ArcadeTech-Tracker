"""Desktop power tables for the list pages (app/lists/engine.py, app/lists/specs.py)."""


def desk_list(page):
    """For a list route: its desktop table's rows, or the CSV when ?format=csv."""
    from app.lists.engine import view
    from app.lists.specs import SPECS

    return view(SPECS[page])
