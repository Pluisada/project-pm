"""Apply a confirmed Salcomp Bids import preview, in one transaction (Part 22D).

    preview = preview_bid_import(db, read_result, board_id, decisions)   # read-only
    ... the user reviews it and confirms ...
    outcome = apply_bid_import(db, read_result, preview, decisions, board_id, user)

apply_bid_import() trusts nothing it receives. Inside the transaction - after
inserting the import batch, which takes SQLite's write lock, so no other
writer can change the data in between - it rebuilds the preview from the
current database and the same decisions, and refuses unless that is exactly
the preview the user confirmed (a concurrent import, an edited card or a
second click on "confirm" all show up as differences). Only then does it
write, and only:
- CREATE rows: card in the decided stage + card_bid_details + card_source_refs;
- UPDATE rows whose update was confirmed: only the mapped fields; the card
  moves only if the move was confirmed too.
IGNORE and REVIEW rows (and unconfirmed updates) are only counted.
A preview whose rows are all IGNORE (a repeat of an import that changed nothing)
is a no-op: the transaction is rolled back, so no batch, ledger or event is
left behind - a "completed" batch with nothing in it would make an earlier
batch's ledger revert wait for it (later_batch_decision_on_card).

Everything - batch, cards, details, source refs, stage events, counts - is
committed together or rolled back together; on any failure nothing remains.
Stage events come from stage_history.py's listener as always: with the session's
source set to "import", a new card gets a "baseline" event (not "created": the
import is not when the opportunity was created), and a confirmed move gets the
usual moved/won/lost event, dated now.
"""
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, time

from sqlalchemy import func
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bid_excel_reader import BidReadResult
from bid_import_preview import (
    BID_DETAILS_FIELDS,
    CARD_FIELDS,
    ROW_HASH_VERSION,
    SOURCE_KEY_VERSION,
    SOURCE_REF_FIELDS,
    SOURCE_SYSTEM,
    STAGE_RULES,
    Action,
    DecisionNeeded,
    ExistingImport,
    ImportPreview,
    RowDecision,
    StageRef,
    bid_source_key,
    build_import_preview,
)
from bid_import_ledger import LEDGER_VERSION, card_snapshot, snapshot_json
from models import (
    Board, BoardColumn, Card, CardBidDetails, CardImportChange, CardSourceRef, CardStageEvent, ImportBatch,
    ImportBatchItem, ImportBatchLedger, User,
)
from stage_history import SOURCE_IMPORT, stage_context, utcnow

SOURCE_REF_COLUMNS = ("source_system", "source_key", *SOURCE_REF_FIELDS, "source_row_hash")
FAILURE_VALIDATION, FAILURE_CONFLICT, FAILURE_UNEXPECTED = "validation", "conflict", "unexpected"

logger = logging.getLogger(__name__)


class ImportRejected(ValueError):
    """The request can't be applied as given (bad input or decisions). Nothing was written."""


class ImportConflict(RuntimeError):
    """The data changed since the preview, or another import got there first. Nothing was written."""


@dataclass(frozen=True)
class ImportOutcome:
    batch_id: int | None   # None for a no-op: nothing was written, so no batch exists
    created_card_ids: tuple[int, ...]
    updated_card_ids: tuple[int, ...]
    moved_card_ids: tuple[int, ...]
    skipped: int   # IGNORE rows: unchanged, older source, skipped by decision
    review: int    # REVIEW rows and updates still awaiting confirmation
    no_op: bool = False  # every row was IGNORE: rolled back, no batch, no ledger, no events


# --- reading the current state (no writes) ------------------------------------------------

def load_board_stages(db: Session, board_id: int) -> list[StageRef]:
    columns = db.query(BoardColumn).filter(BoardColumn.board_id == board_id).order_by(BoardColumn.position).all()
    return [StageRef(c.id, c.title, c.stage_kind) for c in columns]


def _source_keys(read_result: BidReadResult) -> set[str]:
    return {
        bid_source_key(row.record.code, row.record.customer)
        for row in read_result.rows
        if row.record.code and row.record.customer
    }


