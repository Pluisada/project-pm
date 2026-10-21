"""AI moves and undo of a deletion keep each column numbered 0..n-1 (no ties, no gaps), like the REST move."""
import pytest

from ai_kanban import apply_board_actions
from models import Board, Card, Column
from test_ai_kanban import _admin, _columns, db  # noqa: F401  (fixture/helpers)
from undo import apply_undo


def seed(db, layout):
    """layout: {column index: [titles]} -> cards numbered 0..n-1; returns {title: card}."""
    board = db.query(Board).first()
    columns = _columns(db, board.id)
    cards = {}
    for index, titles in layout.items():
        for pos, title in enumerate(titles):
            cards[title] = Card(column_id=columns[index].id, title=title, details="", position=pos)
            db.add(cards[title])
    db.commit()
    return board, columns, cards


def order(db, column):
    db.expire_all()
    return [(c.title, c.position) for c in db.query(Card).filter(Card.column_id == column.id).order_by(Card.position, Card.id)]


def dense(db, column, titles):
    assert order(db, column) == list(enumerate_titles(titles))


def enumerate_titles(titles):
    return [(t, i) for i, t in enumerate(titles)]


@pytest.mark.asyncio
async def test_ai_reorder_within_a_column_is_exact_and_dense(db):
    board, cols, cards = seed(db, {0: ["a", "b", "c", "d"]})
    result = await apply_board_actions(
        db, board.id, [{"type": "move", "card_id": cards["d"].id, "column_id": cols[0].id, "position": 1}], _admin(db))
    assert result["failed"] == []
    dense(db, cols[0], ["a", "d", "b", "c"])


@pytest.mark.asyncio
async def test_ai_cross_column_move_renumbers_both_columns_and_undo_restores(db):
    board, cols, cards = seed(db, {0: ["a", "b", "c"], 1: ["x", "y"]})
    admin = _admin(db)
    result = await apply_board_actions(
        db, board.id, [{"type": "move", "card_id": cards["b"].id, "column_id": cols[1].id, "position": 1}], admin)
    assert result["failed"] == []
    dense(db, cols[0], ["a", "c"])
    dense(db, cols[1], ["x", "b", "y"])

    apply_undo(db, admin, board.id)
    db.commit()
    dense(db, cols[0], ["a", "b", "c"])
    dense(db, cols[1], ["x", "y"])


@pytest.mark.asyncio
async def test_ai_move_with_a_non_integer_position_is_refused_untouched(db):
    board, cols, cards = seed(db, {0: ["a", "b"], 1: ["x"]})
    result = await apply_board_actions(
        db, board.id, [{"type": "move", "card_id": cards["a"].id, "column_id": cols[1].id, "position": "first"}], _admin(db))
    assert result["successful"] == [] and result["failed"][0]["reason"] == "Invalid position"
    dense(db, cols[0], ["a", "b"])
    dense(db, cols[1], ["x"])


@pytest.mark.asyncio
async def test_undo_of_a_deletion_reinserts_in_order_and_leaves_no_ties(db):
    board, cols, cards = seed(db, {0: ["a", "b", "c", "d"]})
    cards["d"].position = 1  # a tie left by older writers: b and d share position 1
    db.commit()
    admin = _admin(db)
    await apply_board_actions(db, board.id, [{"type": "delete", "card_id": cards["b"].id}], admin)
    apply_undo(db, admin, board.id)
    db.commit()
    dense(db, cols[0], ["a", "b", "d", "c"])  # b back at index 1; the others keep their relative order
