"""The stock ledger. On hand is the sum of moves; no column stores a quantity.

Everything here only INSERTs: the app role can't UPDATE or DELETE stock_lot or
stock_move, and the stock_move_lot_guard trigger refuses any move that would take a
lot below zero (a concurrent move on the same lot waits for it, then fails cleanly).
Callers commit; on IntegrityError they roll back and tell the user stock changed."""

from decimal import Decimal

from sqlalchemy import func, select

from app.extensions import db
from app.formatting import qty as fmt_qty
from app.models import StockLot, StockMove

ZERO = Decimal("0")


class StockError(Exception):
    """Something the user can fix; the message is shown as is."""


def on_hand(part_id):
    return db.session.scalar(
        select(func.coalesce(func.sum(StockMove.qty), 0)).where(StockMove.part_id == part_id))


def on_hand_map(part_ids=None):
    q = select(StockMove.part_id, func.sum(StockMove.qty)).group_by(StockMove.part_id)
    if part_ids is not None:
        q = q.where(StockMove.part_id.in_(list(part_ids)))
    return {pid: total for pid, total in db.session.execute(q)}


def lots(part_id, open_only=False):
    """[(lot, remaining)], oldest first: the order FIFO takes them in."""
    remaining = func.coalesce(func.sum(StockMove.qty), 0)
    q = (select(StockLot, remaining).outerjoin(StockMove, StockMove.lot_id == StockLot.id)
         .where(StockLot.part_id == part_id).group_by(StockLot.id)
         .order_by(StockLot.received_at, StockLot.id))
    if open_only:
        q = q.having(remaining > 0)
    return [(lot, rem) for lot, rem in db.session.execute(q)]


def _check(part, qty):
    if not part.track_stock:
        raise StockError(f"{part.sku} isn't stocked (a consumable); it has no ledger.")
    if qty <= 0:
        raise StockError("Quantity must be more than zero.")


def _move(part, lot, qty, reason, note, wo_job_id=None):
    move = StockMove(part_id=part.id, lot_id=lot.id, qty=qty, reason=reason, note=note,
                     wo_job_id=wo_job_id)
    db.session.add(move)
    return move


def receive(part, qty, unit_cost, *, vendor_id=None, date_code=None, vendor_lot=None,
            note=None, reason="receive"):
    """A new lot at one cost, and the move that puts qty into it."""
    _check(part, qty)
    if unit_cost is None or unit_cost < 0:
        raise StockError("A received lot needs its unit cost.")
    lot = StockLot(part_id=part.id, unit_cost=unit_cost, vendor_id=vendor_id,
                   date_code=date_code, vendor_lot=vendor_lot, note=note)
    db.session.add(lot)
    db.session.flush()
    _move(part, lot, qty, reason, note)
    db.session.flush()
    return lot


def take(part, qty, reason, note=None, lot_id=None, wo_job_id=None):
    """Remove qty: from one lot when lot_id is given, else oldest lot first (FIFO).
    Returns the moves (one per lot touched)."""
    _check(part, qty)
    open_lots = lots(part.id, open_only=True)
    if lot_id is not None:
        open_lots = [(lot, rem) for lot, rem in open_lots if lot.id == lot_id]
        if not open_lots:
            raise StockError("That lot is empty or isn't this part's.")
    available = sum((rem for _, rem in open_lots), ZERO)
    if available < qty:
        where = "in that lot" if lot_id is not None else "on hand"
        raise StockError(f"Only {fmt_qty(available)} {where}.")
    moves, need = [], qty
    for lot, rem in open_lots:
        if need <= 0:
            break
        step = min(rem, need)
        moves.append(_move(part, lot, -step, reason, note, wo_job_id))
        need -= step
    db.session.flush()
    return moves


def put(part, qty, reason, note=None, lot_id=None, wo_job_id=None):
    """Add qty that turned up (a count or an adjustment) to lot_id, or else the newest
    lot, at that lot's cost. With no lot yet, open one at the part's default cost."""
    _check(part, qty)
    q = select(StockLot).where(StockLot.part_id == part.id)
    if lot_id is not None:
        newest = db.session.scalar(q.where(StockLot.id == lot_id))
        if newest is None:
            raise StockError("That lot isn't this part's.")
    else:
        newest = db.session.scalar(q.order_by(StockLot.received_at.desc(), StockLot.id.desc())
                                   .limit(1))
    if newest is None:
        if part.default_cost is None:
            raise StockError(f"{part.sku} has no lots and no default cost: receive it, "
                             "with a cost, instead.")
        lot = receive(part, qty, part.default_cost, note=note, reason=reason)
        return db.session.scalar(select(StockMove).where(StockMove.lot_id == lot.id))
    move = _move(part, newest, qty, reason, note, wo_job_id)
    db.session.flush()
    return move


def adjust(part, delta, reason, note=None, lot_id=None):
    """A signed change: down takes (FIFO, or from lot_id), up puts."""
    if delta < 0:
        return take(part, -delta, reason, note, lot_id)
    return [put(part, delta, reason, note, lot_id)]


def count(part, counted, note=None):
    """Set on hand to what was counted, with `count` moves for the difference. Returns
    the difference (zero: nothing written)."""
    if counted < 0:
        raise StockError("A count can't be negative.")
    diff = counted - on_hand(part.id)
    if diff:
        adjust(part, diff, "count", note)
    return diff


def fifo_cost(part_id, qty=Decimal(1)):
    """What the next qty units would cost, taken oldest lot first; None if fewer are on
    hand. Moves nothing."""
    total, need = ZERO, Decimal(qty)
    for lot, rem in lots(part_id, open_only=True):
        step = min(rem, need)
        total += step * lot.unit_cost
        need -= step
        if need <= 0:
            return total
    return None
