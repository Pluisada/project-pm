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
from lead_options import PIPELINE_STAGES

STAGES = [title for title, _kind in PIPELINE_STAGES]
from main import app
from models import Base, Board, Card, CardEditHistory, Column, User
from reports import build_board_report
from auth_test_utils import SETUP_HEADERS, add_member, VERIFIED


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
        "/api/setup", json={"username": "admin", "password": "Adminpass123!"}, headers=SETUP_HEADERS
    ).json()["access_token"]
    add_member(client, "member", "Memberpass123!")
    member = client.post(
        "/api/login", json={"username": "member", "password": "Memberpass123!"}
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
    "currency": "USD",
    "expected_close_date": "2026-12-31",
    "lead_source": "Referral",
    "product_line": "Power supplies",
}


def test_setup_seeds_sales_stages_with_leads(client, tokens, board):
    detail = client.get(f"/api/boards/{board['id']}", headers=auth(tokens["admin"])).json()
    assert [col["title"] for col in detail["columns"]] == STAGES
    assert [col["stage_kind"] for col in detail["columns"]] == [kind for _t, kind in PIPELINE_STAGES]
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
        {"currency": "EUR"},   # a former option: no longer accepted for new writes
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
    member = test_db.query(User).filter(User.username == "member").one()
    update(client, tokens["admin"], board, card_id, {"salesperson_id": member.id})
    assert test_db.query(CardEditHistory).count() == 0

    card = update(
        client, tokens["member"], board, card_id,
        {"deal_value": 1000, "expected_close_date": "2027-01-31"},
    ).json()
    assert card["has_member_edits"] is True

    history = client.get(
        f"/api/boards/{board['id']}/cards/{card_id}/history", headers=auth(tokens["admin"])
    ).json()
    changes = history[0]["changes"]
    assert "salesperson" not in changes
    assert changes["expected_close_date"]["new"] == "2027-01-31"
    assert changes["deal_value"]["new"] == 1000


def test_lead_options_open_to_members(client, tokens):
    response = client.get("/api/lead-options", headers=auth(tokens["member"]))
    assert response.status_code == 200
    options = response.json()
    assert options["currencies"] == ["USD"]
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
    alice = User(email_verified_at=VERIFIED, username="alice", password_hash="x", role="member")
    bob = User(email_verified_at=VERIFIED, username="bob", password_hash="x", role="member")
    test_db.add_all([alice, bob])
    test_db.flush()
    board = Board(user_id=alice.id, title="B")
    test_db.add(board)
    test_db.flush()
    cols = {
        t: Column(board_id=board.id, title=t, position=i, stage_kind=kind)
        for i, (t, kind) in enumerate(PIPELINE_STAGES)
    }
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


def test_report_totals_are_usd_whatever_legacy_currency_codes_the_rows_hold(test_db, report_board):
    board, _ = report_board
    report = build_board_report(test_db, board.id, today=TODAY)
    kpis = report["kpis"]
    assert kpis["open_count"] == 3
    # The fixture rows carry EUR and USD codes; amounts are summed as USD, unconverted.
    assert kpis["open_totals"] == {"USD": 350}
    assert kpis["won_totals"] == {"USD": 300}
    assert kpis["lost_count"] == 1
    assert kpis["win_rate"] == 0.5

    by_person = {g["key"]: g for g in report["by_salesperson"]}
    assert by_person["alice"]["open_totals"] == {"USD": 300}
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


def _bid_board(client, test_db, tokens, board):
    """Give the first card of the seeded board Bid details and a source ref; return (card_id, other_card_id)."""
    from models import CardBidDetails, CardSourceRef

    detail = client.get(f"/api/boards/{board['id']}", headers=auth(tokens["admin"])).json()
    cards = [c for col in detail["columns"] for c in col["cards"]]
    with_bid, without = cards[0], cards[1]
    test_db.add(CardBidDetails(
        card_id=with_bid["id"], plant="Manaus", product="Charger", confidence=40,
        revenue_current_year_cents=123456, revenue_currency="USD", cpm="CPM Name",
        next_step="Send RFI", next_step_due=date(2027, 1, 15), status_note="note",
    ))
    test_db.add(CardSourceRef(card_id=with_bid["id"], source_key="k1", source_code="BID-1"))
    test_db.commit()
    return with_bid, without


