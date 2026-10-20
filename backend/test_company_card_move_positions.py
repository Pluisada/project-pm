"""Central card move: both columns stay numbered 0..n-1, and undo restores the same dense layout.

Disposable tmp_path databases only.
"""
import pytest

from test_company_card_writes import NOW, admin, member, rig  # noqa: F401  (fixtures/helpers)

BUSINESS = "title, details, salesperson_id, deal_value"


def layout(rig):
    return {col: [r[0] for r in rig.sql("SELECT id FROM cards WHERE column_id = ? ORDER BY position, id", (col,))]
            for col in (1, 2)}


def positions(rig, col):
    return [r[0] for r in rig.sql("SELECT position FROM cards WHERE column_id = ? ORDER BY position", (col,))]


@pytest.fixture
def board(rig):
    """Column 1: cards 1..4; column 2: cards 5..6; dense, owners 1 and 2 -> user 7 (local row exists)."""
    rig.sql("DELETE FROM cards")
    for cid, col, pos in ((1, 1, 0), (2, 1, 1), (3, 1, 2), (4, 1, 3), (5, 2, 0), (6, 2, 1), (7, 3, 0)):
        if cid == 7:
            continue
        rig.sql("INSERT INTO cards (id, column_id, title, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (cid, col, f"card {cid}", pos, NOW, NOW))
    return rig


def move(rig, card, col, pos, token=None):
    r = rig.call("put", f"/cards/{card}/position", token or admin(rig), json={"column_id": col, "position": pos})
    assert r.status_code == 200, r.text
    return r


def assert_dense(rig):
    for col in (1, 2):
        assert positions(rig, col) == list(range(len(positions(rig, col))))


# source card (column 1: [1,2,3,4]) -> destination (column 2: [5,6]) at start/middle/end
@pytest.mark.parametrize("card,dest_pos,src_after,dst_after", [
    (1, 0, [2, 3, 4], [1, 5, 6]),   # first card to the start
    (2, 1, [1, 3, 4], [5, 2, 6]),   # middle card to the middle
    (4, 2, [1, 2, 3], [5, 6, 4]),   # last card to the end
    (3, 99, [1, 2, 4], [5, 6, 3]),  # index past the end means last
])
def test_cross_column_move_closes_gap_and_keeps_order(board, card, dest_pos, src_after, dst_after):
    before = board.sql(f"SELECT id, {BUSINESS} FROM cards ORDER BY id")
    move(board, card, 2, dest_pos)
    assert layout(board) == {1: src_after, 2: dst_after}
    assert_dense(board)
    assert board.sql(f"SELECT id, {BUSINESS} FROM cards ORDER BY id") == before  # no card lost or changed
    assert board.sql("SELECT COUNT(*), COUNT(DISTINCT id) FROM cards") == [(6, 6)]


def test_only_card_of_a_column_leaves_it_empty(board):
    move(board, 5, 1, 0)
    move(board, 6, 1, 0)
    assert layout(board) == {1: [6, 5, 1, 2, 3, 4], 2: []}
    assert_dense(board)


@pytest.mark.parametrize("card,pos,expected", [
    (1, 2, [2, 3, 1, 4]), (4, 0, [4, 1, 2, 3]), (2, 2, [1, 3, 2, 4]), (3, 3, [1, 2, 4, 3]),
])
def test_reorder_inside_column(board, card, pos, expected):
    move(board, card, 1, pos)
    assert layout(board) == {1: expected, 2: [5, 6]}
    assert_dense(board)


def test_move_from_a_column_with_a_gap_renumbers_it(board):
    board.sql("UPDATE cards SET position = position * 5 + 3 WHERE column_id = 1")  # legacy-style sparse positions
    move(board, 2, 2, 0)
    assert layout(board) == {1: [1, 3, 4], 2: [2, 5, 6]}
    assert_dense(board)


@pytest.mark.parametrize("card,col,pos", [(1, 2, 1), (4, 2, 0), (2, 1, 3), (3, 1, 0), (5, 1, 2)])
def test_undo_restores_exact_layout(board, card, col, pos):
    before = layout(board)
    rows = board.sql(f"SELECT id, {BUSINESS} FROM cards ORDER BY id")
    move(board, card, col, pos)
    r = board.call("post", "/undo", admin(board))
    assert r.status_code == 200, r.text
    assert layout(board) == before
    assert_dense(board)
    assert board.sql(f"SELECT id, {BUSINESS} FROM cards ORDER BY id") == rows


def test_member_move_and_undo_keep_owner_and_layout(board):
    uid, token = member(board)
    board.assign(2, uid)
    before = layout(board)
    move(board, 2, 2, 1, token)
    assert board.sql("SELECT salesperson_id FROM cards WHERE id = 2") == [(uid,)]
    assert board.call("post", "/undo", token).status_code == 200
    assert layout(board) == before and board.sql("SELECT salesperson_id FROM cards WHERE id = 2") == [(uid,)]
    assert_dense(board)
    # still forward-only for a member
    move(board, 2, 2, 0, token)
    assert board.call("put", "/cards/2/position", token, json={"column_id": 1, "position": 0}).status_code == 403
