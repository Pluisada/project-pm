"""GET /api/companies/{company_slug}/boards/{board_id}: central auth, company-scoped read.

Disposable tmp_path identity and company databases only; no legacy database is touched.
Company databases are seeded with raw SQL so no ORM flush listener runs.
"""
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import company_runtime as cr
import identity_service as svc
from auth import create_access_token, create_user_access_token
from company_routes import build_company_router
from company_runtime import CompanyRuntime
from create_company_db import create_company_database
from database_targets import COMPANY_SLUGS, company_database_path
from identity_database import identity_db_dependency
from identity_models import IdentityCompany, IdentityMembership, IdentityUser
from test_central_deps import GOOD, Env

NOW = "2026-01-01 00:00:00"


def seed_company(path, title):
    con = sqlite3.connect(path)
    try:
        con.execute("INSERT INTO users (id, username, created_at, updated_at) VALUES (7, 'ana', ?, ?)", (NOW, NOW))
        con.execute("INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (1, 7, ?, ?, ?)",
                    (title, NOW, NOW))
        con.execute("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
                    "VALUES (1, 1, 'Lead', 0, 'open', ?, ?)", (NOW, NOW))
        con.execute("INSERT INTO cards (id, column_id, title, position, salesperson_id, created_at, updated_at) "
                    "VALUES (1, 1, ?, 0, 7, ?, ?)", (title + " card", NOW, NOW))
        con.commit()
    finally:
        con.close()


class Rig:
    def __init__(self, e):
        self.e = e
        for slug in COMPANY_SLUGS:
            create_company_database(slug, e.env)
            seed_company(company_database_path(slug, e.env), slug + " board")
        self.runtime = CompanyRuntime(e.env)
        self.opened: list[str] = []
        self.sessions: list = []
        real_session_factory = cr.company_session_factory

        def spying_factory(engine):
            factory = real_session_factory(engine)
            slug = factory.kw["info"]["company_slug"]
            self.opened.append(slug)

            def make():
                session = factory()
                self.sessions.append(session)
                return session
            return make

        self.patch = spying_factory
        app = FastAPI()
        app.include_router(build_company_router(identity_db_dependency(e.factory), self.runtime))
        self.client = TestClient(app)

    def get(self, slug, token=None, board=1, **kw):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.get(f"/api/companies/{slug}/boards/{board}", headers=headers, **kw)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    e = Env(tmp_path)
    r = Rig(e)
    monkeypatch.setattr(cr, "company_session_factory", r.patch)
    yield r
    r.runtime.dispose()
    e.session.close()
    e.engine.dispose()


def test_member_reads_board(rig):
    rig.e.member("m@example.com")
    r = rig.get("lity", rig.e.token("m@example.com"))
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "lity board"
    assert [c["title"] for c in body["columns"]] == ["Lead"]
    card = body["columns"][0]["cards"][0]
    assert (card["title"], card["salesperson_id"], card["salesperson_name"], card["has_member_edits"]) == (
        "lity board card", 7, "ana", False)


def test_response_shape_matches_legacy_board_detail(rig):
    from schemas import BoardDetail
    rig.e.member("m@example.com")
    body = rig.get("lity", rig.e.token("m@example.com")).json()
    assert set(body) == set(BoardDetail.model_fields)
    assert body == BoardDetail.model_validate(body).model_dump(mode="json")


def test_global_admin_reads_every_active_company(rig):
    token = rig.e.token("admin@example.com")
    for slug in COMPANY_SLUGS:
        r = rig.get(slug, token)
        assert r.status_code == 200 and r.json()["title"] == f"{slug} board"


def test_visitor_may_read(rig):
    rig.e.member("v@example.com", role="visitor")
    assert rig.get("lity", rig.e.token("v@example.com")).status_code == 200


def test_missing_board_is_404_and_session_closed(rig):
    rig.e.member("m@example.com")
    assert rig.get("lity", rig.e.token("m@example.com"), board=99).status_code == 404
    assert len(rig.sessions) == 1


def test_company_session_closed_after_request(rig, monkeypatch):
    rig.e.member("m@example.com")
    closed = []
    real = cr.CompanyRuntime.session_for

    def tracking(self, context):
        gen = real(self, context)
        session = next(gen)
        original_close = session.close
        session.close = lambda: (closed.append(True), original_close())[1]
        try:
            yield session
        finally:
            gen.close()

    monkeypatch.setattr(cr.CompanyRuntime, "session_for", tracking)
    assert rig.get("lity", rig.e.token("m@example.com")).status_code == 200
    assert closed == [True]


