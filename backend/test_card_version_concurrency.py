"""Stage 2 of optimistic concurrency (central mode): fail-closed version schema, and the expected_version
precondition of the company update, move and delete routes. Disposable tmp_path databases only."""
import threading

import pytest
from sqlalchemy.exc import OperationalError

import card_reviews
import company_routes
from diagnose_company_schema import diagnose_company
from test_card_versions import orm, version
from test_company_card_writes import admin, member
from test_company_card_writes import rig  # noqa: F401  (fixture)

CONFLICT = "card_version_conflict"
BROKEN = {
    "missing table": "DROP TABLE card_versions",
    "missing column": "DROP TABLE card_versions; CREATE TABLE card_versions (card_id INTEGER PRIMARY KEY, version INTEGER NOT NULL)",
    "no primary key": "DROP TABLE card_versions; CREATE TABLE card_versions (card_id INTEGER, version INTEGER NOT NULL, updated_at DATETIME NOT NULL)",
    "wrong primary key": "DROP TABLE card_versions; CREATE TABLE card_versions (card_id INTEGER, version INTEGER NOT NULL, "
                         "updated_at DATETIME NOT NULL, PRIMARY KEY (card_id, version))",
}


def break_versions(rig, how):
    import sqlite3
    from database_targets import company_database_path
    con = sqlite3.connect(company_database_path("lity", rig.e.env))
    try:
        con.executescript(BROKEN[how])
    finally:
        con.close()


def card_state(rig):
    return rig.sql("SELECT id, column_id, title, position FROM cards ORDER BY id"), rig.sql("SELECT * FROM card_versions")


def assert_conflict(response, card_id, expected, current):
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {
        "code": CONFLICT, "message": response.json()["detail"]["message"],
        "card_id": card_id, "expected_version": expected, "current_version": current}


# --- schema readiness ------------------------------------------------------------------------------------------

def test_a_valid_schema_without_a_row_is_version_zero(rig):
    with orm(rig) as db:
        assert card_reviews.card_version(db, 1) == 0
        assert card_reviews.card_versions(db, [1, 2]) == {1: 0, 2: 0}
        assert card_reviews.board_card_versions(db, 1) == {}


@pytest.mark.parametrize("how", list(BROKEN))
def test_an_invalid_version_schema_fails_closed_for_every_reader(rig, how):
    break_versions(rig, how)
    with orm(rig) as db:
        for read in (lambda: card_reviews.card_version(db, 1), lambda: card_reviews.card_versions(db, [1, 2]),
                     lambda: card_reviews.board_card_versions(db, 1), lambda: card_reviews.require_reviews(db)):
            with pytest.raises(card_reviews.ReviewsNotInitialized) as caught:
                read()
            assert caught.value.status == 503 and caught.value.code == "review_not_initialized"


def test_a_schema_that_cannot_be_inspected_is_not_version_zero(rig, monkeypatch):
    def broken(_conn):
        raise OperationalError("PRAGMA", {}, Exception("disk I/O error"))
    monkeypatch.setattr(card_reviews, "inspect", broken)
    with orm(rig) as db:
        with pytest.raises(card_reviews.ReviewsNotInitialized):
            card_reviews.card_version(db, 1)
        with pytest.raises(card_reviews.ReviewsNotInitialized):
            card_reviews.board_card_versions(db, 1)


def test_the_board_read_answers_503_on_an_invalid_schema(rig):
    break_versions(rig, "no primary key")
    response = rig.client.get("/api/companies/lity/boards/1", headers={"Authorization": f"Bearer {admin(rig)}"})
    assert response.status_code == 503


@pytest.mark.parametrize("how", ["no primary key", "missing column"])
def test_the_diagnostic_detects_an_invalid_card_versions_table(rig, how):
    assert diagnose_company("lity", rig.e.env).complete
    break_versions(rig, how)
    result = diagnose_company("lity", rig.e.env)
    assert not result.complete
    text = " ".join(result.incompatible)
    assert "card_versions" in text and ("primary key" if how == "no primary key" else "missing columns") in text


