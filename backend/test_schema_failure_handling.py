"""Stage 2C: a missing or malformed card-version schema fails closed with 503 review_not_initialized in the central
imports (Bid confirm and revert, cards workbook), the AI board assistant and Undo, and never leaves partial effects.
Disposable tmp_path databases only."""
import json
import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

import card_reviews
from database_targets import company_database_path
from test_card_version_concurrency import BROKEN, BROKEN_OBLIGATIONS
from test_bid_excel_reader import build, row as bid_row
from test_card_versions import orm
from test_company_admin_ai_undo import local_row
from test_company_card_writes import admin, ai, fake_ai, member
from test_company_cards_excel_import import book, confirm as excel_confirm, preview as excel_preview, row as excel_row
from test_company_undo_import import XLSX, confirm, imp, preview, rig, run_import, undo  # noqa: F401  (fixture/helpers)

SCHEMAS = [BROKEN[h] for h in BROKEN] + [BROKEN_OBLIGATIONS[h] for h in BROKEN_OBLIGATIONS]
IDS = [f"versions-{h}" for h in BROKEN] + [f"obligations-{h}" for h in BROKEN_OBLIGATIONS]
TABLES = ("cards", "card_versions", "card_review_obligations", "import_batches", "import_batch_ledgers", "import_batch_items",
          "card_import_changes", "card_source_refs", "card_bid_details", "card_excel_imports", "audit_events",
          "card_stage_events", "card_edit_history", "undo_entries", "conversation_messages")
NOT_INITIALIZED = {"code": "review_not_initialized", "message": "The review workflow is not set up for this company yet"}


def break_schema(rig, ddl):
    con = sqlite3.connect(company_database_path("lity", rig.e.env))
    try:
        con.executescript(ddl)
    finally:
        con.close()


def state(rig):
    """Every row of every table a failed operation could touch (a table the broken schema dropped counts as absent)."""
    out = {}
    for table in TABLES:
        try:
            out[table] = rig.sql(f"SELECT * FROM {table} ORDER BY 1")
        except sqlite3.OperationalError:
            out[table] = None
    return out


def refused(response):
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == NOT_INITIALIZED


# --- Bid import confirm and revert ---------------------------------------------------------------------------

@pytest.mark.parametrize("ddl", SCHEMAS, ids=IDS)
def test_bid_import_confirm_is_refused_503_and_writes_nothing(rig, ddl):
    token = admin(rig)
    body = preview(rig, token).json()
    break_schema(rig, ddl)
    before = state(rig)
    refused(confirm(rig, token, body["preview_token"]))
    assert state(rig) == before
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(0,)]
    assert rig.sql("SELECT COUNT(*) FROM import_batches") == [(0,)]


@pytest.mark.parametrize("ddl", SCHEMAS, ids=IDS)
def test_bid_import_revert_is_refused_503_and_changes_nothing(rig, ddl):
    token = admin(rig)
    run_import(rig, token)
    batch = imp(rig, "get", "/boards/2/imports", token).json()["items"][0]["id"]
    fingerprint = imp(rig, "get", f"/imports/{batch}/revert-preview", token).json()["plan_fingerprint"]
    break_schema(rig, ddl)
    before = state(rig)
    refused(imp(rig, "post", f"/imports/{batch}/revert", token, json={"plan_fingerprint": fingerprint}))
    assert state(rig) == before
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(2,)]
    assert rig.sql("SELECT COUNT(*) FROM import_batch_ledgers WHERE reverted_by_user_id IS NOT NULL") == [(0,)]


def test_bid_import_with_a_valid_schema_is_unchanged(rig):
    token = admin(rig)
    run_import(rig, token)
    batch = imp(rig, "get", "/boards/2/imports", token).json()["items"][0]["id"]
    fingerprint = imp(rig, "get", f"/imports/{batch}/revert-preview", token).json()["plan_fingerprint"]
    assert imp(rig, "post", f"/imports/{batch}/revert", token, json={"plan_fingerprint": fingerprint}).status_code == 200
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(0,)]


# --- cards workbook import -----------------------------------------------------------------------------------

@pytest.mark.parametrize("ddl", SCHEMAS, ids=IDS)
def test_cards_workbook_confirm_is_refused_503_and_writes_nothing(rig, ddl):
    token = admin(rig)
    content = book([excel_row(title="New A"), excel_row(card_id=1, title="Renamed")])
    digest = excel_preview(rig, token, content).json()["plan_digest"]
    break_schema(rig, ddl)
    before = state(rig)
    refused(excel_confirm(rig, token, content, digest))
    assert state(rig) == before
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(2,)] and rig.sql("SELECT title FROM cards WHERE id = 1") == [("card 1",)]


