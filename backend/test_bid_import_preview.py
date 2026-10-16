"""Part 22C: bid_import_preview - synthetic records only (fictitious data), no database."""
import hashlib
import os
import subprocess
import sys
import unicodedata
from dataclasses import replace
from datetime import date, datetime

import pytest

from bid_excel_reader import BidReadResult, BidRecord, BidRow, FileMetadata, RawRow, ReadIssue, Severity, read_bid_excel
from bid_import_preview import (
    Action,
    BID_DETAILS_FIELDS,
    CARD_FIELDS,
    Classification,
    DecisionNeeded,
    ExistingImport,
    NOT_MAPPED_FIELDS,
    RowDecision,
    SOURCE_REF_FIELDS,
    SOURCE_SYSTEM,
    StageRef,
    bid_row_hash,
    bid_source_key,
    build_import_preview,
    classify_stage,
)
from models import Card, CardBidDetails, CardSourceRef, SOURCE_SYSTEM_SALCOMP_BIDS
from test_bid_excel_reader import build, row as excel_row

STAGES = [
    StageRef(11, "1st Contact", "open"),
    StageRef(12, "F2F Meeting", "open"),
    StageRef(13, "RFI", "open"),
    StageRef(14, "Proposal", "open"),
    StageRef(15, "Award", "won"),
    StageRef(16, "Lost", "lost"),
]


def rec(row_number=2, **overrides) -> BidRecord:
    values = dict(
        code="000123-01", customer="Cliente Ficticio", cat="OPEN", stage="RFQ", project_name="Projeto Teste",
        prospect_description="Descricao", plant="Planta", customer_type="LEAD", business_type="CASH COW",
        product_category="Charger", product="Produto X", solution_type="ODM",
        confidence_label="LOW (10%)", confidence_percent=10, volume_current_year=1500, volume_next_year=None,
        production_start_date=date(2026, 7, 1), revenue_current_year_cents=123456, revenue_next_year_cents=None,
        status_note="Status", cpm="Pessoa A", project_lead="Pessoa B", next_step="Ligar",
        next_step_due=date(2026, 6, 30), close_reason=None, close_reason_note=None,
        source_created_on=date(2026, 2, 1), source_updated_on=date(2026, 3, 5), source_last_edited_on=None,
        year=2026, budget_enabled="Não", budget=None,
    )
    values.update(overrides)
    return BidRecord(row_number=row_number, **values)


def result(*records, errors=None) -> BidReadResult:
    errors = errors or {}
    rows = tuple(
        BidRow(raw=RawRow(r.row_number, {}), record=r, errors=tuple(errors.get(r.row_number, ())))
        for r in records
    )
    return BidReadResult(file=FileMetadata("relatorio-leads.xlsx", 1, "0" * 64), rows=rows)


def preview(*records, existing=(), decisions=(), stages=STAGES, errors=None):
    return build_import_preview(result(*records, errors=errors), stages, existing, decisions)


def one(*records, **kwargs):
    [row] = preview(*records, **kwargs).rows
    return row


def existing_for(record: BidRecord, card_id=900, stage_title="Proposal", **field_overrides) -> ExistingImport:
    """An ExistingImport as the import step would have stored `record`."""
    fields = one(record).fields.flat()
    fields.update(field_overrides)
    return ExistingImport(fields["source_ref.source_key"], card_id, stage_title, fields)


def codes(issues):
    return [issue.code for issue in issues]


# --- classification -------------------------------------------------------------------

