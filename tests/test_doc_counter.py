import threading
import time
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.extensions import db
from app.numbering import issue_number
from app.timeutil import local_today


def test_sequential_per_kind_and_year(app):
    with app.app_context():
        d = date(2026, 5, 1)
        assert issue_number("EST", d) == "EST-2026-0001"
        assert issue_number("EST", d) == "EST-2026-0002"
        assert issue_number("INV", d) == "INV-2026-0001"
        assert issue_number("EST", date(2027, 1, 1)) == "EST-2027-0001"
        db.session.commit()


def test_rollback_leaves_no_gap(app):
    with app.app_context():
        d = date(2026, 5, 1)
        assert issue_number("INV", d) == "INV-2026-0001"
        db.session.commit()
        assert issue_number("INV", d) == "INV-2026-0002"
        db.session.rollback()  # the issue failed: the number goes back
        assert issue_number("INV", d) == "INV-2026-0002"
        db.session.commit()


def test_unknown_kind_refused(app):
    with app.app_context():
        with pytest.raises(ValueError):
            issue_number("XYZ", date(2026, 1, 1))


def test_database_refuses_unknown_kind_too(app):
    from sqlalchemy import text

    with app.app_context():
        with pytest.raises(IntegrityError):
            db.session.execute(text("INSERT INTO doc_counter (kind, year) VALUES ('XYZ', 2026)"))
        db.session.rollback()


def test_concurrent_issuers_serialize(app):
    """The second issuer waits on the first's row lock and gets the next number."""
    results = {}
    first_has_number = threading.Event()

    def first():
        with app.app_context(), Session(db.engine) as s:
            results["first"] = issue_number("WO", date(2026, 3, 3), session=s)
            first_has_number.set()
            time.sleep(0.5)
            s.commit()
            results["first_done"] = time.monotonic()

    def second():
        first_has_number.wait(5)
        with app.app_context(), Session(db.engine) as s:
            results["second"] = issue_number("WO", date(2026, 3, 3), session=s)
            results["second_got"] = time.monotonic()
            s.commit()

    with app.app_context():
        issue_number("WO", date(2026, 3, 3))  # create the row up front
        db.session.commit()
    t1, t2 = threading.Thread(target=first), threading.Thread(target=second)
    t1.start(), t2.start()
    t1.join(10), t2.join(10)
    assert (results["first"], results["second"]) == ("WO-2026-0002", "WO-2026-0003")
    assert results["second_got"] >= results["first_done"]


def test_year_is_the_shops_local_year(app):
    # 2026-12-31 11:00 UTC is already 2027-01-01 in the test zone (UTC+14).
    with app.app_context():
        assert local_today(datetime(2026, 12, 31, 11, 0, tzinfo=UTC)) == date(2027, 1, 1)
        assert issue_number("EST", local_today(datetime(2026, 12, 31, 11, 0, tzinfo=UTC))) \
            == "EST-2027-0001"
        db.session.commit()
