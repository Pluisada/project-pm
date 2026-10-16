"""Part 23, step 3: bid_import_revert - the revert planner and executor.

Temp SQLite files with the full schema (the `env` fixture of test_bid_import_service);
the lock tests use two independent connections on that file, never :memory:.
Synthetic workbooks only."""
import json
import sqlite3
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import bid_import_revert as revert_module
from bid_import_ledger import card_snapshot
from bid_import_preview import Action, DecisionNeeded, RowDecision
from bid_import_revert import (
    RevertBusy, RevertOutcomeUnconfirmed, RevertRefused, RevertVerificationFailed, build_revert_plan, execute_revert,
    is_lock_error, revert_preview,
)
from models import (
    Card, CardAction, CardBidDetails, CardEditHistory, ImportBatch, ImportBatchItem, ImportBatchLedger,
    UndoEntry,
)
from stage_history import SOURCE_API, set_stage_context
from test_bid_excel_reader import row as excel_row
from test_bid_import_service import (  # noqa: F401 - env is a fixture
    NEWER, ROWS, card_by_code, env, events_of, run, workbook,
)
from undo import UndoError, apply_undo, record_undo, undo_update

CODE = "000101-01"


# --- helpers --------------------------------------------------------------------------------------

def source_row(code=CODE, **overrides):
    return excel_row(**{"Código": code, **overrides})


def import_rows(env, *rows, decisions=()):
    _, outcome = run(env, workbook(*rows), decisions)
    return outcome


def import_first(env, code=CODE):
    outcome = import_rows(env, source_row(code))
    return card_by_code(env.db, code), outcome.batch_id


def update_batch(env, *, move=False, **overrides):
    """A second batch that updates the card of CODE (title / next step), optionally moving it."""
    overrides = {"Nome do projeto": "Projeto Novo", "Próximo passo": "Enviar", **NEWER, **overrides}
    decision = RowDecision(2, confirm_update=True, confirm_move=move)
    return import_rows(env, source_row(**overrides), decisions=[decision]).batch_id


def plan_of(env, batch_id):
    return build_revert_plan(env.db, batch_id)


def do_revert(env, batch_id, fingerprint=None):
    if fingerprint is None:
        fingerprint = plan_of(env, batch_id).plan_fingerprint
    return execute_revert(env.db, batch_id, env.admin, fingerprint)


def blocker_codes(plan):
    return sorted(b.code for b in plan.blockers)


def refused(env, batch_id, code, fingerprint=None):
    with pytest.raises(RevertRefused) as raised:
        do_revert(env, batch_id, fingerprint)
    assert raised.value.code == code
    return raised.value


def dump(path):
    """Every table, every row: nothing at all may differ after a refused or failed revert."""
    conn = sqlite3.connect(path)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: conn.execute(f"SELECT * FROM {n} ORDER BY rowid").fetchall() for n in names}
    finally:
        conn.close()


def ledger_of(env, batch_id):
    env.db.expire_all()
    return env.db.query(ImportBatchLedger).filter_by(import_batch_id=batch_id).one()


def edit(env, card, **fields):
    for name, value in fields.items():
        setattr(card, name, value)
    env.db.commit()


def edit_details(env, card, **fields):
    for name, value in fields.items():
        setattr(card.bid_details, name, value)
    env.db.commit()


def comparable(card):
    return card_snapshot(card)


# --- created cards --------------------------------------------------------------------------------

