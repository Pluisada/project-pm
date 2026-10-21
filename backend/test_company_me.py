"""GET /api/companies/{company_slug}/me: stable identity, effective role and capabilities for one company.

Disposable tmp_path identity and company databases only.
"""
import hashlib
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import company_runtime as cr
import identity_service as svc
from central_deps import company_capabilities, resolve_company_context
from company_database import company_session_factory, open_company_engine
from company_routes import build_company_router
from company_runtime import CompanyRuntime
from create_company_db import create_company_database
from database_targets import COMPANY_SLUGS, company_database_path
from identity_database import identity_db_dependency
from identity_models import IdentityMembership, IdentityUser
from test_central_deps import Env
from test_company_card_writes import Rig as WriteRig, member as write_member

NOW = "2026-01-01 00:00:00"
SUPER = {"readOnly": False, "canEditOwnCards": True, "canEditAnyCard": True, "canMoveBack": True,
         "canDeleteCards": True, "canAssignSalesperson": True, "canManageBoards": True, "canImport": True,
         "canExport": True, "canManageProducts": True, "canReview": True}
# A company Admin keeps the commercial card operations and the export; delete, imports, boards and products are Super Admin only.
ADMIN = SUPER | {"canDeleteCards": False, "canManageBoards": False, "canImport": False, "canManageProducts": False}
MEMBER = {"readOnly": False, "canEditOwnCards": True, "canEditAnyCard": False, "canMoveBack": False,
          "canDeleteCards": False, "canAssignSalesperson": False, "canManageBoards": False, "canImport": False,
          "canExport": False, "canManageProducts": False, "canReview": False}
VISITOR = {k: False for k in SUPER} | {"readOnly": True}


class Rig:
    def __init__(self, e):
        self.e = e
        for slug in COMPANY_SLUGS:
            create_company_database(slug, e.env)
        self.runtime = CompanyRuntime(e.env)
        app = FastAPI()
        app.include_router(build_company_router(identity_db_dependency(e.factory), self.runtime))
        self.client = TestClient(app)

    def me(self, slug, email=None, token=None):
        token = token or (self.e.token(email) if email else None)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.get(f"/api/companies/{slug}/me", headers=headers)

    def sql(self, slug, query, params=()):
        con = sqlite3.connect(company_database_path(slug, self.e.env))
        try:
            rows = con.execute(query, params).fetchall()
            con.commit()
            return rows
        finally:
            con.close()


@pytest.fixture
def rig(tmp_path):
    e = Env(tmp_path)
    r = Rig(e)
    yield r
    r.runtime.dispose()
    e.session.close()
    e.engine.dispose()


def test_capabilities_by_role(rig):
    e = rig.e
    e.member("m@example.com")
    e.member("v@example.com", role="visitor")
    for email, caps, role in (("admin@example.com", SUPER, "super_admin"), ("m@example.com", MEMBER, "sales_member"),
                              ("v@example.com", VISITOR, "visitor")):
        body = rig.me("lity", email).json()
        assert body["role"] == role and body["capabilities"] == caps and body["company_slug"] == "lity"


def test_response_has_no_personal_data_or_extra_keys(rig):
    rig.e.member("m@example.com")
    body = rig.me("lity", "m@example.com").json()
    assert set(body) == {"user_id", "company_slug", "role", "capabilities"}
    assert "m@example.com" not in rig.me("lity", "m@example.com").text


def test_user_id_is_the_central_id_and_the_local_card_owner(rig):
    e = rig.e
    uid = e.member("m@example.com")
    assert rig.me("lity", "m@example.com").json()["user_id"] == uid
    assert rig.me("lity", "admin@example.com").json()["user_id"] == e.admin
    factory = company_session_factory(open_company_engine("lity", e.env))
    with factory() as company:
        assert svc.sync_company_user(e.session, company, uid, "lity").id == uid
    rig.sql("lity", "INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (1, ?, 'b', ?, ?)", (uid, NOW, NOW))
    rig.sql("lity", "INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
                    "VALUES (1, 1, 'c', 0, 'open', ?, ?)", (NOW, NOW))
    token = e.token("m@example.com")
    r = rig.client.post("/api/companies/lity/boards/1/cards", headers={"Authorization": f"Bearer {token}"},
                        json={"column_id": 1, "title": "mine"})
    assert r.status_code == 201 and r.json()["salesperson_id"] == rig.me("lity", token=token).json()["user_id"]


def test_companies_are_isolated(rig):
    e = rig.e
    e.member("m@example.com")  # lity only
    assert rig.me("lity", "m@example.com").status_code == 200
    assert rig.me("salcomp", "m@example.com").status_code == 403
    e.update(IdentityMembership, IdentityMembership.user_id != e.admin, role="visitor")
    assert rig.me("lity", "m@example.com").json()["capabilities"] == VISITOR


def test_role_is_per_company(rig):
    e = rig.e
    e.member("m@example.com")
    e.member("m2@example.com", slug="salcomp", role="visitor")
    assert rig.me("lity", "m@example.com").json()["role"] == "sales_member"
    assert rig.me("salcomp", "m2@example.com").json()["role"] == "visitor"
    assert rig.me("salcomp", "m@example.com").status_code == 403


@pytest.mark.parametrize("slug", ["nowhere", "LITY", "lity%2F.."])
def test_unknown_company_is_403_like_no_access(rig, slug):
    rig.e.member("m@example.com")
    assert rig.me(slug, "m@example.com").status_code in (403, 404)
    assert rig.me("nowhere", "m@example.com").status_code == 403


def test_authentication_failures(rig):
    assert rig.me("lity").status_code == 401
    assert rig.me("lity", token="junk").status_code == 401


