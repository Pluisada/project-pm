"""GET /api/companies/{company_slug}/boards/{board_id}/report: central auth, company-scoped read.

Disposable tmp_path identity and company databases only. Company databases are seeded with raw
SQL (foreign keys off, so a card can point at a user id that has no local row).
"""
import hashlib
import shutil
import sqlite3
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from auth import create_access_token
from database_targets import COMPANY_SLUGS, company_database_path
from identity_models import IdentityCompany, IdentityMembership, IdentityUser
from reports import FORMER_USER_LABEL
from test_company_routes import NOW, rig  # noqa: F401  (rig is a fixture)

SOON = (date.today() + timedelta(days=5)).isoformat()


def add_leads(path):
    """Board 1 of the company database: ana (7, seeded) plus two 'bob's, an unassigned card and a dangling id."""
    con = sqlite3.connect(path)
    try:
        for uid in (8, 9):
            con.execute("INSERT INTO users (id, username, created_at, updated_at) VALUES (?, 'bob', ?, ?)",
                        (uid, NOW, NOW))
        con.execute("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
                    "VALUES (2, 1, 'Won', 1, 'won', ?, ?)", (NOW, NOW))
        rows = [  # id, column, salesperson, deal_value
            (2, 1, 8, 10), (3, 1, 9, 20), (4, 1, None, 30), (5, 1, 99, 40), (6, 2, 8, 50),
        ]
        for card_id, column, person, value in rows:
            con.execute(
                "INSERT INTO cards (id, column_id, title, position, salesperson_id, deal_value, "
                "expected_close_date, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (card_id, column, f"card {card_id}", card_id, person, value, SOON, NOW, NOW))
        con.commit()
    finally:
        con.close()


@pytest.fixture
def lity(rig):  # noqa: F811
    add_leads(company_database_path("lity", rig.e.env))
    return rig


def report(rig, slug, token=None, board=1, **params):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return rig.client.get(f"/api/companies/{slug}/boards/{board}/report", headers=headers, params=params)


def groups(body, section="by_salesperson"):
    return {g["key"]: g for g in body[section]}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- access ------------------------------------------------------------------

def test_admin_member_and_visitor_read(lity):
    lity.e.member("m@example.com")
    lity.e.member("v@example.com", role="visitor")
    for email in ("admin@example.com", "m@example.com", "v@example.com"):
        r = report(lity, "lity", lity.e.token(email))
        assert r.status_code == 200
        assert [s["title"] for s in r.json()["stages"]] == ["Lead", "Won"]


@pytest.mark.parametrize("auth", ["none", "garbage", "legacy"])
def test_bad_tokens_rejected_before_company_is_opened(lity, auth):
    token = {"none": None, "garbage": "x", "legacy": create_access_token({"sub": "admin"}, timedelta(minutes=5))}[auth]
    assert report(lity, "lity", token).status_code == 401
    assert lity.opened == [] and lity.sessions == []


def test_non_member_inactive_user_and_inactive_company_rejected(lity):
    lity.e.member("m@example.com", slug="valor-log")
    lity.e.member("n@example.com")
    outsider, soon_inactive = lity.e.token("m@example.com"), lity.e.token("n@example.com")
    assert report(lity, "lity", outsider).status_code == 403
    lity.e.update(IdentityUser, IdentityUser.email == "n@example.com", is_active=False)
    assert report(lity, "lity", soon_inactive).status_code in (401, 403)
    lity.e.update(IdentityCompany, IdentityCompany.slug == "salcomp", is_active=False)
    assert report(lity, "salcomp", lity.e.token("admin@example.com")).status_code == 403
    lity.e.member("r@example.com")
    revoked = lity.e.token("r@example.com")
    assert report(lity, "lity", revoked).status_code == 200
    lity.e.update(IdentityMembership, IdentityMembership.id > 0, is_active=False)
    assert report(lity, "lity", revoked).status_code == 403
    assert lity.opened == ["lity"]


def test_cross_company_isolation(lity):
    lity.e.member("m@example.com")
    token = lity.e.token("m@example.com")
    assert report(lity, "valor-log", token).status_code == 403
    assert report(lity, "salcomp", token).status_code == 403
    # the same board id in another company holds other data
    admin = lity.e.token("admin@example.com")
    assert report(lity, "lity", admin).json()["kpis"]["open_count"] == 5
    assert report(lity, "salcomp", admin).json()["kpis"]["open_count"] == 1
    assert lity.opened == ["lity", "salcomp"]


# --- content -------------------------------------------------------------------

def test_parity_with_legacy_route(lity, tmp_path):
    import main
    from database import get_db
    from deps import get_current_user

    legacy_copy = tmp_path / "legacy-copy.db"
    shutil.copyfile(company_database_path("lity", lity.e.env), legacy_copy)
    engine = create_engine(f"sqlite:///{legacy_copy}")
    session = sessionmaker(bind=engine)()
    main.app.dependency_overrides[get_db] = lambda: (yield session)
    main.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role="admin")
    try:
        legacy = TestClient(main.app)
        admin = lity.e.token("admin@example.com")
        for params in ({}, {"salesperson_id": 8}, {"salesperson_id": 99}, {"product_line": "Chargers"},
                       {"salesperson_id": 7, "product_line": "Chargers"}):
            old = legacy.get("/api/boards/1/report", params=params)
            new = report(lity, "lity", admin, **params)
            assert old.status_code == new.status_code == 200
            assert old.json() == new.json()
    finally:
        main.app.dependency_overrides.pop(get_db, None)
        main.app.dependency_overrides.pop(get_current_user, None)
        session.close()
        engine.dispose()


