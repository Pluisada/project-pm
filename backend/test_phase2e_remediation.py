"""Phase 2E remediation: M3 (AI scoped to the requested board), M2 (company Admin content edits audited),
M1 (authority read again before an in-flight AI request, import confirmation or revert writes).

Synthetic identities and tmp_path databases only. The M1 tests pause the request at a defined point with
threading events, change the membership from the test thread, then resume.
"""
import json
import threading

import pytest

import bid_import_revert
import bid_import_service
import import_routes
import routes
import identity_service as svc
from identity_models import IdentityCompany, IdentityUser
from test_company_admin_products import company_admin
from test_company_card_writes import NOW, admin, member
from test_company_card_writes import ai, fake_ai
from test_company_undo_import import confirm, imp, preview, rig, run_import  # noqa: F401  (fixture/helpers)

FIELDS = "id, column_id, position, title, salesperson_id"


def board2_card(rig, owner=None):
    rig.sql("INSERT INTO cards (id, column_id, title, position, salesperson_id, created_at, updated_at) "
            "VALUES (3, 10, 'other board card', 0, ?, ?, ?)", (owner, NOW, NOW))
    return 3


def snapshot(rig):
    return (rig.sql(f"SELECT {FIELDS} FROM cards ORDER BY id"), rig.sql("SELECT COUNT(*) FROM audit_events"),
            rig.sql("SELECT COUNT(*) FROM card_stage_events"), rig.sql("SELECT COUNT(*) FROM undo_entries"))


# --- M3 ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("who", ["admin", "super"])
def test_ai_cannot_update_move_or_delete_a_card_of_another_board(rig, monkeypatch, who):
    token = company_admin(rig)[1] if who == "admin" else admin(rig)
    card = board2_card(rig)
    fake_ai(monkeypatch, [{"type": "update", "card_id": card, "title": "hijacked"},
                          {"type": "move", "card_id": card, "column_id": 2},
                          {"type": "delete", "card_id": card}])
    before = snapshot(rig)
    applied = ai(rig, token).json()["actions_applied"]
    assert applied["successful"] == [] and len(applied["failed"]) == 3
    assert snapshot(rig) == before  # no card, audit, stage-history or undo record changed


def test_valid_same_board_ai_actions_still_work(rig, monkeypatch):
    token = admin(rig)
    board2_card(rig)
    fake_ai(monkeypatch, [{"type": "update", "card_id": 1, "title": "same board"},
                          {"type": "move", "card_id": 2, "column_id": 2}, {"type": "delete", "card_id": 1}])
    applied = ai(rig, token).json()["actions_applied"]
    assert len(applied["successful"]) == 3 and not applied["failed"]
    assert rig.sql("SELECT title FROM cards WHERE id = 3") == [("other board card",)]


# --- M2 ----------------------------------------------------------------------------------------------

def edits(rig):
    return rig.sql("SELECT action, actor_user_id, card_id, details FROM audit_events WHERE action = 'edit' ORDER BY id")


def test_admin_editing_another_users_card_is_audited_with_changed_fields_and_no_member_badge(rig):
    aid, token = company_admin(rig)
    m, _ = member(rig, "m@example.com")
    rig.assign(1, m)
    r = rig.call("put", "/cards/1", token, json={"title": "Edited", "details": "d"})
    assert r.status_code == 200
    (action, actor, card, details), = edits(rig)
    assert (action, actor, card) == ("edit", aid, 1)
    assert json.loads(details) == {"title": "Edited", "fields": ["details", "title"]}  # names only, no values
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(0,)]
    board = rig.call("get", "", token).json()
    assert [c["has_member_edits"] for col in board["columns"] for c in col["cards"] if c["id"] == 1] == [False]


def test_no_edit_audit_for_own_unassigned_or_unchanged_cards_and_reassignment_stays_an_assign(rig):
    aid, token = company_admin(rig)
    m, _ = member(rig, "m@example.com")
    rig.sql("INSERT OR IGNORE INTO users (id, username, created_at, updated_at) VALUES (?, 'mm', ?, ?)", (m, NOW, NOW))
    assert rig.call("put", "/cards/2", token, json={"title": "unassigned"}).status_code == 200  # nobody's card
    rig.sql("UPDATE cards SET salesperson_id = ? WHERE id = 1", (aid,))
    assert rig.call("put", "/cards/1", token, json={"title": "mine"}).status_code == 200
    rig.sql("UPDATE cards SET salesperson_id = ? WHERE id = 1", (m,))
    assert rig.call("put", "/cards/1", token, json={"title": "mine"}).status_code == 200  # no actual change
    assert rig.call("put", "/cards/1", token, json={"salesperson_id": None}).status_code == 200  # only reassigns
    assert edits(rig) == []
    assert rig.sql("SELECT action FROM audit_events") == [("assign",)]


