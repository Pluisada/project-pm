"""GET /api/companies/{company_slug}/boards/{board_id}/cards/{card_id}/history: central auth, read-only.

Disposable tmp_path identity and company databases only. Company databases are seeded with raw SQL.
"""
import hashlib
import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import company_runtime as cr
from auth import create_access_token, create_user_access_token
from company_routes import build_company_router
from company_runtime import CompanyRuntime
from create_company_db import create_company_database
from database_targets import COMPANY_SLUGS, company_database_path
from identity_database import identity_db_dependency
from identity_models import IdentityCompany, IdentityMembership, IdentityUser
from test_central_deps import Env

NOW = "2026-01-01 00:00:00"
CHANGES_OLD = {"title": {"old": "Ana's lead", "new": "Acme Lead"}, "details": {"old": None, "new": "by ana"}}
CHANGES_NEW = {"title": {"old": "Acme Lead", "new": "Acme Lead 2"}}


def seed_company(path, slug):
    con = sqlite3.connect(path)
    try:
        con.execute("INSERT INTO users (id, username, created_at, updated_at) VALUES (7, 'ana', ?, ?)", (NOW, NOW))
        con.execute("INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (1, 7, ?, ?, ?)",
                    (slug + " board", NOW, NOW))
        con.execute("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
                    "VALUES (1, 1, 'Lead', 0, 'open', ?, ?)", (NOW, NOW))
        for cid in (1, 2):
            con.execute("INSERT INTO cards (id, column_id, title, position, created_at, updated_at) "
                        "VALUES (?, 1, ?, ?, ?, ?)", (cid, f"{slug} card {cid}", cid, NOW, NOW))
        # card 1: ana (existing local user), user 99 (no local row), NULL user
        rows = [
            (1, 1, 7, CHANGES_OLD, "2026-02-01 10:00:00"),
            (2, 1, 99, CHANGES_NEW, "2026-02-02 10:00:00"),
            (3, 1, None, CHANGES_NEW, "2026-02-03 10:00:00"),
        ]
        for rid, card, user, changes, at in rows:
            con.execute("INSERT INTO card_edit_history (id, card_id, user_id, changes, created_at) VALUES (?,?,?,?,?)",
                        (rid, card, user, json.dumps(changes), at))
        con.commit()
    finally:
        con.close()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Rig:
    def __init__(self, e):
        self.e = e
        for slug in COMPANY_SLUGS:
            create_company_database(slug, e.env)
            seed_company(company_database_path(slug, e.env), slug)
        self.runtime = CompanyRuntime(e.env)
        self.opened: list[str] = []
        self.sessions: list = []
        real = cr.company_session_factory

        def spying(engine):
            factory = real(engine)
            self.opened.append(factory.kw["info"]["company_slug"])

            def make():
                session = factory()
                self.sessions.append(session)
                return session
            return make

        self.patch = spying
        app = FastAPI()
        app.include_router(build_company_router(identity_db_dependency(e.factory), self.runtime))
        self.client = TestClient(app)

    def get(self, slug, token=None, board=1, card=1):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.get(f"/api/companies/{slug}/boards/{board}/cards/{card}/history", headers=headers)

    def snapshot(self):
        paths = {s: company_database_path(s, self.e.env) for s in COMPANY_SLUGS}
        return {s: digest(p) for s, p in paths.items()} | {"identity": digest(self.e.tmp_path / "identity.db")}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    e = Env(tmp_path)
    r = Rig(e)
    monkeypatch.setattr(cr, "company_session_factory", r.patch)
    yield r
    r.runtime.dispose()
    e.session.close()
    e.engine.dispose()


def test_contract_order_and_missing_users(rig):
    rig.e.member("m@example.com")
    r = rig.get("lity", rig.e.token("m@example.com"))
    assert r.status_code == 200
    body = r.json()
    assert [(x["id"], x["edited_by"]) for x in body] == [(3, "unknown"), (2, "unknown"), (1, "ana")]
    assert all(set(x) == {"id", "edited_by", "changes", "created_at"} for x in body)
    assert body[2]["changes"] == CHANGES_OLD  # exact stored text


def test_historical_text_unchanged_when_user_renamed(rig):
    con = sqlite3.connect(company_database_path("lity", rig.e.env))
    con.execute("UPDATE users SET username = 'renamed' WHERE id = 7")
    con.commit()
    con.close()
    body = rig.get("lity", rig.e.token("admin@example.com")).json()
    assert body[2]["edited_by"] == "renamed"  # label follows the local row, as in the legacy route
    assert body[2]["changes"] == CHANGES_OLD  # stored text is never rewritten


def test_empty_history(rig):
    assert rig.get("lity", rig.e.token("admin@example.com"), card=2).json() == []


@pytest.mark.parametrize("role", ["sales_member", "visitor"])
def test_roles_may_read(rig, role):
    rig.e.member("x@example.com", role=role)
    assert rig.get("lity", rig.e.token("x@example.com")).status_code == 200


def test_global_admin_reads_every_company(rig):
    token = rig.e.token("admin@example.com")
    for slug in COMPANY_SLUGS:
        assert rig.get(slug, token).status_code == 200


