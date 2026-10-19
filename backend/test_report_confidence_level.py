"""Stage 4: confidence_level filter and by_confidence_level grouping on the board report.

Company databases in tmp_path only (the Phase 2E rig); the legacy route runs on a copy in tmp_path. Synthetic data.
Board 1 of "lity" (see test_company_report.add_leads): card 1 ana/open/no value, 2 bob8/open/10, 3 bob9/open/20,
4 unassigned/open/30, 5 dangling user/open/40, 6 bob8/won/50. Levels are set below.
"""
import shutil
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database_targets import company_database_path
from test_company_report import digest, groups, lity, report  # noqa: F401  (fixtures/helpers)
from test_company_routes import rig  # noqa: F401,F811

LEVELS = {1: None, 2: "LOW", 3: "LOW", 4: "HIGH", 5: "MEDIUM", 6: "HIGH"}
ORDER = ["LOW", "MEDIUM", "HIGH", "Not assigned"]


def sql(rig, query, args=(), slug="lity"):
    con = sqlite3.connect(company_database_path(slug, rig.e.env))
    try:
        con.execute(query, args)
        con.commit()
    finally:
        con.close()


def set_levels(rig, levels=LEVELS, slug="lity"):
    for card_id, value in levels.items():
        sql(rig, "UPDATE cards SET confidence_level = ? WHERE id = ?", (value, card_id), slug)


@pytest.fixture
def leveled(lity):  # noqa: F811
    set_levels(lity)
    return lity


def admin_token(rig):
    return rig.e.token("admin@example.com")


def rows(body):
    return [(g["key"], g["open_count"], g["won_count"], g["open_totals"], g["won_totals"]) for g in body["by_confidence_level"]]


# --- grouping --------------------------------------------------------------------------------------------------

def test_four_rows_in_fixed_order_with_counts_and_usd_totals(leveled):
    body = report(leveled, "lity", admin_token(leveled)).json()
    assert rows(body) == [
        ("LOW", 2, 0, {"USD": 30}, {}),
        ("MEDIUM", 1, 0, {"USD": 40}, {}),
        ("HIGH", 1, 1, {"USD": 30}, {"USD": 50}),
        ("Not assigned", 1, 0, {}, {}),  # card 1 has no Deal Value: counted, but adds nothing (not zero)
    ]
    assert all(g["salesperson_id"] is None for g in body["by_confidence_level"])


def test_every_card_is_in_exactly_one_row_and_totals_match_the_kpis(leveled):
    body = report(leveled, "lity", admin_token(leveled)).json()
    assert sum(g["open_count"] for g in body["by_confidence_level"]) == body["kpis"]["open_count"] == 5
    assert sum(g["won_count"] for g in body["by_confidence_level"]) == body["kpis"]["won_count"] == 1
    for measure in ("totals", "next_totals", "sum_totals"):
        total = sum(v for g in body["by_confidence_level"] for v in g[f"open_{measure}"].values())
        assert total == sum(body["kpis"][f"open_{measure}"].values())


def test_empty_groups_are_still_returned(lity):
    body = report(lity, "lity", admin_token(lity)).json()  # nothing assigned a level
    assert rows(body) == [("LOW", 0, 0, {}, {}), ("MEDIUM", 0, 0, {}, {}), ("HIGH", 0, 0, {}, {}),
                          ("Not assigned", 5, 1, {"USD": 100}, {"USD": 50})]


def test_lost_cards_are_left_out_like_the_other_breakdowns(leveled):
    sql(leveled, "INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
                "VALUES (3, 1, 'Lost', 2, 'lost', '2026-01-01', '2026-01-01')")
    sql(leveled, "UPDATE cards SET column_id = 3 WHERE id = 2")
    body = report(leveled, "lity", admin_token(leveled)).json()
    assert body["kpis"]["lost_count"] == 1 and groups(body, "by_confidence_level")["LOW"]["open_count"] == 1


def test_a_missing_deal_value_is_not_counted_as_zero_in_the_current_year_total(leveled):
    sql(leveled, "UPDATE cards SET deal_value = NULL WHERE id = 4")
    body = report(leveled, "lity", admin_token(leveled)).json()
    high = groups(body, "by_confidence_level")["HIGH"]
    assert high["open_count"] == 1 and high["open_totals"] == {}


# --- filter ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value, open_count, won_count", [
    ("LOW", 2, 0), ("MEDIUM", 1, 0), ("HIGH", 1, 1), ("UNASSIGNED", 1, 0)])
def test_each_level_and_the_unassigned_selection(leveled, value, open_count, won_count):
    body = report(leveled, "lity", admin_token(leveled), confidence_level=value).json()
    assert (body["kpis"]["open_count"], body["kpis"]["won_count"]) == (open_count, won_count)
    assert sum(s["count"] for s in body["stages"]) == open_count + won_count  # stages follow the filtered set
    shown = [g for g in body["by_confidence_level"] if g["open_count"] or g["won_count"]]
    assert len(shown) == 1 and shown[0]["key"] == ("Not assigned" if value == "UNASSIGNED" else value)
    assert [g["key"] for g in body["by_confidence_level"]] == ORDER  # all four rows stay, no double counting


