"""Card review workflow in central (company) mode: independent Admin / Super Admin obligations, version binding,
backward-move reviews, authorization and atomicity. Synthetic data and disposable tmp_path databases only."""
import json
import sqlite3
import threading

import pytest

import card_reviews
import identity_service as svc
from auth import create_access_token
from company_database import company_database_path
from test_company_admin_products import company_admin
from test_company_card_writes import NOW, admin, member
from test_company_card_writes import ai, fake_ai, rig  # noqa: F401  (fixtures/helpers)
from test_company_concurrency_integrity import Call, TIMEOUT, auth, warm_up
from test_company_undo_import import undo

OB = "SELECT reviewer_role, status, card_version, refresh_count, cleared_by_user_id FROM card_review_obligations WHERE card_id = ? ORDER BY id"


def state(rig):
    """Everything a review action could touch (the actor's own local reference row is not part of it)."""
    return [rig.sql(q) for q in (
        "SELECT * FROM cards ORDER BY id", "SELECT * FROM card_versions ORDER BY card_id",
        "SELECT * FROM card_review_obligations ORDER BY id", "SELECT * FROM audit_events ORDER BY id",
        "SELECT * FROM card_edit_history ORDER BY id", "SELECT * FROM card_stage_events ORDER BY id",
        "SELECT * FROM undo_entries ORDER BY id")]


def version(rig, card_id=1):
    rows = rig.sql("SELECT version FROM card_versions WHERE card_id = ?", (card_id,))
    return rows[0][0] if rows else 0


def pending(rig, card_id=1):
    return sorted(rig.sql("SELECT reviewer_role, card_version FROM card_review_obligations "
                          "WHERE card_id = ? AND status = 'pending'", (card_id,)))


def edit(rig, token, card_id=1, **fields):
    r = rig.call("put", f"/cards/{card_id}", token, json=fields or {"title": "edited"})
    assert r.status_code == 200, r.text
    return r


def reviews(rig, token, slug="lity"):
    return rig.call("get", "/reviews", token, slug=slug)


def clear(rig, token, role, card_id=1, card_version=None, slug="lity", **extra):
    return rig.call("post", f"/cards/{card_id}/reviews/{role}/clear", token, slug=slug,
                    json={"card_version": card_version if card_version is not None else version(rig, card_id), **extra})


@pytest.fixture
def world(rig):
    """A Sales Member owning card 1, a company Admin and the Super Admin; card 2 stays unassigned."""
    uid, mtoken = member(rig)
    rig.assign(1, uid)
    aid, atoken = company_admin(rig)
    return rig, uid, mtoken, aid, atoken, admin(rig)


# --- A. independent obligations ------------------------------------------------------------------------------------

def test_a_member_edit_creates_one_obligation_for_each_role_at_the_new_version(world):
    rig, uid, mtoken, _, atoken, stoken = world
    assert pending(rig) == []
    edit(rig, mtoken, title="Deal value changed", deal_value=5)
    v = version(rig)
    assert v == 1 and pending(rig) == [("admin", v), ("super_admin", v)]
    items = reviews(rig, atoken).json()
    assert items["reviewer_role"] == "admin" and [i["card_id"] for i in items["items"]] == [1]
    assert items["items"][0]["card_version"] == v and items["items"][0]["triggers"][0]["kind"] == "member_edit"
    assert items["items"][0]["triggers"][0]["changes"]["deal_value"]["new"] == 5
    assert reviews(rig, stoken).json()["reviewer_role"] == "super_admin"
    assert len(reviews(rig, stoken).json()["items"]) == 1


def test_admin_clears_only_its_own_and_the_super_admin_flag_stays(world):
    rig, _, mtoken, aid, atoken, stoken = world
    edit(rig, mtoken)
    v = version(rig)
    r = clear(rig, atoken, "admin")
    assert r.status_code == 200 and r.json()["card_version"] == v
    assert pending(rig) == [("super_admin", v)]
    assert rig.sql(OB, (1,))[0] == ("admin", "cleared", v, 0, aid)
    assert reviews(rig, atoken).json()["items"] == []
    assert len(reviews(rig, stoken).json()["items"]) == 1  # the other role's pending state is intact
    assert rig.sql("SELECT action, actor_user_id FROM audit_events WHERE action = 'review_cleared'") == [("review_cleared", aid)]


