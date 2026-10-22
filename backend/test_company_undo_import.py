"""Phase 4: Undo and Bid import/revert in central mode. Authorization, company scoping, atomicity, concurrency,
conflicts and recovery, on disposable identity and company databases only."""
import json
import threading

import pytest

from lead_options import PIPELINE_STAGES
from test_bid_excel_reader import build, row as excel_row
from test_company_card_writes import NOW, Rig, admin, member
from test_central_deps import Env
import company_runtime as cr

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ROWS = (
    excel_row(**{"Código": "000101-01"}),                                                  # -> Proposal
    excel_row(**{"Código": "000102-01", "CAT": "WON", "Estágio": "AWARD GIVEN"}),          # -> Award
)
_data = build(list(ROWS))  # built once: workbooks carry a creation time


@pytest.fixture
def rig(tmp_path, monkeypatch):
    e = Env(tmp_path)
    r = Rig(e)
    for slug in ("lity", "valor-log"):  # board 2 has the real pipeline stages the import maps to
        r.sql("INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (2, 7, 'Pipeline', ?, ?)", (NOW, NOW), slug)
        for i, (title, kind) in enumerate(PIPELINE_STAGES):
            r.sql("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) VALUES (?, 2, ?, ?, ?, ?, ?)",
                  (10 + i, title, i, kind, NOW, NOW), slug)
    monkeypatch.setattr(cr, "company_session_factory", r.patch)
    yield r
    r.runtime.dispose()
    e.session.close()
    e.engine.dispose()


def undo(rig, token, slug="lity", method="post", board=1):
    return rig.client.request(method, f"/api/companies/{slug}/boards/{board}/undo", headers={"Authorization": f"Bearer {token}"})


def title(rig, card_id=1, slug="lity"):
    return rig.sql("SELECT title FROM cards WHERE id = ?", (card_id,), slug)[0][0]


# --- Undo -------------------------------------------------------------------------------------------

def test_admin_edit_undo_roundtrip_and_entry_is_used_up(rig):
    token = admin(rig)
    assert rig.call("put", "/cards/1", token, json={"title": "changed"}).status_code == 200
    assert undo(rig, token, method="get").json() == {"available": True, "description": 'Undo edit to "card 1"', "undone": None}
    assert undo(rig, token).status_code == 200 and title(rig) == "card 1"
    assert undo(rig, token).status_code == 404  # used up: idempotent, no second effect
    assert title(rig) == "card 1"


def test_visitor_refused_before_the_company_database_opens(rig):
    _, token = member(rig, role="visitor")
    for method in ("get", "post"):
        assert undo(rig, token, method=method).status_code == 403
    assert rig.opened == []


def test_unauthenticated_and_other_company_member_refused(rig):
    assert undo(rig, None).status_code == 401
    _, token = member(rig, slug="valor-log")
    assert undo(rig, token).status_code == 403 and rig.opened == []


def test_entries_are_per_company_even_with_matching_ids(rig):
    token = admin(rig)  # a global admin works in both companies; both have board 1 and card 1
    rig.call("put", "/cards/1", token, json={"title": "lity edit"})
    assert undo(rig, token, slug="valor-log").status_code == 404  # nothing recorded in valor-log
    assert title(rig, slug="valor-log") == "card 1" and title(rig) == "lity edit"
    assert undo(rig, token).status_code == 200 and title(rig) == "card 1"


def test_member_undoes_own_edit_but_not_after_losing_the_card(rig):
    uid, member_token = member(rig)
    rig.assign(1, uid)
    assert rig.call("put", "/cards/1", member_token, json={"title": "mine"}).status_code == 200
    rig.assign(1, 7)  # an administrator reassigns it to someone else meanwhile
    r = undo(rig, member_token)
    assert r.status_code == 409 and title(rig) == "mine"
    assert rig.sql("SELECT COUNT(*) FROM undo_entries") == [(0,)]  # consumed


def test_member_undo_restores_a_card_they_moved_forward(rig):
    uid, member_token = member(rig)
    rig.assign(1, uid)
    assert rig.call("put", "/cards/1/position", member_token, json={"column_id": 2, "position": 0}).status_code == 200
    assert undo(rig, member_token).status_code == 200
    assert rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(1,)]
    assert rig.sql("SELECT source FROM card_stage_events WHERE card_id = 1 ORDER BY id DESC LIMIT 1") == [("undo",)]


def test_a_later_change_makes_undo_refuse_with_409_and_keeps_the_later_value(rig):
    token = admin(rig)
    rig.call("put", "/cards/1", token, json={"title": "first"})
    rig.sql("UPDATE cards SET title = 'someone else' WHERE id = 1")  # another actor, behind the undo's back
    assert undo(rig, token).status_code == 409
    assert title(rig) == "someone else"