def test_board_returns_bid_details_separate_from_lead_fields(client, test_db, tokens, board):
    with_bid, without = _bid_board(client, test_db, tokens, board)
    cards = {
        c["id"]: c
        for col in client.get(f"/api/boards/{board['id']}", headers=auth(tokens["member"])).json()["columns"]
        for c in col["cards"]
    }
    card = cards[with_bid["id"]]
    bid = card["bid_details"]
    assert bid["plant"] == "Manaus" and bid["cpm"] == "CPM Name" and bid["confidence"] == 40
    assert bid["revenue_current_year_cents"] == 123456 and bid["revenue_currency"] == "USD"
    assert bid["revenue_next_year_cents"] is None and bid["volume_current_year"] is None
    assert bid["next_step_due"] == "2027-01-15"
    assert card["source_ref"] == {"source_code": "BID-1", "source_confidence": None}
    # Lead fields are untouched by the Bid data.
    assert card["deal_value"] == with_bid["deal_value"]
    assert card["currency"] == with_bid["currency"]
    assert card["expected_close_date"] == with_bid["expected_close_date"]
    # A card without Bid details keeps the old shape plus null.
    assert cards[without["id"]]["bid_details"] is None
    assert cards[without["id"]]["source_ref"] is None


def test_board_bid_details_do_not_cost_a_query_per_card(client, test_db, tokens, board):
    from sqlalchemy import event

    _bid_board(client, test_db, tokens, board)
    statements = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    engine = test_db.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        response = client.get(f"/api/boards/{board['id']}", headers=auth(tokens["admin"]))
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200
    assert sum("FROM card_bid_details" in s for s in statements) == 1
    assert sum("FROM card_source_refs" in s for s in statements) == 1


# --- annual Deal Value: deal_value (current year) + deal_value_next_year ---

def _card(client, token, board, card_id):
    detail = client.get(f"/api/boards/{board['id']}", headers=auth(token)).json()
    return next(c for col in detail["columns"] for c in col["cards"] if c["id"] == card_id)


def test_new_card_has_zero_next_year_and_the_current_year(client, tokens, board):
    detail = client.get(f"/api/boards/{board['id']}", headers=auth(tokens["admin"])).json()
    created = client.post(
        f"/api/boards/{board['id']}/cards",
        json={"column_id": detail["columns"][0]["id"], "title": "Fresh"},
        headers=auth(tokens["admin"]),
    ).json()
    assert created["deal_value_next_year"] == 0
    assert created["deal_year"] == date.today().year


def test_both_annual_values_save_independently_in_usd(client, tokens, board, card_id):
    token = tokens["admin"]
    update(client, token, board, card_id, {"deal_value": 100000, "currency": "USD"})
    card = update(client, token, board, card_id, {"deal_value_next_year": 50000}).json()
    assert (card["deal_value"], card["deal_value_next_year"], card["currency"]) == (100000, 50000, "USD")

    card = update(client, token, board, card_id, {"deal_value": 120000}).json()
    assert (card["deal_value"], card["deal_value_next_year"]) == (120000, 50000)
    # The stored year is not something a client can set.
    year = card["deal_year"]
    assert update(client, token, board, card_id, {"deal_year": year + 5}).json()["deal_year"] == year


def test_cleared_next_year_is_zero_and_negative_is_rejected(client, tokens, board, card_id):
    update(client, tokens["admin"], board, card_id, {"deal_value_next_year": 70})
    card = update(client, tokens["admin"], board, card_id, {"deal_value_next_year": None}).json()
    assert card["deal_value_next_year"] == 0
    assert update(client, tokens["admin"], board, card_id, {"deal_value_next_year": -1}).status_code == 422


def test_next_year_edit_follows_the_card_permissions(client, tokens, board, card_id, test_db):
    # Unassigned card: a member can't edit it, so can't touch the next-year value either.
    assert update(client, tokens["member"], board, card_id, {"deal_value_next_year": 5}).status_code == 403
    member = test_db.query(User).filter(User.username == "member").one()
    update(client, tokens["admin"], board, card_id, {"salesperson_id": member.id})
    card = update(client, tokens["member"], board, card_id, {"deal_value_next_year": 5}).json()
    assert card["deal_value_next_year"] == 5
    history = client.get(
        f"/api/boards/{board['id']}/cards/{card_id}/history", headers=auth(tokens["admin"])
    ).json()
    assert history[0]["changes"]["deal_value_next_year"]["new"] == 5