def test_a_failed_edit_or_a_rolled_back_request_leaves_no_audit(rig, monkeypatch):
    _, token = company_admin(rig)
    m, _ = member(rig, "m@example.com")
    rig.assign(1, m)
    assert rig.call("put", "/cards/1", token, json={"title": "x", "product_line": "No such line"}).status_code == 422
    import company_routes
    monkeypatch.setattr(company_routes, "record_undo", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        rig.call("put", "/cards/1", token, json={"title": "will roll back"})
    assert edits(rig) == [] and rig.sql("SELECT title FROM cards WHERE id = 1") == [("card 1",)]


def test_super_admin_and_member_audit_behavior_is_unchanged(rig):
    sup = admin(rig)
    m, mtoken = member(rig, "m@example.com")
    rig.assign(1, m)
    assert rig.call("put", "/cards/1", sup, json={"title": "by super"}).status_code == 200
    assert edits(rig) == [] and rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(0,)]
    assert rig.call("put", "/cards/1", mtoken, json={"title": "by member"}).status_code == 200
    assert edits(rig) == [] and rig.sql("SELECT user_id FROM card_edit_history") == [(m,)]  # the "M" badge source


def test_ai_edits_by_a_company_admin_are_audited_too(rig, monkeypatch):
    aid, token = company_admin(rig)
    m, _ = member(rig, "m@example.com")
    rig.assign(1, m)
    fake_ai(monkeypatch, [{"type": "update", "card_id": 1, "title": "via ai"}])
    assert not ai(rig, token).json()["actions_applied"]["failed"]
    (_, actor, card, details), = edits(rig)
    assert (actor, card, json.loads(details)["fields"]) == (aid, 1, ["title"])


# --- M1 ----------------------------------------------------------------------------------------------

class Pause:
    """Stop a request at a hook until the test releases it; the test thread revokes authority in between."""

    def __init__(self):
        self.reached, self.go = threading.Event(), threading.Event()

    def wait(self):
        self.reached.set()
        assert self.go.wait(30)


def in_thread(fn):
    out = {}
    thread = threading.Thread(target=lambda: out.setdefault("r", fn()))
    thread.start()
    return thread, out


def run_paused(pause, request, revoke):
    thread, out = in_thread(request)
    assert pause.reached.wait(30)
    revoke()
    pause.go.set()
    thread.join(30)
    return out["r"]


def slow_ai(monkeypatch, pause, actions):
    async def fake(board_state, history, message):
        pause.wait()  # "the LLM is answering"
        return {"success": True, "response": "ok", "actions": actions, "confidence": 1, "tokens_used": 0}
    monkeypatch.setattr(routes, "call_ai_with_board", fake)


def test_ai_request_revoked_while_the_model_answers_writes_nothing(rig, monkeypatch):
    uid, token = company_admin(rig)
    pause = Pause()
    slow_ai(monkeypatch, pause, [{"type": "update", "card_id": 1, "title": "late"}, {"type": "delete", "card_id": 2}])
    before = snapshot(rig)
    r = run_paused(pause, lambda: ai(rig, token),
                   lambda: svc.deactivate_membership(rig.e.factory(), rig.e.admin, "lity", uid))
    assert r.status_code == 403
    assert snapshot(rig) == before and rig.sql("SELECT COUNT(*) FROM conversation_messages") == [(0,)]


def test_ai_request_demoted_while_the_model_answers_loses_admin_powers(rig, monkeypatch):
    uid, token = company_admin(rig)
    pause = Pause()
    slow_ai(monkeypatch, pause, [{"type": "delete", "card_id": 1}])  # unassigned: Admin could, a Sales Member cannot
    r = run_paused(pause, lambda: ai(rig, token),
                   lambda: svc.change_membership_role(rig.e.factory(), rig.e.admin, "lity", uid, "sales_member"))
    assert r.status_code == 200 and r.json()["actions_applied"]["successful"] == []
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]


def test_ai_request_with_an_inactive_company_writes_nothing(rig, monkeypatch):
    _, token = company_admin(rig)
    pause = Pause()
    slow_ai(monkeypatch, pause, [{"type": "delete", "card_id": 1}])
    r = run_paused(pause, lambda: ai(rig, token),
                   lambda: rig.e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=False))
    assert r.status_code == 403 and rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]


def revoke_global_admin(rig):
    """The Super Admin loses the global flag while a request is in flight (its token and session stay valid)."""
    rig.e.update(IdentityUser, IdentityUser.id == rig.e.admin, is_global_admin=False)


def test_a_super_admin_ai_delete_is_refused_when_the_global_flag_is_removed_while_the_model_answers(rig, monkeypatch):
    token = admin(rig)
    pause = Pause()
    slow_ai(monkeypatch, pause, [{"type": "delete", "card_id": 1}])
    r = run_paused(pause, lambda: ai(rig, token), lambda: revoke_global_admin(rig))
    assert r.status_code == 403 and rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]