class TestClassification:
    @pytest.mark.parametrize("cat, stage, target", [
        ("WON", "AWARD GIVEN", "Award"),
        ("LOST", "NOT AWARDED BY CUSTOMER", "Lost"),
        ("OPEN", "SALES EVALUATION", "F2F Meeting"),
        ("OPEN", "INTERNAL TECHNICAL EVALUATION", "RFI"),
        ("OPEN", "RFQ", "Proposal"),
        ("Open CE", "SALES EVALUATION", "F2F Meeting"),
        ("Open CE", "INTERNAL TECHNICAL EVALUATION", "RFI"),
        ("Open CE", "RFQ", "Proposal"),
    ])
    def test_approved_rules(self, cat, stage, target):
        row = one(rec(cat=cat, stage=stage))
        assert row.classification.status is Classification.MAPPED
        assert row.target_stage == target and row.action is Action.CREATE
        assert row.classification.target in STAGES  # an existing stage, never a new one

    def test_open_ce_stays_distinct_and_warns(self):
        open_ce, open_ = one(rec(cat="Open CE")), one(rec(cat="OPEN"))
        assert open_ce.source_status == "Open CE" and open_ce.fields.source_ref["source_status"] == "Open CE"
        assert "open_ce_unconfirmed" in codes(open_ce.warnings)
        assert "open_ce_unconfirmed" not in codes(open_.warnings)
        assert open_ce.fields.source_ref["source_row_hash"] != open_.fields.source_ref["source_row_hash"]

    def test_normalization_only_for_comparison(self):
        row = one(rec(cat="  open   ce ", stage="Rfq"))
        assert row.classification.status is Classification.MAPPED and row.target_stage == "Proposal"
        assert (row.source_status, row.source_stage) == ("  open   ce ", "Rfq")
        assert (row.classification.cat, row.classification.stage) == ("  open   ce ", "Rfq")
        assert row.fields.source_ref["source_status"] == "  open   ce " and row.fields.source_ref["source_stage"] == "Rfq"

    def test_closed_not_awarded_needs_explicit_confirmation(self):
        record = rec(cat="CLOSED", stage="NOT AWARDED BY CUSTOMER")
        row = one(record)
        assert row.classification.status is Classification.NEEDS_CONFIRMATION
        assert row.classification.target is None and row.classification.proposed.title == "Lost"
        assert row.action is Action.REVIEW and row.required_decisions == (DecisionNeeded.CONFIRM_STAGE,)

        confirmed = one(record, decisions=[RowDecision(2, confirm_proposed_stage=True)])
        assert confirmed.classification.status is Classification.DECIDED
        assert confirmed.target_stage == "Lost" and confirmed.action is Action.CREATE

    def test_closed_by_salcomp_stays_pending(self):
        record = rec(cat="CLOSED", stage="CLOSED (BY SALCOMP)")
        row = one(record)
        assert row.classification.status is Classification.NEEDS_REVIEW
        assert row.classification.target is None and row.classification.proposed is None
        assert row.action is Action.REVIEW and row.required_decisions == (DecisionNeeded.CHOOSE_STAGE,)
        # Nothing to confirm: only an explicit stage choice resolves it.
        assert one(record, decisions=[RowDecision(2, confirm_proposed_stage=True)]).action is Action.REVIEW
        decided = one(record, decisions=[RowDecision(2, target_stage="Lost")])
        assert decided.classification.status is Classification.DECIDED and decided.action is Action.CREATE

    @pytest.mark.parametrize("cat, stage", [
        ("WON", "RFQ"), ("LOST", "RFQ"), ("CLOSED", "RFQ"),
        ("WON", "NOT AWARDED BY CUSTOMER"), ("LOST", "AWARD GIVEN"), ("OPEN", "AWARD GIVEN"),
        ("OPEN", "NOT AWARDED BY CUSTOMER"), ("Open CE", "CLOSED (BY SALCOMP)"), ("WON", "SALES EVALUATION"),
        ("CLOSED", "AWARD GIVEN"),
    ])
    def test_inconsistent_combinations_get_no_stage(self, cat, stage):
        row = one(rec(cat=cat, stage=stage))
        assert row.classification.status is Classification.INCONSISTENT
        assert row.target_stage is None and row.action is Action.REVIEW
        assert row.required_decisions == (DecisionNeeded.CHOOSE_STAGE,)
        assert "inconsistent_combination" in codes(row.warnings)

    @pytest.mark.parametrize("cat, stage", [("PENDING", "RFQ"), ("OPEN", "NEGOTIATION"), ("Ganho", "Ganho")])
    def test_unknown_combinations_are_pending(self, cat, stage):
        row = one(rec(cat=cat, stage=stage))
        assert row.classification.status is Classification.UNKNOWN
        assert row.action is Action.REVIEW and "unknown_combination" in codes(row.warnings)

    def test_won_and_lost_found_by_stage_kind_not_title(self):
        renamed = [replace(s, title={"Award": "Ganho", "Lost": "Perdido"}.get(s.title, s.title)) for s in STAGES]
        assert one(rec(cat="WON", stage="AWARD GIVEN"), stages=renamed).target_stage == "Ganho"
        assert one(rec(cat="LOST", stage="NOT AWARDED BY CUSTOMER"), stages=renamed).target_stage == "Perdido"

    def test_missing_open_target_is_reviewed(self):
        stages = [s for s in STAGES if s.title != "RFI"]
        row = one(rec(stage="INTERNAL TECHNICAL EVALUATION"), stages=stages)
        assert row.action is Action.REVIEW and "target_stage_missing" in codes(row.warnings)

    def test_board_needs_one_won_and_one_lost(self):
        with pytest.raises(ValueError, match="won"):
            preview(rec(), stages=[s for s in STAGES if s.stage_kind != "won"])

    def test_classify_stage_directly(self):
        assert classify_stage("WON", "AWARD GIVEN", STAGES).target.column_id == 15


