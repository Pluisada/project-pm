"""Stage 2B.3: POST /api/companies/{slug}/boards/default creates the company's first board with the six standard
stages, only on request, once, in one transaction. Disposable tmp_path databases; real routes and company engine."""
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import company_routes
import main
from company_routes import DEFAULT_BOARD_DESCRIPTION, DEFAULT_BOARD_TITLE, build_company_router
from identity_database import identity_db_dependency
from lead_options import PIPELINE_STAGES
from test_company_board_setup import empty_company
from test_company_card_writes import admin, member, rig  # noqa: F401  (fixture/helpers)
from test_company_concurrency_integrity import (  # noqa: F401  (fixture/helpers)
    TIMEOUT, Call, app_with_handler, auth, hold_write_lock, interleave, release,
)



def create(rig, token, slug="lity", client=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return (client or rig.client).post(f"/api/companies/{slug}/boards/default", headers=headers)


def warm_up(rig, token):
    """Open the company engine (lazy) so a Gate can attach to it; board 1 does not exist in an empty company."""
    assert rig.client.get("/api/companies/lity/boards", headers=auth(token)).status_code == 200


def board_state(rig, slug="lity"):
    return (
        rig.sql("SELECT id, title, description FROM boards ORDER BY id", slug=slug),
        rig.sql("SELECT board_id, title, position, stage_kind FROM columns ORDER BY id", slug=slug),
    )


@pytest.fixture
def empty(rig):
    empty_company(rig, "lity")
    return rig


def test_first_request_creates_one_board_with_the_six_stages_and_no_cards(empty):
    rig = empty
    r = create(rig, admin(rig))
    assert r.status_code == 201
    body = r.json()
    assert body["created"] is True and body["title"] == "Sales Pipeline" and body["description"] == "Company sales pipeline"
    boards, columns = board_state(rig)
    assert boards == [(body["id"], DEFAULT_BOARD_TITLE, DEFAULT_BOARD_DESCRIPTION)]
    assert [(c[1], c[2], c[3]) for c in columns] == [(t, i, k) for i, (t, k) in enumerate(PIPELINE_STAGES)]
    assert {c[0] for c in columns} == {body["id"]}
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(0,)]


def test_repeat_returns_the_same_board_with_200_and_changes_nothing(empty):
    rig = empty
    token = admin(rig)
    first = create(rig, token).json()
    state = board_state(rig)
    again = create(rig, token)
    assert again.status_code == 200
    assert again.json()["id"] == first["id"] and again.json()["created"] is False
    assert board_state(rig) == state


def test_an_existing_manual_board_is_returned_untouched(empty):
    rig = empty
    token = admin(rig)
    manual = rig.client.post("/api/companies/lity/boards", headers=auth(token), json={"title": "Mine", "description": "d"}).json()
    rig.client.post(f"/api/companies/lity/boards/{manual['id']}/columns", headers=auth(token),
                    json={"title": "Only", "position": 0, "stage_kind": "open"})
    state = board_state(rig)
    r = create(rig, token)
    assert r.status_code == 200 and r.json()["id"] == manual["id"] and r.json()["title"] == "Mine"
    assert r.json()["created"] is False
    assert board_state(rig) == state  # no default stages added, no second board


def test_seeded_company_returns_its_first_board_by_id(rig):
    r = create(rig, admin(rig))
    assert r.status_code == 200 and r.json()["id"] == 1 and r.json()["created"] is False


def test_boards_cannot_be_deleted_so_a_board_without_columns_is_returned_as_is(empty):
    rig = empty
    token = admin(rig)
    board_id = create(rig, token).json()["id"]
    assert rig.client.delete(f"/api/companies/lity/boards/{board_id}", headers=auth(token)).status_code in (404, 405)
    for column_id, in rig.sql("SELECT id FROM columns"):
        assert rig.client.delete(f"/api/companies/lity/boards/{board_id}/columns/{column_id}", headers=auth(token)).status_code == 204
    r = create(rig, token)
    assert r.status_code == 200 and r.json()["id"] == board_id
    assert rig.sql("SELECT COUNT(*) FROM columns") == [(0,)]  # the stages are never silently re-seeded


