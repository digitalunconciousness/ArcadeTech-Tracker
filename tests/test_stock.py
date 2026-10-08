"""The stock ledger: on hand is the sum of moves, lots never go negative (the database
says so, even under concurrency), the ledger is append-only, FIFO takes the oldest lot."""

import random
import threading
from decimal import Decimal as D

import psycopg
import pytest
from sqlalchemy import select, text

from app.actor import set_actor
from app.extensions import db
from app.models import AuditLog, Part, StockLot, StockMove
from app.parts import stock
from conftest import libpq


def new_part(app, sku="CAP-470-25", track=True, default_cost=None):
    with app.app_context():
        part = Part(sku=sku, name=f"Part {sku}", track_stock=track, default_cost=default_cost,
                    taxable=True, price_mode="fixed", unit="each", active=True)
        db.session.add(part)
        db.session.commit()
        return part.id


def app_conn(tdb):
    """A raw connection as the app role, outside the ORM."""
    return psycopg.connect(libpq(tdb.url("app")))


def ledger_sum(app, part_id):
    with app.app_context():
        return db.session.scalar(text(
            "SELECT COALESCE(sum(qty), 0) FROM stock_move WHERE part_id = :p"), {"p": part_id})


def test_receive_take_fifo(app):
    pid = new_part(app)
    with app.app_context():
        part = db.session.get(Part, pid)
        old = stock.receive(part, D(10), D("1.0000"), date_code="2219")
        new = stock.receive(part, D(10), D("2.5000"), date_code="2301")
        db.session.commit()
        assert stock.on_hand(pid) == D(20)
        assert stock.fifo_cost(pid) == D("1.0000")
        assert stock.fifo_cost(pid, D(12)) == D("15.0000")      # 10 × 1 + 2 × 2.5
        assert stock.fifo_cost(pid, D(21)) is None

        moves = stock.take(part, D(15), "issue", "job test")
        db.session.commit()
        assert [(m.lot_id, m.qty) for m in moves] == [(old.id, D(-10)), (new.id, D(-5))]
        assert [(lot.id, rem) for lot, rem in stock.lots(pid, open_only=True)] == [(new.id, D(5))]
        assert stock.fifo_cost(pid) == D("2.5000")
        assert stock.on_hand(pid) == D(5)

        with pytest.raises(stock.StockError, match="Only 5 on hand"):
            stock.take(part, D(6), "issue")
        with pytest.raises(stock.StockError, match="empty"):
            stock.take(part, D(1), "scrap", lot_id=old.id)
        db.session.rollback()


def test_put_goes_to_newest_lot_or_opens_one_at_default_cost(app):
    pid = new_part(app, default_cost=D("0.0120"))
    with app.app_context():
        part = db.session.get(Part, pid)
        move = stock.put(part, D(100), "count", "opening count")
        db.session.commit()
        lot = db.session.get(StockLot, move.lot_id)
        assert lot.unit_cost == D("0.0120") and move.qty == D(100) and move.reason == "count"
        later = stock.receive(part, D(5), D("0.0100"))
        stock.put(part, D(3), "adjust", "found in drawer")
        db.session.commit()
        assert dict((lot.id, rem) for lot, rem in stock.lots(pid))[later.id] == D(8)

        bare = db.session.get(Part, new_part(app, sku="NO-COST"))
        with pytest.raises(stock.StockError, match="no lots and no default cost"):
            stock.put(bare, D(1), "count")
        db.session.rollback()


def test_untracked_parts_have_no_ledger(app):
    pid = new_part(app, sku="FLUX-PEN", track=False)
    with app.app_context():
        part = db.session.get(Part, pid)
        with pytest.raises(stock.StockError, match="isn't stocked"):
            stock.receive(part, D(1), D(5))
        db.session.rollback()


def test_count_writes_the_difference(app):
    pid = new_part(app)
    with app.app_context():
        part = db.session.get(Part, pid)
        stock.receive(part, D(10), D(1))
        stock.receive(part, D(10), D(2))
        assert stock.count(part, D(20)) == 0
        assert stock.count(part, D("12.5")) == D("-7.5")
        assert stock.count(part, D(14)) == D("1.5")
        db.session.commit()
        reasons = db.session.scalars(select(StockMove.reason).where(StockMove.part_id == pid)
                                     .order_by(StockMove.id)).all()
        assert reasons == ["receive", "receive", "count", "count"]
        assert stock.on_hand(pid) == D(14)
        with pytest.raises(stock.StockError):
            stock.count(part, D(-1))


