"""Company-scoped card writes: central auth and roles first, card_rules for ownership.

Disposable tmp_path identity and company databases only.
"""
import re
import hashlib
import json
import sqlite3

import pytest

import identity_service as svc
from fastapi import FastAPI
from fastapi.testclient import TestClient

import company_runtime as cr
from company_routes import build_company_router
from company_runtime import CompanyRuntime
from create_company_db import create_company_database
from database_targets import COMPANY_SLUGS, company_database_path
from identity_database import identity_db_dependency
from test_central_deps import Env

NOW = "2026-01-01 00:00:00"


def seed_company(path):
    con = sqlite3.connect(path)
    try:
        con.execute("INSERT INTO users (id, username, created_at, updated_at) VALUES (7, 'ana', ?, ?)", (NOW, NOW))
        con.execute("INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (1, 7, 'b', ?, ?)", (NOW, NOW))
        for cid, pos, title in ((1, 0, "First"), (2, 1, "Second")):
            con.execute("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
                        "VALUES (?, 1, ?, ?, 'open', ?, ?)", (cid, title, pos, NOW, NOW))
        for cid in (1, 2):  # card 1 and 2 unassigned, in column 1
            con.execute("INSERT INTO cards (id, column_id, title, position, created_at, updated_at) "
                        "VALUES (?, 1, ?, ?, ?, ?)", (cid, f"card {cid}", cid, NOW, NOW))
        con.commit()
    finally:
        con.close()


class Rig:
    def __init__(self, e):
        self.e = e
        for slug in COMPANY_SLUGS:
            create_company_database(slug, e.env)
            seed_company(company_database_path(slug, e.env))
        self.runtime = CompanyRuntime(e.env)
        self.opened: list[str] = []
        real = cr.company_session_factory

        def spying(engine):
            factory = real(engine)
            self.opened.append(factory.kw["info"]["company_slug"])
            return factory

        self.patch = spying
        app = FastAPI()
        app.include_router(build_company_router(identity_db_dependency(e.factory), self.runtime))
        self.client = TestClient(app)

    def call(self, method, path, token=None, slug="lity", versionless=False, **kw):
        """One request to board 1. The card update, move and delete routes require `expected_version` (428 without it),
        so, like the real client, this sends the card's current version unless the test already named one or passes
        `versionless=True` to exercise the missing precondition."""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if not versionless:
            path, kw = self.versioned(method, path, kw, slug)
        return self.client.request(method, f"/api/companies/{slug}/boards/1{path}", headers=headers, **kw)

    def versioned(self, method, path, kw, slug="lity"):
        """(path, kwargs) of a card update, move or delete with the card's current version added when the request does
        not carry one; any other request is returned unchanged. `path` may carry any prefix before /cards/<id>."""
        match = re.search(r"/cards/(\d+)(/position)?$", path)
        if not match or method.lower() not in ("put", "delete"):
            return path, kw
        try:
            rows = self.sql("SELECT version FROM card_versions WHERE card_id = ?", (int(match.group(1)),), slug)
        except sqlite3.OperationalError:  # a test that broke the version table: the route must refuse, not this helper
            rows = []
        current = rows[0][0] if rows else 0
        if method.lower() == "put":
            body = dict(kw.get("json") or {})
            body.setdefault("expected_version", current)
            return path, {**kw, "json": body}
        return (path if "?" in path else f"{path}?expected_version={current}"), kw

    def sql(self, query, args=(), slug="lity"):
        con = sqlite3.connect(company_database_path(slug, self.e.env))
        try:
            rows = con.execute(query, args).fetchall()
            con.commit()
            return rows
        finally:
            con.close()

    def digest(self, slug):
        return hashlib.sha256(company_database_path(slug, self.e.env).read_bytes()).hexdigest()

    def assign(self, card_id, user_id, username="local", slug="lity"):
        """Give a card to an identity user, with the local reference row."""
        self.sql("INSERT OR IGNORE INTO users (id, username, created_at, updated_at) VALUES (?, ?, ?, ?)",
                 (user_id, username, NOW, NOW), slug)
        self.sql("UPDATE cards SET salesperson_id = ? WHERE id = ?", (user_id, card_id), slug)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    e = Env(tmp_path)
    r = Rig(e)
    monkeypatch.setattr(cr, "company_session_factory", r.patch)
    yield r
    r.runtime.dispose()
    e.session.close()
    e.engine.dispose()


