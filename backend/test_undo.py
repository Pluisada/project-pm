"""Tests for single-level, per-user undo."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ai_kanban import apply_board_actions
from database import get_db
from main import app
from models import Base, Card, CardEditHistory, User


@pytest.fixture(scope="function")
def test_db():
    """Create an isolated in-memory database per test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(test_db):
    """Test client wired to the isolated database."""
    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tokens(client):
    """Admin (setup seeds the sales board) and a member."""
    admin = client.post(
        "/api/setup", json={"username": "admin", "password": "adminpass123"}
    ).json()["access_token"]
    client.post(
        "/api/users",
        json={"username": "member", "password": "memberpass123"},
        headers=auth(admin),
    )
    member = client.post(
        "/api/login", json={"username": "member", "password": "memberpass123"}
    ).json()["access_token"]
    return {"admin": admin, "member": member}


@pytest.fixture
def board(client, tokens):
    detail_id = client.get("/api/boards", headers=auth(tokens["admin"])).json()[0]["id"]
    return client.get(f"/api/boards/{detail_id}", headers=auth(tokens["admin"])).json()


@pytest.fixture
def card(board):
    """The first lead in the first stage, with its column ids for moves."""
    return board["columns"][0]["cards"][0]


def base(board):
    return f"/api/boards/{board['id']}"


def status(client, token, board):
    return client.get(f"{base(board)}/undo", headers=auth(token)).json()


def undo(client, token, board):
    return client.post(f"{base(board)}/undo", headers=auth(token))


def move(client, token, board, card_id, column_index, position=0):
    return client.put(
        f"{base(board)}/cards/{card_id}/position",
        json={"column_id": board["columns"][column_index]["id"], "position": position},
        headers=auth(token),
    )


def test_nothing_to_undo(client, tokens, board):
    assert status(client, tokens["admin"], board) == {
        "available": False, "description": None, "undone": None,
    }
    assert undo(client, tokens["admin"], board).status_code == 404


def test_undo_move(client, tokens, board, card, test_db):
    move(client, tokens["admin"], board, card["id"], 2)
    entry = status(client, tokens["admin"], board)
    assert entry["available"] and "1st Contact" in entry["description"]

    response = undo(client, tokens["admin"], board)
    assert response.status_code == 200
    assert response.json()["undone"] == entry["description"]
    assert test_db.get(Card, card["id"]).column_id == board["columns"][0]["id"]
    assert status(client, tokens["admin"], board)["available"] is False


def test_member_can_undo_own_forward_move(client, tokens, board, card, test_db):
    assert move(client, tokens["member"], board, card["id"], 3).status_code == 200
    # A direct backward move is still refused...
    assert move(client, tokens["member"], board, card["id"], 0).status_code == 403
    # ...but undoing their own move puts it back.
    assert undo(client, tokens["member"], board).status_code == 200
    assert test_db.get(Card, card["id"]).column_id == board["columns"][0]["id"]


def test_undo_lead_edit_is_logged_for_member(client, tokens, board, card, test_db):
    original = {"company": card["company"], "deal_value": card["deal_value"]}
    client.put(
        f"{base(board)}/cards/{card['id']}",
        json={"company": "Changed Oy", "deal_value": 1},
        headers=auth(tokens["member"]),
    )
    assert undo(client, tokens["member"], board).status_code == 200

    restored = test_db.get(Card, card["id"])
    test_db.refresh(restored)
    assert {"company": restored.company, "deal_value": restored.deal_value} == original
    # The edit and its undo are both member edits on the record.
    assert test_db.query(CardEditHistory).filter(CardEditHistory.card_id == card["id"]).count() == 2


def test_no_op_edit_keeps_previous_entry(client, tokens, board, card):
    move(client, tokens["admin"], board, card["id"], 1)
    client.put(
        f"{base(board)}/cards/{card['id']}",
        json={"title": card["title"]},
        headers=auth(tokens["admin"]),
    )
    assert "back to" in status(client, tokens["admin"], board)["description"]


def test_undo_create(client, tokens, board, test_db):
    created = client.post(
        f"{base(board)}/cards",
        json={"column_id": board["columns"][0]["id"], "title": "Oops"},
        headers=auth(tokens["member"]),
    ).json()
    assert undo(client, tokens["member"], board).status_code == 200
    assert test_db.get(Card, created["id"]) is None


def test_undo_delete_recreates_card(client, tokens, board, card, test_db):
    client.delete(f"{base(board)}/cards/{card['id']}", headers=auth(tokens["admin"]))
    assert undo(client, tokens["admin"], board).status_code == 200

    recreated = test_db.query(Card).filter(Card.title == card["title"]).one()
    assert recreated.column_id == card["column_id"]
    assert recreated.company == card["company"]
    assert recreated.expected_close_date.isoformat() == card["expected_close_date"]
    assert recreated.salesperson_id == card["salesperson_id"]


def test_single_level(client, tokens, board, card, test_db):
    move(client, tokens["admin"], board, card["id"], 1)
    move(client, tokens["admin"], board, card["id"], 2)
    assert undo(client, tokens["admin"], board).status_code == 200
    assert test_db.get(Card, card["id"]).column_id == board["columns"][1]["id"]
    assert undo(client, tokens["admin"], board).status_code == 404


def test_undo_is_per_user(client, tokens, board, card):
    move(client, tokens["admin"], board, card["id"], 1)
    assert status(client, tokens["member"], board)["available"] is False
    assert undo(client, tokens["member"], board).status_code == 404
    assert status(client, tokens["admin"], board)["available"] is True


def test_undo_after_card_deleted_conflicts(client, tokens, board, card):
    move(client, tokens["member"], board, card["id"], 1)
    client.delete(f"{base(board)}/cards/{card['id']}", headers=auth(tokens["admin"]))

    response = undo(client, tokens["member"], board)
    assert response.status_code == 409
    assert "deleted" in response.json()["detail"]
    assert status(client, tokens["member"], board)["available"] is False


@pytest.mark.asyncio
async def test_ai_request_undone_in_one_step(client, tokens, board, card, test_db):
    member = test_db.query(User).filter(User.username == "member").one()
    first_column = board["columns"][0]["id"]
    await apply_board_actions(
        test_db,
        board["id"],
        [
            {"type": "create", "column_id": first_column, "title": "AI lead"},
            {"type": "update", "card_id": card["id"], "company": "AI Corp"},
            {"type": "move", "card_id": card["id"], "column_id": board["columns"][2]["id"]},
        ],
        member,
    )
    assert "3 changes" in status(client, tokens["member"], board)["description"]

    assert undo(client, tokens["member"], board).status_code == 200
    test_db.expire_all()
    restored = test_db.get(Card, card["id"])
    assert restored.column_id == first_column
    assert restored.company == card["company"]
    assert test_db.query(Card).filter(Card.title == "AI lead").count() == 0
