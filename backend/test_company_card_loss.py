"""Mandatory loss reason when a card enters the lost stage (central/company mode): validation, history, atomicity,
authorization, AI and Undo. Synthetic data and disposable tmp_path databases only."""
import threading

import pytest

from test_company_admin_products import company_admin
from test_company_card_writes import NOW, admin, member
from test_company_card_writes import fake_ai, rig  # noqa: F401  (fixtures/helpers)
from test_company_concurrency_integrity import Call, TIMEOUT, auth, warm_up
from test_company_undo_import import undo

LOST = 3


def lost_column(rig, position=2, kind="lost", title="Closed - Lost", column_id=LOST):
    rig.sql("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
            "VALUES (?, 1, ?, ?, ?, ?, ?)", (column_id, title, position, kind, NOW, NOW))


def state(rig, slug="lity"):
    """Everything a refused move must leave alone (the actor's own local reference row is not part of it)."""
    return [rig.sql(q, slug=slug) for q in (
        "SELECT id, column_id, position FROM cards ORDER BY id", "SELECT * FROM card_stage_events ORDER BY id",
        "SELECT * FROM card_loss_events ORDER BY id", "SELECT * FROM card_review_obligations ORDER BY id",
        "SELECT * FROM card_versions ORDER BY card_id", "SELECT * FROM undo_entries ORDER BY id",
        "SELECT * FROM audit_events ORDER BY id")]


def put_move(rig, token, card_id, column_id, **extra):
    return rig.call("put", f"/cards/{card_id}/position", token, json={"column_id": column_id, "position": 0, **extra})


def losses(rig):
    return rig.sql("SELECT card_id, from_column_id, to_column_id, reason, user_id, source FROM card_loss_events ORDER BY id")


def column_of(rig, card_id=1):
    return rig.sql("SELECT column_id FROM cards WHERE id = ?", (card_id,))[0][0]


@pytest.fixture
def world(rig):
    """Board 1: First(1), Second(2), Closed-Lost(3, kind lost). A member owns card 1; card 2 is unassigned."""
    lost_column(rig)
    uid, mtoken = member(rig)
    rig.assign(1, uid)
    aid, atoken = company_admin(rig)
    return rig, uid, mtoken, aid, atoken, admin(rig)


# --- A. validation -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("extra", [{}, {"loss_reason": None}, {"loss_reason": ""}, {"loss_reason": "   \n\t "},
                                   {"loss_reason": 5}, {"loss_reason": ["x"]}])
def test_a_move_into_lost_without_a_usable_reason_is_refused_and_changes_nothing(world, extra):
    rig, _, mtoken, _, _, _ = world
    before = state(rig)
    r = put_move(rig, mtoken, 1, LOST, **extra)
    assert r.status_code == 422, r.text
    if "loss_reason" not in extra or not isinstance(extra["loss_reason"], (int, list)):
        assert r.json()["detail"]["code"] == "loss_reason_required"
    assert column_of(rig) == 1 and state(rig) == before


def test_a_valid_reason_is_accepted_and_trimmed(world):
    rig, uid, mtoken, _, _, _ = world
    assert put_move(rig, mtoken, 1, LOST, loss_reason="  Lost on price to a local supplier \n").status_code == 200
    assert column_of(rig) == LOST
    assert losses(rig) == [(1, 1, LOST, "Lost on price to a local supplier", uid, "api")]


def test_the_maximum_length_is_enforced_on_the_trimmed_reason(world):
    rig, _, mtoken, _, _, _ = world
    before = state(rig)
    assert put_move(rig, mtoken, 1, LOST, loss_reason="x" * 1001).status_code == 422
    assert state(rig) == before
    assert put_move(rig, mtoken, 1, LOST, loss_reason="  " + "x" * 1000 + "  ").status_code == 200
    assert losses(rig)[0][3] == "x" * 1000


def test_skipping_stages_straight_into_lost_still_needs_the_reason(world):
    rig, _, mtoken, _, _, _ = world
    assert put_move(rig, mtoken, 1, LOST).status_code == 422  # from the first stage over the second
    assert put_move(rig, mtoken, 1, LOST, loss_reason="Customer cancelled the project").status_code == 200
    assert column_of(rig) == LOST


