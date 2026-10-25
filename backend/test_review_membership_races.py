"""Reviewer lifecycle races and trigger paths for per-user CLEAR (disposable databases only).

The identity and company databases are separate SQLite files, so no transaction spans both. What the code guarantees,
and what these tests pin down deterministically (gates and hooks, no sleeps):
- the clearer's own authority is re-read from the identity database at commit time, under the company write lock, so a
  reviewer removed mid-request records NO acknowledgment (403);
- the required reviewers are read from the identity database when an obligation is written or reconciled; a change that
  commits in the identity database after that read is NOT seen by that company transaction. It leaves a transient state
  (an extra pending row of a removed reviewer, or a new Admin without a row yet) that is reconciled by the next card
  change, CLEAR or deletion-undo check, and that the reviewer lists compute correctly meanwhile;
- a removed reviewer is never recorded as having cleared, and other reviewers' acknowledgments are never erased."""
import pytest

import card_reviews
import central_auth as ca
import company_routes
import identity_service as svc
from company_database import company_session_factory, open_company_engine
from company_database import begin_write
from test_company_admin_products import company_admin
from test_company_card_reviews import _race, clear, edit, move, reviews, version
from test_company_card_writes import admin, member
from test_company_card_writes import ai, fake_ai, rig  # noqa: F401  (fixtures/helpers)
from test_company_concurrency_integrity import BEGIN_WRITE, Call, auth, interleave, warm_up
from test_company_undo_import import undo
from test_independent_card_review_clear import acks, ids, obligations, world  # noqa: F401  (fixture/helpers)


def ack_status(rig, role="admin", obligation_index=0):
    return {uid: st for r, uid, st, _ in acks(rig) if r == role}


def remove(rig, uid, how):
    """Remove a reviewer in the identity database through the real service, on its own session."""
    with rig.e.factory() as session:
        if how == "deactivate":
            svc.deactivate_membership(session, rig.e.admin, "lity", uid)
        elif how == "demote":
            svc.change_membership_role(session, rig.e.admin, "lity", uid, "visitor")
        else:
            ca.cancel_user(session, rig.e.admin, uid)


def after_first_reader_list_read(monkeypatch, action, role="admin"):
    """Run `action` right after the FIRST reviewer-list read of `role` by a company session (the window between reading
    who must review and finishing the company write)."""
    real = company_routes.reviewer_provider
    fired = []

    def patched(identity, slug):
        base = real(identity, slug)

        def provider(r):
            out = base(r)
            if r == role and not fired:
                fired.append(True)
                action()
            return out
        return provider

    monkeypatch.setattr(company_routes, "reviewer_provider", patched)
    return fired


def reconcile_now(rig):
    """The next reconciling operation (any card change, CLEAR or deletion-undo check), run directly and committed."""
    engine = open_company_engine("lity", rig.e.env)
    try:
        with company_session_factory(engine)() as db, rig.e.factory() as ident:
            card_reviews.set_reviewer_provider(db, lambda role: svc.review_reviewer_ids(ident, "lity", role))
            begin_write(db)
            card_reviews.reconcile(db)
            db.commit()
    finally:
        engine.dispose()


# --- D. reviewer removal during a CLEAR ------------------------------------------------------------------------------

@pytest.mark.parametrize("how", ["deactivate", "demote", "cancel"])
def test_a_reviewer_removed_after_the_list_was_read_is_never_recorded_as_clearing(world, monkeypatch, how):
    rig, mtoken, (aid, atoken), (bid, btoken), stoken = world
    edit(rig, mtoken)
    fired = after_first_reader_list_read(monkeypatch, lambda: remove(rig, bid, how))
    assert clear(rig, atoken, "admin").status_code == 200 and fired
    monkeypatch.undo()
    # transient state: the company transaction did not see the removal, so B's row is still pending (not cleared)
    assert ack_status(rig) == {aid: "cleared", bid: "pending"}
    assert obligations(rig)[0] == ("admin", "pending", None) and obligations(rig)[1][1] == "pending"
    assert ids(rig, atoken) == []  # A cleared its own and is not asked again
    # reconciled by the next operation: B voided, the obligation completes as A's, the Super Admin's row is untouched
    reconcile_now(rig)
    assert ack_status(rig) == {aid: "cleared", bid: "void"}
    assert obligations(rig)[0] == ("admin", "cleared", None)
    assert rig.sql("SELECT cleared_by_user_id FROM card_review_obligations WHERE reviewer_role = 'admin'") == [(aid,)]
    assert ack_status(rig, "super_admin") == {rig.sql("SELECT reviewer_user_id FROM card_review_acknowledgments "
                                                      "WHERE obligation_id = 2")[0][0]: "pending"}
    reconcile_now(rig)  # idempotent
    assert ack_status(rig) == {aid: "cleared", bid: "void"}