# --- source key ---------------------------------------------------------------------------

class TestSourceKey:
    def test_versioned_and_deterministic(self):
        key = bid_source_key("000123-01", "Cliente Ficticio")
        assert key == 'v1:["000123-01","cliente ficticio"]'
        assert bid_source_key("000123-01", "Cliente Ficticio") == key

    def test_leading_zeros_preserved(self):
        assert bid_source_key("000123-01", "X") != bid_source_key("123-01", "X")
        assert '"000123-01"' in bid_source_key("000123-01", "X")

    @pytest.mark.parametrize("variant", [
        "cliente são paulo", "  CLIENTE   SÃO PAULO ", "Cliente Sao Paulo", "cliente\tsão\npaulo",
        unicodedata.normalize("NFD", "Cliente São Paulo"),
    ])
    def test_spacing_case_and_accents(self, variant):
        assert bid_source_key("000123-01", variant) == bid_source_key("000123-01", "Cliente São Paulo")

    @pytest.mark.parametrize("a, b", [
        (("A|B", "C"), ("A", "B|C")),
        (("A,B", "C"), ("A", "B,C")),
        (('A"', "B"), ("A", '"B')),
        (('A","B', "C"), ("A", 'B","C')),
        (("A B", "C"), ("A", "B C")),
    ])
    def test_no_concatenation_collisions(self, a, b):
        assert bid_source_key(*a) != bid_source_key(*b)

    def test_same_code_different_customer_is_another_identity(self):
        assert bid_source_key("000123-01", "Cliente A") != bid_source_key("000123-01", "Cliente B")

    @pytest.mark.parametrize("code, customer", [("", "X"), ("X", "  "), (None, "X")])
    def test_both_parts_required(self, code, customer):
        with pytest.raises(ValueError):
            bid_source_key(code, customer)

    def test_source_system(self):
        assert SOURCE_SYSTEM == SOURCE_SYSTEM_SALCOMP_BIDS
        assert one(rec()).fields.source_ref["source_system"] == "salcomp-bids"


# --- duplicates within the file ---------------------------------------------------------------