def test_super_admin_clears_only_its_own_and_the_admin_flag_stays(world):
    rig, _, mtoken, _, atoken, stoken = world
    edit(rig, mtoken)
    v = version(rig)
    assert clear(rig, stoken, "super_admin").status_code == 200
    assert pending(rig) == [("admin", v)]
    assert rig.sql(OB, (1,))[1][:3] == ("super_admin", "cleared", v)
    assert len(reviews(rig, atoken).json()["items"]) == 1 and reviews(rig, stoken).json()["items"] == []
    assert clear(rig, atoken, "admin").status_code == 200 and pending(rig) == []


def test_a_repeated_edit_refreshes_the_same_pending_rows_to_the_latest_version(world):
    rig, _, mtoken, _, atoken, _ = world
    edit(rig, mtoken, title="one")
    edit(rig, mtoken, title="two", details="x")
    v = version(rig)
    assert v == 2 and pending(rig) == [("admin", 2), ("super_admin", 2)]
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations") == [(2,)]  # no accumulation of duplicates
    assert [r[3] for r in rig.sql(OB, (1,))] == [1, 1]
    item = reviews(rig, atoken).json()["items"][0]
    assert item["card_version"] == 2 and [t["version"] for t in item["triggers"]] == [1, 2]
    # the complete underlying history is untouched
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history WHERE card_id = 1") == [(2,)]
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations WHERE status = 'void'") == [(0,)]


def test_an_old_approval_is_not_inherited_by_a_new_edit(world):
    rig, _, mtoken, _, atoken, stoken = world
    edit(rig, mtoken, title="one")
    assert clear(rig, atoken, "admin").status_code == 200
    edit(rig, mtoken, title="two")
    v = version(rig)
    assert pending(rig) == [("admin", v), ("super_admin", v)]  # Admin must review again; the Super Admin's is the same row
    admin_rows = [r for r in rig.sql(OB, (1,)) if r[0] == "admin"]
    assert [r[1] for r in admin_rows] == ["cleared", "pending"] and admin_rows[0][2] == 1 and admin_rows[1][2] == 2
    assert [r[1] for r in rig.sql(OB, (1,)) if r[0] == "super_admin"] == ["pending"]


def test_an_edit_without_a_real_change_creates_nothing_and_admin_edits_create_nothing(world):
    rig, _, mtoken, _, atoken, stoken = world
    r = rig.call("put", "/cards/1", mtoken, json={"title": "card 1"})  # same value
    assert r.status_code == 200 and pending(rig) == [] and version(rig) == 0
    edit(rig, atoken, card_id=1, title="by admin")
    edit(rig, stoken, card_id=2, title="by super")
    assert pending(rig, 1) == [] and pending(rig, 2) == []


def test_ai_member_edits_create_the_same_obligations(world, monkeypatch):
    rig, _, mtoken, _, _, _ = world
    fake_ai(monkeypatch, [{"type": "update", "card_id": 1, "title": "AI edit"}])
    assert ai(rig, mtoken).status_code == 200
    assert pending(rig) == [("admin", 1), ("super_admin", 1)]


def test_a_members_undo_of_their_edit_is_a_new_change_to_review(world):
    rig, _, mtoken, _, atoken, _ = world
    edit(rig, mtoken)
    assert clear(rig, atoken, "admin").status_code == 200
    assert undo(rig, mtoken).status_code == 200
    assert version(rig) == 2 and ("admin", 2) in pending(rig)  # the Admin's approval of v1 does not cover the undo


# --- B. versions, staleness, atomicity, concurrency ------------------------------------------------------------------