def test_a_versioned_request_on_an_invalid_schema_is_a_503_and_changes_nothing(rig):
    token = admin(rig)
    break_versions(rig, "no primary key")
    before = card_state(rig)
    for method, path, kw in (("put", "/cards/1", {"json": {"title": "z", "expected_version": 0}}),
                             ("put", "/cards/1/position", {"json": {"column_id": 2, "position": 0, "expected_version": 0}}),
                             ("delete", "/cards/1?expected_version=0", {})):
        response = rig.call(method, path, token, **kw)
        assert response.status_code == 503, (path, response.text)
    assert card_state(rig) == before


# --- update ----------------------------------------------------------------------------------------------------

def test_a_matching_expected_version_allows_the_update_and_returns_the_new_version(rig):
    token = admin(rig)
    first = rig.call("put", "/cards/1", token, json={"title": "a", "expected_version": 0})
    assert first.status_code == 200 and first.json()["version"] == 1 and "expected_version" not in first.json()
    second = rig.call("put", "/cards/1", token, json={"title": "b", "expected_version": 1})
    assert second.status_code == 200 and second.json()["version"] == 2


def test_a_stale_update_is_a_409_and_changes_nothing(rig):
    token = admin(rig)
    assert rig.call("put", "/cards/1", token, json={"title": "a"}).status_code == 200
    before = card_state(rig)
    audits = rig.sql("SELECT COUNT(*) FROM audit_events")
    stale = rig.call("put", "/cards/1", token, json={"title": "stale", "expected_version": 0})
    assert_conflict(stale, 1, 0, 1)
    assert card_state(rig) == before and rig.sql("SELECT COUNT(*) FROM audit_events") == audits


def test_a_stale_update_by_the_admin_wins_over_a_salesperson_validation_error(rig):
    token = admin(rig)
    assert rig.call("put", "/cards/1", token, json={"title": "a"}).status_code == 200
    stale = rig.call("put", "/cards/1", token, json={"salesperson_id": 99999, "expected_version": 0})
    assert_conflict(stale, 1, 0, 1)


def test_a_current_update_still_gets_domain_validation(rig):
    token = admin(rig)
    bad = rig.call("put", "/cards/1", token, json={"salesperson_id": 99999, "expected_version": 0})
    assert bad.status_code == 422


REQUIRED = "card_version_required"


def assert_required(response, card_id):
    assert response.status_code == 428, response.text
    detail = response.json()["detail"]
    assert detail["code"] == REQUIRED and detail["card_id"] == card_id


def test_an_update_without_a_version_is_a_428_and_changes_nothing(rig):
    token = admin(rig)
    before = card_state(rig)
    assert_required(rig.call("put", "/cards/1", token, versionless=True, json={"title": "no version"}), 1)
    assert_required(rig.call("put", "/cards/1", token, json={"title": "null version", "expected_version": None}), 1)
    assert card_state(rig) == before
    assert rig.sql("SELECT COUNT(*) FROM undo_entries") == [(0,)] and rig.sql("SELECT COUNT(*) FROM audit_events") == [(0,)]


def test_a_missing_card_is_a_404_before_the_version_is_required_and_a_member_gets_403_first(rig):
    token = admin(rig)
    assert rig.call("put", "/cards/99", token, versionless=True, json={"title": "x"}).status_code == 404
    assert rig.call("delete", "/cards/99", token, versionless=True).status_code == 404
    uid, member_token = member(rig)  # card 1 is unassigned: a member may not edit, move or delete it
    for method, path, kw in (("put", "/cards/1", {"json": {"title": "x"}}),
                             ("put", "/cards/1/position", {"json": {"column_id": 2, "position": 0}}),
                             ("delete", "/cards/1", {})):
        r = rig.call(method, path, member_token, versionless=True, **kw)
        assert r.status_code == 403 and REQUIRED not in r.text, (method, path)