@pytest.mark.parametrize("auth", ["none", "garbage", "legacy", "legacy_user"])
def test_invalid_and_legacy_tokens_rejected_without_opening_company(rig, auth):
    token = {
        "none": None,
        "garbage": "not-a-jwt",
        "legacy": create_access_token({"sub": "admin"}, timedelta(minutes=5)),
        "legacy_user": create_user_access_token(SimpleNamespace(username="admin", password_hash="x", session_version=0)),
    }[auth]
    r = rig.get("lity", token)
    assert r.status_code == 401
    assert rig.opened == [] and rig.sessions == []


def test_no_membership_is_403_without_opening_company(rig):
    rig.e.member("m@example.com", slug="valor-log")
    token = rig.e.token("m@example.com")
    assert rig.get("lity", token).status_code == 403
    assert rig.opened == []


def test_cross_company_isolation(rig):
    rig.e.member("m@example.com", slug="lity")
    token = rig.e.token("m@example.com")
    assert rig.get("lity", token).json()["title"] == "lity board"
    assert rig.get("valor-log", token).status_code == 403
    assert rig.get("salcomp", token).status_code == 403
    assert rig.opened == ["lity"]


def test_inactive_user_rejected(rig):
    rig.e.member("m@example.com")
    token = rig.e.token("m@example.com")
    rig.e.update(IdentityUser, IdentityUser.email == "m@example.com", is_active=False)
    assert rig.get("lity", token).status_code in (401, 403)
    assert rig.opened == []


def test_inactive_company_rejected_even_for_admin(rig):
    rig.e.member("m@example.com")
    member, admin = rig.e.token("m@example.com"), rig.e.token("admin@example.com")
    rig.e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=False)
    assert rig.get("lity", member).status_code == 403
    assert rig.get("lity", admin).status_code == 403
    assert rig.opened == []


def test_deactivated_membership_rejected(rig):
    rig.e.member("m@example.com")
    token = rig.e.token("m@example.com")
    assert rig.get("lity", token).status_code == 200
    rig.e.update(IdentityMembership, IdentityMembership.id > 0, is_active=False)
    assert rig.get("lity", token).status_code == 403


def test_unknown_slug_is_403(rig):
    assert rig.get("acme", rig.e.token("admin@example.com")).status_code == 403
    assert rig.opened == []


def test_local_company_user_row_grants_nothing(rig):
    # user 7 exists in every company database; without central membership there is no access
    uid = rig.e.member("outsider@example.com", slug="valor-log")
    con = sqlite3.connect(company_database_path("lity", rig.e.env))
    con.execute("INSERT OR REPLACE INTO users (id, username, created_at, updated_at) VALUES (?, 'o', ?, ?)",
                (uid, NOW, NOW))
    con.commit()
    con.close()
    assert rig.get("lity", rig.e.token("outsider@example.com")).status_code == 403


def test_no_fallback_to_database_url(rig, tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{legacy}")
    env = {k: v for k, v in rig.e.env.items() if not k.startswith("PM_COMPANY_DB_")}
    runtime = CompanyRuntime(env)
    app = FastAPI()
    app.include_router(build_company_router(identity_db_dependency(rig.e.factory), runtime))
    r = TestClient(app).get("/api/companies/lity/boards/1",
                            headers={"Authorization": f"Bearer {rig.e.token('admin@example.com')}"})
    assert r.status_code == 503 and r.json()["detail"] == cr.UNAVAILABLE_DETAIL
    assert not legacy.exists()
    assert rig.opened == []


def test_missing_company_database_is_503_and_never_created(rig):
    company_database_path("lity", rig.e.env).unlink()
    assert rig.get("lity", rig.e.token("admin@example.com")).status_code == 503
    assert not company_database_path("lity", rig.e.env).exists()


def test_wrong_schema_version_is_503(rig):
    con = sqlite3.connect(company_database_path("lity", rig.e.env))
    con.execute("PRAGMA user_version = 1")
    con.close()
    assert rig.get("lity", rig.e.token("admin@example.com")).status_code == 503
    assert rig.opened == []


def test_engine_reused_per_company(rig):
    token = rig.e.token("admin@example.com")
    rig.get("lity", token)
    rig.get("lity", token)
    rig.get("salcomp", token)
    assert rig.opened == ["lity", "salcomp"]


def test_route_registered_in_main_app():
    import main
    paths = {r.path for r in main.app.routes}
    assert "/api/companies/{company_slug}/boards/{board_id}" in paths
    assert "/api/boards/{board_id}" in paths