def admin(rig):
    return rig.e.token("admin@example.com")


def member(rig, email="m@example.com", role="sales_member", slug="lity"):
    uid = rig.e.member(email, slug=slug, role=role)
    return uid, rig.e.token(email)


WRITES = [
    ("post", "/cards", {"json": {"column_id": 1, "title": "x"}}),
    ("put", "/cards/1", {"json": {"title": "y"}}),
    ("put", "/cards/1/position", {"json": {"column_id": 2, "position": 0}}),
    ("delete", "/cards/1", {}),
]


@pytest.mark.parametrize("method,path,kw", WRITES)
def test_visitor_refused_before_company_db_opens(rig, method, path, kw):
    uid, token = member(rig, role="visitor")
    rig.assign(1, uid)  # even a card assigned to the visitor stays read-only
    before = rig.digest("lity")
    assert rig.call(method, path, token, **kw).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before


@pytest.mark.parametrize("method,path,kw", WRITES)
def test_unauthenticated_and_legacy_refused_before_company_db_opens(rig, method, path, kw):
    for token in (None, "garbage"):
        assert rig.call(method, path, token, **kw).status_code == 401
    assert rig.opened == []


@pytest.mark.parametrize("method,path,kw", WRITES)
def test_member_of_another_company_refused_and_nothing_written(rig, method, path, kw):
    _, token = member(rig, slug="valor-log")
    before = rig.digest("lity")
    assert rig.call(method, path, token, **kw).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before


def test_member_create_is_assigned_to_self_with_audit_and_event(rig):
    uid, token = member(rig)
    r = rig.call("post", "/cards", token, json={"column_id": 1, "title": "mine"})
    assert r.status_code == 201 and r.json()["salesperson_id"] == uid
    card_id = r.json()["id"]
    assert rig.sql("SELECT username FROM users WHERE id = ?", (uid,)) == [("m@example.com",)]
    actions = rig.sql("SELECT action, actor_user_id FROM audit_events WHERE card_id = ? ORDER BY id", (card_id,))
    assert actions == [("create", uid), ("assign", uid)]
    assert rig.sql("SELECT event_type, user_id, source FROM card_stage_events WHERE card_id = ?", (card_id,)) == [
        ("created", uid, "api")]


def sales_person(rig, email="seller@example.com"):
    """An eligible Sales Member of lity who has never written there (no local reference row yet)."""
    return member(rig, email)[0]


def test_create_stores_annual_values_in_usd_and_rejects_negatives(rig):
    seller = sales_person(rig)
    body = {"column_id": 1, "title": "v", "deal_value": 100, "deal_value_next_year": 40, "salesperson_id": seller}
    r = rig.call("post", "/cards", admin(rig), json=body)
    assert r.status_code == 201
    assert (r.json()["deal_value"], r.json()["deal_value_next_year"], r.json()["currency"]) == (100, 40, "USD")
    bad = rig.call("post", "/cards", admin(rig), json={**body, "deal_value_next_year": -1})
    assert bad.status_code == 422


def test_create_validates_column(rig):
    seller = sales_person(rig)
    assert rig.call("post", "/cards", admin(rig), json={"column_id": 99, "title": "x", "salesperson_id": seller}).status_code == 404
    assert rig.call("post", "/cards", admin(rig), json={"column_id": 1, "title": "x", "salesperson_id": seller}).json()["salesperson_id"] == seller


def test_member_edits_only_own_cards_and_edit_is_logged(rig):
    uid, token = member(rig)
    rig.assign(1, uid)
    assert rig.call("put", "/cards/1", token, json={"title": "new"}).status_code == 200
    history = rig.sql("SELECT user_id, changes FROM card_edit_history WHERE card_id = 1")
    assert history[0][0] == uid and json.loads(history[0][1])["title"]["new"] == "new"
    assert rig.call("put", "/cards/2", token, json={"title": "nope"}).status_code == 403  # unassigned
    rig.assign(2, 7)
    assert rig.call("put", "/cards/2", token, json={"title": "nope"}).status_code == 403  # someone else's
    assert rig.sql("SELECT title FROM cards WHERE id = 2") == [("card 2",)]


