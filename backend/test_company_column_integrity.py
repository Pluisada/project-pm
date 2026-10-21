"""Parts 2D-A..C: tests for column integrity in central (company) mode.

These tests pin the behavior of the column routes. Part 2D-B: a column that holds cards cannot be deleted (409,
nothing changes). Part 2D-C: creating or moving a column renumbers the board 0..n-1 (unique, contiguous, index past the end = last).

Synthetic data only, on the disposable tmp_path databases of the shared `rig` fixture (no commercial database).
"""
import json

import pytest

import company_runtime as cr
import central_auth as ca
import identity_service as svc
from test_central_deps import GOOD
from test_company_admin_products import company_admin
from test_company_card_writes import NOW, admin, member
from test_company_card_writes import rig  # noqa: F401  (fixture)
from test_company_products import send
from test_company_undo_import import undo

COLS = "/boards/1/columns"


def columns(rig, board=1, slug="lity"):
    return rig.sql("SELECT id, title, position, stage_kind FROM columns WHERE board_id = ? ORDER BY id", (board,), slug)


DATA_TABLES = ("boards", "columns", "cards", "card_edit_history", "import_batches", "card_import_changes",
               "audit_events", "undo_entries", "card_stage_events")


def data(rig, slug="lity"):
    """Every table a column operation could touch, as rows. The `users` table is left out on purpose: an authorized
    write first syncs the actor's local reference row, which is not a column effect."""
    return {t: rig.sql(f"SELECT * FROM {t} ORDER BY id", slug=slug) for t in DATA_TABLES}


def count(rig, table, slug="lity", where="1=1", args=()):
    return rig.sql(f"SELECT COUNT(*) FROM {table} WHERE {where}", args, slug)[0][0]


def add_board2(rig, slug="lity"):
    """A second board (id 2) with one column (id 10) and one card (id 10)."""
    rig.sql("INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (2, 7, 'Other', ?, ?)", (NOW, NOW), slug)
    rig.sql("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
            "VALUES (10, 2, 'Other col', 0, 'open', ?, ?)", (NOW, NOW), slug)
    rig.sql("INSERT INTO cards (id, column_id, title, position, created_at, updated_at) "
            "VALUES (10, 10, 'other card', 0, ?, ?)", (NOW, NOW), slug)


def related_rows(rig, slug="lity"):
    """Edit history, an import batch with a per-card change, a bid-details row and a source ref for card 1."""
    rig.sql("INSERT INTO card_edit_history (id, card_id, user_id, changes, created_at) VALUES (1, 1, 7, ?, ?)",
            (json.dumps({"title": {"old": "a", "new": "b"}}), NOW), slug)
    rig.sql("INSERT INTO import_batches (id, board_id, user_id, filename, file_sha256, status, created_at, updated_at) "
            "VALUES (1, 1, 7, 'synthetic.xlsx', ?, 'completed', ?, ?)", ("0" * 64, NOW, NOW), slug)
    rig.sql("INSERT INTO card_import_changes (id, card_id, import_batch_id, user_id, changes, created_at) "
            "VALUES (1, 1, 1, 7, ?, ?)", (json.dumps({"card.title": {"old": "a", "new": "b"}}), NOW), slug)


# --- A. Deleting an empty column ------------------------------------------------------------------------------------

def test_super_admin_deletes_an_empty_column_and_only_that_column(rig):
    token = admin(rig)
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id = 2") == [(0,)]
    other = rig.digest("valor-log")
    r = send(rig, "delete", f"{COLS}/2", token)
    assert r.status_code == 204 and r.content == b""
    assert columns(rig) == [(1, "First", 0, "open")]
    assert count(rig, "cards") == 2 and count(rig, "boards") == 1
    assert rig.digest("valor-log") == other


@pytest.mark.parametrize("role", ["sales_member", "visitor"])
def test_members_and_visitors_cannot_delete_an_empty_column(rig, role):
    _, token = member(rig, role=role)
    before = data(rig)
    assert send(rig, "delete", f"{COLS}/2", token).status_code == 403
    assert data(rig) == before and len(columns(rig)) == 2


def test_company_admin_cannot_delete_an_empty_column(rig):
    _, token = company_admin(rig)
    before = data(rig)
    assert send(rig, "delete", f"{COLS}/2", token).status_code == 403
    assert data(rig) == before


