"""Part 23, step 4: the revert endpoints (GET /api/imports/{id}/revert-preview,
POST /api/imports/{id}/revert) and the revert state in the batch history.

Same temp SQLite file and synthetic workbooks as test_import_routes.py (never
backend/pm.db). The transactional logic is tested in test_bid_import_revert.py;
these tests cover the HTTP contract and the error mapping."""
import logging
import sqlite3

import pytest

import import_routes
from bid_import_revert import RevertBusy, RevertOutcomeUnconfirmed, RevertRefused
from test_import_routes import DECISIONS, auth, env, run, snapshot, xlsx  # noqa: F401 - env is a fixture

UNKNOWN_FINGERPRINT = "0" * 64


def preview_url(batch_id=1):
    return f"/api/imports/{batch_id}/revert-preview"


def revert_url(batch_id=1):
    return f"/api/imports/{batch_id}/revert"


def get_preview(env, batch_id=1, user="admin"):
    return env.client.get(preview_url(batch_id), headers=auth(env, user))


def post_revert(env, fingerprint, batch_id=1, user="admin"):
    return env.client.post(revert_url(batch_id), json={"plan_fingerprint": fingerprint}, headers=auth(env, user))


def imported(env):
    """A completed batch 1 (three cards created, one row left for review)."""
    return run(env, decisions=DECISIONS)


def fingerprint_of(env, batch_id=1):
    response = get_preview(env, batch_id)
    assert response.status_code == 200, response.text
    return response.json()["plan_fingerprint"]


def sql(env, statement, *params):
    conn = sqlite3.connect(env.path)
    try:
        with conn:
            return conn.execute(statement, params).fetchall()
    finally:
        conn.close()


def card_count(env):
    return sql(env, "SELECT COUNT(*) FROM cards")[0][0]


# --- authentication and authorization -------------------------------------------------

def test_unauthenticated_requests_are_refused(env):
    for response in (
        env.client.get(preview_url()),
        env.client.post(revert_url(), json={"plan_fingerprint": UNKNOWN_FINGERPRINT}),
        env.client.get(preview_url(), headers={"Authorization": "Bearer not-a-token"}),
    ):
        assert response.status_code == 401


def test_members_are_refused_and_nothing_is_written(env):
    imported(env)
    before = snapshot(env.path)
    assert get_preview(env, user="member").status_code == 403
    response = post_revert(env, UNKNOWN_FINGERPRINT, user="member")
    assert response.status_code == 403
    assert response.json() == {"detail": "Admin access required"}
    assert snapshot(env.path) == before


# --- preview ----------------------------------------------------------------------------

def test_preview_contract_and_no_writes(env):
    imported(env)
    before = snapshot(env.path)
    response = get_preview(env)
    assert response.status_code == 200
    body = response.json()
    assert snapshot(env.path) == before
    assert set(body) == {
        "batch_id", "status", "reason", "revertible", "no_commercial_changes", "ledger_version", "plan_fingerprint",
        "counts", "blockers", "dependent_batches", "items",
    }
    assert body["batch_id"] == 1
    assert body["status"] == "ready" and body["revertible"] is True
    assert body["no_commercial_changes"] is False
    assert len(body["plan_fingerprint"]) == 64
    assert body["blockers"] == [] and body["dependent_batches"] == []
    assert body["counts"]["cards_to_delete"] == 3 and body["counts"]["blocked"] == 0
    assert len(body["items"]) == body["counts"]["items"] == 3
    item = body["items"][0]
    assert set(item) == {
        "item_id", "card_id", "source_key", "action", "observation", "classification", "operations", "blockers",
    }
    assert item["action"] == "created" and item["classification"] == "delete"
    assert item["operations"] == [
        {"kind": "delete_card", "card_id": item["card_id"], "fields": None, "from_column_id": None, "to_column_id": None},
    ]
    # Stable until something changes.
    assert get_preview(env).json()["plan_fingerprint"] == body["plan_fingerprint"]


def test_missing_batch_is_404(env):
    response = get_preview(env, 9999)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "batch_not_found"
    response = post_revert(env, UNKNOWN_FINGERPRINT, 9999)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "batch_not_found"


def test_batch_without_a_ledger_is_not_revertible(env):
    imported(env)
    sql(env, "DELETE FROM import_batch_items")
    sql(env, "DELETE FROM import_batch_ledgers")
    body = get_preview(env).json()
    assert (body["status"], body["reason"], body["revertible"]) == ("not_revertible", "no_ledger", False)
    before = snapshot(env.path)
    response = post_revert(env, body["plan_fingerprint"])
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "not_revertible"
    assert snapshot(env.path) == before