def test_other_moves_need_no_reason_and_record_none(world):
    rig, _, mtoken, _, atoken, _ = world
    assert put_move(rig, mtoken, 1, 2).status_code == 200
    assert put_move(rig, atoken, 1, 1, loss_reason="ignored").status_code == 200  # a reason elsewhere is not stored
    assert losses(rig) == []


def test_lost_is_decided_by_stage_kind_never_by_the_title(world):
    rig, _, mtoken, _, _, _ = world
    lost_column(rig, position=3, kind="open", title="Lost", column_id=4)  # titled Lost, but an open stage
    assert put_move(rig, mtoken, 1, 4).status_code == 200 and losses(rig) == []
    rig.sql("UPDATE columns SET title = 'Archive' WHERE id = ?", (LOST,))  # renamed, still the lost stage
    rig.sql("UPDATE cards SET column_id = 1 WHERE id = 1")
    assert put_move(rig, mtoken, 1, LOST).status_code == 422


def test_reordering_inside_the_lost_stage_is_not_a_new_loss(world):
    rig, _, mtoken, _, _, _ = world
    assert put_move(rig, mtoken, 1, LOST, loss_reason="Lost on price").status_code == 200
    assert put_move(rig, mtoken, 1, LOST).status_code == 200  # same column, new position
    assert len(losses(rig)) == 1


# --- B. persistence and history ------------------------------------------------------------------------------------

def test_the_reason_is_stored_with_the_actor_time_and_the_matching_stage_event(world):
    rig, uid, mtoken, _, _, _ = world
    assert put_move(rig, mtoken, 1, LOST, loss_reason="No budget this year").status_code == 200
    (row,) = rig.sql("SELECT occurred_at FROM card_loss_events")
    assert row[0]
    assert rig.sql("SELECT event_type, to_column_id, user_id FROM card_stage_events WHERE event_type = 'lost'") == [("lost", LOST, uid)]


def test_moving_out_of_lost_keeps_the_reason_and_a_second_loss_is_a_separate_event(world):
    rig, uid, mtoken, aid, atoken, _ = world
    put_move(rig, mtoken, 1, LOST, loss_reason="First loss: price")
    assert put_move(rig, mtoken, 1, LOST, loss_reason="not a new loss").status_code == 200
    assert put_move(rig, mtoken, 1, 2).status_code == 403  # a member cannot reopen
    assert put_move(rig, atoken, 1, 2).status_code == 200  # an Admin can
    assert [r[3] for r in losses(rig)] == ["First loss: price"]
    assert put_move(rig, atoken, 1, LOST).status_code == 422  # a new loss needs a new reason
    assert put_move(rig, atoken, 1, LOST, loss_reason="Second loss: project cancelled").status_code == 200
    assert [(r[3], r[4]) for r in losses(rig)] == [("First loss: price", uid), ("Second loss: project cancelled", aid)]
    history = rig.call("get", "/cards/1/loss-events", mtoken).json()
    assert [(h["reason"], h["source"]) for h in history] == [("First loss: price", "api"), ("Second loss: project cancelled", "api")]
    assert history[0]["recorded_by"] and history[0]["occurred_at"]


def test_unrelated_edits_and_later_moves_never_touch_earlier_loss_events(world):
    rig, _, mtoken, _, atoken, stoken = world
    put_move(rig, mtoken, 1, LOST, loss_reason="Price")
    before = rig.sql("SELECT * FROM card_loss_events")
    assert rig.call("put", "/cards/1", atoken, json={"title": "renamed", "details": "d"}).status_code == 200
    assert rig.call("put", "/cards/1", mtoken, json={"deal_value": 3}).status_code == 200
    assert put_move(rig, stoken, 1, 2).status_code == 200
    assert rig.sql("SELECT * FROM card_loss_events") == before


def test_undo_keeps_the_loss_event_and_restores_the_stage(world):
    rig, _, mtoken, _, _, _ = world
    put_move(rig, mtoken, 1, LOST, loss_reason="Price")
    assert undo(rig, mtoken).status_code == 200
    assert column_of(rig) == 1
    assert [r[3] for r in losses(rig)] == ["Price"]  # the fact that the card entered LOST, and why, stays in history
    kinds = [r[0] for r in rig.sql("SELECT event_type FROM card_stage_events WHERE card_id = 1 ORDER BY id")]
    assert "lost" in kinds and kinds[-1] == "reopened"


