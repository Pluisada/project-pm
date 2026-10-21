"""AI chat: permissions are decided on the card's state after the model call, not before it.

get_board_context loads the cards before the (slow) model call; another user may reassign or move a card meanwhile.
The change is made from inside the fake model call, through a separate connection to a disposable tmp_path
database, so the interleaving is deterministic (no threads, no sleeps).
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import routes
from auth import create_user_access_token, hash_password
from auth_test_utils import VERIFIED
from database import get_db
from main import app
from models import Base, Board, Card, Column, ConversationMessage, UndoEntry, User
from test_company_card_writes import NOW, member
from test_company_card_writes import rig  # noqa: F401  (fixture)


def model_that(monkeypatch, during, actions):
    """Replace the model call: `during()` runs while the request is 'waiting for the model'."""
    async def fake(board_state, history, message):
        during()
        return {"success": True, "response": "ok", "actions": actions, "confidence": 1, "tokens_used": 0}

    monkeypatch.setattr(routes, "call_ai_with_board", fake)


# --- company path ------------------------------------------------------------------------------------------------

def company_ai(rig, token):
    return rig.client.post("/api/companies/lity/boards/1/ai", headers={"Authorization": f"Bearer {token}"},
                           json={"message": "move it"})


def company_state(rig):
    return (rig.sql("SELECT id, column_id, position, salesperson_id FROM cards ORDER BY id"),
            rig.sql("SELECT card_id, from_column_id, to_column_id FROM card_stage_events ORDER BY id"),
            rig.sql("SELECT COUNT(*) FROM undo_entries"), rig.sql("SELECT COUNT(*) FROM card_edit_history"))


def test_company_reassigned_card_is_not_moved_on_stale_ownership(rig, monkeypatch):
    uid, token = member(rig)
    other, _ = member(rig, "other@example.com")
    rig.assign(1, uid)
    reassign = lambda: rig.sql("INSERT OR IGNORE INTO users (id, username, created_at, updated_at) VALUES (?, 'other', ?, ?)",
                               (other, NOW, NOW)) or rig.sql("UPDATE cards SET salesperson_id = ? WHERE id = 1", (other,))
    model_that(monkeypatch, reassign, [{"type": "move", "card_id": 1, "column_id": 2}])
    before_cards = rig.sql("SELECT id, column_id, position FROM cards ORDER BY id")
    r = company_ai(rig, token)
    assert r.status_code == 200, r.text
    applied = r.json()["actions_applied"]
    assert applied["successful"] == [] and [f["reason"] for f in applied["failed"]] == [
        "You can only move opportunities assigned to you"]
    assert rig.sql("SELECT id, column_id, position FROM cards ORDER BY id") == before_cards
    assert rig.sql("SELECT salesperson_id FROM cards WHERE id = 1") == [(other,)]
    assert rig.sql("SELECT COUNT(*) FROM card_stage_events") == [(0,)]
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(0,)]
    steps = rig.sql("SELECT steps FROM undo_entries")
    assert all("move" not in s[0] for s in steps)  # at most an empty entry: nothing restorable was recorded


def test_company_reassigned_card_is_not_edited_on_stale_ownership(rig, monkeypatch):
    uid, token = member(rig)
    other, _ = member(rig, "other@example.com")
    rig.assign(1, uid)
    reassign = lambda: rig.sql("INSERT OR IGNORE INTO users (id, username, created_at, updated_at) VALUES (?, 'other', ?, ?)",
                               (other, NOW, NOW)) or rig.sql("UPDATE cards SET salesperson_id = ? WHERE id = 1", (other,))
    model_that(monkeypatch, reassign, [{"type": "update", "card_id": 1, "title": "hijacked"}])
    r = company_ai(rig, token)
    assert r.status_code == 200, r.text
    assert r.json()["actions_applied"]["successful"] == []
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("card 1",)]
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(0,)]


def test_company_card_moved_forward_meanwhile_cannot_be_moved_back_by_a_member(rig, monkeypatch):
    uid, token = member(rig)
    rig.assign(1, uid)
    rig.sql("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
            "VALUES (3, 1, 'Third', 2, 'open', ?, ?)", (NOW, NOW))
    # Stale view: card in column 1, so "move to column 2" looks forward. Fresh state: it is in column 3, so it is backward.
    model_that(monkeypatch, lambda: rig.sql("UPDATE cards SET column_id = 3 WHERE id = 1"),
               [{"type": "move", "card_id": 1, "column_id": 2}])
    r = company_ai(rig, token)
    assert r.status_code == 200, r.text
    applied = r.json()["actions_applied"]
    assert applied["successful"] == [] and "forward" in applied["failed"][0]["reason"]
    assert rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(3,)]
    assert rig.sql("SELECT COUNT(*) FROM card_stage_events") == [(0,)]


def test_company_unaffected_move_still_applies(rig, monkeypatch):
    uid, token = member(rig)
    rig.assign(1, uid)
    model_that(monkeypatch, lambda: None, [{"type": "move", "card_id": 1, "column_id": 2}])
    r = company_ai(rig, token)
    assert [a["type"] for a in r.json()["actions_applied"]["successful"]] == ["move"]
    assert rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(2,)]
    assert rig.sql("SELECT COUNT(*) FROM undo_entries") == [(1,)]


# --- legacy path -------------------------------------------------------------------------------------------------

@pytest.fixture
def legacy(tmp_path):
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add_all([
            User(email_verified_at=VERIFIED, username="admin", password_hash=hash_password("Adminpass123!"), role="admin"),
            User(email_verified_at=VERIFIED, username="m1@example.com", email="m1@example.com",
                 password_hash=hash_password("Memberpass123!"), role="member"),
            User(email_verified_at=VERIFIED, username="m2@example.com", email="m2@example.com",
                 password_hash=hash_password("Memberpass123!"), role="member"),
        ])
        db.commit()
        db.add(Board(user_id=1, title="B"))
        db.commit()
        for i, title in enumerate(("First", "Second", "Third")):
            db.add(Column(board_id=1, title=title, position=i))
        db.commit()
        db.add(Card(column_id=1, title="c", position=0, salesperson_id=2))
        db.commit()
        tokens = {n: create_user_access_token(db.query(User).filter(User.username == n).one())
                  for n in ("m1@example.com",)}

    def get_db_override():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = get_db_override
    raw = sqlite3.connect(path, check_same_thread=False)
    yield TestClient(app), tokens["m1@example.com"], raw
    raw.close()
    app.dependency_overrides.clear()
    engine.dispose()


def legacy_ai(client, token):
    return client.post("/api/boards/1/ai", headers={"Authorization": f"Bearer {token}"}, json={"message": "move it"})


def legacy_snapshot(raw):
    return (raw.execute("SELECT id, column_id, position, salesperson_id FROM cards").fetchall(),
            raw.execute("SELECT COUNT(*) FROM card_stage_events").fetchall(),
            raw.execute("SELECT COUNT(*) FROM card_edit_history").fetchall())


def test_legacy_reassigned_card_is_not_moved_on_stale_ownership(legacy, monkeypatch):
    client, token, raw = legacy

    def reassign():
        raw.execute("UPDATE cards SET salesperson_id = 3 WHERE id = 1")
        raw.commit()

    model_that(monkeypatch, reassign, [{"type": "move", "card_id": 1, "column_id": 2}])
    events = raw.execute("SELECT COUNT(*) FROM card_stage_events").fetchall()
    r = legacy_ai(client, token)
    assert r.status_code == 200, r.text
    applied = r.json()["actions_applied"]
    assert applied["successful"] == [] and [f["reason"] for f in applied["failed"]] == [
        "You can only move opportunities assigned to you"]
    assert legacy_snapshot(raw) == ([(1, 1, 0, 3)], events, [(0,)])
    assert all("move" not in (row[0] or "") for row in raw.execute("SELECT steps FROM undo_entries"))


def test_legacy_card_moved_forward_meanwhile_cannot_be_moved_back_by_a_member(legacy, monkeypatch):
    client, token, raw = legacy

    def advance():
        raw.execute("UPDATE cards SET column_id = 3 WHERE id = 1")
        raw.commit()

    model_that(monkeypatch, advance, [{"type": "move", "card_id": 1, "column_id": 2}])
    events = raw.execute("SELECT COUNT(*) FROM card_stage_events").fetchall()
    r = legacy_ai(client, token)
    applied = r.json()["actions_applied"]
    assert applied["successful"] == [] and "forward" in applied["failed"][0]["reason"]
    assert raw.execute("SELECT column_id FROM cards WHERE id = 1").fetchall() == [(3,)]
    assert raw.execute("SELECT COUNT(*) FROM card_stage_events").fetchall() == events


def test_legacy_unaffected_move_still_applies(legacy, monkeypatch):
    client, token, raw = legacy
    model_that(monkeypatch, lambda: None, [{"type": "move", "card_id": 1, "column_id": 2}])
    r = legacy_ai(client, token)
    assert [a["type"] for a in r.json()["actions_applied"]["successful"]] == ["move"]
    assert raw.execute("SELECT column_id FROM cards WHERE id = 1").fetchall() == [(2,)]