def test_missing_board_and_card_are_404(rig):
    token = rig.e.token("admin@example.com")
    for kw in ({"board": 99}, {"card": 99}):
        r = rig.get("lity", token, **kw)
        assert (r.status_code, r.json()["detail"]) == (404, "Card not found")


@pytest.mark.parametrize("auth", ["none", "garbage", "legacy", "legacy_user"])
def test_invalid_and_legacy_tokens_rejected_before_company_db(rig, auth):
    token = {
        "none": None,
        "garbage": "not-a-jwt",
        "legacy": create_access_token({"sub": "admin"}, timedelta(minutes=5)),
        "legacy_user": create_user_access_token(SimpleNamespace(username="admin", password_hash="x", session_version=0)),
    }[auth]
    assert rig.get("lity", token).status_code == 401
    assert rig.opened == [] and rig.sessions == []


def test_non_member_inactive_user_company_and_revoked_membership(rig):
    e = rig.e
    e.member("m@example.com", slug="valor-log")
    assert rig.get("lity", e.token("m@example.com")).status_code == 403
    e.member("a@example.com")
    token = e.token("a@example.com")
    assert rig.get("lity", token).status_code == 200
    n = len(rig.opened)
    e.update(IdentityMembership, IdentityMembership.user_id != e.admin, is_active=False)
    assert rig.get("lity", token).status_code == 403
    e.update(IdentityUser, IdentityUser.email == "a@example.com", is_active=False)
    assert rig.get("lity", token).status_code in (401, 403)
    e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=False)
    assert rig.get("lity", e.token("admin@example.com")).status_code == 403
    assert len(rig.opened) == n


def test_cross_company_isolation(rig):
    con = sqlite3.connect(company_database_path("salcomp", rig.e.env))
    con.execute("UPDATE card_edit_history SET changes = ? WHERE id = 1", (json.dumps({"title": {"old": "S", "new": "S2"}}),))
    con.commit()
    con.close()
    rig.e.member("m@example.com", slug="lity")
    token = rig.e.token("m@example.com")
    assert rig.get("lity", token).json()[2]["changes"] == CHANGES_OLD
    assert rig.get("salcomp", token).status_code == 403
    assert rig.opened == ["lity"]
    assert rig.get("salcomp", rig.e.token("admin@example.com")).json()[2]["changes"]["title"]["new"] == "S2"


def test_legacy_company_parity(rig):
    """The shared helper on a plain session (the legacy route's path) gives the company route's payload."""
    from routes import card_history_payload
    engine = create_engine(f"sqlite:///{company_database_path('lity', rig.e.env)}")
    with sessionmaker(bind=engine)() as db:
        legacy = [x.model_dump(mode="json") for x in card_history_payload(db, 1, 1)]
    engine.dispose()
    assert rig.get("lity", rig.e.token("admin@example.com")).json() == legacy


def test_read_only_no_stub_and_no_writes(rig):
    before = rig.snapshot()
    assert rig.get("lity", rig.e.token("admin@example.com")).status_code == 200
    rig.runtime.dispose()
    assert rig.snapshot() == before
    path = company_database_path("lity", rig.e.env)
    con = sqlite3.connect(path)
    try:
        assert con.execute("SELECT id FROM users WHERE id = 99").fetchall() == []
    finally:
        con.close()
    assert not [p for p in path.parent.iterdir() if p.name.startswith(path.name) and p != path]


def test_no_fallback_to_database_url(rig, tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{legacy}")
    env = {k: v for k, v in rig.e.env.items() if not k.startswith("PM_COMPANY_DB_")}
    app = FastAPI()
    app.include_router(build_company_router(identity_db_dependency(rig.e.factory), CompanyRuntime(env)))
    r = TestClient(app).get("/api/companies/lity/boards/1/cards/1/history",
                            headers={"Authorization": f"Bearer {rig.e.token('admin@example.com')}"})
    assert r.status_code == 503
    assert not legacy.exists()


def test_session_closed_after_request(rig, monkeypatch):
    closed = []
    real = cr.CompanyRuntime.session_for

    def tracking(self, context):
        gen = real(self, context)
        session = next(gen)
        original = session.close
        session.close = lambda: (closed.append(True), original())[1]
        try:
            yield session
        finally:
            gen.close()

    monkeypatch.setattr(cr.CompanyRuntime, "session_for", tracking)
    assert rig.get("lity", rig.e.token("admin@example.com")).status_code == 200
    assert closed == [True]


def test_frontend_contract_matches_cardedithistoryentry_type():
    from schemas import CardEditHistoryEntry
    assert list(CardEditHistoryEntry.model_fields) == ["id", "edited_by", "changes", "created_at"]
    assert CardEditHistoryEntry.model_fields["edited_by"].annotation is str  # api.ts: edited_by: string


def test_routes_registered_in_main_app():
    import main
    paths = set(main.app.openapi()["paths"])
    assert "/api/companies/{company_slug}/boards/{board_id}/cards/{card_id}/history" in paths
    assert "/api/boards/{board_id}/cards/{card_id}/history" in paths