def test_unauthenticated_delete_is_refused(rig):
    before = data(rig)
    assert rig.client.request("delete", f"/api/companies/lity{COLS}/2").status_code == 401
    assert data(rig) == before


def test_deleting_a_nonexistent_column_is_404_and_changes_nothing(rig):
    before = data(rig)
    r = send(rig, "delete", f"{COLS}/999", admin(rig))
    assert r.status_code == 404 and r.json()["detail"] == "Column not found"
    assert data(rig) == before


def test_deleting_a_nonexistent_board_is_404_and_changes_nothing(rig):
    before = data(rig)
    r = send(rig, "delete", "/boards/99/columns/1", admin(rig))
    assert r.status_code == 404 and r.json()["detail"] == "Board not found"
    assert data(rig) == before


def test_deleting_twice_is_204_then_404(rig):
    token = admin(rig)
    assert send(rig, "delete", f"{COLS}/2", token).status_code == 204
    assert send(rig, "delete", f"{COLS}/2", token).status_code == 404


def test_deleting_the_last_column_of_a_board_is_currently_allowed(rig):
    """No minimum-columns rule exists today; the board is left with no columns."""
    token = admin(rig)
    assert send(rig, "delete", f"{COLS}/2", token).status_code == 204
    # column 1 still holds cards, so empty it of cards first through the same route would delete them: use board 2
    add_board2(rig)
    rig.sql("DELETE FROM cards WHERE id = 10")
    assert send(rig, "delete", "/boards/2/columns/10", token).status_code == 204
    assert columns(rig, board=2) == [] and count(rig, "boards", where="id = 2") == 1


# --- E. Board and company isolation ---------------------------------------------------------------------------------

def test_column_of_another_board_cannot_be_deleted_through_this_board(rig):
    add_board2(rig)
    before = data(rig)
    r = send(rig, "delete", f"{COLS}/10", admin(rig))  # column 10 belongs to board 2
    assert r.status_code == 404 and r.json()["detail"] == "Column not found"
    assert data(rig) == before and columns(rig, board=2) == [(10, "Other col", 0, "open")]


def test_column_of_another_board_cannot_be_edited_through_this_board(rig):
    add_board2(rig)
    before = data(rig)
    r = send(rig, "put", f"{COLS}/10", admin(rig), json={"title": "Hijacked", "position": 9, "stage_kind": "won"})
    assert r.status_code == 404 and data(rig) == before


def test_column_cannot_be_deleted_or_edited_with_the_other_boards_url_either(rig):
    """Column 1 belongs to board 1; addressing it under board 2 is equally refused."""
    add_board2(rig)
    before = data(rig)
    token = admin(rig)
    assert send(rig, "delete", "/boards/2/columns/1", token).status_code == 404
    assert send(rig, "put", "/boards/2/columns/1", token, json={"title": "X"}).status_code == 404
    assert data(rig) == before


def test_same_column_id_in_another_company_is_untouched_by_a_delete_and_an_edit(rig):
    """Both companies were seeded with identical ids; a write under one slug never reaches the other database."""
    token = admin(rig)
    before = {s: rig.digest(s) for s in ("salcomp", "valor-log")}
    assert send(rig, "put", f"{COLS}/2", token, json={"title": "Only lity"}).status_code == 200
    assert send(rig, "delete", f"{COLS}/2", token).status_code == 204
    assert {s: rig.digest(s) for s in ("salcomp", "valor-log")} == before
    assert columns(rig, slug="valor-log") == [(1, "First", 0, "open"), (2, "Second", 1, "open")]


def test_member_of_another_company_cannot_edit_or_delete_columns(rig):
    _, token = member(rig, slug="valor-log")
    before = data(rig)
    rig.opened.clear()
    assert send(rig, "put", f"{COLS}/1", token, json={"title": "X"}).status_code == 403
    assert send(rig, "delete", f"{COLS}/2", token).status_code == 403
    assert send(rig, "post", COLS, token, json={"title": "N"}).status_code == 403
    assert data(rig) == before and rig.opened == []


