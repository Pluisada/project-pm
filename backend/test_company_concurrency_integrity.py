"""Part 2D-D1: concurrency tests for column, card-move and Undo integrity in central (company) mode.

Tests only; no production code is changed. Real routes, real company engine, disposable tmp_path SQLite files.

How the interleavings are made deterministic (no sleep, no probability): `Gate` installs a SQLAlchemy
`before/after_cursor_execute` listener on the company engine. The FIRST statement matching a pattern pauses its
request (signals `paused`, waits for `release`). The test starts request X, waits for `paused`, runs request Y to
completion, then releases X. Which request pauses is decided by start order, not by thread identity (the route runs
in a framework worker thread). pysqlite opens its write transaction only when the DML statement executes, so a
request paused before its first DML holds no SQLite lock and the other request can commit meanwhile.

Result classes used in the report: PASS, DEFECT REPRODUCED (the assertion that fails is the defect), INCONCLUSIVE.
"""
import re
import sqlite3
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

import main
from company_routes import build_company_router
from database_targets import company_database_path
from identity_database import identity_db_dependency
from test_company_card_writes import NOW, admin, member, rig  # noqa: F401  (fixture/helpers)
from test_company_undo_import import undo

TIMEOUT = 20  # seconds; every wait is bounded so a test deadlock fails instead of hanging


class Gate:
    """Pause the first statement matching `pattern`, `when` = "before" or "after" it executes."""

    def __init__(self, rig, pattern, when="before", slug="lity"):
        self.engine = rig.runtime._engines[slug]  # exists once a request has opened the company database
        self.regex, self.when = re.compile(pattern, re.I | re.S), when
        self.paused, self.release, self._taken = threading.Event(), threading.Event(), False
        self._lock = threading.Lock()
        self.lock_attempted = threading.Event()  # another request reached BEGIN IMMEDIATE while the first is paused
        event.listen(self.engine, f"{when}_cursor_execute", self._hook)
        event.listen(self.engine, "before_cursor_execute", self._lock_hook)

    def _hook(self, conn, cursor, statement, parameters, context, executemany):
        if not self.regex.search(statement):
            return
        with self._lock:
            if self._taken:
                return
            self._taken = True
        self.paused.set()
        assert self.release.wait(TIMEOUT), "gate never released"

    def _lock_hook(self, conn, cursor, statement, parameters, context, executemany):
        if self.paused.is_set() and statement.lstrip().upper().startswith("BEGIN IMMEDIATE"):
            self.lock_attempted.set()

    def close(self):
        event.remove(self.engine, "before_cursor_execute", self._lock_hook)
        event.remove(self.engine, f"{self.when}_cursor_execute", self._hook)
        self.release.set()


class Call(threading.Thread):
    """One HTTP call in its own thread; the response or the exception is kept for the main thread."""

    def __init__(self, fn):
        super().__init__(daemon=True)
        self.fn, self.response, self.error = fn, None, None

    def run(self):
        try:
            self.response = self.fn()
        except BaseException as exc:  # propagated by result()
            self.error = exc

    def result(self):
        self.join(TIMEOUT)
        assert not self.is_alive(), "request did not finish (deadlock?)"
        if self.error:
            raise self.error
        return self.response


def warm_up(rig, token):
    """Open the company engine (lazy) so a Gate can attach to it."""
    assert rig.call("get", "", token).status_code == 200


def columns_url(path=""):
    return f"/api/companies/lity/boards/1/columns{path}"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def col_rows(rig):
    return rig.sql("SELECT id, position FROM columns WHERE board_id = 1 ORDER BY position, id")


def assert_unique_contiguous(positions):
    assert sorted(positions) == list(range(len(positions))), f"positions not unique/contiguous: {sorted(positions)}"


def seed_columns(rig, n=4):
    """Board 1 with n open columns ids 1..n at positions 0..n-1 (column 1 keeps the two seeded cards)."""
    for cid in range(3, n + 1):
        rig.sql("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
                "VALUES (?, 1, ?, ?, 'open', ?, ?)", (cid, f"C{cid}", cid - 1, NOW, NOW))


