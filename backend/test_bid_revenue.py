"""Bid revenue semantics: separate from Deal Value, NULL is not zero, years and currencies never mixed."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from bid_revenue import revenue_for
from reports import build_board_report
from routes import kpi_pipeline_payload


def bid(cur=None, nxt=None, currency="BRL"):
    return NS(revenue_current_year_cents=cur, revenue_next_year_cents=nxt, revenue_currency=currency)


def test_null_is_not_zero_and_not_counted():
    rows = revenue_for([bid(100, None), bid(None, 50), bid(None, None), None, bid(25, 25)])
    assert rows == [{"currency": "BRL", "current_cents": 125, "current_count": 2, "next_cents": 75, "next_count": 2}]


def test_zero_is_a_value_and_is_counted():
    assert revenue_for([bid(0, 0)])[0]["current_count"] == 1


def test_currencies_are_kept_apart_never_converted():
    rows = revenue_for([bid(100, 0, "BRL"), bid(7, 0, "USD")])
    assert [(r["currency"], r["current_cents"]) for r in rows] == [("BRL", 100), ("USD", 7)]


def test_no_details_gives_no_rows():
    assert revenue_for([]) == [] and revenue_for([None]) == []


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        user = models.User(username="a", email="a@example.com", role="admin", password_hash="x")
        session.add(user)
        session.flush()
        board = models.Board(user_id=user.id, title="B")
        session.add(board)
        session.flush()
        cols = {k: models.Column(board_id=board.id, title=k, position=i, stage_kind=k) for i, k in enumerate(("open", "won", "lost"))}
        session.add_all(cols.values())
        session.flush()

        def card(kind, title, deal=None, cur=None, nxt=None, details=True):
            c = models.Card(column_id=cols[kind].id, title=title, position=0, deal_value=deal)
            session.add(c)
            session.flush()
            if details:
                session.add(models.CardBidDetails(card_id=c.id, revenue_current_year_cents=cur, revenue_next_year_cents=nxt, revenue_currency="BRL"))
        card("open", "o1", cur=1000, nxt=2000)
        card("open", "o2", cur=None, nxt=500)
        card("open", "o3", deal=300.0, details=False)  # manual USD value only, no bid
        card("won", "w1", cur=700, nxt=None)
        card("lost", "l1", cur=9999, nxt=9999)  # lost: in its stage, never in open/won
        session.commit()
        yield session, board.id


def test_report_separates_bid_revenue_from_deal_value(db):
    session, board_id = db
    report = build_board_report(session, board_id)
    kpis = report["kpis"]
    assert kpis["open_bid_revenue"] == [{"currency": "BRL", "current_cents": 1000, "current_count": 1, "next_cents": 2500, "next_count": 2}]
    assert kpis["won_bid_revenue"] == [{"currency": "BRL", "current_cents": 700, "current_count": 1, "next_cents": 0, "next_count": 0}]
    assert kpis["open_totals"] == {"USD": 300.0}  # Deal Value untouched: only the manual 300
    stages = {s["title"]: s for s in report["stages"]}
    assert stages["lost"]["bid_revenue"][0]["current_cents"] == 9999
    assert stages["open"]["bid_revenue"][0]["next_cents"] == 2500


def test_report_filters_apply_to_bid_revenue(db):
    session, board_id = db
    assert build_board_report(session, board_id, salesperson_id=999)["kpis"]["open_bid_revenue"] == []


def test_pipeline_stage_carries_bid_revenue(db):
    session, board_id = db
    stages = {s["title"]: s for s in kpi_pipeline_payload(session, board_id)["stages"]}
    assert stages["open"]["bid_revenue"] == [{"currency": "BRL", "current_cents": 1000, "current_count": 1, "next_cents": 2500, "next_count": 2}]
    assert stages["won"]["bid_revenue"][0]["current_cents"] == 700