def test_company_admin_of_another_company_cannot_edit_or_delete_columns(rig):
    _, token = company_admin(rig, slug="valor-log")
    before = data(rig)
    assert send(rig, "put", f"{COLS}/1", token, json={"title": "X"}).status_code == 403
    assert send(rig, "delete", f"{COLS}/2", token).status_code == 403
    assert send(rig, "put", f"{COLS}/1", token, slug="valor-log", json={"title": "Own"}).status_code == 403  # Super Admin only
    assert data(rig) == before


def test_unknown_company_slug_is_refused(rig):
    token = admin(rig)
    assert send(rig, "delete", f"{COLS}/2", token, slug="nope").status_code in (403, 404)


# --- B. Deleting a column that holds cards (refused with 409) --------------------------------------------------------

REFUSAL = "Column cannot be deleted while it contains cards. Move the cards to another column first."


def seed_delete_scenario(rig):
    """Column 1 holds cards 1 and 2; card 3 sits in column 2; card 1 has edit history and an import change."""
    rig.sql("INSERT INTO cards (id, column_id, title, position, created_at, updated_at) VALUES (3, 2, 'safe', 0, ?, ?)", (NOW, NOW))
    related_rows(rig)


def test_delete_of_a_column_with_cards_is_409_with_a_clear_message(rig):
    seed_delete_scenario(rig)
    r = send(rig, "delete", f"{COLS}/1", admin(rig))
    assert r.status_code == 409 and r.json()["detail"] == REFUSAL


def test_rejected_delete_keeps_column_cards_and_every_related_row(rig):
    seed_delete_scenario(rig)
    before = data(rig)
    positions = rig.sql("SELECT id, column_id, position FROM cards ORDER BY id")
    assert send(rig, "delete", f"{COLS}/1", admin(rig)).status_code == 409
    assert data(rig) == before  # columns, cards, edit history, import tables, audit, undo and stage events
    assert rig.sql("SELECT id, column_id, position FROM cards ORDER BY id") == positions
    assert [c[0] for c in columns(rig)] == [1, 2]
    assert count(rig, "card_edit_history") == 1 and count(rig, "card_import_changes") == 1
    assert rig.sql("SELECT id, status, created_count FROM import_batches") == [(1, "completed", 0)]


def test_rejected_delete_writes_no_audit_event_and_no_undo_entry(rig):
    token = admin(rig)
    seed_delete_scenario(rig)
    assert send(rig, "delete", f"{COLS}/1", token).status_code == 409
    assert count(rig, "audit_events") == 0 and count(rig, "undo_entries") == 0
    assert undo(rig, token, method="get").json()["available"] is False


def test_companion_single_card_delete_is_audited(rig):
    """Contrast: DELETE of one card writes an audit event (a rule that is meant to stay)."""
    token = admin(rig)
    assert rig.call("delete", "/cards/1", token).status_code == 204
    assert rig.sql("SELECT action, card_id, board_id FROM audit_events") == [("delete", 1, 1)]


def test_rejected_delete_adds_no_stage_events(rig):
    token = admin(rig)
    r = rig.call("post", "/cards", token, json={"column_id": 2, "title": "tracked", "salesperson_id": rig.e.admin})
    assert r.status_code == 201
    cid = r.json()["id"]
    events = rig.sql("SELECT * FROM card_stage_events ORDER BY id")
    assert send(rig, "delete", f"{COLS}/2", token).status_code == 409
    assert rig.sql("SELECT * FROM card_stage_events ORDER BY id") == events
    assert rig.sql("SELECT COUNT(*) FROM card_stage_events WHERE event_type = 'deleted'") == [(0,)]
    assert rig.sql("SELECT id FROM cards WHERE id = ?", (cid,)) == [(cid,)]


def test_rejected_delete_does_not_invalidate_an_earlier_undo_entry(rig):
    """Move card 1 to column 2, try to delete column 2 (now holding it): refused, and the undo still works."""
    token = admin(rig)
    seed_delete_scenario(rig)
    assert rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0}).status_code == 200
    assert count(rig, "undo_entries") == 1
    assert send(rig, "delete", f"{COLS}/2", token).status_code == 409
    assert count(rig, "undo_entries") == 1
    assert undo(rig, token, method="get").json()["available"] is True
    r = undo(rig, token)
    assert r.status_code == 200, r.text
    assert rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(1,)]


