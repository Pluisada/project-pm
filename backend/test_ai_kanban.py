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