class TestCreated:
    def test_revert_deletes_what_the_batch_created(self, env):
        outcome = import_rows(env, *ROWS[:2])
        ids = outcome.created_card_ids
        plan = plan_of(env, outcome.batch_id)
        assert plan.status == "ready" and plan.counts["cards_to_delete"] == 2 and not plan.no_commercial_changes
        assert {item.classification for item in plan.items} == {"delete"}

        result = do_revert(env, outcome.batch_id, plan.plan_fingerprint)
        assert (result.outcome, result.cards_deleted) == ("reverted", 2)
        env.db.expire_all()
        for card_id in ids:
            assert env.db.get(Card, card_id) is None
        assert env.db.get(Card, env.manual_id) is not None  # not imported: untouched
        assert env.db.query(CardBidDetails).count() == 0
        conn = sqlite3.connect(env.path)
        assert conn.execute("SELECT COUNT(*) FROM card_source_refs").fetchone()[0] == 0
        conn.close()

    def test_compensating_stage_events(self, env):
        card, batch_id = import_first(env)
        do_revert(env, batch_id)
        last = events_of(env.db, card.id)[-1]
        assert (last.event_type, last.source, last.user_id) == ("deleted", "import_revert", env.admin.id)
        assert last.to_column_id is None and last.from_column_id == env.columns["Proposal"].id

    def test_ledger_survives_the_deleted_card(self, env):
        card, batch_id = import_first(env)
        do_revert(env, batch_id)
        ledger = ledger_of(env, batch_id)
        assert [i.card_id for i in ledger.items] == [card.id]
        assert ledger.reverted_at is not None and ledger.reverted_by_user_id == env.admin.id

    @pytest.mark.parametrize("change", ["deal_value", "title", "contact"])
    def test_later_field_change_blocks(self, env, change):
        card, batch_id = import_first(env)
        edit(env, card, **{change: 99.0 if change == "deal_value" else "Changed"})
        before = dump(env.path)
        plan = plan_of(env, batch_id)
        assert plan.status == "blocked" and blocker_codes(plan) == ["field_modified_since"]
        assert plan.blockers[0].field == f"card.{change}"
        refused(env, batch_id, "revert_blocked", plan.plan_fingerprint)
        assert dump(env.path) == before

    def test_member_edit_history_blocks(self, env):
        card, batch_id = import_first(env)
        env.db.add(CardEditHistory(card_id=card.id, user_id=env.admin.id, changes="{}"))
        env.db.commit()
        assert blocker_codes(plan_of(env, batch_id)) == ["edited_since"]

    def test_card_action_blocks(self, env):
        card, batch_id = import_first(env)
        env.db.add(CardAction(board_id=env.board.id, action_type="update", card_id=card.id))
        env.db.commit()
        assert blocker_codes(plan_of(env, batch_id)) == ["activity_since"]

    def test_move_blocks_even_if_moved_back(self, env):
        card, batch_id = import_first(env)
        origin = card.column_id
        set_stage_context(env.db, env.admin, SOURCE_API)
        edit(env, card, column_id=env.columns["F2F Meeting"].id)
        assert {"moved_since", "stage_activity_since"} <= set(blocker_codes(plan_of(env, batch_id)))
        edit(env, card, column_id=origin)  # back to where it was: still later commercial activity
        plan = plan_of(env, batch_id)
        assert blocker_codes(plan) == ["stage_activity_since"]

    def test_bid_details_modified_after_import(self, env):
        card, batch_id = import_first(env)
        edit_details(env, card, next_step="Changed later")
        assert blocker_codes(plan_of(env, batch_id)) == ["bid_details_modified_after_import"]

    def test_bid_details_missing_while_card_exists(self, env):
        card, batch_id = import_first(env)
        env.db.delete(card.bid_details)
        env.db.commit()
        assert blocker_codes(plan_of(env, batch_id)) == ["bid_details_missing"]


# --- updated cards --------------------------------------------------------------------------------