@pytest.mark.parametrize("how", ["deactivate", "demote", "cancel"])
def test_a_clearer_who_loses_access_after_authorization_records_nothing_and_the_other_can_finish(world, monkeypatch, how):
    rig, mtoken, (aid, atoken), (bid, btoken), stoken = world
    edit(rig, mtoken)
    after_first_reader_list_read(monkeypatch, lambda: remove(rig, bid, how))
    assert clear(rig, btoken, "admin").status_code == 403  # re-checked under the company write lock at commit
    monkeypatch.undo()
    assert ack_status(rig) == {aid: "pending", bid: "pending"}  # nothing recorded for B, nothing lost for A
    assert obligations(rig)[0] == ("admin", "pending", None)
    assert clear(rig, atoken, "admin").status_code == 200  # A's own CLEAR reconciles: B is void, A completes it
    assert ack_status(rig) == {aid: "cleared", bid: "void"} and obligations(rig)[0] == ("admin", "cleared", None)


def test_a_pending_reviewer_removed_while_another_clears_is_reconciled_by_that_clear(world):
    """Gate: A's CLEAR pauses just before taking the company write lock; B is removed in the identity database and the
    removal completes; A resumes and reads the reviewers under the lock, so B no longer blocks."""
    rig, mtoken, (aid, atoken), (bid, btoken), stoken = world
    edit(rig, mtoken)
    warm_up(rig, atoken)
    post = lambda: clear(rig, atoken, "admin")
    removal = lambda: remove(rig, bid, "deactivate") or "removed"
    ra, rb = interleave(rig, post, removal, BEGIN_WRITE)
    assert ra.status_code == 200 and rb == "removed"
    assert ack_status(rig) == {aid: "cleared", bid: "void"}
    assert obligations(rig)[0] == ("admin", "cleared", None)


def test_removal_racing_a_clear_with_free_threads_keeps_every_invariant(world):
    rig, mtoken, (aid, atoken), (bid, btoken), stoken = world
    edit(rig, mtoken)
    warm_up(rig, atoken)
    ra, removed = _race(lambda: clear(rig, atoken, "admin"), lambda: remove(rig, bid, "deactivate") or "removed")
    assert ra.status_code in (200, 503) and removed == "removed"
    states = ack_status(rig)
    assert states[bid] in ("pending", "void") and states[bid] != "cleared"  # B never clears; A's row is A's alone
    assert states[aid] == ("cleared" if ra.status_code == 200 else "pending")
    reconcile_now(rig)
    assert ack_status(rig)[bid] == "void"
    if ra.status_code == 200:
        assert obligations(rig)[0][1] == "cleared"


# --- B. a reviewer added while a card change writes the obligation -------------------------------------------------------

@pytest.fixture
def one_admin(rig):
    uid, mtoken = member(rig)
    rig.assign(1, uid)
    aid, atoken = company_admin(rig, "a@example.com")
    cid, ctoken = member(rig, "c@example.com")  # a Sales Member who is promoted during the test
    return rig, mtoken, (aid, atoken), (cid, ctoken), admin(rig)