def interleave(rig, first, second, pattern, when="before"):
    """Run `first` until its first statement matching `pattern`, run `second` to completion, then resume `first`."""
    gate = Gate(rig, pattern, when)
    try:
        a = Call(first)
        a.start()
        assert gate.paused.wait(TIMEOUT), "first request never reached the gate"
        b = Call(second)
        b.start()
        # The second request either completes (no write lock needed: nothing to serialize) or reaches its
        # BEGIN IMMEDIATE and waits for the first, which is the protection under test. Either way the first
        # is resumed only after that is established, so the order is decided by the lock, not by the test.
        deadline = TIMEOUT
        while b.is_alive() and not gate.lock_attempted.is_set() and deadline > 0:
            b.join(0.05)
            deadline -= 0.05
        gate.release.set()
        return a.result(), b.result()
    finally:
        gate.close()


# --- Group A: columns -----------------------------------------------------------------------------------------------

def test_A1_interleaved_reorders_leave_duplicate_positions(rig):
    """A1. Both reorders read the same initial state; the second writes after the first committed.
    The renumbering is read-compute-write with no lock/version check, so the second only writes the rows that
    differ from ITS stale read and leaves the first's values in the others. Defect: duplicate positions."""
    token = admin(rig)
    seed_columns(rig, 4)  # a0 b1 c2 d3 (ids 1..4)
    warm_up(rig, token)
    # X (paused before its first UPDATE, having read a0 b1 c2 d3): a to index 1 -> b0 a1 c2 d3; writes only rows that
    #   differ from ITS read (b, a). Y (runs meanwhile): d to index 0 -> d0 a1 b2 c3, committed.
    # After X resumes: b=0 (X) next to d=0 (Y).
    x = lambda: rig.client.put(columns_url("/1"), json={"position": 1}, headers=auth(token))
    y = lambda: rig.client.put(columns_url("/4"), json={"position": 0}, headers=auth(token))
    rx, ry = interleave(rig, x, y, r"UPDATE columns")
    assert (rx.status_code, ry.status_code) == (200, 200)  # both are reported as success
    assert_unique_contiguous([p for _, p in col_rows(rig)])  # FAILS today: b0 and d0 both hold position 0


def test_A1_sequential_reorders_are_the_control(rig):
    """Control: the same two operations one after the other give a unique, contiguous order."""
    token = admin(rig)
    seed_columns(rig, 4)
    assert rig.client.put(columns_url("/4"), json={"position": 0}, headers=auth(token)).status_code == 200
    assert rig.client.put(columns_url("/1"), json={"position": 1}, headers=auth(token)).status_code == 200
    assert [i for i, _ in col_rows(rig)] == [4, 1, 2, 3]


def test_A2_concurrent_creates_both_finish_with_unique_positions(rig):
    """A2. Two creates interleaved the same way (second commits between the first's read and write)."""
    token = admin(rig)
    warm_up(rig, token)
    mk = lambda title: (lambda: rig.client.post(columns_url(), json={"title": title, "position": 0}, headers=auth(token)))
    ra, rb = interleave(rig, mk("A"), mk("B"), r"UPDATE columns|INSERT INTO columns")
    codes = (ra.status_code, rb.status_code)
    assert 500 not in codes and all(c in (201, 409, 503) for c in codes), codes
    rows = col_rows(rig)
    created = 2 if codes == (201, 201) else 1
    assert len(rows) == 2 + created, "a column was lost"  # seeded 2 + the successful creates
    assert_unique_contiguous([p for _, p in rows])


def test_A2_threads_started_together_never_500_and_lose_nothing(rig):
    """A2 (barrier variant, not a forced interleaving): whatever order the engine picks, no 500 and no lost column."""
    token = admin(rig)
    warm_up(rig, token)
    barrier = threading.Barrier(2, timeout=TIMEOUT)

    def mk(title):
        def go():
            barrier.wait()
            return rig.client.post(columns_url(), json={"title": title, "position": 99}, headers=auth(token))
        return go
    a, b = Call(mk("A")), Call(mk("B"))
    a.start(); b.start()
    codes = [a.result().status_code, b.result().status_code]
    assert 500 not in codes and all(c in (201, 503) for c in codes), codes
    assert len(col_rows(rig)) == 2 + codes.count(201)
    assert_unique_contiguous([p for _, p in col_rows(rig)])