def test_cards_workbook_confirm_with_a_valid_schema_is_unchanged(rig):
    token = admin(rig)
    content = book([excel_row(title="New A"), excel_row(card_id=1, title="Renamed")])
    digest = excel_preview(rig, token, content).json()["plan_digest"]
    assert excel_confirm(rig, token, content, digest).status_code == 201
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(3,)] and rig.sql("SELECT title FROM cards WHERE id = 1") == [("Renamed",)]


# --- AI ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("ddl", SCHEMAS, ids=IDS)
def test_ai_is_refused_503_before_anything_runs(rig, monkeypatch, ddl):
    token = admin(rig)
    break_schema(rig, ddl)
    before = state(rig)
    fake_ai(monkeypatch, [{"type": "update", "card_id": 1, "title": "x"}])
    refused(ai(rig, token))
    assert state(rig) == before


def fail_once_a_version_row_exists(monkeypatch):
    """The schema 'breaks' after the first flush of the transaction: the listener's check fails as soon as a version row
    written by this very transaction is visible, i.e. in the middle of the batch (the preflight saw a valid schema)."""
    real = card_reviews.review_schema_problem

    def check(conn):
        if conn.exec_driver_sql("SELECT COUNT(*) FROM card_versions").scalar() > 0:
            return "table card_review_obligations is missing"
        return real(conn)

    monkeypatch.setattr(card_reviews, "review_schema_problem", check)


def test_a_schema_failure_during_the_ai_batch_aborts_it_with_503_and_rolls_everything_back(rig, monkeypatch):
    token = admin(rig)
    before = state(rig)
    fail_once_a_version_row_exists(monkeypatch)
    owner, _ = member(rig, "m@example.com")
    local_row(rig, owner)
    before = state(rig)
    # The create flushes explicitly, so the first update is already written (and versioned) when a later flush fails.
    fake_ai(monkeypatch, [{"type": "update", "card_id": 1, "title": "first"},
                          {"type": "create", "column_id": 1, "title": "made", "salesperson_id": owner},
                          {"type": "update", "card_id": 2, "title": "second"}])
    refused(ai(rig, token))
    assert state(rig) == before
    assert rig.sql("SELECT COUNT(*) FROM card_versions") == [(0,)]


def test_ordinary_ai_action_failures_stay_per_action_and_the_rest_applies(rig, monkeypatch):
    token = admin(rig)
    fake_ai(monkeypatch, [{"type": "create", "column_id": 999, "title": "nowhere"},
                          {"type": "update", "card_id": 1, "title": "ok"}, {"type": "update", "card_id": 999, "title": "gone"}])
    r = ai(rig, token)
    assert r.status_code == 200
    applied = r.json()["actions_applied"]
    assert [x["type"] for x in applied["successful"]] == ["update"] and len(applied["failed"]) == 2
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("ok",)]
    assert rig.sql("SELECT COUNT(*) FROM card_versions") == [(1,)]


# --- Undo ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("ddl", SCHEMAS, ids=IDS)
def test_undo_is_refused_503_before_the_entry_is_touched(rig, ddl):
    token = admin(rig)
    assert rig.call("put", "/cards/1", token, json={"title": "changed"}).status_code == 200
    break_schema(rig, ddl)
    before = state(rig)
    refused(undo(rig, token))
    assert state(rig) == before and rig.sql("SELECT COUNT(*) FROM undo_entries") == [(1,)]


def test_a_schema_failure_during_undo_rolls_back_and_keeps_the_entry(rig, monkeypatch):
    token = admin(rig)
    assert rig.call("put", "/cards/1", token, json={"title": "changed"}).status_code == 200
    before = state(rig)
    real = card_reviews.review_schema_problem

    def check(conn):  # valid for the preflight (entry present); broken once Undo has claimed the entry in its transaction
        if conn.exec_driver_sql("SELECT COUNT(*) FROM undo_entries").scalar() == 0:
            return "table card_versions is missing"
        return real(conn)

    monkeypatch.setattr(card_reviews, "review_schema_problem", check)
    refused(undo(rig, token))
    assert state(rig) == before
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("changed",)] and rig.sql("SELECT version FROM card_versions") == [(1,)]
    monkeypatch.undo()  # the entry survived, so Undo works once the schema is sound again
    assert undo(rig, token).status_code == 200 and rig.sql("SELECT title FROM cards WHERE id = 1") == [("card 1",)]