# --- C. atomicity, authorization, isolation, concurrency --------------------------------------------------------------

def test_a_refused_move_writes_no_event_obligation_undo_entry_or_audit(world):
    rig, _, mtoken, _, atoken, _ = world
    rig.sql("UPDATE columns SET position = -1 WHERE id = ?", (LOST,))  # LOST before the others: this move is backward
    rig.sql("UPDATE cards SET column_id = 2 WHERE id = 2")
    before = state(rig)
    assert put_move(rig, atoken, 2, LOST, loss_reason="  ").status_code == 422
    assert state(rig) == before  # in particular: no SUPERADMIN review obligation for a move that did not happen
    assert put_move(rig, atoken, 2, LOST, loss_reason="Cancelled").status_code == 200
    assert rig.sql("SELECT reviewer_role, status FROM card_review_obligations WHERE card_id = 2") == [("super_admin", "pending")]


def test_a_successful_move_into_lost_keeps_the_review_rules(world):
    rig, _, mtoken, _, _, _ = world
    from test_company_card_reviews import pending, version
    assert put_move(rig, mtoken, 1, LOST, loss_reason="Price").status_code == 200  # forward: no obligation of its own
    assert pending(rig) == [] and version(rig) == 1
    rig.call("put", "/cards/1", mtoken, json={"title": "after"})  # a member edit still creates both reviews
    assert pending(rig) == [("admin", 2), ("super_admin", 2)]


def test_a_sales_member_cannot_move_unassigned_or_foreign_cards_into_lost(world):
    rig, _, mtoken, _, _, _ = world
    other, _ = member(rig, "o@example.com")
    rig.assign(2, other)
    before = state(rig)
    for card in (2, 999):
        assert put_move(rig, mtoken, card, LOST, loss_reason="Price").status_code in (403, 404)
    rig.sql("UPDATE cards SET salesperson_id = NULL WHERE id = 2")
    assert put_move(rig, mtoken, 2, LOST, loss_reason="Price").status_code == 403
    assert state(rig) == before


def test_admin_and_super_admin_keep_their_movement_rights_with_a_reason(world):
    rig, _, _, _, atoken, stoken = world
    assert put_move(rig, atoken, 2, LOST, loss_reason="a").status_code == 200   # any card, skipping stages
    assert put_move(rig, stoken, 1, LOST, loss_reason="b").status_code == 200
    assert put_move(rig, stoken, 1, 1).status_code == 200                      # and back
    assert [r[3] for r in losses(rig)] == ["a", "b"]


def test_visitors_and_outsiders_are_refused_before_the_database_opens(world):
    rig, _, _, _, _, _ = world
    _, visitor = member(rig, "v@example.com", role="visitor")
    _, outsider = member(rig, "o@example.com", slug="valor-log")
    before = rig.digest("lity")
    rig.opened.clear()
    for token in (visitor, outsider):
        assert put_move(rig, token, 1, LOST, loss_reason="Price").status_code == 403
    assert rig.call("get", "/cards/1/loss-events", outsider).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before
    assert rig.call("get", "/cards/1/loss-events", visitor).status_code == 200  # reads stay open to every member


def test_loss_events_stay_in_their_own_company(world):
    rig, _, mtoken, _, _, stoken = world
    put_move(rig, mtoken, 1, LOST, loss_reason="Price")
    assert rig.sql("SELECT COUNT(*) FROM card_loss_events", slug="salcomp") == [(0,)]
    assert rig.call("get", "/cards/1/loss-events", stoken, slug="salcomp").status_code == 200  # salcomp's own card 1
    assert rig.call("get", "/cards/1/loss-events", stoken, slug="salcomp").json() == []


def test_without_the_loss_table_a_move_into_lost_is_refused_not_recorded_without_a_reason(world):
    rig, _, mtoken, _, _, _ = world
    rig.sql("DROP TABLE card_loss_events")
    before = rig.sql("SELECT id, column_id FROM cards ORDER BY id")
    r = put_move(rig, mtoken, 1, LOST, loss_reason="Price")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "loss_not_initialized"
    assert rig.sql("SELECT id, column_id FROM cards ORDER BY id") == before
    assert put_move(rig, mtoken, 1, 2).status_code == 200  # other moves are unaffected