@pytest.mark.parametrize("kind", ["won", "lost"])
def test_A3_concurrent_promotion_to_single_won_or_lost_column(rig, kind):
    """A3. The API allows changing an empty column's stage_kind. Two requests promote two different empty columns to
    the same kind; _check_stage_kind (at most one won / one lost per board) reads before either commits."""
    token = admin(rig)
    seed_columns(rig, 3)  # columns 1 (has cards), 2 and 3 are empty
    warm_up(rig, token)
    promote = lambda cid: (lambda: rig.client.put(columns_url(f"/{cid}"), json={"stage_kind": kind}, headers=auth(token)))
    r2, r3 = interleave(rig, promote(2), promote(3), r"UPDATE columns")
    assert 500 not in (r2.status_code, r3.status_code)
    kinds = [k for (k,) in rig.sql("SELECT stage_kind FROM columns WHERE board_id = 1")]
    assert kinds.count(kind) <= 1, f"{kinds.count(kind)} '{kind}' columns on one board (codes {r2.status_code}/{r3.status_code})"


def test_A3_concurrent_creates_of_won_column(rig):
    """A3 (create flow): two creates of a 'won' column; the check precedes the INSERT of each."""
    token = admin(rig)
    warm_up(rig, token)
    mk = lambda t: (lambda: rig.client.post(columns_url(), json={"title": t, "position": 5, "stage_kind": "won"}, headers=auth(token)))
    ra, rb = interleave(rig, mk("W1"), mk("W2"), r"INSERT INTO columns")
    assert 500 not in (ra.status_code, rb.status_code)
    kinds = [k for (k,) in rig.sql("SELECT stage_kind FROM columns WHERE board_id = 1")]
    assert kinds.count("won") <= 1, f"{kinds.count('won')} won columns (codes {ra.status_code}/{rb.status_code})"


# --- Group B: delete column / move cards ----------------------------------------------------------------------------

def test_B1_move_into_a_column_being_deleted_never_loses_the_card(rig):
    """B1. The delete passes its 'column is empty' check (SELECT cards ...); then a move lands card 1 in that column
    and commits; then the delete proceeds. The card must survive (move refused, or delete refused)."""
    token = admin(rig)
    warm_up(rig, token)
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id = 2") == [(0,)]
    delete = lambda: rig.client.delete(columns_url("/2"), headers=auth(token))
    move = lambda: rig.client.put("/api/companies/lity/boards/1/cards/1/position", json={"column_id": 2, "position": 0},
                                  headers=auth(token))
    rd, rm = interleave(rig, delete, move, r"FROM cards\s+WHERE cards\.column_id = \?\s+LIMIT", when="after")
    # The delete holds the write lock from its first statement, so the move waits and runs second: the column is
    # gone, the move is refused (404) and the card stays. (The other legal outcome, the move first and the delete
    # refused with 409, is accepted too.) What may never happen is a 200 move whose card vanished with the column.
    assert (rd.status_code, rm.status_code) in ((204, 404), (409, 200)), (rd.status_code, rm.status_code)
    card = rig.sql("SELECT id, column_id FROM cards WHERE id = 1")
    assert card, f"card 1 silently disappeared (delete answered {rd.status_code}, move answered {rm.status_code})"
    assert rig.sql("SELECT COUNT(*) FROM columns WHERE id = ?", (card[0][1],)) == [(1,)], "card points at a missing column"


def test_B1_reverse_order_move_is_refused_with_404(rig):
    """B1 control: delete committed first, then the move: the target column is gone, the card stays where it was."""
    token = admin(rig)
    assert rig.client.delete(columns_url("/2"), headers=auth(token)).status_code == 204
    r = rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0})
    assert r.status_code == 404
    assert rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(1,)]


UNDO_CLAIM_FREE_DML = r"DELETE FROM undo_entries WHERE undo_entries\.user_id"  # record_undo: first write of a move


def move_url(card):
    return f"/api/companies/lity/boards/1/cards/{card}/position"


def card_layout(rig):
    out = {}
    for cid, col, pos in rig.sql("SELECT id, column_id, position FROM cards ORDER BY column_id, position, id"):
        out.setdefault(col, []).append((cid, pos))
    return out


