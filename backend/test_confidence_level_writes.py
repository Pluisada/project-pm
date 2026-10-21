"""cards.confidence_level, Stage 3: edit, history/audit, undo and AI integration.

Company part: tmp_path company databases through the Phase 2E rig. Legacy AI part: in-memory SQLite. Synthetic data only.
The field is set through an edit (CardCreate has none); Bid import never fills it (test_confidence_level.py).
"""
import json

import pytest

from models import Board, Card, CardBidDetails, CardEditHistory, Column
from test_company_admin_ai_undo import undo
from test_company_admin_products import company_admin
from test_company_card_writes import admin, ai, fake_ai, member, rig  # noqa: F401  (fixtures/helpers)
from test_bid_import_service import env  # noqa: F401  (fixture)
from test_ai_kanban import _admin, _columns, _member, db  # noqa: F401  (fixtures/helpers)
from ai_kanban import apply_board_actions, get_board_context, SYSTEM_PROMPT
from undo import UndoError, apply_undo

import asyncio


def level(rig, card_id=1, slug="lity"):
    return rig.sql("SELECT confidence_level FROM cards WHERE id = ?", (card_id,), slug)[0][0]


def put(rig, token, body, card_id=1, slug="lity"):
    return rig.call("put", f"/cards/{card_id}", token, json=body, slug=slug)


# --- create / edit ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["LOW", "MEDIUM", "HIGH"])
def test_company_edit_persists_each_level_and_a_new_card_starts_empty(rig, value):
    _, token = company_admin(rig)
    seller, _ = member(rig, "seller@example.com")  # an Admin names the salesperson of a new card (Phase 1)
    created = rig.call("post", "/cards", token, json={"column_id": 1, "title": "Fresh", "salesperson_id": seller})
    assert created.status_code == 201 and created.json()["confidence_level"] is None
    assert put(rig, token, {"confidence_level": value}).json()["confidence_level"] == value
    assert level(rig) == value


def test_company_edit_changes_clears_and_omits(rig):
    _, token = company_admin(rig)
    put(rig, token, {"confidence_level": "LOW"})
    assert put(rig, token, {"title": "other"}).json()["confidence_level"] == "LOW"  # omitted: kept
    assert put(rig, token, {"confidence_level": None}).json()["confidence_level"] is None  # explicit null: cleared
    assert level(rig) is None


@pytest.mark.parametrize("value", ["low", "Medium", "HIGH ", "", "MAYBE", 3, True, ["HIGH"]])
def test_company_edit_rejects_invalid_values_and_writes_nothing(rig, value):
    uid, token = member(rig)
    rig.assign(1, uid)
    put(rig, token, {"confidence_level": "MEDIUM"})
    history = rig.sql("SELECT COUNT(*) FROM card_edit_history")
    assert put(rig, token, {"confidence_level": value}).status_code == 422
    assert level(rig) == "MEDIUM" and rig.sql("SELECT COUNT(*) FROM card_edit_history") == history


def test_roles_are_unchanged_for_the_new_field(rig):
    uid, token = member(rig)
    _, visitor = member(rig, "v@example.com", role="visitor")
    assert put(rig, token, {"confidence_level": "HIGH"}).status_code == 403  # unassigned: admin only
    rig.assign(1, 7)
    assert put(rig, token, {"confidence_level": "HIGH"}).status_code == 403  # someone else's
    rig.assign(1, uid)
    assert put(rig, visitor, {"confidence_level": "HIGH"}).status_code == 403  # read-only, even on its own card
    rig.sql("UPDATE cards SET salesperson_id = ? WHERE id = 1", (uid,))
    assert level(rig) is None
    assert put(rig, token, {"confidence_level": "HIGH"}).status_code == 200  # own card


def test_a_write_in_one_company_never_touches_another(rig):
    _, token = company_admin(rig)
    other = member(rig, "o@example.com", slug="valor-log")[1]
    before = rig.digest("valor-log")
    assert put(rig, token, {"confidence_level": "HIGH"}).status_code == 200
    assert put(rig, other, {"confidence_level": "HIGH"}, slug="lity").status_code == 403  # outsider
    assert rig.digest("valor-log") == before and level(rig, slug="valor-log") is None


# --- history / audit ----------------------------------------------------------------------------------------------

def test_member_change_is_one_history_row_naming_the_field(rig):
    uid, token = member(rig)
    rig.assign(1, uid)
    put(rig, token, {"confidence_level": "HIGH"})
    rows = rig.sql("SELECT user_id, changes FROM card_edit_history WHERE card_id = 1")
    assert len(rows) == 1 and rows[0][0] == uid
    assert json.loads(rows[0][1]) == {"confidence_level": {"old": None, "new": "HIGH"}}
    put(rig, token, {"confidence_level": None})  # clearing is logged too
    assert json.loads(rig.sql("SELECT changes FROM card_edit_history ORDER BY id DESC")[0][0]) == {
        "confidence_level": {"old": "HIGH", "new": None}}
    board = rig.call("get", "", token).json()
    assert next(c for col in board["columns"] for c in col["cards"] if c["id"] == 1)["has_member_edits"] is True