def test_bid_revenue_stays_apart_from_the_annual_values(client, tokens, board, card_id, test_db):
    from models import CardBidDetails
    test_db.add(CardBidDetails(
        card_id=card_id, revenue_current_year_cents=999900, revenue_next_year_cents=111100, revenue_currency="USD",
    ))
    test_db.commit()
    update(client, tokens["admin"], board, card_id, {"deal_value": 10, "deal_value_next_year": 20, "currency": "USD"})
    card = _card(client, tokens["admin"], board, card_id)
    assert (card["deal_value"], card["deal_value_next_year"], card["currency"]) == (10, 20, "USD")
    assert card["bid_details"]["revenue_current_year_cents"] == 999900
    assert card["bid_details"]["revenue_next_year_cents"] == 111100
    assert card["bid_details"]["revenue_currency"] == "USD"


# --- report: current-year, next-year and two-year totals ---

def _set_next_year(test_db, amounts):
    for title, next_year in amounts.items():
        test_db.query(Card).filter(Card.title == title).one().deal_value_next_year = next_year
    test_db.commit()


def test_report_exposes_current_next_and_two_year_totals_in_usd(test_db, report_board):
    board, _ = report_board
    _set_next_year(test_db, {"1st Contact 100": 40, "Proposal 50": 25, "Award 300": 10, "Lost 999": 500})
    report = build_board_report(test_db, board.id, today=TODAY)
    kpis = report["kpis"]
    # Current-year totals are the old amounts added as USD (fixture codes were EUR and USD); counts do not move.
    assert (kpis["open_count"], kpis["won_count"], kpis["lost_count"], kpis["win_rate"]) == (3, 1, 1, 0.5)
    assert kpis["open_totals"] == {"USD": 350}
    assert kpis["open_next_totals"] == {"USD": 65}
    assert kpis["open_sum_totals"] == {"USD": 415}
    assert (kpis["won_totals"], kpis["won_next_totals"], kpis["won_sum_totals"]) == (
        {"USD": 300}, {"USD": 10}, {"USD": 310})
    assert report["deal_years"] == [date.today().year]

    stages = {s["title"]: s for s in report["stages"]}
    assert stages["1st Contact"]["sum_totals"] == {"USD": 140}
    assert stages["Lost"]["next_totals"] == {"USD": 500}   # stage rows include every stage, as before
    assert stages["RFI"]["next_totals"] == {"USD": 0}      # a stored 0, not "unavailable"

    by_person = {g["key"]: g for g in report["by_salesperson"]}
    assert by_person["alice"]["open_sum_totals"] == {"USD": 340}
    assert by_person["alice"]["open_next_totals"] == {"USD": 40}
    assert by_person["bob"]["open_sum_totals"] == {"USD": 75}
    assert by_person["bob"]["won_sum_totals"] == {"USD": 310}


def test_report_has_a_single_usd_group_and_keeps_unavailable_apart_from_zero(test_db, report_board):
    board, alice = report_board
    column = test_db.query(Column).filter(Column.title == "RFI", Column.board_id == board.id).one()
    test_db.add_all([
        Card(column_id=column.id, title="next only", deal_value=None, deal_value_next_year=7, currency="EUR",
             salesperson_id=alice.id),
        Card(column_id=column.id, title="empty", salesperson_id=alice.id),
        # No currency at all: its amounts are still USD now.
        Card(column_id=column.id, title="no currency", deal_value=5, deal_value_next_year=5,
             salesperson_id=alice.id),
    ])
    test_db.commit()
    kpis = build_board_report(test_db, board.id, today=TODAY)["kpis"]
    # "next only" has no current-year value: unavailable there, counted in next year and total.
    assert kpis["open_totals"] == {"USD": 355}
    assert kpis["open_next_totals"] == {"USD": 12}
    assert kpis["open_sum_totals"] == {"USD": 367}
    assert set(kpis["open_totals"]) == {"USD"}
    assert kpis["open_count"] == 6   # counts are about opportunities, not amounts


def test_report_filters_apply_to_every_measure_and_year_list(test_db, report_board):
    board, alice = report_board
    _set_next_year(test_db, {"RFI 200": 60, "Proposal 50": 25})
    by_alice = build_board_report(test_db, board.id, salesperson_id=alice.id, today=TODAY)
    assert by_alice["kpis"]["open_sum_totals"] == {"USD": 360}
    psu = build_board_report(test_db, board.id, product_line="Power supplies", today=TODAY)
    assert (psu["kpis"]["open_totals"], psu["kpis"]["open_next_totals"], psu["kpis"]["open_sum_totals"]) == (
        {"USD": 50}, {"USD": 25}, {"USD": 75})
    assert [lead["title"] for lead in psu["closing_soon"]] == []


