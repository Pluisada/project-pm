"""Cards workbook import of a workbook exported from ANOTHER board: the opt-in that turns card_ids which are not cards
of the destination board into new cards (fresh ids), and the guarantees around it. Disposable tmp_path databases only."""
import pytest

import cards_excel_import
from test_company_card_writes import NOW, admin, member
from test_company_card_writes import rig  # noqa: F401  (fixture)
from test_company_cards_excel_import import book, row, upload, counts, cards

OPTION = {"new_for_unmatched_ids": "true"}


def board_url(board, step):
    return f"/api/companies/lity/boards/{board}/excel/import/{step}"


def post(rig, token, step, content, board=1, **data):
    headers = {"Authorization": f"Bearer {token}"}
    return rig.client.post(board_url(board, step), headers=headers, files=upload(content), data=data)


def preview(rig, token, content, board=1, option=True):
    return post(rig, token, "preview", content, board, **(OPTION if option else {}))


def confirm(rig, token, content, digest, board=1, option=True):
    return post(rig, token, "confirm", content, board, confirm="true", plan_digest=digest, **(OPTION if option else {}))


@pytest.fixture
def two_boards(rig):
    """Board 1 (cards 1 and 2, columns First/Second) and a new EMPTY board 2 with the same stage names."""
    rig.sql("INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (2, 7, 'New board', ?, ?)", (NOW, NOW))
    for column_id, (title, position) in ((21, ("First", 0)), (22, ("Second", 1))):
        rig.sql("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) VALUES (?, 2, ?, ?, 'open', ?, ?)",
                (column_id, title, position, NOW, NOW))
    return rig


def exported(rig, token):
    """The real export of board 1: card_id column filled with ids that mean nothing on any other board."""
    r = rig.call("get", "/excel/export", token)
    assert r.status_code == 200, r.text
    return r.content


def board_cards(rig, board):
    return rig.sql("SELECT c.id, c.title, c.column_id FROM cards c JOIN columns col ON col.id = c.column_id WHERE col.board_id = ? ORDER BY c.id", (board,))


def test_without_the_option_an_exported_workbook_is_still_refused_by_a_different_board(two_boards):
    rig = two_boards
    token = admin(rig)
    body = preview(rig, token, exported(rig, token), board=2, option=False).json()
    assert body["confirmable"] is False and body["summary"]["invalid"] == 2
    assert {e["code"] for r in body["rows"] for e in r["errors"]} == {"card_not_found"}


def test_exported_workbook_into_a_new_empty_board_previews_new_cards_and_confirms_with_fresh_ids(two_boards):
    rig = two_boards
    token = admin(rig)
    content = exported(rig, token)
    before_all = (counts(rig), board_cards(rig, 1), cards(rig))
    body = preview(rig, token, content, board=2).json()
    assert body["confirmable"] is True and body["blocked_reason"] is None
    assert body["summary"]["creates"] == 2 and body["summary"]["updates"] == 0
    assert body["creates_from_unmatched_ids"] == 2 and body["summary"]["invalid"] == 0 and body["summary"]["warnings"] >= 2
    for r in body["rows"]:
        assert r["action"] == "create" and r["unmatched_id"] is True
        assert [w["code"] for w in r["warnings"]] == ["source_id_new_card"] and "new card is created" in r["warnings"][0]["message"]
    assert (counts(rig), board_cards(rig, 1), cards(rig)) == before_all  # a preview writes nothing
    assert board_cards(rig, 2) == []

    done = confirm(rig, token, content, body["plan_digest"], board=2)
    assert done.status_code == 201 and done.json()["created"] == 2 and done.json()["updated"] == 0
    created = board_cards(rig, 2)
    assert [t for _, t, _ in created] == ["card 1", "card 2"]
    assert all(card_id > 2 for card_id, _, _ in created)  # fresh ids from this database, never the source ids 1 and 2
    assert {col for _, _, col in created} <= {21, 22}  # placed in this board's stages
    assert board_cards(rig, 1) == [(1, "card 1", 1), (2, "card 2", 1)]  # the source board is untouched
    assert rig.sql("SELECT COUNT(*) FROM card_excel_imports WHERE board_id = 2") == [(1,)]


def test_the_same_board_still_updates_the_matching_cards_even_with_the_option_on(rig):
    token = admin(rig)
    content = book([row(card_id=1, title="Renamed one"), row(card_id=2, title="Renamed two")])
    body = preview(rig, token, content).json()
    assert body["summary"]["updates"] == 2 and body["summary"]["creates"] == 0 and body["creates_from_unmatched_ids"] == 0
    assert all(r["action"] == "update" and r["unmatched_id"] is False and r["warnings"] == [] for r in body["rows"])
    assert confirm(rig, token, content, body["plan_digest"]).status_code == 201
    assert rig.sql("SELECT id, title FROM cards ORDER BY id") == [(1, "Renamed one"), (2, "Renamed two")]


def test_a_mixed_workbook_updates_matches_and_creates_for_unmatched_and_empty_ids(rig):
    token = admin(rig)
    content = book([row(card_id=1, title="Updated"), row(card_id=999, title="From elsewhere"), row(title="Blank id")])
    body = preview(rig, token, content).json()
    assert [r["action"] for r in body["rows"]] == ["update", "create", "create"]
    assert [r["unmatched_id"] for r in body["rows"]] == [False, True, False]
    assert body["summary"]["creates"] == 2 and body["creates_from_unmatched_ids"] == 1
    assert confirm(rig, token, content, body["plan_digest"]).status_code == 201
    titles = dict(rig.sql("SELECT id, title FROM cards"))
    assert titles[1] == "Updated" and titles[2] == "card 2"
    new = {t: i for i, t in titles.items() if i > 2}
    assert set(new) == {"From elsewhere", "Blank id"} and 999 not in titles