def seed_cards(rig):
    """Column 1: cards 1..4 at 0..3; column 2: cards 5,6 at 0,1."""
    rig.sql("DELETE FROM cards")
    for cid, col, pos in ((1, 1, 0), (2, 1, 1), (3, 1, 2), (4, 1, 3), (5, 2, 0), (6, 2, 1)):
        rig.sql("INSERT INTO cards (id, column_id, title, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (cid, col, f"card {cid}", pos, NOW, NOW))


def test_B2_two_moves_of_different_cards_in_the_same_column(rig):
    """B2. (Pause point: just before the move's first DML, `DELETE FROM undo_entries` in record_undo. From that
    statement on the request holds SQLite's write lock, so the interleaving that remains possible is "stale card
    loaded before the lock".) Semantics expected: `position` is the index among the target column's cards and the column is renumbered
    0..n-1 (see test_company_card_move_positions). Which card ends first depends on commit order, but positions must
    stay unique and contiguous and no card may be lost or duplicated. X: card 1 -> index 2; Y: card 4 -> index 0."""
    token = admin(rig)
    seed_cards(rig)
    warm_up(rig, token)
    mv = lambda card, pos: (lambda: rig.client.put(move_url(card), json={"column_id": 1, "position": pos}, headers=auth(token)))
    rx, ry = interleave(rig, mv(1, 2), mv(4, 0), UNDO_CLAIM_FREE_DML)
    assert (rx.status_code, ry.status_code) == (200, 200)
    assert rig.sql("SELECT COUNT(*), COUNT(DISTINCT id) FROM cards") == [(6, 6)]
    positions = [p for (p,) in rig.sql("SELECT position FROM cards WHERE column_id = 1")]
    assert_unique_contiguous(positions)


def test_B2_moves_between_the_same_two_columns(rig):
    """B2 variant: card 1 goes 1 -> 2 while card 5 goes 2 -> 1; both columns must stay dense."""
    token = admin(rig)
    seed_cards(rig)
    warm_up(rig, token)
    mv = lambda card, col, pos: (lambda: rig.client.put(move_url(card), json={"column_id": col, "position": pos}, headers=auth(token)))
    rx, ry = interleave(rig, mv(1, 2, 0), mv(5, 1, 0), UNDO_CLAIM_FREE_DML)
    assert (rx.status_code, ry.status_code) == (200, 200)
    assert rig.sql("SELECT COUNT(*), COUNT(DISTINCT id) FROM cards") == [(6, 6)]
    for col in (1, 2):
        assert_unique_contiguous([p for (p,) in rig.sql("SELECT position FROM cards WHERE column_id = ?", (col,))])


def test_B3_same_card_moved_to_two_destinations(rig):
    """B3. The same card is moved to column 2 and, concurrently, to column 3. A business conflict is legitimate (one
    wins); corruption is not: exactly one column, no duplicate card, dense positions in all three columns."""
    token = admin(rig)
    seed_columns(rig, 3)
    seed_cards(rig)
    rig.sql("INSERT INTO cards (id, column_id, title, position, created_at, updated_at) VALUES (7, 3, 'card 7', 0, ?, ?)", (NOW, NOW))
    warm_up(rig, token)
    mv = lambda col: (lambda: rig.client.put(move_url(1), json={"column_id": col, "position": 0}, headers=auth(token)))
    rx, ry = interleave(rig, mv(2), mv(3), UNDO_CLAIM_FREE_DML)
    assert 500 not in (rx.status_code, ry.status_code)
    assert rig.sql("SELECT COUNT(*), COUNT(DISTINCT id) FROM cards") == [(7, 7)]
    assert len(rig.sql("SELECT column_id FROM cards WHERE id = 1")) == 1
    for col in (1, 2, 3):
        pos = [p for (p,) in rig.sql("SELECT position FROM cards WHERE column_id = ?", (col,))]
        assert_unique_contiguous(pos)


# --- Group C: Undo --------------------------------------------------------------------------------------------------

def test_C1_undo_after_the_owner_moved_the_card_on_is_refused(rig):
    """C1 (sequenced): admin moves card 1 to column 2 (undoable); the card's owner (a member) moves it on to
    column 3 before the admin undoes. Undo's `expect` check must refuse (409), keep the later placement and leave
    every column dense."""
    token = admin(rig)
    seed_columns(rig, 3)
    seed_cards(rig)
    uid, owner = member(rig, "second@example.com")
    rig.assign(1, uid)
    assert rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0}).status_code == 200
    assert rig.call("put", "/cards/1/position", owner, json={"column_id": 3, "position": 0}).status_code == 200
    r = undo(rig, token)
    assert r.status_code == 409, r.text
    assert rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(3,)]
    for col in (1, 2, 3):
        assert_unique_contiguous([p for (p,) in rig.sql("SELECT position FROM cards WHERE column_id = ?", (col,))])