def test_a_failure_while_creating_the_stages_rolls_back_the_board_and_every_stage(empty, monkeypatch):
    rig = empty
    token = admin(rig)

    def failing(db, board):
        from models import Column
        db.add(Column(board_id=board.id, title="partial", position=0, stage_kind="open"))
        db.flush()
        raise RuntimeError("boom")

    monkeypatch.setattr(company_routes, "seed_stages", failing)
    quiet = TestClient(rig.client.app, raise_server_exceptions=False)
    assert create(rig, token, client=quiet).status_code == 500
    assert board_state(rig) == ([], [])
    monkeypatch.undo()
    assert create(rig, token).status_code == 201  # a clean retry works


def test_concurrent_requests_create_at_most_one_board(empty):
    rig = empty
    token = admin(rig)
    warm_up(rig, token)
    barrier = threading.Barrier(4, timeout=TIMEOUT)

    def go():
        barrier.wait()
        return create(rig, token)

    calls = [Call(go) for _ in range(4)]
    for c in calls:
        c.start()
    codes = sorted(c.result().status_code for c in calls)
    assert set(codes) <= {200, 201, 503} and codes.count(201) == 1, codes
    boards, columns = board_state(rig)
    assert len(boards) == 1 and len(columns) == 6


def test_interleaved_requests_create_one_board_and_the_second_sees_it(empty):
    rig = empty
    token = admin(rig)
    warm_up(rig, token)
    ra, rb = interleave(rig, lambda: create(rig, token), lambda: create(rig, token), r"INSERT INTO boards")
    assert sorted((ra.status_code, rb.status_code)) == [200, 201]
    boards, columns = board_state(rig)
    assert len(boards) == 1 and len(columns) == 6
    assert ra.json()["id"] == rb.json()["id"]


def test_lock_contention_answers_503_database_busy_and_writes_nothing(empty, app_with_handler):
    rig = empty
    token = admin(rig)
    assert create(rig, token, client=app_with_handler).status_code == 201  # the caller's local reference row now exists
    rig.sql("DELETE FROM columns")
    rig.sql("DELETE FROM boards")
    blocker = hold_write_lock(rig)
    try:
        r = create(rig, token, client=app_with_handler)
    finally:
        release(blocker)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "database_busy"
    assert r.headers.get("Retry-After") == main.REVERT_RETRY_AFTER_SECONDS
    assert board_state(rig) == ([], [])
    assert create(rig, token, client=app_with_handler).status_code == 201


def test_an_admin_of_another_company_cannot_create_a_board_here(empty):
    rig = empty
    _, other = member(rig, "o@example.com", slug="valor-log")  # a member elsewhere: no membership in lity
    before = rig.digest("lity")
    assert create(rig, other).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before


@pytest.mark.parametrize("role", ["sales_member", "visitor"])
def test_roles_without_board_administration_are_refused_before_the_database_opens(empty, role):
    rig = empty
    _, token = member(rig, f"{role}@example.com", role=role)
    before = rig.digest("lity")
    assert create(rig, token).status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before


def test_unauthenticated_callers_are_refused_before_the_database_opens(empty):
    rig = empty
    before = rig.digest("lity")
    for token in (None, "garbage"):
        assert create(rig, token).status_code == 401
    assert rig.opened == [] and rig.digest("lity") == before


def test_reads_never_create_a_board(empty):
    rig = empty
    token = admin(rig)
    before = rig.digest("lity")
    assert rig.client.get("/api/companies/lity/boards", headers=auth(token)).json() == []
    assert rig.client.get("/api/companies/lity/lead-options", headers=auth(token)).status_code == 200
    assert board_state(rig) == ([], []) and rig.digest("lity") == before