def test_two_concurrent_moves_into_lost_leave_one_loss_event_and_a_consistent_card(world):
    rig, _, mtoken, _, atoken, _ = world
    warm_up(rig, mtoken)
    barrier = threading.Barrier(2, timeout=TIMEOUT)

    def mk(token, reason):
        def go():
            barrier.wait()
            return rig.client.put("/api/companies/lity/boards/1/cards/1/position",
                                  json={"column_id": LOST, "position": 0, "loss_reason": reason}, headers=auth(token))
        return go
    a, b = Call(mk(mtoken, "Reason A")), Call(mk(atoken, "Reason B"))
    a.start(); b.start()
    codes = [a.result().status_code, b.result().status_code]
    assert all(c in (200, 503) for c in codes), codes
    if codes.count(200):
        assert column_of(rig) == LOST
        assert len(losses(rig)) == 1  # the second request found the card already lost: a reorder, not a new loss
        assert rig.sql("SELECT COUNT(*) FROM card_stage_events WHERE event_type = 'lost'") == [(1,)]
    else:
        assert losses(rig) == [] and column_of(rig) == 1


# --- D. AI -----------------------------------------------------------------------------------------------------------

def ai_with(rig, token, **body):
    return rig.client.post("/api/companies/lity/boards/1/ai", headers={"Authorization": f"Bearer {token}"},
                           json={"message": "close it", **body})


def test_ai_move_into_lost_with_a_reason_from_the_person_succeeds(world, monkeypatch):
    rig, uid, mtoken, _, _, _ = world
    fake_ai(monkeypatch, [{"type": "move", "card_id": 1, "column_id": LOST}])
    r = ai_with(rig, mtoken, loss_reason="  Customer chose a competitor ")
    applied = r.json()["actions_applied"]
    assert [x["type"] for x in applied["successful"]] == ["move"] and not applied["failed"]
    assert losses(rig) == [(1, 1, LOST, "Customer chose a competitor", uid, "ai")]


@pytest.mark.parametrize("body", [{}, {"loss_reason": ""}, {"loss_reason": "   "}])
def test_ai_move_into_lost_without_a_reason_fails_safely(world, monkeypatch, body):
    rig, _, mtoken, _, _, _ = world
    fake_ai(monkeypatch, [{"type": "move", "card_id": 1, "column_id": LOST}])
    before = state(rig)
    applied = ai_with(rig, mtoken, **body).json()["actions_applied"]
    assert applied["successful"] == [] and "loss_reason" in applied["failed"][0]["reason"]
    assert column_of(rig) == 1
    assert state(rig)[:2] == before[:2] and state(rig)[2:6] == before[2:6]  # no event, loss, obligation, version, undo entry


def test_the_models_own_reason_is_never_used(world, monkeypatch):
    rig, _, mtoken, _, _, _ = world
    fake_ai(monkeypatch, [{"type": "move", "card_id": 1, "column_id": LOST, "loss_reason": "Model made this up",
                           "reason": "also invented", "details": "Lost on price"}])
    applied = ai_with(rig, mtoken).json()["actions_applied"]
    assert applied["successful"] == [] and column_of(rig) == 1 and losses(rig) == []
    # and when the person did give one, the person's text is what is stored
    applied = ai_with(rig, mtoken, loss_reason="The user's reason").json()["actions_applied"]
    assert len(applied["successful"]) == 1 and [r[3] for r in losses(rig)] == ["The user's reason"]


def test_ai_keeps_the_role_rules_and_company_isolation(world, monkeypatch):
    rig, _, mtoken, _, atoken, _ = world
    _, visitor = member(rig, "v@example.com", role="visitor")
    _, outsider = member(rig, "o@example.com", slug="valor-log")
    fake_ai(monkeypatch, [{"type": "move", "card_id": 2, "column_id": LOST}])  # card 2 is not the member's
    applied = ai_with(rig, mtoken, loss_reason="Price").json()["actions_applied"]
    assert applied["successful"] == [] and losses(rig) == []
    before = rig.digest("lity")
    rig.opened.clear()
    assert ai_with(rig, visitor, loss_reason="x").status_code == 403 and ai_with(rig, outsider, loss_reason="x").status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before
    assert ai_with(rig, atoken, loss_reason="Admin reason").json()["actions_applied"]["successful"][0]["card_id"] == 2


