"""Import preview for the Salcomp Bids report (Part 22C): commercial mapping,
classification, duplicate/re-import detection and a proposed action per row.

build_import_preview() is a pure function of its inputs - the reader's
BidReadResult, the board's stages, optional references to already-imported
cards and optional explicit decisions. It reads no database and writes
nothing; the future endpoints load those inputs and the future import step
applies the result.

Nothing ambiguous is decided here: a row whose stage needs confirmation or
review, a duplicate key, an invalid row, an update or a stage move is never
importable until a RowDecision says so.
"""
import json
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field, fields as dataclass_fields
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
from typing import Iterable, Mapping, Sequence

from bid_excel_reader import BidReadResult, BidRecord, FileMetadata, ReadIssue, Severity

SOURCE_SYSTEM = "salcomp-bids"  # same value as models.SOURCE_SYSTEM_SALCOMP_BIDS
SOURCE_KEY_VERSION = "v1"
ROW_HASH_VERSION = "v1"


# --- stages and classification ------------------------------------------------------

@dataclass(frozen=True)
class StageRef:
    """A board column, as the caller loaded it."""
    column_id: int
    title: str
    stage_kind: str  # open | won | lost


class Classification(str, Enum):
    MAPPED = "mapped"                          # an approved rule gives the stage
    NEEDS_CONFIRMATION = "needs_confirmation"  # a stage is proposed but must be confirmed
    NEEDS_REVIEW = "needs_review"              # known combination with no automatic stage
    INCONSISTENT = "inconsistent"              # CAT and Estágio contradict each other
    UNKNOWN = "unknown"                        # a CAT or Estágio value no rule knows
    DECIDED = "decided"                        # a RowDecision chose or confirmed the stage


# Targets of the approved rules. Won/lost targets are found by stage_kind
# (unique per board), never by title; open stages can only be told apart by title.
TARGET_WON, TARGET_LOST = "won", "lost"
TARGET_F2F, TARGET_RFI, TARGET_PROPOSAL = "F2F Meeting", "RFI", "Proposal"

CAT_OPEN, CAT_OPEN_CE, CAT_WON, CAT_LOST, CAT_CLOSED = "open", "open ce", "won", "lost", "closed"
STAGE_AWARD_GIVEN = "award given"
STAGE_NOT_AWARDED = "not awarded by customer"
STAGE_SALES_EVALUATION = "sales evaluation"
STAGE_TECHNICAL_EVALUATION = "internal technical evaluation"
STAGE_RFQ = "rfq"
STAGE_CLOSED_BY_SALCOMP = "closed (by salcomp)"

KNOWN_CATS = {CAT_OPEN, CAT_OPEN_CE, CAT_WON, CAT_LOST, CAT_CLOSED}
KNOWN_STAGES = {
    STAGE_AWARD_GIVEN, STAGE_NOT_AWARDED, STAGE_SALES_EVALUATION,
    STAGE_TECHNICAL_EVALUATION, STAGE_RFQ, STAGE_CLOSED_BY_SALCOMP,
}

# (CAT, Estágio) after normalize_label -> (classification, target). Anything not
# listed with two known values is INCONSISTENT; an unknown value is UNKNOWN.
STAGE_RULES: dict[tuple[str, str], tuple[Classification, str | None]] = {
    (CAT_WON, STAGE_AWARD_GIVEN): (Classification.MAPPED, TARGET_WON),
    (CAT_LOST, STAGE_NOT_AWARDED): (Classification.MAPPED, TARGET_LOST),
    **{
        (cat, stage): (Classification.MAPPED, target)
        for cat in (CAT_OPEN, CAT_OPEN_CE)
        for stage, target in (
            (STAGE_SALES_EVALUATION, TARGET_F2F),
            (STAGE_TECHNICAL_EVALUATION, TARGET_RFI),
            (STAGE_RFQ, TARGET_PROPOSAL),
        )
    },
    (CAT_CLOSED, STAGE_NOT_AWARDED): (Classification.NEEDS_CONFIRMATION, TARGET_LOST),
    (CAT_CLOSED, STAGE_CLOSED_BY_SALCOMP): (Classification.NEEDS_REVIEW, None),
}