def test_revoked_membership_and_inactive_user_lose_access(rig):
    e = rig.e
    a = e.member("a@example.com")
    b = e.member("b@example.com")
    ta, tb = e.token("a@example.com"), e.token("b@example.com")
    e.update(IdentityMembership, IdentityMembership.user_id == a, is_active=False)
    e.update(IdentityUser, IdentityUser.id == b, is_active=False)
    assert rig.me("lity", token=ta).status_code == 403
    assert rig.me("lity", token=tb).status_code == 401


def test_browser_supplied_role_is_ignored(rig):
    rig.e.member("v@example.com", role="visitor")
    token = rig.e.token("v@example.com")
    r = rig.client.get("/api/companies/lity/me?role=super_admin&canImport=true",
                       headers={"Authorization": f"Bearer {token}", "X-Role": "super_admin"})
    assert r.json()["role"] == "visitor" and r.json()["capabilities"] == VISITOR


def test_capabilities_match_context_predicates_and_admin_role_stays_unassignable(rig):
    e = rig.e
    e.member("m@example.com")
    ctx = resolve_company_context(e.session, e.token("m@example.com"), "lity")
    assert company_capabilities(ctx) == MEMBER
    assert svc.can_grant_admin("sales_member") is False


def test_no_write_and_no_company_database_touched(rig):
    e = rig.e
    e.member("m@example.com")
    paths = [company_database_path(s, e.env) for s in COMPANY_SLUGS] + [e.tmp_path / "identity.db"]
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    rig.me("lity", "m@example.com")
    # token issuing in e.token() may touch identity; take the snapshot after the first call
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    rig.me("lity", "m@example.com")
    assert before == [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]


# --- capabilities against what the routes really grant ---------------------------------------------------------

def _actor(w, role):
    """(user id, token) of one caller of each role; the reserved "admin" is set directly, as no service path can."""
    if role == "super_admin":
        return w.e.admin, w.e.token("admin@example.com")
    uid, token = write_member(w, f"{role}-user@example.com", role="sales_member" if role == "admin" else role)
    if role == "admin":
        w.e.update(IdentityMembership, IdentityMembership.user_id == uid, role="admin")
        token = w.e.token(f"{role}-user@example.com")
    return uid, token


def _probe(w, uid, token):
    """What the routes actually allow this caller, one request per capability (each on its own seeded state)."""
    h = {"Authorization": f"Bearer {token}"}
    base = "/api/companies/lity"

    def ok(method, path, codes, **kw):
        return w.client.request(method, base + path, headers=h, **kw).status_code in codes

    other, _ = write_member(w, "other@example.com")
    w.assign(1, other, "other")
    w.sql("INSERT OR IGNORE INTO users (id, username, created_at, updated_at) VALUES (?, 'me', ?, ?)", (uid, "2026-01-01", "2026-01-01"))
    caps = {"canEditAnyCard": ok("put", "/boards/1/cards/2", (200,), json={"title": "any"})}  # card 2: unassigned
    w.assign(2, uid, "me")
    caps["canEditOwnCards"] = ok("put", "/boards/1/cards/2", (200,), json={"title": "own"})
    w.sql("UPDATE cards SET column_id = 2, position = 0 WHERE id = 2")
    caps["canMoveBack"] = ok("put", "/boards/1/cards/2/position", (200,), json={"column_id": 1, "position": 0})
    caps["canAssignSalesperson"] = ok("put", "/boards/1/cards/2", (200,), json={"salesperson_id": other})
    caps["canDeleteCards"] = ok("delete", "/boards/1/cards/2", (204,))
    caps["canManageBoards"] = ok("post", "/boards", (201,), json={"title": "t"})
    caps["canImport"] = not ok("post", "/boards/1/imports/preview", (403,))
    caps["canExport"] = ok("get", "/boards/1/excel/template", (200,))
    caps["canManageProducts"] = ok("post", "/products", (201,), json={"name": "Probe"})
    caps["canReview"] = ok("get", "/boards/1/reviews", (200,))
    return caps


@pytest.fixture
def wrig(tmp_path):
    e = Env(tmp_path)
    r = WriteRig(e)
    yield r
    r.runtime.dispose()
    e.session.close()
    e.engine.dispose()


@pytest.mark.parametrize("role,expected", [("super_admin", SUPER), ("admin", ADMIN), ("sales_member", MEMBER),
                                           ("visitor", VISITOR)])
def test_each_capability_matches_what_the_routes_grant(wrig, role, expected):
    uid, token = _actor(wrig, role)
    reported = wrig.client.get("/api/companies/lity/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert reported["role"] == ("super_admin" if role == "super_admin" else role)
    actual = _probe(wrig, uid, token)
    assert {k: v for k, v in reported["capabilities"].items() if k != "readOnly"} == actual
    assert reported["capabilities"] == expected


def test_reserved_admin_is_no_super_admin_beyond_the_enabled_predicates(wrig):
    uid, token = _actor(wrig, "admin")
    ctx = resolve_company_context(wrig.e.session, token, "lity")
    assert (ctx.is_admin_level, ctx.can_manage_members, ctx.can_grant_admin) == (False, False, False)
    assert (ctx.can_manage_cards, ctx.can_export_cards) == (True, True)
    assert (ctx.can_delete_cards, ctx.can_manage_boards, ctx.can_manage_imports, ctx.can_manage_products) == (False,) * 4
    col = wrig.client.delete("/api/companies/lity/boards/1/columns/2", headers={"Authorization": f"Bearer {token}"})
    assert col.status_code == 403  # column deletion stays Super Admin only and is not a reported capability