def test_ai_backward_move_into_lost_keeps_the_super_admin_review(world, monkeypatch):
    rig, _, _, _, atoken, _ = world
    rig.sql("UPDATE columns SET position = -1 WHERE id = ?", (LOST,))
    rig.sql("UPDATE cards SET column_id = 2 WHERE id = 2")
    fake_ai(monkeypatch, [{"type": "move", "card_id": 2, "column_id": LOST}])
    assert ai_with(rig, atoken).json()["actions_applied"]["successful"] == []
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations") == [(0,)]
    assert len(ai_with(rig, atoken, loss_reason="Cancelled").json()["actions_applied"]["successful"]) == 1
    assert rig.sql("SELECT reviewer_role FROM card_review_obligations") == [("super_admin",)]


# --- E. the Cards workbook import and set-up ----------------------------------------------------------------------------

def test_the_cards_workbook_import_cannot_move_a_card_into_lost(world):
    from test_company_cards_excel_import import book, preview, row
    rig, _, _, _, _, stoken = world
    rig.assign(1, rig.e.admin, "root")
    body = preview(rig, stoken, book([row(card_id=1, stage="Closed - Lost")])).json()
    errors = [e["code"] for r in body["rows"] for e in r.get("errors", [])]
    assert "loss_reason_required" in errors
    assert column_of(rig) == 1


def test_the_init_script_creates_the_table_only_with_apply_and_is_idempotent(rig):
    from init_company_card_loss import init_company_card_loss

    rig.sql("DROP TABLE card_loss_events")
    rig.runtime.dispose()
    before = rig.digest("lity")
    assert init_company_card_loss("lity", environ=rig.e.env) is True and rig.digest("lity") == before  # dry run
    assert init_company_card_loss("lity", apply=True, environ=rig.e.env) is True
    assert init_company_card_loss("lity", apply=True, environ=rig.e.env) is False
    assert rig.sql("SELECT COUNT(*) FROM card_loss_events") == [(0,)] and rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]


# --- F. creating a card straight into the lost stage ---------------------------------------------------------------------

def create_in_lost(rig, token, **extra):
    return rig.call("post", "/cards", token, json={"column_id": LOST, "title": "Born lost", **extra})


@pytest.mark.parametrize("extra", [{}, {"loss_reason": ""}, {"loss_reason": "  \n"}, {"loss_reason": "x" * 1001}])
def test_creating_in_lost_without_a_valid_reason_is_refused_and_leaves_nothing(world, extra):
    rig, uid, mtoken, _, _, _ = world
    before = rig.sql("SELECT COUNT(*) FROM cards"), rig.sql("SELECT COUNT(*) FROM audit_events"), rig.sql("SELECT COUNT(*) FROM undo_entries")
    r = create_in_lost(rig, mtoken, **extra)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] in ("loss_reason_required", "loss_reason_too_long")
    assert (rig.sql("SELECT COUNT(*) FROM cards"), rig.sql("SELECT COUNT(*) FROM audit_events"), rig.sql("SELECT COUNT(*) FROM undo_entries")) == before
    assert losses(rig) == [] and rig.sql("SELECT COUNT(*) FROM card_stage_events WHERE event_type = 'created' AND to_column_id = ?", (LOST,)) == [(0,)]


def test_a_member_creates_in_lost_with_a_reason_and_it_is_recorded(world):
    rig, uid, mtoken, _, _, _ = world
    r = create_in_lost(rig, mtoken, loss_reason="  Never qualified ")
    assert r.status_code == 201 and r.json()["salesperson_id"] == uid  # still the member's own card
    (card_id,) = (r.json()["id"],)
    assert losses(rig) == [(card_id, None, LOST, "Never qualified", uid, "api")]
    assert rig.call("get", f"/cards/{card_id}/loss-events", mtoken).json()[0]["reason"] == "Never qualified"


@pytest.mark.parametrize("who", ["company_admin", "super_admin"])
def test_admin_level_creates_in_lost_for_an_eligible_salesperson_with_a_reason(world, who):
    rig, uid, _, aid, atoken, stoken = world
    token, actor = (atoken, aid) if who == "company_admin" else (stoken, rig.e.admin)
    assert create_in_lost(rig, token, loss_reason="x").status_code == 422  # an Admin-level caller must still name a salesperson
    r = create_in_lost(rig, token, loss_reason="Customer went elsewhere", salesperson_id=uid)
    assert r.status_code == 201 and r.json()["salesperson_id"] == uid
    assert losses(rig)[-1][3:] == ("Customer went elsewhere", actor, "api")  # recorded against the actor, not the owner


