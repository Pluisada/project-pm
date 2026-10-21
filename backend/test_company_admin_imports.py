"""Bid imports (preview, confirm, list, read, revert) are Super Admin only; a company Admin is refused everywhere
(Phase 1 permission contract). An import can create cards and a revert can delete them.

Column deletion stays Super Admin only. Synthetic workbooks and tmp_path databases only.
"""
import pytest

import central_auth as ca
import identity_service as svc
from auth import create_access_token
from identity_models import IdentityCompany
from test_central_deps import GOOD
from test_company_admin_products import company_admin
from test_company_card_writes import admin, member
from test_company_products import send
from test_company_undo_import import confirm, imp, preview, rig, run_import  # noqa: F401  (fixture/helpers)

READS = [("get", "/boards/2/imports", {}), ("get", "/imports/1", {}), ("get", "/imports/1/revert-preview", {})]
WRITES = [("post", "/boards/2/imports/preview", {}), ("post", "/boards/2/imports", {}),
          ("post", "/imports/1/revert", {"json": {"plan_fingerprint": "x"}})]


def imported_cards(rig, slug="lity"):
    return rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10", slug=slug)[0][0]


def test_predicate_covers_super_admin_only():
    assert [svc.can_manage_imports(r) for r in ("super_admin", "admin", "sales_member", "visitor", "Admin", "")] == [
        True, False, False, False, False, False]


def test_company_admin_is_refused_every_import_route_before_the_db_opens(rig):
    _, token = company_admin(rig)
    before = rig.digest("lity")
    rig.opened.clear()
    for method, path, kw in READS + WRITES:
        assert imp(rig, method, path, token, **kw).status_code == 403, path
    assert rig.opened == [] and rig.digest("lity") == before
    assert imported_cards(rig) == 0


def test_super_admin_previews_confirms_lists_and_reads_an_import(rig):
    token = admin(rig)
    body = preview(rig, token).json()
    assert body["confirmable"] and body["preview_token"]
    result = confirm(rig, token, body["preview_token"])
    assert result.status_code == 201 and len(result.json()["created_card_ids"]) == 2
    assert rig.sql("SELECT user_id FROM import_batches") == [(rig.e.admin,)]
    listing = imp(rig, "get", "/boards/2/imports", token).json()
    assert listing["total"] == 1
    assert imp(rig, "get", f"/imports/{listing['items'][0]['id']}", token).status_code == 200
    assert imp(rig, "get", "/imports/999", token).status_code == 404  # existing error contract
    assert rig.sql("SELECT COUNT(*) FROM import_batches", slug="valor-log") == [(0,)]


def test_duplicate_confirmation_and_stale_preview_keep_their_protections(rig):
    token = admin(rig)
    body = preview(rig, token).json()
    assert confirm(rig, token, body["preview_token"]).status_code == 201
    assert confirm(rig, token, body["preview_token"]).status_code == 409  # already applied
    assert imported_cards(rig) == 2
    assert imp(rig, "post", "/boards/2/imports", token, data={"confirm": "true", "preview_token": "junk"},
               files={"file": ("x.xlsx", b"not a workbook", "application/octet-stream")}).status_code == 422
    assert imp(rig, "post", "/boards/2/imports/preview", token, files={"file": ("x.xlsx", b"junk", "application/octet-stream")},
               data={"decisions": "[]"}).status_code in (400, 415, 422)
    assert imported_cards(rig) == 2


def test_failed_import_rolls_back(rig, monkeypatch):
    import bid_import_service
    token = admin(rig)
    body = preview(rig, token).json()
    real = bid_import_service._write_rows
    def write_then_fail(*a, **k):
        real(*a, **k)  # rows are in the open transaction when it fails
        raise RuntimeError("synthetic failure after writes")
    monkeypatch.setattr(bid_import_service, "_write_rows", write_then_fail)
    assert confirm(rig, token, body["preview_token"]).status_code == 500
    assert imported_cards(rig) == 0 and rig.sql("SELECT COUNT(*) FROM import_batch_items") == [(0,)]
    assert rig.sql("SELECT COUNT(*) FROM import_batches WHERE status = 'completed'") == [(0,)]


def test_company_admin_cannot_revert_and_the_super_admin_revert_still_guards_later_edits(rig):
    _, admin_token = company_admin(rig)
    token = admin(rig)
    run_import(rig, token)
    batch = imp(rig, "get", "/boards/2/imports", token).json()["items"][0]["id"]
    plan = imp(rig, "get", f"/imports/{batch}/revert-preview", token)
    assert plan.status_code == 200
    fingerprint = plan.json()["plan_fingerprint"]
    # the Admin can neither see the plan nor revert (a revert deletes the imported cards)
    assert imp(rig, "get", f"/imports/{batch}/revert-preview", admin_token).status_code == 403
    assert imp(rig, "post", f"/imports/{batch}/revert", admin_token, json={"plan_fingerprint": fingerprint}).status_code == 403
    assert imported_cards(rig) == 2
    done = imp(rig, "post", f"/imports/{batch}/revert", token, json={"plan_fingerprint": fingerprint})
    assert done.status_code == 200 and imported_cards(rig) == 0
    assert imp(rig, "post", f"/imports/{batch}/revert", token, json={"plan_fingerprint": fingerprint}).status_code == 409
    run_import(rig, token)
    batch2 = imp(rig, "get", "/boards/2/imports", token).json()["items"][0]["id"]
    fingerprint = imp(rig, "get", f"/imports/{batch2}/revert-preview", token).json()["plan_fingerprint"]
    first = rig.sql("SELECT MIN(id) FROM cards WHERE column_id >= 10")[0][0]
    rig.sql("UPDATE cards SET title = 'Edited later' WHERE id = ?", (first,))
    blocked = imp(rig, "post", f"/imports/{batch2}/revert", token, json={"plan_fingerprint": fingerprint})
    assert blocked.status_code == 409
    assert rig.sql("SELECT title FROM cards WHERE id = ?", (first,)) == [("Edited later",)] and imported_cards(rig) == 2


