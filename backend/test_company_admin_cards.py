"""A company Admin assigns/reassigns, edits any card and moves backward in its own company; deleting cards is Super Admin only
(Phase 1 permission contract).

Column deletion is not enabled for it (AI/undo: test_company_admin_ai_undo.py; imports: test_company_admin_imports.py). Synthetic data, tmp_path databases only.
"""
import json
from types import SimpleNamespace

import pytest

import card_rules
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


def call(rig, method, path, token, slug="lity", **kw):
    return rig.call(method, path, token, slug=slug, **kw)


def test_predicates_manage_cards_covers_super_admin_and_company_admin_but_delete_only_super_admin():
    roles = ("super_admin", "admin", "sales_member", "visitor", "Admin", "")
    assert [svc.can_manage_cards(r) for r in roles] == [True, True, False, False, False, False]
    assert [svc.can_delete_cards(r) for r in roles] == [True, False, False, False, False, False]


def test_company_admin_assigns_reassigns_edits_any_card_with_audit_and_no_member_history(rig):
    aid, token = company_admin(rig)
    m1, _ = member(rig, "m1@example.com")
    m2, _ = member(rig, "m2@example.com")
    local_row(rig, m1, "one"), local_row(rig, m2, "two")
    assert call(rig, "put", "/cards/1", token, json={"salesperson_id": m1}).status_code == 200
    assert call(rig, "put", "/cards/1", token, json={"salesperson_id": m2}).json()["salesperson_id"] == m2  # reassign
    assert call(rig, "put", "/cards/1", token, json={"title": "Edited"}).status_code == 200  # someone else's card
    assert rig.sql("SELECT action, actor_user_id FROM audit_events WHERE card_id = 1 ORDER BY id") == [
        ("assign", aid), ("assign", aid), ("edit", aid)]  # the title edit of m2's card is audited (M2)
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(0,)]  # admin edits never set the member badge


def test_assignment_validation_is_unchanged_for_the_company_admin(rig):
    aid, token = company_admin(rig)
    visitor, _ = member(rig, "v@example.com", role="visitor")
    outsider, _ = member(rig, "o@example.com", slug="valor-log")
    lapsed, _ = member(rig, "l@example.com")
    other_admin, _ = company_admin(rig, "a2@example.com")
    svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", lapsed)
    no_row, _ = member(rig, "n@example.com")  # eligible centrally, no local row
    if no_row == 7:  # id 7 is the seeded local user "ana"; use a user without any local row
        no_row, _ = member(rig, "n2@example.com")
    for uid in (visitor, outsider, lapsed, other_admin, 9999):  # the Admin's own id is accepted: test_company_admin_self_assignment.py
        local_row(rig, uid) if uid != 9999 else None
        r = call(rig, "put", "/cards/1", token, json={"salesperson_id": uid})
        assert r.status_code == 422, (uid, r.text)
    assert rig.sql("SELECT salesperson_id FROM cards WHERE id = 1") == [(None,)]
    # A centrally eligible person without a local row is accepted: the edit creates the local reference first, as card
    # creation does (correction batch 1). Only ineligible ids (above) are refused.
    assert rig.sql("SELECT COUNT(*) FROM users WHERE id = ?", (no_row,)) == [(0,)]
    r = call(rig, "put", "/cards/1", token, json={"salesperson_id": no_row})
    assert r.status_code == 200, r.text
    assert r.json()["salesperson_id"] == no_row
    assert rig.sql("SELECT COUNT(*) FROM users WHERE id = ?", (no_row,)) == [(1,)]
    assert rig.sql("SELECT salesperson_id FROM cards WHERE id = 1") == [(no_row,)]
    # the central eligibility rule is unchanged (company Admins are not salespeople); only the caller's own id is added
    # by the company routes (assignable_ids_for)
    assert aid not in svc.assignable_user_ids(rig.e.session, "lity")