def test_the_current_version_is_accepted_for_every_write_and_the_next_one_must_be_newer(rig):
    token = admin(rig)
    assert rig.call("put", "/cards/1", token, json={"title": "a", "expected_version": 0}).status_code == 200
    assert rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0, "expected_version": 1}).status_code == 200
    assert_conflict(rig.call("put", "/cards/1", token, json={"title": "b", "expected_version": 1}), 1, 1, 2)
    assert rig.call("delete", "/cards/1?expected_version=2", token).status_code == 204


def test_the_version_must_be_a_non_negative_integer(rig):
    token = admin(rig)
    assert rig.call("put", "/cards/1", token, json={"title": "c", "expected_version": -1}).status_code == 422
    assert rig.call("put", "/cards/1", token, json={"title": "c", "expected_version": "x"}).status_code == 422


def test_a_member_cannot_probe_versions_of_a_card_it_may_not_edit(rig):
    uid, token = member(rig)  # card 1 is unassigned: a member may not edit it
    before = card_state(rig)
    response = rig.call("put", "/cards/1", token, json={"title": "x", "expected_version": 41})
    assert response.status_code == 403 and "version" not in response.text.lower() and CONFLICT not in response.text
    moved = rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0, "expected_version": 41})
    assert moved.status_code == 403 and CONFLICT not in moved.text
    assert card_state(rig) == before


def test_a_member_stale_update_of_an_own_card_conflicts_without_side_effects(rig):
    uid, token = member(rig)
    rig.assign(1, uid)
    assert rig.call("put", "/cards/1", token, json={"title": "mine", "expected_version": 0}).json()["version"] == 1
    history = rig.sql("SELECT COUNT(*) FROM card_edit_history")
    obligations = rig.sql("SELECT COUNT(*) FROM card_review_obligations")
    stale = rig.call("put", "/cards/1", token, json={"title": "again", "expected_version": 0})
    assert_conflict(stale, 1, 0, 1)
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history") == history
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations") == obligations
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("mine",)]


def test_a_missing_card_is_a_404_whatever_the_version(rig):
    token = admin(rig)
    assert rig.call("put", "/cards/99", token, json={"title": "x", "expected_version": 3}).status_code == 404
    assert rig.call("put", "/cards/99/position", token, json={"column_id": 2, "position": 0, "expected_version": 3}).status_code == 404
    assert rig.call("delete", "/cards/99?expected_version=3", token).status_code == 404


# --- move ------------------------------------------------------------------------------------------------------

def test_a_matching_move_succeeds_and_a_stale_one_is_refused(rig):
    token = admin(rig)
    moved = rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0, "expected_version": 0})
    assert moved.status_code == 200 and moved.json()["version"] == 1
    before = card_state(rig)
    undo = rig.sql("SELECT COUNT(*) FROM card_stage_events")
    stale = rig.call("put", "/cards/1/position", token, json={"column_id": 1, "position": 0, "expected_version": 0})
    assert_conflict(stale, 1, 0, 1)
    assert card_state(rig) == before and rig.sql("SELECT COUNT(*) FROM card_stage_events") == undo


def test_a_move_without_a_version_is_a_428(rig):
    r = rig.call("put", "/cards/1/position", admin(rig), versionless=True, json={"column_id": 2, "position": 0})
    assert_required(r, 1)
    assert rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(1,)] and version(rig, 1) == 0


def test_a_stale_move_wins_over_the_lost_reason_check(rig):
    token = admin(rig)
    rig.sql("UPDATE columns SET stage_kind = 'lost' WHERE id = 2")
    assert rig.call("put", "/cards/1", token, json={"title": "a"}).status_code == 200
    stale = rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0, "expected_version": 0})
    assert_conflict(stale, 1, 0, 1)


# --- delete ----------------------------------------------------------------------------------------------------