def test_column_becomes_deletable_once_its_cards_are_moved_away(rig):
    token = admin(rig)
    assert send(rig, "delete", f"{COLS}/1", token).status_code == 409
    for cid in (1, 2):
        assert rig.call("put", f"/cards/{cid}/position", token, json={"column_id": 2, "position": cid}).status_code == 200
    assert send(rig, "delete", f"{COLS}/1", token).status_code == 204
    assert columns(rig) == [(2, "Second", 1, "open")] and count(rig, "cards") == 2


@pytest.mark.parametrize("role", ["sales_member", "visitor"])
def test_members_and_visitors_still_get_403_not_409_for_a_column_with_cards(rig, role):
    _, token = member(rig, role=role)
    before = data(rig)
    assert send(rig, "delete", f"{COLS}/1", token).status_code == 403
    assert data(rig) == before


def test_company_admin_still_gets_403_for_a_column_with_cards(rig):
    _, token = company_admin(rig)
    assert send(rig, "delete", f"{COLS}/1", token).status_code == 403


def test_unauthenticated_delete_of_a_column_with_cards_is_401(rig):
    assert rig.client.request("delete", f"/api/companies/lity{COLS}/1").status_code == 401


def test_rejected_delete_with_cards_leaves_other_boards_and_companies_intact(rig):
    add_board2(rig)
    seed_delete_scenario(rig)
    other = rig.digest("valor-log")
    assert send(rig, "delete", f"{COLS}/1", admin(rig)).status_code == 409
    assert columns(rig, board=2) == [(10, "Other col", 0, "open")]
    assert rig.sql("SELECT id FROM cards WHERE id IN (3, 10) ORDER BY id") == [(3,), (10,)]
    assert rig.digest("valor-log") == other


def test_column_of_another_board_with_cards_is_404_through_this_board(rig):
    add_board2(rig)
    before = data(rig)
    assert send(rig, "delete", f"{COLS}/10", admin(rig)).status_code == 404
    assert data(rig) == before


# --- C. Column positions (Part 2D-C): unique, contiguous, 0-based; index past the end means last ----------------------

def order(rig, token):
    body = send(rig, "get", "/boards/1", token).json()
    return [(c["id"], c["position"]) for c in body["columns"]]


def ids(rig, token):
    return [i for i, _ in order(rig, token)]


def assert_dense(rig, token, board=1):
    stored = [p for (p,) in rig.sql("SELECT position FROM columns WHERE board_id = ? ORDER BY position", (board,))]
    assert stored == list(range(len(stored)))
    if board == 1:
        assert [p for _, p in order(rig, token)] == stored  # the API shows what is persisted


def add_columns(rig, token, n=3):
    """Appends n columns at the end of the two seeded ones; returns their ids (board order: 1, 2, *new)."""
    new = []
    for i in range(n):
        r = send(rig, "post", COLS, token, json={"title": f"C{i + 3}", "position": i + 2, "stage_kind": "open"})
        assert r.status_code == 201 and r.json()["position"] == i + 2
        new.append(r.json()["id"])
    return new


def put_pos(rig, token, cid, pos):
    r = send(rig, "put", f"{COLS}/{cid}", token, json={"position": pos})
    assert r.status_code == 200, r.text
    return r


# board [1, 2, a, b, c]: (moved column, target index, resulting order)
@pytest.mark.parametrize("which,target,expected", [
    (4, 0, [4, 1, 2, 3, 5]),    # last-but-one to the start
    (4, 2, [1, 2, 4, 3, 5]),    # intermediate, occupied position
    (1, 4, [2, 3, 4, 5, 1]),    # first to the end
    (3, 3, [1, 2, 4, 3, 5]),    # forward by one
    (5, 1, [1, 5, 2, 3, 4]),    # last to the second place
])
def test_move_in_a_five_column_board_renumbers_the_others(rig, which, target, expected):
    token = admin(rig)
    new = add_columns(rig, token)
    assert ids(rig, token) == [1, 2, *new]
    mapped = {1: 1, 2: 2, 3: new[0], 4: new[1], 5: new[2]}
    r = put_pos(rig, token, mapped[which], target)
    assert r.json()["position"] == target
    assert ids(rig, token) == [mapped[e] for e in expected]
    assert_dense(rig, token)


def test_move_to_the_own_position_changes_nothing(rig):
    token = admin(rig)
    add_columns(rig, token)
    before = order(rig, token)
    assert put_pos(rig, token, 2, 1).json()["position"] == 1
    assert order(rig, token) == before