class TestUpdated:
    def setup_card(self, env):
        card, first = import_first(env)
        # Edits made in the app to fields the import does not manage; they must survive a revert.
        edit(env, card, deal_value=5000.0, currency="USD", contact="Contato", expected_close_date=date(2026, 9, 1))
        state = comparable(card)
        return card, first, state

    def test_restores_changed_fields_only(self, env):
        card, first, state = self.setup_card(env)
        second = update_batch(env)
        edit(env, card, deal_value=7000.0)  # a later edit of a field the batch did not change
        plan = plan_of(env, second)
        assert plan.status == "ready" and plan.counts["cards_to_restore"] == 1 and plan.counts["cards_to_delete"] == 0

        result = do_revert(env, second, plan.plan_fingerprint)
        assert (result.outcome, result.cards_restored) == ("reverted", 1)
        env.db.expire_all()
        reverted = comparable(env.db.get(Card, card.id))
        assert reverted["card"]["title"] == state["card"]["title"]
        assert reverted["bid_details"] == state["bid_details"]
        assert reverted["source_ref"] == state["source_ref"]  # last_import_batch_id and source_row_hash included
        assert reverted["source_ref"]["last_import_batch_id"] == first
        assert env.db.get(Card, card.id).deal_value == 7000.0  # untouched by the batch: kept as is

    def test_conflict_blocks_the_whole_revert(self, env):
        card, _, _ = self.setup_card(env)
        second = update_batch(env)
        edit(env, card, title="Edited by hand")  # neither the old nor the new value
        before = dump(env.path)
        plan = plan_of(env, second)
        assert plan.status == "blocked"
        [blocker] = plan.blockers
        assert (blocker.code, blocker.field, blocker.imported, blocker.current) == (
            "field_modified_since", "card.title", "Projeto Novo", "Edited by hand")
        refused(env, second, "revert_blocked", plan.plan_fingerprint)
        assert dump(env.path) == before  # next_step was not restored either

    def test_field_already_at_old_value_is_not_written(self, env):
        card, _, state = self.setup_card(env)
        second = update_batch(env)
        edit(env, card, title=state["card"]["title"])  # someone already put the old title back
        plan = plan_of(env, second)
        assert plan.status == "ready"
        [restore] = [op for op in plan.operations if op.kind == "restore_fields"]
        assert "card.title" not in restore.data["fields"] and "bid_details.next_step" in restore.data["fields"]
        do_revert(env, second, plan.plan_fingerprint)
        env.db.expire_all()
        assert env.db.get(Card, card.id).bid_details.next_step == state["bid_details"]["next_step"]

    def test_everything_already_reverted_is_a_no_op(self, env):
        card, first, _ = self.setup_card(env)
        second = update_batch(env)
        [restore] = [op for op in plan_of(env, second).operations if op.kind == "restore_fields"]
        targets = {"card": card, "bid_details": card.bid_details, "source_ref": card.source_ref}
        for key, value in restore.data["fields"].items():  # put every changed field back by hand
            section, name = key.split(".", 1)
            setattr(targets[section], name, revert_module._coerce(targets[section], name, value))
        env.db.commit()
        plan = plan_of(env, second)
        assert plan.status == "ready" and [i.classification for i in plan.items] == ["already_reverted"]
        assert plan.no_commercial_changes
        assert do_revert(env, second, plan.plan_fingerprint).outcome == "no_commercial_changes"

    def test_confirmed_move_goes_back_to_the_end_of_the_origin_column(self, env):
        card, first, _ = self.setup_card(env)
        origin = card.column_id  # Proposal
        second = update_batch(env, move=True, Estágio="SALES EVALUATION")
        env.db.expire_all()
        assert env.db.get(Card, card.id).column.title == "F2F Meeting"
        other = Card(column_id=origin, title="Added later to the origin column", position=40)
        env.db.add(other)
        env.db.commit()

        plan = plan_of(env, second)
        assert plan.counts["moves_to_revert"] == 1
        result = do_revert(env, second, plan.plan_fingerprint)
        assert result.moves_reverted == 1
        env.db.expire_all()
        back = env.db.get(Card, card.id)
        assert back.column_id == origin and back.position > env.db.get(Card, other.id).position
        last = events_of(env.db, card.id)[-1]
        assert (last.event_type, last.source, last.to_column_id) == ("moved", "import_revert", origin)

    def test_moved_since_blocks(self, env):
        card, _, _ = self.setup_card(env)
        second = update_batch(env, move=True, Estágio="SALES EVALUATION")
        edit(env, card, column_id=env.columns["RFI"].id)
        plan = plan_of(env, second)
        assert blocker_codes(plan) == ["moved_since"]

    def test_card_moved_by_hand_back_to_origin_is_already_reverted(self, env):
        card, _, _ = self.setup_card(env)
        second = update_batch(env, move=True, Estágio="SALES EVALUATION")
        origin = env.columns["Proposal"].id
        edit(env, card, column_id=origin)
        plan = plan_of(env, second)
        assert plan.status == "ready" and plan.counts["moves_to_revert"] == 0

    def test_details_created_by_the_batch_are_deleted(self, env):
        card, first, _ = self.setup_card(env)
        env.db.delete(card.bid_details)  # a card whose details row did not exist before the update
        env.db.commit()
        second = update_batch(env)
        item = ledger_of(env, second).items[0]
        assert item.bid_details_created is True
        plan = plan_of(env, second)
        assert plan.counts["bid_details_to_delete"] == 1
        result = do_revert(env, second, plan.plan_fingerprint)
        assert result.bid_details_deleted == 1
        env.db.expire_all()
        assert env.db.get(Card, card.id).bid_details is None
        assert env.db.get(Card, card.id).title != "Projeto Novo"

    def test_details_created_by_the_batch_but_modified_later_block(self, env):
        card, first, _ = self.setup_card(env)
        env.db.delete(card.bid_details)
        env.db.commit()
        second = update_batch(env)
        edit_details(env, card, plant="Changed later")
        assert blocker_codes(plan_of(env, second)) == ["bid_details_modified_after_import"]

    def test_preexisting_details_keep_fields_the_batch_did_not_change(self, env):
        card, first, _ = self.setup_card(env)
        second = update_batch(env)
        edit_details(env, card, plant="Plant edited later")  # `plant` was not changed by the batch
        do_revert(env, second)
        env.db.expire_all()
        details = env.db.get(Card, card.id).bid_details
        assert details.plant == "Plant edited later" and details.next_step == "Ligar"

    def test_details_missing_for_a_preexisting_row_blocks(self, env):
        card, first, _ = self.setup_card(env)
        second = update_batch(env)
        env.db.delete(card.bid_details)
        env.db.commit()
        assert blocker_codes(plan_of(env, second)) == ["bid_details_missing"]

    def test_reimport_after_revert_proposes_the_same_update(self, env):
        card, first, _ = self.setup_card(env)
        second = update_batch(env)
        do_revert(env, second)
        source = workbook(source_row(**{"Nome do projeto": "Projeto Novo", "Próximo passo": "Enviar", **NEWER}))
        from bid_import_service import preview_bid_import
        preview = preview_bid_import(env.db, source, env.board.id)
        [row] = preview.rows
        assert row.action is Action.UPDATE and DecisionNeeded.CONFIRM_UPDATE in row.required_decisions