def test_a_reviewer_who_opened_an_older_version_cannot_clear_it(world):
    rig, _, mtoken, _, atoken, stoken = world
    edit(rig, mtoken, title="one")
    seen = reviews(rig, atoken).json()["items"][0]["card_version"]
    edit(rig, mtoken, title="two")  # the card changes after the reviewer opened it
    before = state(rig)
    r = clear(rig, atoken, "admin", card_version=seen)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "review_stale"
    assert state(rig) == before  # nothing recorded, nothing cleared
    assert pending(rig) == [("admin", 2), ("super_admin", 2)]
    assert clear(rig, atoken, "admin", card_version=2).status_code == 200


def test_any_change_by_anyone_moves_pending_reviews_to_the_new_version(world):
    rig, _, mtoken, _, atoken, stoken = world
    edit(rig, mtoken, title="one")
    edit(rig, atoken, title="admin touched it")  # not a member edit: no new obligation, but the version moves
    assert pending(rig) == [("admin", 2), ("super_admin", 2)]
    assert clear(rig, atoken, "admin", card_version=1).status_code == 409
    assert clear(rig, atoken, "admin", card_version=2).status_code == 200
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations") == [(2,)]


def test_failed_review_operations_leave_the_database_unchanged(world):
    rig, _, mtoken, _, atoken, stoken = world
    edit(rig, mtoken)
    before = state(rig)
    assert clear(rig, atoken, "admin", card_version=99).status_code == 409
    assert clear(rig, atoken, "super_admin").status_code == 403
    assert clear(rig, mtoken, "admin").status_code == 403
    assert clear(rig, atoken, "admin", card_id=2, card_version=1).status_code == 404  # card 2 has nothing pending
    assert clear(rig, atoken, "admin", card_id=999, card_version=1).status_code == 404
    assert clear(rig, atoken, "nobody", card_version=1).status_code == 403
    assert rig.call("post", "/cards/1/reviews/admin/clear", atoken, json={"card_version": 0}).status_code == 422
    assert state(rig) == before


def test_a_second_clear_is_refused_and_does_not_duplicate_anything(world):
    rig, _, mtoken, _, atoken, _ = world
    edit(rig, mtoken)
    assert clear(rig, atoken, "admin").status_code == 200
    assert clear(rig, atoken, "admin", card_version=1).status_code == 404
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations WHERE reviewer_role = 'admin'") == [(1,)]


def test_the_database_itself_refuses_a_second_pending_row_for_the_same_card_and_role(world):
    rig, _, mtoken, _, _, _ = world
    edit(rig, mtoken)
    con = sqlite3.connect(company_database_path("lity", rig.e.env))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO card_review_obligations (card_id, reviewer_role, status, card_version, context_json, "
                        "refresh_count, created_at, updated_at) VALUES (1, 'admin', 'pending', 5, '[]', 0, ?, ?)", (NOW, NOW))
        # a cleared history row never conflicts
        con.execute("INSERT INTO card_review_obligations (card_id, reviewer_role, status, card_version, context_json, "
                    "refresh_count, created_at, updated_at) VALUES (1, 'admin', 'cleared', 5, '[]', 0, ?, ?)", (NOW, NOW))
        con.commit()
    finally:
        con.close()


def test_retried_identical_requests_never_duplicate_pending_obligations(world):
    rig, _, mtoken, _, _, _ = world
    for n in range(4):
        edit(rig, mtoken, title=f"t{n}")
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations WHERE status = 'pending'") == [(2,)]
    assert pending(rig) == [("admin", 4), ("super_admin", 4)]


def _race(*fns):
    barrier = threading.Barrier(len(fns), timeout=TIMEOUT)

    def wrap(fn):
        def go():
            barrier.wait()
            return fn()
        return go
    calls = [Call(wrap(f)) for f in fns]
    for c in calls:
        c.start()
    return [c.result() for c in calls]