class TestDuplicates:
    def test_same_code_and_customer_go_to_review(self):
        p = preview(rec(2), rec(3, customer="  cliente   FICTICIO"), rec(4, code="000999-01"))
        first, second, other = p.rows
        assert first.action is second.action is Action.REVIEW
        assert first.duplicate_rows == (3,) and second.duplicate_rows == (2,)
        assert first.required_decisions == (DecisionNeeded.RESOLVE_DUPLICATE,)
        assert other.action is Action.CREATE
        assert p.summary.internal_duplicate_rows == 2 and p.summary.proposed_creates == 1

    def test_skipping_one_resolves_the_duplicate(self):
        p = preview(rec(2), rec(3), decisions=[RowDecision(3, skip=True)])
        assert [r.action for r in p.rows] == [Action.CREATE, Action.IGNORE]
        assert p.summary.internal_duplicate_rows == 0

    def test_same_code_different_customers_are_not_duplicates(self):
        p = preview(rec(2, customer="Cliente A"), rec(3, customer="Cliente B"))
        assert [r.action for r in p.rows] == [Action.CREATE, Action.CREATE]


# --- re-import ---------------------------------------------------------------------------------

class TestReimport:
    def test_new_record(self):
        row = one(rec())
        assert row.action is Action.CREATE and row.existing_card_id is None and codes(row.reasons) == ["new_record"]

    def test_unchanged_is_ignored(self):
        record = rec()
        row = one(record, existing=[existing_for(record)])
        assert row.action is Action.IGNORE and codes(row.reasons) == ["unchanged"]
        assert row.existing_card_id == 900 and row.required_decisions == ()

    def test_unchanged_even_if_the_card_was_moved_in_the_app(self):
        record = rec()
        row = one(record, existing=[existing_for(record, stage_title="Award")])
        assert row.action is Action.IGNORE and row.stage_change is None

    def test_newer_source_with_changes_proposes_an_update_awaiting_confirmation(self):
        old = rec(next_step="Ligar")
        new = rec(next_step="Enviar proposta", source_updated_on=date(2026, 4, 1))
        row = one(new, existing=[existing_for(old)])
        assert row.action is Action.UPDATE and codes(row.reasons) == ["newer_source"]
        assert row.required_decisions == (DecisionNeeded.CONFIRM_UPDATE,)
        changed = {c.field: (c.current, c.incoming) for c in row.changed_fields}
        assert changed["bid_details.next_step"] == ("Ligar", "Enviar proposta")
        assert changed["source_ref.source_updated_at"] == (date(2026, 3, 5), date(2026, 4, 1))
        assert "source_ref.source_row_hash" not in changed

        confirmed = one(new, existing=[existing_for(old)], decisions=[RowDecision(2, confirm_update=True)])
        assert confirmed.action is Action.UPDATE and confirmed.required_decisions == ()

    def test_existing_datetimes_compare_by_date(self):
        old = rec(next_step="Ligar")
        stored = existing_for(old, **{"source_ref.source_updated_at": datetime(2026, 3, 5, 0, 0)})
        row = one(rec(next_step="Outro", source_updated_on=date(2026, 4, 1)), existing=[stored])
        assert codes(row.reasons) == ["newer_source"]

    def test_older_source_does_not_overwrite(self):
        stored = existing_for(rec(next_step="Mais recente", source_updated_on=date(2026, 5, 1)))
        incoming = rec(next_step="Antigo", source_updated_on=date(2026, 3, 5))
        row = one(incoming, existing=[stored])
        assert row.action is Action.IGNORE and codes(row.reasons) == ["older_source"]
        assert row.required_decisions == (DecisionNeeded.CONFIRM_UPDATE,)
        forced = one(incoming, existing=[stored], decisions=[RowDecision(2, confirm_update=True)])
        assert forced.action is Action.UPDATE and forced.required_decisions == ()

    @pytest.mark.parametrize("incoming_date, stored_date", [
        (date(2026, 3, 5), date(2026, 3, 5)),  # same date, different content
        (None, date(2026, 3, 5)),
        (date(2026, 3, 5), None),
    ])
    def test_undecidable_dates_need_review(self, incoming_date, stored_date):
        stored = existing_for(rec(next_step="A", source_updated_on=stored_date))
        incoming = rec(next_step="B", source_updated_on=incoming_date)
        row = one(incoming, existing=[stored])
        assert row.action is Action.REVIEW and codes(row.reasons) == ["ambiguous_dates"]
        assert row.required_decisions == (DecisionNeeded.CONFIRM_UPDATE,)
        confirmed = one(incoming, existing=[stored], decisions=[RowDecision(2, confirm_update=True)])
        assert confirmed.action is Action.UPDATE and confirmed.required_decisions == ()

    def test_last_edited_date_counts_as_freshness(self):
        stored = existing_for(rec(next_step="A"))
        incoming = rec(next_step="B", source_last_edited_on=date(2026, 6, 1))
        assert codes(one(incoming, existing=[stored]).reasons) == ["newer_source"]

    def test_stage_change_never_moves_automatically(self):
        stored = existing_for(rec(stage="INTERNAL TECHNICAL EVALUATION"), stage_title="RFI")
        incoming = rec(stage="RFQ", source_updated_on=date(2026, 4, 1))
        row = one(incoming, existing=[stored])
        assert row.action is Action.UPDATE
        assert row.stage_change == ("RFI", "Proposal") and not row.move_confirmed
        assert set(row.required_decisions) == {DecisionNeeded.CONFIRM_UPDATE, DecisionNeeded.CONFIRM_MOVE}
        assert "stage_change" in codes(row.reasons)
        moved = one(incoming, existing=[stored], decisions=[RowDecision(2, confirm_update=True, confirm_move=True)])
        assert moved.move_confirmed and moved.required_decisions == ()

    def test_status_change_with_same_stage_is_not_a_move(self):
        stored = existing_for(rec(cat="OPEN"), stage_title="Proposal")
        row = one(rec(cat="Open CE", source_updated_on=date(2026, 4, 1)), existing=[stored])
        assert row.stage_change is None
        assert ("source_ref.source_status", "OPEN", "Open CE") in [(c.field, c.current, c.incoming) for c in row.changed_fields]

    def test_changed_record_with_undetermined_stage_needs_review(self):
        stored = existing_for(rec())
        row = one(rec(cat="CLOSED", stage="CLOSED (BY SALCOMP)", source_updated_on=date(2026, 4, 1)), existing=[stored])
        assert row.action is Action.REVIEW and row.required_decisions == (DecisionNeeded.CHOOSE_STAGE,)

    def test_existing_keys_must_be_unique(self):
        stored = existing_for(rec())
        with pytest.raises(ValueError, match="unique"):
            preview(rec(), existing=[stored, replace(stored, card_id=901)])


