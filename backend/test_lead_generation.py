"""Lead Generation Pareto (reports.build_lead_generation and GET .../kpis/lead-generation).

Engine tests use an in-memory company schema with plain inserts so each test controls the exact timestamps.
Route tests use the company rig (disposable databases in tmp_path). Synthetic data only.
"""
import json
import sqlite3
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database_targets import company_database_path
from models import AuditEvent, Board, Card, Column, CompanyBase, CompanyUser
from reports import FORMER_USER_LABEL, build_lead_generation
from test_company_report import lity  # noqa: F401  (fixture)
from test_company_routes import NOW, rig  # noqa: F401,F811

NOW_UTC = datetime(2026, 10, 24, 12, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    CompanyBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    for uid, name in ((1, "ana"), (2, "bob"), (3, "carol")):
        session.add(CompanyUser(id=uid, username=name))
    session.add(Board(id=1, user_id=1, title="B"))
    session.add(Column(id=1, board_id=1, title="Lead", position=0, stage_kind="open"))
    session.commit()
    yield session
    session.close()


def add(db, card_id, created, creator=1, owner=None, board_id=1, **fields):
    """A card created at `created` by `creator` (None: no create event) and currently owned by `owner`."""
    db.execute(Card.__table__.insert().values(
        id=card_id, column_id=1, title=f"c{card_id}", position=card_id, salesperson_id=owner,
        created_at=created, updated_at=created, **fields))
    if creator is not False:
        db.add(AuditEvent(board_id=board_id, card_id=card_id, actor_user_id=creator, action="create",
                          details=json.dumps({"title": f"c{card_id}"}), created_at=created))
    db.commit()


def run(db, **kw):
    return build_lead_generation(db, 1, now=kw.pop("now", NOW_UTC))


def names(result):
    return [(r["name"], r["count"]) for r in result["rows"]]


def test_counts_only_the_current_calendar_year_up_to_now(db):
    add(db, 1, datetime(2026, 1, 1, 0, 0, 0))          # first instant of the year: in
    add(db, 2, datetime(2025, 12, 31, 23, 59, 59, 999999))  # last instant of last year: out
    add(db, 3, NOW_UTC)                                 # exactly now: in
    add(db, 4, datetime(2026, 10, 24, 12, 0, 1))        # a second in the future: out
    add(db, 5, datetime(2027, 1, 1, 0, 0, 0))           # next year's boundary: out
    result = run(db)
    assert result["total"] == 2
    assert result["period_start"].isoformat() == "2026-01-01"
    assert result["period_end"].isoformat() == "2026-10-24"


def test_the_year_is_derived_from_now_not_hardcoded(db):
    add(db, 1, datetime(2026, 5, 1))
    add(db, 2, datetime(2027, 2, 1), creator=2)
    assert names(run(db, now=datetime(2027, 3, 1))) == [("bob", 1)]
    assert names(run(db, now=datetime(2026, 12, 31, 23, 59, 59))) == [("ana", 1)]


def test_credit_goes_to_the_creator_not_the_current_owner(db):
    add(db, 1, datetime(2026, 3, 1), creator=1, owner=2)  # ana created it, now bob's
    add(db, 2, datetime(2026, 3, 2), creator=1, owner=3)
    add(db, 3, datetime(2026, 3, 3), creator=2, owner=None)
    assert names(run(db)) == [("ana", 2), ("bob", 1)]


def test_earliest_create_event_wins(db):
    add(db, 1, datetime(2026, 3, 1), creator=1)
    db.add(AuditEvent(board_id=1, card_id=1, actor_user_id=2, action="create", details="{}", created_at=datetime(2026, 3, 2)))
    db.commit()
    assert names(run(db)) == [("ana", 1)]


def test_ranking_percentages_and_cumulative_are_correct(db):
    for i in range(1, 4):
        add(db, i, datetime(2026, 2, i), creator=2)
    add(db, 4, datetime(2026, 2, 4), creator=1)
    add(db, 5, datetime(2026, 2, 5), creator=3)
    add(db, 6, datetime(2026, 2, 6), creator=3)
    result = run(db)
    assert names(result) == [("bob", 3), ("carol", 2), ("ana", 1)]
    assert [r["share"] for r in result["rows"]] == [3 / 6, 2 / 6, 1 / 6]
    assert [r["cumulative_share"] for r in result["rows"]] == [3 / 6, 5 / 6, 1.0]
    assert sum(r["share"] for r in result["rows"]) == pytest.approx(1)


def test_ties_are_ordered_by_name_then_id(db):
    db.add(CompanyUser(id=4, username="Bob"))  # same name as id 2, differing in case
    db.commit()
    add(db, 1, datetime(2026, 2, 1), creator=3)
    add(db, 2, datetime(2026, 2, 2), creator=2)
    add(db, 3, datetime(2026, 2, 3), creator=1)
    add(db, 4, datetime(2026, 2, 4), creator=4)
    assert [(r["name"], r["salesperson_id"]) for r in run(db)["rows"]] == [("ana", 1), ("bob", 2), ("Bob", 4), ("carol", 3)]


def test_empty_board_and_no_attributed_leads_do_not_divide_by_zero(db):
    assert run(db) == {"period_start": datetime(2026, 1, 1).date(), "period_end": NOW_UTC.date(),
                       "total": 0, "unattributed_count": 0, "rows": []}
    add(db, 1, datetime(2026, 2, 1), creator=False)
    add(db, 2, datetime(2026, 2, 2), creator=None)
    result = run(db)
    assert (result["total"], result["unattributed_count"], result["rows"]) == (0, 2, [])


def test_cards_without_a_creator_are_counted_apart_never_credited_to_the_owner(db):
    add(db, 1, datetime(2026, 2, 1), creator=False, owner=1)
    add(db, 2, datetime(2026, 2, 2), creator=2, owner=1)
    result = run(db)
    assert names(result) == [("bob", 1)] and result["unattributed_count"] == 1 and result["total"] == 1


def test_deleted_cards_and_other_boards_are_left_out(db):
    add(db, 1, datetime(2026, 2, 1), creator=1)
    db.add(AuditEvent(board_id=1, card_id=99, actor_user_id=2, action="create", details="{}", created_at=datetime(2026, 2, 1)))
    db.add(AuditEvent(board_id=2, card_id=1, actor_user_id=3, action="create", details="{}", created_at=datetime(2026, 2, 1)))
    db.commit()  # card 99 no longer exists; the board-2 event is not this board's
    assert names(run(db)) == [("ana", 1)]


def test_unknown_creator_is_labelled_not_dropped(db):
    add(db, 1, datetime(2026, 2, 1), creator=77)
    assert names(run(db)) == [(FORMER_USER_LABEL, 1)]


def test_query_count_does_not_grow_with_the_number_of_leads(db):
    statements = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda *a: statements.append(a[2]))
    add(db, 1, datetime(2026, 2, 1))
    statements.clear()
    run(db)
    few = len(statements)
    for i in range(2, 30):
        add(db, i, datetime(2026, 2, 1), creator=1 + i % 3)
    statements.clear()
    run(db)
    assert len(statements) == few