def test_salesperson_filter_is_by_id(lity):
    token = lity.e.token("admin@example.com")
    counts = {sid: report(lity, "lity", token, salesperson_id=sid).json()["kpis"] for sid in (7, 8, 9, 99, 12345)}
    assert [(k["open_count"], k["won_count"]) for k in map(counts.get, (7, 8, 9, 99, 12345))] == [
        (1, 0), (1, 1), (1, 0), (1, 0), (0, 0)]  # 8 and 9 share the label 'bob' but filter apart


def test_missing_local_user_is_not_unassigned(lity):
    body = report(lity, "lity", lity.e.token("admin@example.com")).json()
    by_person = groups(body)
    assert by_person["Unassigned"]["open_count"] == 1  # card 4 only
    assert by_person[FORMER_USER_LABEL]["open_count"] == 1  # card 5, id 99 has no local row
    assert by_person["ana"]["open_count"] == 1
    soon = {lead["card_id"]: lead["salesperson_name"] for lead in body["closing_soon"]}
    assert soon[4] is None and soon[5] == FORMER_USER_LABEL and soon[2] == "bob"


def test_equal_labels_with_different_ids_stay_separate(lity):
    body = report(lity, "lity", lity.e.token("admin@example.com")).json()
    rows = {(g["key"], g["salesperson_id"]): g for g in body["by_salesperson"]}
    assert (rows[("bob", 8)]["open_count"], rows[("bob", 8)]["won_count"]) == (1, 1)
    assert (rows[("bob", 9)]["open_count"], rows[("bob", 9)]["won_count"]) == (1, 0)
    assert rows[("Unassigned", None)]["open_count"] == 1
    assert rows[(FORMER_USER_LABEL, 99)]["open_count"] == 1
    assert [g["key"] for g in body["by_salesperson"]].count("bob") == 2
    assert all(g["salesperson_id"] is None for g in body["by_product_line"])
    for sid, other in ((8, 9), (9, 8)):
        filtered = report(lity, "lity", lity.e.token("admin@example.com"), salesperson_id=sid).json()
        assert [(g["key"], g["salesperson_id"]) for g in filtered["by_salesperson"]] == [("bob", sid)]
        assert {c["card_id"] for c in filtered["closing_soon"]} == ({2} if sid == 8 else {3})


def test_missing_board_and_invalid_filters(lity):
    token = lity.e.token("admin@example.com")
    assert report(lity, "lity", token, board=99).status_code == 404
    assert report(lity, "lity", token, salesperson_id="abc").status_code == 422
    assert report(lity, "lity", token, product_line="Nothing").json()["kpis"]["open_count"] == 0


# --- side effects ----------------------------------------------------------------

def test_requests_leave_company_files_byte_identical(lity):
    token = lity.e.token("admin@example.com")
    paths = [company_database_path(s, lity.e.env) for s in COMPANY_SLUGS]
    before = [digest(p) for p in paths]
    for slug in COMPANY_SLUGS:
        assert report(lity, slug, token).status_code == 200
        assert report(lity, slug, token, salesperson_id=99).status_code == 200
    assert [digest(p) for p in paths] == before
    con = sqlite3.connect(company_database_path("lity", lity.e.env))
    try:
        assert con.execute("SELECT count(*) FROM users").fetchone()[0] == 3  # no stub for 99
    finally:
        con.close()


def test_session_closed_per_request(lity):
    token = lity.e.token("admin@example.com")
    report(lity, "lity", token)
    report(lity, "lity", token, board=99)
    assert len(lity.sessions) == 2
    assert all(s.get_bind().pool.checkedout() == 0 for s in lity.sessions)


def test_no_database_url_fallback(lity, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from company_routes import build_company_router
    from company_runtime import CompanyRuntime
    from identity_database import identity_db_dependency

    legacy = tmp_path / "legacy.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{legacy}")
    env = {k: v for k, v in lity.e.env.items() if not k.startswith("PM_COMPANY_DB_")}
    app = FastAPI()
    app.include_router(build_company_router(identity_db_dependency(lity.e.factory), CompanyRuntime(env)))
    r = TestClient(app).get("/api/companies/lity/boards/1/report",
                            headers={"Authorization": f"Bearer {lity.e.token('admin@example.com')}"})
    assert r.status_code == 503 and not legacy.exists()


def test_route_registered_in_main_app():
    import main
    paths = set(main.app.openapi()["paths"])
    assert "/api/companies/{company_slug}/boards/{board_id}/report" in paths
    assert "/api/boards/{board_id}/report" in paths