# --- observed items and empty batches ------------------------------------------------------------

class TestObservedAndEmpty:
    def test_unchanged_reimport_records_no_batch_and_leaves_the_first_revertible(self, env):
        card, first = import_first(env)
        before = dump(env.path)
        outcome = import_rows(env, source_row())
        assert outcome.no_op and outcome.batch_id is None
        assert dump(env.path) == before  # not even a batch row
        assert plan_of(env, first).status == "ready"

    def test_unconfirmed_update_batch_reverts_as_a_no_op(self, env):
        card, first = import_first(env)
        second = import_rows(env, source_row(**{"Próximo passo": "Other", **NEWER})).batch_id  # update proposed, not confirmed -> observed
        item = ledger_of(env, second).items[0]
        assert (item.action, item.observation) == ("observed", "update_not_confirmed")
        before = dump(env.path)
        plan = plan_of(env, second)
        assert plan.status == "ready" and plan.no_commercial_changes and plan.counts["observed"] == 1
        result = do_revert(env, second, plan.plan_fingerprint)
        assert (result.outcome, result.no_commercial_changes) == ("no_commercial_changes", True)
        after = dump(env.path)
        assert {t: rows for t, rows in after.items() if t != "import_batch_ledgers"} == \
            {t: rows for t, rows in before.items() if t != "import_batch_ledgers"}

    def test_batch_with_no_items(self, env):
        import_first(env)
        outcome = import_rows(env, ROWS[2])  # a review row with no card behind it
        assert ledger_of(env, outcome.batch_id).items == []
        plan = plan_of(env, outcome.batch_id)
        assert plan.status == "ready" and plan.items == () and plan.no_commercial_changes
        assert do_revert(env, outcome.batch_id, plan.plan_fingerprint).outcome == "no_commercial_changes"


# --- dependencies between batches -----------------------------------------------------------------

