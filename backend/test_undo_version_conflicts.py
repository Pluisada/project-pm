"""Undo on company boards is guarded by card versions: a card changed after the recorded change (also A -> B -> A) makes
Undo refuse with a structured card_version_conflict, apply nothing and keep the entry. Disposable tmp_path databases only."""
import json

import pytest

from models import Card
from test_card_versions import orm, version
from test_company_admin_ai_undo import local_row
from test_company_card_writes import admin, ai, fake_ai, member
from test_company_undo_import import rig, undo  # noqa: F401  (fixture/helper)

TABLES = ("cards", "card_versions", "audit_events", "card_stage_events", "card_edit_history", "undo_entries")


def state(rig):
    return {t: rig.sql(f"SELECT * FROM {t} ORDER BY 1") for t in TABLES}


def steps(rig):
    return json.loads(rig.sql("SELECT steps FROM undo_entries")[0][0])


def someone_else(rig, card_id, **fields):
    """A versioned change by another person (a separate company session that commits)."""
    with orm(rig) as db:
        card = db.get(Card, card_id)
        for name, value in fields.items():
            setattr(card, name, value)
        db.commit()


def edit(rig, token, title="B", card_id=1):
    assert rig.call("put", f"/cards/{card_id}", token, json={"title": title}).status_code == 200


def refused_by_version(response, card_id, expected, current):
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "card_version_conflict"
    assert (detail["card_id"], detail["expected_version"], detail["current_version"]) == (card_id, expected, current)


def test_entries_are_stamped_with_the_version_the_change_produced(rig):
    token = admin(rig)
    edit(rig, token)
    assert steps(rig)[0]["version"] == 1 and version(rig, 1) == 1
    assert rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0}).status_code == 200
    assert steps(rig)[0]["version"] == 2  # the move raised it again


def test_undo_of_an_update_still_works_and_raises_the_version(rig):
    token = admin(rig)
    edit(rig, token)
    assert undo(rig, token).status_code == 200
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("card 1",)] and version(rig, 1) == 2
    assert rig.sql("SELECT COUNT(*) FROM undo_entries") == [(0,)]
    assert undo(rig, token).status_code == 404


def test_undo_of_a_move_a_creation_and_a_deletion_still_work(rig):
    token = admin(rig)
    assert rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0}).status_code == 200
    assert undo(rig, token).status_code == 200 and rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(1,)]
    owner, _ = member(rig, "o@example.com")
    local_row(rig, owner)
    created = rig.call("post", "/cards", token, json={"column_id": 1, "title": "new", "salesperson_id": owner})
    assert created.status_code == 201, created.text
    new_id = created.json()["id"]
    assert steps(rig)[0]["version"] == 0
    assert undo(rig, token).status_code == 200 and rig.sql("SELECT COUNT(*) FROM cards WHERE id = ?", (new_id,)) == [(0,)]
    assert rig.call("delete", "/cards/2", token).status_code == 204
    assert "version" not in steps(rig)[0]  # recreating a deleted card names no existing card
    assert undo(rig, token).status_code == 200 and rig.sql("SELECT title FROM cards WHERE title = 'card 2'") == [("card 2",)]


def test_a_card_changed_by_someone_else_refuses_undo_and_keeps_the_entry(rig):
    token = admin(rig)
    edit(rig, token)
    someone_else(rig, 1, title="C")
    before = state(rig)
    refused_by_version(undo(rig, token), 1, 1, 2)
    assert state(rig) == before  # nothing applied, no version raised, the entry is still there
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("C",)] and rig.sql("SELECT COUNT(*) FROM undo_entries") == [(1,)]
    refused_by_version(undo(rig, token), 1, 1, 2)  # still refused, still kept (not consumed by the refusal)


def test_an_a_b_a_sequence_is_refused_although_the_visible_values_match(rig):
    token = admin(rig)
    edit(rig, token, "B")  # the entry expects title "B" at version 1
    someone_else(rig, 1, title="C")
    someone_else(rig, 1, title="B")  # back to exactly what the entry expects
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("B",)] and steps(rig)[0]["expect"] == {"title": "B"}
    before = state(rig)
    refused_by_version(undo(rig, token), 1, 1, 3)
    assert state(rig) == before


def test_an_entry_without_versions_keeps_the_value_comparison_only(rig):
    token = admin(rig)
    edit(rig, token, "B")
    stripped = [{k: v for k, v in step.items() if k != "version"} for step in steps(rig)]
    rig.sql("UPDATE undo_entries SET steps = ?", (json.dumps(stripped),))
    someone_else(rig, 1, title="C")
    r = undo(rig, token)
    assert r.status_code == 409 and r.json()["detail"] == "Can't undo: \"C\" was changed since (by another user, the AI or an import)"
    assert rig.sql("SELECT COUNT(*) FROM undo_entries") == [(0,)]  # the existing terminal refusal still consumes it


def test_a_position_only_change_by_someone_else_is_not_a_conflict(rig):
    token = admin(rig)
    assert rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0}).status_code == 200
    someone_else(rig, 2, position=7)  # a neighbour renumbered; no version is raised for positions
    assert undo(rig, token).status_code == 200


def test_a_conflict_on_one_card_of_a_batch_refuses_the_whole_undo(rig, monkeypatch):
    token = admin(rig)
    owner, _ = member(rig, "o@example.com")
    local_row(rig, owner)
    fake_ai(monkeypatch, [{"type": "update", "card_id": 1, "title": "ai 1"}, {"type": "update", "card_id": 2, "title": "ai 2"}])
    assert ai(rig, token).status_code == 200
    assert {s["card_id"]: s["version"] for s in steps(rig)} == {1: 1, 2: 1}
    someone_else(rig, 2, title="theirs")
    before = state(rig)
    refused_by_version(undo(rig, token), 2, 1, 2)
    assert state(rig) == before and rig.sql("SELECT title FROM cards ORDER BY id") == [("ai 1",), ("theirs",)]


def test_one_entry_is_applied_once_and_a_second_undo_finds_nothing(rig):
    token = admin(rig)
    edit(rig, token)
    assert undo(rig, token).status_code == 200
    after = state(rig)
    assert undo(rig, token).status_code == 404 and state(rig) == after


def test_a_schema_failure_still_rolls_back_and_keeps_the_entry(rig):
    import sqlite3
    from database_targets import company_database_path
    token = admin(rig)
    edit(rig, token)
    con = sqlite3.connect(company_database_path("lity", rig.e.env))
    con.executescript("DROP TABLE card_versions")
    con.close()
    assert undo(rig, token).status_code == 503
    assert rig.sql("SELECT COUNT(*) FROM undo_entries") == [(1,)] and rig.sql("SELECT title FROM cards WHERE id = 1") == [("B",)]