def test_two_concurrent_member_edits_leave_one_consistent_review_state(world):
    rig, _, mtoken, _, _, _ = world
    warm_up(rig, mtoken)
    seen = version(rig)  # both edits start from the same version, as two people looking at the same card would
    put = lambda title: (lambda: rig.client.put("/api/companies/lity/boards/1/cards/1", json={"title": title, "expected_version": seen}, headers=auth(mtoken)))
    ra, rb = _race(put("A"), put("B"))
    codes = [ra.status_code, rb.status_code]
    assert all(c in (200, 409, 503) for c in codes), codes  # at most one wins; the other is stale (409) or busy (503)
    assert codes.count(200) <= 1, codes
    done = codes.count(200)
    assert version(rig) == done and rig.sql("SELECT COUNT(*) FROM card_review_obligations") == [(2,)]
    assert pending(rig) == [("admin", done), ("super_admin", done)]
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(done,)]


def test_concurrent_reviews_of_the_same_obligation_clear_it_exactly_once(world):
    rig, _, mtoken, _, atoken, _ = world
    edit(rig, mtoken)
    post = lambda: rig.client.post("/api/companies/lity/boards/1/cards/1/reviews/admin/clear", json={"card_version": 1}, headers=auth(atoken))
    ra, rb = _race(post, post)
    codes = sorted([ra.status_code, rb.status_code])
    assert codes[0] == 200 and codes[1] in (404, 409, 503), codes
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations WHERE reviewer_role = 'admin' AND status = 'cleared'") == [(1,)]
    assert rig.sql("SELECT COUNT(*) FROM audit_events WHERE action = 'review_cleared'") == [(1,)]


def test_a_review_racing_a_new_member_edit_never_approves_the_wrong_version(world):
    rig, _, mtoken, _, atoken, _ = world
    edit(rig, mtoken, title="one")
    warm_up(rig, atoken)
    review = lambda: rig.client.post("/api/companies/lity/boards/1/cards/1/reviews/admin/clear", json={"card_version": 1}, headers=auth(atoken))
    v_seen = version(rig)
    change = lambda: rig.client.put("/api/companies/lity/boards/1/cards/1", json={"title": "two", "expected_version": v_seen}, headers=auth(mtoken))
    rr, rc = _race(review, change)
    assert rc.status_code in (200, 503) and rr.status_code in (200, 409, 503)
    current = version(rig)
    for role, status, v, *_ in rig.sql(OB, (1,)):
        if status == "pending":
            assert v == current  # a pending flag always refers to the latest version
        if status == "cleared":
            assert v < current or rc.status_code != 200  # an approval never covers a later version
    if rc.status_code == 200:
        assert ("admin", current) in pending(rig)  # the new edit needs a new Admin review whichever order won


# --- C. authorization -------------------------------------------------------------------------------------------------

def test_roles_cannot_clear_each_others_obligations(world):
    rig, _, mtoken, _, atoken, stoken = world
    edit(rig, mtoken)
    before = state(rig)
    assert clear(rig, atoken, "super_admin").status_code == 403  # Admin -> Super Admin's
    assert clear(rig, stoken, "admin").status_code == 403        # Super Admin through the Admin action
    assert state(rig) == before and len(pending(rig)) == 2


@pytest.mark.parametrize("who", ["sales_member", "visitor"])
def test_sales_members_and_visitors_cannot_list_or_clear_before_the_database_opens(world, who):
    rig, _, mtoken, _, atoken, _ = world
    edit(rig, mtoken)
    token = mtoken if who == "sales_member" else member(rig, "v@example.com", role="visitor")[1]
    before = rig.digest("lity")
    rig.opened.clear()
    assert reviews(rig, token).status_code == 403
    for role in ("admin", "super_admin"):
        assert clear(rig, token, role, card_version=1).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before and len(pending(rig)) == 2


