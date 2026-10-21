"""Part 22D: bid_import_service - transactional import into temp SQLite files
with the full schema. Synthetic workbooks (fictitious data) only."""
import hashlib
import json
import os
import sqlite3
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import bid_import_service as service
from bid_excel_reader import read_bid_excel
from bid_import_preview import Action, RowDecision
from bid_import_service import ImportConflict, ImportRejected, apply_bid_import, preview_bid_import
from lead_options import PIPELINE_STAGES
from card_rules import apply_card_updates
from models import (
    Base, Board, BoardColumn, Card, CardBidDetails, CardEditHistory, CardImportChange, CardSourceRef, CardStageEvent,
    ImportBatch, User,
)
from stage_history import SOURCE_API, set_stage_context, utcnow
from test_bid_excel_reader import build, row as excel_row

TABLES = ("users", "boards", "columns", "cards", "card_bid_details", "card_source_refs", "import_batches",
          "card_stage_events", "card_import_changes", "card_edit_history")


@pytest.fixture
def env(tmp_path):
    path = tmp_path / "import.db"
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    admin = User(username="admin", password_hash="x", role="admin")
    db.add(admin)
    db.flush()  # the commercial rows refer to the user by id (no ORM link across the identity boundary)
    board = Board(title="Pipeline", user_id=admin.id)
    columns = [BoardColumn(board=board, title=t, position=i, stage_kind=k) for i, (t, k) in enumerate(PIPELINE_STAGES)]
    manual = Card(column=columns[3], title="Manual card", position=0)  # not imported; must never be touched
    db.add_all([board, *columns, manual])
    db.commit()
    db.info.clear()

    class Env:
        pass

    e = Env()
    e.path, e.engine, e.db, e.admin, e.board = path, engine, db, admin, board
    e.columns = {c.title: c for c in columns}
    e.manual_id = manual.id
    yield e
    db.close()
    engine.dispose()


def workbook(*rows):
    return read_bid_excel(build(list(rows)), "relatorio-leads.xlsx")


def run(env, read_result, decisions=()):
    preview = preview_bid_import(env.db, read_result, env.board.id, decisions)
    return preview, apply_bid_import(env.db, read_result, preview, decisions, env.board.id, env.admin)


def counts(path):
    conn = sqlite3.connect(path)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    finally:
        conn.close()


def snapshot(path, tables=TABLES):
    conn = sqlite3.connect(path)
    try:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}
    finally:
        conn.close()


COMMERCIAL = tuple(t for t in TABLES if t != "import_batches")


def commercial(path):
    """Everything except import_batches, where a failed attempt is recorded."""
    return snapshot(path, COMMERCIAL)