def test_company_admin_moves_backward_and_any_card(rig):
    _, token = company_admin(rig)
    m, _ = member(rig, "m@example.com")
    local_row(rig, m)
    rig.assign(1, m)
    assert call(rig, "put", "/cards/1/position", token, json={"column_id": 2, "position": 0}).status_code == 200
    assert call(rig, "put", "/cards/1/position", token, json={"column_id": 1, "position": 0}).status_code == 200  # back
    assert rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(1,)]
    assert call(rig, "put", "/cards/1/position", token, json={"column_id": 99}).status_code == 404  # same validation
    events = rig.sql("SELECT event_type FROM card_stage_events WHERE card_id = 1 ORDER BY id")
    assert events.count(("moved",)) == 2


def test_company_admin_cannot_delete_cards_even_by_direct_api_before_the_company_db_opens(rig):
    aid, token = company_admin(rig)
    before = rig.digest("lity")
    rig.opened.clear()
    assert call(rig, "delete", "/cards/1", token).status_code == 403
    assert call(rig, "delete", "/cards/999", token).status_code == 403  # same answer whether or not the card exists
    assert rig.opened == [] and rig.digest("lity") == before
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(1,)]
    assert rig.sql("SELECT COUNT(*) FROM audit_events") == [(0,)]
    assert send(rig, "delete", "/boards/1/columns/1", token).status_code == 403  # column deletion stays reserved
    assert rig.sql("SELECT COUNT(*) FROM columns") == [(2,)]


def test_super_admin_deletes_a_card_and_it_is_audited(rig):
    token = admin(rig)
    assert call(rig, "delete", "/cards/1", token).status_code == 204
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(0,)]
    assert rig.sql("SELECT action FROM audit_events") == [("delete",)]
    assert call(rig, "delete", "/cards/1", token).status_code == 404


def test_super_admin_behavior_is_unchanged(rig):
    token = admin(rig)
    m, _ = member(rig)
    local_row(rig, m)
    assert call(rig, "put", "/cards/1", token, json={"salesperson_id": m}).status_code == 200
    assert call(rig, "put", "/cards/1/position", token, json={"column_id": 2}).status_code == 200
    assert call(rig, "put", "/cards/1/position", token, json={"column_id": 1}).status_code == 200
    assert call(rig, "delete", "/cards/1", token).status_code == 204


def test_company_admin_gets_nothing_in_other_companies_even_as_a_member_there(rig):
    uid, _ = member(rig, "a@example.com", slug="lity")
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "admin")
    for slug, role in (("salcomp", "sales_member"), ("valor-log", "visitor")):
        invite = svc.create_invitation(rig.e.session, rig.e.admin, "a@example.com", slug, role).token
        ca.join_invitation(rig.e.session, invite, "a@example.com", GOOD)
    token = rig.e.token("a@example.com")
    def data(slug):  # a writer's own local reference row may be synced; no card or audit data may change
        return rig.sql("SELECT id, column_id, position, salesperson_id, title FROM cards ORDER BY id", slug=slug), \
            rig.sql("SELECT COUNT(*) FROM audit_events", slug=slug)

    before = {s: data(s) for s in ("salcomp", "valor-log")}
    before_digest = rig.digest("valor-log")  # a Visitor there: not even a reference row
    rig.opened.clear()
    for other in ("salcomp", "valor-log"):
        assert call(rig, "delete", "/cards/1", token, slug=other).status_code == 403
        assert call(rig, "put", "/cards/1/position", token, slug=other, json={"column_id": 2}).status_code == 403
        assert call(rig, "put", "/cards/1", token, slug=other, json={"salesperson_id": uid}).status_code in (403, 422)
    assert {s: data(s) for s in before} == before and rig.digest("valor-log") == before_digest
    assert call(rig, "put", "/cards/2/position", token, json={"column_id": 2}).status_code == 200  # still fine in its own company
    assert call(rig, "delete", "/cards/2", token).status_code == 403  # but never a deletion