def test_an_admin_promoted_after_the_reviewer_list_was_read_inherits_the_pending_obligation(one_admin, monkeypatch):
    rig, mtoken, (aid, atoken), (cid, ctoken), stoken = one_admin
    promote = lambda: svc.change_membership_role(rig.e.factory(), rig.e.admin, "lity", cid, "admin")
    fired = after_first_reader_list_read(monkeypatch, promote)
    edit(rig, mtoken)  # writes the obligation; the promotion lands right after its reviewer list was read
    monkeypatch.undo()
    assert fired
    assert ack_status(rig) == {aid: "pending"}  # the snapshot did not see C yet: a transient gap, not a lost review
    assert ids(rig, ctoken) == [1]  # the list is computed from the CURRENT reviewers: C sees it pending
    assert clear(rig, atoken, "admin").status_code == 200
    assert obligations(rig)[0] == ("admin", "pending", None)  # A alone no longer completes it: C is added by the CLEAR
    assert ack_status(rig) == {aid: "cleared", cid: "pending"}
    assert clear(rig, ctoken, "admin").status_code == 200
    assert obligations(rig)[0] == ("admin", "cleared", None)


def test_a_promotion_after_a_completed_obligation_does_not_reopen_it(one_admin):
    rig, mtoken, (aid, atoken), (cid, ctoken), stoken = one_admin
    edit(rig, mtoken)
    assert clear(rig, atoken, "admin").status_code == 200
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", cid, "admin")
    assert ids(rig, ctoken) == [] and obligations(rig)[0] == ("admin", "cleared", None)


# --- card change versus reviewers clearing ------------------------------------------------------------------------------

def test_a_card_change_racing_two_clears_never_leaves_an_acknowledgment_of_an_old_version(world):
    rig, mtoken, (aid, atoken), (bid, btoken), stoken = world
    edit(rig, mtoken, title="one")
    warm_up(rig, atoken)
    warm_up(rig, btoken)
    seen = version(rig)
    put = lambda: rig.client.put("/api/companies/lity/boards/1/cards/1",
                                 json={"title": "two", "expected_version": seen}, headers=auth(mtoken))
    a = lambda: clear(rig, atoken, "admin", card_version=seen)
    b = lambda: clear(rig, btoken, "admin", card_version=seen)
    ra, rb, rc = _race(a, b, put)
    for r in (ra, rb):
        assert r.status_code in (200, 409, 404, 503), r.text
    assert rc.status_code in (200, 503)
    rows = rig.sql("SELECT o.id, o.status, o.card_version, k.status, k.card_version FROM card_review_obligations o "
                   "JOIN card_review_acknowledgments k ON k.obligation_id = o.id WHERE o.reviewer_role = 'admin'")
    for oid, ostatus, over, kstatus, kver in rows:
        if ostatus == "pending":
            assert kver == over  # a pending obligation never carries an acknowledgment of another version
            if kstatus == "cleared":
                assert over == version(rig)
        if ostatus == "cleared":
            assert kstatus in ("cleared", "void")
    if rc.status_code == 200:  # the edit won or came later: a pending Admin obligation exists for the latest version
        assert ("admin", version(rig)) in [r for r in rig.sql(
            "SELECT reviewer_role, card_version FROM card_review_obligations WHERE status = 'pending'")]


# --- C. trigger paths with two Admins ------------------------------------------------------------------------------------

def test_ai_member_edits_give_both_admins_and_the_super_admin_independent_requirements(world, monkeypatch):
    rig, mtoken, (aid, atoken), (bid, btoken), stoken = world
    fake_ai(monkeypatch, [{"type": "update", "card_id": 1, "title": "AI edit"}])
    assert ai(rig, mtoken).status_code == 200
    assert ack_status(rig) == {aid: "pending", bid: "pending"} and len(ack_status(rig, "super_admin")) == 1
    assert clear(rig, atoken, "admin").status_code == 200
    assert ack_status(rig) == {aid: "cleared", bid: "pending"}
    assert ids(rig, btoken) == [1] and ids(rig, stoken, "super_admin") == [1]