def test_an_unrevoked_slow_ai_request_still_succeeds(rig, monkeypatch):
    token = admin(rig)
    pause = Pause()
    slow_ai(monkeypatch, pause, [{"type": "delete", "card_id": 1}])
    r = run_paused(pause, lambda: ai(rig, token), lambda: None)
    assert r.status_code == 200 and len(r.json()["actions_applied"]["successful"]) == 1


def _pause_in_write_phase(monkeypatch, pause):
    real = bid_import_service._write_rows

    def paused(*a, **k):
        out = real(*a, **k)  # rows are in the open transaction
        pause.wait()
        return out
    monkeypatch.setattr(bid_import_service, "_write_rows", paused)


def test_import_confirmation_revoked_before_the_commit_commits_nothing(rig, monkeypatch):
    token = admin(rig)
    body = preview(rig, token).json()
    pause = Pause()
    _pause_in_write_phase(monkeypatch, pause)
    r = run_paused(pause, lambda: confirm(rig, token, body["preview_token"]),
                   lambda: revoke_global_admin(rig))
    assert r.status_code == 403
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(0,)]
    assert rig.sql("SELECT COUNT(*) FROM import_batches WHERE status = 'completed'") == [(0,)]
    assert rig.sql("SELECT COUNT(*) FROM import_batch_items") == [(0,)]


def test_import_confirmation_revoked_before_the_write_starts_is_refused(rig, monkeypatch):
    token = admin(rig)
    body = preview(rig, token).json()
    pause = Pause()
    real = import_routes._build_preview

    def paused(*a, **k):
        out = real(*a, **k)
        pause.wait()  # after the upload and the preview, right before the write
        return out
    monkeypatch.setattr(import_routes, "_build_preview", paused)
    r = run_paused(pause, lambda: confirm(rig, token, body["preview_token"]),
                   lambda: revoke_global_admin(rig))
    assert r.status_code == 403
    assert rig.sql("SELECT COUNT(*) FROM import_batches") == [(0,)]  # not even a failed attempt: the write never began
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(0,)]


def test_valid_import_is_unaffected_and_stays_single_use(rig, monkeypatch):
    token = admin(rig)
    body = preview(rig, token).json()
    pause = Pause()
    _pause_in_write_phase(monkeypatch, pause)
    r = run_paused(pause, lambda: confirm(rig, token, body["preview_token"]), lambda: None)
    assert r.status_code == 201 and rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(2,)]
    assert confirm(rig, token, body["preview_token"]).status_code == 409  # single use, unchanged


def test_failed_import_still_rolls_back_for_a_still_authorized_admin(rig, monkeypatch):
    token = admin(rig)
    body = preview(rig, token).json()
    real = bid_import_service._write_rows

    def fail(*a, **k):
        real(*a, **k)
        raise RuntimeError("synthetic")
    monkeypatch.setattr(bid_import_service, "_write_rows", fail)
    assert confirm(rig, token, body["preview_token"]).status_code == 500
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(0,)]


def test_revert_revoked_before_the_commit_changes_nothing_and_can_be_retried(rig, monkeypatch):
    token = admin(rig)
    run_import(rig, token)
    batch = imp(rig, "get", "/boards/2/imports", token).json()["items"][0]["id"]
    fp = imp(rig, "get", f"/imports/{batch}/revert-preview", token).json()["plan_fingerprint"]
    pause = Pause()
    real = bid_import_revert._verify

    def paused(*a, **k):
        out = real(*a, **k)
        pause.wait()
        return out
    monkeypatch.setattr(bid_import_revert, "_verify", paused)
    r = run_paused(pause, lambda: imp(rig, "post", f"/imports/{batch}/revert", token, json={"plan_fingerprint": fp}),
                   lambda: revoke_global_admin(rig))
    assert r.status_code == 403
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(2,)]
    assert rig.sql("SELECT reverted_at FROM import_batch_ledgers") == [(None,)]  # the claim was rolled back too
    # once the flag is back the Super Admin can still revert it: nothing was half-done
    rig.e.update(IdentityUser, IdentityUser.id == rig.e.admin, is_global_admin=True)
    sup = admin(rig)
    fp2 = imp(rig, "get", f"/imports/{batch}/revert-preview", sup).json()["plan_fingerprint"]
    assert imp(rig, "post", f"/imports/{batch}/revert", sup, json={"plan_fingerprint": fp2}).status_code == 200
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(0,)]


def test_column_deletion_and_legacy_helpers_are_untouched(rig):
    from test_company_products import send
    _, token = company_admin(rig)
    assert send(rig, "delete", "/boards/1/columns/1", token).status_code == 403
    # legacy callers pass neither admin= nor a recheck
    import inspect
    assert inspect.signature(routes.board_ai_chat_payload).parameters["reauthorize"].default is None
    assert inspect.signature(import_routes.confirm_import_impl).parameters["recheck"].default is None
    assert inspect.signature(import_routes.revert_import_impl).parameters["recheck"].default is None