def failed_batches(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT failure_kind, failure_reason, user_id, board_id, file_sha256, created_count, updated_count, "
            "mapping_json FROM import_batches WHERE status = 'failed' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def card_by_code(db, code):
    return db.query(Card).join(CardSourceRef).filter(CardSourceRef.source_code == code).one()


def events_of(db, card_id):
    return db.query(CardStageEvent).filter(CardStageEvent.card_id == card_id).order_by(CardStageEvent.id).all()


ROWS = (
    excel_row(**{"Código": "000101-01"}),                                                   # OPEN/RFQ -> Proposal
    excel_row(**{"Código": "000102-01", "CAT": "WON", "Estágio": "AWARD GIVEN"}),           # -> Award
    excel_row(**{"Código": "000103-01", "CAT": "CLOSED", "Estágio": "CLOSED (BY SALCOMP)"}),  # review
    excel_row(**{"Código": "000104-01", "CAT": "CLOSED", "Estágio": "NOT AWARDED BY CUSTOMER"}),  # confirm -> Lost
    excel_row(**{"Código": "000105-01"}),                                                   # skipped
)
DECISIONS = (RowDecision(5, confirm_proposed_stage=True), RowDecision(6, skip=True))


# --- creating a batch ----------------------------------------------------------------------

class TestCreate:
    def test_full_batch(self, env):
        started = utcnow()
        preview, outcome = run(env, workbook(*ROWS), DECISIONS)
        assert len(outcome.created_card_ids) == 3 and outcome.updated_card_ids == ()
        assert (outcome.skipped, outcome.review) == (1, 1)
        batch = env.db.get(ImportBatch, outcome.batch_id)
        assert (batch.created_count, batch.updated_count, batch.skipped_count, batch.review_count) == (3, 0, 1, 1)
        assert batch.status == "completed" and batch.completed_at >= started
        assert (batch.board_id, batch.user_id, batch.filename) == (env.board.id, env.admin.id, "relatorio-leads.xlsx")
        assert batch.file_sha256 == preview.file.sha256
        mapping = json.loads(batch.mapping_json)
        assert mapping["source_key_version"] == "v1" and len(mapping["decisions"]) == 2
        assert "Cliente Ficticio" not in batch.mapping_json  # no row content in the batch
        stages = {card_by_code(env.db, code).column.title for code in ("000101-01", "000102-01", "000104-01")}
        assert stages == {"Proposal", "Award", "Lost"}

    def test_card_and_bid_details(self, env):
        run(env, workbook(*ROWS), DECISIONS)
        card = card_by_code(env.db, "000101-01")
        assert (card.title, card.details, card.company) == ("Projeto Teste", "Descricao", "Cliente Ficticio")
        for unmapped in ("deal_value", "currency", "expected_close_date", "lead_source", "product_line", "contact",
                         "salesperson_id"):
            assert getattr(card, unmapped) is None
        assert card.position == 1  # after the manual card already in Proposal
        details = card.bid_details
        assert details.revenue_current_year_cents == 123456 and details.revenue_next_year_cents is None
        assert details.revenue_currency == "BRL"
        assert (details.volume_current_year, details.volume_next_year) == (1500, 3000)
        assert details.next_step_due == date(2026, 6, 30) and details.production_start_date == date(2026, 7, 1)
        assert details.customer_type == "LEAD" and details.project_lead == "Pessoa B"
        assert env.db.query(User).count() == 1  # nobody created from CPM / Líder do projeto

    def test_source_ref(self, env):
        _, outcome = run(env, workbook(*ROWS), DECISIONS)
        ref = card_by_code(env.db, "000101-01").source_ref
        assert ref.source_system == "salcomp-bids"
        assert ref.source_key == 'v1:["000101-01","cliente ficticio"]'
        assert (ref.source_code, ref.source_status, ref.source_stage) == ("000101-01", "OPEN", "RFQ")
        assert ref.source_created_at == datetime(2026, 2, 1) and ref.source_updated_at == datetime(2026, 3, 5)
        assert ref.source_last_edited_at is None
        assert ref.source_row_hash.startswith("v1:") and ref.last_import_batch_id == outcome.batch_id

    def test_baseline_events_only(self, env):
        started = utcnow()
        _, outcome = run(env, workbook(*ROWS), DECISIONS)
        for card_id in outcome.created_card_ids:
            [only] = events_of(env.db, card_id)
            assert (only.event_type, only.source, only.user_id) == ("baseline", "import", env.admin.id)
            assert only.from_column_id is None and only.occurred_at >= started  # import time, not a source date
        won = card_by_code(env.db, "000102-01")
        [baseline] = events_of(env.db, won.id)
        assert baseline.to_stage_kind == "won"  # in Award, but no fictitious "won" event
        assert env.db.query(CardStageEvent).filter(
            CardStageEvent.card_id.in_(outcome.created_card_ids),
            CardStageEvent.event_type.in_(["created", "won", "lost", "moved"]),
        ).count() == 0
        assert env.db.query(CardStageEvent).filter(CardStageEvent.source == "import").count() == 3

    def test_stage_context_is_restored(self, env):
        set_stage_context(env.db, env.admin, SOURCE_API)
        run(env, workbook(ROWS[0]))
        env.db.add(Card(column_id=env.columns["RFI"].id, title="After", position=0))
        env.db.commit()
        [event_] = events_of(env.db, env.db.query(Card).filter(Card.title == "After").one().id)
        assert (event_.event_type, event_.source) == ("created", "api")


# --- what must not be created ----------------------------------------------------------------

class TestRejections:
    def test_pending_rows_are_not_created(self, env):
        _, outcome = run(env, workbook(*ROWS))  # no decisions: rows 4 and 5 stay pending
        assert len(outcome.created_card_ids) == 3 and outcome.review == 2 and outcome.skipped == 0
        codes = {ref.source_code for ref in env.db.query(CardSourceRef)}
        assert codes == {"000101-01", "000102-01", "000105-01"}

    @pytest.mark.parametrize("decisions", [
        (RowDecision(4, confirm_proposed_stage=True),),          # nothing to confirm on CLOSED (BY SALCOMP)
        (RowDecision(2, skip=True, confirm_update=True),),
        (RowDecision(2, confirm_update=True),),                  # new record: no update to confirm
    ])
    def test_decisions_that_do_not_fit_reject_everything(self, env, decisions):
        before = commercial(env.path)
        read = workbook(*ROWS)  # built once: the .xlsx carries a creation time (to the second), so two builds can differ
        preview = preview_bid_import(env.db, read, env.board.id, decisions)
        with pytest.raises(ImportRejected, match="don't fit"):
            apply_bid_import(env.db, read, preview, decisions, env.board.id, env.admin)
        assert commercial(env.path) == before

    @pytest.mark.parametrize("decision", [RowDecision(2, target_stage="Negotiation"), RowDecision(99, skip=True)])
    def test_invalid_decisions(self, env, decision):
        with pytest.raises(ImportRejected):
            preview_bid_import(env.db, workbook(*ROWS), env.board.id, [decision])

    def test_decisions_must_match_the_confirmed_preview(self, env):
        before = commercial(env.path)
        read = workbook(*ROWS)  # built once (see test_decisions_that_do_not_fit_reject_everything)
        preview = preview_bid_import(env.db, read, env.board.id)
        with pytest.raises(ImportConflict):  # preview without the decisions, applied with them
            apply_bid_import(env.db, read, preview, DECISIONS, env.board.id, env.admin)
        assert commercial(env.path) == before

    def test_internal_duplicates_are_not_created(self, env):
        dup = excel_row(**{"Código": "000101-01", "Cliente": "  CLIENTE ficticio "})
        _, outcome = run(env, workbook(ROWS[0], dup, ROWS[1]))
        assert len(outcome.created_card_ids) == 1 and outcome.review == 2

    def test_file_with_errors(self, env):
        bad = read_bid_excel(b"", "relatorio-leads.xlsx")
        preview = preview_bid_import(env.db, bad, env.board.id)
        with pytest.raises(ImportRejected, match="errors"):
            apply_bid_import(env.db, bad, preview, (), env.board.id, env.admin)

    def test_preview_of_another_file(self, env):
        preview = preview_bid_import(env.db, workbook(ROWS[0]), env.board.id)
        with pytest.raises(ImportRejected, match="another file"):
            apply_bid_import(env.db, workbook(ROWS[1]), preview, (), env.board.id, env.admin)

    def test_unknown_board_or_user(self, env):
        rr = workbook(ROWS[0])
        with pytest.raises(ImportRejected, match="Board"):
            preview_bid_import(env.db, rr, 999)
        preview = preview_bid_import(env.db, rr, env.board.id)
        ghost = User(id=999, username="ghost", password_hash="x", role="admin")
        with pytest.raises(ImportRejected, match="user"):
            apply_bid_import(env.db, rr, preview, (), env.board.id, ghost)
        # Recorded without a user (it doesn't exist); no commercial data.
        [(kind, _reason, user_id, *_rest)] = failed_batches(env.path)
        assert (kind, user_id) == ("validation", None) and counts(env.path)["cards"] == 1

    def test_session_must_be_clean(self, env):
        rr = workbook(ROWS[0])
        preview = preview_bid_import(env.db, rr, env.board.id)
        env.db.add(Card(column_id=env.columns["RFI"].id, title="Unsaved", position=0))
        with pytest.raises(ImportRejected, match="unsaved"):
            apply_bid_import(env.db, rr, preview, (), env.board.id, env.admin)

    def test_key_imported_into_another_board(self, env):
        run(env, workbook(ROWS[0]))
        other = Board(title="Other", user_id=env.admin.id)
        env.db.add(other)
        env.db.add_all([BoardColumn(board=other, title=t, position=i, stage_kind=k) for i, (t, k) in enumerate(PIPELINE_STAGES)])
        env.db.commit()
        with pytest.raises(ImportConflict, match="another board"):
            preview_bid_import(env.db, workbook(ROWS[0]), other.id)


# --- updates -------------------------------------------------------------------------------------

NEWER = {"Atualizado em": "01/04/2026"}


class TestUpdate:
    def _imported(self, env):
        run(env, workbook(ROWS[0]))
        card = card_by_code(env.db, "000101-01")
        # Edits made in the app to fields the import doesn't manage:
        card.deal_value, card.currency, card.contact, card.expected_close_date = 5000.0, "USD", "Contato", date(2026, 9, 1)
        card.salesperson_id = env.admin.id
        env.db.commit()
        return card

    def test_confirmed_update(self, env):
        card = self._imported(env)
        events_before = len(events_of(env.db, card.id))
        changed = workbook(excel_row(**{"Código": "000101-01", "Nome do projeto": "Projeto Novo",
                                        "Próximo passo": "Enviar proposta", "Nível de Confiança": "HIGH (85%)", **NEWER}))
        _, outcome = run(env, changed, [RowDecision(2, confirm_update=True)])
        assert outcome.updated_card_ids == (card.id,) and outcome.created_card_ids == ()
        env.db.refresh(card)
        assert card.title == "Projeto Novo" and card.bid_details.next_step == "Enviar proposta"
        assert card.bid_details.next_step == "Enviar proposta"
        assert card.source_ref.source_updated_at == datetime(2026, 4, 1)
        assert card.source_ref.last_import_batch_id == outcome.batch_id
        # Not managed by the import: untouched.
        assert (card.deal_value, card.currency, card.contact) == (5000.0, "USD", "Contato")
        assert card.expected_close_date == date(2026, 9, 1) and card.salesperson_id == env.admin.id
        assert card.column.title == "Proposal" and len(events_of(env.db, card.id)) == events_before
        batch = env.db.get(ImportBatch, outcome.batch_id)
        assert (batch.created_count, batch.updated_count, batch.review_count) == (0, 1, 0)

    def test_update_without_confirmation_is_not_applied(self, env):
        card = self._imported(env)
        before = commercial(env.path)
        changed = workbook(excel_row(**{"Código": "000101-01", "Próximo passo": "Outro", **NEWER}))
        preview, outcome = run(env, changed)
        assert preview.rows[0].action is Action.UPDATE
        assert outcome.updated_card_ids == () and outcome.review == 1
        env.db.refresh(card)
        assert card.bid_details.next_step == "Ligar"
        after = snapshot(env.path)
        assert after["cards"] == before["cards"] and after["card_bid_details"] == before["card_bid_details"]
        assert env.db.get(ImportBatch, outcome.batch_id).updated_count == 0

    def test_stage_change_needs_its_own_confirmation(self, env):
        card = self._imported(env)  # in Proposal (RFQ)
        moved_source = excel_row(**{"Código": "000101-01", "Estágio": "SALES EVALUATION", **NEWER})
        _, outcome = run(env, workbook(moved_source), [RowDecision(2, confirm_update=True)])
        env.db.refresh(card)
        assert outcome.updated_card_ids == (card.id,) and outcome.moved_card_ids == ()
        assert card.column.title == "Proposal" and card.source_ref.source_stage == "SALES EVALUATION"

    def test_confirmed_move(self, env):
        card = self._imported(env)
        moved_source = excel_row(**{"Código": "000101-01", "Estágio": "SALES EVALUATION", **NEWER})
        _, outcome = run(env, workbook(moved_source), [RowDecision(2, confirm_update=True, confirm_move=True)])
        env.db.refresh(card)
        assert outcome.moved_card_ids == (card.id,) and card.column.title == "F2F Meeting"
        last = events_of(env.db, card.id)[-1]
        assert (last.event_type, last.source, last.to_column_id) == ("moved", "import", env.columns["F2F Meeting"].id)

    def test_unchanged_reimport_is_not_an_update(self, env):
        self._imported(env)
        before = snapshot(env.path)  # every table: no batch, ledger, event or history row either
        _, outcome = run(env, workbook(ROWS[0]))
        assert outcome.updated_card_ids == () and outcome.skipped == 1
        assert (outcome.no_op, outcome.batch_id) == (True, None)
        assert snapshot(env.path) == before


class TestOperator:
    def test_the_technical_operator_is_recorded_apart_from_the_administrator(self, env):
        rr = workbook(ROWS[0])
        preview = preview_bid_import(env.db, rr, env.board.id)
        outcome = apply_bid_import(env.db, rr, preview, (), env.board.id, env.admin, operator="cli:ops")
        batch = env.db.get(ImportBatch, outcome.batch_id)
        assert batch.user_id == env.admin.id and json.loads(batch.mapping_json)["operator"] == "cli:ops"

    def test_no_operator_key_without_one(self, env):
        _, outcome = run(env, workbook(ROWS[0]))
        assert "operator" not in json.loads(env.db.get(ImportBatch, outcome.batch_id).mapping_json)

    def test_a_failed_attempt_records_the_operator_too(self, env, monkeypatch):
        rr = workbook(ROWS[0])
        preview = preview_bid_import(env.db, rr, env.board.id)
        monkeypatch.setattr(service, "_write_rows", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            apply_bid_import(env.db, rr, preview, (), env.board.id, env.admin, operator="cli:ops")
        [(_, _, user_id, *_rest, mapping)] = failed_batches(env.path)
        assert user_id == env.admin.id and json.loads(mapping)["operator"] == "cli:ops"


# --- idempotency and conflicts -------------------------------------------------------------------

class TestIdempotency:
    def test_repeating_the_same_confirmation_creates_nothing(self, env):
        rr = workbook(*ROWS)
        preview = preview_bid_import(env.db, rr, env.board.id, DECISIONS)
        apply_bid_import(env.db, rr, preview, DECISIONS, env.board.id, env.admin)
        after_first = commercial(env.path)
        with pytest.raises(ImportConflict, match="already applied"):
            apply_bid_import(env.db, rr, preview, DECISIONS, env.board.id, env.admin)
        assert commercial(env.path) == after_first
        assert [f[0] for f in failed_batches(env.path)] == ["conflict"]

        # A fresh preview of the same file only finds unchanged rows.
        _, outcome = run(env, rr, (RowDecision(6, skip=True),))
        assert outcome.created_card_ids == () and counts(env.path)["cards"] == len(after_first["cards"])

    def test_unique_source_identity_conflict_leaves_nothing(self, env, monkeypatch):
        run(env, workbook(ROWS[0]))
        before = commercial(env.path)
        # Simulate a stale read: the service doesn't see the existing import, so it proposes
        # creating the same Bid again. The database's unique constraint stops it.
        monkeypatch.setattr(service, "load_existing_imports", lambda *args: [])
        with pytest.raises(ImportConflict, match="same Bid"):
            run(env, workbook(ROWS[0]))
        assert commercial(env.path) == before


# --- failures roll everything back ------------------------------------------------------------------

def _fail_on(model, nth=1):
    calls = {"n": 0}

    def listener(*_args):
        calls["n"] += 1
        if calls["n"] >= nth:
            raise RuntimeError(f"injected failure on {model.__tablename__}")

    return listener


class TestRollback:
    @pytest.mark.parametrize("model, nth", [(CardBidDetails, 1), (CardSourceRef, 1), (CardSourceRef, 2), (Card, 2)])
    def test_failure_while_writing_rows(self, env, model, nth):
        before = commercial(env.path)
        listener = _fail_on(model, nth)
        event.listen(model, "before_insert", listener)
        try:
            with pytest.raises(RuntimeError, match="injected"):
                run(env, workbook(*ROWS), DECISIONS)
        finally:
            event.remove(model, "before_insert", listener)
        assert commercial(env.path) == before  # no card, no details, no ref, no event
        [failed] = failed_batches(env.path)
        assert failed[0] == "unexpected" and failed[1] == "RuntimeError"

    def test_failure_recording_the_baseline(self, env):
        before = commercial(env.path)

        def fail(conn, cursor, statement, *args):
            if statement.lstrip().upper().startswith("INSERT INTO CARD_STAGE_EVENTS"):
                raise RuntimeError("injected failure on card_stage_events")

        event.listen(env.engine, "before_cursor_execute", fail)
        try:
            with pytest.raises(RuntimeError, match="injected"):
                run(env, workbook(*ROWS), DECISIONS)
        finally:
            event.remove(env.engine, "before_cursor_execute", fail)
        assert commercial(env.path) == before

    def test_failure_while_finishing_the_batch(self, env, monkeypatch):
        before = commercial(env.path)
        original = service._write_rows

        def write_then_fail(*args):
            original(*args)
            raise RuntimeError("injected failure after writing the rows")

        monkeypatch.setattr(service, "_write_rows", write_then_fail)
        with pytest.raises(RuntimeError, match="injected"):
            run(env, workbook(*ROWS), DECISIONS)
        assert commercial(env.path) == before

    def test_nothing_is_visible_before_the_commit(self, env, monkeypatch):
        seen = {}
        original_commit = env.db.commit

        def commit():
            seen["before_commit"] = counts(env.path)  # another connection, mid-transaction
            original_commit()

        monkeypatch.setattr(env.db, "commit", commit)
        before = counts(env.path)
        run(env, workbook(*ROWS), DECISIONS)
        assert seen["before_commit"] == before
        assert counts(env.path)["cards"] == before["cards"] + 3


# --- integrity ------------------------------------------------------------------------------------

class TestIntegrity:
    def test_foreign_keys_and_one_to_one_links(self, env):
        run(env, workbook(*ROWS), DECISIONS)
        conn = sqlite3.connect(env.path)
        try:
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute(
                "SELECT COUNT(*) FROM cards c JOIN card_bid_details d ON d.card_id = c.id "
                "JOIN card_source_refs r ON r.card_id = c.id"
            ).fetchone()[0] == 3
        finally:
            conn.close()

    def test_manual_cards_untouched(self, env):
        before = env.db.get(Card, env.manual_id)
        snapshot_before = (before.title, before.column_id, before.position)
        run(env, workbook(*ROWS), DECISIONS)
        after = env.db.get(Card, env.manual_id)
        assert (after.title, after.column_id, after.position) == snapshot_before
        assert after.bid_details is None and after.source_ref is None

    def test_deleting_an_imported_card_keeps_its_history(self, env):
        _, outcome = run(env, workbook(ROWS[0]))
        card = env.db.get(Card, outcome.created_card_ids[0])
        env.db.delete(card)
        env.db.commit()
        assert env.db.query(CardBidDetails).count() == 0 and env.db.query(CardSourceRef).count() == 0
        assert [e.event_type for e in events_of(env.db, outcome.created_card_ids[0])] == ["baseline", "deleted"]


def test_development_database_untouched(env):
    dev = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pm.db")
    if not os.path.exists(dev):
        pytest.skip("no development database here")
    digest = lambda: hashlib.sha256(open(dev, "rb").read()).hexdigest()
    before = digest()
    run(env, workbook(*ROWS), DECISIONS)
    assert digest() == before
    assert os.path.realpath(env.path) != os.path.realpath(dev)


# --- failed attempts (recorded after the rollback, in their own transaction) -----------------------

class TestFailedAttempts:
    def test_success_records_no_failure(self, env):
        run(env, workbook(*ROWS), DECISIONS)
        assert failed_batches(env.path) == []
        assert {b.status for b in env.db.query(ImportBatch)} == {"completed"}

    def test_validation_failure_is_recorded_without_row_content(self, env):
        rr = workbook(*ROWS)
        before = commercial(env.path)
        decisions = (RowDecision(4, confirm_proposed_stage=True),)
        preview = preview_bid_import(env.db, rr, env.board.id, decisions)
        with pytest.raises(ImportRejected):
            apply_bid_import(env.db, rr, preview, decisions, env.board.id, env.admin)
        assert commercial(env.path) == before
        [(kind, reason, user_id, board_id, sha, created, updated, mapping)] = failed_batches(env.path)
        assert (kind, user_id, board_id, sha) == ("validation", env.admin.id, env.board.id, rr.file.sha256)
        assert "rows 4" in reason and (created, updated) == (0, 0)
        assert "Cliente Ficticio" not in reason + mapping and "Projeto Teste" not in reason + mapping

    def test_unexpected_failure_keeps_only_the_exception_type(self, env, monkeypatch):
        def boom(*args):
            raise RuntimeError("message quoting Cliente Ficticio")  # must not be stored

        monkeypatch.setattr(service, "_write_rows", boom)
        with pytest.raises(RuntimeError):
            run(env, workbook(*ROWS), DECISIONS)
        [(kind, reason, *_)] = failed_batches(env.path)
        assert (kind, reason) == ("unexpected", "RuntimeError")

    def test_failed_recording_never_hides_the_error_or_writes_data(self, env, monkeypatch):
        before = commercial(env.path)

        def refuse_failed_rows(_mapper, _connection, target):
            if target.status == "failed":
                raise RuntimeError("cannot record")

        event.listen(ImportBatch, "before_insert", refuse_failed_rows)
        monkeypatch.setattr(service, "_write_rows", lambda *args: (_ for _ in ()).throw(ValueError("x")))
        try:
            with pytest.raises(ValueError):  # the original error, not the recording one
                run(env, workbook(*ROWS), DECISIONS)
        finally:
            event.remove(ImportBatch, "before_insert", refuse_failed_rows)
        assert commercial(env.path) == before and counts(env.path)["import_batches"] == 0

    def test_rejection_before_the_transaction_keeps_the_callers_pending_work(self, env):
        rr = workbook(ROWS[0])
        preview = preview_bid_import(env.db, rr, env.board.id)
        pending = Card(column_id=env.columns["RFI"].id, title="Unsaved", position=0)
        env.db.add(pending)
        with pytest.raises(ImportRejected, match="unsaved"):
            apply_bid_import(env.db, rr, preview, (), env.board.id, env.admin)
        assert pending in env.db.new  # neither flushed nor discarded by the import
        assert [f[0] for f in failed_batches(env.path)] == ["validation"]

    def test_no_record_for_an_unknown_board(self, env):
        rr = workbook(ROWS[0])
        preview = preview_bid_import(env.db, rr, env.board.id)
        with pytest.raises(ImportRejected, match="Board"):
            apply_bid_import(env.db, rr, preview, (), 999, env.admin)
        assert failed_batches(env.path) == []


class TestOtherBoard:
    def _other_board(self, env):
        other = Board(title="Other", user_id=env.admin.id)
        env.db.add(other)
        env.db.add_all([BoardColumn(board=other, title=t, position=i, stage_kind=k)
                        for i, (t, k) in enumerate(PIPELINE_STAGES)])
        env.db.commit()
        return other

    def test_conflict_is_explained_and_nothing_is_written(self, env):
        other = self._other_board(env)
        rr = workbook(ROWS[0], ROWS[1])
        preview_b = preview_bid_import(env.db, rr, other.id)   # before the Bid lands on board A
        run(env, workbook(ROWS[0]))                            # board A now owns 000101-01
        before = commercial(env.path)
        with pytest.raises(ImportConflict) as exc:
            apply_bid_import(env.db, rr, preview_b, (), other.id, env.admin)
        message = str(exc.value)
        assert "000101-01" in message and f"board {env.board.id}" in message and "only be imported into one board" in message
        assert "Cliente Ficticio" not in message
        assert commercial(env.path) == before  # 000102-01 wasn't created on board B either
        assert [f[0] for f in failed_batches(env.path)] == ["conflict"]
        assert env.db.query(CardSourceRef).count() == 1



# --- import history (card_import_changes) ---------------------------------------------------------

def import_changes(db, card_id):
    return db.query(CardImportChange).filter(CardImportChange.card_id == card_id).order_by(CardImportChange.id).all()


class TestImportHistory:
    def _imported(self, env, *rows):
        run(env, workbook(*(rows or (ROWS[0],))))
        return card_by_code(env.db, "000101-01")

    def test_records_only_what_changed_with_old_and_new_values(self, env):
        card = self._imported(env)
        changed = workbook(excel_row(**{"Código": "000101-01", "Nome do projeto": "Projeto Novo",
                                        "Próximo passo": "Enviar proposta", "Nível de Confiança": "HIGH (85%)", **NEWER}))
        _, outcome = run(env, changed, [RowDecision(2, confirm_update=True)])
        [entry] = import_changes(env.db, card.id)
        assert (entry.import_batch_id, entry.user_id) == (outcome.batch_id, env.admin.id)
        assert entry.created_at is not None
        assert json.loads(entry.changes) == {
            "card.title": {"old": "Projeto Teste", "new": "Projeto Novo"},
            "bid_details.next_step": {"old": "Ligar", "new": "Enviar proposta"},
            "source_ref.source_updated_at": {"old": "2026-03-05T00:00:00", "new": "2026-04-01T00:00:00"},
        }

    def test_values_match_the_database_before_and_after(self, env):
        card = self._imported(env)
        card.company = "Changed in the app"  # the import overwrites company: history shows the real old value
        env.db.commit()
        _, outcome = run(env, workbook(excel_row(**{"Código": "000101-01", **NEWER})), [RowDecision(2, confirm_update=True)])
        changes = json.loads(import_changes(env.db, card.id)[0].changes)
        assert changes["card.company"] == {"old": "Changed in the app", "new": "Cliente Ficticio"}
        env.db.refresh(card)
        assert card.company == "Cliente Ficticio"

    def test_no_history_for_new_unchanged_skipped_or_unconfirmed_rows(self, env):
        run(env, workbook(*ROWS), DECISIONS)                       # creations, a skip, pending rows
        run(env, workbook(*ROWS), (RowDecision(6, skip=True),))    # same file again: unchanged
        changed = workbook(excel_row(**{"Código": "000101-01", "Próximo passo": "Outro", **NEWER}))
        run(env, changed)                                          # update proposed but not confirmed
        older = workbook(excel_row(**{"Código": "000102-01", "CAT": "WON", "Estágio": "AWARD GIVEN",
                                      "Próximo passo": "Antigo", "Atualizado em": "01/01/2026"}))
        run(env, older)                                            # older source: ignored
        assert env.db.query(CardImportChange).count() == 0

    def test_update_that_changes_no_managed_field_records_nothing(self, env):
        card = self._imported(env)
        only_budget = workbook(excel_row(**{"Código": "000101-01", "Budget": "5"}))  # not mapped; same dates: confirmed
        _, outcome = run(env, only_budget, [RowDecision(2, confirm_update=True)])
        assert outcome.updated_card_ids == (card.id,)  # new hash stored
        assert import_changes(env.db, card.id) == []

    def test_cpm_change_is_a_managed_change_and_never_touches_ownership(self, env):
        card = self._imported(env)
        only_cpm = workbook(excel_row(**{"Código": "000101-01", "CPM": "Pessoa Z"}))
        run(env, only_cpm, [RowDecision(2, confirm_update=True)])
        env.db.refresh(card)
        assert card.bid_details.cpm == "Pessoa Z" and card.salesperson_id is None
        assert env.db.query(User).count() == 1
        assert set(json.loads(import_changes(env.db, card.id)[0].changes)) == {"bid_details.cpm"}

    def test_one_entry_per_card_per_batch(self, env):
        run(env, workbook(ROWS[0], ROWS[1]))
        changed = workbook(
            excel_row(**{"Código": "000101-01", "Próximo passo": "A", "Planta": "P2", **NEWER}),
            excel_row(**{"Código": "000102-01", "CAT": "WON", "Estágio": "AWARD GIVEN", "Próximo passo": "B", **NEWER}),
        )
        _, outcome = run(env, changed, [RowDecision(2, confirm_update=True), RowDecision(3, confirm_update=True)])
        entries = env.db.query(CardImportChange).filter(CardImportChange.import_batch_id == outcome.batch_id).all()
        assert sorted(e.card_id for e in entries) == sorted(outcome.updated_card_ids)

    def test_a_confirmed_move_is_in_the_stage_history_not_here(self, env):
        card = self._imported(env)
        moved = workbook(excel_row(**{"Código": "000101-01", "Estágio": "SALES EVALUATION", **NEWER}))
        run(env, moved, [RowDecision(2, confirm_update=True, confirm_move=True)])
        changes = json.loads(import_changes(env.db, card.id)[0].changes)
        assert "card.column_id" not in changes
        assert changes["source_ref.source_stage"] == {"old": "RFQ", "new": "SALES EVALUATION"}
        assert events_of(env.db, card.id)[-1].event_type == "moved"

    def test_history_rolls_back_with_the_update(self, env):
        card = self._imported(env)
        before = commercial(env.path)
        listener = _fail_on(CardImportChange)
        event.listen(CardImportChange, "before_insert", listener)
        try:
            with pytest.raises(RuntimeError, match="injected"):
                run(env, workbook(excel_row(**{"Código": "000101-01", "Próximo passo": "X", **NEWER})),
                    [RowDecision(2, confirm_update=True)])
        finally:
            event.remove(CardImportChange, "before_insert", listener)
        assert commercial(env.path) == before
        env.db.refresh(card)
        assert card.bid_details.next_step == "Ligar" and import_changes(env.db, card.id) == []

    def test_member_history_and_m_badge_are_unaffected(self, env):
        run(env, workbook(ROWS[0], ROWS[1]))
        member = User(username="member", password_hash="x", role="member")
        env.db.add(member)
        edited_by_member = card_by_code(env.db, "000101-01")
        edited_by_member.salesperson_id = member.id
        env.db.flush()
        apply_card_updates(env.db, edited_by_member, {"details": "member note"}, member)
        env.db.commit()
        only_imported = card_by_code(env.db, "000102-01")
        member_history_before = snapshot(env.path, ("card_edit_history",))

        changed = workbook(
            excel_row(**{"Código": "000101-01", "Próximo passo": "A", **NEWER}),
            excel_row(**{"Código": "000102-01", "CAT": "WON", "Estágio": "AWARD GIVEN", "Próximo passo": "B", **NEWER}),
        )
        run(env, changed, [RowDecision(2, confirm_update=True), RowDecision(3, confirm_update=True)])
        assert snapshot(env.path, ("card_edit_history",)) == member_history_before
        env.db.refresh(edited_by_member)
        env.db.refresh(only_imported)
        assert edited_by_member.has_member_edits and len(edited_by_member.edit_history) == 1
        assert not only_imported.has_member_edits and only_imported.edit_history == []
        assert len(only_imported.import_changes) == 1  # recorded, but never as a member edit

    def test_referential_integrity(self, env):
        card = self._imported(env)
        run(env, workbook(excel_row(**{"Código": "000101-01", "Próximo passo": "X", **NEWER})),
            [RowDecision(2, confirm_update=True)])
        conn = sqlite3.connect(env.path)
        try:
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            conn.close()
        env.db.delete(card)
        env.db.commit()
        assert env.db.query(CardImportChange).count() == 0