def test_super_admin_import_and_revert_are_unchanged(rig):
    token = admin(rig)
    run_import(rig, token)
    batch = imp(rig, "get", "/boards/2/imports", token).json()["items"][0]["id"]
    fp = imp(rig, "get", f"/imports/{batch}/revert-preview", token).json()["plan_fingerprint"]
    assert imp(rig, "post", f"/imports/{batch}/revert", token, json={"plan_fingerprint": fp}).status_code == 200
    assert imported_cards(rig) == 0 and rig.sql("SELECT reverted_by_user_id FROM import_batch_ledgers") == [(rig.e.admin,)]


def test_admin_cannot_import_or_revert_in_another_company_even_as_a_member_there(rig):
    uid, _ = member(rig, "a@example.com", slug="lity")
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "admin")
    for slug, role in (("valor-log", "sales_member"), ("salcomp", "visitor")):
        invite = svc.create_invitation(rig.e.session, rig.e.admin, "a@example.com", slug, role).token
        ca.join_invitation(rig.e.session, invite, "a@example.com", GOOD)
    token = rig.e.token("a@example.com")
    body = preview(rig, admin(rig)).json()
    before = {s: rig.digest(s) for s in ("valor-log", "salcomp")}
    rig.opened.clear()
    for other in ("valor-log", "salcomp"):
        for method, path, kw in READS + WRITES:
            assert imp(rig, method, path, token, other, **kw).status_code == 403, (other, path)
    assert rig.opened == [] and {s: rig.digest(s) for s in before} == before
    # a preview made in its own company cannot be confirmed anywhere else, even where it holds a writer role
    assert confirm(rig, token, body["preview_token"], "valor-log").status_code == 403
    assert imported_cards(rig, "valor-log") == 0


def test_a_revert_cannot_name_another_companys_batch(rig):
    sup = admin(rig)
    run_import(rig, sup, "valor-log")  # a batch of valor-log
    batch = imp(rig, "get", "/boards/2/imports", sup, "valor-log").json()["items"][0]["id"]
    fp = imp(rig, "get", f"/imports/{batch}/revert-preview", sup, "valor-log").json()["plan_fingerprint"]
    _, token = company_admin(rig)
    assert imp(rig, "get", f"/imports/{batch}/revert-preview", token).status_code == 403  # a company Admin never reverts
    assert imp(rig, "post", f"/imports/{batch}/revert", token, json={"plan_fingerprint": fp}).status_code == 403
    assert imp(rig, "get", f"/imports/{batch}/revert-preview", sup).status_code == 404  # absent in lity, even for the Super Admin
    assert imp(rig, "post", f"/imports/{batch}/revert", token, "valor-log", json={"plan_fingerprint": fp}).status_code == 403
    assert imported_cards(rig, "valor-log") == 2


def test_company_admin_stays_refused_through_role_changes_and_an_inactive_company(rig):
    uid, token = company_admin(rig)
    body = preview(rig, admin(rig)).json()
    assert confirm(rig, token, body["preview_token"]).status_code == 403
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "sales_member")
    assert confirm(rig, token, body["preview_token"]).status_code == 403
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "admin")
    rig.e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=False)
    before = rig.digest("lity")
    rig.opened.clear()
    assert confirm(rig, token, body["preview_token"]).status_code == 403 and preview(rig, token).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before
    rig.e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=True)
    svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", uid)
    assert preview(rig, token).status_code == 403 and imported_cards(rig) == 0


@pytest.mark.parametrize("role", ["sales_member", "visitor"])
def test_sales_member_and_visitor_stay_refused_before_the_db_opens(rig, role):
    _, token = member(rig, f"{role}@example.com", role)
    before = rig.digest("lity")
    for method, path, kw in READS + WRITES:
        assert imp(rig, method, path, token, **kw).status_code == 403, path
    assert rig.opened == [] and rig.digest("lity") == before


def test_forged_and_stale_role_claims_grant_nothing(rig):
    uid, _ = member(rig)
    before = rig.digest("lity")
    for claims in ({"sub": str(uid), "role": "admin"}, {"sub": str(uid), "role": "super_admin", "is_global_admin": True}):
        legacy = create_access_token(claims)
        for method, path, kw in READS + WRITES:
            assert imp(rig, method, path, legacy, **kw).status_code == 401
    assert imp(rig, "get", "/boards/2/imports", None).status_code == 401
    assert rig.opened == [] and rig.digest("lity") == before


def test_admin_keeps_only_its_commercial_card_rights(rig):
    _, token = company_admin(rig)
    assert send(rig, "delete", "/boards/1/columns/1", token).status_code == 403
    assert send(rig, "post", "/products", token, json={"name": "Adapters"}).status_code == 403
    assert send(rig, "post", "/boards", token, json={"title": "B3"}).status_code == 403
    assert rig.call("put", "/cards/1", token, json={"title": "x"}).status_code == 200
    assert rig.call("delete", "/cards/2", token).status_code == 403