@pytest.mark.parametrize("value", ["low", "High", "HIGH ", "", "NULL", "null", "none", "Not assigned", "0", "VERY HIGH"])
def test_invalid_filter_values_are_422_and_change_nothing(leveled, value):
    before = digest(company_database_path("lity", leveled.e.env))
    assert report(leveled, "lity", admin_token(leveled), confidence_level=value).status_code == 422
    assert digest(company_database_path("lity", leveled.e.env)) == before


def test_filter_combines_with_the_existing_filters(leveled):
    token = admin_token(leveled)
    both = report(leveled, "lity", token, confidence_level="LOW", salesperson_id=8).json()
    assert both["kpis"]["open_count"] == 1 and both["kpis"]["open_totals"] == {"USD": 10}
    unassigned_person = report(leveled, "lity", token, confidence_level="HIGH", salesperson_id=8).json()
    assert (unassigned_person["kpis"]["open_count"], unassigned_person["kpis"]["won_count"]) == (0, 1)
    none = report(leveled, "lity", token, confidence_level="MEDIUM", salesperson_id=8).json()
    assert none["kpis"]["open_count"] == none["kpis"]["won_count"] == 0
    sql(leveled, "UPDATE cards SET product_line = 'Chargers' WHERE id IN (2, 4)")
    product = report(leveled, "lity", token, confidence_level="LOW", product_line="Chargers").json()
    assert product["kpis"]["open_count"] == 1 and product["kpis"]["open_totals"] == {"USD": 10}


def test_existing_filters_and_totals_are_unchanged_without_the_new_parameter(leveled):
    token = admin_token(leveled)
    plain = report(leveled, "lity", token).json()
    assert plain["kpis"]["open_count"] == 5 and plain["kpis"]["open_totals"] == {"USD": 100}
    assert report(leveled, "lity", token, salesperson_id=8).json()["kpis"]["won_count"] == 1
    assert report(leveled, "lity", token, salesperson_id=99).json()["kpis"]["open_count"] == 1
    for key in ("by_salesperson", "by_product_line", "by_source"):
        assert key in plain


# --- companies, isolation, access --------------------------------------------------------------------------------

def test_same_semantics_in_all_three_companies_and_no_leakage(rig):  # noqa: F811
    for slug, level in (("lity", "LOW"), ("valor-log", "MEDIUM"), ("salcomp", "HIGH")):
        set_levels(rig, {1: level}, slug)
    token = admin_token(rig)
    for slug, level in (("lity", "LOW"), ("valor-log", "MEDIUM"), ("salcomp", "HIGH")):
        body = report(rig, slug, token).json()
        assert [(g["key"], g["open_count"]) for g in body["by_confidence_level"]] == [
            (k, 1 if k == level else 0) for k in ORDER]
        for other in ("LOW", "MEDIUM", "HIGH", "UNASSIGNED"):
            filtered = report(rig, slug, token, confidence_level=other).json()["kpis"]["open_count"]
            assert filtered == (1 if other == level else 0)


def test_other_company_members_are_refused_before_filtering(leveled):
    leveled.e.member("o@example.com", slug="valor-log")
    other = leveled.e.token("o@example.com")
    leveled.opened.clear()
    assert report(leveled, "lity", other, confidence_level="LOW").status_code == 403
    assert leveled.opened == []
    assert report(leveled, "lity", None, confidence_level="LOW").status_code == 401


def test_member_and_visitor_may_read_the_filtered_report(leveled):
    leveled.e.member("m@example.com")
    leveled.e.member("v@example.com", role="visitor")
    for email in ("m@example.com", "v@example.com"):
        response = report(leveled, "lity", leveled.e.token(email), confidence_level="LOW")
        assert response.status_code == 200 and response.json()["kpis"]["open_count"] == 2


def test_the_report_stays_read_only(leveled):
    path = company_database_path("lity", leveled.e.env)
    before = digest(path)
    report(leveled, "lity", admin_token(leveled), confidence_level="HIGH")
    assert digest(path) == before


def test_legacy_route_matches_the_company_route(leveled, tmp_path):
    import main
    from database import get_db
    from deps import get_current_user

    copy = tmp_path / "legacy-copy.db"
    shutil.copyfile(company_database_path("lity", leveled.e.env), copy)
    engine = create_engine(f"sqlite:///{copy}")
    session = sessionmaker(bind=engine)()
    main.app.dependency_overrides[get_db] = lambda: (yield session)
    main.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role="admin")
    try:
        legacy = TestClient(main.app)
        for params in ({}, {"confidence_level": "LOW"}, {"confidence_level": "UNASSIGNED"},
                       {"confidence_level": "HIGH", "salesperson_id": 8}):
            old = legacy.get("/api/boards/1/report", params=params)
            assert old.status_code == 200 and old.json() == report(leveled, "lity", admin_token(leveled), **params).json()
        assert legacy.get("/api/boards/1/report", params={"confidence_level": "low"}).status_code == 422
    finally:
        main.app.dependency_overrides.pop(get_db, None)
        main.app.dependency_overrides.pop(get_current_user, None)
        session.close()
        engine.dispose()