class TestDependencies:
    def test_later_observed_batch_blocks_the_earlier_one_until_reverted(self, env):
        card, first = import_first(env)
        second = import_rows(env, source_row(**{"Próximo passo": "Other", **NEWER})).batch_id  # observed (update not confirmed)
        plan = plan_of(env, first)
        assert plan.status == "blocked" and blocker_codes(plan) == ["later_batch_decision_on_card"]
        assert plan.dependent_batches == ({"batch_id": second, "card_ids": [card.id]},)
        refused(env, first, "revert_blocked", plan.plan_fingerprint)

        do_revert(env, second)
        assert plan_of(env, first).status == "ready"
        do_revert(env, first)
        env.db.expire_all()
        assert env.db.get(Card, card.id) is None

    def test_update_not_confirmed_is_a_dependency(self, env):
        card, first = import_first(env)
        second = import_rows(env, source_row(**{"Próximo passo": "Other", **NEWER})).batch_id  # update, not confirmed
        assert ledger_of(env, second).items[0].observation == "update_not_confirmed"
        assert blocker_codes(plan_of(env, first)) == ["later_batch_decision_on_card"]

    def test_later_updating_batch_blocks_and_reverting_it_frees_the_first(self, env):
        card, first = import_first(env)
        second = update_batch(env)
        assert "later_batch_decision_on_card" in blocker_codes(plan_of(env, first))
        do_revert(env, second)
        assert plan_of(env, first).status == "ready"
        do_revert(env, first)
        env.db.expire_all()
        assert env.db.get(Card, card.id) is None

    def test_reverted_move_of_a_later_batch_does_not_leave_activity_behind(self, env):
        card, first = import_first(env)
        second = update_batch(env, move=True, Estágio="SALES EVALUATION")
        do_revert(env, second)  # leaves an import event and its import_revert compensation on the card
        assert plan_of(env, first).status == "ready"
        # ... while a manual move still counts.
        set_stage_context(env.db, env.admin, SOURCE_API)
        edit(env, card, column_id=env.columns["RFI"].id)
        edit(env, card, column_id=env.columns["Proposal"].id)
        assert blocker_codes(plan_of(env, first)) == ["stage_activity_since"]

    def test_earlier_batch_does_not_block_a_later_one(self, env):
        card, first = import_first(env)
        second = update_batch(env)
        assert plan_of(env, second).status == "ready"  # the first batch is still applied

    def test_card_proven_deleted_is_not_a_dependency(self, env):
        card, first = import_first(env)
        second = import_rows(env, source_row()).batch_id
        set_stage_context(env.db, env.admin, SOURCE_API)
        env.db.delete(card)  # the stage listener records the "deleted" event
        env.db.commit()
        plan = plan_of(env, first)
        assert plan.status == "ready" and [i.classification for i in plan.items] == ["already_deleted"]
        assert plan.dependent_batches == ()
        result = do_revert(env, first, plan.plan_fingerprint)
        assert (result.already_deleted, result.cards_deleted) == (1, 0) and result.no_commercial_changes

    def test_missing_without_a_deleted_event_blocks(self, env):
        card, first = import_first(env)
        card_id = card.id
        env.db.close()
        conn = sqlite3.connect(env.path)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))  # raw delete: no stage event
        conn.commit()
        conn.close()
        plan = plan_of(env, first)
        assert plan.status == "blocked" and blocker_codes(plan) == ["missing_without_trace"]

    def test_updated_card_deleted_later_is_already_deleted(self, env):
        card, first = import_first(env)
        second = update_batch(env)
        set_stage_context(env.db, env.admin, SOURCE_API)
        env.db.delete(card)
        env.db.commit()
        plan = plan_of(env, second)
        assert plan.status == "ready" and [i.classification for i in plan.items] == ["already_deleted"]

    def test_failed_or_pending_later_batch_does_not_block(self, env):
        card, first = import_first(env)
        for status in ("failed", "pending"):
            later = ImportBatch(board_id=env.board.id, filename="x.xlsx", file_sha256="0" * 64, status=status)
            later.ledger = ImportBatchLedger(items=[ImportBatchItem(
                card_id=card.id, board_id=env.board.id, source_key="k", action="observed", observation="unchanged")])
            env.db.add(later)
        env.db.commit()
        assert plan_of(env, first).status == "ready"


# --- eligibility ----------------------------------------------------------------------------------

class TestEligibility:
    def test_unknown_batch(self, env):
        with pytest.raises(RevertRefused) as raised:
            plan_of(env, 9999)
        assert raised.value.code == "batch_not_found"
        refused(env, 9999, "batch_not_found", fingerprint="x")

    def test_batch_without_a_ledger_is_not_revertible(self, env):
        card, first = import_first(env)
        env.db.delete(ledger_of(env, first))
        env.db.commit()
        plan = plan_of(env, first)
        assert (plan.status, plan.reason) == ("not_revertible", "no_ledger")
        before = dump(env.path)
        refused(env, first, "not_revertible", plan.plan_fingerprint)
        assert dump(env.path) == before

    def test_failed_batch_is_not_revertible(self, env):
        failed = ImportBatch(board_id=env.board.id, filename="x.xlsx", file_sha256="0" * 64, status="failed")
        env.db.add(failed)
        env.db.commit()
        assert plan_of(env, failed.id).status == "not_revertible"

    def test_second_revert_is_already_reverted(self, env):
        card, first = import_first(env)
        plan = plan_of(env, first)
        do_revert(env, first, plan.plan_fingerprint)
        again = plan_of(env, first)
        assert again.status == "already_reverted"
        before = dump(env.path)
        refused(env, first, "already_reverted", plan.plan_fingerprint)
        assert dump(env.path) == before


# --- fingerprint ----------------------------------------------------------------------------------