def load_existing_imports(db: Session, source_keys, board_id: int) -> list[ExistingImport]:
    """The already-imported cards for these keys, in ExistingImport form.
    A key imported into another board can't be updated from here: ImportConflict."""
    if not source_keys:
        return []
    rows = (
        db.query(CardSourceRef, Card, BoardColumn)
        .join(Card, CardSourceRef.card_id == Card.id)
        .join(BoardColumn, Card.column_id == BoardColumn.id)
        .filter(CardSourceRef.source_system == SOURCE_SYSTEM, CardSourceRef.source_key.in_(sorted(source_keys)))
        .order_by(CardSourceRef.id)
        .all()
    )
    elsewhere = sorted({(column.board_id, ref.source_code) for ref, _card, column in rows if column.board_id != board_id})
    if elsewhere:
        # source_system + source_key is unique across all boards: a Bid lives on one board only.
        listed = "; ".join(f"Código {code} on board {other}" for other, code in elsewhere)
        raise ImportConflict(
            f"{len(elsewhere)} Bid(s) in this file were already imported into another board ({listed}); "
            "a Bid can only be imported into one board - import into that board, or remove those rows"
        )
    existing = []
    for ref, card, column in rows:
        details = card.bid_details
        fields = {f"card.{name}": getattr(card, name) for name in CARD_FIELDS}
        fields.update({f"bid_details.{name}": getattr(details, name) if details else None for name in BID_DETAILS_FIELDS})
        fields.update({f"source_ref.{name}": getattr(ref, name) for name in SOURCE_REF_COLUMNS})
        existing.append(ExistingImport(ref.source_key, card.id, column.title, fields))
    return existing


def preview_bid_import(db: Session, read_result: BidReadResult, board_id: int, decisions=()) -> ImportPreview:
    """The preview against the current database. Read-only."""
    if db.get(Board, board_id) is None:
        raise ImportRejected("Board not found")
    stages = load_board_stages(db, board_id)
    existing = load_existing_imports(db, _source_keys(read_result), board_id) if read_result.ok else []
    try:
        return build_import_preview(read_result, stages, existing, tuple(decisions))
    except ValueError as exc:
        raise ImportRejected(str(exc)) from None


# --- applying -------------------------------------------------------------------------------