def test_member_cannot_assign_or_reassign(rig):
    uid, token = member(rig)
    rig.assign(1, uid)
    assert rig.call("put", "/cards/1", token, json={"salesperson_id": 7}).status_code == 403
    assert rig.sql("SELECT salesperson_id FROM cards WHERE id = 1") == [(uid,)]


def test_admin_assignment_needs_central_eligibility_and_creates_the_local_row(rig):
    token = admin(rig)
    uid, _ = member(rig)
    other_company, _ = member(rig, "v@example.com", slug="valor-log")
    # not a member of this company: refused on central eligibility even if a local row exists
    rig.sql("INSERT INTO users (id, username, created_at, updated_at) VALUES (?, 'x', ?, ?)", (other_company, NOW, NOW))
    assert rig.call("put", "/cards/1", token, json={"salesperson_id": other_company}).status_code == 422
    # eligible centrally but never wrote here: the local reference is created, as on card creation
    assert rig.sql("SELECT COUNT(*) FROM users WHERE id = ?", (uid,)) == [(0,)]
    r = rig.call("put", "/cards/1", token, json={"salesperson_id": uid})
    assert r.status_code == 200 and r.json()["salesperson_id"] == uid
    assert r.json()["salesperson_name"] == rig.sql("SELECT username FROM users WHERE id = ?", (uid,))[0][0]
    assert rig.sql("SELECT COUNT(*) FROM users WHERE id = ?", (uid,)) == [(1,)]
    # reassigning from one person to another answers with the new name, not the previous owner's
    uid2, _ = member(rig, "m2@example.com")
    r = rig.call("put", "/cards/1", token, json={"salesperson_id": uid2})
    assert r.status_code == 200 and r.json()["salesperson_id"] == uid2
    assert r.json()["salesperson_name"] == rig.sql("SELECT username FROM users WHERE id = ?", (uid2,))[0][0]
    assert rig.sql("SELECT action FROM audit_events WHERE card_id = 1") == [("assign",), ("assign",)]  # one per reassignment
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(0,)]  # admin edits are not logged


def test_move_rules(rig):
    uid, token = member(rig)
    rig.assign(1, uid)
    assert rig.call("put", "/cards/2/position", token, json={"column_id": 2}).status_code == 403  # unassigned
    assert rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0}).status_code == 200
    assert rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(2,)]
    assert rig.sql("SELECT event_type, user_id FROM card_stage_events WHERE card_id = 1 ORDER BY id")[-1] == ("moved", uid)
    assert rig.call("put", "/cards/1/position", token, json={"column_id": 1}).status_code == 403  # backward
    assert rig.call("put", "/cards/1/position", admin(rig), json={"column_id": 1}).status_code == 200
    assert rig.call("put", "/cards/1/position", admin(rig), json={"column_id": 99}).status_code == 404


def test_move_renumbers_target_column_so_order_is_exact(rig):
    order = lambda col: [r[0] for r in rig.sql("SELECT id FROM cards WHERE column_id = ? ORDER BY position, id", (col,))]
    positions = lambda col: [r[0] for r in rig.sql("SELECT position FROM cards WHERE column_id = ? ORDER BY position", (col,))]
    assert rig.call("put", "/cards/2/position", admin(rig), json={"column_id": 1, "position": 0}).status_code == 200
    assert order(1) == [2, 1] and positions(1) == [0, 1]  # reorder inside the same column
    assert rig.call("put", "/cards/1/position", admin(rig), json={"column_id": 2, "position": 0}).status_code == 200
    assert rig.call("put", "/cards/2/position", admin(rig), json={"column_id": 2, "position": 0}).status_code == 200
    assert order(2) == [2, 1] and positions(2) == [0, 1]  # between columns, inserted at the index
    assert rig.call("put", "/cards/1/position", admin(rig), json={"column_id": 2, "position": 99}).status_code == 200
    assert order(2) == [2, 1]  # an index past the end means last
    assert rig.call("put", "/cards/1/position", admin(rig), json={"column_id": 2, "position": -1}).status_code == 422