def test_delete_is_undone_by_recreating_the_card_with_audit(rig):
    token = admin(rig)
    assert rig.call("delete", "/cards/2", token).status_code == 204
    assert undo(rig, token).status_code == 200
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'card 2'") == [(1,)]
    assert rig.sql("SELECT COUNT(*) FROM audit_events WHERE action = 'delete'") == [(1,)]


def test_partial_failure_leaves_the_board_untouched(rig):
    token = admin(rig)
    rig.call("put", "/cards/1", token, json={"title": "edited"})
    steps = json.dumps([
        {"op": "update", "card_id": 1, "fields": {"title": "card 1"}, "expect": {"title": "edited"}},
        {"op": "move", "card_id": 2, "column_id": 999, "position": 0, "expect": {"column_id": 1}},  # column is gone
    ])
    rig.sql("UPDATE undo_entries SET steps = ?", (steps,))
    assert undo(rig, token).status_code == 409
    assert title(rig) == "edited"  # the first step was not applied either
    assert rig.sql("SELECT COUNT(*) FROM undo_entries") == [(0,)]


def test_entry_recorded_without_expect_is_refused(rig):
    token = admin(rig)
    rig.call("put", "/cards/1", token, json={"title": "edited"})
    rig.sql("UPDATE undo_entries SET steps = ?", (json.dumps([{"op": "update", "card_id": 1, "fields": {"title": "x"}}]),))
    r = undo(rig, token)
    assert r.status_code == 409 and "recorded before" in r.json()["detail"] and title(rig) == "edited"


def test_concurrent_undo_requests_apply_at_most_once(rig):
    token = admin(rig)
    rig.call("delete", "/cards/2", token)
    barrier, codes = threading.Barrier(2), []

    def go():
        barrier.wait()
        codes.append(undo(rig, token).status_code)
    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(codes) == 2 and codes.count(200) == 1 and set(codes) <= {200, 404, 409}  # never a 500 or an exception
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'card 2'") == [(1,)]  # recreated once, not twice


def test_ai_changes_are_recorded_and_undoable(rig, monkeypatch):
    from test_company_card_writes import ai, fake_ai
    token = admin(rig)
    fake_ai(monkeypatch, [{"type": "update", "card_id": 1, "title": "by ai"}])
    assert ai(rig, token).status_code == 200 and title(rig) == "by ai"
    assert undo(rig, token).status_code == 200 and title(rig) == "card 1"


# --- Bid import and revert ----------------------------------------------------------------------------

def imp(rig, method, path, token, slug="lity", **kw):
    return rig.client.request(method, f"/api/companies/{slug}{path}", headers={"Authorization": f"Bearer {token}"} if token else {}, **kw)


def preview(rig, token, slug="lity", board=2):
    return imp(rig, "post", f"/boards/{board}/imports/preview", token, slug,
               files={"file": ("relatorio-leads.xlsx", _data, XLSX)}, data={"decisions": "[]"})


def confirm(rig, token, preview_token, slug="lity", board=2):
    return imp(rig, "post", f"/boards/{board}/imports", token, slug,
               files={"file": ("relatorio-leads.xlsx", _data, XLSX)},
               data={"decisions": "[]", "confirm": "true", "preview_token": preview_token})


def run_import(rig, token, slug="lity"):
    body = preview(rig, token, slug).json()
    r = confirm(rig, token, body["preview_token"], slug)
    assert r.status_code == 201, r.text
    return r.json()


def test_only_global_admins_reach_import_endpoints_and_before_the_database_opens(rig):
    for role in ("sales_member", "visitor"):
        _, token = member(rig, f"{role}@example.com", role)
        for method, path in (("post", "/boards/2/imports/preview"), ("post", "/boards/2/imports"), ("get", "/boards/2/imports"),
                             ("get", "/imports/1"), ("get", "/imports/1/revert-preview"), ("post", "/imports/1/revert")):
            kw = {"json": {"plan_fingerprint": "x"}} if path.endswith("/revert") else {}
            assert imp(rig, method, path, token, **kw).status_code == 403, (role, path)
    assert rig.opened == []
    assert imp(rig, "get", "/boards/2/imports", None).status_code == 401


def test_import_writes_canonical_actor_and_only_the_target_company(rig):
    token = admin(rig)
    result = run_import(rig, token)
    assert len(result["created_card_ids"]) == 2
    assert rig.sql("SELECT COUNT(*) FROM import_batches", slug="valor-log") == [(0,)]  # isolation
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10", slug="valor-log") == [(0,)]
    admin_id = rig.e.admin
    assert rig.sql("SELECT user_id FROM import_batches") == [(admin_id,)]
    assert rig.sql("SELECT DISTINCT source FROM card_stage_events WHERE source = 'import'") == [("import",)]
    assert rig.sql("SELECT COUNT(*) FROM undo_entries") == [(0,)]  # imports do not create Undo entries