def test_C1_undo_applied_between_a_moves_card_load_and_its_first_write(rig):
    """C1 (interleaved). The owner's move to column 3 has loaded card 1 (still in column 2) and is about to take
    the write lock; the admin's undo then runs to completion (card back in column 1); the move resumes with the stale
    card. The move is acknowledged (200) and the card ends in column 3, but the column it really left (1) is never
    renumbered, so column 1 is left with a gap. Expected: either the move is refused as stale, or all columns stay
    dense."""
    token = admin(rig)
    seed_columns(rig, 3)
    seed_cards(rig)
    uid, owner = member(rig, "second@example.com")
    rig.assign(1, uid)
    assert rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0}).status_code == 200
    run_move = lambda: rig.client.put(move_url(1), json={"column_id": 3, "position": 0}, headers=auth(owner))
    rm, ru = interleave(rig, run_move, lambda: undo(rig, token), UNDO_CLAIM_FREE_DML)
    # The move holds the write lock from its first statement, so the undo runs after it and must find card 1 in
    # column 3, not where its `expect` says (column 2): refused with 409, the later placement kept.
    assert (rm.status_code, ru.status_code) == (200, 409), (rm.status_code, ru.status_code, ru.text)
    assert rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(3,)]
    assert rig.sql("SELECT COUNT(*), COUNT(DISTINCT id) FROM cards") == [(6, 6)]
    for col in (1, 2, 3):
        assert_unique_contiguous([p for (p,) in rig.sql("SELECT position FROM cards WHERE column_id = ?", (col,))])


def test_C2_double_consumption_of_one_undo_entry_is_covered_elsewhere(rig):
    """C2. Already covered exactly by test_company_undo_import.py::test_concurrent_undo_requests_apply_at_most_once
    (barrier, two simultaneous POST /undo: one 200, none 500, card recreated once) and by
    test_admin_edit_undo_roundtrip_and_entry_is_used_up (second undo 404). Not duplicated; this adds the claim step
    seen under a forced interleaving: the loser must find the entry gone and apply nothing."""
    token = admin(rig)
    seed_cards(rig)
    assert rig.call("put", "/cards/1/position", token, json={"column_id": 2, "position": 0}).status_code == 200
    warm_up(rig, token)
    ra, rb = interleave(rig, lambda: undo(rig, token), lambda: undo(rig, token), r"DELETE FROM undo_entries WHERE undo_entries\.id")
    codes = sorted([ra.status_code, rb.status_code])
    assert codes == [200, 404], codes  # one applies; the other finds the entry gone (nothing to undo)
    assert rig.sql("SELECT column_id FROM cards WHERE id = 1") == [(1,)]
    assert_unique_contiguous([p for (p,) in rig.sql("SELECT position FROM cards WHERE column_id = 1")])


# --- Group D: SQLite contention -------------------------------------------------------------------------------------

@pytest.fixture
def app_with_handler(rig):
    """The company router plus main.py's real OperationalError handler (the Rig app has none)."""
    app = FastAPI()
    app.include_router(build_company_router(identity_db_dependency(rig.e.factory), rig.runtime))
    app.add_exception_handler(OperationalError, main.database_error_handler)
    return TestClient(app, raise_server_exceptions=False)


def hold_write_lock(rig):
    blocker = sqlite3.connect(company_database_path("lity", rig.e.env))
    blocker.execute("BEGIN IMMEDIATE")
    return blocker


def release(blocker):
    blocker.rollback()
    blocker.close()


def test_D1_busy_database_on_a_column_operation_answers_503_with_retry_after(rig, app_with_handler):
    """D1. Another connection holds the write lock; a column create arrives from a user whose local reference row
    already exists (so the first write is the column INSERT itself). No busy timeout is configured on the engine
    (sqlite3 default, 5 s). Contract in main.database_error_handler: 503 + Retry-After, no SQL/driver text, nothing
    partially applied."""
    token = admin(rig)
    assert app_with_handler.post(columns_url(), json={"title": "seed", "position": 9}, headers=auth(token)).status_code == 201
    before = rig.sql("SELECT * FROM columns ORDER BY id")
    blocker = hold_write_lock(rig)
    try:
        r = app_with_handler.post(columns_url(), json={"title": "blocked", "position": 0}, headers=auth(token))
    finally:
        release(blocker)
    assert r.status_code == 503, (r.status_code, r.text)
    assert r.headers.get("Retry-After") == main.REVERT_RETRY_AFTER_SECONDS
    assert "SELECT" not in r.text and "locked" not in r.text.lower()
    assert rig.sql("SELECT * FROM columns ORDER BY id") == before
    assert app_with_handler.post(columns_url(), json={"title": "ok", "position": 0}, headers=auth(token)).status_code == 201