def test_member_cannot_move_or_reorder_cards_of_others(rig):
    uid, token = member(rig)
    rig.assign(1, uid)
    # card 2 is unassigned: neither another column nor a reorder of its own column is allowed
    for body in ({"column_id": 2, "position": 0}, {"column_id": 1, "position": 0}):
        assert rig.call("put", "/cards/2/position", token, json=body).status_code == 403
    assert rig.sql("SELECT id, column_id, position FROM cards ORDER BY id") == [(1, 1, 1), (2, 1, 2)]
    # reordering an own card in its column is allowed, and renumbers the others without changing them otherwise
    assert rig.call("put", "/cards/1/position", token, json={"column_id": 1, "position": 1}).status_code == 200
    assert rig.sql("SELECT id, position FROM cards ORDER BY id") == [(1, 1), (2, 0)]


def test_delete_is_admin_only_and_audited(rig):
    uid, token = member(rig)
    rig.assign(1, uid)
    before = rig.digest("lity")
    assert rig.call("delete", "/cards/1", token).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before
    assert rig.call("delete", "/cards/1", admin(rig)).status_code == 204
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(0,)]
    assert rig.sql("SELECT action, actor_user_id FROM audit_events") == [("delete", rig.e.admin)]
    assert rig.call("delete", "/cards/1", admin(rig)).status_code == 404


def test_write_in_one_company_never_touches_another(rig):
    seller = sales_person(rig)
    before = {s: rig.digest(s) for s in COMPANY_SLUGS if s != "lity"}
    assert rig.call("post", "/cards", admin(rig), json={"column_id": 1, "title": "x", "salesperson_id": seller}).status_code == 201
    assert {s: rig.digest(s) for s in before} == before
    assert rig.opened == ["lity"]


def test_reads_still_create_no_local_user_row(rig):
    uid, token = member(rig)
    assert rig.call("get", "", token).status_code == 200
    assert rig.sql("SELECT COUNT(*) FROM users WHERE id = ?", (uid,)) == [(0,)]


def test_board_and_column_admin_routes(rig):
    uid, token = member(rig)
    _, visitor = member(rig, "v@example.com", role="visitor")
    atoken = admin(rig)
    before = rig.digest("lity")
    for method, path, body in (
        ("post", "/columns", {"title": "N", "position": 5}),
        ("put", "/columns/2", {"title": "Renamed"}),
        ("delete", "/columns/2", None),
    ):
        for t in (token, visitor):
            kw = {"json": body} if body is not None else {}
            assert rig.call(method, path, t, **kw).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before
    assert rig.call("get", "", visitor).status_code == 200
    r = rig.client.get("/api/companies/lity/boards", headers={"Authorization": f"Bearer {visitor}"})
    assert r.status_code == 200 and [b["id"] for b in r.json()] == [1]
    assert rig.client.post("/api/companies/lity/boards", headers={"Authorization": f"Bearer {token}"}, json={"title": "x"}).status_code == 403
    created = rig.call("post", "/columns", atoken, json={"title": "N", "position": 5, "stage_kind": "open"})
    assert created.status_code == 201
    assert rig.call("put", "/columns/2", atoken, json={"title": "Renamed"}).json()["title"] == "Renamed"
    assert rig.call("post", "/columns", atoken, json={"title": "W", "position": 6, "stage_kind": "won"}).status_code == 201
    assert rig.call("post", "/columns", atoken, json={"title": "W2", "position": 7, "stage_kind": "won"}).status_code == 409
    assert rig.call("put", "/columns/1", atoken, json={"stage_kind": "lost"}).status_code == 409  # holds cards
    assert rig.call("delete", f"/columns/{created.json()['id']}", atoken).status_code == 204
    assert rig.call("delete", "/columns/99", atoken).status_code == 404
    b = rig.client.post("/api/companies/lity/boards", headers={"Authorization": f"Bearer {atoken}"}, json={"title": "Second"})
    assert b.status_code == 201 and b.json()["user_id"] == rig.e.admin