def test_terminal_undo_refusals_still_consume_the_entry(rig):
    token = admin(rig)
    assert rig.call("put", "/cards/1", token, json={"title": "changed"}).status_code == 200
    rig.sql("UPDATE cards SET title = 'edited elsewhere' WHERE id = 1")
    assert undo(rig, token).status_code == 409
    assert rig.sql("SELECT COUNT(*) FROM undo_entries") == [(0,)]
    assert undo(rig, token).status_code == 404


# --- database error classification ---------------------------------------------------------------------------

def inspection_fails(monkeypatch, message):
    def boom(conn):
        raise OperationalError("PRAGMA", {}, sqlite3.OperationalError(message))

    monkeypatch.setattr(card_reviews, "inspect", boom)


def test_a_lock_contention_while_inspecting_the_schema_is_busy_not_not_initialized(rig, monkeypatch):
    from main import database_error_handler  # the application's handler for an OperationalError no route handled
    rig.client.app.add_exception_handler(OperationalError, database_error_handler)
    token = admin(rig)
    assert rig.call("put", "/cards/1", token, json={"title": "changed"}).status_code == 200
    before = state(rig)
    inspection_fails(monkeypatch, "database is locked")
    r = undo(rig, token)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "database_busy" and r.headers["Retry-After"]
    assert state(rig) == before


def test_another_inspection_failure_still_fails_closed_as_not_initialized(rig, monkeypatch):
    token = admin(rig)
    assert rig.call("put", "/cards/1", token, json={"title": "changed"}).status_code == 200
    before = state(rig)
    inspection_fails(monkeypatch, "disk I/O error")
    refused_body = undo(rig, token)
    assert refused_body.status_code == 503 and refused_body.json()["detail"]["code"] == "review_not_initialized"
    assert state(rig) == before


# --- the schema breaks AFTER the route's preflight (Stage 2C fix) ---------------------------------------------------
# The preflight passes against the sound schema, then the real tables are altered through a second connection before
# any write: the flush listener of the actual import code raises ReviewsNotInitialized, and the route's own handler
# answers. Nothing is mocked in the import, transaction or error-handling code.

def break_after_preflight(monkeypatch, rig, ddl):
    real = card_reviews.require_reviews
    fired = []

    def gate_then_break(db):
        real(db)  # the preflight really runs, and passes
        if not fired:
            fired.append(True)
            break_schema(rig, ddl)

    monkeypatch.setattr(card_reviews, "require_reviews", gate_then_break)
    return fired


def altered_table(ddl):
    return "card_versions" if "card_versions" in ddl else "card_review_obligations"


def state_without(rig, *skip):
    return {t: rows for t, rows in state(rig).items() if t not in skip}


def altered_table_is_untouched(rig, ddl):
    """The table the break altered holds no row: it was dropped or recreated empty, and nothing was written to it."""
    table = altered_table(ddl)
    try:
        return rig.sql(f"SELECT COUNT(*) FROM {table}") == [(0,)]
    except sqlite3.OperationalError:
        return True


def update_workbook():
    return build([bid_row(**{"Código": "000101-01", "Nome do projeto": "Projeto Novo", "Atualizado em": "01/04/2026"})])


@pytest.mark.parametrize("ddl", SCHEMAS, ids=IDS)
def test_bid_confirm_update_path_answers_503_when_the_schema_breaks_after_the_preflight(rig, monkeypatch, ddl):
    token = admin(rig)
    run_import(rig, token)  # two cards, row 2 is Código 000101-01
    data, decisions = update_workbook(), json.dumps([{"row_number": 2, "confirm_update": True}])
    body = imp(rig, "post", "/boards/2/imports/preview", token, files={"file": ("relatorio-leads.xlsx", data, XLSX)},
               data={"decisions": decisions}).json()
    assert body["confirmable"], body
    titles = rig.sql("SELECT id, title FROM cards ORDER BY id")
    skip = (altered_table(ddl), "import_batches")
    before, batches = state_without(rig, *skip), rig.sql("SELECT id, status FROM import_batches ORDER BY id")
    fired = break_after_preflight(monkeypatch, rig, ddl)
    r = imp(rig, "post", "/boards/2/imports", token, files={"file": ("relatorio-leads.xlsx", data, XLSX)},
            data={"decisions": decisions, "confirm": "true", "preview_token": body["preview_token"]})
    assert fired
    refused(r)
    assert state_without(rig, *skip) == before and rig.sql("SELECT id, title FROM cards ORDER BY id") == titles
    # The service's contract: the attempt is recorded as a failed batch (separate transaction), never as completed.
    after = rig.sql("SELECT id, status FROM import_batches ORDER BY id")
    assert after[:len(batches)] == batches and [status for _, status in after[len(batches):]] == ["failed"]
    assert altered_table_is_untouched(rig, ddl)


