"""Phase 2E part 4: a company Admin uses AI and undo in its own company, on its own undo entry only.

Imports and column deletion stay Super Admin only. Synthetic data, tmp_path databases only.
"""
import pytest

import central_auth as ca
import identity_service as svc
from auth import create_access_token
from identity_models import IdentityCompany
from test_central_deps import GOOD
from test_company_admin_products import company_admin
from test_company_card_writes import NOW, admin, member
from test_company_card_writes import ai, fake_ai, rig  # noqa: F401  (fixtures/helpers)
from test_company_products import send


def local_row(rig, uid, name="loc", slug="lity"):
    rig.sql("INSERT OR IGNORE INTO users (id, username, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (uid, name, NOW, NOW), slug)


def undo(rig, token, slug="lity"):
    return rig.call("post", "/undo", token, slug=slug)


def owner(rig, card_id):
    return rig.sql("SELECT salesperson_id FROM cards WHERE id = ?", (card_id,))[0][0]


# --- AI ---------------------------------------------------------------------

def test_ai_admin_can_edit_reassign_move_back_and_delete_with_the_same_audit(rig, monkeypatch):
    aid, token = company_admin(rig)
    m, _ = member(rig, "m@example.com")
    local_row(rig, m)
    rig.assign(2, m)
    rig.sql("UPDATE cards SET column_id = 2 WHERE id = 2")
    fake_ai(monkeypatch, [
        {"type": "update", "card_id": 2, "title": "AI edit"},                      # someone else's card
        {"type": "update", "card_id": 1, "salesperson_id": m},                      # assignment
        {"type": "move", "card_id": 2, "column_id": 1},                             # backward
        {"type": "delete", "card_id": 1},
    ])
    applied = ai(rig, token).json()["actions_applied"]
    assert [x["type"] for x in applied["successful"]] == ["update", "update", "move", "delete"] and not applied["failed"]
    assert rig.sql("SELECT title, column_id FROM cards ORDER BY id") == [("AI edit", 1)]
    assert rig.sql("SELECT action, actor_user_id FROM audit_events ORDER BY id") == [
        ("edit", aid), ("assign", aid), ("delete", aid)]  # the title edit of another person's card is audited (M2)
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(0,)]
    assert rig.sql("SELECT source FROM card_stage_events WHERE event_type = 'moved'") == [("ai",)]
    assert rig.sql("SELECT user_id FROM undo_entries") == [(aid,)]  # one entry, the caller's own


def test_ai_assignment_validation_is_unchanged_for_the_admin(rig, monkeypatch):
    _, token = company_admin(rig)
    visitor, _ = member(rig, "v@example.com", role="visitor")
    outsider, _ = member(rig, "o@example.com", slug="valor-log")
    other_admin, _ = company_admin(rig, "a2@example.com")
    for uid in (visitor, outsider, other_admin):
        local_row(rig, uid)
    fake_ai(monkeypatch, [{"type": "update", "card_id": 1, "salesperson_id": u} for u in (visitor, outsider, other_admin)])
    applied = ai(rig, token).json()["actions_applied"]
    assert applied["successful"] == [] and len(applied["failed"]) == 3
    assert owner(rig, 1) is None


def test_ai_create_by_admin_is_still_assigned_to_the_creator_and_structure_is_refused(rig, monkeypatch):
    aid, token = company_admin(rig)
    fake_ai(monkeypatch, [{"type": "create", "column_id": 1, "title": "AI card"},
                          {"type": "create_column", "title": "Nope"}, {"type": "import", "rows": []}])
    applied = ai(rig, token).json()["actions_applied"]
    assert [x["type"] for x in applied["successful"]] == ["create"] and len(applied["failed"]) == 2
    assert rig.sql("SELECT salesperson_id FROM cards WHERE title = 'AI card'") == [(aid,)]
    assert rig.sql("SELECT COUNT(*) FROM columns") == [(2,)] and rig.sql("SELECT COUNT(*) FROM boards") == [(1,)]


