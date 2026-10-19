"""Phase 2E part 1: a company Admin writes the product catalog of its own company, nothing else.

Synthetic identities and disposable tmp_path databases only.
"""
import pytest

import central_auth as ca
import identity_service as svc
from auth import create_access_token
from identity_models import IdentityCompany
from database_targets import COMPANY_SLUGS
from test_company_card_writes import admin, member
from test_company_card_writes import rig  # noqa: F401  (fixture)
from test_central_deps import GOOD
from test_company_products import DEFAULTS, names, send

PRODUCT_WRITES = [
    ("post", "/products", {"json": {"name": "X"}}),
    ("put", "/products/1", {"json": {"name": "X"}}),
    ("delete", "/products/1", {}),
]


def company_admin(rig, email="a@example.com", slug="lity"):
    uid, _ = member(rig, email, slug=slug)
    svc.change_membership_role(rig.e.session, rig.e.admin, slug, uid, "admin")
    return uid, rig.e.token(email)  # a fresh token: the role is never in it


def test_predicate_covers_super_admin_and_company_admin_only():
    assert [svc.can_manage_products(r) for r in ("super_admin", "admin", "sales_member", "visitor", "", "Admin")] == [
        True, True, False, False, False, False]


def test_company_admin_creates_renames_and_deletes_in_its_company(rig):
    _, token = company_admin(rig)
    pid = send(rig, "post", "/products", token, json={"name": "Adapters"}).json()["id"]
    assert send(rig, "put", f"/products/{pid}", token, json={"name": "Cables"}).json()["name"] == "Cables"
    assert send(rig, "delete", f"/products/{pid}", token).status_code == 204
    assert names(rig, token) == DEFAULTS


def test_company_admin_cannot_write_products_of_another_company(rig):
    _, token = company_admin(rig)
    before = {s: rig.digest(s) for s in COMPANY_SLUGS}
    rig.opened.clear()
    for method, path, kw in PRODUCT_WRITES + [("get", "/products", {})]:
        for other in ("salcomp", "valor-log"):
            assert send(rig, method, path, token, slug=other, **kw).status_code == 403
    assert rig.opened == [] and {s: rig.digest(s) for s in COMPANY_SLUGS} == before


def test_admin_in_one_company_does_not_help_in_another_where_it_is_a_member(rig):
    uid, _ = member(rig, "a@example.com", slug="lity")
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "admin")
    invite = svc.create_invitation(rig.e.session, rig.e.admin, "a@example.com", "salcomp", "sales_member").token
    ca.join_invitation(rig.e.session, invite, "a@example.com", GOOD)
    token = rig.e.token("a@example.com")
    assert send(rig, "post", "/products", token, json={"name": "A"}).status_code == 201
    assert send(rig, "post", "/products", token, slug="salcomp", json={"name": "A"}).status_code == 403
    assert names(rig, token, "salcomp") == DEFAULTS


@pytest.mark.parametrize("role", ["sales_member", "visitor"])
def test_sales_member_and_visitor_remain_refused_before_the_company_db_opens(rig, role):
    _, token = member(rig, role=role)
    before = rig.digest("lity")
    rig.opened.clear()
    for method, path, kw in PRODUCT_WRITES:
        assert send(rig, method, path, token, **kw).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before


def test_demotion_or_deactivation_takes_effect_on_the_same_token(rig):
    uid, token = company_admin(rig)
    assert send(rig, "post", "/products", token, json={"name": "A"}).status_code == 201
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "sales_member")
    assert send(rig, "post", "/products", token, json={"name": "B"}).status_code == 403
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "admin")
    assert send(rig, "post", "/products", token, json={"name": "B"}).status_code == 201
    svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", uid)
    assert send(rig, "post", "/products", token, json={"name": "C"}).status_code == 403
    assert names(rig, rig.e.token("admin@example.com")) == DEFAULTS + ["A", "B"]


def test_inactive_company_refuses_its_admin(rig):
    _, token = company_admin(rig)
    rig.e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=False)
    before = rig.digest("lity")
    rig.opened.clear()
    assert send(rig, "post", "/products", token, json={"name": "A"}).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before


def test_unauthenticated_and_legacy_role_claim_tokens_are_refused(rig):
    uid, _ = member(rig, "m@example.com")
    assert rig.client.post("/api/companies/lity/products", json={"name": "A"}).status_code == 401
    legacy = create_access_token({"sub": str(uid), "role": "admin"})  # a claimed role is never read
    assert send(rig, "post", "/products", legacy, json={"name": "A"}).status_code == 401
    assert names(rig, rig.e.token("admin@example.com")) == DEFAULTS


# Still Super Admin only after part 2 (boards and column create/edit moved to test_company_admin_boards.py).
NOT_YET = [
    ("delete", "/boards/1/columns/1", {}),
]


@pytest.mark.parametrize("method,path,kw", NOT_YET)
def test_company_admin_gains_nothing_else_yet(rig, method, path, kw):
    _, token = company_admin(rig)
    before = rig.digest("lity")
    rig.opened.clear()
    assert send(rig, method, path, token, **kw).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before