def test_a_failure_while_creating_in_lost_leaves_no_card_loss_event_version_or_obligation(world, monkeypatch):
    import company_routes

    rig, _, mtoken, _, _, _ = world

    def boom(*a, **k):
        raise RuntimeError("synthetic failure after the insert")
    monkeypatch.setattr(company_routes, "audit_new_card_owner", boom)
    with pytest.raises(RuntimeError):
        create_in_lost(rig, mtoken, loss_reason="Price")
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]
    assert losses(rig) == [] and rig.sql("SELECT COUNT(*) FROM card_versions") == [(0,)]
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations") == [(0,)]


def test_creating_in_other_stages_needs_no_reason(world):
    rig, _, mtoken, _, _, _ = world
    assert rig.call("post", "/cards", mtoken, json={"column_id": 1, "title": "Normal", "loss_reason": "ignored"}).status_code == 201
    assert losses(rig) == []


def test_ai_create_in_lost_needs_the_persons_reason(world, monkeypatch):
    rig, uid, mtoken, _, _, _ = world
    fake_ai(monkeypatch, [{"type": "create", "column_id": LOST, "title": "AI lost", "loss_reason": "Model made this up"}])
    applied = ai_with(rig, mtoken).json()["actions_applied"]
    assert applied["successful"] == [] and "loss_reason" in applied["failed"][0]["reason"]
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'AI lost'") == [(0,)] and losses(rig) == []
    applied = ai_with(rig, mtoken, loss_reason="  The user's reason ").json()["actions_applied"]
    assert len(applied["successful"]) == 1
    assert [(r[1], r[3], r[5]) for r in losses(rig)] == [(None, "The user's reason", "ai")]


def test_the_workbook_import_cannot_create_a_card_in_lost_either(world):
    from test_company_cards_excel_import import book, preview, row
    rig, _, _, _, _, stoken = world
    body = preview(rig, stoken, book([row(title="New", stage="Closed - Lost")])).json()
    assert "loss_reason_required" in [e["code"] for r in body["rows"] for e in r.get("errors", [])]
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]


# --- G. reports and KPIs are unaffected by loss history ------------------------------------------------------------------

def test_loss_events_never_change_or_duplicate_what_reports_and_kpis_read(world):
    rig, _, mtoken, _, atoken, stoken = world
    rig.sql("UPDATE cards SET deal_value = 100 WHERE id = 1")
    put_move(rig, mtoken, 1, LOST, loss_reason="Price")
    put_move(rig, atoken, 1, 2)
    put_move(rig, atoken, 1, LOST, loss_reason="Cancelled")   # a second, separate loss event for the same card
    assert len(losses(rig)) == 2
    cards_before = rig.sql("SELECT id, column_id, title, deal_value, deal_value_next_year, salesperson_id FROM cards ORDER BY id")

    def read():
        # `as_of` is the clock of the request, not data
        return tuple({k: v for k, v in rig.call("get", path, stoken).json().items() if k != "as_of"}
                     for path in ("/report", "/kpis/summary", "/kpis/pipeline"))
    with_events = read()
    report = with_events[0]
    assert (report["kpis"]["lost_count"], report["kpis"]["open_count"]) == (1, 1)  # one lost card counted once, one open
    rig.sql("DELETE FROM card_loss_events")
    assert read() == with_events  # reports and KPIs do not read the loss table at all
    assert rig.sql("SELECT id, column_id, title, deal_value, deal_value_next_year, salesperson_id FROM cards ORDER BY id") == cards_before


# --- H. Undo never produces a LOST state without a recorded loss reason ----------------------------------------------------

def _entry(rig, user_id, steps, description="forged"):
    import json
    rig.sql("INSERT INTO undo_entries (user_id, board_id, description, steps, created_at) VALUES (?, 1, ?, ?, ?)",
            (user_id, description, json.dumps(steps), NOW))


