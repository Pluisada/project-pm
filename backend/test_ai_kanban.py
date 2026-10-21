"""Tests for apply_board_actions - the AI chat sidebar must be bound by the
same member/admin card rules as the REST routes (see card_rules.py)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ai_kanban import apply_board_actions
from auth import hash_password
from models import Base, User, UserRole, Board, Column, Card, CardEditHistory
from auth_test_utils import VERIFIED


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    member = User(email_verified_at=VERIFIED, username="member", password_hash=hash_password("pw"), role=UserRole.MEMBER.value)
    admin = User(email_verified_at=VERIFIED, username="admin", password_hash=hash_password("pw"), role=UserRole.ADMIN.value)
    session.add_all([member, admin])
    session.commit()

    board = Board(user_id=admin.id, title="Test")
    session.add(board)
    session.commit()

    columns = [Column(board_id=board.id, title=t, position=i) for i, t in enumerate(["To Do", "Doing", "Done"])]
    session.add_all(columns)
    session.commit()

    yield session
    session.close()


def _member(db):
    return db.query(User).filter(User.username == "member").first()


def _admin(db):
    return db.query(User).filter(User.username == "admin").first()


def _columns(db, board_id):
    return db.query(Column).filter(Column.board_id == board_id).order_by(Column.position).all()


@pytest.mark.asyncio
async def test_member_delete_action_is_rejected(db):
    board = db.query(Board).first()
    columns = _columns(db, board.id)
    card = Card(column_id=columns[0].id, title="Task", details="", position=0)
    db.add(card)
    db.commit()

    result = await apply_board_actions(
        db, board.id, [{"type": "delete", "card_id": card.id}], _member(db)
    )
    assert result["success"] is True
    assert result["successful"] == []
    assert result["failed"][0]["reason"] == "Only admins can delete cards"
    assert db.query(Card).filter(Card.id == card.id).first() is not None


@pytest.mark.asyncio
async def test_admin_delete_action_succeeds(db):
    board = db.query(Board).first()
    columns = _columns(db, board.id)
    card = Card(column_id=columns[0].id, title="Task", details="", position=0)
    db.add(card)
    db.commit()

    result = await apply_board_actions(
        db, board.id, [{"type": "delete", "card_id": card.id}], _admin(db)
    )
    assert result["successful"] == [{"type": "delete", "card_id": card.id}]
    assert db.query(Card).filter(Card.id == card.id).first() is None


@pytest.mark.asyncio
async def test_member_backward_move_action_is_rejected(db):
    board = db.query(Board).first()
    columns = _columns(db, board.id)
    card = Card(column_id=columns[2].id, title="Task", details="", position=0, salesperson_id=_member(db).id)
    db.add(card)
    db.commit()

    result = await apply_board_actions(
        db, board.id, [{"type": "move", "card_id": card.id, "column_id": columns[0].id}], _member(db)
    )
    assert result["successful"] == []
    assert "forward" in result["failed"][0]["reason"]
    db.refresh(card)
    assert card.column_id == columns[2].id


@pytest.mark.asyncio
async def test_member_forward_move_action_succeeds(db):
    board = db.query(Board).first()
    columns = _columns(db, board.id)
    card = Card(column_id=columns[0].id, title="Task", details="", position=0, salesperson_id=_member(db).id)
    db.add(card)
    db.commit()

    result = await apply_board_actions(
        db, board.id, [{"type": "move", "card_id": card.id, "column_id": columns[2].id}], _member(db)
    )
    assert result["successful"] == [{"type": "move", "card_id": card.id}]


@pytest.mark.asyncio
async def test_member_update_action_creates_history_and_admin_does_not(db):
    board = db.query(Board).first()
    columns = _columns(db, board.id)
    card = Card(column_id=columns[0].id, title="Old", details="", position=0, salesperson_id=_member(db).id)
    db.add(card)
    db.commit()

    await apply_board_actions(
        db, board.id, [{"type": "update", "card_id": card.id, "title": "New"}], _member(db)
    )
    assert db.query(CardEditHistory).filter(CardEditHistory.card_id == card.id).count() == 1

    await apply_board_actions(
        db, board.id, [{"type": "update", "card_id": card.id, "title": "Admin renamed"}], _admin(db)
    )
    assert db.query(CardEditHistory).filter(CardEditHistory.card_id == card.id).count() == 1


@pytest.mark.asyncio
async def test_legacy_board_context_has_no_loss_reason_flag(db):
    """The loss-reason instruction is for company boards only: the legacy board state is unchanged."""
    from ai_kanban import get_board_context

    state = await get_board_context(db, db.query(Board).first().id)
    assert "loss_reason_required" not in state


# --- F9: internal exception details never reach the client; they are logged ---

SENTINEL = "SELECT secret FROM cards WHERE token = 'hunter2'"


def _db_error():
    from sqlalchemy.exc import OperationalError
    return OperationalError(SENTINEL, {"token": "hunter2"}, Exception("database is locked"))


def _assert_log_is_safe(caplog, *expected):
    """The handlers log category and exception class only: no message, SQL, parameters or traceback."""
    records = [r for r in caplog.records if r.name == "ai_kanban"]
    assert records
    for record in records:
        assert record.exc_info is None and not record.exc_text
    for leaked in ("secret", "hunter2", "SELECT", "Traceback", "database is locked"):
        assert leaked not in caplog.text
    for fragment in expected:
        assert fragment in caplog.text


def _admin_card(db):
    board = db.query(Board).first()
    columns = _columns(db, board.id)
    card = Card(column_id=columns[0].id, title="Task", details="", position=0)
    db.add(card)
    db.commit()
    return board, columns, card


@pytest.mark.asyncio
async def test_unexpected_action_error_is_sanitized_and_logged(db, monkeypatch, caplog):
    board, columns, card = _admin_card(db)
    monkeypatch.setattr("ai_kanban.place_card", lambda *a, **k: (_ for _ in ()).throw(_db_error()))
    action = {"type": "move", "card_id": card.id, "column_id": columns[1].id}

    with caplog.at_level("ERROR", logger="ai_kanban"):
        result = await apply_board_actions(db, board.id, [action], _admin(db))

    assert result["failed"][0]["reason"] == "This action could not be applied"
    assert "secret" not in str(result) and "hunter2" not in str(result)
    _assert_log_is_safe(caplog, "category=action_error", "exception=OperationalError", "action_type=move")


@pytest.mark.asyncio
async def test_batch_failure_is_sanitized_and_logged(db, monkeypatch, caplog):
    board, columns, card = _admin_card(db)
    monkeypatch.setattr("ai_kanban.record_undo", lambda *a, **k: (_ for _ in ()).throw(_db_error()))
    action = {"type": "update", "card_id": card.id, "title": "Renamed"}

    with caplog.at_level("ERROR", logger="ai_kanban"):
        result = await apply_board_actions(db, board.id, [action], _admin(db))

    assert result == {"success": False, "error": "Failed to apply actions"}
    _assert_log_is_safe(caplog, "category=batch_error", "exception=OperationalError")


@pytest.mark.asyncio
async def test_batch_failure_http_detail_is_sanitized(db, monkeypatch):
    from fastapi import HTTPException
    import routes
    from schemas import AIChatRequest

    board, columns, card = _admin_card(db)

    async def fake_ai(*args, **kwargs):
        return {"success": True, "response": "ok", "confidence": 1, "tokens_used": 1,
                "actions": [{"type": "update", "card_id": card.id, "title": "Renamed"}]}

    monkeypatch.setattr(routes, "call_ai_with_board", fake_ai)
    monkeypatch.setattr("ai_kanban.record_undo", lambda *a, **k: (_ for _ in ()).throw(_db_error()))

    with pytest.raises(HTTPException) as caught:
        await routes.board_ai_chat_payload(db, board.id, AIChatRequest(message="rename it"), _admin(db))

    assert caught.value.status_code == 400
    assert caught.value.detail == "Failed to apply actions"


@pytest.mark.asyncio
async def test_authored_action_messages_stay_visible(db):
    board, columns, card = _admin_card(db)
    result = await apply_board_actions(
        db, board.id,
        [{"type": "update", "card_id": card.id, "salesperson_id": 9999},
         {"type": "update", "card_id": card.id, "title": "x" * 500},
         {"type": "delete", "card_id": card.id}],
        _member(db),
    )
    reasons = [f["reason"] for f in result["failed"]]
    assert "Salesperson not found" in reasons[0] or "assigned to you" in reasons[0]
    assert reasons[2] == "Only admins can delete cards"
    assert all("could not be applied" not in r for r in reasons)


@pytest.mark.asyncio
async def test_unknown_action_type_is_not_logged_verbatim(db, monkeypatch, caplog):
    board, columns, card = _admin_card(db)
    monkeypatch.setattr("ai_kanban.place_card", lambda *a, **k: (_ for _ in ()).throw(_db_error()))
    monkeypatch.setattr("ai_kanban.LOGGED_ACTION_TYPES", ("create", "update", "delete"))  # force "move" to be unlisted

    with caplog.at_level("ERROR", logger="ai_kanban"):
        await apply_board_actions(db, board.id, [{"type": "move", "card_id": card.id, "column_id": columns[1].id}], _admin(db))

    assert "action_type=other" in caplog.text and "action_type=move" not in caplog.text