# --- rows that can't be imported ------------------------------------------------------------------

class TestBlockedRows:
    def test_invalid_row_is_never_importable(self):
        error = ReadIssue(Severity.ERROR, "invalid_date", "Not a date", row=2, column="Deadline", field="next_step_due")
        row = one(rec(), errors={2: [error]})
        assert row.action is Action.REVIEW and row.required_decisions == (DecisionNeeded.FIX_SOURCE_ROW,)
        assert row.errors == (error,)
        # A stage decision doesn't make it importable; only skipping removes it.
        assert one(rec(), errors={2: [error]}, decisions=[RowDecision(2, target_stage="RFI")]).action is Action.REVIEW
        assert one(rec(), errors={2: [error]}, decisions=[RowDecision(2, skip=True)]).action is Action.IGNORE

    def test_invalid_row_without_key(self):
        error = ReadIssue(Severity.ERROR, "required_value_missing", "Required", row=2, column="Código", field="code")
        row = one(rec(code=None), errors={2: [error]})
        assert row.source_key is None and row.fields is None and row.action is Action.REVIEW

    def test_code_needing_review_blocks_import(self):
        row = one(rec(code="123", code_needs_review=True))
        assert row.action is Action.REVIEW and codes(row.reasons) == ["code_needs_review"]

    def test_missing_title_blocks_import(self):
        row = one(rec(project_name=None))
        assert row.action is Action.REVIEW and codes(row.reasons) == ["missing_title"]

    def test_file_errors_give_no_rows(self):
        bad = BidReadResult(
            file=FileMetadata("x.xlsx", 0, ""),
            file_errors=(ReadIssue(Severity.ERROR, "empty_file", "The file is empty"),),
        )
        p = build_import_preview(bad, STAGES)
        assert not p.ok and p.rows == () and p.file_errors == bad.file_errors

    @pytest.mark.parametrize("decision, message", [
        (RowDecision(99, skip=True), "not in the file"),
        (RowDecision(2, target_stage="Negotiation"), "no stage titled"),
    ])
    def test_invalid_decisions_are_refused(self, decision, message):
        with pytest.raises(ValueError, match=message):
            preview(rec(), decisions=[decision])

    @pytest.mark.parametrize("record, decision, existing", [
        (rec(), RowDecision(2, skip=True, confirm_update=True), False),
        (rec(cat="CLOSED", stage="NOT AWARDED BY CUSTOMER"), RowDecision(2, target_stage="Lost", confirm_proposed_stage=True), False),
        (rec(cat="CLOSED", stage="CLOSED (BY SALCOMP)"), RowDecision(2, confirm_proposed_stage=True), False),
        (rec(project_name=None), RowDecision(2, target_stage="RFI"), False),
        (rec(), RowDecision(2, confirm_update=True), False),           # new record: nothing to update
        (rec(), RowDecision(2, confirm_update=True), True),            # unchanged: nothing to update
        (rec(), RowDecision(2, confirm_move=True), False),             # no stage change
    ])
    def test_decisions_that_do_not_fit_are_flagged_not_ignored(self, record, decision, existing):
        stored = [existing_for(record)] if existing else []
        row = one(record, decisions=[decision], existing=stored)
        assert row.action is Action.REVIEW and "decision_not_applicable" in codes(row.reasons)

    def test_move_needs_the_update_confirmed_too(self):
        stored = existing_for(rec(stage="INTERNAL TECHNICAL EVALUATION"), stage_title="RFI")
        row = one(rec(stage="RFQ", source_updated_on=date(2026, 4, 1)), existing=[stored],
                  decisions=[RowDecision(2, confirm_move=True)])
        assert row.action is Action.REVIEW and "decision_not_applicable" in codes(row.reasons)

    def test_one_decision_per_row(self):
        with pytest.raises(ValueError, match="More than one"):
            preview(rec(), decisions=[RowDecision(2, skip=True), RowDecision(2, confirm_update=True)])


