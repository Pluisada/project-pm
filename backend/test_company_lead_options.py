"""GET /api/companies/{company_slug}/lead-options: central eligibility, local labels, no writes.

Disposable tmp_path identity and company databases only. The company databases are seeded with raw SQL.
"""
import hashlib
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import company_runtime as cr
import identity_service as svc
from company_routes import build_company_router
from company_runtime import CompanyRuntime
from create_company_db import create_company_database
from database_targets import COMPANY_SLUGS, company_database_path
from identity_database import identity_db_dependency
from identity_models import IdentityMembership, IdentityUser
from lead_options import CURRENCIES, LEAD_SOURCES, PRODUCT_LINES
from test_central_deps import Env

NOW = "2026-01-01 00:00:00"


def add_local(slug, env, user_id, username):
    con = sqlite3.connect(company_database_path(slug, env))
    try:
        con.execute("INSERT INTO users (id, username, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (user_id, username, NOW, NOW))
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
        self.runtime = CompanyRuntime(e.env)
        self.opened: list[str] = []
        real = cr.company_session_factory

        def spying(engine):
            factory = real(engine)
            self.opened.append(factory.kw["info"]["company_slug"])
            return factory

        self.spy = spying
        app = FastAPI()
        app.include_router(build_company_router(identity_db_dependency(e.factory), self.runtime))
        self.client = TestClient(app)

    def get(self, slug, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.get(f"/api/companies/{slug}/lead-options", headers=headers)

    def people(self, slug, token):
        r = self.get(slug, token)
        assert r.status_code == 200, r.text
        return [(p["id"], p["username"]) for p in r.json()["salespeople"]]

    def paths(self):
        return {s: company_database_path(s, self.e.env) for s in COMPANY_SLUGS}

    def snapshot(self):
        return {s: digest(p) for s, p in self.paths().items()} | {"identity": digest(self.e.tmp_path / "identity.db")}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    e = Env(tmp_path)
    r = Rig(e)
    monkeypatch.setattr(cr, "company_session_factory", r.spy)
    yield r
    r.runtime.dispose()
    e.session.close()
    e.engine.dispose()


def test_eligibility_admin_member_visitor(rig):
    e = rig.e
    m = e.member("m@example.com")
    v = e.member("v@example.com", role="visitor")
    for uid, name in ((e.admin, "root"), (m, "mia"), (v, "vic")):
        add_local("lity", e.env, uid, name)
    for who in ("admin@example.com", "m@example.com", "v@example.com"):  # every role may read
        assert rig.people("lity", e.token(who)) == [(m, "mia"), (e.admin, "root")]


def test_response_matches_legacy_shape_and_frontend_consumer(rig):
    e = rig.e
    m = e.member("m@example.com")
    add_local("lity", e.env, m, "mia")
    body = rig.get("lity", e.token("m@example.com")).json()
    assert set(body) == {"currencies", "lead_sources", "product_lines", "salespeople"}  # LeadOptions in api.ts
    assert (body["currencies"], body["lead_sources"], body["product_lines"]) == (CURRENCIES, LEAD_SOURCES, PRODUCT_LINES)
    assert body["salespeople"] == [{"id": m, "username": "mia"}]


def test_inactive_user_and_revoked_membership_excluded(rig):
    e = rig.e
    a, b, c = (e.member(f"{n}@example.com") for n in "abc")
    for uid, name in ((a, "amy"), (b, "ben"), (c, "cat")):
        add_local("lity", e.env, uid, name)
    token = e.token("a@example.com")
    e.update(IdentityUser, IdentityUser.id == b, is_active=False)
    e.update(IdentityMembership, IdentityMembership.user_id == c, is_active=False)
    assert rig.people("lity", token) == [(a, "amy")]


def test_member_without_local_row_is_omitted_and_no_stub_is_created(rig):
    e = rig.e
    m = e.member("m@example.com")
    n = e.member("n@example.com")
    add_local("lity", e.env, m, "mia")
    before = rig.snapshot()
    assert rig.people("lity", e.token("m@example.com")) == [(m, "mia")]
    assert rig.snapshot() == before
    con = sqlite3.connect(company_database_path("lity", e.env))
    try:
        assert con.execute("SELECT id FROM users WHERE id = ?", (n,)).fetchall() == []
    finally:
        con.close()


def test_local_stub_without_active_membership_is_not_eligible(rig):
    e = rig.e
    m = e.member("m@example.com")
    other = e.member("o@example.com", slug="salcomp")  # member elsewhere only
    add_local("lity", e.env, m, "mia")
    add_local("lity", e.env, other, "olga")
    add_local("lity", e.env, 999, "ghost")  # no central user at all
    assert rig.people("lity", e.token("m@example.com")) == [(m, "mia")]


def test_labels_ordered_by_label_then_id_and_equal_labels_kept_apart(rig):
    e = rig.e
    ids = [e.member(f"{n}@example.com") for n in "abc"]
    for uid, name in zip(ids, ("zed", "bob", "bob")):
        add_local("lity", e.env, uid, name)
    token = e.token("a@example.com")
    assert rig.people("lity", token) == [(ids[1], "bob"), (ids[2], "bob"), (ids[0], "zed")]
    assert rig.people("lity", token) == rig.people("lity", token)


def test_cross_company_isolation(rig):
    e = rig.e
    m = e.member("m@example.com", slug="lity")
    o = e.member("o@example.com", slug="salcomp")
    add_local("lity", e.env, m, "mia")
    add_local("lity", e.env, o, "lity-label-for-o")
    add_local("salcomp", e.env, o, "olga")
    add_local("salcomp", e.env, m, "salcomp-label-for-m")
    assert rig.people("lity", e.token("m@example.com")) == [(m, "mia")]
    assert rig.get("salcomp", e.token("m@example.com")).status_code == 403
    assert rig.people("salcomp", e.token("o@example.com")) == [(o, "olga")]
    assert set(rig.opened) == {"lity", "salcomp"}


@pytest.mark.parametrize("auth", ["none", "garbage"])
def test_authentication_failure_before_company_db(rig, auth):
    rig.e.member("m@example.com")
    assert rig.get("lity", None if auth == "none" else "not-a-jwt").status_code == 401
    assert rig.opened == []


def test_no_membership_and_inactive_company_denied_before_company_db(rig):
    from identity_models import IdentityCompany
    e = rig.e
    e.member("m@example.com", slug="valor-log")
    assert rig.get("lity", e.token("m@example.com")).status_code == 403
    e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=False)
    assert rig.get("lity", e.token("admin@example.com")).status_code == 403
    assert rig.opened == []


def test_visitor_denied_nothing_but_not_listed_when_alone(rig):
    e = rig.e
    v = e.member("v@example.com", role="visitor")
    add_local("lity", e.env, v, "vic")
    assert rig.people("lity", e.token("v@example.com")) == []


def test_request_changes_no_file(rig):
    e = rig.e
    m = e.member("m@example.com")
    add_local("lity", e.env, m, "mia")
    token = e.token("m@example.com")
    before = rig.snapshot()
    assert rig.get("lity", token).status_code == 200
    rig.runtime.dispose()
    assert rig.snapshot() == before
    for slug, path in rig.paths().items():
        assert not [p for p in path.parent.iterdir() if p.name.startswith(path.name) and p != path and p.suffix in ("-wal", "-shm", "-journal")]


def test_assignable_user_ids_unit(rig):
    e = rig.e
    m = e.member("m@example.com")
    e.member("v@example.com", role="visitor")
    assert svc.assignable_user_ids(e.session, "lity") == [e.admin, m]


def test_route_registered_in_main_app():
    import main
    assert "/api/companies/{company_slug}/lead-options" in set(main.app.openapi()["paths"])
    assert "/api/lead-options" in set(main.app.openapi()["paths"])