def test_position_past_the_end_means_last(rig):
    token = admin(rig)
    assert put_pos(rig, token, 1, 1000).json()["position"] == 1
    assert order(rig, token) == [(2, 0), (1, 1)]


def test_move_in_a_two_column_board(rig):
    token = admin(rig)
    assert put_pos(rig, token, 2, 0).json()["position"] == 0
    assert order(rig, token) == [(2, 0), (1, 1)]
    assert put_pos(rig, token, 2, 1).json()["position"] == 1
    assert order(rig, token) == [(1, 0), (2, 1)]


def test_move_in_a_three_column_board(rig):
    token = admin(rig)
    (third,) = add_columns(rig, token, 1)
    put_pos(rig, token, third, 0)
    assert ids(rig, token) == [third, 1, 2]
    put_pos(rig, token, third, 1)
    assert ids(rig, token) == [1, third, 2]
    assert_dense(rig, token)


def test_every_move_in_a_longer_board_leaves_unique_contiguous_positions(rig):
    token = admin(rig)
    new = add_columns(rig, token, 6)
    expected = [1, 2, *new]
    for cid, target in ((new[5], 0), (1, 7), (new[2], 3), (2, 0), (new[0], 99), (new[3], 4)):
        put_pos(rig, token, cid, target)
        expected.remove(cid)
        expected.insert(min(target, len(expected)), cid)
        assert ids(rig, token) == expected
        assert_dense(rig, token)


def test_legacy_duplicate_and_gapped_positions_are_normalized_with_id_as_tie_break(rig):
    token = admin(rig)
    new = add_columns(rig, token)
    rig.sql("UPDATE columns SET position = 5 WHERE id IN (1, 2)")
    rig.sql("UPDATE columns SET position = 9 WHERE id = ?", (new[0],))
    put_pos(rig, token, new[2], 1)  # others by (position, id): [new[1]=3, new[2]... excluded], 1@5, 2@5, new[0]@9
    assert ids(rig, token) == [new[1], new[2], 1, 2, new[0]]
    assert_dense(rig, token)


def test_negative_position_is_rejected_and_nothing_changes(rig):
    token = admin(rig)
    before = data(rig)
    assert send(rig, "put", f"{COLS}/1", token, json={"position": -1}).status_code == 422
    assert send(rig, "post", COLS, token, json={"title": "N", "position": -1}).status_code == 422
    assert data(rig) == before


def test_title_and_stage_kind_edits_do_not_renumber(rig):
    token = admin(rig)
    rig.sql("UPDATE columns SET position = 7 WHERE id = 2")
    assert send(rig, "put", f"{COLS}/1", token, json={"title": "Renamed"}).status_code == 200
    assert rig.sql("SELECT id, position FROM columns ORDER BY id") == [(1, 0), (2, 7)]


def test_reordering_changes_only_column_positions(rig):
    token = admin(rig)
    add_columns(rig, token)
    before = data(rig)
    put_pos(rig, token, 1, 3)
    after = data(rig)
    assert {t: after[t] for t in DATA_TABLES if t != "columns"} == {t: before[t] for t in DATA_TABLES if t != "columns"}
    # id, board_id, title, stage_kind unchanged; only position differs
    strip = lambda rows: [(r[0], r[1], r[2], r[4]) for r in rows]  # noqa: E731  (columns: id, board_id, title, position, stage_kind)
    assert strip(after["columns"]) == strip(before["columns"])


def test_reordering_keeps_cards_in_their_columns_with_the_same_order(rig):
    token = admin(rig)
    cards = rig.sql("SELECT id, column_id, position FROM cards ORDER BY id")
    stages = rig.sql("SELECT * FROM card_stage_events ORDER BY id")
    put_pos(rig, token, 1, 1)
    put_pos(rig, token, 2, 1)
    assert rig.sql("SELECT id, column_id, position FROM cards ORDER BY id") == cards
    assert rig.sql("SELECT * FROM card_stage_events ORDER BY id") == stages


def test_a_rejected_update_leaves_positions_untouched(rig):
    token = admin(rig)
    add_columns(rig, token)
    before = data(rig)
    assert send(rig, "put", f"{COLS}/1", token, json={"position": 3, "stage_kind": "bogus"}).status_code == 422
    assert send(rig, "put", f"{COLS}/1", token, json={"position": -2}).status_code == 422
    assert data(rig) == before


