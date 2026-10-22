"""AI assistant on company boards: an action built from an out-of-date card is refused with a structured
card_version_conflict and never overwrites a newer change. Disposable tmp_path databases only."""
import pytest

import routes
from models import Card
from test_card_versions import orm, version
from test_company_admin_ai_undo import local_row
from test_company_card_writes import admin, ai, member
from test_company_card_writes import rig  # noqa: F401  (fixture)

def propose(monkeypatch, actions, concurrently=None):
    """The model 'answers' with `actions`; `concurrently(db)` runs while it is thinking (a versioned change by someone else)."""
    async def fake(board_state, history, message):
        if concurrently is not None:
            with orm_rig() as db:
                concurrently(db)
                db.commit()
        return {"success": True, "response": "ok", "actions": actions, "confidence": 1, "tokens_used": 0}

    monkeypatch.setattr(routes, "call_ai_with_board", fake)


class _Holder:
    rig = None


def orm_rig():
    return orm(_Holder.rig)


@pytest.fixture(autouse=True)
def bind(rig):
    _Holder.rig = rig


def failed(response):
    assert response.status_code == 200, response.text
    return response.json()["actions_applied"]["failed"]


def title_change(card_id, title):
    return lambda db: setattr(db.get(Card, card_id), "title", title)


def test_an_unchanged_card_is_updated_and_its_version_raised(rig, monkeypatch):
    token = admin(rig)
    propose(monkeypatch, [{"type": "update", "card_id": 1, "title": "by ai"}])
    r = ai(rig, token)
    assert r.status_code == 200 and r.json()["actions_applied"]["failed"] == []
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("by ai",)] and version(rig, 1) == 1


@pytest.mark.parametrize("action", [
    {"type": "update", "card_id": 1, "title": "by ai"},
    {"type": "move", "card_id": 1, "column_id": 2},
    {"type": "delete", "card_id": 1},
], ids=["update", "move", "delete"])
def test_a_stale_action_is_refused_and_the_newer_change_survives(rig, monkeypatch, action):
    token = admin(rig)
    propose(monkeypatch, [action], concurrently=title_change(1, "typed by a person"))
    r = ai(rig, token)
    entries = failed(r)
    assert len(entries) == 1 and entries[0]["code"] == "card_version_conflict"
    assert entries[0]["card_id"] == 1 and entries[0]["expected_version"] == 0 and entries[0]["current_version"] == 1
    assert r.json()["actions_applied"]["successful"] == []
    assert rig.sql("SELECT title, column_id FROM cards WHERE id = 1") == [("typed by a person", 1)]
    assert version(rig, 1) == 1  # only the person's change
    assert rig.sql("SELECT COUNT(*) FROM audit_events") == [(0,)] and rig.sql("SELECT COUNT(*) FROM card_stage_events WHERE event_type = 'moved'") == [(0,)]
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(0,)]


def test_a_batch_refuses_only_the_stale_cards_and_applies_the_rest(rig, monkeypatch):
    token = admin(rig)
    propose(monkeypatch, [{"type": "update", "card_id": 1, "title": "stale target"}, {"type": "update", "card_id": 2, "title": "fresh target"},
                          {"type": "move", "card_id": 1, "column_id": 2}],
            concurrently=title_change(1, "typed by a person"))
    r = ai(rig, token)
    entries = failed(r)
    assert [e["card_id"] for e in entries] == [1, 1] and {e["code"] for e in entries} == {"card_version_conflict"}
    assert [x["card_id"] for x in r.json()["actions_applied"]["successful"]] == [2]
    assert rig.sql("SELECT id, title, column_id FROM cards ORDER BY id") == [(1, "typed by a person", 1), (2, "fresh target", 1)]
    assert version(rig, 1) == 1 and version(rig, 2) == 1


def test_two_actions_of_one_batch_on_a_fresh_card_do_not_conflict_with_each_other(rig, monkeypatch):
    token = admin(rig)
    propose(monkeypatch, [{"type": "update", "card_id": 1, "title": "one"}, {"type": "move", "card_id": 1, "column_id": 2},
                          {"type": "update", "card_id": 1, "details": "two"}])
    r = ai(rig, token)
    assert r.status_code == 200 and r.json()["actions_applied"]["failed"] == []
    assert rig.sql("SELECT title, column_id, details FROM cards WHERE id = 1") == [("one", 2, "two")]
    assert version(rig, 1) == 1  # one flush, one increment


def test_a_card_that_appeared_after_the_snapshot_is_refused(rig, monkeypatch):
    token = admin(rig)

    def add_card(db):
        db.add(Card(column_id=1, title="appeared meanwhile", position=9))

    propose(monkeypatch, [{"type": "update", "card_id": 3, "title": "guess"}], concurrently=add_card)
    entries = failed(ai(rig, token))
    assert entries[0]["code"] == "card_version_conflict" and entries[0]["expected_version"] is None
    assert rig.sql("SELECT title FROM cards WHERE id = 3") == [("appeared meanwhile",)]


def test_permission_comes_before_the_conflict_so_a_refused_person_learns_nothing(rig, monkeypatch):
    uid, token = member(rig, "m@example.com")
    local_row(rig, uid)
    propose(monkeypatch, [{"type": "update", "card_id": 1, "title": "mine?"}, {"type": "move", "card_id": 1, "column_id": 2}],
            concurrently=title_change(1, "typed by a person"))  # card 1 is unassigned: a member may not touch it
    entries = failed(ai(rig, token))
    assert len(entries) == 2 and all("code" not in e and "current_version" not in e for e in entries)
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("typed by a person",)]
