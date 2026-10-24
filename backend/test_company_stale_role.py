"""A session opened before a role change never keeps the old role's card privileges.

The company context is resolved from the central database on every request (token -> user -> active membership and its
CURRENT role); nothing about the role is stored in the token or in a session. These tests reuse the very same bearer
token after the Super Admin demotes, deactivates or promotes the member, and check that every card-mutation route
(edit, create, move, undo, AI) is judged on the current role, and that a refused request changes nothing. Synthetic
data, temporary databases only.
"""
import pytest

import identity_service as svc
from test_company_admin_products import company_admin
from test_company_card_writes import admin, member
from test_company_card_writes import ai, fake_ai, rig  # noqa: F401  (fixtures/helpers)
from test_company_undo_import import undo

SNAPSHOT = (
    "SELECT * FROM cards ORDER BY id", "SELECT * FROM card_versions ORDER BY card_id",
    "SELECT * FROM card_stage_events ORDER BY id", "SELECT * FROM audit_events ORDER BY id",
    "SELECT * FROM card_edit_history ORDER BY id", "SELECT * FROM undo_entries ORDER BY id",
    "SELECT * FROM card_review_obligations ORDER BY id", "SELECT * FROM card_loss_events ORDER BY id",
)


def snapshot(rig):
    return [rig.sql(q) for q in SNAPSHOT]


def demote(rig, uid, role="visitor"):
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, role)


@pytest.fixture
def world(rig):
    """A Sales Member owning card 1 (card 2 unassigned), with a token issued BEFORE any role change."""
    uid, mtoken = member(rig)
    rig.assign(1, uid)
    return rig, uid, mtoken


def mutations(rig, token):
    """Every way a card is changed for one board, with the same token: name -> status code."""
    return {
        "edit": rig.call("put", "/cards/1", token, json={"title": "changed by stale session"}).status_code,
        "create": rig.call("post", "/cards", token, json={"column_id": 1, "title": "new by stale session"}).status_code,
        "move": rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0}).status_code,
        "undo": undo(rig, token).status_code,
    }


def test_the_same_token_edits_and_moves_before_the_demotion(world):
    rig, _, mtoken = world
    assert rig.call("put", "/cards/1", mtoken, json={"title": "ok"}).status_code == 200
    assert rig.call("put", "/cards/1/position", mtoken, json={"column_id": 2, "position": 0}).status_code == 200


def test_a_demoted_member_is_refused_on_every_card_mutation_with_its_old_token(world, monkeypatch):
    rig, uid, mtoken = world
    assert rig.call("put", "/cards/1", mtoken, json={"title": "before"}).status_code == 200  # same token, still a member
    demote(rig, uid, "visitor")
    before = snapshot(rig)
    results = mutations(rig, mtoken)
    assert results == {"edit": 403, "create": 403, "move": 403, "undo": 403}, results
    fake_ai(monkeypatch, [{"type": "update", "card_id": 1, "title": "AI edit"}, {"type": "move", "card_id": 1, "column_id": 2}])
    assert ai(rig, mtoken).status_code == 403
    assert snapshot(rig) == before  # card data, stage, version, movement history, audit and undo entries untouched
    assert rig.sql("SELECT title, column_id FROM cards WHERE id = 1") == [("before", 1)]


def test_refusals_say_access_denied_and_reveal_nothing_about_the_card(world):
    rig, uid, mtoken = world
    demote(rig, uid, "visitor")
    for r in (rig.call("put", "/cards/1", mtoken, json={"title": "x"}),
              rig.call("put", "/cards/1/position", mtoken, json={"column_id": 2, "position": 0}),
              rig.call("put", "/cards/999", mtoken, json={"title": "x"})):  # an unknown card is refused the same way
        assert r.status_code == 403 and r.json() == {"detail": "Access denied"}


def test_a_demoted_member_can_still_read_but_the_capabilities_now_say_read_only(world):
    rig, uid, mtoken = world
    demote(rig, uid, "visitor")
    me = rig.client.get("/api/companies/lity/me", headers={"Authorization": f"Bearer {mtoken}"})
    assert me.status_code == 200 and me.json()["capabilities"]["readOnly"] is True
    assert rig.call("get", "", mtoken).status_code == 200