def test_won_conflict_is_refused_before_positions_move(rig):
    token = admin(rig)
    rig.sql("UPDATE columns SET stage_kind = 'won' WHERE id = 1")
    (third,) = add_columns(rig, token, 1)
    before = data(rig)
    r = send(rig, "put", f"{COLS}/{third}", token, json={"position": 0, "stage_kind": "won"})
    assert r.status_code >= 400 and data(rig) == before


def test_a_failure_during_the_commit_rolls_back_every_position(rig, monkeypatch):
    """Positions are written in one transaction (a single commit): if it fails nothing is persisted."""
    from sqlalchemy.orm import Session
    token = admin(rig)
    add_columns(rig, token)
    before = data(rig)

    def boom(self):
        raise RuntimeError("simulated failure at commit")

    monkeypatch.setattr(Session, "commit", boom)
    with pytest.raises(RuntimeError):
        send(rig, "put", f"{COLS}/1", token, json={"position": 3})
    monkeypatch.undo()
    assert data(rig) == before


# Creation

def test_create_the_first_column_of_an_empty_board(rig):
    token = admin(rig)
    rig.sql("DELETE FROM cards")
    rig.sql("DELETE FROM columns")
    r = send(rig, "post", COLS, token, json={"title": "Only", "position": 5})
    assert r.status_code == 201 and r.json()["position"] == 0
    assert_dense(rig, token)


def test_create_at_the_start_in_the_middle_and_at_the_end(rig):
    token = admin(rig)
    start = send(rig, "post", COLS, token, json={"title": "S", "position": 0}).json()["id"]
    assert ids(rig, token) == [start, 1, 2]
    middle = send(rig, "post", COLS, token, json={"title": "M", "position": 2}).json()["id"]
    assert ids(rig, token) == [start, 1, middle, 2]
    end = send(rig, "post", COLS, token, json={"title": "E", "position": 4})
    assert end.json()["position"] == 4 and ids(rig, token) == [start, 1, middle, 2, end.json()["id"]]
    assert_dense(rig, token)


def test_create_at_an_occupied_position_shifts_the_others_without_duplicates(rig):
    token = admin(rig)
    r = send(rig, "post", COLS, token, json={"title": "Dup", "position": 1})
    assert r.status_code == 201 and r.json()["position"] == 1
    assert order(rig, token) == [(1, 0), (r.json()["id"], 1), (2, 2)]


def test_create_past_the_end_appends_and_the_response_has_the_real_position(rig):
    token = admin(rig)
    r = send(rig, "post", COLS, token, json={"title": "Far", "position": 500})
    assert r.status_code == 201 and r.json()["position"] == 2
    assert ids(rig, token) == [1, 2, r.json()["id"]]


def test_create_without_a_position_uses_the_schema_default_and_goes_first(rig):
    """ColumnCreate.position defaults to 0, so an omitted position inserts at the start (contract kept)."""
    token = admin(rig)
    r = send(rig, "post", COLS, token, json={"title": "Default"})
    assert r.status_code == 201 and r.json()["position"] == 0
    assert ids(rig, token) == [r.json()["id"], 1, 2]
    assert_dense(rig, token)


def test_create_on_one_board_does_not_renumber_another(rig):
    token = admin(rig)
    add_board2(rig)
    send(rig, "post", COLS, token, json={"title": "X", "position": 0})
    assert rig.sql("SELECT id, position FROM columns WHERE board_id = 2") == [(10, 0)]


def test_a_rejected_create_changes_nothing(rig):
    token = admin(rig)
    before = data(rig)
    assert send(rig, "post", COLS, token, json={"title": "W", "position": 0, "stage_kind": "bogus"}).status_code == 422
    assert data(rig) == before


# Authorization and isolation

@pytest.mark.parametrize("role", ["sales_member", "visitor"])
def test_members_and_visitors_cannot_create_or_reorder(rig, role):
    _, token = member(rig, "x@example.com", role)
    before = data(rig)
    assert send(rig, "put", f"{COLS}/1", token, json={"position": 1}).status_code == 403
    assert send(rig, "post", COLS, token, json={"title": "N", "position": 0}).status_code == 403
    assert data(rig) == before