def normalize_label(value: str | None) -> str:
    """For comparing CAT/Estágio with the rules only - never stored."""
    return " ".join(unicodedata.normalize("NFKC", value or "").split()).casefold()


@dataclass(frozen=True)
class StageClassification:
    status: Classification
    cat: str | None              # as written in the file
    stage: str | None            # as written in the file
    target: StageRef | None      # where the card would go; None until decided
    proposed: StageRef | None    # suggested target awaiting confirmation (NEEDS_CONFIRMATION)
    notes: tuple[ReadIssue, ...] = ()


def _resolve_target(target: str | None, stages: Sequence[StageRef]) -> StageRef | None:
    if target is None:
        return None
    if target in (TARGET_WON, TARGET_LOST):
        matches = [s for s in stages if s.stage_kind == target]
    else:
        matches = [s for s in stages if s.title == target and s.stage_kind == "open"]
    return matches[0] if len(matches) == 1 else None


def classify_stage(cat: str | None, stage: str | None, stages: Sequence[StageRef], row: int | None = None) -> StageClassification:
    """Apply the approved rules to one row's CAT and Estágio."""
    cat_key, stage_key = normalize_label(cat), normalize_label(stage)
    notes = []
    if cat_key == CAT_OPEN_CE:
        notes.append(ReadIssue(
            Severity.WARNING, "open_ce_unconfirmed",
            'CAT "Open CE" is kept apart from OPEN; its meaning is not confirmed yet', row=row, field="cat",
        ))
    if cat_key not in KNOWN_CATS or stage_key not in KNOWN_STAGES:
        unknown = "CAT" if cat_key not in KNOWN_CATS else "Estágio"
        notes.append(ReadIssue(
            Severity.WARNING, "unknown_combination", f"No rule knows this {unknown} value", row=row,
        ))
        return StageClassification(Classification.UNKNOWN, cat, stage, None, None, tuple(notes))
    rule = STAGE_RULES.get((cat_key, stage_key))
    if rule is None:
        notes.append(ReadIssue(
            Severity.WARNING, "inconsistent_combination", "CAT and Estágio contradict each other", row=row,
        ))
        return StageClassification(Classification.INCONSISTENT, cat, stage, None, None, tuple(notes))
    status, target_name = rule
    target = _resolve_target(target_name, stages)
    if target_name is not None and target is None:
        notes.append(ReadIssue(
            Severity.WARNING, "target_stage_missing", f'The board has no single "{target_name}" stage', row=row,
        ))
        return StageClassification(Classification.NEEDS_REVIEW, cat, stage, None, None, tuple(notes))
    if status is Classification.NEEDS_CONFIRMATION:
        notes.append(ReadIssue(
            Severity.WARNING, "stage_needs_confirmation",
            f'CLOSED + NOT AWARDED BY CUSTOMER: confirm before classifying as "{target.title}"', row=row,
        ))
        return StageClassification(status, cat, stage, None, target, tuple(notes))
    if status is Classification.NEEDS_REVIEW:
        notes.append(ReadIssue(
            Severity.WARNING, "stage_needs_review", "CLOSED (BY SALCOMP) has no automatic stage", row=row,
        ))
        return StageClassification(status, cat, stage, None, None, tuple(notes))
    return StageClassification(status, cat, stage, target, None, tuple(notes))


# --- source identity ----------------------------------------------------------------------

def _key_part(value: str) -> str:
    """NFKD, accents (combining marks) dropped, whitespace collapsed, casefolded.
    "São  Paulo" and "SAO PAULO" give the same part; leading zeros stay."""
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(unicodedata.normalize("NFC", without_marks).split()).casefold()