def test_failed_batch_is_not_revertible(env):
    imported(env)
    sql(env, "UPDATE import_batches SET status='failed'")
    body = get_preview(env).json()
    assert (body["status"], body["reason"]) == ("not_revertible", "batch_not_completed")
    assert post_revert(env, body["plan_fingerprint"]).status_code == 409


# --- confirmation -------------------------------------------------------------------------

def test_revert_contract_and_effects(env):
    imported(env)
    undo_before = snapshot(env.path, ("undo_entries",))
    assert card_count(env) == 3
    response = post_revert(env, fingerprint_of(env))
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"batch", "outcome", "no_commercial_changes", "counts"}
    assert body["outcome"] == "reverted" and body["no_commercial_changes"] is False
    assert body["counts"] == {
        "cards_deleted": 3, "cards_restored": 0, "moves_reverted": 0, "already_deleted": 0,
        "bid_details_deleted": 0, "already_reverted": 0, "observed": 0,
    }
    revert = body["batch"]["revert"]
    assert revert["reverted"] is True and revert["revertible"] is False and revert["reason"] == "already_reverted"
    assert revert["reverted_by_username"] == "admin" and revert["reverted_at"]
    assert card_count(env) == 0
    assert snapshot(env.path, ("undo_entries",)) == undo_before


def test_malformed_confirmation_bodies_are_422(env):
    imported(env)
    before = snapshot(env.path)
    for payload in ({}, {"plan_fingerprint": "abc"}, {"plan_fingerprint": 5}, {"plan_fingerprint": "G" * 64},
                    {"plan_fingerprint": UNKNOWN_FINGERPRINT, "extra": 1}):
        response = env.client.post(revert_url(), json=payload, headers=auth(env))
        assert response.status_code == 422, payload
    assert env.client.post(revert_url(), headers=auth(env)).status_code == 422
    assert snapshot(env.path) == before


def test_stale_fingerprint_is_409_and_changes_nothing(env):
    imported(env)
    before = snapshot(env.path)
    response = post_revert(env, UNKNOWN_FINGERPRINT)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "plan_changed"
    assert snapshot(env.path) == before
    # The batch is still revertible with the right fingerprint.
    assert post_revert(env, fingerprint_of(env)).status_code == 200


def test_plan_that_changed_after_the_preview_is_409(env):
    imported(env)
    fingerprint = fingerprint_of(env)
    sql(env, "UPDATE cards SET title = title || ' (edited)' WHERE id = (SELECT MIN(id) FROM cards)")
    before = snapshot(env.path)
    response = post_revert(env, fingerprint)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "plan_changed"
    assert snapshot(env.path) == before


def test_already_reverted_batch_is_409(env):
    imported(env)
    fingerprint = fingerprint_of(env)
    assert post_revert(env, fingerprint).status_code == 200
    response = post_revert(env, fingerprint)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "already_reverted"
    preview = get_preview(env).json()
    assert (preview["status"], preview["revertible"]) == ("already_reverted", False)


# --- conflicts and dependencies -----------------------------------------------------------

def test_commercial_conflict_blocks_with_details_and_changes_nothing(env):
    imported(env)
    sql(env, "UPDATE cards SET deal_value = 123456 WHERE id = (SELECT MIN(id) FROM cards)")
    preview = get_preview(env).json()
    assert preview["status"] == "blocked" and preview["revertible"] is False
    assert preview["counts"]["blocked"] == 1
    conflict = next(b for b in preview["blockers"] if b["field"] == "card.deal_value")
    assert conflict["code"] == "field_modified_since" and conflict["current"] == 123456
    assert conflict["batch_ids"] == []

    before = snapshot(env.path)
    response = post_revert(env, preview["plan_fingerprint"])
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "revert_blocked"
    assert [b["field"] for b in detail["blockers"]] == ["card.deal_value"]
    assert snapshot(env.path) == before


def test_conflict_values_are_cut(env):
    imported(env)
    sql(env, "UPDATE cards SET title = ? WHERE id = (SELECT MIN(id) FROM cards)", "x" * 500)
    conflict = next(b for b in get_preview(env).json()["blockers"] if b["field"] == "card.title")
    assert len(conflict["current"]) == import_routes.REVERT_VALUE_MAX_CHARS