def _as_datetime(value):
    """card_source_refs dates are DATETIME columns; the report only has days."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min)
    return value


def _source_ref_values(fields) -> dict:
    return {name: _as_datetime(value) for name, value in fields.source_ref.items()}


def _audit_value(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _audit_snapshot(managed) -> dict:
    """{"card.title": value, "bid_details.plant": value, ...} for the fields the
    import manages, as JSON values - read from the objects themselves, so the
    history shows what really changed in the database."""
    return {
        f"{prefix}.{name}": _audit_value(getattr(obj, name))
        for prefix, obj, fields in managed
        for name in fields
    }


def _mapping_json(stages: list[StageRef], decisions: tuple[RowDecision, ...], operator: str | None = None) -> str:
    """How this batch was mapped: rule versions, rules, board stages and the
    explicit decisions - and, for a run from a command line, the technical
    operator (the batch's user_id is always the application administrator it
    ran as). No row content."""
    return json.dumps({
        **({"operator": operator} if operator else {}),
        "source_system": SOURCE_SYSTEM,
        "source_key_version": SOURCE_KEY_VERSION,
        "row_hash_version": ROW_HASH_VERSION,
        "stage_rules": [[cat, stage, status.value, target] for (cat, stage), (status, target) in STAGE_RULES.items()],
        "stages": [asdict(stage) for stage in stages],
        "decisions": [asdict(decision) for decision in decisions],
    }, ensure_ascii=False, sort_keys=True)


def apply_bid_import(
    db: Session,
    read_result: BidReadResult,
    preview: ImportPreview,
    decisions,
    board_id: int,
    user: User,
    operator: str | None = None,
) -> ImportOutcome:
    """Write the confirmed preview in one transaction, or nothing at all.

    A failure is re-raised after the rollback and, when its board exists,
    recorded as a "failed" import_batches row in a separate transaction
    (record_failed_import). A completed import is never recorded as failed.
    """
    decisions = tuple(decisions)
    # Read before anything else, without autoflush: loading an expired user
    # later would flush the caller's pending changes and take the write lock.
    with db.no_autoflush:
        user_id = user.id if user is not None else None
    try:
        return _apply(db, read_result, preview, decisions, board_id, user, operator)
    except Exception as exc:
        record_failed_import(db, read_result, decisions, board_id, user_id, *_failure(exc), operator=operator)
        raise


def _failure(exc: Exception) -> tuple[str, str]:
    """(failure_kind, failure_reason) for a failed import."""
    if isinstance(exc, ImportRejected):
        return FAILURE_VALIDATION, str(exc)
    if isinstance(exc, ImportConflict):
        return FAILURE_CONFLICT, str(exc)
    # Only the exception type: its message may quote SQL parameters (row content).
    return FAILURE_UNEXPECTED, type(exc).__name__


def record_failed_import(
    db: Session, read_result, decisions, board_id, user_id, kind: str, reason: str, operator: str | None = None
) -> int | None:
    """Record a failed attempt in its own short transaction, on a separate
    session, after the import was rolled back. Returns the batch id, or None
    when nothing could be recorded (no such board, or the recording itself
    failed - which is logged by type only and never hides the original error).
    """
    bind = db.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    try:
        with Session(engine) as session, session.begin():
            if session.get(Board, board_id) is None:
                return None
            if user_id is not None and session.get(User, user_id) is None:
                user_id = None
            batch = ImportBatch(
                board_id=board_id,
                user_id=user_id,
                filename=read_result.file.filename,
                file_sha256=read_result.file.sha256,
                mapping_json=_mapping_json(load_board_stages(session, board_id), tuple(decisions), operator),
                status="failed",
                failure_kind=kind,
                failure_reason=reason[:500],
                completed_at=utcnow(),
            )
            session.add(batch)
            session.flush()
            return batch.id
    except Exception as exc:
        logger.warning("Could not record a failed Bid import attempt (%s)", type(exc).__name__)
        return None


def _apply(db: Session, read_result, preview, decisions, board_id, user, operator=None) -> ImportOutcome:
    if db.new or db.dirty or db.deleted:
        raise ImportRejected("The session has unsaved changes; apply an import in a clean session")
    if not read_result.ok or not preview.ok:
        raise ImportRejected("The file has errors; nothing can be imported")
    if preview.file.sha256 != read_result.file.sha256:
        raise ImportRejected("The preview belongs to another file")

    try:
        with stage_context(db, user, SOURCE_IMPORT):
            if db.get(Board, board_id) is None:
                raise ImportRejected("Board not found")
            if user is None or db.get(User, user.id) is None:
                raise ImportRejected("Unknown user")
            stages = load_board_stages(db, board_id)
            batch = ImportBatch(
                board_id=board_id,
                user_id=user.id,
                filename=read_result.file.filename,
                file_sha256=read_result.file.sha256,
                mapping_json=_mapping_json(stages, decisions, operator),
                status="pending",
            )
            db.add(batch)
            db.flush()  # first write: from here SQLite holds the write lock until commit/rollback

            current = preview_bid_import(db, read_result, board_id, decisions)
            if current != preview:
                raise ImportConflict("The data changed since the preview (or it was already applied); build it again")
            unfit = [row.row_number for row in current.rows if any(r.code == "decision_not_applicable" for r in row.reasons)]
            if unfit:
                raise ImportRejected(f"Decisions that don't fit rows {', '.join(map(str, unfit))}")

            if all(row.action is Action.IGNORE for row in current.rows):
                db.rollback()  # drops the pending batch; nothing else was written
                return ImportOutcome(None, (), (), (), len(current.rows), 0, no_op=True)

            outcome = _write_rows(db, batch, current, stages, board_id)
            batch.created_count = len(outcome.created_card_ids)
            batch.updated_count = len(outcome.updated_card_ids)
            batch.skipped_count = outcome.skipped
            batch.review_count = outcome.review
            batch.status = "completed"
            batch.completed_at = utcnow()
            db.flush()
        db.commit()
        return outcome
    except IntegrityError as exc:
        db.rollback()
        raise ImportConflict("Another import wrote the same Bid first; nothing was written") from exc
    except BaseException:
        db.rollback()
        raise


def _existing_card_id(db: Session, row) -> int | None:
    """A row left out by decision never reached the preview's lookup; find the card it points at."""
    if not row.source_key:
        return None
    return (
        db.query(CardSourceRef.card_id)
        .filter(CardSourceRef.source_system == SOURCE_SYSTEM, CardSourceRef.source_key == row.source_key)
        .scalar()
    )


def _ignore_observation(row) -> str:
    codes = {reason.code for reason in row.reasons}
    for observation in ("skipped_by_decision", "unchanged", "older_source"):
        if observation in codes:
            return observation
    return "unchanged"


def _write_ledger(db: Session, batch: ImportBatch, board_id, touched, observed) -> None:
    """The batch's ledger, in the import's own transaction (after the final flush, so ids
    and stage events exist). A completed batch always gets one, even with no items."""
    max_event = {}
    if touched:
        max_event = dict(
            db.query(CardStageEvent.card_id, func.max(CardStageEvent.id))
            .filter(CardStageEvent.card_id.in_([card.id for card, *_ in touched]))
            .group_by(CardStageEvent.card_id)
            .all()
        )
    items = [
        ImportBatchItem(
            card_id=card.id, board_id=board_id, source_key=source_key, action=action,
            before_json=before, after_json=snapshot_json(card_snapshot(card)),
            bid_details_created=details_created, stage_event_max_id_after=max_event.get(card.id),
        )
        for card, action, source_key, before, details_created in touched
    ]
    taken = {item.card_id for item in items}
    items += [
        ImportBatchItem(card_id=card_id, board_id=board_id, source_key=source_key, action="observed", observation=observation)
        for card_id, (source_key, observation) in observed.items()
        if card_id not in taken
    ]
    db.add(ImportBatchLedger(import_batch=batch, ledger_version=LEDGER_VERSION, items=items))
    db.flush()