def fake_ai(monkeypatch, actions):
    import routes

    async def fake(board_state, history, message):
        return {"success": True, "response": "ok", "actions": actions, "confidence": 1, "tokens_used": 0}

    monkeypatch.setattr(routes, "call_ai_with_board", fake)


def ai(rig, token):
    return rig.client.post("/api/companies/lity/boards/1/ai", headers={"Authorization": f"Bearer {token}"},
                           json={"message": "do it"})


def test_ai_is_refused_for_visitors_and_outsiders_before_company_db(rig, monkeypatch):
    fake_ai(monkeypatch, [{"type": "delete", "card_id": 1}])
    _, visitor = member(rig, "v@example.com", role="visitor")
    _, outsider = member(rig, "o@example.com", slug="valor-log")
    before = rig.digest("lity")
    assert ai(rig, visitor).status_code == 403 and ai(rig, outsider).status_code == 403 and ai(rig, None).status_code == 401
    assert rig.opened == [] and rig.digest("lity") == before


def test_ai_member_rules_apply_and_record_one_undo_entry(rig, monkeypatch):
    uid, token = member(rig)
    rig.assign(1, uid)
    fake_ai(monkeypatch, [
        {"type": "create", "column_id": 1, "title": "AI card"},
        {"type": "update", "card_id": 1, "title": "AI edit"},
        {"type": "update", "card_id": 2, "title": "not mine"},
        {"type": "delete", "card_id": 1},
    ])
    r = ai(rig, token)
    assert r.status_code == 200
    applied = r.json()["actions_applied"]
    assert [x["type"] for x in applied["successful"]] == ["create", "update"] and len(applied["failed"]) == 2
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(1,)]  # member cannot delete
    assert rig.sql("SELECT salesperson_id FROM cards WHERE title = 'AI card'") == [(uid,)]
    # the request created a card, and undoing a creation deletes it (Super Admin only): no entry is offered at all
    assert rig.sql("SELECT user_id FROM undo_entries") == []
    assert rig.sql("SELECT user_id, role FROM conversation_messages ORDER BY id") == [(uid, "user"), (uid, "assistant")]


def test_ai_member_edit_only_request_records_one_undo_entry(rig, monkeypatch):
    uid, token = member(rig)
    rig.assign(1, uid)
    fake_ai(monkeypatch, [{"type": "update", "card_id": 1, "title": "AI edit"}])
    assert ai(rig, token).status_code == 200
    assert rig.sql("SELECT user_id FROM undo_entries") == [(uid,)]  # one entry, the caller's own


def test_ai_admin_assignment_limited_to_centrally_eligible(rig, monkeypatch):
    uid, _ = member(rig)
    other, _ = member(rig, "o@example.com", slug="valor-log")
    for u, name in ((uid, "mm"), (other, "oo")):
        rig.sql("INSERT INTO users (id, username, created_at, updated_at) VALUES (?, ?, ?, ?)", (u, name, NOW, NOW))
    fake_ai(monkeypatch, [{"type": "update", "card_id": 1, "salesperson_id": uid},
                          {"type": "update", "card_id": 2, "salesperson_id": other}])
    applied = ai(rig, admin(rig)).json()["actions_applied"]
    assert len(applied["successful"]) == 1 and len(applied["failed"]) == 1
    assert rig.sql("SELECT id, salesperson_id FROM cards ORDER BY id") == [(1, uid), (2, None)]


# --- Creation contract by role (the owner of a new card is decided by the server alone) ---

def _created_owner(rig, token, **extra):
    r = rig.call("post", "/cards", token, json={"column_id": 1, "title": "t", **extra})
    assert r.status_code == 201
    return r.json()["id"], r.json()["salesperson_id"]


def _audit_actions(rig, card_id):
    return [a for (a,) in rig.sql("SELECT action FROM audit_events WHERE card_id = ? ORDER BY id", (card_id,))]


def _local_ids(rig):
    return {i for (i,) in rig.sql("SELECT id FROM users")}


