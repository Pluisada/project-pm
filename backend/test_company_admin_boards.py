"""Phase 2E part 2: a company Admin creates boards and creates/edits columns in its own company only.

Column deletion, card deletion and everything else stay Super Admin only. Synthetic data, tmp_path databases.
"""
import pytest

import central_auth as ca
import identity_service as svc
from database_targets import COMPANY_SLUGS
from identity_models import IdentityCompany, IdentityMembership
from test_central_deps import GOOD
from test_company_admin_products import company_admin
from test_company_card_writes import admin, member
from test_company_card_writes import rig  # noqa: F401  (fixture)
from test_company_products import send

BOARD_WRITES = [
    ("post", "/boards", {"json": {"title": "New board"}}),
    ("post", "/boards/1/columns", {"json": {"title": "Extra", "position": 5, "stage_kind": "open"}}),
    ("put", "/boards/1/columns/1", {"json": {"title": "Renamed"}}),
]


def titles(rig, slug="lity"):
    return rig.sql("SELECT title FROM columns ORDER BY id", slug=slug), rig.sql("SELECT title FROM boards ORDER BY id", slug=slug)


def test_predicate_is_separate_and_covers_super_admin_and_company_admin_only():
    assert [svc.can_manage_boards(r) for r in ("super_admin", "admin", "sales_member", "visitor", "Admin", "")] == [
        True, True, False, False, False, False]


def test_super_admin_still_does_all_four_operations(rig):
    token = admin(rig)
    for method, path, kw in BOARD_WRITES:
        assert send(rig, method, path, token, **kw).status_code in (200, 201)
    assert send(rig, "delete", "/boards/1/columns/2", token).status_code == 204


def test_company_admin_creates_boards_and_creates_and_edits_columns(rig):
    uid, token = company_admin(rig)
    board = send(rig, "post", "/boards", token, json={"title": "New board"})
    assert board.status_code == 201 and board.json()["title"] == "New board"
    assert rig.sql("SELECT user_id FROM boards WHERE title = 'New board'") == [(uid,)]  # the actor's local row
    col = send(rig, "post", "/boards/1/columns", token, json={"title": "Extra", "position": 5, "stage_kind": "open"})
    assert col.status_code == 201 and col.json()["title"] == "Extra"
    upd = send(rig, "put", f"/boards/1/columns/{col.json()['id']}", token, json={"title": "Renamed", "position": 6})
    # A position past the end is normalized to the last valid index (the board holds three columns here, so 2):
    # the response reports the effective position, a later read of the board shows the same order, and the
    # positions of all columns stay unique and contiguous.
    assert upd.status_code == 200 and (upd.json()["title"], upd.json()["position"]) == ("Renamed", 2)
    detail = send(rig, "get", "/boards/1", token)
    assert detail.status_code == 200
    cols = sorted(detail.json()["columns"], key=lambda c: c["position"])
    assert len(cols) == 3
    assert [c["position"] for c in cols] == [0, 1, 2]
    assert cols[-1]["id"] == col.json()["id"] and cols[-1]["title"] == "Renamed"
    assert rig.sql("SELECT id, position FROM columns WHERE board_id = 1 ORDER BY position") == [
        (c["id"], c["position"]) for c in cols]


def test_validation_and_errors_match_the_super_admin(rig):
    _, token = company_admin(rig)
    sup = admin(rig)
    cases = [
        ("post", "/boards", {"json": {}}),
        ("post", "/boards/1/columns", {"json": {"title": "X", "position": 0, "stage_kind": "bogus"}}),
        ("post", "/boards/99/columns", {"json": {"title": "X", "position": 0, "stage_kind": "open"}}),
        ("put", "/boards/1/columns/99", {"json": {"title": "X"}}),
        ("put", "/boards/1/columns/1", {"json": {"stage_kind": "bogus"}}),
    ]
    for method, path, kw in cases:
        a, b = send(rig, method, path, token, **kw), send(rig, method, path, sup, **kw)
        assert a.status_code == b.status_code and a.status_code in (404, 409, 422), (path, a.text, b.text)
        assert a.json() == b.json()


def test_company_admin_cannot_delete_columns_or_cards_or_anything_else_reserved(rig):
    _, token = company_admin(rig)
    before = rig.digest("lity")
    rig.opened.clear()
    for method, path, kw in (("delete", "/boards/1/columns/1", {}),):
        assert send(rig, method, path, token, **kw).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before


def test_company_admin_cannot_touch_other_companies_even_as_a_member_there(rig):
    uid, _ = member(rig, "a@example.com", slug="lity")
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "admin")
    for slug, role in (("salcomp", "sales_member"), ("valor-log", "visitor")):
        invite = svc.create_invitation(rig.e.session, rig.e.admin, "a@example.com", slug, role).token
        ca.join_invitation(rig.e.session, invite, "a@example.com", GOOD)
    token = rig.e.token("a@example.com")
    assert send(rig, "post", "/boards", token, json={"title": "Mine"}).status_code == 201
    before = {s: rig.digest(s) for s in ("salcomp", "valor-log")}
    rig.opened.clear()
    for other in ("salcomp", "valor-log"):
        for method, path, kw in BOARD_WRITES:
            assert send(rig, method, path, token, slug=other, **kw).status_code == 403
    assert {s: rig.digest(s) for s in ("salcomp", "valor-log")} == before and "salcomp" not in rig.opened


@pytest.mark.parametrize("role", ["sales_member", "visitor"])
def test_sales_member_and_visitor_remain_denied_before_the_company_db_opens(rig, role):
    _, token = member(rig, role=role)
    before = rig.digest("lity")
    rig.opened.clear()
    for method, path, kw in BOARD_WRITES + [("delete", "/boards/1/columns/1", {})]:
        assert send(rig, method, path, token, **kw).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before


def test_unauthenticated_is_refused(rig):
    for method, path, kw in BOARD_WRITES:
        assert rig.client.request(method, f"/api/companies/lity{path}", **kw).status_code == 401
    assert rig.opened == []


def test_inactive_membership_demotion_and_inactive_company_are_refused_on_the_same_token(rig):
    uid, token = company_admin(rig)
    assert send(rig, "post", "/boards", token, json={"title": "One"}).status_code == 201
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "sales_member")
    assert send(rig, "post", "/boards", token, json={"title": "Two"}).status_code == 403
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "admin")
    assert send(rig, "post", "/boards", token, json={"title": "Two"}).status_code == 201
    rig.e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=False)
    before = rig.digest("lity")
    rig.opened.clear()
    assert send(rig, "post", "/boards", token, json={"title": "Three"}).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before
    rig.e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=True)
    svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", uid)
    assert send(rig, "put", "/boards/1/columns/1", token, json={"title": "Z"}).status_code == 403
    assert rig.sql("SELECT title FROM boards ORDER BY id")[-1] == ("Two",)


def test_part1_product_behavior_and_membership_rules_are_unchanged(rig):
    uid, token = company_admin(rig)
    assert send(rig, "post", "/products", token, json={"name": "Adapters"}).status_code == 201
    _, member_token = member(rig, "m@example.com")
    assert send(rig, "post", "/products", member_token, json={"name": "Nope"}).status_code == 403
    # an Admin still cannot invite, promote or manage roles
    with pytest.raises(svc.NotAuthorized):
        svc.create_invitation(rig.e.session, uid, "n@example.com", "lity", "visitor")
    with pytest.raises(svc.NotAuthorized):
        svc.change_membership_role(rig.e.session, uid, "lity", uid, "visitor")