def test_company_admin_cannot_create_or_reorder_columns(rig):
    _, token = company_admin(rig)
    before = data(rig)
    assert send(rig, "put", f"{COLS}/1", token, json={"position": 1}).status_code == 403
    assert send(rig, "post", COLS, token, json={"title": "N", "position": 0}).status_code == 403
    assert data(rig) == before


def test_reordering_through_another_boards_url_is_404_and_changes_nothing(rig):
    token = admin(rig)
    add_board2(rig)
    before = data(rig)
    assert send(rig, "put", "/boards/2/columns/1", token, json={"position": 0}).status_code == 404
    assert send(rig, "put", f"{COLS}/10", token, json={"position": 0}).status_code == 404
    assert data(rig) == before


def test_reordering_in_one_company_leaves_the_other_companys_columns_untouched(rig):
    token = admin(rig)
    before = {s_: rig.digest(s_) for s_ in ("salcomp", "valor-log")}
    put_pos(rig, token, 1, 1)
    assert send(rig, "post", COLS, token, json={"title": "N", "position": 0}).status_code == 201
    assert {s_: rig.digest(s_) for s_ in ("salcomp", "valor-log")} == before


# --- D. stage_kind ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["open", "won", "lost"])
def test_allowed_stage_kinds_can_be_created_on_a_fresh_board(rig, kind):
    add_board2(rig)
    rig.sql("DELETE FROM cards")
    r = send(rig, "post", "/boards/2/columns", admin(rig), json={"title": "K", "position": 3, "stage_kind": kind})
    assert r.status_code == 201 and r.json()["stage_kind"] == kind


@pytest.mark.parametrize("bad", ["bogus", "WON", "Open", "", "closed"])
def test_unknown_stage_kind_is_422_on_create_and_update(rig, bad):
    token = admin(rig)
    before = data(rig)
    assert send(rig, "post", COLS, token, json={"title": "X", "position": 3, "stage_kind": bad}).status_code == 422
    assert send(rig, "put", f"{COLS}/2", token, json={"stage_kind": bad}).status_code == 422
    assert data(rig) == before


@pytest.mark.parametrize("kind", ["won", "lost"])
def test_a_second_won_or_lost_column_is_refused_on_create(rig, kind):
    token = admin(rig)
    assert send(rig, "post", COLS, token, json={"title": "One", "position": 2, "stage_kind": kind}).status_code == 201
    before = data(rig)
    r = send(rig, "post", COLS, token, json={"title": "Two", "position": 3, "stage_kind": kind})
    assert r.status_code == 409 and r.json()["detail"] == f'This board already has a "{kind}" stage'
    assert data(rig) == before and len(columns(rig)) == 3


@pytest.mark.parametrize("kind", ["won", "lost"])
def test_changing_an_empty_column_to_an_already_used_won_or_lost_is_refused(rig, kind):
    token = admin(rig)
    send(rig, "post", COLS, token, json={"title": "One", "position": 2, "stage_kind": kind})
    before = data(rig)
    r = send(rig, "put", f"{COLS}/2", token, json={"stage_kind": kind})  # column 2 is empty
    assert r.status_code == 409 and data(rig) == before


def test_stage_kind_of_a_column_with_cards_cannot_change(rig):
    token = admin(rig)
    before = data(rig)
    r = send(rig, "put", f"{COLS}/1", token, json={"stage_kind": "won"})  # column 1 holds cards 1 and 2
    assert r.status_code == 409 and "has cards" in r.json()["detail"]
    assert data(rig) == before and columns(rig)[0][3] == "open"


def test_stage_kind_of_a_column_with_cards_cannot_change_and_nothing_else_in_the_request_is_applied(rig):
    """The rejected request is atomic: its title and position are not applied either."""
    token = admin(rig)
    before = data(rig)
    r = send(rig, "put", f"{COLS}/1", token, json={"title": "Renamed", "position": 9, "stage_kind": "lost"})
    assert r.status_code == 409 and data(rig) == before


def test_resending_the_same_stage_kind_on_a_column_with_cards_is_accepted(rig):
    token = admin(rig)
    r = send(rig, "put", f"{COLS}/1", token, json={"stage_kind": "open", "title": "Renamed"})
    assert r.status_code == 200 and columns(rig)[0][1:] == ("Renamed", 0, "open")