def test_other_company_outsiders_and_forged_identities_are_refused(world):
    rig, _, mtoken, aid, atoken, stoken = world
    edit(rig, mtoken)
    _, outsider = member(rig, "o@example.com", slug="valor-log")
    before = rig.digest("lity")
    rig.opened.clear()
    assert reviews(rig, outsider).status_code == 403
    assert clear(rig, outsider, "admin", card_version=1).status_code == 403
    assert clear(rig, atoken, "admin", slug="valor-log", card_version=1).status_code == 403  # Admin of lity only
    assert clear(rig, atoken, "admin", slug="salcomp", card_version=1).status_code == 403
    assert "lity" not in rig.opened and rig.digest("lity") == before
    # role, identity or reviewer smuggled in the request grant nothing
    r = rig.client.post("/api/companies/lity/boards/1/cards/1/reviews/super_admin/clear?role=super_admin&user_id=1",
                        json={"card_version": 1, "reviewer_role": "super_admin", "user_id": rig.e.admin, "role": "super_admin"},
                        headers={**auth(atoken), "X-Role": "super_admin", "X-Company-Slug": "lity"})
    assert r.status_code == 403
    forged = create_access_token({"sub": str(aid), "role": "super_admin", "is_global_admin": True})
    assert clear(rig, forged, "super_admin", card_version=1).status_code == 401
    assert len(pending(rig)) == 2
    # a member's role in the identity database is read fresh: after demotion the former Admin has no review rights
    svc.change_membership_role(rig.e.session, rig.e.admin, "lity", aid, "sales_member")
    assert clear(rig, atoken, "admin", card_version=1).status_code == 403 and reviews(rig, atoken).status_code == 403


def test_the_super_admin_of_one_company_never_sees_or_clears_another_companys_reviews(world):
    rig, _, mtoken, _, _, stoken = world
    edit(rig, mtoken)
    assert reviews(rig, stoken, slug="salcomp").json()["items"] == []
    assert clear(rig, stoken, "super_admin", slug="salcomp", card_version=1).status_code == 404
    assert pending(rig) == [("admin", 1), ("super_admin", 1)]


def test_capabilities_report_review_rights_by_role(world):
    rig, _, mtoken, _, atoken, stoken = world
    caps = lambda t: rig.client.get("/api/companies/lity/me", headers=auth(t)).json()["capabilities"]["canReview"]
    assert (caps(stoken), caps(atoken), caps(mtoken)) == (True, True, False)
    assert caps(member(rig, "v@example.com", role="visitor")[1]) is False


# --- D. backward movements --------------------------------------------------------------------------------------------

def move(rig, token, card_id, column_id):
    return rig.call("put", f"/cards/{card_id}/position", token, json={"column_id": column_id, "position": 0})


def test_an_admin_moving_a_card_back_creates_a_super_admin_obligation_only(world):
    rig, _, mtoken, aid, atoken, stoken = world
    rig.sql("UPDATE cards SET column_id = 2 WHERE id = 1")
    assert move(rig, atoken, 1, 1).status_code == 200
    v = version(rig)
    assert pending(rig) == [("super_admin", v)]  # none for the Admin, whose own move it was
    item = reviews(rig, stoken).json()["items"][0]
    assert item["triggers"][0]["kind"] == "backward_move" and (item["triggers"][0]["from_title"], item["triggers"][0]["to_title"]) == ("Second", "First")
    assert reviews(rig, atoken).json()["items"] == []
    assert clear(rig, atoken, "super_admin").status_code == 403 and clear(rig, atoken, "admin").status_code == 404
    assert clear(rig, stoken, "super_admin").status_code == 200 and pending(rig) == []
    # the movement history is untouched
    assert rig.sql("SELECT event_type, user_id FROM card_stage_events WHERE event_type = 'moved'") == [("moved", aid)]


def test_a_super_admin_moving_a_card_back_creates_its_own_obligation(world):
    rig, _, _, _, _, stoken = world
    rig.sql("UPDATE cards SET column_id = 2 WHERE id = 2")
    assert move(rig, stoken, 2, 1).status_code == 200
    assert pending(rig, 2) == [("super_admin", 1)]
    assert rig.sql("SELECT event_type FROM card_stage_events WHERE card_id = 2 AND event_type = 'moved'") == [("moved",)]


def test_a_sales_member_cannot_move_back_and_leaves_no_obligation(world):
    rig, _, mtoken, _, _, _ = world
    rig.sql("UPDATE cards SET column_id = 2 WHERE id = 1")
    before = state(rig)
    assert move(rig, mtoken, 1, 1).status_code == 403
    assert state(rig) == before and pending(rig) == []


