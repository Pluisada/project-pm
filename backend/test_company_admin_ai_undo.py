"""A company Admin uses AI and undo in its own company, on its own undo entry only; the AI cannot delete for it.

Imports, card deletion and column deletion stay Super Admin only. Synthetic data, tmp_path databases only.
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

def test_ai_admin_can_edit_reassign_and_move_back_but_not_delete(rig, monkeypatch):
    aid, token = company_admin(rig)
    m, _ = member(rig, "m@example.com")
    local_row(rig, m)
    rig.assign(2, m)
    rig.sql("UPDATE cards SET column_id = 2 WHERE id = 2")
    fake_ai(monkeypatch, [
        {"type": "update", "card_id": 2, "title": "AI edit"},                      # someone else's card
        {"type": "update", "card_id": 1, "salesperson_id": m},                      # assignment
        {"type": "move", "card_id": 2, "column_id": 1},                             # backward
        {"type": "delete", "card_id": 1},                                           # refused: Super Admin only
    ])
    applied = ai(rig, token).json()["actions_applied"]
    assert [x["type"] for x in applied["successful"]] == ["update", "update", "move"]
    assert [x["reason"] for x in applied["failed"]] == ["Only admins can delete cards"]
    assert rig.sql("SELECT title, column_id FROM cards ORDER BY id") == [("card 1", 1), ("AI edit", 1)]
    assert rig.sql("SELECT action, actor_user_id FROM audit_events ORDER BY id") == [
        ("edit", aid), ("assign", aid)]  # the title edit of another person's card is audited (M2); nothing was deleted
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(0,)]
    assert rig.sql("SELECT source FROM card_stage_events WHERE event_type = 'moved'") == [("ai",)]
    assert rig.sql("SELECT user_id FROM undo_entries") == [(aid,)]  # one entry, the caller's own


def test_ai_super_admin_can_delete_with_the_same_audit(rig, monkeypatch):
    token = admin(rig)
    fake_ai(monkeypatch, [{"type": "delete", "card_id": 1}])
    applied = ai(rig, token).json()["actions_applied"]
    assert [x["type"] for x in applied["successful"]] == ["delete"] and not applied["failed"]
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(0,)]
    assert rig.sql("SELECT action FROM audit_events") == [("delete",)]


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


def test_ai_create_by_admin_needs_an_eligible_salesperson_and_structure_is_refused(rig, monkeypatch):
    aid, token = company_admin(rig)
    m, _ = member(rig, "m@example.com")
    local_row(rig, m)
    visitor, _ = member(rig, "v@example.com", role="visitor")
    local_row(rig, visitor)
    fake_ai(monkeypatch, [{"type": "create", "column_id": 1, "title": "No owner"},                          # refused: no fallback
                          {"type": "create", "column_id": 1, "title": "Visitor card", "salesperson_id": visitor},  # not eligible
                          {"type": "create", "column_id": 1, "title": "AI card", "salesperson_id": m},
                          {"type": "create_column", "title": "Nope"}, {"type": "import", "rows": []}])
    applied = ai(rig, token).json()["actions_applied"]
    assert [x["type"] for x in applied["successful"]] == ["create"] and len(applied["failed"]) == 4
    assert rig.sql("SELECT title, salesperson_id FROM cards WHERE id > 2") == [("AI card", m)]  # never the creator
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


def test_super_admin_undoes_its_own_deletion_and_an_admin_its_ai_request(rig, monkeypatch):
    sup = admin(rig)
    assert rig.call("delete", "/cards/1", sup).status_code == 204
    assert undo(rig, sup).status_code == 200 and rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'card 1'") == [(1,)]
    _, token = company_admin(rig)
    m, _ = member(rig, "m@example.com")
    local_row(rig, m)
    fake_ai(monkeypatch, [{"type": "update", "card_id": 2, "salesperson_id": m}, {"type": "update", "card_id": 2, "title": "T"}])
    assert not ai(rig, token).json()["actions_applied"]["failed"]
    assert undo(rig, token).status_code == 200
    assert rig.sql("SELECT title, salesperson_id FROM cards WHERE id = 2") == [("card 2", None)]


def test_an_admin_cannot_restore_or_delete_through_undo_what_it_could_not_delete(rig):
    _, token = company_admin(rig)
    sup = admin(rig)
    assert rig.call("delete", "/cards/1", token).status_code == 403          # no deletion, so no undo entry either
    assert undo(rig, token).status_code == 404
    assert rig.call("delete", "/cards/1", sup).status_code == 204            # the Super Admin's entry is its own
    assert undo(rig, token).status_code == 404 and rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(0,)]


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
    assert send(rig, "post", "/products", token, json={"name": "Adapters"}).status_code == 403
    assert send(rig, "post", "/boards", token, json={"title": "B2"}).status_code == 403
    assert send(rig, "delete", "/boards/1/columns/1", token).status_code == 403


# --- undo can never delete a card for anyone but the Super Admin ------------------------------------------------

def _create(rig, token, seller):
    r = rig.call("post", "/cards", token, json={"column_id": 1, "title": "Fresh", "salesperson_id": seller})
    assert r.status_code == 201
    return r.json()["id"]


def _undo_available(rig, token):
    r = rig.call("get", "/undo", token)
    assert r.status_code == 200
    return r.json()["available"]


@pytest.mark.parametrize("who", ["company_admin", "sales_member"])
def test_nobody_but_the_super_admin_can_undo_a_creation(rig, who):
    if who == "company_admin":
        uid, token = company_admin(rig)
        seller, _ = member(rig, "s@example.com")
    else:
        uid, token = member(rig)
        seller = uid
    card_id = _create(rig, token, seller)
    assert not _undo_available(rig, token)  # no "Remove new card" is offered
    before = rig.digest("lity")
    assert undo(rig, token).status_code == 404
    assert rig.digest("lity") == before
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = ?", (card_id,)) == [(1,)]
    assert rig.sql("SELECT COUNT(*) FROM audit_events WHERE action = 'delete'") == [(0,)]


def test_a_creation_does_not_leave_an_older_undo_entry_behind(rig):
    uid, token = member(rig)
    rig.assign(1, uid)
    assert rig.call("put", "/cards/1", token, json={"title": "Edited"}).status_code == 200
    assert _undo_available(rig, token)
    _create(rig, token, uid)
    assert not _undo_available(rig, token)  # the newer change replaced it, and it is not undoable


def test_super_admin_can_still_undo_its_own_creation_with_the_deletion_audited(rig):
    sup = admin(rig)
    seller, _ = member(rig, "s@example.com")
    card_id = _create(rig, sup, seller)
    assert _undo_available(rig, sup)
    assert undo(rig, sup).status_code == 200
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = ?", (card_id,)) == [(0,)]
    assert rig.sql("SELECT action FROM audit_events WHERE action = 'delete'") == [("delete",)]


@pytest.mark.parametrize("who", ["company_admin", "sales_member"])
def test_a_stale_delete_step_is_refused_at_apply_time_and_nothing_is_deleted(rig, who):
    """Defence in depth: an entry holding a removal (recorded before this rule, or by a since-demoted Super Admin)."""
    if who == "company_admin":
        uid, token = company_admin(rig)
    else:
        uid, token = member(rig)
    rig.assign(2, uid)
    rig.call("get", "/undo", token)  # the first request ensures the actor's local row
    step = {"op": "delete", "card_id": 2, "expect": {"column_id": 1, "title": "card 2"}}
    rig.sql("INSERT INTO undo_entries (user_id, board_id, description, steps, created_at) VALUES (?, 1, 'stale', ?, ?)",
            (uid, __import__("json").dumps([step]), NOW))
    r = undo(rig, token)
    assert r.status_code == 409 and "Super Admin" in r.text
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 2") == [(1,)]
    assert rig.sql("SELECT COUNT(*) FROM audit_events WHERE action = 'delete'") == [(0,)]


def test_a_visitor_still_cannot_undo(rig):
    _, token = member(rig, "v@example.com", role="visitor")
    assert undo(rig, token).status_code == 403
    assert rig.call("get", "/undo", token).status_code == 403


def test_undo_is_per_company_and_a_super_admin_entry_of_one_company_is_not_usable_in_another(rig):
    sup = admin(rig)
    seller, _ = member(rig, "s@example.com")
    _create(rig, sup, seller)
    data = lambda: (rig.sql("SELECT id, title FROM cards ORDER BY id", slug="salcomp"),
                    rig.sql("SELECT COUNT(*) FROM undo_entries", slug="salcomp"))
    before = data()
    assert undo(rig, sup, slug="salcomp").status_code == 404  # its entry lives in lity's database only
    assert data() == before and _undo_available(rig, sup)


def test_ai_creation_by_non_deleters_is_not_undoable_but_the_super_admins_is(rig, monkeypatch):
    uid, token = company_admin(rig)
    seller, _ = member(rig, "s@example.com")
    local_row(rig, seller)
    fake_ai(monkeypatch, [{"type": "create", "column_id": 1, "title": "AI card", "salesperson_id": seller},
                          {"type": "update", "card_id": 1, "title": "Also edited"}])
    assert len(ai(rig, token).json()["actions_applied"]["successful"]) == 2
    assert not _undo_available(rig, token)  # no partial undo: the whole request is not offered
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'AI card'") == [(1,)]
    sup = admin(rig)
    fake_ai(monkeypatch, [{"type": "create", "column_id": 1, "title": "Super AI card", "salesperson_id": seller}])
    assert not ai(rig, sup).json()["actions_applied"]["failed"]
    assert _undo_available(rig, sup)
    assert undo(rig, sup).status_code == 200
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'Super AI card'") == [(0,)]


# --- eligibility of the restored salesperson (F5) ---------------------------

def test_undo_of_a_reassignment_needs_the_previous_salesperson_to_be_eligible(rig):
    _, token = company_admin(rig)
    old, _ = member(rig, "old@example.com")
    new, _ = member(rig, "new@example.com")
    for uid in (old, new):
        local_row(rig, uid)
    rig.assign(1, old)
    assert rig.call("put", "/cards/1", token, json={"salesperson_id": new}).status_code == 200
    assert undo(rig, token).status_code == 200 and owner(rig, 1) == old  # still eligible: restored
    assert rig.call("put", "/cards/1", token, json={"salesperson_id": new}).status_code == 200
    svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", old)
    assert undo(rig, token).status_code == 409
    assert owner(rig, 1) == new  # nothing restored
    assert undo(rig, token).status_code == 404  # the refused entry is used up, as for any refused undo


def test_undo_of_a_deletion_is_refused_when_the_restored_salesperson_is_ineligible(rig):
    sup = admin(rig)
    m, _ = member(rig, "m@example.com")
    local_row(rig, m)
    rig.assign(1, m)
    assert rig.call("delete", "/cards/1", sup).status_code == 204
    svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", m)
    assert undo(rig, sup).status_code == 409
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'card 1'") == [(0,)]
    assert undo(rig, sup).status_code == 404


def test_ai_assignment_uses_the_eligibility_read_after_the_model_call(rig, monkeypatch):
    import routes

    _, token = company_admin(rig)
    m, _ = member(rig, "m@example.com")
    local_row(rig, m)

    async def fake(board_state, history, message):
        # The person stops being eligible while the model is answering.
        svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", m)
        return {"success": True, "response": "ok", "actions": [{"type": "update", "card_id": 1, "salesperson_id": m}],
                "confidence": 1, "tokens_used": 0}

    monkeypatch.setattr(routes, "call_ai_with_board", fake)
    applied = ai(rig, token).json()["actions_applied"]
    assert applied["successful"] == [] and len(applied["failed"]) == 1
    assert owner(rig, 1) is None