def test_ai_sales_member_keeps_every_restriction(rig, monkeypatch):
    uid, token = member(rig)
    other, _ = member(rig, "o@example.com")
    local_row(rig, other)
    rig.assign(1, uid)
    rig.assign(2, other)
    rig.sql("UPDATE cards SET column_id = 2 WHERE id = 1")
    fake_ai(monkeypatch, [
        {"type": "update", "card_id": 1, "salesperson_id": other},    # reassign
        {"type": "update", "card_id": 2, "title": "not mine"},
        {"type": "move", "card_id": 1, "column_id": 1},               # backward
        {"type": "delete", "card_id": 1},
    ])
    applied = ai(rig, token).json()["actions_applied"]
    assert applied["successful"] == [] and len(applied["failed"]) == 4
    assert rig.sql("SELECT id, title, column_id, salesperson_id FROM cards ORDER BY id") == [
        (1, "card 1", 2, uid), (2, "card 2", 1, other)]


def test_ai_visitor_and_cross_company_and_forged_tokens_are_refused_before_the_db_opens(rig, monkeypatch):
    uid, _ = member(rig, "a@example.com", slug="lity")
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "admin")
    invite = svc.create_invitation(rig.e.session, rig.e.admin, "a@example.com", "valor-log", "visitor").token
    ca.join_invitation(rig.e.session, invite, "a@example.com", GOOD)
    token = rig.e.token("a@example.com")
    _, visitor = member(rig, "v@example.com", role="visitor")
    fake_ai(monkeypatch, [{"type": "delete", "card_id": 1}])
    before = {s: rig.digest(s) for s in ("lity", "valor-log", "salcomp")}
    rig.opened.clear()
    post = lambda slug, t: rig.client.post(f"/api/companies/{slug}/boards/1/ai", json={"message": "x"},
                                           headers={"Authorization": f"Bearer {t}"})
    assert post("valor-log", token).status_code == 403  # Visitor there, Admin elsewhere
    assert post("salcomp", token).status_code == 403    # no membership
    assert post("lity", visitor).status_code == 403
    assert post("lity", create_access_token({"sub": str(uid), "role": "admin"})).status_code == 401
    assert rig.opened == [] and {s: rig.digest(s) for s in before} == before


def test_ai_admin_loses_effect_on_demotion_deactivation_and_inactive_company(rig, monkeypatch):
    uid, token = company_admin(rig)
    fake_ai(monkeypatch, [{"type": "delete", "card_id": 1}])
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "sales_member")
    applied = ai(rig, token).json()["actions_applied"]  # now a Sales Member: delete refused
    assert applied["successful"] == [] and rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(1,)]
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "admin")
    rig.e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=False)
    rig.opened.clear()
    assert ai(rig, token).status_code == 403 and rig.opened == []
    rig.e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=True)
    svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", uid)
    assert ai(rig, token).status_code == 403
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(1,)]


# --- undo -------------------------------------------------------------------

def test_admin_undoes_its_own_reassignment_of_another_persons_card(rig):
    _, token = company_admin(rig)
    m, _ = member(rig, "m@example.com")
    local_row(rig, m)
    assert rig.call("put", "/cards/1", token, json={"salesperson_id": m}).status_code == 200
    assert undo(rig, token).status_code == 200
    assert owner(rig, 1) is None
    assert undo(rig, token).status_code == 404  # used up


def test_admin_undoes_its_own_edit_of_another_persons_card_and_backward_move(rig):
    _, token = company_admin(rig)
    m, _ = member(rig, "m@example.com")
    rig.assign(1, m)
    assert rig.call("put", "/cards/1", token, json={"title": "Edited"}).status_code == 200
    assert undo(rig, token).status_code == 200 and rig.sql("SELECT title FROM cards WHERE id = 1") == [("card 1",)]
    rig.sql("UPDATE cards SET column_id = 2 WHERE id = 1")
    assert rig.call("put", "/cards/1/position", token, json={"column_id": 1, "position": 0}).status_code == 200
    assert undo(rig, token).status_code == 200
    assert rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(2,)]


def test_admin_undoes_its_own_deletion_and_its_ai_request(rig, monkeypatch):
    _, token = company_admin(rig)
    assert rig.call("delete", "/cards/1", token).status_code == 204
    assert undo(rig, token).status_code == 200 and rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'card 1'") == [(1,)]
    m, _ = member(rig, "m@example.com")
    local_row(rig, m)
    fake_ai(monkeypatch, [{"type": "update", "card_id": 2, "salesperson_id": m}, {"type": "update", "card_id": 2, "title": "T"}])
    assert not ai(rig, token).json()["actions_applied"]["failed"]
    assert undo(rig, token).status_code == 200
    assert rig.sql("SELECT title, salesperson_id FROM cards WHERE id = 2") == [("card 2", None)]