def test_report_lists_every_stored_year_when_cards_span_years(test_db, report_board):
    board, _ = report_board
    test_db.query(Card).filter(Card.title == "Proposal 50").one().deal_year = 2031
    test_db.commit()
    years = build_board_report(test_db, board.id, today=TODAY)["deal_years"]
    assert years == sorted({date.today().year, 2031})


def test_closing_soon_lead_shows_both_years_and_the_total(test_db, report_board):
    board, _ = report_board
    _set_next_year(test_db, {"RFI 200": 60})
    lead = next(l for l in build_board_report(test_db, board.id, today=TODAY)["closing_soon"] if l["stage"] == "RFI")
    assert (lead["deal_value"], lead["deal_value_next_year"], lead["deal_value_total"]) == (200, 60, 260)
    assert lead["deal_year"] == date.today().year


def test_report_endpoint_serialises_the_annual_measures(client, tokens, board, card_id):
    def totals():
        kpis = client.get(f"/api/boards/{board['id']}/report", headers=auth(tokens["admin"])).json()["kpis"]
        return kpis["open_sum_totals"]["USD"], kpis["open_next_totals"]["USD"]

    before_sum, before_next = totals()
    detail = client.get(f"/api/boards/{board['id']}", headers=auth(tokens["admin"])).json()
    old = next(c for col in detail["columns"] for c in col["cards"] if c["id"] == card_id)
    update(client, tokens["admin"], board, card_id, {"deal_value": 100, "deal_value_next_year": 50})
    after_sum, after_next = totals()
    # Only this card's contribution moved: (100 + 50) instead of (old + 0), all USD.
    assert after_sum - before_sum == 150 - (old["deal_value"] or 0)
    assert after_next - before_next == 50
    body = client.get(f"/api/boards/{board['id']}/report", headers=auth(tokens["admin"])).json()
    assert body["deal_years"] == [date.today().year]


# --- Deal Value is always USD ---

def test_only_usd_is_accepted_and_options_list_it(client, tokens, board, card_id):
    for code in ("EUR", "BRL", "GBP", "usd", ""):
        assert update(client, tokens["admin"], board, card_id, {"currency": code}).status_code == 422
    assert update(client, tokens["admin"], board, card_id, {"currency": "USD"}).status_code == 200
    options = client.get("/api/lead-options", headers=auth(tokens["admin"])).json()
    assert options["currencies"] == ["USD"]


def test_legacy_currency_codes_are_read_as_usd_with_amounts_unchanged(client, tokens, board, card_id, test_db):
    row = test_db.get(Card, card_id)
    row.deal_value, row.deal_value_next_year, row.currency = 12345.5, 678.25, "BRL"
    test_db.commit()
    card = _card(client, tokens["admin"], board, card_id)
    assert (card["deal_value"], card["deal_value_next_year"], card["currency"]) == (12345.5, 678.25, "USD")
    assert test_db.get(Card, card_id).currency == "BRL"      # reading never rewrites the row


def test_a_card_with_amounts_but_no_currency_is_usd(client, tokens, board, card_id, test_db):
    row = test_db.get(Card, card_id)
    row.deal_value, row.currency = 500, None
    test_db.commit()
    assert _card(client, tokens["admin"], board, card_id)["currency"] == "USD"


def test_saving_amounts_stores_usd_without_sending_a_currency_and_without_logging_it(
    client, tokens, board, card_id, test_db
):
    member = test_db.query(User).filter(User.username == "member").one()
    row = test_db.get(Card, card_id)
    row.currency, row.salesperson_id = "EUR", member.id            # a legacy code, assigned to the member
    test_db.commit()
    card = update(client, tokens["member"], board, card_id, {"deal_value": 777}).json()
    assert (card["deal_value"], card["currency"]) == (777, "USD")
    assert test_db.get(Card, card_id).currency == "USD"
    history = client.get(
        f"/api/boards/{board['id']}/cards/{card_id}/history", headers=auth(tokens["admin"])
    ).json()
    assert set(history[0]["changes"]) == {"deal_value"}


def test_closing_soon_and_aging_report_usd(client, tokens, board, card_id, test_db):
    row = test_db.get(Card, card_id)
    row.currency, row.expected_close_date = "INR", date.today()
    test_db.commit()
    report = client.get(f"/api/boards/{board['id']}/report", headers=auth(tokens["admin"])).json()
    assert {lead["currency"] for lead in report["closing_soon"]} == {"USD"}


# --- non-finite Deal Value is rejected at the schema boundary ---

def _raw_put(client, token, board, card_id, body):
    return client.put(
        f"/api/boards/{board['id']}/cards/{card_id}",
        content=body,
        headers={**auth(token), "Content-Type": "application/json"},
    )


