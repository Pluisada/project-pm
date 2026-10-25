"""Per-user CLEAR: every required reviewer (each active company Admin, the global administrator) acknowledges a card
review obligation on their own. Synthetic data and disposable tmp_path databases only."""
import sqlite3

import pytest

import card_reviews
import identity_service as svc
from company_database import company_database_path
from test_company_admin_products import company_admin
from test_company_card_reviews import _race, clear, edit, reviews, version
from test_company_card_writes import admin, member
from test_company_card_writes import ai, fake_ai, rig  # noqa: F401  (fixtures/helpers)
from test_company_concurrency_integrity import auth


@pytest.fixture
def world(rig):
    """Sales Member (owns card 1), company Admins A and B, and the Super Admin."""
    uid, mtoken = member(rig)
    rig.assign(1, uid)
    aid, atoken = company_admin(rig, "a@example.com")
    bid, btoken = company_admin(rig, "b@example.com")
    return rig, mtoken, (aid, atoken), (bid, btoken), admin(rig)


def acks(rig, card_id=1):
    return rig.sql(
        "SELECT o.reviewer_role, k.reviewer_user_id, k.status, k.card_version FROM card_review_acknowledgments k "
        "JOIN card_review_obligations o ON o.id = k.obligation_id WHERE o.card_id = ? ORDER BY o.id, k.reviewer_user_id",
        (card_id,))


def obligations(rig, card_id=1):
    return rig.sql("SELECT reviewer_role, status, closed_reason FROM card_review_obligations WHERE card_id = ? ORDER BY id",
                   (card_id,))


def ids(rig, token, role="admin"):
    r = reviews(rig, token)
    assert r.status_code == 200 and r.json()["reviewer_role"] == role
    return [i["card_id"] for i in r.json()["items"]]


def test_a_member_edit_snapshots_every_required_reviewer(world):
    rig, mtoken, (aid, _), (bid, _), _ = world
    edit(rig, mtoken)
    rows = acks(rig)
    assert [(r[0], r[2], r[3]) for r in rows] == [("admin", "pending", 1), ("admin", "pending", 1), ("super_admin", "pending", 1)]
    assert {r[1] for r in rows if r[0] == "admin"} == {aid, bid}


def test_one_admins_clear_leaves_the_other_admin_and_the_super_admin_pending(world):
    rig, mtoken, (aid, atoken), (bid, btoken), stoken = world
    edit(rig, mtoken)
    assert clear(rig, atoken, "admin").status_code == 200
    assert ids(rig, atoken) == []  # A cleared its own
    assert ids(rig, btoken) == [1]  # B still sees it as pending
    assert ids(rig, stoken, "super_admin") == [1]  # and so does the Super Admin
    assert obligations(rig) == [("admin", "pending", None), ("super_admin", "pending", None)]
    item = reviews(rig, btoken).json()["items"][0]
    assert (item["my_status"], item["reviewers_total"], item["reviewers_cleared"]) == ("pending", 2, 1)
    # A cannot clear twice, and B's clear completes the Admin obligation without touching the Super Admin's
    assert clear(rig, atoken, "admin").status_code == 404
    assert clear(rig, btoken, "admin").status_code == 200
    assert obligations(rig) == [("admin", "cleared", None), ("super_admin", "pending", None)]
    assert [r[2] for r in acks(rig)] == ["cleared", "cleared", "pending"]
    assert clear(rig, stoken, "super_admin").status_code == 200
    assert obligations(rig) == [("admin", "cleared", None), ("super_admin", "cleared", None)]


def test_the_super_admin_clearing_first_does_not_clear_any_admin(world):
    rig, mtoken, (_, atoken), (_, btoken), stoken = world
    edit(rig, mtoken)
    assert clear(rig, stoken, "super_admin").status_code == 200
    assert ids(rig, atoken) == [1] and ids(rig, btoken) == [1]
    assert obligations(rig) == [("admin", "pending", None), ("super_admin", "cleared", None)]


def test_a_new_card_version_resets_every_acknowledgment(world):
    rig, mtoken, (_, atoken), (_, btoken), _ = world
    edit(rig, mtoken, title="one")
    assert clear(rig, atoken, "admin").status_code == 200
    stale = version(rig)
    edit(rig, mtoken, title="two")  # a new relevant version: A's clear of the old version no longer counts
    assert ids(rig, atoken) == [1] and ids(rig, btoken) == [1]
    assert {r[3] for r in acks(rig)} == {2} and [r[2] for r in acks(rig)] == ["pending"] * 3
    assert clear(rig, btoken, "admin", card_version=stale).status_code == 409  # stale version rejected
    assert clear(rig, btoken, "admin").status_code == 200


def test_a_non_triggering_change_also_resets_acknowledgments_to_the_new_version(world):
    rig, mtoken, (_, atoken), _, stoken = world
    edit(rig, mtoken)
    assert clear(rig, atoken, "admin").status_code == 200
    edit(rig, stoken, title="Super Admin edit")  # creates no obligation but moves the pending ones to version 2
    assert version(rig) == 2 and {r[3] for r in acks(rig)} == {2}
    assert ids(rig, atoken) == [1]