def test_admin_cannot_undo_another_users_operation(rig):
    _, token = company_admin(rig)
    sup = admin(rig)
    m_id, m_token = member(rig, "m@example.com")
    rig.assign(2, m_id)
    assert rig.call("put", "/cards/1", sup, json={"title": "By super"}).status_code == 200
    assert rig.call("put", "/cards/2", m_token, json={"title": "By member"}).status_code == 200
    assert undo(rig, token).status_code == 404  # the Admin has no entry of its own
    assert rig.sql("SELECT user_id FROM undo_entries ORDER BY user_id") == sorted([(rig.e.admin,), (m_id,)])
    assert rig.sql("SELECT title FROM cards ORDER BY id") == [("By super",), ("By member",)]


def test_undo_conflicts_and_missing_cards_are_refused_unchanged(rig):
    _, token = company_admin(rig)
    sup = admin(rig)
    m, _ = member(rig, "m@example.com")
    local_row(rig, m)
    assert rig.call("put", "/cards/1", token, json={"salesperson_id": m}).status_code == 200
    assert rig.call("put", "/cards/1", sup, json={"salesperson_id": None}).status_code == 200  # later change
    assert undo(rig, token).status_code == 409
    assert rig.call("put", "/cards/2", token, json={"title": "Mine"}).status_code == 200
    assert rig.call("delete", "/cards/2", sup).status_code == 204
    assert undo(rig, token).status_code == 409
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 2") == [(0,)]


def test_demotion_deactivation_and_inactive_company_before_undo(rig):
    uid, token = company_admin(rig)
    m, _ = member(rig, "m@example.com")
    local_row(rig, m)
    assert rig.call("put", "/cards/1", token, json={"salesperson_id": m}).status_code == 200
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "sales_member")
    assert undo(rig, token).status_code == 409  # a Sales Member cannot touch another person's card
    assert owner(rig, 1) == m
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "admin")
    assert rig.call("put", "/cards/2", token, json={"title": "x"}).status_code == 200
    rig.e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=False)
    before = rig.digest("lity")
    rig.opened.clear()
    assert undo(rig, token).status_code == 403 and rig.opened == [] and rig.digest("lity") == before
    rig.e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=True)
    svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", uid)
    assert undo(rig, token).status_code == 403
    assert rig.sql("SELECT title FROM cards WHERE id = 2") == [("x",)]


def test_undo_never_crosses_companies(rig):
    uid, _ = member(rig, "a@example.com", slug="lity")
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "admin")
    for slug, role in (("salcomp", "sales_member"), ("valor-log", "visitor")):
        invite = svc.create_invitation(rig.e.session, rig.e.admin, "a@example.com", slug, role).token
        ca.join_invitation(rig.e.session, invite, "a@example.com", GOOD)
    token = rig.e.token("a@example.com")
    assert rig.call("put", "/cards/1", token, json={"title": "Edited"}).status_code == 200
    before = {s: rig.digest(s) for s in ("valor-log",)}
    rig.opened.clear()
    assert undo(rig, token, "valor-log").status_code == 403  # Visitor there
    assert undo(rig, token, "salcomp").status_code == 404  # Sales Member there: no entry of its own
    assert rig.sql("SELECT COUNT(*) FROM undo_entries", slug="lity") == [(1,)]
    assert rig.sql("SELECT title FROM cards WHERE id = 1", slug="salcomp") == [("card 1",)]
    assert rig.digest("valor-log") == before["valor-log"]


def test_sales_member_and_visitor_undo_behavior_is_unchanged(rig):
    uid, token = member(rig)
    rig.assign(1, uid)
    assert rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0}).status_code == 200
    assert undo(rig, token).status_code == 200  # a member's own change is restored, even backward
    assert rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(1,)]
    _, visitor = member(rig, "v@example.com", role="visitor")
    before = rig.digest("lity")
    rig.opened.clear()
    assert undo(rig, visitor).status_code == 403 and rig.call("get", "/undo", visitor).status_code == 403
    assert undo(rig, create_access_token({"sub": str(uid), "role": "admin"})).status_code == 401
    assert rig.opened == [] and rig.digest("lity") == before


def test_column_deletion_and_earlier_parts_remain(rig):
    _, token = company_admin(rig)
    assert send(rig, "post", "/products", token, json={"name": "Adapters"}).status_code == 201
    assert send(rig, "post", "/boards", token, json={"title": "B2"}).status_code == 201
    assert send(rig, "delete", "/boards/1/columns/1", token).status_code == 403