def test_forward_moves_create_no_obligation_but_move_pending_ones_to_the_new_version(world):
    rig, _, mtoken, _, atoken, stoken = world
    assert move(rig, atoken, 1, 2).status_code == 200 and pending(rig) == []
    edit(rig, mtoken, title="edited")
    rig.sql("UPDATE cards SET column_id = 1 WHERE id = 1")  # arrange: back to the first column (a plain forward move follows)
    assert move(rig, mtoken, 1, 2).status_code == 200
    assert pending(rig) == [("admin", version(rig)), ("super_admin", version(rig))]
    assert all(t["kind"] == "member_edit" for t in reviews(rig, stoken).json()["items"][0]["triggers"])


def test_repeated_backward_moves_never_leave_an_older_move_approved_as_the_latest(world):
    rig, _, _, _, atoken, stoken = world
    rig.sql("UPDATE cards SET column_id = 2 WHERE id = 1")
    assert move(rig, atoken, 1, 1).status_code == 200
    seen = reviews(rig, stoken).json()["items"][0]["card_version"]
    assert move(rig, atoken, 1, 2).status_code == 200   # forward again: the pending review follows the card
    assert move(rig, atoken, 1, 1).status_code == 200   # and back once more
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations WHERE reviewer_role = 'super_admin'") == [(1,)]
    current = version(rig)
    assert current == seen + 2 and pending(rig) == [("super_admin", current)]
    assert clear(rig, stoken, "super_admin", card_version=seen).status_code == 409
    item = reviews(rig, stoken).json()["items"][0]
    assert [t["kind"] for t in item["triggers"]] == ["backward_move", "backward_move"]
    assert clear(rig, stoken, "super_admin", card_version=current).status_code == 200
    assert rig.sql("SELECT COUNT(*) FROM card_stage_events WHERE event_type = 'moved'") == [(3,)]


def test_ai_backward_moves_by_an_admin_and_admin_level_undo_are_flagged_too(world, monkeypatch):
    rig, _, _, _, atoken, _ = world
    rig.sql("UPDATE cards SET column_id = 2 WHERE id = 1")
    fake_ai(monkeypatch, [{"type": "move", "card_id": 1, "column_id": 1}])
    assert ai(rig, atoken).status_code == 200
    assert pending(rig) == [("super_admin", 1)]
    # an Admin undoing its own forward move sends the card back: the same obligation applies
    rig.sql("DELETE FROM card_review_obligations")
    rig.sql("DELETE FROM undo_entries")
    assert move(rig, atoken, 2, 2).status_code == 200 and pending(rig, 2) == []
    assert undo(rig, atoken).status_code == 200
    assert pending(rig, 2) == [("super_admin", version(rig, 2))]


# --- E. lifecycle and set-up ---------------------------------------------------------------------------------------------

def test_deleting_a_card_closes_its_pending_reviews_and_keeps_the_rows(world):
    rig, _, mtoken, _, atoken, stoken = world
    edit(rig, mtoken)
    assert rig.call("delete", "/cards/1", stoken).status_code == 204
    assert rig.sql("SELECT status, closed_reason FROM card_review_obligations WHERE card_id = 1") == [("void", "card_deleted")] * 2
    assert reviews(rig, atoken).json()["items"] == [] and clear(rig, atoken, "admin", card_version=1).status_code == 404