def test_preview_token_is_bound_to_its_company(rig):
    token = admin(rig)
    body = preview(rig, token, "lity").json()
    r = confirm(rig, token, body["preview_token"], "valor-log")
    assert r.status_code == 422
    assert rig.sql("SELECT COUNT(*) FROM import_batches", slug="valor-log") == [(0,)]
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10", slug="valor-log") == [(0,)]


def test_a_company_preview_token_is_useless_on_the_legacy_route_and_vice_versa():
    import import_routes
    from auth import create_purpose_token, decode_purpose_token
    from datetime import timedelta
    company = create_purpose_token("1", import_routes.PREVIEW_TOKEN_PURPOSE, timedelta(minutes=1), board_id=2, company="lity")
    legacy = create_purpose_token("1", import_routes.PREVIEW_TOKEN_PURPOSE, timedelta(minutes=1), board_id=2)
    assert decode_purpose_token(company, import_routes.PREVIEW_TOKEN_PURPOSE)["company"] == "lity"
    assert decode_purpose_token(legacy, import_routes.PREVIEW_TOKEN_PURPOSE).get("company") is None


def test_import_history_and_detail(rig):
    token = admin(rig)
    run_import(rig, token)
    listing = imp(rig, "get", "/boards/2/imports", token).json()
    assert listing["total"] == 1 and listing["items"][0]["revert"]["revertible"] is True
    assert imp(rig, "get", f"/imports/{listing['items'][0]['id']}", token).status_code == 200
    assert imp(rig, "get", "/imports/999", token).status_code == 404


def test_revert_is_atomic_audited_idempotent_and_company_scoped(rig):
    token = admin(rig)
    run_import(rig, token)
    batch = imp(rig, "get", "/boards/2/imports", token).json()["items"][0]["id"]
    plan = imp(rig, "get", f"/imports/{batch}/revert-preview", token)
    assert plan.status_code == 200
    fingerprint = plan.json()["plan_fingerprint"]
    # the same batch id in another company is a different (absent) batch
    assert imp(rig, "post", f"/imports/{batch}/revert", token, "valor-log", json={"plan_fingerprint": fingerprint}).status_code == 404
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(2,)]
    done = imp(rig, "post", f"/imports/{batch}/revert", token, json={"plan_fingerprint": fingerprint})
    assert done.status_code == 200, done.text
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(0,)]
    assert rig.sql("SELECT reverted_by_user_id FROM import_batch_ledgers") == [(rig.e.admin,)]  # canonical actor recorded
    assert rig.sql("SELECT COUNT(*) FROM card_stage_events WHERE source = 'import_revert'")[0][0] >= 2
    again = imp(rig, "post", f"/imports/{batch}/revert", token, json={"plan_fingerprint": fingerprint})
    assert again.status_code == 409
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(0,)]


def test_revert_with_a_stale_plan_is_refused_and_changes_nothing(rig):
    token = admin(rig)
    run_import(rig, token)
    batch = imp(rig, "get", "/boards/2/imports", token).json()["items"][0]["id"]
    fingerprint = imp(rig, "get", f"/imports/{batch}/revert-preview", token).json()["plan_fingerprint"]
    rig.sql("UPDATE cards SET title = 'edited after preview' WHERE column_id >= 10 AND id = (SELECT MIN(id) FROM cards WHERE column_id >= 10)")
    r = imp(rig, "post", f"/imports/{batch}/revert", token, json={"plan_fingerprint": fingerprint})
    assert r.status_code == 409
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(2,)]


# --- a deleted imported card cannot be recreated faithfully, so its deletion is not undoable ---------------------

def test_deleting_an_imported_card_offers_no_undo_and_drops_the_stale_entry(rig):
    token = admin(rig)
    run_import(rig, token)
    imported = rig.sql("SELECT id FROM cards WHERE column_id >= 10 ORDER BY id LIMIT 1")[0][0]
    rig.call("put", "/cards/1", token, json={"title": "an earlier undoable edit"})
    assert undo(rig, token, method="get").json()["available"] is True
    path, kw = rig.versioned("delete", f"/boards/2/cards/{imported}", {})
    assert imp(rig, "delete", path, token, **kw).status_code == 204
    assert undo(rig, token, method="get").json()["available"] is False  # nothing offered, not even the earlier edit
    assert undo(rig, token).status_code == 404
    assert rig.sql("SELECT COUNT(*) FROM card_bid_details WHERE card_id = ?", (imported,)) == [(0,)]


def test_deleting_a_plain_card_is_still_undoable(rig):
    token = admin(rig)
    assert rig.call("delete", "/cards/2", token).status_code == 204
    assert undo(rig, token).status_code == 200