def test_D1_busy_database_on_a_first_write_by_a_new_user_has_no_retry_after(rig, app_with_handler):
    """D1 (variant). The same contention, but the caller has no local reference row yet: the write that needs the
    lock is sync_company_user's INSERT, which identity_service turns into LocalSyncError, and company_routes into
    a fixed 503 'Company data is unavailable'. Expected by the contract of the busy handler: 503 with Retry-After
    (the client is told to retry). Nothing may be partially applied."""
    token = admin(rig)
    before = rig.sql("SELECT * FROM columns ORDER BY id")
    users_before = rig.sql("SELECT id FROM users ORDER BY id")
    blocker = hold_write_lock(rig)
    try:
        r = app_with_handler.post(columns_url(), json={"title": "blocked", "position": 0}, headers=auth(token))
    finally:
        release(blocker)
    assert r.status_code == 503, (r.status_code, r.text)
    assert rig.sql("SELECT * FROM columns ORDER BY id") == before and rig.sql("SELECT id FROM users ORDER BY id") == users_before
    assert r.headers.get("Retry-After") == main.REVERT_RETRY_AFTER_SECONDS, f"503 without Retry-After: {r.json()}"


# --- Group E: card create racing with the deletion of its target column ----------------------------------------------

def test_E1_create_card_in_a_column_being_deleted_never_500(rig):
    """E1. The create reads its target column (SELECT columns ...); then the column's delete commits; then the create
    inserts. Under the write lock the delete waits (create first, then 409 because the column now has a card) or the
    create runs after it and is refused with 404. A foreign-key failure surfacing as 500 is the defect."""
    token = admin(rig)
    warm_up(rig, token)
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE column_id = 2") == [(0,)]
    create = lambda: rig.client.post("/api/companies/lity/boards/1/cards",
                                     json={"column_id": 2, "title": "Racing", "position": 0, "salesperson_id": rig.e.admin},
                                     headers=auth(token))
    delete = lambda: rig.client.delete(columns_url("/2"), headers=auth(token))
    rc, rd = interleave(rig, create, delete, r"FROM columns\s+WHERE columns\.id = \?\s+AND columns\.board_id", when="after")
    assert (rc.status_code, rd.status_code) in ((201, 409), (404, 204)), (rc.status_code, rd.status_code)
    cards = rig.sql("SELECT column_id FROM cards WHERE title = 'Racing'")
    for (column_id,) in cards:
        assert rig.sql("SELECT COUNT(*) FROM columns WHERE id = ?", (column_id,)) == [(1,)], "card points at a missing column"


# --- Group F1: card edit and delete take the write lock before reading the card ---------------------------------------

BEGIN_WRITE = r"BEGIN IMMEDIATE"  # the first request pauses just before taking the lock; the second one runs fully


def card_url(card):
    return f"/api/companies/lity/boards/1/cards/{card}"


def test_F1_edit_after_reassignment_is_refused_and_leaves_no_trace(rig):
    """A member's edit that has not yet taken the write lock must judge ownership after a concurrent reassignment."""
    token = admin(rig)
    uid1, owner = member(rig, "first@example.com")
    uid2, _ = member(rig, "second@example.com")
    rig.sql("INSERT INTO users (id, username, created_at, updated_at) VALUES (?, 'second', ?, ?)", (uid2, NOW, NOW))
    rig.assign(1, uid1)
    warm_up(rig, token)
    edit = lambda: rig.client.put(card_url(1), json={"title": "stale edit"}, headers=auth(owner))
    reassign = lambda: rig.client.put(card_url(1), json={"salesperson_id": uid2}, headers=auth(token))
    re_, rr = interleave(rig, edit, reassign, BEGIN_WRITE)
    assert (rr.status_code, re_.status_code) == (200, 403), (rr.text, re_.text)
    assert rig.sql("SELECT salesperson_id FROM cards WHERE id = 1") == [(uid2,)]
    assert rig.sql("SELECT title FROM cards WHERE id = 1") != [("stale edit",)]
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(0,)]
    assert rig.sql("SELECT COUNT(*) FROM undo_entries WHERE user_id = ?", (uid1,)) == [(0,)]
    assert rig.sql("SELECT action FROM audit_events WHERE card_id = 1") == [("assign",)]


