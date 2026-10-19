"""Part 23, step 2: the import writes its ledger (created / updated / observed items,
canonical snapshots) in the import's own transaction. Synthetic data only."""
import json
import sqlite3

import pytest

import bid_import_service as service
from bid_import_preview import RowDecision
from models import (
    Card, CardBidDetails, CardEditHistory, CardImportChange, CardStageEvent, ImportBatch, ImportBatchItem,
    ImportBatchLedger,
)
from test_bid_excel_reader import row as excel_row
from test_bid_import_service import (  # noqa: F401 - env is a fixture
    NEWER, ROWS, card_by_code, commercial, counts, env, events_of, run, workbook,
)


def items_of(db, batch_id):
    db.expire_all()
    ledger = db.query(ImportBatchLedger).filter_by(import_batch_id=batch_id).one()
    return {item.card_id: item for item in ledger.items}


def import_first(env):
    _, outcome = run(env, workbook(ROWS[0]))
    return card_by_code(env.db, "000101-01"), outcome


class TestCreated:
    def test_created_items_and_snapshots(self, env):
        _, outcome = run(env, workbook(ROWS[0], ROWS[1]))
        items = items_of(env.db, outcome.batch_id)
        assert set(items) == set(outcome.created_card_ids)
        for card_id, item in items.items():
            card = env.db.get(Card, card_id)
            assert (item.action, item.observation, item.before_json) == ("created", None, None)
            assert item.board_id == env.board.id and item.bid_details_created is True
            assert item.source_key == card.source_ref.source_key
            assert item.stage_event_max_id_after == events_of(env.db, card_id)[-1].id
            after = json.loads(item.after_json)
            assert after["card"]["title"] == card.title and after["card"]["column_id"] == card.column_id
            assert after["bid_details"]["present"] is True and after["bid_details"]["id"] == card.bid_details.id
            assert after["source_ref"]["last_import_batch_id"] == outcome.batch_id
            assert after["source_ref"]["source_row_hash"] == card.source_ref.source_row_hash

    def test_snapshot_is_canonical_and_leaves_out_volatile_columns(self, env):
        _, outcome = import_first(env)
        [item] = items_of(env.db, outcome.batch_id).values()
        after = json.loads(item.after_json)
        assert item.after_json == json.dumps(after, ensure_ascii=False, sort_keys=True)
        assert not {"id", "position", "created_at", "updated_at"} & set(after["card"])
        assert not {"created_at", "updated_at"} & (set(after["bid_details"]) | set(after["source_ref"]))
        assert isinstance(after["source_ref"]["source_updated_at"], str)  # ISO date


class TestUpdated:
    def test_confirmed_update_before_and_after(self, env):
        card, first = import_first(env)
        before_state = json.loads(items_of(env.db, first.batch_id)[card.id].after_json)
        changed = workbook(excel_row(**{"Código": "000101-01", "Nome do projeto": "Projeto Novo",
                                        "Próximo passo": "Enviar", **NEWER}))
        _, outcome = run(env, changed, [RowDecision(2, confirm_update=True)])
        item = items_of(env.db, outcome.batch_id)[card.id]
        assert (item.action, item.bid_details_created) == ("updated", False)
        before, after = json.loads(item.before_json), json.loads(item.after_json)
        assert before == before_state  # captured before any write: the state the first batch left
        assert before["card"]["title"] != "Projeto Novo" and after["card"]["title"] == "Projeto Novo"
        assert after["bid_details"]["next_step"] == "Enviar" and before["bid_details"]["next_step"] != "Enviar"
        assert before["source_ref"]["last_import_batch_id"] == first.batch_id
        assert after["source_ref"]["last_import_batch_id"] == outcome.batch_id
        assert before["card"]["column_id"] == after["card"]["column_id"]
        assert item.stage_event_max_id_after == events_of(env.db, card.id)[-1].id

    def test_confirmed_move_is_in_the_snapshot_and_event_id(self, env):
        card, _ = import_first(env)
        moved = workbook(excel_row(**{"Código": "000101-01", "Estágio": "SALES EVALUATION", **NEWER}))
        _, outcome = run(env, moved, [RowDecision(2, confirm_update=True, confirm_move=True)])
        item = items_of(env.db, outcome.batch_id)[card.id]
        before, after = json.loads(item.before_json), json.loads(item.after_json)
        assert before["card"]["column_id"] == env.columns["Proposal"].id
        assert after["card"]["column_id"] == env.columns["F2F Meeting"].id
        last = events_of(env.db, card.id)[-1]
        assert last.event_type == "moved" and item.stage_event_max_id_after == last.id

    def test_update_that_creates_the_missing_bid_details_row(self, env):
        card, _ = import_first(env)
        env.db.delete(card.bid_details)
        env.db.commit()
        env.db.expire_all()
        changed = workbook(excel_row(**{"Código": "000101-01", "Próximo passo": "Novo", **NEWER}))
        _, outcome = run(env, changed, [RowDecision(2, confirm_update=True)])
        item = items_of(env.db, outcome.batch_id)[card.id]
        assert item.bid_details_created is True
        assert json.loads(item.before_json)["bid_details"] == {"present": False}
        after = json.loads(item.after_json)["bid_details"]
        assert after["present"] is True and after["id"] == env.db.get(Card, card.id).bid_details.id