def bid_source_key(code: str, customer: str) -> str:
    """Deterministic identity of a Bid: Código + Cliente.

    Both parts are normalized with _key_part, then encoded as a JSON array, so
    no separator inside either value can make two different pairs collide
    ("A|B" + "C" vs "A" + "B|C"). Prefixed with a version so the rule can
    change later without silently matching old keys.
    """
    parts = [_key_part(code or ""), _key_part(customer or "")]
    if not all(parts):
        raise ValueError("A Bid source key needs both a code and a customer")
    return f"{SOURCE_KEY_VERSION}:" + json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def _canonical(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def bid_row_hash(record: BidRecord) -> str:
    """SHA-256 of every value the reader produced for the row (except its
    position in the file), to tell an unchanged re-import from a changed one."""
    payload = {
        f.name: _canonical(getattr(record, f.name))
        for f in dataclass_fields(record)
        if f.name not in ("row_number", "code_needs_review")
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"{ROW_HASH_VERSION}:" + sha256(text.encode("utf-8")).hexdigest()


# --- field mapping --------------------------------------------------------------------------

# Model column <- BidRecord field. Card fields not listed (deal_value, currency,
# expected_close_date, lead_source, product_line, contact, salesperson_id) are
# never set by the import.
CARD_FIELDS = {
    "title": "project_name",
    "details": "prospect_description",
    "company": "customer",
}
BID_DETAILS_FIELDS = {
    "plant": "plant",
    "customer_type": "customer_type",
    "business_type": "business_type",
    "product_category": "product_category",
    "product": "product",
    "solution_type": "solution_type",
    "volume_current_year": "volume_current_year",
    "volume_next_year": "volume_next_year",
    "production_start_date": "production_start_date",
    "revenue_current_year_cents": "revenue_current_year_cents",
    "revenue_next_year_cents": "revenue_next_year_cents",
    "revenue_currency": "revenue_currency",
    "status_note": "status_note",
    "project_lead": "project_lead",
    "cpm": "cpm",
    "next_step": "next_step",
    "next_step_due": "next_step_due",
    "close_reason": "close_reason",
    "close_reason_note": "close_reason_note",
}
SOURCE_REF_FIELDS = {
    "source_code": "code",
    "source_status": "cat",
    "source_stage": "stage",
    "source_created_at": "source_created_on",
    "source_updated_at": "source_updated_on",
    "source_last_edited_at": "source_last_edited_on",
}
# Read but with no agreed destination yet: shown in the preview, never imported.
NOT_MAPPED_FIELDS = ("year", "budget_enabled", "budget")


@dataclass(frozen=True)
class ImportFields:
    """What the import would write, keyed by model column (None = no value)."""
    card: dict
    bid_details: dict
    source_ref: dict
    not_mapped: dict

    def flat(self) -> dict:
        return {
            **{f"card.{k}": v for k, v in self.card.items()},
            **{f"bid_details.{k}": v for k, v in self.bid_details.items()},
            **{f"source_ref.{k}": v for k, v in self.source_ref.items()},
        }


def map_fields(record: BidRecord, source_key: str, row_hash: str) -> ImportFields:
    take = lambda mapping: {column: getattr(record, attr) for column, attr in mapping.items()}
    return ImportFields(
        card=take(CARD_FIELDS),
        bid_details=take(BID_DETAILS_FIELDS),
        source_ref={
            "source_system": SOURCE_SYSTEM,
            "source_key": source_key,
            **take(SOURCE_REF_FIELDS),
            "source_row_hash": row_hash,
        },
        not_mapped={attr: getattr(record, attr) for attr in NOT_MAPPED_FIELDS},
    )


# --- inputs from the caller -------------------------------------------------------------------

@dataclass(frozen=True)
class ExistingImport:
    """A card already imported from this source, as the caller loaded it.
    `fields` uses ImportFields.flat() keys ("card.title", "bid_details.plant",
    "source_ref.source_row_hash", "source_ref.source_updated_at", ...)."""
    source_key: str
    card_id: int
    stage_title: str | None
    fields: Mapping[str, object]


@dataclass(frozen=True)
class RowDecision:
    """An explicit choice for one row of this file (by Excel row number)."""
    row_number: int
    target_stage: str | None = None        # board column title to classify the row into
    confirm_proposed_stage: bool = False   # accept a NEEDS_CONFIRMATION proposal
    skip: bool = False                     # leave the row out of the import
    confirm_update: bool = False           # accept updating an existing card
    confirm_move: bool = False             # accept moving an existing card to the new stage


class Action(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    IGNORE = "ignore"
    REVIEW = "review"


class DecisionNeeded(str, Enum):
    FIX_SOURCE_ROW = "fix_source_row"        # invalid row: correct the file or skip it
    CONFIRM_STAGE = "confirm_stage"          # confirm the proposed stage or choose one
    CHOOSE_STAGE = "choose_stage"            # choose a stage (no automatic one)
    RESOLVE_DUPLICATE = "resolve_duplicate"  # skip all but one row with the same key
    CONFIRM_UPDATE = "confirm_update"
    CONFIRM_MOVE = "confirm_move"


@dataclass(frozen=True)
class FieldChange:
    field: str      # ImportFields.flat() key
    current: object
    incoming: object


# --- output ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class PreviewRow:
    row_number: int
    code: str | None
    customer: str | None
    project_name: str | None
    source_status: str | None          # CAT as written
    source_stage: str | None           # Estágio as written
    source_key: str | None
    classification: StageClassification
    action: Action
    reasons: tuple[ReadIssue, ...]     # why this action (and what blocks importing)
    errors: tuple[ReadIssue, ...]      # from the reader
    warnings: tuple[ReadIssue, ...]    # reader + classification
    required_decisions: tuple[DecisionNeeded, ...]
    fields: ImportFields | None        # None when the row couldn't be keyed
    duplicate_rows: tuple[int, ...] = ()       # other rows of this file with the same key
    existing_card_id: int | None = None
    changed_fields: tuple[FieldChange, ...] = ()
    stage_change: tuple[str | None, str] | None = None  # (current column, proposed column)
    move_confirmed: bool = False

    @property
    def target_stage(self) -> str | None:
        return self.classification.target.title if self.classification.target else None


@dataclass(frozen=True)
class PreviewSummary:
    total_rows: int = 0
    valid_rows: int = 0
    rows_with_errors: int = 0
    rows_with_warnings: int = 0
    proposed_creates: int = 0           # importable new cards, nothing pending
    proposed_updates: int = 0           # changed existing cards (each needs confirm_update until given)
    unconfirmed_updates: int = 0
    unchanged: int = 0
    internal_duplicate_rows: int = 0
    pending_review: int = 0
    ignored: int = 0                    # unchanged + skipped + older source
    by_target_stage: dict = field(default_factory=dict)    # column title (or "none") -> rows
    by_classification: dict = field(default_factory=dict)  # Classification value -> rows
    by_action: dict = field(default_factory=dict)
    # Revenue of create/update rows, per currency, current and next year apart, in cents.
    revenue_cents_by_currency: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ImportPreview:
    file: FileMetadata
    rows: tuple[PreviewRow, ...] = ()
    file_errors: tuple[ReadIssue, ...] = ()
    summary: PreviewSummary = field(default_factory=PreviewSummary)
    written: bool = False  # always False: building a preview never writes anything
    notice: str = "Preview only - nothing was written."

    @property
    def ok(self) -> bool:
        return not self.file_errors


# --- engine -----------------------------------------------------------------------------------------

def _freshness(updated, last_edited) -> date | None:
    days = [d.date() if isinstance(d, datetime) else d for d in (updated, last_edited) if d is not None]
    return max(days) if days else None


def _comparable(value):
    return value.date() if isinstance(value, datetime) else value


def _changes(incoming: ImportFields, existing: ExistingImport) -> tuple[FieldChange, ...]:
    skip = {"source_ref.source_row_hash", "source_ref.source_key", "source_ref.source_system"}
    return tuple(
        FieldChange(name, existing.fields.get(name), value)
        for name, value in incoming.flat().items()
        if name not in skip and _comparable(existing.fields.get(name)) != _comparable(value)
    )


def _issue(code: str, message: str, row: int, severity=Severity.WARNING) -> ReadIssue:
    return ReadIssue(severity, code, message, row=row)


def _validate_inputs(read_result, stages, existing, decisions):
    kinds = Counter(s.stage_kind for s in stages)
    if kinds["won"] != 1 or kinds["lost"] != 1:
        raise ValueError("The board must have exactly one won and one lost stage")
    keys = Counter(e.source_key for e in existing)
    if any(n > 1 for n in keys.values()):
        raise ValueError("Existing imports must have unique source keys")
    rows = {row.row_number for row in read_result.rows}
    titles = {s.title for s in stages}
    by_row = {}
    for decision in decisions:
        if decision.row_number not in rows:
            raise ValueError(f"Decision for row {decision.row_number}, which is not in the file")
        if decision.row_number in by_row:
            raise ValueError(f"More than one decision for row {decision.row_number}")
        if decision.target_stage is not None and decision.target_stage not in titles:
            raise ValueError(f"Decision for row {decision.row_number}: no stage titled {decision.target_stage!r}")
        by_row[decision.row_number] = decision
    return by_row


def _apply_stage_decision(classification: StageClassification, decision: RowDecision | None, stages) -> StageClassification:
    if decision is None:
        return classification
    if decision.target_stage is not None:
        [target] = [s for s in stages if s.title == decision.target_stage]
        return StageClassification(Classification.DECIDED, classification.cat, classification.stage, target, None, classification.notes)
    if decision.confirm_proposed_stage and classification.proposed is not None:
        return StageClassification(
            Classification.DECIDED, classification.cat, classification.stage, classification.proposed, None, classification.notes,
        )
    return classification


def build_import_preview(
    read_result: BidReadResult,
    stages: Sequence[StageRef],
    existing: Iterable[ExistingImport] = (),
    decisions: Iterable[RowDecision] = (),
) -> ImportPreview:
    """Propose an action for every row of a read Bids report. Pure: no I/O."""
    if not read_result.ok:
        return ImportPreview(file=read_result.file, file_errors=read_result.file_errors)
    existing = tuple(existing)
    decisions = _validate_inputs(read_result, stages, existing, tuple(decisions))
    existing_by_key = {e.source_key: e for e in existing}

    # Keys first: duplicates are judged among the rows that aren't skipped.
    keyed = {}
    for row in read_result.rows:
        record = row.record
        if record.code and record.customer:
            keyed[row.row_number] = bid_source_key(record.code, record.customer)
    groups = defaultdict(list)
    for row_number, key in keyed.items():
        if not (decisions.get(row_number) and decisions[row_number].skip):
            groups[key].append(row_number)

    preview_rows = []
    for row in read_result.rows:
        n, record = row.row_number, row.record
        decision = decisions.get(n)
        key = keyed.get(n)
        rule_classification = classify_stage(record.cat, record.stage, stages, row=n)
        classification = _apply_stage_decision(rule_classification, decision, stages)
        fields_ = map_fields(record, key, bid_row_hash(record)) if key else None
        reasons, needed = [], []
        extra = {}

        if decision and decision.skip:
            action = Action.IGNORE
            reasons.append(_issue("skipped_by_decision", "Left out by an explicit decision", n))
        elif not row.is_valid:
            action = Action.REVIEW
            reasons.append(_issue("invalid_row", "The row has reading errors; fix the file or skip the row", n, Severity.ERROR))
            needed.append(DecisionNeeded.FIX_SOURCE_ROW)
        elif record.code_needs_review:
            action = Action.REVIEW
            reasons.append(_issue("code_needs_review", "The code may have lost leading zeros; fix the file or skip the row", n))
            needed.append(DecisionNeeded.FIX_SOURCE_ROW)
        elif not record.project_name:
            action = Action.REVIEW
            reasons.append(_issue("missing_title", "No project name: a card needs a title", n))
            needed.append(DecisionNeeded.FIX_SOURCE_ROW)
        elif len(groups[key]) > 1:
            action = Action.REVIEW
            others = tuple(r for r in groups[key] if r != n)
            extra["duplicate_rows"] = others
            reasons.append(_issue(
                "duplicate_key", f"Same Código + Cliente as row(s) {', '.join(map(str, others))}; skip all but one", n,
            ))
            needed.append(DecisionNeeded.RESOLVE_DUPLICATE)
        else:
            action = _resolve_action(n, key, fields_, classification, decision, existing_by_key, reasons, needed, extra)

        problems = _decision_problems(decision, rule_classification, needed, extra) if decision else []
        if problems:
            # A decision that doesn't fit the row is shown, never silently ignored
            # or turned into another action; the import refuses the whole file.
            action = Action.REVIEW
            reasons.extend(_issue("decision_not_applicable", problem, n, Severity.ERROR) for problem in problems)

        preview_rows.append(PreviewRow(
            row_number=n,
            code=record.code,
            customer=record.customer,
            project_name=record.project_name,
            source_status=record.cat,
            source_stage=record.stage,
            source_key=key,
            classification=classification,
            action=action,
            reasons=tuple(reasons),
            errors=row.errors,
            warnings=row.warnings + classification.notes,
            required_decisions=tuple(needed),
            fields=fields_,
            **extra,
        ))
    return ImportPreview(file=read_result.file, rows=tuple(preview_rows), summary=_summarize(preview_rows))


def _decision_problems(decision: RowDecision, rule: StageClassification, needed, extra) -> list[str]:
    """Why `decision` doesn't fit this row (empty when it does)."""
    problems = []
    if decision.skip:
        if decision.target_stage or decision.confirm_proposed_stage or decision.confirm_update or decision.confirm_move:
            problems.append("Skip can't be combined with other choices for the same row")
        return problems
    if decision.target_stage and decision.confirm_proposed_stage:
        problems.append("Choose a stage or confirm the proposed one, not both")
    if decision.confirm_proposed_stage and rule.status is not Classification.NEEDS_CONFIRMATION:
        problems.append("There is no proposed stage to confirm for this row")
    if (decision.target_stage or decision.confirm_proposed_stage) and DecisionNeeded.FIX_SOURCE_ROW in needed:
        problems.append("A stage can't be chosen for a row that must be fixed in the file or skipped")
    if decision.confirm_update and "changed_fields" not in extra:
        problems.append("There is no changed, already-imported card to update for this row")
    if decision.confirm_move and "stage_change" not in extra:
        problems.append("There is no stage change to confirm for this row")
    elif decision.confirm_move and DecisionNeeded.CONFIRM_UPDATE in needed:
        problems.append("A move can only be confirmed together with the update")
    return problems


def _stage_decision_needed(classification: StageClassification) -> DecisionNeeded | None:
    if classification.target is not None:
        return None
    if classification.status is Classification.NEEDS_CONFIRMATION:
        return DecisionNeeded.CONFIRM_STAGE
    return DecisionNeeded.CHOOSE_STAGE


def _resolve_action(n, key, fields_, classification, decision, existing_by_key, reasons, needed, extra) -> Action:
    stage_needed = _stage_decision_needed(classification)
    current = existing_by_key.get(key)
    if current is None:
        if stage_needed:
            reasons.append(_issue("stage_not_determined", "No stage yet; an explicit decision is required", n))
            needed.append(stage_needed)
            return Action.REVIEW
        reasons.append(_issue("new_record", "Not imported before", n))
        return Action.CREATE

    extra["existing_card_id"] = current.card_id
    incoming_hash = fields_.source_ref["source_row_hash"]
    if current.fields.get("source_ref.source_row_hash") == incoming_hash:
        reasons.append(_issue("unchanged", "Same content as the last import", n))
        return Action.IGNORE

    extra["changed_fields"] = _changes(fields_, current)
    incoming_date = _freshness(fields_.source_ref["source_updated_at"], fields_.source_ref["source_last_edited_at"])
    current_date = _freshness(
        current.fields.get("source_ref.source_updated_at"), current.fields.get("source_ref.source_last_edited_at"),
    )
    if stage_needed:
        reasons.append(_issue("stage_not_determined", "Changed, but its stage needs a decision first", n))
        needed.append(stage_needed)
        return Action.REVIEW
    # Without confirm_update: newer -> UPDATE awaiting confirmation; older -> IGNORE
    # (never overwritten); undecidable dates -> REVIEW. With it, all three update.
    confirmed = bool(decision and decision.confirm_update)
    if incoming_date is None or current_date is None or incoming_date == current_date:
        code, message, unconfirmed = (
            "ambiguous_dates", "Content changed but the source dates can't tell which version is newer", Action.REVIEW,
        )
    elif incoming_date < current_date:
        code, message, unconfirmed = (
            "older_source", "This file is older than the last import; not overwriting", Action.IGNORE,
        )
    else:
        code, message, unconfirmed = (
            "newer_source", "Newer source with changes; update after confirmation", Action.UPDATE,
        )
    reasons.append(_issue(code, message, n))
    if not confirmed:
        needed.append(DecisionNeeded.CONFIRM_UPDATE)
        if unconfirmed is not Action.UPDATE:
            return unconfirmed

    target = classification.target.title
    if current.stage_title != target:
        extra["stage_change"] = (current.stage_title, target)
        extra["move_confirmed"] = bool(decision and decision.confirm_move)
        reasons.append(_issue(
            "stage_change", "The source points to another stage; the card only moves with explicit confirmation", n,
        ))
        if not extra["move_confirmed"]:
            needed.append(DecisionNeeded.CONFIRM_MOVE)
    return Action.UPDATE


def _summarize(rows: list[PreviewRow]) -> PreviewSummary:
    revenue = defaultdict(lambda: {"current_year": 0, "next_year": 0})
    for row in rows:
        if row.action in (Action.CREATE, Action.UPDATE) and row.fields:
            details = row.fields.bid_details
            totals = revenue[details["revenue_currency"]]
            totals["current_year"] += details["revenue_current_year_cents"] or 0
            totals["next_year"] += details["revenue_next_year_cents"] or 0
    return PreviewSummary(
        total_rows=len(rows),
        valid_rows=sum(not row.errors for row in rows),
        rows_with_errors=sum(bool(row.errors) for row in rows),
        rows_with_warnings=sum(bool(row.warnings) for row in rows),
        proposed_creates=sum(row.action is Action.CREATE for row in rows),
        proposed_updates=sum(row.action is Action.UPDATE for row in rows),
        unconfirmed_updates=sum(
            row.action is Action.UPDATE and DecisionNeeded.CONFIRM_UPDATE in row.required_decisions for row in rows
        ),
        unchanged=sum(any(r.code == "unchanged" for r in row.reasons) for row in rows),
        internal_duplicate_rows=sum(bool(row.duplicate_rows) for row in rows),
        pending_review=sum(row.action is Action.REVIEW for row in rows),
        ignored=sum(row.action is Action.IGNORE for row in rows),
        by_target_stage=dict(Counter(row.target_stage or "none" for row in rows)),
        by_classification=dict(Counter(row.classification.status.value for row in rows)),
        by_action=dict(Counter(row.action.value for row in rows)),
        revenue_cents_by_currency={currency: dict(totals) for currency, totals in revenue.items()},
    )