# --- route: company scoping and authorization ----------------------------------------------------------------

def pareto(rig, slug, token=None, board=1):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return rig.client.get(f"/api/companies/{slug}/boards/{board}/kpis/lead-generation", headers=headers)


def sql(rig, query, args=(), slug="lity"):
    con = sqlite3.connect(company_database_path(slug, rig.e.env))
    try:
        con.execute(query, args)
        con.commit()
    finally:
        con.close()


def stamp(rig, slug="lity"):
    """Card 2..6 created this year; ana (7) created 2 and 3 (owned by bob), card 4 has no create event."""
    year = datetime.now().year
    sql(rig, "UPDATE cards SET created_at = ?", (f"{year}-01-15 10:00:00",), slug)
    for card_id, actor in ((2, 7), (3, 7), (5, 8)):
        sql(rig, "INSERT INTO audit_events (board_id, card_id, actor_user_id, action, details, created_at) "
                 "VALUES (1, ?, ?, 'create', '{}', ?)", (card_id, actor, f"{year}-01-15 10:00:00"), slug)


def test_route_returns_the_pareto_for_every_role_and_only_for_that_company(lity):
    stamp(lity)
    lity.e.member("m@example.com")
    lity.e.member("v@example.com", role="visitor")
    for email in ("admin@example.com", "m@example.com", "v@example.com"):
        body = pareto(lity, "lity", lity.e.token(email)).json()
        assert [(r["name"], r["count"]) for r in body["rows"]] == [("ana", 2), ("bob", 1)]
        assert body["total"] == 3 and body["unattributed_count"] == 3  # cards 1, 4 and 6 have no create event
        assert body["rows"][-1]["cumulative_share"] == 1.0


def test_route_isolation_and_authentication(lity):
    stamp(lity)
    token = lity.e.token("admin@example.com")
    other = pareto(lity, "valor-log", token)
    assert other.status_code == 200 and other.json()["rows"] == []  # nothing of lity's leaks into another company
    assert pareto(lity, "lity").status_code in (401, 403)


def test_board_report_no_longer_carries_the_pareto(lity):
    from test_company_report import report
    assert "lead_generation" not in report(lity, "lity", lity.e.token("admin@example.com")).json()