@pytest.mark.parametrize("who", ["super_admin", "company_admin"])
def test_admin_level_creates_a_card_assigned_to_an_eligible_sales_member(rig, who):
    from test_company_admin_products import company_admin
    token = admin(rig) if who == "super_admin" else company_admin(rig)[1]
    seller = sales_person(rig)
    assert seller not in _local_ids(rig)
    card_id, owner = _created_owner(rig, token, salesperson_id=seller)
    assert owner == seller
    assert seller in _local_ids(rig)  # the local reference is ensured for a centrally eligible person
    assert _audit_actions(rig, card_id) == ["create", "assign"]
    assert rig.sql("SELECT actor_user_id FROM audit_events WHERE card_id = ? AND action = 'assign'", (card_id,)) != []


@pytest.mark.parametrize("who", ["super_admin", "company_admin"])
def test_admin_level_must_name_a_salesperson_and_nothing_falls_back_to_the_creator(rig, who):
    from test_company_admin_products import company_admin
    token = admin(rig) if who == "super_admin" else company_admin(rig)[1]
    before = rig.sql("SELECT COUNT(*) FROM cards")
    r = rig.call("post", "/cards", token, json={"column_id": 1, "title": "t"})
    assert r.status_code == 422 and "salesperson" in r.text.lower()
    assert rig.sql("SELECT COUNT(*) FROM cards") == before and rig.sql("SELECT COUNT(*) FROM audit_events") == [(0,)]


@pytest.mark.parametrize("who", ["super_admin", "company_admin"])
def test_admin_level_assignment_to_an_ineligible_person_is_refused_and_writes_nothing(rig, who):
    from test_company_admin_products import company_admin
    token = admin(rig) if who == "super_admin" else company_admin(rig)[1]
    visitor, _ = member(rig, "v@example.com", role="visitor")
    outsider, _ = member(rig, "o@example.com", slug="valor-log")
    lapsed, _ = member(rig, "l@example.com")
    svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", lapsed)
    before = rig.sql("SELECT COUNT(*) FROM cards")
    for bad in (visitor, outsider, lapsed, 9999, 0, -1):
        r = rig.call("post", "/cards", token, json={"column_id": 1, "title": "t", "salesperson_id": bad})
        assert r.status_code == 422, (bad, r.text)
    assert rig.sql("SELECT COUNT(*) FROM cards") == before
    assert not {visitor, outsider, lapsed, 9999} & _local_ids(rig)  # no stub row for an ineligible person
    assert rig.sql("SELECT COUNT(*) FROM audit_events") == [(0,)]


def test_the_super_admin_may_assign_itself_as_a_globally_eligible_person(rig):
    card_id, owner = _created_owner(rig, admin(rig), salesperson_id=rig.e.admin)
    assert owner == rig.e.admin


def test_sales_member_card_is_always_theirs_and_a_supplied_salesperson_is_ignored(rig):
    other_uid, _ = member(rig, "other@example.com")
    uid, token = member(rig)
    card_id, owner = _created_owner(rig, token)
    assert owner == uid and _audit_actions(rig, card_id) == ["create", "assign"]
    card_id, owner = _created_owner(rig, token, salesperson_id=other_uid)
    assert owner == uid != other_uid  # the field is ignored for a member (documented contract)
    card_id, owner = _created_owner(rig, token, salesperson_id=9999)
    assert owner == uid
    assert other_uid not in _local_ids(rig)


def test_visitor_cannot_create(rig):
    uid, token = member(rig, "v@example.com", role="visitor")
    before = rig.digest("lity")
    rig.opened.clear()
    r = rig.call("post", "/cards", token, json={"column_id": 1, "title": "t", "salesperson_id": uid})
    assert r.status_code == 403 and rig.opened == [] and rig.digest("lity") == before


def test_user_without_membership_and_member_of_another_company_cannot_create(rig):
    from identity_models import IdentityUser
    outsider, token = member(rig, "o@example.com", slug="valor-log")
    seller = sales_person(rig)
    before = rig.digest("lity")
    rig.opened.clear()
    r = rig.call("post", "/cards", token, json={"column_id": 1, "title": "t", "salesperson_id": seller})
    assert r.status_code == 403 and rig.opened == [] and rig.digest("lity") == before