class TestFingerprint:
    def test_is_deterministic_and_ignores_unrelated_changes(self, env):
        card, first = import_first(env)
        one = plan_of(env, first).plan_fingerprint
        assert plan_of(env, first).plan_fingerprint == one and len(one) == 64
        edit(env, env.db.get(Card, env.manual_id), title="Unrelated edit")
        assert plan_of(env, first).plan_fingerprint == one

    def test_stale_after_a_change_to_the_card(self, env):
        card, first = import_first(env)
        stale = plan_of(env, first).plan_fingerprint
        edit(env, card, details="Edited between preview and confirmation")  # not a blocker, but part of the state
        before = dump(env.path)
        error = refused(env, first, "plan_changed", stale)
        assert error.plan is not None and error.plan.plan_fingerprint != stale
        assert dump(env.path) == before
        assert ledger_of(env, first).reverted_at is None

    def test_stale_after_a_later_batch(self, env):
        card, first = import_first(env)
        stale = plan_of(env, first).plan_fingerprint
        import_rows(env, source_row(**{"Próximo passo": "Other", **NEWER}))  # observed (update not confirmed): now a dependency
        refused(env, first, "plan_changed", stale)

    def test_wrong_fingerprint(self, env):
        card, first = import_first(env)
        refused(env, first, "plan_changed", "0" * 64)

    def test_plan_changed_is_reported_before_blockers(self, env):
        card, first = import_first(env)
        stale = plan_of(env, first).plan_fingerprint
        edit(env, card, deal_value=1.0)
        assert refused(env, first, "plan_changed", stale).plan.status == "blocked"


# --- atomicity ------------------------------------------------------------------------------------

class TestAtomicity:
    def scenario(self, env):
        """A revert with every kind of write: a deletion, a restore and a move."""
        import_rows(env, source_row("000102-01"))
        card, first = import_first(env)
        second = update_batch(env, move=True, Estágio="SALES EVALUATION")
        return second

    def test_failure_in_the_middle_of_the_writes_rolls_everything_back(self, env, monkeypatch):
        batch_id = self.scenario(env)
        fingerprint = plan_of(env, batch_id).plan_fingerprint
        before = dump(env.path)
        original = revert_module._apply

        def apply_then_fail(db, plan):
            original(db, plan)
            db.flush()
            raise RuntimeError("injected failure")

        monkeypatch.setattr(revert_module, "_apply", apply_then_fail)
        with pytest.raises(RuntimeError, match="injected"):
            do_revert(env, batch_id, fingerprint)
        assert dump(env.path) == before
        assert not (env.db.new or env.db.dirty or env.db.deleted)
        monkeypatch.undo()
        assert do_revert(env, batch_id, fingerprint).outcome == "reverted"  # the session is usable again

    def test_failure_in_the_verification_rolls_everything_back(self, env, monkeypatch):
        batch_id = self.scenario(env)
        fingerprint = plan_of(env, batch_id).plan_fingerprint
        before = dump(env.path)
        original = revert_module._apply

        def apply_and_touch_another_card(db, plan):
            original(db, plan)
            db.get(Card, env.manual_id).title = "Collateral damage"

        monkeypatch.setattr(revert_module, "_apply", apply_and_touch_another_card)
        with pytest.raises(RevertVerificationFailed, match="differs from the plan"):
            do_revert(env, batch_id, fingerprint)
        assert dump(env.path) == before

    def test_verification_catches_a_missing_compensating_event(self, env, monkeypatch):
        batch_id = self.scenario(env)
        before = dump(env.path)
        original = revert_module._apply

        def apply_and_drop_events(db, plan):
            original(db, plan)
            db.flush()
            db.execute(text("DELETE FROM card_stage_events WHERE source = 'import_revert'"))

        monkeypatch.setattr(revert_module, "_apply", apply_and_drop_events)
        with pytest.raises(RevertVerificationFailed, match="stage events"):
            do_revert(env, batch_id)
        assert dump(env.path) == before

    def test_verification_catches_a_changed_undo_table(self, env, monkeypatch):
        batch_id = self.scenario(env)
        before = dump(env.path)
        original = revert_module._apply

        def apply_and_add_undo(db, plan):
            original(db, plan)
            db.add(UndoEntry(user_id=env.admin.id, board_id=env.board.id, description="x", steps="[]"))

        monkeypatch.setattr(revert_module, "_apply", apply_and_add_undo)
        with pytest.raises(RevertVerificationFailed, match="undo_entries"):
            do_revert(env, batch_id)
        assert dump(env.path) == before

    def test_unsaved_session_changes_are_refused(self, env):
        card, first = import_first(env)
        fingerprint = plan_of(env, first).plan_fingerprint
        env.db.add(Card(column_id=env.columns["RFI"].id, title="Unsaved", position=0))
        with pytest.raises(ValueError, match="clean session"):
            execute_revert(env.db, first, env.admin, fingerprint)
        env.db.rollback()


# --- persistence ----------------------------------------------------------------------------------