def test_on_hand_is_the_sum_of_moves_randomized(app):
    """The Phase 2 exit check: hundreds of mixed moves, and on hand always equals both
    the sum of the ledger and a shadow tally; no lot ever goes below zero."""
    rng = random.Random(20261008)
    pids = [new_part(app, sku=f"RND-{i}", default_cost=D("0.5")) for i in range(3)]
    expected = {pid: D(0) for pid in pids}
    with app.app_context():
        for _ in range(300):
            pid = rng.choice(pids)
            part = db.session.get(Part, pid)
            op = rng.choice(["receive", "issue", "adjust", "scrap", "count", "return"])
            amount = D(rng.randint(1, 4000)) / 1000 * rng.choice([1, 1, 10])
            try:
                if op == "receive":
                    stock.receive(part, amount, D(rng.randint(1, 99999)) / 10000)
                    expected[pid] += amount
                elif op == "return":
                    stock.put(part, amount, "return")
                    expected[pid] += amount
                elif op in ("issue", "scrap"):
                    stock.take(part, amount, op)
                    expected[pid] -= amount
                elif op == "adjust":
                    delta = amount * rng.choice([1, -1])
                    stock.adjust(part, delta, "adjust", "random")
                    expected[pid] += delta
                else:
                    stock.count(part, amount)
                    expected[pid] = amount
                db.session.commit()
            except stock.StockError:
                db.session.rollback()   # asked for more than was there; nothing written
            assert stock.on_hand(pid) == expected[pid]
        for pid in pids:
            assert ledger_sum(app, pid) == expected[pid] == stock.on_hand_map([pid]).get(pid, 0)
            lots = stock.lots(pid)
            assert all(rem >= 0 for _, rem in lots)
            assert sum((rem for _, rem in lots), D(0)) == expected[pid]
        assert db.session.scalar(select(StockMove.id).limit(1)) is not None


def test_database_refuses_a_lot_going_negative(app, tdb):
    pid = new_part(app)
    with app.app_context():
        lot = stock.receive(db.session.get(Part, pid), D(5), D(1))
        db.session.commit()
        lot_id = lot.id
    with app_conn(tdb) as c:
        c.execute("INSERT INTO stock_move (part_id, lot_id, qty, reason) VALUES (%s, %s, -5, 'issue')",
                  (pid, lot_id))
        c.commit()
        with pytest.raises(psycopg.errors.CheckViolation, match="below zero"):
            c.execute("INSERT INTO stock_move (part_id, lot_id, qty, reason) "
                      "VALUES (%s, %s, -0.001, 'issue')", (pid, lot_id))
        c.rollback()
        # several rows in one statement are checked row by row, too
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO stock_move (part_id, lot_id, qty, reason) VALUES "
                      "(%s, %s, 3, 'return'), (%s, %s, -2, 'issue'), (%s, %s, -2, 'issue')",
                      (pid, lot_id) * 3)
        c.rollback()


def test_concurrent_takes_cannot_both_drain_a_lot(app, tdb):
    """Two transactions each take 3 of 5. The second waits for the first's lock, then
    sees its move and is refused."""
    pid = new_part(app)
    with app.app_context():
        lot_id = stock.receive(db.session.get(Part, pid), D(5), D(1)).id
        db.session.commit()
    insert = "INSERT INTO stock_move (part_id, lot_id, qty, reason) VALUES (%s, %s, -3, 'issue')"
    outcome = {}
    with app_conn(tdb) as first, app_conn(tdb) as second:
        first.execute(insert, (pid, lot_id))           # holds the lot's lock, uncommitted

        def other():
            try:
                second.execute(insert, (pid, lot_id))
                second.commit()
                outcome["second"] = "committed"
            except psycopg.errors.CheckViolation:
                second.rollback()
                outcome["second"] = "refused"

        t = threading.Thread(target=other)
        t.start()
        t.join(timeout=1.5)
        assert t.is_alive(), "the second take should be waiting on the first's lock"
        first.commit()
        t.join(timeout=10)
    assert outcome == {"second": "refused"}
    assert ledger_sum(app, pid) == D(2)


def test_ledger_is_append_only_for_the_app(app, tdb):
    pid = new_part(app)
    with app.app_context():
        lot_id = stock.receive(db.session.get(Part, pid), D(5), D(1)).id
        db.session.commit()
    with app_conn(tdb) as c:
        for sql in ("UPDATE stock_move SET qty = 50", "DELETE FROM stock_move",
                    "UPDATE stock_lot SET unit_cost = 0", "DELETE FROM stock_lot",
                    "TRUNCATE stock_move"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)
            c.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM stock_lot WHERE id = %s FOR UPDATE", (lot_id,))
        c.rollback()


def test_a_move_must_use_its_own_parts_lot(app, tdb):
    a, b = new_part(app, sku="A-1"), new_part(app, sku="B-1")
    with app.app_context():
        lot_a = stock.receive(db.session.get(Part, a), D(5), D(1)).id
        db.session.commit()
    with app_conn(tdb) as c:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            c.execute("INSERT INTO stock_move (part_id, lot_id, qty, reason) "
                      "VALUES (%s, %s, 1, 'adjust')", (b, lot_a))
        c.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO stock_move (part_id, lot_id, qty, reason) "
                      "VALUES (%s, %s, 0, 'adjust')", (a, lot_a))
        c.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO stock_move (part_id, lot_id, qty, reason) "
                      "VALUES (%s, %s, 1, 'borrowed')", (a, lot_a))
        c.rollback()


def test_count_moves_are_audited_with_the_actor(app, make_user):
    owner = make_user("olive", "owner")
    pid = new_part(app)
    with app.app_context():
        set_actor(owner["id"])
        part = db.session.get(Part, pid)
        stock.receive(part, D(10), D(1))
        stock.count(part, D(7), "shelf count")
        db.session.commit()
        move = db.session.scalar(select(StockMove).where(StockMove.reason == "count"))
        row = db.session.scalar(select(AuditLog).where(AuditLog.table_name == "stock_move",
                                                       AuditLog.row_id == str(move.id)))
    assert move.qty == D(-3) and move.created_by == owner["id"]
    assert row.action == "I" and row.user_id == owner["id"]
    assert row.after["reason"] == "count" and D(str(row.after["qty"])) == D(-3)