# ---- Next Action Date and Next Action Description (card_bid_details.next_step_due / next_step) ----

def test_next_action_is_stored_returned_and_cleared_through_the_company_route(rig):
    r = rig.call("put", "/cards/1", admin(rig), json={"next_step": "  Call the buyer ", "next_step_due": "2026-11-02"})
    assert r.status_code == 200
    body = r.json()
    assert body["bid_details"]["next_step"] == "Call the buyer"  # trimmed
    assert body["bid_details"]["next_step_due"] == "2026-11-02"
    assert rig.sql("SELECT next_step, next_step_due FROM card_bid_details WHERE card_id = 1") == [("Call the buyer", "2026-11-02")]
    board = rig.call("get", "", admin(rig)).json()
    shown = next(c for col in board["columns"] for c in col["cards"] if c["id"] == 1)
    assert shown["bid_details"]["next_step"] == "Call the buyer"

    cleared = rig.call("put", "/cards/1", admin(rig), json={"next_step": "   ", "next_step_due": None})
    assert cleared.status_code == 200
    assert cleared.json()["bid_details"]["next_step"] is None and cleared.json()["bid_details"]["next_step_due"] is None
    assert rig.sql("SELECT next_step, next_step_due FROM card_bid_details WHERE card_id = 1") == [(None, None)]


def test_next_action_description_is_limited_to_100_characters_and_the_date_must_be_a_date(rig):
    assert rig.call("put", "/cards/1", admin(rig), json={"next_step": "x" * 101}).status_code == 422
    assert rig.call("put", "/cards/1", admin(rig), json={"next_step_due": "not a date"}).status_code == 422
    assert rig.sql("SELECT COUNT(*) FROM card_bid_details") == [(0,)]  # nothing was written
    assert rig.call("put", "/cards/1", admin(rig), json={"next_step": "x" * 100}).status_code == 200


def test_a_card_without_a_next_action_reads_as_null_and_stays_editable(rig):
    board = rig.call("get", "", admin(rig)).json()
    card = next(c for col in board["columns"] for c in col["cards"] if c["id"] == 2)
    assert card["bid_details"] is None  # no details row until a value is first set
    assert rig.sql("SELECT COUNT(*) FROM card_bid_details") == [(0,)]
    assert rig.call("put", "/cards/2", admin(rig), json={"title": "renamed"}).status_code == 200
    assert rig.sql("SELECT COUNT(*) FROM card_bid_details") == [(0,)]  # editing something else creates no row


def test_next_action_edit_by_a_member_follows_the_ownership_rules_and_is_logged(rig):
    uid, token = member(rig)
    rig.assign(1, uid)
    assert rig.call("put", "/cards/1", token, json={"next_step": "Visit"}).status_code == 200
    changes = json.loads(rig.sql("SELECT changes FROM card_edit_history WHERE card_id = 1")[0][0])
    assert changes["next_step"]["new"] == "Visit"
    assert rig.call("put", "/cards/2", token, json={"next_step": "No"}).status_code == 403  # not theirs


def test_a_stale_version_refuses_a_next_action_edit(rig):
    assert rig.call("put", "/cards/1", admin(rig), json={"next_step": "one"}).status_code == 200
    stale = rig.call("put", "/cards/1", admin(rig), json={"next_step": "two", "expected_version": 0})
    assert stale.status_code == 409
    assert rig.sql("SELECT next_step FROM card_bid_details WHERE card_id = 1") == [("one",)]


def test_the_closing_date_stays_stored_readable_and_untouched_by_other_edits(rig):
    rig.sql("UPDATE cards SET expected_close_date = '2026-12-31' WHERE id = 1")
    r = rig.call("put", "/cards/1", admin(rig), json={"next_step": "Call"})
    assert r.status_code == 200 and r.json()["expected_close_date"] == "2026-12-31"
    assert rig.sql("SELECT expected_close_date FROM cards WHERE id = 1") == [("2026-12-31",)]