def test_undo_may_restore_a_lost_state_whose_loss_event_exists_and_adds_no_new_event(world):
    rig, _, mtoken, _, atoken, _ = world
    assert put_move(rig, mtoken, 1, LOST, loss_reason="Price").status_code == 200
    original = rig.sql("SELECT * FROM card_loss_events")
    assert put_move(rig, atoken, 1, 2).status_code == 200      # an Admin reopens it
    assert undo(rig, atoken).status_code == 200                 # ...and undoes that: LOST again, no new reason asked
    assert column_of(rig) == LOST
    assert rig.sql("SELECT * FROM card_loss_events") == original  # same single row: nothing created, nothing changed
    assert [r[0] for r in rig.sql("SELECT event_type FROM card_stage_events WHERE card_id = 1 ORDER BY id")][-2:] == ["reopened", "lost"]


def test_undo_refuses_to_put_a_card_back_into_lost_when_no_loss_reason_was_ever_recorded(world):
    rig, _, _, _, atoken, _ = world
    rig.sql("UPDATE cards SET column_id = ? WHERE id = 2", (LOST,))     # a LOST card from before reasons existed
    assert put_move(rig, atoken, 2, 2).status_code == 200               # an Admin moves it out
    before = state(rig)
    r = undo(rig, atoken)
    assert r.status_code == 409 and "loss reason" in r.json()["detail"]
    assert column_of(rig, 2) == 2 and losses(rig) == []
    assert rig.sql("SELECT * FROM card_loss_events") == [] and rig.sql("SELECT COUNT(*) FROM undo_entries") == [(0,)]
    assert [s for s in state(rig)[:3]] == [s for s in before[:3]]  # cards, stage events and loss events untouched by the refusal


def test_a_stale_or_forged_undo_step_into_lost_is_refused_for_every_role(world):
    rig, uid, mtoken, aid, atoken, stoken = world
    step = {"op": "move", "card_id": 1, "column_id": LOST, "position": 0, "expect": {"column_id": 1}}
    rig.assign(1, uid)
    for token, who in ((mtoken, uid), (atoken, aid)):
        rig.call("get", "/undo", token)  # ensures the actor's local reference row
        _entry(rig, who, [step])
        r = undo(rig, token)
        assert r.status_code == 409, r.text
        assert column_of(rig) == 1 and losses(rig) == []
    # the Super Admin gets no exception either
    rig.call("get", "/undo", stoken)
    _entry(rig, rig.e.admin, [step])
    assert undo(rig, stoken).status_code == 409 and column_of(rig) == 1


def test_a_deleted_lost_card_cannot_be_restored_by_undo(world):
    rig, _, mtoken, _, _, stoken = world
    put_move(rig, mtoken, 1, LOST, loss_reason="Price")
    assert rig.call("delete", "/cards/1", stoken).status_code == 204
    assert not rig.call("get", "/undo", stoken).json()["available"]          # not offered
    assert undo(rig, stoken).status_code == 404
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(0,)]
    assert [r[3] for r in losses(rig)] == ["Price"]                          # the history is untouched
    # a card of an open stage is still restorable, as before
    assert rig.call("delete", "/cards/2", stoken).status_code == 204
    assert undo(rig, stoken).status_code == 200 and rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'card 2'") == [(1,)]


def test_a_stale_create_step_into_lost_is_refused_at_apply_time(world):
    rig, _, _, _, _, stoken = world
    rig.call("get", "/undo", stoken)
    _entry(rig, rig.e.admin, [{"op": "create", "column_id": LOST, "position": 0, "fields": {"title": "Ghost"}}])
    r = undo(rig, stoken)
    assert r.status_code == 409 and rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'Ghost'") == [(0,)]


# --- I. AI instructions ------------------------------------------------------------------------------------------------------

def test_the_ai_is_told_never_to_invent_a_loss_reason_and_company_boards_say_so(world, monkeypatch):
    import routes
    from ai_kanban import SYSTEM_PROMPT

    assert '"loss_reason_required": true' in SYSTEM_PROMPT and "NEVER invent a loss reason" in SYSTEM_PROMPT
    assert "USER stated explicitly" in SYSTEM_PROMPT and "ask them for it in your response" in SYSTEM_PROMPT
    seen = {}

    async def spy(board_state, history, message):
        seen.update(board_state)
        return {"success": True, "response": "ok", "actions": [], "confidence": 1, "tokens_used": 0}
    monkeypatch.setattr(routes, "call_ai_with_board", spy)
    rig, _, mtoken, _, _, _ = world
    assert ai_with(rig, mtoken).status_code == 200
    assert seen["loss_reason_required"] is True
