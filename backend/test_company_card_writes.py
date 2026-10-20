"""Company-scoped card writes: central auth and roles first, card_rules for ownership.

Disposable tmp_path identity and company databases only.
"""
import hashlib
import json
import sqlite3

import pytest
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

    def call(self, method, path, token=None, slug="lity", **kw):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.request(method, f"/api/companies/{slug}/boards/1{path}", headers=headers, **kw)

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


def test_create_stores_annual_values_in_usd_and_rejects_negatives(rig):
    r = rig.call("post", "/cards", admin(rig), json={"column_id": 1, "title": "v", "deal_value": 100, "deal_value_next_year": 40})
    assert r.status_code == 201
    assert (r.json()["deal_value"], r.json()["deal_value_next_year"], r.json()["currency"]) == (100, 40, "USD")
    bad = rig.call("post", "/cards", admin(rig), json={"column_id": 1, "title": "v", "deal_value_next_year": -1})
    assert bad.status_code == 422


def test_create_validates_column(rig):
    assert rig.call("post", "/cards", admin(rig), json={"column_id": 99, "title": "x"}).status_code == 404
    assert rig.call("post", "/cards", admin(rig), json={"column_id": 1, "title": "x"}).json()["salesperson_id"] is None


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


def test_admin_assignment_needs_central_eligibility_and_local_row(rig):
    token = admin(rig)
    uid, _ = member(rig)
    other_company, _ = member(rig, "v@example.com", slug="valor-log")
    # eligible centrally but no local row yet: refused by card_rules, nothing created by this write
    assert rig.call("put", "/cards/1", token, json={"salesperson_id": uid}).status_code == 422
    # not a member of this company: refused on central eligibility even if a local row exists
    rig.sql("INSERT INTO users (id, username, created_at, updated_at) VALUES (?, 'x', ?, ?)", (other_company, NOW, NOW))
    assert rig.call("put", "/cards/1", token, json={"salesperson_id": other_company}).status_code == 422
    rig.sql("INSERT INTO users (id, username, created_at, updated_at) VALUES (?, 'mm', ?, ?)", (uid, NOW, NOW))
    r = rig.call("put", "/cards/1", token, json={"salesperson_id": uid})
    assert r.status_code == 200 and r.json()["salesperson_id"] == uid
    assert rig.sql("SELECT action FROM audit_events WHERE card_id = 1") == [("assign",)]
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
    before = {s: rig.digest(s) for s in COMPANY_SLUGS if s != "lity"}
    assert rig.call("post", "/cards", admin(rig), json={"column_id": 1, "title": "x"}).status_code == 201
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
    assert rig.sql("SELECT user_id FROM undo_entries") == [(uid,)]  # one entry, the caller's own
    assert rig.sql("SELECT user_id, role FROM conversation_messages ORDER BY id") == [(uid, "user"), (uid, "assistant")]


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


def test_create_super_admin_card_is_unassigned_and_audited_without_assign(rig):
    card_id, owner = _created_owner(rig, admin(rig))
    assert owner is None
    assert [a for (a,) in rig.sql("SELECT action FROM audit_events WHERE card_id = ? ORDER BY id", (card_id,))] == ["create"]


def test_create_company_admin_card_is_assigned_to_self_today(rig):
    """Documents current behaviour: new_card_fields judges is_admin(context), which only the global administrator
    satisfies, so a company Admin creates like a Sales Member (assigned to self) although it may reassign later."""
    from test_company_admin_products import company_admin
    uid, token = company_admin(rig)
    card_id, owner = _created_owner(rig, token)
    assert owner == uid
    assert [a for (a,) in rig.sql("SELECT action FROM audit_events WHERE card_id = ? ORDER BY id", (card_id,))] == ["create", "assign"]


@pytest.mark.parametrize("who", ["member", "super_admin"])
def test_create_ignores_a_client_supplied_salesperson_id(rig, who):
    other_uid, _ = member(rig, "other@example.com")
    rig.assign(2, other_uid, "other@example.com")
    uid, token = member(rig) if who == "member" else (None, admin(rig))
    card_id, owner = _created_owner(rig, token, salesperson_id=other_uid)  # unknown field, not part of CardCreate
    assert owner == (uid if who == "member" else None)
    assert owner != other_uid
    assert rig.sql("SELECT salesperson_id FROM cards WHERE id = ?", (card_id,)) == [(owner,)]