class TestObserved:
    def test_unchanged(self, env):
        card, _ = import_first(env)
        _, outcome = run(env, workbook(ROWS[0], ROWS[1]))  # a batch with nothing to write isn't recorded
        item = items_of(env.db, outcome.batch_id)[card.id]
        assert (item.card_id, item.action, item.observation) == (card.id, "observed", "unchanged")
        assert (item.before_json, item.after_json, item.stage_event_max_id_after) == (None, None, None)
        assert item.bid_details_created is False

    def test_older_source(self, env):
        card, _ = import_first(env)
        older = workbook(excel_row(**{"Código": "000101-01", "Próximo passo": "X", "Atualizado em": "01/02/2026"}), ROWS[1])
        _, outcome = run(env, older)
        item = items_of(env.db, outcome.batch_id)[card.id]
        assert (item.card_id, item.observation) == (card.id, "older_source")

    def test_skipped_by_decision_finds_the_existing_card(self, env):
        card, _ = import_first(env)
        changed = workbook(excel_row(**{"Código": "000101-01", "Próximo passo": "X", **NEWER}), ROWS[1])
        _, outcome = run(env, changed, [RowDecision(2, skip=True)])
        item = items_of(env.db, outcome.batch_id)[card.id]
        assert (item.card_id, item.action, item.observation) == (card.id, "observed", "skipped_by_decision")

    def test_update_not_confirmed(self, env):
        card, _ = import_first(env)
        changed = workbook(excel_row(**{"Código": "000101-01", "Próximo passo": "X", **NEWER}))
        _, outcome = run(env, changed)
        [item] = items_of(env.db, outcome.batch_id).values()
        assert (item.card_id, item.observation) == (card.id, "update_not_confirmed")

    def test_rows_without_an_existing_card_are_not_recorded(self, env):
        skipped_new = excel_row(**{"Código": "000199-01"})
        broken = excel_row(**{"Código": "000198-01", "Nome do projeto": None})
        _, outcome = run(env, workbook(ROWS[0], skipped_new, broken), [RowDecision(3, skip=True)])
        items = items_of(env.db, outcome.batch_id)
        assert [i.action for i in items.values()] == ["created"]

    def test_mixed_batch_one_item_per_card(self, env):
        run(env, workbook(ROWS[0], ROWS[1]))
        rows = workbook(ROWS[0], excel_row(**{"Código": "000102-01", "CAT": "WON", "Estágio": "AWARD GIVEN",
                                              "Próximo passo": "Y", **NEWER}), ROWS[3])
        _, outcome = run(env, rows, [RowDecision(3, confirm_update=True)])
        items = items_of(env.db, outcome.batch_id)
        by_action = sorted((i.action, i.observation) for i in items.values())
        assert ("observed", "unchanged") in by_action and ("updated", None) in by_action
        assert len(items) == len({i.card_id for i in items.values()})


