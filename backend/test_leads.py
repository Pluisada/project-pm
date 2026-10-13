"""Tests for sales lead fields, lead options, the report and the column migration."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ai_kanban import apply_board_actions
from database import _add_missing_card_columns, get_db
from lead_options import STAGES
from main import app
from models import Base, Board, Card, CardEditHistory, Column, User
from reports import build_board_report


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
    """Admin (via setup, which seeds the sales board) and a member."""
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
    return client.get("/api/boards", headers=auth(tokens["admin"])).json()[0]


@pytest.fixture
def card_id(client, tokens, board):
    detail = client.get(f"/api/boards/{board['id']}", headers=auth(tokens["admin"])).json()
    return detail["columns"][0]["cards"][0]["id"]


def update(client, token, board, card_id, payload):
    return client.put(
        f"/api/boards/{board['id']}/cards/{card_id}", json=payload, headers=auth(token)
    )


LEAD = {
    "company": "Acme Oy",
    "contact": "Matti Meikalainen",
    "deal_value": 125000.5,
    "currency": "EUR",
    "expected_close_date": "2026-12-31",
    "lead_source": "Referral",
    "product_line": "Power supplies",
}


def test_setup_seeds_sales_stages_with_leads(client, tokens, board):
    detail = client.get(f"/api/boards/{board['id']}", headers=auth(tokens["admin"])).json()
    assert [col["title"] for col in detail["columns"]] == STAGES
    first = detail["columns"][0]["cards"][0]
    assert first["company"] and first["currency"] and first["salesperson_name"] == "admin"


def test_lead_fields_round_trip(client, tokens, board, card_id, test_db):
    member = test_db.query(User).filter(User.username == "member").one()
    response = update(client, tokens["admin"], board, card_id, {**LEAD, "salesperson_id": member.id})
    assert response.status_code == 200

    detail = client.get(f"/api/boards/{board['id']}", headers=auth(tokens["admin"])).json()
    card = next(c for col in detail["columns"] for c in col["cards"] if c["id"] == card_id)
    for field, value in LEAD.items():
        assert card[field] == value
    assert card["salesperson_name"] == "member"


@pytest.mark.parametrize(
    "payload",
    [
        {"currency": "GBP"},
        {"product_line": "Batteries"},
        {"lead_source": "Billboard"},
        {"deal_value": -1},
        {"expected_close_date": "not-a-date"},
        {"title": None},
        {"salesperson_id": 9999},
    ],
)
def test_invalid_lead_fields_rejected(client, tokens, board, card_id, payload):
    assert update(client, tokens["admin"], board, card_id, payload).status_code == 422


def test_null_clears_and_omitted_keeps(client, tokens, board, card_id):
    update(client, tokens["admin"], board, card_id, LEAD)
    card = update(client, tokens["admin"], board, card_id, {"company": None}).json()
    assert card["company"] is None
    assert card["contact"] == LEAD["contact"]


def test_member_lead_edit_is_logged(client, tokens, board, card_id, test_db):
    admin = test_db.query(User).filter(User.username == "admin").one()
    update(client, tokens["admin"], board, card_id, {"salesperson_id": None})
    assert test_db.query(CardEditHistory).count() == 0

    card = update(
        client, tokens["member"], board, card_id,
        {"deal_value": 1000, "expected_close_date": "2027-01-31", "salesperson_id": admin.id},
    ).json()
    assert card["has_member_edits"] is True

    history = client.get(
        f"/api/boards/{board['id']}/cards/{card_id}/history", headers=auth(tokens["admin"])
    ).json()
    changes = history[0]["changes"]
    assert changes["salesperson"] == {"old": None, "new": "admin"}
    assert changes["expected_close_date"]["new"] == "2027-01-31"
    assert changes["deal_value"]["new"] == 1000


def test_lead_options_open_to_members(client, tokens):
    response = client.get("/api/lead-options", headers=auth(tokens["member"]))
    assert response.status_code == 200
    options = response.json()
    assert "EUR" in options["currencies"]
    assert options["product_lines"] == ["Chargers", "Power supplies"]
    assert {s["username"] for s in options["salespeople"]} == {"admin", "member"}


def test_report_endpoint(client, tokens, board):
    response = client.get(f"/api/boards/{board['id']}/report", headers=auth(tokens["member"]))
    assert response.status_code == 200
    report = response.json()
    assert [s["title"] for s in report["stages"]] == STAGES
    assert report["kpis"]["won_count"] == 1 and report["kpis"]["lost_count"] == 1
    assert report["kpis"]["win_rate"] == 0.5


@pytest.fixture
def report_board(test_db):
    """A board with hand-picked leads for exact report assertions."""
    alice = User(username="alice", password_hash="x", role="member")
    bob = User(username="bob", password_hash="x", role="member")
    test_db.add_all([alice, bob])
    test_db.flush()
    board = Board(user_id=alice.id, title="B")
    test_db.add(board)
    test_db.flush()
    cols = {t: Column(board_id=board.id, title=t, position=i) for i, t in enumerate(STAGES)}
    test_db.add_all(cols.values())
    test_db.flush()

    def lead(stage, value, currency, person, product="Chargers", close=None, source=None):
        test_db.add(Card(
            column_id=cols[stage].id, title=f"{stage} {value}", deal_value=value,
            currency=currency, salesperson_id=person.id if person else None,
            product_line=product, expected_close_date=close, lead_source=source,
        ))

    lead("1st Contact", 100, "EUR", alice, close=date(2026, 10, 1))   # overdue
    lead("RFI", 200, "EUR", alice, close=date(2026, 10, 20))          # within 30 days
    lead("Proposal", 50, "USD", bob, product="Power supplies", close=date(2027, 6, 1))
    lead("Award", 300, "USD", bob, close=date(2026, 10, 2))           # won: not "closing soon"
    lead("Lost", 999, "EUR", None, close=date(2026, 10, 3))
    test_db.commit()
    return board, alice


TODAY = date(2026, 10, 13)


def test_report_totals_per_currency(test_db, report_board):
    board, _ = report_board
    report = build_board_report(test_db, board.id, today=TODAY)
    kpis = report["kpis"]
    assert kpis["open_count"] == 3
    assert kpis["open_totals"] == {"EUR": 300, "USD": 50}
    assert kpis["won_totals"] == {"USD": 300}
    assert kpis["lost_count"] == 1
    assert kpis["win_rate"] == 0.5

    by_person = {g["key"]: g for g in report["by_salesperson"]}
    assert by_person["alice"]["open_totals"] == {"EUR": 300}
    assert by_person["bob"]["won_count"] == 1
    assert "Unassigned" not in by_person  # the only unassigned lead is lost


def test_report_closing_soon(test_db, report_board):
    board, _ = report_board
    soon = build_board_report(test_db, board.id, today=TODAY)["closing_soon"]
    assert [(lead["stage"], lead["overdue"]) for lead in soon] == [
        ("1st Contact", True),
        ("RFI", False),
    ]


def test_report_filters(test_db, report_board):
    board, alice = report_board
    by_alice = build_board_report(test_db, board.id, salesperson_id=alice.id, today=TODAY)
    assert by_alice["kpis"]["open_count"] == 2
    assert by_alice["kpis"]["win_rate"] is None

    psu = build_board_report(test_db, board.id, product_line="Power supplies", today=TODAY)
    assert psu["kpis"]["open_totals"] == {"USD": 50}


@pytest.mark.asyncio
async def test_ai_update_with_lead_fields(test_db, report_board):
    board, alice = report_board
    card = test_db.query(Card).filter(Card.title == "RFI 200").one()
    results = await apply_board_actions(
        test_db,
        board.id,
        [
            {"type": "update", "card_id": card.id, "company": "Acme", "currency": "USD"},
            {"type": "update", "card_id": card.id, "currency": "GBP"},
            {"type": "create", "column_id": card.column_id, "title": "New", "product_line": "Chargers"},
        ],
        alice,
    )
    assert len(results["successful"]) == 2
    assert results["failed"][0]["action"]["currency"] == "GBP"

    test_db.refresh(card)
    assert (card.company, card.currency) == ("Acme", "USD")
    entry = test_db.query(CardEditHistory).filter(CardEditHistory.card_id == card.id).one()
    assert set(json.loads(entry.changes)) == {"company", "currency"}
    created = test_db.query(Card).filter(Card.title == "New").one()
    assert created.product_line == "Chargers" and not created.has_member_edits


def test_add_missing_card_columns_migrates_old_table():
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE cards (id INTEGER PRIMARY KEY, column_id INTEGER, title VARCHAR, "
            "details TEXT, position INTEGER, created_at DATETIME, updated_at DATETIME)"
        ))
        conn.execute(text("INSERT INTO cards (id, column_id, title, position) VALUES (1, 1, 'old', 0)"))

    _add_missing_card_columns(engine)
    _add_missing_card_columns(engine)  # safe to run again

    names = {col["name"] for col in inspect(engine).get_columns("cards")}
    assert {"company", "deal_value", "currency", "salesperson_id", "expected_close_date"} <= names
    with engine.connect() as conn:
        assert conn.execute(text("SELECT title, company FROM cards")).one() == ("old", None)