@pytest.mark.parametrize("field", ["deal_value", "deal_value_next_year"])
@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN", "1e999"])
def test_non_finite_deal_values_rejected_and_not_persisted(
    client, tokens, board, card_id, test_db, field, literal
):
    before = client.get(f"/api/boards/{board['id']}", headers=auth(tokens["admin"])).json()
    response = _raw_put(client, tokens["admin"], board, card_id, f'{{"{field}": {literal}}}')
    assert response.status_code == 422
    after = client.get(f"/api/boards/{board['id']}", headers=auth(tokens["admin"])).json()
    assert after == before


@pytest.mark.parametrize("field", ["deal_value", "deal_value_next_year"])
def test_zero_and_ordinary_deal_values_still_accepted(client, tokens, board, card_id, field):
    for value in (0, 1234.5):
        assert update(client, tokens["admin"], board, card_id, {field: value}).json()[field] == value


# ---- Next Action Date and Action (card_bid_details.next_step_due / next_step) ----

def test_action_and_next_action_date_round_trip_apart_from_expected_close(client, tokens, board, card_id, test_db):
    payload = {"next_step": "Call customer", "next_step_due": "2026-10-23", "expected_close_date": "2027-01-31"}
    card = update(client, tokens["admin"], board, card_id, payload).json()
    assert card["bid_details"]["next_step"] == "Call customer"
    assert card["bid_details"]["next_step_due"] == "2026-10-23"
    assert card["expected_close_date"] == "2027-01-31"
    # Editing one leaves the others (and the two dates stay independent).
    card = update(client, tokens["admin"], board, card_id, {"next_step_due": "2026-11-02"}).json()
    assert card["bid_details"]["next_step"] == "Call customer"
    assert card["expected_close_date"] == "2027-01-31"


def test_action_limit_is_100_characters_in_the_backend(client, tokens, board, card_id):
    assert update(client, tokens["admin"], board, card_id, {"next_step": "x" * 100}).status_code == 200
    too_long = update(client, tokens["admin"], board, card_id, {"next_step": "x" * 101})
    assert too_long.status_code == 422
    card = client.get(f"/api/boards/{board['id']}", headers=auth(tokens["admin"])).json()
    stored = next(c for col in card["columns"] for c in col["cards"] if c["id"] == card_id)
    assert stored["bid_details"]["next_step"] == "x" * 100


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_action_is_stored_as_null(client, tokens, board, card_id, blank):
    update(client, tokens["admin"], board, card_id, {"next_step": "Send quote", "next_step_due": "2026-10-23"})
    card = update(client, tokens["admin"], board, card_id, {"next_step": blank, "next_step_due": None}).json()
    assert card["bid_details"]["next_step"] is None
    assert card["bid_details"]["next_step_due"] is None


def test_clearing_on_a_card_without_bid_details_creates_no_row(client, tokens, board, card_id):
    card = update(client, tokens["admin"], board, card_id, {"next_step": None, "next_step_due": None}).json()
    assert card["bid_details"] is None


def test_action_edit_follows_card_permissions_and_member_history(client, tokens, board, card_id, test_db):
    assert update(client, tokens["member"], board, card_id, {"next_step": "Call"}).status_code == 403
    member = test_db.query(User).filter(User.username == "member").one()
    update(client, tokens["admin"], board, card_id, {"salesperson_id": member.id})
    assert update(client, tokens["member"], board, card_id, {"next_step": "Call", "next_step_due": "2026-10-23"}).status_code == 200
    history = client.get(
        f"/api/boards/{board['id']}/cards/{card_id}/history", headers=auth(tokens["admin"])
    ).json()
    assert history[0]["changes"]["next_step"] == {"old": None, "new": "Call"}
    assert history[0]["changes"]["next_step_due"] == {"old": None, "new": "2026-10-23"}


def test_undo_restores_previous_action(client, tokens, board, card_id):
    update(client, tokens["admin"], board, card_id, {"next_step": "Call", "next_step_due": "2026-10-23"})
    update(client, tokens["admin"], board, card_id, {"next_step": "Visit"})
    assert client.post(f"/api/boards/{board['id']}/undo", headers=auth(tokens["admin"])).status_code == 200
    detail = client.get(f"/api/boards/{board['id']}", headers=auth(tokens["admin"])).json()
    card = next(c for col in detail["columns"] for c in col["cards"] if c["id"] == card_id)
    assert card["bid_details"]["next_step"] == "Call"