def test_stage_kind_of_an_empty_column_can_change_to_a_free_kind(rig):
    token = admin(rig)
    r = send(rig, "put", f"{COLS}/2", token, json={"stage_kind": "won"})
    assert r.status_code == 200 and columns(rig)[1][3] == "won"


def test_rename_alone_never_changes_stage_kind(rig):
    token = admin(rig)
    send(rig, "put", f"{COLS}/2", token, json={"stage_kind": "lost"})
    assert send(rig, "put", f"{COLS}/2", token, json={"title": "Closed"}).status_code == 200
    assert columns(rig)[1] == (2, "Closed", 1, "lost")


def test_stage_kind_uniqueness_is_per_board(rig):
    token = admin(rig)
    add_board2(rig)
    assert send(rig, "post", COLS, token, json={"title": "W1", "position": 2, "stage_kind": "won"}).status_code == 201
    assert send(rig, "post", "/boards/2/columns", token, json={"title": "W2", "position": 2, "stage_kind": "won"}).status_code == 201


def test_deleting_the_won_column_frees_the_won_slot(rig):
    token = admin(rig)
    cid = send(rig, "post", COLS, token, json={"title": "W", "position": 2, "stage_kind": "won"}).json()["id"]
    assert send(rig, "delete", f"{COLS}/{cid}", token).status_code == 204
    assert send(rig, "post", COLS, token, json={"title": "W again", "position": 2, "stage_kind": "won"}).status_code == 201


def test_company_admin_cannot_change_stage_kinds(rig):
    _, token = company_admin(rig)
    before = data(rig)
    assert send(rig, "put", f"{COLS}/1", token, json={"stage_kind": "lost"}).status_code == 403
    assert send(rig, "put", f"{COLS}/2", token, json={"stage_kind": "bogus"}).status_code == 403  # authorization first
    assert data(rig) == before


# --- F. Title and input validation ----------------------------------------------------------------------------------

def test_title_length_limits_are_enforced_by_the_request_schema(rig):
    token = admin(rig)
    before = data(rig)
    for body in ({"title": ""}, {"title": "x" * 256}):
        assert send(rig, "post", COLS, token, json={**body, "position": 2}).status_code == 422
        assert send(rig, "put", f"{COLS}/2", token, json=body).status_code == 422
    assert data(rig) == before
    assert send(rig, "post", COLS, token, json={"title": "x" * 255, "position": 2}).status_code == 201
    assert send(rig, "put", f"{COLS}/2", token, json={"title": "y" * 255}).status_code == 200


def test_current_whitespace_only_and_duplicate_titles_are_accepted(rig):
    """GAP (no rule exists): the schema checks length only, so a title of spaces and a title equal to another
    column's are stored as given (no trimming, no uniqueness). Recorded, not endorsed."""
    token = admin(rig)
    r = send(rig, "post", COLS, token, json={"title": "   ", "position": 2})
    assert r.status_code == 201 and r.json()["title"] == "   "
    assert send(rig, "put", f"{COLS}/2", token, json={"title": "First"}).status_code == 200
    assert [c[1] for c in columns(rig)] == ["First", "First", "   "]


def test_current_title_is_stored_without_trimming(rig):
    token = admin(rig)
    r = send(rig, "put", f"{COLS}/2", token, json={"title": "  padded  "})
    assert r.status_code == 200 and columns(rig)[1][1] == "  padded  "


def test_null_title_on_update_means_unchanged_and_an_empty_body_is_a_noop(rig):
    token = admin(rig)
    before = columns(rig)
    assert send(rig, "put", f"{COLS}/2", token, json={"title": None}).status_code == 200
    assert send(rig, "put", f"{COLS}/2", token, json={}).status_code == 200
    assert columns(rig) == before


def test_create_requires_a_title_and_defaults_position_and_kind(rig):
    token = admin(rig)
    assert send(rig, "post", COLS, token, json={}).status_code == 422
    r = send(rig, "post", COLS, token, json={"title": "Defaults"})
    assert r.status_code == 201 and (r.json()["position"], r.json()["stage_kind"]) == (0, "open")


def test_create_on_a_missing_board_is_404_and_writes_nothing(rig):
    before = data(rig)
    assert send(rig, "post", "/boards/99/columns", admin(rig), json={"title": "X"}).status_code == 404
    assert data(rig) == before