def test_without_the_review_tables_changes_needing_a_review_are_refused_not_skipped(world):
    rig, _, mtoken, _, atoken, stoken = world
    rig.sql("DROP TABLE card_review_obligations")
    rig.sql("DROP TABLE card_versions")
    r = rig.call("put", "/cards/1", mtoken, json={"title": "x"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "review_not_initialized"
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("card 1",)]
    assert move(rig, atoken, 1, 2).status_code == 503
    assert reviews(rig, atoken).status_code == 503 and clear(rig, atoken, "admin", card_version=1).status_code == 503
    # the board read carries card versions, so it fails closed too (503) without the version table
    assert rig.call("get", "", atoken).status_code == 503


def test_the_init_script_creates_the_tables_only_with_apply_and_is_idempotent(rig, monkeypatch):
    from init_company_card_reviews import init_company_card_reviews

    rig.sql("DROP TABLE card_review_obligations")
    rig.sql("DROP TABLE card_versions")
    rig.runtime.dispose()
    env = rig.e.env
    before = rig.digest("lity")
    assert init_company_card_reviews("lity", environ=env) is True and rig.digest("lity") == before  # dry run
    assert init_company_card_reviews("lity", apply=True, environ=env) is True
    assert init_company_card_reviews("lity", apply=True, environ=env) is False
    tables = {r[0] for r in rig.sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"card_versions", "card_review_obligations"} <= tables
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]


def test_the_legacy_runtime_is_untouched():
    import card_rules
    from types import SimpleNamespace

    class Db:  # a legacy session carries no company slug
        info = {}
    assert card_reviews.is_company_session(Db()) is False
    card_reviews.note_member_edit(Db(), SimpleNamespace(id=1), SimpleNamespace(id=1), {"title": {"old": "a", "new": "b"}})
    assert Db.info == {} and card_rules.record_member_edit is not None


# --- F. undo of a deletion never drops a pending review ---------------------------------------------------------------

def test_undo_of_a_deletion_is_refused_while_reviews_were_pending(world):
    rig, _, mtoken, _, _, stoken = world
    edit(rig, mtoken)
    assert len(pending(rig)) == 2
    cards_before = rig.sql("SELECT COUNT(*) FROM cards")[0][0]
    entries_before = rig.sql("SELECT COUNT(*) FROM undo_entries")[0][0]  # the member's own edit entry
    assert rig.call("delete", "/cards/1", stoken).status_code == 204
    assert rig.sql("SELECT status, closed_reason FROM card_review_obligations WHERE card_id = 1") == [("void", "card_deleted")] * 2
    assert undo(rig, stoken, method="get").json()["available"] is False
    assert rig.sql("SELECT COUNT(*) FROM undo_entries") == [(entries_before,)]  # no entry for the deletion
    assert undo(rig, stoken).status_code == 404
    assert rig.sql("SELECT COUNT(*) FROM cards")[0][0] == cards_before - 1
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'edited'") == [(0,)]
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations WHERE status = 'pending'") == [(0,)]
    assert rig.sql("SELECT COUNT(*) FROM audit_events WHERE action = 'delete'") == [(1,)]


def test_undo_of_a_deletion_still_works_without_pending_reviews(world):
    rig, _, _, _, _, stoken = world
    assert rig.call("delete", "/cards/1", stoken).status_code == 204
    assert undo(rig, stoken).status_code == 200
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'card 1'") == [(1,)]
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations") == [(0,)]


def test_undo_of_a_deletion_ignores_cleared_obligations(world):
    rig, _, mtoken, _, atoken, stoken = world
    edit(rig, mtoken)
    assert clear(rig, atoken, "admin").status_code == 200
    assert clear(rig, stoken, "super_admin").status_code == 200
    assert pending(rig) == []
    assert rig.call("delete", "/cards/1", stoken).status_code == 204
    assert undo(rig, stoken).status_code == 200
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'edited'") == [(1,)]
    assert sorted(r[0] for r in rig.sql("SELECT status FROM card_review_obligations")) == ["cleared", "cleared"]


def test_undo_of_a_deletion_ignores_obligations_already_voided(world):
    rig, _, _, _, _, stoken = world
    rig.sql("INSERT INTO card_review_obligations (card_id, reviewer_role, status, card_version, context_json, refresh_count, "
            "created_at, updated_at, closed_reason) VALUES (1, 'admin', 'void', 1, '[]', 0, ?, ?, 'card_deleted')", (NOW, NOW))
    assert rig.call("delete", "/cards/1", stoken).status_code == 204
    assert undo(rig, stoken).status_code == 200