class TestBatchLevel:
    def test_batch_without_commercial_changes_still_has_a_ledger(self, env):
        import_first(env)
        batch_count = env.db.query(ImportBatchLedger).count()
        _, outcome = run(env, workbook(ROWS[0], ROWS[2]))  # ROWS[2] needs review: reviewed, not written
        assert env.db.query(ImportBatchLedger).count() == batch_count + 1
        assert outcome.created_card_ids == () and outcome.updated_card_ids == ()

    def test_batch_with_zero_items_has_an_empty_ledger(self, env):
        _, outcome = run(env, workbook(ROWS[2]))  # needs review, no existing card
        env.db.expire_all()
        ledger = env.db.query(ImportBatchLedger).filter_by(import_batch_id=outcome.batch_id).one()
        assert ledger.items == [] and ledger.ledger_version == 1
        assert (ledger.reverted_at, ledger.revert_outcome) == (None, None)

    def test_import_behaviour_is_unchanged(self, env):
        card, first = import_first(env)
        changed = workbook(excel_row(**{"Código": "000101-01", "Nome do projeto": "Projeto Novo", **NEWER}))
        _, outcome = run(env, changed, [RowDecision(2, confirm_update=True)])
        batch = env.db.get(ImportBatch, outcome.batch_id)
        assert (batch.created_count, batch.updated_count, batch.status) == (0, 1, "completed")
        assert env.db.query(CardImportChange).filter_by(card_id=card.id).count() == 1
        assert env.db.query(CardEditHistory).count() == 0
        assert env.db.query(ImportBatchLedger).count() == 2

    def test_ledger_items_survive_card_deletion(self, env):
        card, outcome = import_first(env)
        card_id = card.id
        env.db.delete(card)
        env.db.commit()
        assert env.db.query(ImportBatchItem).filter_by(card_id=card_id).count() == 1


class TestAtomicity:
    def _no_ledger(self, env):
        c = counts(env.path)
        conn = sqlite3.connect(env.path)
        try:
            return [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("import_batch_ledgers", "import_batch_items")], c
        finally:
            conn.close()

    def test_failure_after_the_ledger_is_written_leaves_no_ledger(self, env, monkeypatch):
        before = commercial(env.path)
        original = service._write_ledger

        def write_then_fail(*args):
            original(*args)
            raise RuntimeError("injected failure right after the ledger")

        monkeypatch.setattr(service, "_write_ledger", write_then_fail)
        with pytest.raises(RuntimeError, match="injected"):
            run(env, workbook(*ROWS))
        assert commercial(env.path) == before
        assert self._no_ledger(env)[0] == [0, 0]

    def test_ledger_write_failure_rolls_back_the_import(self, env, monkeypatch):
        before = commercial(env.path)

        def boom(*args):
            raise RuntimeError("injected ledger failure")

        monkeypatch.setattr(service, "_write_ledger", boom)
        with pytest.raises(RuntimeError, match="injected"):
            run(env, workbook(ROWS[0]))
        assert commercial(env.path) == before and self._no_ledger(env)[0] == [0, 0]

    def test_commit_failure_leaves_no_ledger(self, env, monkeypatch):
        def fail_commit():
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(env.db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected"):
            run(env, workbook(ROWS[0]))
        assert self._no_ledger(env)[0] == [0, 0]

    def test_rejected_and_conflicting_imports_have_no_ledger(self, env):
        rr = workbook(ROWS[0])
        preview = service.preview_bid_import(env.db, rr, env.board.id)
        service.apply_bid_import(env.db, rr, preview, (), env.board.id, env.admin)
        with pytest.raises(service.ImportConflict):
            service.apply_bid_import(env.db, rr, preview, (), env.board.id, env.admin)  # already applied
        assert self._no_ledger(env)[0][0] == 1  # only the first, successful one
        failed = env.db.query(ImportBatch).filter_by(status="failed").all()
        assert failed and all(b.ledger is None for b in failed)