def _write_rows(db: Session, batch: ImportBatch, preview: ImportPreview, stages, board_id) -> ImportOutcome:
    columns = {stage.column_id: stage for stage in stages}
    next_position = {}

    def position_in(column_id):
        if column_id not in next_position:
            highest = db.query(func.max(Card.position)).filter(Card.column_id == column_id).scalar()
            next_position[column_id] = -1 if highest is None else highest
        next_position[column_id] += 1
        return next_position[column_id]

    created, updated, moved = [], [], []
    skipped = review = 0
    # Ledger (Part 23): (card, action, source_key, before snapshot, bid_details_created) for created and
    # updated cards, and {card_id: (source_key, observation)} for existing cards the batch only looked at.
    touched = []
    observed = {}

    def observe(card_id, source_key, observation):
        if card_id is not None and source_key:
            observed.setdefault(card_id, (source_key, observation))

    for row in preview.rows:
        if row.action is Action.CREATE:
            target = row.classification.target
            # Invariants the preview guarantees, re-checked before writing.
            if row.required_decisions or target is None or target.column_id not in columns or row.existing_card_id:
                raise ImportRejected(f"Row {row.row_number} is not ready to be created")
            card = Card(column_id=target.column_id, position=position_in(target.column_id), **row.fields.card)
            card.bid_details = CardBidDetails(**row.fields.bid_details)
            card.source_ref = CardSourceRef(**_source_ref_values(row.fields), last_import_batch=batch)
            db.add(card)
            created.append(card)
            touched.append((card, "created", row.source_key, None, True))
        elif row.action is Action.UPDATE:
            if DecisionNeeded.CONFIRM_UPDATE in row.required_decisions:
                review += 1  # proposed, not confirmed: not an update
                observe(row.existing_card_id, row.source_key, "update_not_confirmed")
                continue
            card = db.get(Card, row.existing_card_id)
            ref = card.source_ref if card is not None else None
            if ref is None or ref.source_key != row.source_key or card.column.board_id != board_id:
                raise ImportConflict(f"Row {row.row_number}: the imported card changed identity")
            before_snapshot = snapshot_json(card_snapshot(card))
            details_created = card.bid_details is None
            details = card.bid_details or CardBidDetails(card=card)
            if details_created:
                db.add(details)  # a backref-only link is not cascaded into the session: without this the row was never saved
            source_values = _source_ref_values(row.fields)
            managed = (
                ("card", card, row.fields.card),
                ("bid_details", details, row.fields.bid_details),
                ("source_ref", ref, {k: v for k, v in source_values.items() if k != "source_row_hash"}),
            )
            before = _audit_snapshot(managed)
            for name, value in row.fields.card.items():
                setattr(card, name, value)
            for name, value in row.fields.bid_details.items():
                setattr(details, name, value)
            for name, value in source_values.items():
                setattr(ref, name, value)
            ref.last_import_batch = batch
            changes = {
                field: {"old": old, "new": new}
                for (field, old), new in zip(before.items(), _audit_snapshot(managed).values())
                if old != new
            }
            if changes:
                db.add(CardImportChange(
                    card=card, import_batch=batch, user_id=batch.user_id,
                    changes=json.dumps(changes, ensure_ascii=False, sort_keys=True),
                ))
            if row.stage_change and row.move_confirmed:
                target = row.classification.target
                if target is None or target.column_id not in columns:
                    raise ImportRejected(f"Row {row.row_number}: no target stage to move to")
                card.column_id = target.column_id
                card.position = position_in(target.column_id)
                moved.append(card)
            updated.append(card)
            touched.append((card, "updated", row.source_key, before_snapshot, details_created))
        elif row.action is Action.IGNORE:
            skipped += 1
            observe(row.existing_card_id or _existing_card_id(db, row), row.source_key, _ignore_observation(row))
        else:
            review += 1
            observe(row.existing_card_id, row.source_key, "update_not_confirmed")
    db.flush()  # ids, and the stage events, inside this transaction
    _write_ledger(db, batch, board_id, touched, observed)
    return ImportOutcome(
        batch_id=batch.id,
        created_card_ids=tuple(card.id for card in created),
        updated_card_ids=tuple(card.id for card in updated),
        moved_card_ids=tuple(card.id for card in moved),
        skipped=skipped,
        review=review,
    )