class TestPersistence:
    def test_result_and_counts_are_stored_on_the_ledger(self, env):
        import_rows(env, source_row("000102-01"))
        card, first = import_first(env)
        second = update_batch(env, move=True, Estágio="SALES EVALUATION")
        do_revert(env, second)
        ledger = ledger_of(env, second)
        assert ledger.revert_outcome == "reverted" and ledger.no_commercial_changes is False
        assert (ledger.cards_deleted_count, ledger.cards_restored_count, ledger.moves_reverted_count,
                ledger.already_deleted_count) == (0, 1, 1, 0)
        assert json.loads(ledger.result_summary_json) == {
            "items": 1, "observed": 0, "already_reverted": 0, "bid_details_deleted": 0}
        do_revert(env, first)
        ledger = ledger_of(env, first)
        assert (ledger.revert_outcome, ledger.cards_deleted_count, ledger.reverted_by_user_id) == (
            "reverted", 1, env.admin.id)
        assert "Projeto" not in ledger.result_summary_json  # counts only
        batch = env.db.get(ImportBatch, first)
        assert batch.status == "completed"  # the batch record itself is not rewritten

    def test_no_commercial_changes_is_distinct(self, env):
        import_first(env)
        second = import_rows(env, source_row(**{"Próximo passo": "Other", **NEWER})).batch_id
        do_revert(env, second)
        ledger = ledger_of(env, second)
        assert (ledger.revert_outcome, ledger.no_commercial_changes) == ("no_commercial_changes", True)
        assert (ledger.cards_deleted_count, ledger.cards_restored_count, ledger.moves_reverted_count) == (0, 0, 0)

    def test_already_deleted_is_counted(self, env):
        card, first = import_first(env)
        set_stage_context(env.db, env.admin, SOURCE_API)
        env.db.delete(card)
        env.db.commit()
        do_revert(env, first)
        assert ledger_of(env, first).already_deleted_count == 1


# --- Kanban undo ----------------------------------------------------------------------------------

class TestKanbanUndoCompatibility:
    def test_creates_no_undo_entry_and_leaves_existing_ones_alone(self, env):
        manual = env.db.get(Card, env.manual_id)
        step = undo_update(manual, {"title": "Edited by the admin"})
        manual.title = "Edited by the admin"
        step["expect"] = {"title": "Edited by the admin"}
        record_undo(env.db, env.admin, env.board.id, "edit", [step])
        env.db.commit()
        card, first = import_first(env)
        entries = dump(env.path)["undo_entries"]
        assert len(entries) == 1

        do_revert(env, first)
        assert dump(env.path)["undo_entries"] == entries

        assert apply_undo(env.db, env.admin, env.board.id) == "edit"  # the admin's own undo still works
        env.db.commit()
        env.db.expire_all()
        assert env.db.get(Card, env.manual_id).title == "Manual card"

    def test_an_undo_step_on_a_card_the_revert_deleted_is_refused_cleanly(self, env):
        card, first = import_first(env)
        step = undo_update(card, {"title": "Edited"})
        card.title = "Edited"
        step["expect"] = {"title": "Edited"}
        record_undo(env.db, env.admin, env.board.id, "edit", [step])
        env.db.commit()
        # The edit is a later change, so the revert refuses; revert after resetting the title by hand.
        assert plan_of(env, first).status == "blocked"
        edit(env, card, title="Projeto Teste")
        do_revert(env, first)
        with pytest.raises(UndoError, match="deleted"):
            apply_undo(env.db, env.admin, env.board.id)
        env.db.rollback()


# --- SQLite locks, two independent connections on a temp file -----------------------------------

@pytest.fixture
def locking(env):
    """A session on its own engine (short busy timeout) plus a second, raw connection to the same file."""
    card, first = import_first(env)
    engine = create_engine(f"sqlite:///{env.path}", connect_args={"timeout": 0.2})
    session = Session(engine)
    other = sqlite3.connect(env.path, timeout=0.2, isolation_level=None)

    class Locking:
        pass

    lock = Locking()
    lock.engine, lock.session, lock.other, lock.card_id, lock.batch_id = engine, session, other, card.id, first
    lock.admin_id = env.admin.id

    class Admin:
        id = env.admin.id

    lock.admin = Admin
    lock.path = env.path
    yield lock
    session.close()
    other.close()
    engine.dispose()


