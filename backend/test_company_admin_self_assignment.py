"""A company Admin may assign a card to themself (on creation, on the edit form, and in the lead options), and only to
themself: other Admins, Visitors, lapsed members and people of another company stay refused. The original creator is
the `create` audit actor, whoever the card is assigned to. Synthetic data, tmp_path databases only."""
import pytest

import identity_service as svc
from test_company_admin_cards import call, company_admin, local_row
from test_company_card_writes import member
from test_company_card_writes import ai, fake_ai, rig  # noqa: F401  (fixtures)


def create(rig, token, salesperson_id, slug="lity", column_id=1):
    return call(rig, "post", "/cards", token, slug=slug,
                json={"column_id": column_id, "title": "new lead", "position": 0, "salesperson_id": salesperson_id})


def options(rig, token, slug="lity"):
    r = rig.client.get(f"/api/companies/{slug}/lead-options", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return {p["id"] for p in r.json()["salespeople"]}


def audit(rig, card_id, slug="lity"):
    return rig.sql("SELECT action, actor_user_id FROM audit_events WHERE card_id = ? ORDER BY id", (card_id,), slug)


def test_company_admin_creates_a_card_assigned_to_themself_in_the_first_stage(rig):
    aid, token = company_admin(rig)
    r = create(rig, token, aid)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["salesperson_id"] == aid
    # the local reference was created for this company only, with the caller's central id
    assert rig.sql("SELECT COUNT(*) FROM users WHERE id = ?", (aid,), "lity") == [(1,)]
    assert rig.sql("SELECT COUNT(*) FROM users WHERE id = ?", (aid,), "valor-log") == [(0,)]
    assert rig.sql("SELECT salesperson_id, column_id FROM cards WHERE id = ?", (body["id"],)) == [(aid, 1)]


def test_original_creator_and_assignment_are_both_audited_for_the_pareto(rig):
    aid, token = company_admin(rig)
    card_id = create(rig, token, aid).json()["id"]
    assert audit(rig, card_id) == [("create", aid), ("assign", aid)]
    # an Admin creating for a salesperson still is the creator of record
    m, _ = member(rig, "m@example.com")
    other = create(rig, token, m).json()["id"]
    assert audit(rig, other) == [("create", aid), ("assign", aid)]
    from reports import build_lead_generation
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from database_targets import company_database_path
    engine = create_engine(f"sqlite:///{company_database_path('lity', rig.e.env)}")
    with sessionmaker(bind=engine)() as db:
        result = build_lead_generation(db, 1)
    assert [(r["salesperson_id"], r["count"]) for r in result["rows"]] == [(aid, 2)]  # credit follows the creator, not the owner


def test_other_ids_are_still_refused_on_creation(rig):
    aid, token = company_admin(rig)
    visitor, _ = member(rig, "v@example.com", role="visitor")
    outsider, _ = member(rig, "o@example.com", slug="valor-log")
    lapsed, _ = member(rig, "l@example.com")
    other_admin, _ = company_admin(rig, "a2@example.com")
    svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", lapsed)
    for uid in (visitor, outsider, lapsed, other_admin, 9999, None):
        r = create(rig, token, uid)
        assert r.status_code == 422, (uid, r.text)
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'new lead'") == [(0,)]


def test_an_admin_of_another_company_cannot_assign_to_themself_here(rig):
    other_admin, token = company_admin(rig, "ao@example.com", slug="valor-log")
    r = create(rig, token, other_admin, slug="lity")
    assert r.status_code in (401, 403, 404), r.text
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'new lead'") == [(0,)]


def test_a_sales_member_still_cannot_choose_the_owner(rig):
    mid, token = member(rig, "m@example.com")
    other, _ = member(rig, "n@example.com")
    local_row(rig, mid)
    r = create(rig, token, other)
    assert r.status_code == 201 and r.json()["salesperson_id"] == mid  # the client-supplied id is ignored


def test_lead_options_offer_the_admin_themself_but_not_other_admins(rig):
    aid, token = company_admin(rig)
    other_admin, _ = company_admin(rig, "a2@example.com")
    m, _ = member(rig, "m@example.com")
    local_row(rig, m, "m@example.com")
    ids = options(rig, token)
    assert aid in ids and m in ids and other_admin not in ids
    # a member's view is unchanged: no admin in the list
    _, member_token = member(rig, "m2@example.com")
    assert aid not in options(rig, member_token)


def test_admin_can_assign_an_existing_card_to_themself_but_not_to_another_admin(rig):
    aid, token = company_admin(rig)
    other_admin, _ = company_admin(rig, "a2@example.com")
    local_row(rig, other_admin)
    assert call(rig, "put", "/cards/1", token, json={"salesperson_id": other_admin}).status_code == 422
    r = call(rig, "put", "/cards/1", token, json={"salesperson_id": aid})
    assert r.status_code == 200, r.text
    assert rig.sql("SELECT salesperson_id FROM cards WHERE id = 1") == [(aid,)]
    assert ("assign", aid) in audit(rig, 1)


def test_central_eligibility_function_is_unchanged(rig):
    aid, _ = company_admin(rig)
    assert aid not in svc.assignable_user_ids(rig.e.session, "lity")