def test_demotion_deactivation_and_inactive_company_take_effect_on_the_same_token(rig):
    uid, token = company_admin(rig)
    assert call(rig, "put", "/cards/1/position", token, json={"column_id": 2}).status_code == 200
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "sales_member")
    assert call(rig, "put", "/cards/1/position", token, json={"column_id": 1}).status_code == 403  # no longer back-moving
    assert call(rig, "put", "/cards/2/position", token, json={"column_id": 2}).status_code == 403  # unassigned: member rule
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "admin")
    rig.e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=False)
    before = rig.digest("lity")
    rig.opened.clear()
    assert call(rig, "put", "/cards/2/position", token, json={"column_id": 2}).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before
    rig.e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=True)
    svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", uid)
    assert call(rig, "put", "/cards/2/position", token, json={"column_id": 2}).status_code == 403
    assert rig.sql("SELECT column_id FROM cards WHERE id = 2") == [(1,)]


def test_sales_member_keeps_every_restriction(rig):
    uid, token = member(rig)
    other, _ = member(rig, "o@example.com")
    local_row(rig, other)
    rig.assign(1, uid)
    rig.assign(2, other)
    assert call(rig, "put", "/cards/1", token, json={"salesperson_id": other}).status_code == 403  # no reassign
    assert call(rig, "put", "/cards/2", token, json={"title": "x"}).status_code == 403  # not theirs
    assert call(rig, "put", "/cards/2/position", token, json={"column_id": 2}).status_code == 403
    assert call(rig, "put", "/cards/1/position", token, json={"column_id": 2, "position": 0}).status_code == 200  # forward
    assert call(rig, "put", "/cards/1/position", token, json={"column_id": 1}).status_code == 403  # backward
    before = rig.digest("lity")
    rig.opened.clear()
    assert call(rig, "delete", "/cards/1", token).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before


def test_visitor_stays_read_only(rig):
    uid, token = member(rig, "v@example.com", role="visitor")
    rig.assign(1, uid)  # even a card assigned to it
    before = rig.digest("lity")
    rig.opened.clear()
    for method, path, kw in (("put", "/cards/1", {"json": {"salesperson_id": uid}}),
                             ("put", "/cards/1/position", {"json": {"column_id": 2}}),
                             ("delete", "/cards/1", {})):
        assert call(rig, method, path, token, **kw).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before


def test_stale_or_malformed_role_claims_grant_nothing(rig):
    uid, _ = member(rig)
    before = rig.digest("lity")
    for claims in ({"sub": str(uid), "role": "admin"}, {"sub": str(uid), "role": "super_admin", "is_global_admin": True},
                   {"sub": "not-a-number", "role": "admin"}):
        legacy = create_access_token(claims)
        assert call(rig, "delete", "/cards/1", legacy).status_code == 401
    assert call(rig, "delete", "/cards/1", "garbage").status_code == 401
    assert rig.digest("lity") == before and rig.opened == []


def test_card_rules_defaults_and_legacy_are_unchanged():
    ctx_admin = card_rules.is_admin(SimpleNamespace(is_global_admin=False, role="admin"))
    assert ctx_admin is False and card_rules.is_admin(SimpleNamespace(role="admin")) is True
    assert card_rules.is_admin(SimpleNamespace(role="member")) is False
    legacy_member = SimpleNamespace(id=5, role="member")
    card = SimpleNamespace(salesperson_id=9, column=SimpleNamespace(position=1))
    assert card_rules.move_denial(legacy_member, card, 0) is not None  # member rules, no override
    assert card_rules.move_denial(SimpleNamespace(id=1, role="admin"), card, 0) is None  # legacy admin
    # an explicit company decision is what lifts it, never the role string
    ctx = SimpleNamespace(id=5, role="admin", is_global_admin=False)
    assert card_rules.move_denial(ctx, card, 0) is not None and card_rules.move_denial(ctx, card, 0, admin=True) is None


def test_company_admin_cannot_administer_products_or_boards_or_columns(rig):
    _, token = company_admin(rig)
    assert send(rig, "post", "/products", token, json={"name": "Adapters"}).status_code == 403
    assert send(rig, "post", "/boards", token, json={"title": "B2"}).status_code == 403
    assert send(rig, "post", "/boards/1/columns", token, json={"title": "C", "position": 0}).status_code == 403
    assert send(rig, "put", "/boards/1/columns/1", token, json={"title": "C"}).status_code == 403
    assert send(rig, "delete", "/boards/1/columns/1", token).status_code == 403