def test_an_unchanged_value_is_not_logged_or_audited(rig):
    uid, token = member(rig)
    rig.assign(1, uid)
    put(rig, token, {"confidence_level": "LOW"})
    before = rig.sql("SELECT COUNT(*) FROM card_edit_history"), rig.sql("SELECT COUNT(*) FROM audit_events")
    put(rig, token, {"confidence_level": "LOW"})
    assert (rig.sql("SELECT COUNT(*) FROM card_edit_history"), rig.sql("SELECT COUNT(*) FROM audit_events")) == before


def test_company_admin_edit_of_anothers_card_is_one_audit_row_with_the_field_name_only(rig):
    aid, token = company_admin(rig)
    m, _ = member(rig, "m@example.com")
    rig.assign(1, m)
    assert put(rig, token, {"confidence_level": "HIGH"}).status_code == 200
    rows = rig.sql("SELECT action, actor_user_id, details FROM audit_events WHERE action = 'edit'")
    assert len(rows) == 1 and rows[0][1] == aid
    assert json.loads(rows[0][2])["fields"] == ["confidence_level"] and "HIGH" not in rows[0][2]  # names, never values
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(0,)]  # no "M" badge for an admin edit


def test_a_failed_write_rolls_the_level_and_its_history_back(rig):
    uid, token = member(rig)
    rig.assign(1, uid)
    response = put(rig, token, {"confidence_level": "HIGH", "product_line": "No such product"})
    assert response.status_code >= 400
    assert level(rig) is None and rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(0,)]


# --- undo ---------------------------------------------------------------------------------------------------------

def test_undo_restores_value_to_null_null_to_value_and_value_to_value(rig):
    _, token = company_admin(rig)
    put(rig, token, {"confidence_level": "LOW"})  # null -> value
    assert undo(rig, token).status_code == 200 and level(rig) is None
    put(rig, token, {"confidence_level": "LOW"})
    put(rig, token, {"confidence_level": "HIGH"})  # value -> value
    assert undo(rig, token).status_code == 200 and level(rig) == "LOW"
    put(rig, token, {"confidence_level": None})  # value -> null
    assert undo(rig, token).status_code == 200 and level(rig) == "LOW"


def test_undo_refuses_when_the_level_changed_since_and_keeps_it(rig):
    _, token = company_admin(rig)
    put(rig, token, {"confidence_level": "LOW"})
    rig.sql("UPDATE cards SET confidence_level = 'HIGH' WHERE id = 1")
    assert undo(rig, token).status_code == 409 and level(rig) == "HIGH"


def test_undo_never_crosses_companies_or_users(rig):
    _, token = company_admin(rig)
    uid, mtoken = member(rig)
    rig.assign(1, uid)
    put(rig, token, {"confidence_level": "HIGH"})
    other = member(rig, "o@example.com", slug="valor-log")[1]
    assert undo(rig, other, slug="lity").status_code == 403
    assert undo(rig, mtoken).status_code == 404  # the member has no entry of its own
    assert level(rig) == "HIGH" and level(rig, slug="valor-log") is None


# --- AI (company) -------------------------------------------------------------------------------------------------

def test_ai_sets_clears_and_undoes_the_level_in_one_request(rig, monkeypatch):
    # The Super Admin: it names the salesperson of the created card (Phase 1) and is the only role whose undo may
    # remove a card (Phase 1), so the whole request is undoable.
    token = admin(rig)
    fake_ai(monkeypatch, [
        {"type": "update", "card_id": 1, "confidence_level": "MEDIUM"},
        {"type": "create", "column_id": 1, "title": "AI card", "confidence_level": "HIGH", "salesperson_id": rig.e.admin},
    ])
    applied = ai(rig, token).json()["actions_applied"]
    assert len(applied["successful"]) == 2 and not applied["failed"]
    assert level(rig) == "MEDIUM"
    assert rig.sql("SELECT confidence_level FROM cards WHERE title = 'AI card'") == [("HIGH",)]
    assert undo(rig, token).status_code == 200
    assert level(rig) is None and rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'AI card'") == [(0,)]