def test_delete_with_the_current_version_succeeds_and_a_stale_one_keeps_the_card(rig):
    token = admin(rig)
    assert rig.call("put", "/cards/1", token, json={"title": "a"}).status_code == 200
    stale = rig.call("delete", "/cards/1?expected_version=0", token)
    assert_conflict(stale, 1, 0, 1)
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(1,)]
    assert rig.sql("SELECT COUNT(*) FROM audit_events WHERE action = 'delete'") == [(0,)]
    assert rig.call("delete", "/cards/1?expected_version=1", token).status_code == 204
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(0,)]


def test_a_delete_without_a_version_is_a_428_and_a_bad_version_is_a_422(rig):
    token = admin(rig)
    assert rig.call("delete", "/cards/1?expected_version=-1", token).status_code == 422
    assert_required(rig.call("delete", "/cards/1", token, versionless=True), 1)
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(1,)]
    assert rig.sql("SELECT COUNT(*) FROM audit_events WHERE action = 'delete'") == [(0,)]


def test_a_member_delete_is_refused_before_any_version_is_looked_at(rig):
    uid, token = member(rig)
    response = rig.call("delete", "/cards/1?expected_version=41", token)
    assert response.status_code == 403 and CONFLICT not in response.text


# --- create ----------------------------------------------------------------------------------------------------

def test_create_needs_no_expected_version_and_starts_at_zero_even_without_version_tables(rig):
    token = admin(rig)
    owner = rig.e.member("s@example.com")
    created = rig.call("post", "/cards", token, json={"column_id": 1, "title": "n", "salesperson_id": owner})
    assert created.status_code == 201 and created.json()["version"] == 0


# --- competing writes and rollback -----------------------------------------------------------------------------

def test_two_sequential_writes_with_the_same_version_cannot_both_succeed(rig):
    token = admin(rig)
    codes = [rig.call("put", "/cards/1", token, json={"title": t, "expected_version": 0}).status_code for t in ("a", "b")]
    assert codes == [200, 409] and version(rig) == 1 and rig.sql("SELECT title FROM cards WHERE id = 1") == [("a",)]


def test_two_concurrent_writes_with_the_same_version_cannot_both_succeed(rig):
    token = admin(rig)
    gate = threading.Barrier(2)
    codes, bodies = [], []

    def write(kind):
        gate.wait()
        for _attempt in range(3):  # a busy database answers 503 (retry later), as for every company write
            if kind == "update":
                r = rig.call("put", "/cards/1", token, json={"title": "t", "expected_version": 0})
            else:
                r = rig.call("put", "/cards/1/position", token,
                             json={"column_id": 2, "position": 0, "expected_version": 0})
            if r.status_code != 503:
                break
        codes.append(r.status_code)
        bodies.append(r.text)

    threads = [threading.Thread(target=write, args=(k,)) for k in ("update", "move")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [200, 409], bodies
    assert version(rig) == 1


@pytest.mark.parametrize("method,path,kw", [
    ("put", "/cards/1", {"json": {"title": "z", "expected_version": 0}}),
    ("put", "/cards/1/position", {"json": {"column_id": 2, "position": 0, "expected_version": 0}}),
])
def test_a_failure_after_the_version_bump_rolls_back_card_and_version(rig, monkeypatch, method, path, kw):
    def boom(*_a, **_k):
        raise RuntimeError("response could not be built")
    monkeypatch.setattr(company_routes, "card_with_version", boom)
    before = card_state(rig)
    with pytest.raises(RuntimeError):
        rig.call(method, path, admin(rig), **kw)
    assert card_state(rig) == before and version(rig) == 0


# --- the flush listener agrees with the readers (Stage 2A) -----------------------------------------------------

BROKEN_OBLIGATIONS = {
    "missing table": "DROP TABLE card_review_obligations",
    "missing column": "DROP TABLE card_review_obligations; CREATE TABLE card_review_obligations (id INTEGER PRIMARY KEY, card_id INTEGER NOT NULL)",
}


def run_ddl(rig, script):
    import sqlite3
    from database_targets import company_database_path
    con = sqlite3.connect(company_database_path("lity", rig.e.env))
    try:
        con.executescript(script)
    finally:
        con.close()


def break_schema(rig, kind, how):
    run_ddl(rig, (BROKEN if kind == "versions" else BROKEN_OBLIGATIONS)[how])


SCHEMAS = [("versions", h) for h in BROKEN] + [("obligations", h) for h in BROKEN_OBLIGATIONS]


@pytest.mark.parametrize("kind,how", SCHEMAS)
def test_a_card_change_never_commits_without_its_version_when_the_schema_is_invalid(rig, kind, how):
    break_schema(rig, kind, how)
    before = rig.sql("SELECT id, column_id, title FROM cards ORDER BY id")
    with orm(rig) as db:
        db.get(card_model(), 1).title = "silent"
        with pytest.raises(card_reviews.ReviewsNotInitialized):
            db.commit()
    assert rig.sql("SELECT id, column_id, title FROM cards ORDER BY id") == before
    with orm(rig) as db:
        db.get(card_model(), 1).column_id = 2
        with pytest.raises(card_reviews.ReviewsNotInitialized):
            db.commit()
    assert rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(1,)]