class TestLocks:
    def test_lock_at_the_start_is_busy_and_nothing_is_applied(self, locking):
        plan = build_revert_plan(locking.session, locking.batch_id)
        locking.session.rollback()
        locking.other.execute("BEGIN IMMEDIATE")  # another writer holds the write lock
        before = dump(locking.path)
        with pytest.raises(RevertBusy) as raised:
            execute_revert(locking.session, locking.batch_id, locking.admin, plan.plan_fingerprint)
        assert (raised.value.code, raised.value.phase, raised.value.applied) == ("import_busy", "start", False)
        locking.other.execute("ROLLBACK")
        assert dump(locking.path) == before

        result = execute_revert(locking.session, locking.batch_id, locking.admin, plan.plan_fingerprint)
        assert result.outcome == "reverted"  # the same preview is still valid once the lock is gone

    def test_lock_during_the_preview(self, locking):
        locking.other.execute("BEGIN EXCLUSIVE")  # readers are locked out too
        with pytest.raises(RevertBusy) as raised:
            revert_preview(locking.session, locking.batch_id)
        assert raised.value.phase == "preview"
        locking.other.execute("ROLLBACK")
        assert revert_preview(locking.session, locking.batch_id).status == "ready"

    def test_lock_during_the_commit_rolls_back_and_is_not_retried(self, locking):
        plan = build_revert_plan(locking.session, locking.batch_id)
        locking.session.rollback()
        reader = locking.other.execute("SELECT * FROM cards")  # an open read blocks the writer's COMMIT
        reader.fetchone()
        before = dump(locking.path)
        with pytest.raises(RevertBusy) as raised:
            execute_revert(locking.session, locking.batch_id, locking.admin, plan.plan_fingerprint)
        assert (raised.value.phase, raised.value.applied) == ("commit", False)
        reader.close()
        assert dump(locking.path) == before  # rolled back: not reverted, nothing changed
        assert not locking.session.in_transaction()

        assert execute_revert(locking.session, locking.batch_id, locking.admin, plan.plan_fingerprint).outcome == "reverted"

    def test_commit_that_actually_committed_is_never_reported_as_success(self, locking, monkeypatch):
        plan = build_revert_plan(locking.session, locking.batch_id)
        locking.session.rollback()
        real_commit = locking.session.commit

        def commit_then_fail():
            real_commit()
            raise OperationalError("COMMIT", None, sqlite3.OperationalError("database is locked"))

        monkeypatch.setattr(locking.session, "commit", commit_then_fail)
        with pytest.raises(RevertOutcomeUnconfirmed) as raised:
            execute_revert(locking.session, locking.batch_id, locking.admin, plan.plan_fingerprint)
        assert raised.value.code == "revert_outcome_unconfirmed"
        monkeypatch.undo()
        again = build_revert_plan(locking.session, locking.batch_id)
        assert again.status == "already_reverted"  # the durable state is what the retry sees

    def test_other_commit_errors_are_not_busy(self, locking, monkeypatch):
        plan = build_revert_plan(locking.session, locking.batch_id)
        locking.session.rollback()
        error = OperationalError("COMMIT", None, sqlite3.OperationalError("disk I/O error"))

        def failing_commit():
            raise error

        monkeypatch.setattr(locking.session, "commit", failing_commit)
        before = dump(locking.path)
        with pytest.raises(OperationalError) as raised:
            execute_revert(locking.session, locking.batch_id, locking.admin, plan.plan_fingerprint)
        assert raised.value is error and dump(locking.path) == before

    def test_a_failed_rollback_discards_the_connection(self, locking, monkeypatch):
        plan = build_revert_plan(locking.session, locking.batch_id)
        locking.session.rollback()
        discarded = []
        monkeypatch.setattr(locking.session, "invalidate", lambda: discarded.append(True))

        def failing_commit():
            raise OperationalError("COMMIT", None, sqlite3.OperationalError("database is locked"))

        def failing_rollback():
            raise RuntimeError("rollback failed")

        monkeypatch.setattr(locking.session, "commit", failing_commit)
        monkeypatch.setattr(locking.session, "rollback", failing_rollback)
        with pytest.raises(RevertBusy):
            execute_revert(locking.session, locking.batch_id, locking.admin, plan.plan_fingerprint)
        assert discarded == [True]


class TestLockDetection:
    @pytest.mark.parametrize("code,expected", [(5, True), (6, True), (261, True), (517, True), (1, False), (10, False)])
    def test_by_result_code(self, code, expected):
        orig = sqlite3.OperationalError("x")
        orig.sqlite_errorcode = code
        assert is_lock_error(OperationalError("s", None, orig)) is expected

    @pytest.mark.parametrize("message,expected", [
        ("database is locked", True), ("database table is locked", True), ("no such table: x", False)])
    def test_by_message(self, message, expected):
        assert is_lock_error(OperationalError("s", None, sqlite3.OperationalError(message))) is expected

    def test_other_exceptions_are_not_lock_errors(self):
        assert is_lock_error(RuntimeError("database is locked")) is False