def test_a_members_undo_resets_every_requirement_and_opens_fresh_ones_for_both_admins(world):
    rig, mtoken, (aid, atoken), (bid, btoken), stoken = world
    edit(rig, mtoken)
    assert clear(rig, atoken, "admin").status_code == 200 and clear(rig, btoken, "admin").status_code == 200
    assert clear(rig, stoken, "super_admin").status_code == 200
    assert undo(rig, mtoken).status_code == 200  # the undo is a new member change to review, by everybody
    assert version(rig) == 2
    pend = [r for r in rig.sql("SELECT o.reviewer_role, k.reviewer_user_id, k.status, k.card_version FROM "
                               "card_review_obligations o JOIN card_review_acknowledgments k ON k.obligation_id = o.id "
                               "WHERE o.status = 'pending'")]
    assert sorted((r[0], r[2], r[3]) for r in pend) == [("admin", "pending", 2), ("admin", "pending", 2), ("super_admin", "pending", 2)]
    assert {r[1] for r in pend if r[0] == "admin"} == {aid, bid}
    # the earlier, completed obligations and their acknowledgments are history and stay as they were
    assert rig.sql("SELECT COUNT(*) FROM card_review_acknowledgments WHERE status = 'cleared'") == [(3,)]


def test_a_backward_move_refreshes_the_admin_requirements_and_adds_the_super_admins(world):
    rig, mtoken, (aid, atoken), (bid, btoken), stoken = world
    rig.sql("UPDATE cards SET column_id = 2 WHERE id = 1")
    edit(rig, mtoken)  # v1: Admin A, Admin B and the Super Admin must review
    assert clear(rig, atoken, "admin").status_code == 200
    assert ack_status(rig) == {aid: "cleared", bid: "pending"}
    assert move(rig, atoken, 1, 1).status_code == 200  # an Admin moves it back: v2
    assert version(rig) == 2
    assert ack_status(rig) == {aid: "pending", bid: "pending"}  # A's clear of v1 no longer counts
    assert {r[3] for r in acks(rig)} == {2}
    item = reviews(rig, stoken).json()["items"][0]
    assert [t["kind"] for t in item["triggers"]] == ["member_edit", "backward_move"]
    assert ids(rig, btoken) == [1] and ids(rig, atoken) == [1]
    assert clear(rig, atoken, "admin", card_version=1).status_code == 409


def test_an_admins_backward_move_needs_only_the_super_admin_and_adds_no_admin_requirement(world):
    rig, mtoken, (aid, atoken), (bid, btoken), stoken = world
    rig.sql("UPDATE cards SET column_id = 2 WHERE id = 1")
    assert move(rig, atoken, 1, 1).status_code == 200
    assert obligations(rig) == [("super_admin", "pending", None)] and ack_status(rig) == {}
    assert len(ack_status(rig, "super_admin")) == 1
    assert ids(rig, atoken) == [] and ids(rig, btoken) == []


def test_ai_and_plain_forward_moves_and_admin_edits_reset_but_never_create_requirements(world, monkeypatch):
    rig, mtoken, (aid, atoken), (bid, btoken), stoken = world
    edit(rig, mtoken)
    assert clear(rig, atoken, "admin").status_code == 200
    fake_ai(monkeypatch, [{"type": "move", "card_id": 1, "column_id": 2}])  # an Admin's forward move through the AI
    assert ai(rig, btoken).status_code == 200
    assert version(rig) == 2
    assert ack_status(rig) == {aid: "pending", bid: "pending"} and {r[3] for r in acks(rig)} == {2}
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations") == [(2,)]  # no new role-level obligation
    edit(rig, stoken, title="Super Admin touched it")
    assert version(rig) == 3 and {r[3] for r in acks(rig)} == {3}
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations") == [(2,)]


def test_regression_a_trigger_for_one_role_still_moves_the_other_roles_pending_obligation(world):
    """An Admin's backward move writes only the Super Admin's obligation. The card's pending ADMIN obligation used to be
    skipped (the whole card counted as 'triggered'), stayed at the old version and could never be cleared (409 at the old
    and at the new version) until some other change moved it."""
    rig, mtoken, (aid, atoken), (bid, btoken), stoken = world
    rig.sql("UPDATE cards SET column_id = 2 WHERE id = 1")
    edit(rig, mtoken)
    assert move(rig, atoken, 1, 1).status_code == 200
    v = version(rig)
    assert sorted(rig.sql("SELECT reviewer_role, card_version FROM card_review_obligations WHERE status = 'pending'")) == [
        ("admin", v), ("super_admin", v)]
    assert clear(rig, btoken, "admin", card_version=v).status_code == 200
    assert clear(rig, atoken, "admin", card_version=v).status_code == 200
    assert obligations(rig)[0] == ("admin", "cleared", None)
