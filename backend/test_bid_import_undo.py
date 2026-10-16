"""Part 22D follow-up: undo vs later changes (an import, another user, the AI).

Undo entries record the state each change left the card in; undo refuses
when the card no longer matches, instead of overwriting the later change."""
import json

import pytest

from ai_kanban import apply_board_actions
from bid_excel_reader import read_bid_excel
from bid_import_preview import RowDecision
from bid_import_service import apply_bid_import, preview_bid_import
from models import Card, CardSourceRef, UndoEntry, User
from test_bid_excel_reader import build, row as excel_row
from test_undo import assign_to_member, auth, base, client, move, status, test_db, tokens, board, undo  # noqa: F401 (fixtures)

NEWER = {"Atualizado em": "01/04/2026"}


def import_rows(db, board_id, *rows, decisions=()):
    read_result = read_bid_excel(build(list(rows)), "relatorio-leads.xlsx")
    admin = db.query(User).filter(User.username == "admin").one()
    preview = preview_bid_import(db, read_result, board_id, decisions)
    return apply_bid_import(db, read_result, preview, decisions, board_id, admin)


def edit(client, token, board, card_id, **fields):
    response = client.put(f"{base(board)}/cards/{card_id}", json=fields, headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def imported(test_db, board):
    outcome = import_rows(test_db, board["id"], excel_row(**{"Código": "000101-01"}))
    return test_db.get(Card, outcome.created_card_ids[0])


def test_normal_undo_of_an_imported_card(client, tokens, board, imported, test_db):
    edit(client, tokens["admin"], board, imported.id, title="Edited in the app")
    assert undo(client, tokens["admin"], board).status_code == 200
    test_db.refresh(imported)
    assert imported.title == "Projeto Teste"


def test_unchanged_reimport_does_not_block_undo(client, tokens, board, imported, test_db):
    edit(client, tokens["admin"], board, imported.id, title="Edited in the app")
    import_rows(test_db, board["id"], excel_row(**{"Código": "000101-01"}))  # same content: ignored
    assert undo(client, tokens["admin"], board).status_code == 200
    test_db.refresh(imported)
    assert imported.title == "Projeto Teste"


def test_undo_refused_after_the_import_updated_the_same_field(client, tokens, board, imported, test_db):
    edit(client, tokens["admin"], board, imported.id, title="Edited in the app")
    import_rows(test_db, board["id"], excel_row(**{"Código": "000101-01", "Nome do projeto": "From the import", **NEWER}),
                decisions=[RowDecision(2, confirm_update=True)])
    response = undo(client, tokens["admin"], board)
    assert response.status_code == 409 and "changed since" in response.json()["detail"]
    test_db.refresh(imported)
    assert imported.title == "From the import"  # the later change survives
    assert status(client, tokens["admin"], board)["available"] is False


def test_undo_of_an_unrelated_field_still_works_after_an_import(client, tokens, board, imported, test_db):
    edit(client, tokens["admin"], board, imported.id, deal_value=5000, currency="USD")
    import_rows(test_db, board["id"], excel_row(**{"Código": "000101-01", "Nome do projeto": "From the import", **NEWER}),
                decisions=[RowDecision(2, confirm_update=True)])
    assert undo(client, tokens["admin"], board).status_code == 200
    test_db.refresh(imported)
    assert imported.deal_value is None and imported.title == "From the import"


def test_undo_of_a_move_refused_after_the_import_moved_the_card(client, tokens, board, imported, test_db):
    move(client, tokens["admin"], board, imported.id, 2)  # Proposal -> RFI, by hand
    import_rows(test_db, board["id"], excel_row(**{"Código": "000101-01", "Estágio": "SALES EVALUATION", **NEWER}),
                decisions=[RowDecision(2, confirm_update=True, confirm_move=True)])
    test_db.refresh(imported)
    assert imported.column.title == "F2F Meeting"
    response = undo(client, tokens["admin"], board)
    assert response.status_code == 409
    test_db.refresh(imported)
    assert imported.column.title == "F2F Meeting"


def test_undo_refused_after_another_user_changed_the_card(client, tokens, board, test_db):
    card_id = board["columns"][1]["cards"][0]["id"]
    assign_to_member(test_db, {"id": card_id})  # members only edit cards assigned to them
    edit(client, tokens["admin"], board, card_id, details="admin's text")
    edit(client, tokens["member"], board, card_id, details="member's later text")
    response = undo(client, tokens["admin"], board)
    assert response.status_code == 409
    detail = client.get(f"/api/boards/{board['id']}", headers=auth(tokens["admin"])).json()
    card = next(c for col in detail["columns"] for c in col["cards"] if c["id"] == card_id)
    assert card["details"] == "member's later text"


def test_undo_of_a_creation_refused_after_someone_edited_the_card(client, tokens, board):
    created = client.post(f"{base(board)}/cards", json={"column_id": board["columns"][0]["id"], "title": "New"},
                          headers=auth(tokens["member"])).json()
    edit(client, tokens["admin"], board, created["id"], title="Renamed by admin")
    assert undo(client, tokens["member"], board).status_code == 409


@pytest.mark.asyncio
async def test_ai_editing_one_card_twice_is_still_undoable(client, tokens, board, test_db):
    card_id = board["columns"][0]["cards"][0]["id"]
    admin = test_db.query(User).filter(User.username == "admin").one()
    original = test_db.get(Card, card_id).title
    await apply_board_actions(test_db, board["id"], [
        {"type": "update", "card_id": card_id, "title": "First"},
        {"type": "update", "card_id": card_id, "title": "Second"},
        {"type": "move", "card_id": card_id, "column_id": board["columns"][2]["id"]},
    ], admin)
    assert undo(client, tokens["admin"], board).status_code == 200
    card = test_db.get(Card, card_id)
    test_db.refresh(card)
    assert card.title == original and card.column_id == board["columns"][0]["id"]


def test_entries_recorded_before_the_check_are_refused(client, tokens, board, test_db):
    card_id = board["columns"][0]["cards"][0]["id"]
    admin = test_db.query(User).filter(User.username == "admin").one()
    test_db.add(UndoEntry(user_id=admin.id, board_id=board["id"], description="legacy",
                          steps=json.dumps([{"op": "update", "card_id": card_id, "fields": {"title": "Old"}}])))
    test_db.commit()
    response = undo(client, tokens["admin"], board)
    assert response.status_code == 409 and "recorded before" in response.json()["detail"]
    assert test_db.get(Card, card_id).title != "Old"


def test_imports_record_no_undo_entry(test_db, board):
    import_rows(test_db, board["id"], excel_row(**{"Código": "000777-01"}))
    assert test_db.query(UndoEntry).count() == 0
    assert test_db.query(CardSourceRef).count() == 1


@pytest.mark.asyncio
async def test_undo_refused_after_the_ai_changed_the_card(client, tokens, board, test_db):
    card_id = board["columns"][0]["cards"][0]["id"]
    assign_to_member(test_db, {"id": card_id})
    edit(client, tokens["member"], board, card_id, details="member's text")
    admin = test_db.query(User).filter(User.username == "admin").one()
    await apply_board_actions(test_db, board["id"], [{"type": "update", "card_id": card_id, "details": "AI text"}], admin)
    response = undo(client, tokens["member"], board)
    assert response.status_code == 409 and "changed since" in response.json()["detail"]
    card = test_db.get(Card, card_id)
    test_db.refresh(card)
    assert card.details == "AI text"


def test_undo_of_an_edit_survives_a_later_stage_move(client, tokens, board, test_db):
    # A move doesn't touch the edited field: the edit can still be undone safely.
    card_id = board["columns"][0]["cards"][0]["id"]
    assign_to_member(test_db, {"id": card_id})
    original = test_db.get(Card, card_id).title
    edit(client, tokens["member"], board, card_id, title="Member title")
    move(client, tokens["admin"], board, card_id, 2)
    assert undo(client, tokens["member"], board).status_code == 200
    card = test_db.get(Card, card_id)
    test_db.refresh(card)
    assert card.title == original and card.column_id == board["columns"][2]["id"]


def test_undo_of_a_move_refused_after_another_stage_move(client, tokens, board, test_db):
    card_id = board["columns"][0]["cards"][0]["id"]
    assign_to_member(test_db, {"id": card_id})
    move(client, tokens["member"], board, card_id, 1)
    move(client, tokens["admin"], board, card_id, 3)
    assert undo(client, tokens["member"], board).status_code == 409
    card = test_db.get(Card, card_id)
    test_db.refresh(card)
    assert card.column_id == board["columns"][3]["id"]