def test_an_admin_added_while_pending_must_acknowledge_but_not_completed_ones(rig):
    uid, mtoken = member(rig)
    rig.assign(1, uid)
    aid, atoken = company_admin(rig, "a@example.com")
    edit(rig, mtoken, title="first")
    assert clear(rig, atoken, "admin").status_code == 200  # completed before B exists
    bid, btoken = company_admin(rig, "b@example.com")
    assert ids(rig, btoken) == []  # B is not asked about a completed obligation
    edit(rig, mtoken, title="second")
    assert {r[1] for r in acks(rig) if r[0] == "admin" and r[3] == 2} == {aid, bid}
    # a reviewer promoted while the obligation is open becomes responsible from then on
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", bid, "sales_member")
    assert clear(rig, atoken, "admin").status_code == 200  # B was voided meanwhile: A alone completes it
    assert sorted(obligations(rig)[1:]) == [("admin", "cleared", None), ("super_admin", "pending", None)]
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", bid, "admin")
    assert ids(rig, btoken) == []  # re-promoted after completion: nothing is pending for B


def test_a_new_admin_inherits_a_still_pending_obligation(rig):
    uid, mtoken = member(rig)
    rig.assign(1, uid)
    aid, atoken = company_admin(rig, "a@example.com")
    edit(rig, mtoken)
    bid, btoken = company_admin(rig, "b@example.com")  # becomes an Admin while the obligation is open
    assert ids(rig, btoken) == [1]  # visible at once (no row yet)
    assert clear(rig, atoken, "admin").status_code == 200
    assert obligations(rig)[0] == ("admin", "pending", None)  # A alone no longer completes it
    assert clear(rig, btoken, "admin").status_code == 200
    assert obligations(rig)[0] == ("admin", "cleared", None)
    assert sorted((r[1], r[2]) for r in acks(rig) if r[0] == "admin") == sorted([(aid, "cleared"), (bid, "cleared")])


@pytest.mark.parametrize("how", ["deactivated", "demoted", "cancelled"])
def test_a_removed_reviewer_no_longer_blocks_completion(world, how):
    rig, mtoken, (aid, atoken), (bid, _), _ = world
    edit(rig, mtoken)
    s, root = rig.e.session, rig.e.admin
    if how == "deactivated":
        svc.deactivate_membership(s, root, "lity", bid)
    elif how == "demoted":
        svc.change_membership_role(s, root, "lity", bid, "visitor")
    else:
        import central_auth as ca
        ca.cancel_user(s, root, bid)
    assert clear(rig, atoken, "admin").status_code == 200
    assert obligations(rig)[0] == ("admin", "cleared", None)
    by_user = {r[1]: r[2] for r in acks(rig) if r[0] == "admin"}
    assert by_user == {aid: "cleared", bid: "void"}  # B's row is void, never "cleared"


def test_the_last_admin_removed_voids_the_admin_obligation_without_faking_a_clear(world):
    rig, mtoken, (aid, _), (bid, _), _ = world
    edit(rig, mtoken)
    for uid in (aid, bid):
        svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", uid)
    edit(rig, mtoken, title="again")  # the next relevant change reconciles
    assert obligations(rig)[:2][0] == ("admin", "void", "no_required_reviewer")
    assert all(r[2] != "cleared" for r in acks(rig) if r[0] == "admin")
    assert not any(r[0] == "admin" and r[2] == "pending" for r in obligations_pending(rig))  # no new Admin obligation
    assert ("super_admin", "pending") in [(r[0], r[1]) for r in obligations(rig)]  # the Super Admin still reviews


def obligations_pending(rig):
    return [r for r in rig.sql("SELECT reviewer_role, status FROM card_review_obligations WHERE status='pending'")]


def test_an_obligation_from_before_the_table_stays_pending_and_is_not_acknowledged(world):
    rig, mtoken, (aid, atoken), (bid, btoken), _ = world
    edit(rig, mtoken)
    con = sqlite3.connect(company_database_path("lity", rig.e.env))
    con.execute("DELETE FROM card_review_acknowledgments")  # what a pre-existing pending role-level row looks like
    con.commit()
    con.close()
    assert ids(rig, atoken) == [1] and ids(rig, btoken) == [1]  # still pending for every current reviewer
    assert clear(rig, atoken, "admin").status_code == 200
    assert obligations(rig)[0] == ("admin", "pending", None) and ids(rig, btoken) == [1]