def test_later_batch_makes_the_dependency_visible(env):
    imported(env)
    run(env, decisions=DECISIONS)  # the same report again: batch 2 decides on the same cards
    preview = get_preview(env, 1).json()
    assert preview["status"] == "blocked"
    assert preview["dependent_batches"] and preview["dependent_batches"][0]["batch_id"] == 2
    assert {b["code"] for b in preview["blockers"]} == {"later_batch_decision_on_card"}
    response = post_revert(env, preview["plan_fingerprint"], 1)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "revert_blocked"
    assert detail["dependent_batches"] == preview["dependent_batches"]
    # Reverting the later batch first frees the earlier one.
    assert post_revert(env, fingerprint_of(env, 2), 2).status_code == 200
    assert get_preview(env, 1).json()["status"] == "ready"


# --- batch history ---------------------------------------------------------------------------

def test_history_and_detail_carry_the_revert_state(env):
    imported(env)
    listing = env.client.get(f"/api/boards/{env.board}/imports", headers=auth(env)).json()["items"][0]
    detail = env.client.get("/api/imports/1", headers=auth(env)).json()
    for batch in (listing, detail):
        # Existing fields are untouched; the revert state is added.
        assert batch["id"] == 1 and batch["status"] == "completed" and batch["created_count"] == 3
        assert batch["revert"] == {"has_ledger": True, "revertible": True, "reverted": False, "reason": None,
                                   "reverted_at": None, "reverted_by_user_id": None, "reverted_by_username": None,
                                   "outcome": None, "no_commercial_changes": None, "cards_deleted": None,
                                   "cards_restored": None, "moves_reverted": None, "already_deleted": None,
                                   "summary": None}
    assert "mapping" in detail and detail["card_changes_count"] >= 0

    assert post_revert(env, fingerprint_of(env)).status_code == 200
    detail = env.client.get("/api/imports/1", headers=auth(env)).json()
    revert = detail["revert"]
    assert revert["reverted"] is True and revert["outcome"] == "reverted"
    assert revert["no_commercial_changes"] is False
    assert revert["cards_deleted"] == 3 and revert["cards_restored"] == 0
    assert revert["summary"]["items"] == 3
    listing = env.client.get(f"/api/boards/{env.board}/imports", headers=auth(env)).json()["items"][0]
    assert listing["revert"]["reverted_by_username"] == "admin"


def test_history_state_of_batches_that_cannot_be_reverted(env):
    imported(env)
    sql(env, "DELETE FROM import_batch_items")
    sql(env, "DELETE FROM import_batch_ledgers")
    revert = env.client.get("/api/imports/1", headers=auth(env)).json()["revert"]
    assert (revert["has_ledger"], revert["revertible"], revert["reason"]) == (False, False, "no_ledger")


# --- rate limiting ------------------------------------------------------------------------------

def test_revert_endpoints_share_the_import_rate_limit(env, monkeypatch):
    imported(env)
    monkeypatch.setenv("RATE_LIMIT_IMPORT_USER", "3/60")
    import rate_limit
    rate_limit.limiter.reset()
    assert get_preview(env).status_code == 200
    assert post_revert(env, UNKNOWN_FINGERPRINT).status_code == 409
    assert get_preview(env).status_code == 200
    blocked = get_preview(env)
    assert blocked.status_code == 429
    assert post_revert(env, UNKNOWN_FINGERPRINT).status_code == 429
    assert env.client.get("/api/imports/1", headers=auth(env)).status_code == 200  # history is not limited


def test_members_and_anonymous_callers_do_not_use_the_admin_quota(env, monkeypatch):
    imported(env)
    monkeypatch.setenv("RATE_LIMIT_IMPORT_USER", "1/60")
    import rate_limit
    rate_limit.limiter.reset()
    for _ in range(3):
        assert env.client.get(preview_url()).status_code == 401
        assert get_preview(env, user="member").status_code == 403
    assert get_preview(env).status_code == 200


# --- SQLite busy and internal failures -------------------------------------------------------------

@pytest.mark.parametrize("phase", ["start", "commit"])
def test_busy_revert_is_503_with_retry_after(env, monkeypatch, phase):
    imported(env)

    def busy(*args, **kwargs):
        raise RevertBusy(phase)

    monkeypatch.setattr(import_routes, "execute_revert", busy)
    response = post_revert(env, UNKNOWN_FINGERPRINT)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.json()["detail"] == {
        "code": "import_busy", "message": response.json()["detail"]["message"], "phase": phase, "applied": False,
    }


