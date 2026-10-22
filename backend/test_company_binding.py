"""F8 mitigation: a company write is bound to the company it was authorized for, and the caller's membership is read
again from the identity database at commit time (under the company write lock). Disposable tmp_path databases only."""
import threading

import pytest
from sqlalchemy.orm import Session

import card_rules
import company_authority
import identity_service as svc
from company_authority import AuthorityRevoked, bind_authority
from company_database import session_company_slug
from database_targets import COMPANY_SLUGS
from test_company_admin_products import company_admin
from test_company_card_writes import admin, member
from test_company_card_writes import rig  # noqa: F401  (fixture)


TABLES = ("cards", "card_versions", "audit_events", "card_stage_events", "card_edit_history", "undo_entries")


def content(rig):
    """Every row a card write could touch, in every company (the actor's local reference row is not business content)."""
    return {s: {t: rig.sql(f"SELECT * FROM {t} ORDER BY 1", slug=s) for t in TABLES} for s in COMPANY_SLUGS}


def titles(rig, slug):
    return rig.sql("SELECT id, title FROM cards ORDER BY id", slug=slug)


def edit(rig, token, slug, title="changed"):
    path, kw = rig.versioned("put", "/cards/1", {"json": {"title": title}}, slug)
    return rig.client.put(f"/api/companies/{slug}/boards/1{path}", headers={"Authorization": f"Bearer {token}"}, **kw)


def test_a_super_admin_writes_to_each_company_and_only_to_that_one(rig):
    token = admin(rig)
    originals = {slug: titles(rig, slug) for slug in COMPANY_SLUGS}
    for n, slug in enumerate(COMPANY_SLUGS):
        assert edit(rig, token, slug, f"edited in {slug}").status_code == 200, slug
        for other in COMPANY_SLUGS:
            done = COMPANY_SLUGS.index(other) <= n
            expected = [(1, f"edited in {other}")] + originals[other][1:] if done else originals[other]
            assert titles(rig, other) == expected


def test_a_member_of_one_company_cannot_write_to_another(rig):
    _, token = member(rig, "m@example.com", slug="valor-log")
    before = {s: rig.digest(s) for s in COMPANY_SLUGS}
    assert edit(rig, token, "lity").status_code == 403 and edit(rig, token, "salcomp").status_code == 403
    assert {s: rig.digest(s) for s in COMPANY_SLUGS} == before


def test_a_session_bound_to_another_company_is_refused_before_anything_is_written(rig, monkeypatch):
    token = admin(rig)
    wrong = rig.runtime._session_factory("valor-log")  # a mis-wired factory: valid, but the OTHER company's database
    monkeypatch.setattr(rig.runtime, "_session_factory", lambda slug: wrong)
    before = {s: rig.digest(s) for s in COMPANY_SLUGS}
    r = edit(rig, token, "lity")
    assert r.status_code == 503 and r.json()["detail"] == "Company data is unavailable"
    assert {s: rig.digest(s) for s in COMPANY_SLUGS} == before  # neither database was touched


def test_a_commit_of_a_session_whose_binding_no_longer_matches_is_refused(rig):
    from test_card_versions import orm
    with orm(rig, "lity") as db:
        assert session_company_slug(db) == "lity"
        bind_authority(db, "valor-log", lambda: True)  # the authorization named another company
        db.info["company_slug"] = "lity"
        with pytest.raises(AuthorityRevoked):
            db.commit()
    with orm(rig, "lity") as db:
        bind_authority(db, "lity", lambda: True)
        db.info["company_slug"] = "valor-log"  # a relabelled session is not trusted either
        with pytest.raises(AuthorityRevoked):
            db.commit()


def revoke_during(rig, monkeypatch, change):
    """Run `change` (an identity-database change) after the request was authorized and its card change was made, i.e.
    while the request is waiting to commit."""
    real = card_rules.apply_card_updates

    def apply_then_revoke(*args, **kwargs):
        result = real(*args, **kwargs)
        change()
        return result

    monkeypatch.setattr(card_rules, "apply_card_updates", apply_then_revoke)
    import company_routes
    monkeypatch.setattr(company_routes, "apply_card_updates", apply_then_revoke)


@pytest.mark.parametrize("how", ["deactivated", "role_changed", "company_inactive"])
def test_authority_lost_before_the_commit_refuses_the_write_and_rolls_it_back(rig, monkeypatch, how):
    from identity_models import IdentityCompany
    uid, token = company_admin(rig)
    changes = {
        "deactivated": lambda: svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", uid),
        "role_changed": lambda: svc.change_membership_role(rig.e.session, rig.e.admin, "lity", uid, "sales_member"),
        "company_inactive": lambda: rig.e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=False),
    }
    revoke_during(rig, monkeypatch, changes[how])
    before = content(rig)
    r = edit(rig, token, "lity")
    assert r.status_code == 403 and r.json()["detail"] == "Access denied"
    assert content(rig) == before  # the change, its version, audit and undo entry were rolled back; nothing leaked elsewhere


def test_authority_that_still_holds_commits_normally(rig, monkeypatch):
    uid, token = company_admin(rig)
    revoke_during(rig, monkeypatch, lambda: None)
    assert edit(rig, token, "lity", "kept").status_code == 200
    assert titles(rig, "lity")[0] == (1, "kept")


def test_two_companies_written_at_the_same_time_stay_apart(rig):
    token = admin(rig)
    results, errors = {}, []

    def run(slug):
        try:
            results[slug] = [edit(rig, token, slug, f"{slug}-{i}").status_code for i in range(5)]
        except Exception as exc:  # pragma: no cover - a failure must show, not hang the join
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(slug,)) for slug in ("lity", "valor-log")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors and results == {"lity": [200] * 5, "valor-log": [200] * 5}
    assert titles(rig, "lity")[0] == (1, "lity-4") and titles(rig, "valor-log")[0] == (1, "valor-log-4")
    assert titles(rig, "salcomp")[0] == (1, "card 1")


def test_an_ai_request_keeps_the_authority_it_was_reauthorized_with_and_loses_it_if_that_changes_before_commit(rig, monkeypatch):
    import ai_kanban
    import routes
    from test_company_card_writes import fake_ai, ai
    uid, token = company_admin(rig)
    fake_ai(monkeypatch, [{"type": "update", "card_id": 1, "title": "by ai"}])
    real = ai_kanban.apply_card_updates

    def apply_then_revoke(*args, **kwargs):
        result = real(*args, **kwargs)
        svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", uid)  # after the reauthorization, before the commit
        return result

    monkeypatch.setattr(ai_kanban, "apply_card_updates", apply_then_revoke)
    before = content(rig)
    r = ai(rig, token)
    assert r.status_code == 403 and content(rig) == before