@pytest.mark.parametrize("bad", ["high", "VERY HIGH", 0.9, 2, True, ["HIGH"], "70"])
def test_ai_invalid_output_fails_that_action_and_changes_nothing(rig, monkeypatch, bad):
    uid, token = member(rig)
    rig.assign(1, uid)
    fake_ai(monkeypatch, [{"type": "update", "card_id": 1, "confidence_level": bad}])
    applied = ai(rig, token).json()["actions_applied"]
    assert applied["successful"] == [] and len(applied["failed"]) == 1
    assert level(rig) is None and rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(0,)]


def test_ai_member_rules_still_bind_the_new_field(rig, monkeypatch):
    uid, token = member(rig)
    rig.assign(1, uid)
    fake_ai(monkeypatch, [
        {"type": "update", "card_id": 1, "confidence_level": "LOW"},
        {"type": "update", "card_id": 2, "confidence_level": "LOW"},  # not theirs
    ])
    applied = ai(rig, token).json()["actions_applied"]
    assert len(applied["successful"]) == 1 and len(applied["failed"]) == 1
    assert level(rig, 1) == "LOW" and level(rig, 2) is None
    assert len(rig.sql("SELECT id FROM card_edit_history")) == 1


# --- AI (legacy, in-memory) ---------------------------------------------------------------------------------------

def test_prompt_and_context_describe_the_field_without_a_bid_inference(db):
    assert 'exactly "LOW", "MEDIUM" or "HIGH"' in SYSTEM_PROMPT and "never infer it" in SYSTEM_PROMPT
    board = db.query(Board).first()
    card = Card(column_id=_columns(db, board.id)[0].id, title="T", details="", position=0, confidence_level="LOW")
    db.add(card)
    db.commit()
    context = asyncio.run(get_board_context(db, board.id))
    assert context["columns"][0]["cards"][0]["confidence_level"] == "LOW"


def test_legacy_ai_update_validation_isolation_and_undo(db):
    board = db.query(Board).first()
    other = Board(user_id=_admin(db).id, title="Other")
    db.add(other)
    db.commit()
    other_col = Column(board_id=other.id, title="X", position=0)
    db.add(other_col)
    db.commit()
    mine = Card(column_id=_columns(db, board.id)[0].id, title="Mine", details="", position=0)
    foreign = Card(column_id=other_col.id, title="Foreign", details="", position=0)
    db.add_all([mine, foreign])
    db.commit()
    admin_user = _admin(db)

    run = lambda actions: asyncio.run(apply_board_actions(db, board.id, actions, admin_user))
    result = run([
        {"type": "update", "card_id": mine.id, "confidence_level": "HIGH"},
        {"type": "update", "card_id": foreign.id, "confidence_level": "HIGH"},  # another board: not found
        {"type": "update", "card_id": mine.id, "confidence_level": "high"},      # invalid: rejected
    ])
    db.expire_all()
    assert [x["card_id"] for x in result["successful"]] == [mine.id] and len(result["failed"]) == 2
    assert db.get(Card, mine.id).confidence_level == "HIGH" and db.get(Card, foreign.id).confidence_level is None
    with pytest.raises(UndoError):
        apply_undo(db, admin_user, other.id)  # the entry belongs to the first board only
    db.expire_all()
    assert db.get(Card, mine.id).confidence_level == "HIGH"
    apply_undo(db, admin_user, board.id)
    db.expire_all()
    assert db.get(Card, mine.id).confidence_level is None


def test_legacy_ai_member_edit_is_logged_like_any_other_field(db):
    board = db.query(Board).first()
    member_user = _member(db)
    card = Card(column_id=_columns(db, board.id)[0].id, title="M", details="", position=0, salesperson_id=member_user.id)
    db.add(card)
    db.commit()
    asyncio.run(apply_board_actions(db, board.id, [{"type": "update", "card_id": card.id, "confidence_level": "LOW"}],
                                    member_user))
    rows = db.query(CardEditHistory).filter(CardEditHistory.card_id == card.id).all()
    assert len(rows) == 1 and json.loads(rows[0].changes) == {"confidence_level": {"old": None, "new": "LOW"}}


# --- Bid import ---------------------------------------------------------------------------------------------------

def test_bid_import_leaves_the_level_null_and_never_stores_the_old_confidence(env):
    from test_bid_import_service import ROWS, DECISIONS, run as run_import, workbook as make_workbook
    _, outcome = run_import(env, make_workbook(*ROWS), DECISIONS)
    cards = [env.db.get(Card, i) for i in outcome.created_card_ids]
    assert cards and all(c.confidence_level is None for c in cards)  # the import never sets it
    cards[0].confidence_level = "HIGH"  # an explicit user edit later
    env.db.commit()
    run_import(env, make_workbook(*ROWS), DECISIONS)  # re-importing the same file changes nothing
    env.db.expire_all()
    assert env.db.get(Card, cards[0].id).confidence_level == "HIGH"
    assert "confidence" not in [c.name for c in CardBidDetails.__table__.columns]