def test_real_write_lock_gives_503_and_changes_nothing(env):
    """A second connection holds the write lock: the claim can't start (waits out SQLite's timeout)."""
    imported(env)
    fingerprint = fingerprint_of(env)
    before = snapshot(env.path)
    holder = sqlite3.connect(env.path, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        response = post_revert(env, fingerprint)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.json()["detail"]["phase"] == "start" and response.json()["detail"]["applied"] is False
    assert snapshot(env.path) == before
    assert post_revert(env, fingerprint).status_code == 200


def test_busy_preview_is_503_with_retry_after(env, monkeypatch):
    imported(env)

    def busy(*args, **kwargs):
        raise RevertBusy("preview")

    monkeypatch.setattr(import_routes, "revert_preview", busy)
    response = get_preview(env)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.json()["detail"]["phase"] == "preview" and response.json()["detail"]["applied"] is False


def test_unconfirmed_commit_is_500_and_never_reported_as_success(env, monkeypatch):
    imported(env)

    def unconfirmed(*args, **kwargs):
        raise RevertOutcomeUnconfirmed("the commit failed but the batch looks reverted")

    monkeypatch.setattr(import_routes, "execute_revert", unconfirmed)
    response = post_revert(env, UNKNOWN_FINGERPRINT)
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "revert_outcome_unconfirmed"
    assert "looks reverted" not in response.text


@pytest.mark.parametrize("target", ["execute_revert", "revert_preview"])
def test_internal_errors_are_500_without_leaking_details(env, monkeypatch, caplog, target):
    imported(env)
    secret = "SELECT * FROM cards WHERE title = 'Secret Customer' /home/app/pm.db Traceback"

    def broken(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(import_routes, target, broken)
    with caplog.at_level(logging.ERROR, logger="import_routes"):
        response = post_revert(env, UNKNOWN_FINGERPRINT) if target == "execute_revert" else get_preview(env)
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "revert_failed"
    for leaked in ("Secret Customer", "SELECT", "pm.db", "Traceback", "RuntimeError"):
        assert leaked not in response.text
    assert "Secret Customer" not in caplog.text and "RuntimeError" in caplog.text


def test_unknown_refusal_code_is_500(env, monkeypatch):
    imported(env)

    def odd(*args, **kwargs):
        raise RevertRefused("something_new")

    monkeypatch.setattr(import_routes, "execute_revert", odd)
    response = post_revert(env, UNKNOWN_FINGERPRINT)
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "revert_failed"


# --- SQLite busy outside the route handlers (global OperationalError handler) -----------------------

def test_lock_during_preview_authentication_is_503_import_busy(env):
    imported(env)
    fingerprint = fingerprint_of(env)
    holder = sqlite3.connect(env.path, isolation_level=None)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        response = get_preview(env)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    detail = response.json()["detail"]
    assert detail["code"] == "import_busy" and detail["applied"] is False and "phase" not in detail
    assert "SELECT" not in response.text and "locked" not in response.text
    assert get_preview(env).json()["plan_fingerprint"] == fingerprint


def test_lock_during_revert_authentication_is_503_and_reverts_nothing(env):
    imported(env)
    fingerprint = fingerprint_of(env)
    before = snapshot(env.path)
    holder = sqlite3.connect(env.path, isolation_level=None)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        response = post_revert(env, fingerprint)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.json()["detail"]["code"] == "import_busy" and response.json()["detail"]["applied"] is False
    assert snapshot(env.path) == before
    assert post_revert(env, fingerprint).status_code == 200


def test_lock_on_a_non_import_endpoint_is_503_database_busy(env):
    holder = sqlite3.connect(env.path, isolation_level=None)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        response = env.client.get(f"/api/boards/{env.board}", headers=auth(env))
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.json()["detail"]["code"] == "database_busy"
    assert "SELECT" not in response.text


def test_non_lock_operational_error_is_500_without_sql(env, caplog):
    from sqlalchemy.exc import OperationalError
    from database import get_db
    from main import app

    def broken():
        raise OperationalError("SELECT secret FROM users", {"p": 1}, sqlite3.OperationalError("no such table: users"))
        yield  # pragma: no cover

    saved = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = broken
    try:
        with caplog.at_level(logging.ERROR):
            response = env.client.get(f"/api/boards/{env.board}", headers=auth(env))
    finally:
        app.dependency_overrides[get_db] = saved
    assert response.status_code == 500
    assert "Retry-After" not in response.headers
    assert "secret" not in response.text and "users" not in response.text
    assert "OperationalError" in caplog.text and "secret" not in caplog.text


@pytest.mark.parametrize("code,expected", [(5, True), (6, True), (261, True), (517, True), (1, False), (8, False), (13, False)])
def test_is_lock_error_classification(code, expected):
    from sqlalchemy.exc import OperationalError
    from bid_import_revert import is_lock_error

    orig = sqlite3.OperationalError("x")
    orig.sqlite_errorcode = code
    assert is_lock_error(OperationalError("s", {}, orig)) is expected
    assert is_lock_error(ValueError("database is locked")) is False