def test_an_old_cleared_role_level_row_is_history_and_untouched(world):
    rig, mtoken, (_, atoken), (_, btoken), _ = world
    edit(rig, mtoken)
    con = sqlite3.connect(company_database_path("lity", rig.e.env))
    con.execute("UPDATE card_review_obligations SET status='cleared', cleared_by_user_id=1, cleared_at=created_at "
                "WHERE reviewer_role='admin'")
    con.execute("DELETE FROM card_review_acknowledgments")
    con.commit()
    con.close()
    before = rig.sql("SELECT * FROM card_review_obligations WHERE reviewer_role='admin'")
    assert ids(rig, atoken) == [] and ids(rig, btoken) == []
    assert rig.sql("SELECT * FROM card_review_obligations WHERE reviewer_role='admin'") == before
    assert rig.sql("SELECT COUNT(*) FROM card_review_acknowledgments") == [(0,)]


def test_the_database_refuses_a_second_row_for_the_same_reviewer_and_obligation(world):
    rig, mtoken, (aid, _), _, _ = world
    edit(rig, mtoken)
    con = sqlite3.connect(company_database_path("lity", rig.e.env))
    oid = con.execute("SELECT id FROM card_review_obligations WHERE reviewer_role='admin'").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO card_review_acknowledgments (obligation_id, reviewer_user_id, status, card_version, "
                    "created_at, updated_at) VALUES (?, ?, 'pending', 1, '2026-01-01', '2026-01-01')", (oid, aid))
    con.close()


def test_two_admins_clearing_concurrently_both_succeed_and_complete_the_obligation_once(world):
    rig, mtoken, (_, atoken), (_, btoken), _ = world
    edit(rig, mtoken)
    post = lambda t: (lambda: rig.client.post("/api/companies/lity/boards/1/cards/1/reviews/admin/clear",
                                              json={"card_version": 1}, headers=auth(t)))
    ra, rb = _race(post(atoken), post(btoken))
    assert sorted([ra.status_code, rb.status_code]) in ([200, 200], [200, 503], [200, 409])
    done = [r[2] for r in acks(rig) if r[0] == "admin"].count("cleared")
    assert done == [ra.status_code, rb.status_code].count(200)
    if done == 2:
        assert obligations(rig)[0][1] == "cleared"
    else:
        assert obligations(rig)[0][1] == "pending"


def test_deleting_a_card_voids_its_pending_acknowledgments(world):
    rig, mtoken, _, _, stoken = world
    edit(rig, mtoken)
    assert rig.call("delete", "/cards/1", stoken).status_code in (200, 204)
    assert {r[2] for r in acks(rig)} == {"void"} and {r[1] for r in obligations(rig)} == {"void"}


def test_upgrading_a_database_that_lacks_only_the_acknowledgment_table_is_additive_and_fails_closed_before(tmp_path):
    """Temporary database only: the shape of a company database initialized before per-reviewer CLEAR."""
    import hashlib

    from create_company_db import create_company_database
    from database_targets import company_env_name
    from diagnose_company_schema import diagnose_company
    from init_company_card_reviews import init_company_card_reviews

    env = {company_env_name("lity"): f"sqlite:///{tmp_path / 'lity.db'}"}
    create_company_database("lity", env)
    path = company_database_path("lity", env)
    con = sqlite3.connect(path)
    con.execute("DROP TABLE card_review_acknowledgments")
    con.execute("INSERT INTO card_versions VALUES (5, 1, '2026-01-01')")
    con.execute("INSERT INTO card_review_obligations (card_id, reviewer_role, status, card_version, context_json, refresh_count, "
                "created_at, updated_at) VALUES (5, 'admin', 'pending', 1, '[]', 0, '2026-01-01', '2026-01-01')")
    con.execute("INSERT INTO card_review_obligations (card_id, reviewer_role, status, card_version, cleared_by_user_id, cleared_at, "
                "context_json, refresh_count, created_at, updated_at) VALUES (6, 'admin', 'cleared', 1, 3, '2026-01-02', '[]', 0, "
                "'2026-01-01', '2026-01-02')")
    con.commit()
    con.close()
    digest = lambda: hashlib.sha256(path.read_bytes()).hexdigest()
    before = digest()
    assert diagnose_company("lity", env).complete is False  # reported, and the diagnostic is read-only
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        assert "card_review_acknowledgments is missing" in card_reviews.review_schema_problem(conn)  # fails closed
    engine.dispose()
    assert digest() == before
    assert init_company_card_reviews("lity", False, env) is True and digest() == before  # dry run writes nothing
    assert init_company_card_reviews("lity", True, env) is True
    assert init_company_card_reviews("lity", True, env) is False  # idempotent
    con = sqlite3.connect(path)
    assert con.execute("SELECT card_id, status, cleared_by_user_id FROM card_review_obligations ORDER BY id").fetchall() == [
        (5, "pending", None), (6, "cleared", 3)]  # nothing acknowledged, nothing reopened
    assert con.execute("SELECT COUNT(*) FROM card_review_acknowledgments").fetchone() == (0,)
    unique = [r for r in con.execute("PRAGMA index_list(card_review_acknowledgments)") if r[2] == 1]
    assert [con.execute(f"PRAGMA index_info({r[1]})").fetchall()[i][2] for r in unique for i in (0, 1)] == ["obligation_id", "reviewer_user_id"]
    con.close()
    assert diagnose_company("lity", env).complete is True