def test_a_deactivated_member_is_refused_with_its_old_token(world):
    rig, uid, mtoken = world
    svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", uid)
    before = snapshot(rig)
    assert mutations(rig, mtoken) == {"edit": 403, "create": 403, "move": 403, "undo": 403}
    assert snapshot(rig) == before


def test_an_undo_entry_made_while_authorized_cannot_be_used_after_the_demotion(world):
    rig, uid, mtoken = world
    assert rig.call("put", "/cards/1", mtoken, json={"title": "edited while a member"}).status_code == 200
    entries = rig.sql("SELECT COUNT(*) FROM undo_entries")
    demote(rig, uid, "visitor")
    assert undo(rig, mtoken).status_code == 403
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("edited while a member",)]
    assert rig.sql("SELECT COUNT(*) FROM undo_entries") == entries  # the entry is neither applied nor consumed


def test_a_member_whose_role_is_unchanged_keeps_editing_and_moving(world):
    rig, uid, mtoken = world
    other, _ = member(rig, "other@example.com")
    demote(rig, other, "visitor")  # someone else's role change does not affect this member
    assert rig.call("put", "/cards/1", mtoken, json={"title": "still allowed"}).status_code == 200
    assert rig.call("put", "/cards/1/position", mtoken, json={"column_id": 2, "position": 0}).status_code == 200
    assert rig.call("put", "/cards/2", mtoken, json={"title": "not mine"}).status_code == 403  # the ownership rule is unchanged


def test_a_promotion_takes_effect_on_the_next_request_with_the_same_token(rig):
    uid, vtoken = member(rig, "v@example.com", role="visitor")
    rig.assign(1, uid)
    assert rig.call("put", "/cards/1", vtoken, json={"title": "no"}).status_code == 403
    demote(rig, uid, "sales_member")  # a visitor becomes a member
    assert rig.call("put", "/cards/1", vtoken, json={"title": "yes"}).status_code == 200


def test_a_demoted_admin_loses_the_admin_card_rights_at_once(rig):
    aid, atoken = company_admin(rig)
    assert rig.call("put", "/cards/2", atoken, json={"title": "admin edits any card"}).status_code == 200
    demote(rig, aid, "sales_member")
    before = snapshot(rig)
    assert rig.call("put", "/cards/2", atoken, json={"title": "no longer"}).status_code == 403  # not assigned to it
    assert snapshot(rig) == before
    demote(rig, aid, "visitor")
    assert rig.call("put", "/cards/2", atoken, json={"title": "no longer"}).status_code == 403
    assert snapshot(rig) == before


def test_the_super_admin_and_a_current_admin_are_not_affected(world):
    rig, uid, mtoken = world
    aid, atoken = company_admin(rig)
    demote(rig, uid, "visitor")  # unrelated role change in the same company
    assert rig.call("put", "/cards/1", admin(rig), json={"title": "super admin"}).status_code == 200
    assert rig.call("put", "/cards/1", atoken, json={"title": "admin"}).status_code == 200
    assert rig.call("put", "/cards/1/position", atoken, json={"column_id": 2, "position": 0}).status_code == 200
    assert undo(rig, atoken).status_code == 200


def test_a_role_change_in_one_company_never_changes_another(rig):
    uid, token = member(rig, "multi@example.com")  # sales member of lity
    other_invite = svc.create_invitation(rig.e.session, rig.e.admin, "multi@example.com", "valor-log", "sales_member").token
    svc.accept_invitation(rig.e.session, other_invite, user_id=uid)  # the same person joins a second company
    demote(rig, uid, "visitor")  # lity only
    assert rig.call("put", "/cards/1", token, json={"title": "x"}).status_code == 403
    assert rig.client.get("/api/companies/valor-log/me", headers={"Authorization": f"Bearer {token}"}).json()["capabilities"]["readOnly"] is False