# --- field mapping -------------------------------------------------------------------------------

class TestFieldMapping:
    def test_card_fields(self):
        fields = one(rec()).fields
        assert fields.card == {"title": "Projeto Teste", "details": "Descricao", "company": "Cliente Ficticio"}

    def test_mapping_targets_are_real_model_columns(self):
        assert set(CARD_FIELDS) <= set(Card.__table__.columns.keys())
        assert set(BID_DETAILS_FIELDS) <= set(CardBidDetails.__table__.columns.keys())
        source_columns = set(CardSourceRef.__table__.columns.keys())
        assert set(SOURCE_REF_FIELDS) | {"source_system", "source_key", "source_row_hash"} <= source_columns
        assert set(one(rec()).fields.source_ref) <= source_columns

    def test_never_mapped_card_fields(self):
        fields = one(rec()).fields
        for column in ("deal_value", "currency", "expected_close_date", "lead_source", "salesperson_id",
                       "contact", "product_line", "created_at", "column_id"):
            assert column not in fields.card
        assert "lead_source" not in fields.bid_details and fields.bid_details["customer_type"] == "LEAD"

    def test_revenue_goes_to_bid_details_in_cents_never_deal_value(self):
        fields = one(rec(revenue_current_year_cents=999999999999999999, revenue_next_year_cents=10)).fields
        assert fields.bid_details["revenue_current_year_cents"] == 999999999999999999
        assert fields.bid_details["revenue_next_year_cents"] == 10
        assert fields.bid_details["revenue_currency"] == "BRL"
        assert all("deal_value" not in key for key in fields.flat())

    def test_bid_details(self):
        details = one(rec()).fields.bid_details
        assert details == {
            "plant": "Planta", "customer_type": "LEAD", "business_type": "CASH COW", "product_category": "Charger",
            "product": "Produto X", "solution_type": "ODM", "confidence": 10, "volume_current_year": 1500,
            "volume_next_year": None, "production_start_date": date(2026, 7, 1),
            "revenue_current_year_cents": 123456, "revenue_next_year_cents": None, "revenue_currency": "BRL",
            "status_note": "Status", "project_lead": "Pessoa B", "cpm": "Pessoa A", "next_step": "Ligar",
            "next_step_due": date(2026, 6, 30), "close_reason": None, "close_reason_note": None,
        }

    def test_absent_values_stay_none(self):
        details = one(rec(volume_current_year=None, revenue_current_year_cents=None, confidence_percent=None)).fields.bid_details
        assert details["volume_current_year"] is None and details["revenue_current_year_cents"] is None
        assert details["confidence"] is None

    def test_source_ref_preserves_the_source(self):
        record = rec(code="000007-03", cat="Open CE", stage="RFQ", source_last_edited_on=date(2026, 3, 9))
        source = one(record).fields.source_ref
        assert source["source_code"] == "000007-03"
        assert (source["source_status"], source["source_stage"]) == ("Open CE", "RFQ")
        assert source["source_created_at"] == date(2026, 2, 1)  # Data de criação stays a source date
        assert source["source_updated_at"] == date(2026, 3, 5)
        assert source["source_last_edited_at"] == date(2026, 3, 9)
        assert source["source_row_hash"] == bid_row_hash(record)

    def test_people_stay_text_and_no_user_is_involved(self):
        fields = one(rec()).fields
        assert fields.bid_details["cpm"] == "Pessoa A" and fields.bid_details["project_lead"] == "Pessoa B"
        assert "cpm" not in fields.not_mapped
        flat = fields.flat()
        assert not any("salesperson" in key or "user" in key for key in flat)
        assert set(fields.not_mapped) == set(NOT_MAPPED_FIELDS)

    def test_confidence_label_goes_to_the_source_ref(self):
        fields = one(rec()).fields
        assert fields.source_ref["source_confidence"] == "LOW (10%)" and fields.bid_details["confidence"] == 10
        assert "confidence_label" not in fields.not_mapped

    def test_deadline_is_next_step_due_not_expected_close(self):
        fields = one(rec()).fields
        assert fields.bid_details["next_step_due"] == date(2026, 6, 30)
        assert "expected_close_date" not in fields.card

    def test_row_hash(self):
        assert bid_row_hash(rec()) == bid_row_hash(rec(row_number=40))  # position doesn't matter
        assert bid_row_hash(rec()) != bid_row_hash(rec(cpm="Pessoa C"))  # every source field counts
        assert bid_row_hash(rec()).startswith("v1:")