def test_invalid_rows_still_block_confirmation_with_the_option_on(rig):
    token = admin(rig)
    for rows, code in (
        ([row(card_id=999, title="ok"), row(card_id=998, title="x", stage="Nowhere")], "unknown_stage"),
        ([row(card_id=7, title="a"), row(card_id=7, title="b")], "duplicate_card_id"),
    ):
        body = preview(rig, token, book(rows)).json()
        assert body["confirmable"] is False and body["blocked_reason"] == "validation_errors"
        assert code in {e["code"] for r in body["rows"] for e in r["errors"]}
        before = counts(rig)
        r = confirm(rig, token, book(rows), body["plan_digest"])
        assert r.status_code == 422 and r.json()["detail"]["code"] == "validation_errors"
        assert counts(rig) == before


def test_a_failed_confirmation_leaves_the_destination_board_and_ledger_unchanged(two_boards, monkeypatch):
    rig = two_boards
    token = admin(rig)
    content = exported(rig, token)
    digest = preview(rig, token, content, board=2).json()["plan_digest"]
    before = (counts(rig), board_cards(rig, 2))

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cards_excel_import, "audit_new_card_owner", explode)
    r = confirm(rig, token, content, digest, board=2)
    assert r.status_code == 500 and r.json()["detail"]["code"] == "import_failed" and r.json()["detail"]["applied"] is False
    assert (counts(rig), board_cards(rig, 2)) == before and board_cards(rig, 2) == []
    monkeypatch.undo()
    assert confirm(rig, token, content, digest, board=2).status_code == 201  # nothing was consumed: it can be retried


def test_reimporting_the_same_workbook_is_refused_and_the_source_ids_never_touch_other_cards(two_boards):
    rig = two_boards
    token = admin(rig)
    content = exported(rig, token)
    digest = preview(rig, token, content, board=2).json()["plan_digest"]
    assert confirm(rig, token, content, digest, board=2).status_code == 201
    after_first = (counts(rig), board_cards(rig, 1), board_cards(rig, 2))
    again = preview(rig, token, content, board=2).json()
    assert again["confirmable"] is False and again["blocked_reason"] == "already_imported"
    r = confirm(rig, token, content, again["plan_digest"], board=2)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_imported"
    assert (counts(rig), board_cards(rig, 1), board_cards(rig, 2)) == after_first  # no duplicates, no overwrites


def test_the_option_is_part_of_the_confirmation(two_boards):
    rig = two_boards
    token = admin(rig)
    content = exported(rig, token)
    digest = preview(rig, token, content, board=2).json()["plan_digest"]
    before = counts(rig)
    stale = confirm(rig, token, content, digest, board=2, option=False)  # confirmed without the choice that was previewed
    assert stale.status_code in (409, 422) and counts(rig) == before and board_cards(rig, 2) == []
    off = preview(rig, token, content, board=2, option=False).json()
    assert confirm(rig, token, content, off["plan_digest"], board=2, option=False).status_code == 422  # a refused preview stays refused
    assert confirm(rig, token, content, off["plan_digest"], board=2).status_code == 409  # and a digest of the other choice is stale


def test_authorization_and_company_isolation_are_unchanged(two_boards):
    rig = two_boards
    token = admin(rig)
    content = exported(rig, token)
    _, member_token = member(rig, "m@example.com")
    before = (counts(rig), board_cards(rig, 2), rig.digest("valor-log"), rig.digest("salcomp"))
    for step in ("preview", "confirm"):
        r = post(rig, member_token, step, content, board=2, **OPTION, **({"confirm": "true", "plan_digest": "x"} if step == "confirm" else {}))
        assert r.status_code == 403
    r = rig.client.post("/api/companies/valor-log/boards/1/excel/import/preview", headers={"Authorization": f"Bearer {member_token}"},
                        files=upload(content), data=OPTION)
    assert r.status_code == 403  # a member of Lity has no access to another company's import
    assert (counts(rig), board_cards(rig, 2), rig.digest("valor-log"), rig.digest("salcomp")) == before


def test_restoring_deleted_cards_from_an_export_needs_the_explicit_choice_and_gives_fresh_ids(rig):
    token = admin(rig)
    content = exported(rig, token)
    for card_id in (1, 2):
        assert rig.call("delete", f"/cards/{card_id}", token).status_code == 204
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(0,)]

    # Without the choice every row is refused for its card_id, so the UI can offer the explicit action.
    first = preview(rig, token, content, option=False).json()
    assert first["confirmable"] is False
    assert [[e["code"] for e in r["errors"]] for r in first["rows"]] == [["card_not_found"], ["card_not_found"]]
    assert confirm(rig, token, content, first["plan_digest"], option=False).status_code == 422
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(0,)]  # no choice, no import

    # With it, both rows are new cards; the digest of the first preview is not accepted for this plan.
    second = preview(rig, token, content).json()
    assert [r["action"] for r in second["rows"]] == ["create", "create"] and second["creates_from_unmatched_ids"] == 2
    assert second["plan_digest"] != first["plan_digest"]
    assert confirm(rig, token, content, first["plan_digest"]).status_code == 409  # stale: not the plan that was shown
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(0,)]
    assert confirm(rig, token, content, second["plan_digest"]).status_code == 201
    restored = rig.sql("SELECT id, title FROM cards ORDER BY id")
    assert [t for _, t in restored] == ["card 1", "card 2"]
    assert all(card_id > 2 for card_id, _ in restored)  # the deleted cards' ids (1 and 2) are never given out again