def test_F1_concurrent_deletes_succeed_once(rig):
    """Two deletes of the same card: one 204, the other 404, one audit row and one restorable undo entry."""
    token = admin(rig)
    warm_up(rig, token)
    delete = lambda: rig.client.delete(card_url(1), headers=auth(token))
    ra, rb = interleave(rig, delete, delete, BEGIN_WRITE)
    assert sorted((ra.status_code, rb.status_code)) == [204, 404], (ra.text, rb.text)
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE id = 1") == [(0,)]
    assert rig.sql("SELECT action FROM audit_events WHERE card_id = 1") == [("delete",)]
    assert rig.sql("SELECT COUNT(*) FROM undo_entries") == [(1,)]
    assert undo(rig, token).status_code == 200
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]  # restored under a new id


def test_F1_edit_that_loses_the_race_to_a_delete_is_404_without_records(rig):
    token = admin(rig)
    uid, owner = member(rig, "first@example.com")
    rig.assign(1, uid)
    warm_up(rig, token)
    edit = lambda: rig.client.put(card_url(1), json={"title": "too late"}, headers=auth(owner))
    delete = lambda: rig.client.delete(card_url(1), headers=auth(token))
    re_, rd = interleave(rig, edit, delete, BEGIN_WRITE)
    assert (rd.status_code, re_.status_code) == (204, 404), (rd.text, re_.text)
    assert rig.sql("SELECT COUNT(*) FROM card_edit_history") == [(0,)]
    assert rig.sql("SELECT COUNT(*) FROM undo_entries WHERE user_id = ?", (uid,)) == [(0,)]
    assert rig.sql("SELECT action FROM audit_events WHERE card_id = 1") == [("delete",)]


def test_F1_delete_that_loses_the_race_to_an_edit_snapshots_the_edited_card(rig):
    """The edit is made by an administrator: it leaves no pending review, so the deletion stays undoable and the undo
    snapshot taken under the lock must carry the edited title (a member's edit is the next test)."""
    token = admin(rig)
    warm_up(rig, token)
    delete = lambda: rig.client.delete(card_url(1), headers=auth(token))
    edit = lambda: rig.client.put(card_url(1), json={"title": "edited first"}, headers=auth(token))
    rd, re_ = interleave(rig, delete, edit, BEGIN_WRITE)
    assert (re_.status_code, rd.status_code) == (200, 204), (re_.text, rd.text)
    assert rig.sql("SELECT action FROM audit_events WHERE card_id = 1") == [("delete",)]
    assert rig.sql("SELECT COUNT(*) FROM card_review_obligations") == [(0,)]
    assert undo(rig, token).status_code == 200
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE title = ?", ("edited first",)) == [(1,)]


def test_F1_F3_delete_that_loses_the_race_to_a_member_edit_voids_the_review_and_is_not_undoable(rig):
    """A member's edit that wins the race leaves pending reviews; the deletion voids them and (F3) cannot be undone."""
    token = admin(rig)
    uid, owner = member(rig, "first@example.com")
    rig.assign(1, uid)
    warm_up(rig, token)
    delete = lambda: rig.client.delete(card_url(1), headers=auth(token))
    edit = lambda: rig.client.put(card_url(1), json={"title": "edited first"}, headers=auth(owner))
    rd, re_ = interleave(rig, delete, edit, BEGIN_WRITE)
    assert (re_.status_code, rd.status_code) == (200, 204), (re_.text, rd.text)
    assert rig.sql("SELECT action FROM audit_events WHERE card_id = 1") == [("delete",)]
    assert rig.sql("SELECT reviewer_role, status FROM card_review_obligations ORDER BY id") == [
        ("admin", "void"), ("super_admin", "void")]
    assert undo(rig, token).status_code == 404
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE title = ?", ("edited first",)) == [(0,)]