# --- summary -------------------------------------------------------------------------------------

class TestSummary:
    def test_summary_matches_the_rows(self):
        error = ReadIssue(Severity.ERROR, "invalid_date", "x", row=6)
        stored_same = rec(7, code="000700-01")
        stored_old = rec(8, code="000800-01", next_step="A")
        p = preview(
            rec(2, code="000200-01"),                                            # create, Proposal
            rec(3, code="000300-01", cat="WON", stage="AWARD GIVEN",
                revenue_current_year_cents=500, revenue_next_year_cents=700),    # create, Award
            rec(4, code="000400-01", cat="CLOSED", stage="CLOSED (BY SALCOMP)"),  # review
            rec(5, code="000500-01", cat="CLOSED", stage="NOT AWARDED BY CUSTOMER"),  # review (confirm)
            rec(6, code="000600-01"),                                            # review (invalid)
            stored_same,                                                         # ignore (unchanged)
            rec(8, code="000800-01", next_step="B", source_updated_on=date(2026, 4, 1)),  # update
            rec(9, code="000900-01"), rec(10, code="000900-01"),                 # duplicates
            rec(11, code="001100-01"),                                           # skipped
            errors={6: [error]},
            existing=[existing_for(stored_same, card_id=1), existing_for(stored_old, card_id=2)],
            decisions=[RowDecision(11, skip=True)],
        )
        s = p.summary
        assert (s.total_rows, s.valid_rows, s.rows_with_errors) == (10, 9, 1)
        assert (s.proposed_creates, s.proposed_updates, s.unconfirmed_updates) == (2, 1, 1)
        assert (s.unchanged, s.ignored, s.pending_review, s.internal_duplicate_rows) == (1, 2, 5, 2)
        assert s.proposed_creates + s.proposed_updates + s.ignored + s.pending_review == s.total_rows
        assert s.by_action == {"create": 2, "update": 1, "ignore": 2, "review": 5}
        assert sum(s.by_classification.values()) == s.total_rows
        assert s.by_classification["needs_review"] == 1 and s.by_classification["needs_confirmation"] == 1
        assert sum(s.by_target_stage.values()) == s.total_rows and s.by_target_stage["Award"] == 1
        # Pending rows are not creates; revenue only from create/update rows, per currency.
        assert s.revenue_cents_by_currency == {"BRL": {"current_year": 123456 + 500 + 123456, "next_year": 700}}
        assert p.written is False and "nothing was written" in p.notice

    def test_deterministic(self):
        records = [rec(2), rec(3, code="000300-01", cat="Open CE"), rec(4, code="000300-01", cat="LOST")]
        assert preview(*records) == preview(*records)