@pytest.mark.parametrize("ddl", SCHEMAS, ids=IDS)
def test_bid_revert_answers_503_when_the_schema_breaks_after_the_preflight(rig, monkeypatch, ddl):
    token = admin(rig)
    run_import(rig, token)
    batch = imp(rig, "get", "/boards/2/imports", token).json()["items"][0]["id"]
    fingerprint = imp(rig, "get", f"/imports/{batch}/revert-preview", token).json()["plan_fingerprint"]
    skip = (altered_table(ddl),)
    before = state_without(rig, *skip)
    fired = break_after_preflight(monkeypatch, rig, ddl)
    refused(imp(rig, "post", f"/imports/{batch}/revert", token, json={"plan_fingerprint": fingerprint}))
    assert fired and state_without(rig, *skip) == before
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(2,)]
    assert rig.sql("SELECT COUNT(*) FROM import_batch_ledgers WHERE reverted_by_user_id IS NOT NULL") == [(0,)]
    assert altered_table_is_untouched(rig, ddl)


@pytest.mark.parametrize("ddl", SCHEMAS, ids=IDS)
def test_cards_workbook_update_answers_503_when_the_schema_breaks_after_the_preflight(rig, monkeypatch, ddl):
    token = admin(rig)
    content = book([excel_row(card_id=1, title="Renamed"), excel_row(title="New A")])
    digest = excel_preview(rig, token, content).json()["plan_digest"]
    skip = (altered_table(ddl),)
    before = state_without(rig, *skip)
    fired = break_after_preflight(monkeypatch, rig, ddl)
    refused(excel_confirm(rig, token, content, digest))
    assert fired and state_without(rig, *skip) == before
    assert rig.sql("SELECT id, title FROM cards ORDER BY id") == [(1, "card 1"), (2, "card 2")]
    assert rig.sql("SELECT COUNT(*) FROM card_excel_imports") == [(0,)]  # no ledger row: the workbook can be retried
    assert altered_table_is_untouched(rig, ddl)


# --- create-only imports: the listener also validates the schema when a card is created ---------------------------

@pytest.mark.parametrize("ddl", SCHEMAS, ids=IDS)
def test_bid_confirm_that_only_creates_cards_answers_503_when_the_schema_breaks_after_the_preflight(rig, monkeypatch, ddl):
    token = admin(rig)
    body = preview(rig, token).json()  # two new cards, no existing card is touched
    assert body["confirmable"] and rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(0,)]
    skip = (altered_table(ddl), "import_batches")
    before = state_without(rig, *skip)
    fired = break_after_preflight(monkeypatch, rig, ddl)
    refused(confirm(rig, token, body["preview_token"]))
    assert fired and state_without(rig, *skip) == before
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(2,)] and rig.sql("SELECT COUNT(*) FROM cards WHERE column_id >= 10") == [(0,)]
    # Explicit exception to "nothing is written": the service records the failed attempt in its own transaction.
    assert rig.sql("SELECT status FROM import_batches ORDER BY id") == [("failed",)]
    assert altered_table_is_untouched(rig, ddl)


@pytest.mark.parametrize("ddl", SCHEMAS, ids=IDS)
def test_cards_workbook_that_only_creates_cards_answers_503_when_the_schema_breaks_after_the_preflight(rig, monkeypatch, ddl):
    token = admin(rig)
    content = book([excel_row(title="New A"), excel_row(title="New B")])  # no card_id: nothing existing is updated
    digest = excel_preview(rig, token, content).json()["plan_digest"]
    skip = (altered_table(ddl),)
    before = state_without(rig, *skip)
    fired = break_after_preflight(monkeypatch, rig, ddl)
    refused(excel_confirm(rig, token, content, digest))
    assert fired and state_without(rig, *skip) == before
    assert rig.sql("SELECT id, title FROM cards ORDER BY id") == [(1, "card 1"), (2, "card 2")]
    assert rig.sql("SELECT COUNT(*) FROM card_excel_imports") == [(0,)]
    assert altered_table_is_untouched(rig, ddl)


def test_creating_a_card_with_a_valid_schema_writes_no_version_row_and_is_version_zero(rig):
    token = admin(rig)
    content = book([excel_row(title="New A")])
    digest = excel_preview(rig, token, content).json()["plan_digest"]
    assert excel_confirm(rig, token, content, digest).status_code == 201
    created = rig.sql("SELECT id FROM cards WHERE title = 'New A'")[0][0]
    assert created > 2 and rig.sql("SELECT COUNT(*) FROM card_versions") == [(0,)]
    with orm(rig) as db:
        assert card_reviews.card_version(db, created) == 0