@pytest.mark.parametrize("kind,how", SCHEMAS)
def test_a_card_deletion_is_refused_by_the_listener_when_the_schema_is_invalid(rig, kind, how):
    break_schema(rig, kind, how)
    with orm(rig) as db:
        db.delete(db.get(card_model(), 1))
        with pytest.raises(card_reviews.ReviewsNotInitialized):
            db.commit()
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(1,)]


def card_model():
    from models import Card
    return Card


def test_a_position_only_change_needs_no_version_and_a_valid_schema_changes_normally(rig):
    break_schema(rig, "obligations", "missing table")
    with orm(rig) as db:  # not a tracked change: no version is owed, so the listener has nothing to refuse
        db.get(card_model(), 1).position = 5
        db.commit()
    assert rig.sql("SELECT position FROM cards WHERE id = 1") == [(5,)]


def test_a_valid_schema_without_a_row_is_version_zero_and_the_first_change_makes_it_one(rig):
    with orm(rig) as db:
        assert card_reviews.card_version(db, 1) == 0
        assert card_reviews.review_schema_problem(db.connection()) is None
        db.get(card_model(), 1).title = "first"
        db.commit()
        assert card_reviews.card_version(db, 1) == 1


@pytest.mark.parametrize("kind,how", SCHEMAS)
@pytest.mark.parametrize("query", ["", "?expected_version=0"])
def test_central_delete_is_refused_503_before_the_lock_when_the_schema_is_invalid(rig, kind, how, query):
    break_schema(rig, kind, how)
    response = rig.call("delete", f"/cards/1{query}", admin(rig))
    assert response.status_code == 503 and response.json()["detail"]["code"] == "review_not_initialized"
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(1,)]
    assert rig.sql("SELECT COUNT(*) FROM audit_events WHERE action = 'delete'") == [(0,)]


def test_central_delete_authorization_comes_before_the_schema_gate(rig):
    uid, token = member(rig)
    break_schema(rig, "obligations", "missing table")
    assert rig.call("delete", "/cards/1", token).status_code == 403  # refused before the company database opens
    assert rig.opened == []


def test_central_delete_on_a_valid_schema_keeps_its_behavior(rig):
    token = admin(rig)
    assert rig.call("delete", "/cards/99", token).status_code == 404
    assert rig.call("delete", "/cards/2", token).status_code == 204
    assert rig.sql("SELECT COUNT(*) FROM audit_events WHERE action = 'delete'") == [(1,)]


def test_a_rollback_after_the_flush_leaves_card_and_version_rows_consistent(rig):
    with orm(rig) as db:
        db.get(card_model(), 1).title = "never committed"
        db.flush()
        assert card_reviews.card_version(db, 1) == 1  # visible inside the transaction only
        db.rollback()
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("card 1",)]
    assert rig.sql("SELECT COUNT(*) FROM card_versions") == [(0,)]