# --- end to end and side effects -------------------------------------------------------------------

def test_from_a_workbook():
    data = build([
        excel_row(),
        excel_row(**{"Código": "000124-01", "CAT": "CLOSED", "Estágio": "CLOSED (BY SALCOMP)"}),
    ])
    p = build_import_preview(read_bid_excel(data, "relatorio-leads.xlsx"), STAGES)
    assert [r.row_number for r in p.rows] == [2, 3]
    assert [r.action for r in p.rows] == [Action.CREATE, Action.REVIEW]
    assert p.rows[0].fields.bid_details["revenue_current_year_cents"] == 123456


def test_preview_touches_no_database(tmp_path):
    database_path = os.environ["DATABASE_URL"].removeprefix("sqlite:///")
    digest = lambda: hashlib.sha256(open(database_path, "rb").read()).hexdigest() if os.path.exists(database_path) else None
    before = digest()
    path = tmp_path / "relatorio-leads.xlsx"
    path.write_bytes(build([excel_row()]))
    script = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from bid_excel_reader import read_bid_excel_file;"
        "from bid_import_preview import StageRef, build_import_preview;"
        "stages = [StageRef(1, 'Proposal', 'open'), StageRef(2, 'Award', 'won'), StageRef(3, 'Lost', 'lost')];"
        "p = build_import_preview(read_bid_excel_file(sys.argv[2]), stages);"
        "assert p.summary.proposed_creates == 1 and p.written is False;"
        "print(','.join(m for m in ('sqlalchemy', 'database', 'models', 'sqlite3') if m in sys.modules) or 'none')"
    )
    backend = os.path.dirname(os.path.abspath(__file__))
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    out = subprocess.run([sys.executable, "-I", "-c", script, backend, str(path)],
                         capture_output=True, text=True, env=env, check=True)
    assert out.stdout.strip() == "none"
    assert digest() == before
