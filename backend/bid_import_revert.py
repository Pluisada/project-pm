"""Plan and execute the revert of a confirmed Bid import (Part 23, step 3).

    plan = revert_preview(db, batch_id)                       # read-only
    ... an admin reviews it ...
    outcome = execute_revert(db, batch_id, admin, plan.plan_fingerprint)

The ledger written by bid_import_service (import_batch_items, canonical
snapshots from bid_import_ledger) is the only source of truth: the revert never
looks at card_import_changes. It is independent of the Kanban Undo - it creates
no UndoEntry and uses nothing from undo.py.

build_revert_plan() classifies every ledger item against the current database:
- created   delete the card, if nothing happened to it since (state equal to the
            after-snapshot, no edit history, no card actions, no later stage
            event other than those of imports and their reverts, no later
            batch that decided on it);
- updated   restore, field by field, only the fields the batch changed (current
            == new -> restore old; current == old -> already reverted; anything
            else -> conflict); a move goes back to the end of the origin column;
            a card_bid_details row the batch created is deleted if untouched;
- observed  nothing to undo, but still a dependency for earlier batches.
A card that is gone is "already_deleted" only when a stage event "deleted"
proves it; otherwise it blocks as "missing_without_trace". Any blocker blocks
the whole revert: there is no partial revert.

execute_revert() runs one transaction. Its first statement is an explicit write
(the claim on the ledger), so SQLite's write lock is held before any decision is
taken; it then rebuilds the plan, compares the fingerprint, writes, verifies the
result (commercial and structural, section 10 of docs/PLAN_PART23.md), stores the
outcome and counts on the ledger and commits once. Any failure rolls everything
back. Nothing is reported as done before commit() returned; a commit that fails
is re-checked from a fresh session (RevertBusy or RevertOutcomeUnconfirmed).
Stage events come from stage_history.py's listener with source "import_revert".
"""
import copy
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import DateTime, func, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload

from bid_import_ledger import card_snapshot
from models import (
    BoardColumn, Card, CardAction, CardBidDetails, CardEditHistory, CardImportChange, CardSourceRef, CardStageEvent,
    ImportBatch, ImportBatchItem, ImportBatchLedger, UndoEntry,
)
from stage_history import SOURCE_IMPORT, SOURCE_IMPORT_REVERT, stage_context, utcnow

FINGERPRINT_VERSION = 1
OUTCOME_REVERTED = "reverted"
OUTCOME_NO_COMMERCIAL_CHANGES = "no_commercial_changes"
MAX_LOGGED_CARD_IDS = 20

# Plan status
READY, BLOCKED, NOT_REVERTIBLE, ALREADY_REVERTED = "ready", "blocked", "not_revertible", "already_reverted"
# Item classification
DELETE, RESTORE, ALREADY_DELETED, ALREADY_REVERTED_ITEM, OBSERVED, BLOCKED_ITEM = (
    "delete", "restore", "already_deleted", "already_reverted", "observed", "blocked",
)
# Operations
OP_DELETE_CARD, OP_RESTORE_FIELDS, OP_MOVE_CARD, OP_DELETE_BID_DETAILS = (
    "delete_card", "restore_fields", "move_card", "delete_bid_details",
)

_LOCK_CODES = (5, 6)  # SQLITE_BUSY, SQLITE_LOCKED
logger = logging.getLogger("bid_import_revert")


# --- errors ---------------------------------------------------------------------------------------

class RevertError(Exception):
    """Base of the errors the route maps to HTTP statuses. `code` is the API error code."""
    code = "revert_error"


class RevertRefused(RevertError):
    """The revert was not applied (nothing written). `code` is one of: batch_not_found, not_revertible,
    already_reverted, plan_changed, revert_blocked. `plan` is the rebuilt plan when there is one."""

    def __init__(self, code: str, plan: "RevertPlan | None" = None):
        super().__init__(code)
        self.code, self.plan = code, plan


class RevertBusy(RevertError):
    """SQLITE_BUSY / SQLITE_LOCKED. Nothing was applied; retry later (503 import_busy, Retry-After 5).
    phase: preview | start | commit."""
    code = "import_busy"

    def __init__(self, phase: str):
        super().__init__(phase)
        self.phase, self.applied = phase, False


class RevertOutcomeUnconfirmed(RevertError):
    """The commit failed and the durable state could not be shown to be un-reverted (500)."""
    code = "revert_outcome_unconfirmed"


class RevertVerificationFailed(RuntimeError):
    """The post-write verification found a difference from the plan; everything was rolled back."""


# --- plan -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Blocker:
    code: str
    card_id: int | None = None
    field: str | None = None          # "card.title", "bid_details.next_step", ...
    imported: object = None           # the value the import left (for the conflict table)
    current: object = None
    batch_ids: tuple[int, ...] = ()   # later batches, for later_batch_decision_on_card


@dataclass(frozen=True)
class Operation:
    kind: str
    card_id: int
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "card_id": self.card_id, **self.data}


@dataclass(frozen=True)
class PlanItem:
    item_id: int
    card_id: int
    board_id: int
    source_key: str
    action: str                       # created | updated | observed
    observation: str | None
    classification: str
    operations: tuple[Operation, ...] = ()
    blockers: tuple[Blocker, ...] = ()


@dataclass(frozen=True)
class RevertPlan:
    batch_id: int
    status: str                       # ready | blocked | not_revertible | already_reverted
    reason: str | None
    ledger_version: int | None
    items: tuple[PlanItem, ...]
    blockers: tuple[Blocker, ...]
    dependent_batches: tuple[dict, ...]   # [{"batch_id", "card_ids"}]
    counts: dict
    no_commercial_changes: bool
    plan_fingerprint: str

    @property
    def operations(self) -> tuple[Operation, ...]:
        return tuple(op for item in self.items for op in item.operations)


@dataclass(frozen=True)
class RevertOutcome:
    batch_id: int
    outcome: str                      # reverted | no_commercial_changes
    no_commercial_changes: bool
    cards_deleted: int
    cards_restored: int
    moves_reverted: int
    already_deleted: int
    bid_details_deleted: int
    already_reverted: int
    observed: int


def _flat(snapshot: dict) -> dict:
    """{"card.title": v, "bid_details.plant": v, "source_ref.source_key": v} for the comparable fields."""
    flat = {f"card.{k}": v for k, v in snapshot["card"].items()}
    details = snapshot["bid_details"]
    flat.update({f"bid_details.{k}": v for k, v in details.items() if k not in ("present", "id")})
    flat.update({f"source_ref.{k}": v for k, v in (snapshot["source_ref"] or {}).items()})
    return flat


def _later_batches(db: Session, batch_id: int, card_ids) -> dict[int, tuple[int, ...]]:
    """{card_id: later completed, not yet reverted batch ids with a ledger item (any action) for it}."""
    if not card_ids:
        return {}
    rows = (
        db.query(ImportBatchItem.card_id, ImportBatch.id)
        .join(ImportBatchLedger, ImportBatchItem.ledger_id == ImportBatchLedger.id)
        .join(ImportBatch, ImportBatchLedger.import_batch_id == ImportBatch.id)
        .filter(
            ImportBatchItem.card_id.in_(card_ids),
            ImportBatch.id > batch_id,
            ImportBatch.status == "completed",
            ImportBatchLedger.reverted_at.is_(None),
        )
        .all()
    )
    later: dict[int, set] = {}
    for card_id, other in rows:
        later.setdefault(card_id, set()).add(other)
    return {card_id: tuple(sorted(batches)) for card_id, batches in later.items()}


def _last_event_ids(db: Session, card_ids, exclude_sources=()) -> dict[int, int]:
    if not card_ids:
        return {}
    query = db.query(CardStageEvent.card_id, func.max(CardStageEvent.id)).filter(CardStageEvent.card_id.in_(card_ids))
    if exclude_sources:
        query = query.filter(CardStageEvent.source.notin_(exclude_sources))
    return dict(query.group_by(CardStageEvent.card_id).all())


def _group_count(db: Session, model, card_ids) -> dict[int, int]:
    return dict(
        db.query(model.card_id, func.count()).filter(model.card_id.in_(card_ids)).group_by(model.card_id).all()
    ) if card_ids else {}


def build_revert_plan(db: Session, batch_id: int, *, claimed: bool = False) -> RevertPlan:
    """The revert plan against the current database. Read-only.

    claimed=True is for execute_revert, which has already set reverted_at in its own
    transaction: that mark is then not "already reverted".
    Raises RevertRefused("batch_not_found") for an unknown batch; every other
    non-revertible case is a plan with a status and a reason.
    """
    db.expire_all()
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise RevertRefused("batch_not_found")
    ledger = db.query(ImportBatchLedger).filter(ImportBatchLedger.import_batch_id == batch_id).one_or_none()
    if batch.status != "completed":
        return _empty_plan(batch_id, NOT_REVERTIBLE, "batch_not_completed", ledger)
    if ledger is None:
        return _empty_plan(batch_id, NOT_REVERTIBLE, "no_ledger", ledger)
    if ledger.reverted_at is not None and not claimed:
        return _empty_plan(batch_id, ALREADY_REVERTED, None, ledger)

    items = sorted(ledger.items, key=lambda item: item.id)
    card_ids = [item.card_id for item in items]
    cards = {}
    if card_ids:
        cards = {
            card.id: card
            for card in db.query(Card)
            .options(selectinload(Card.bid_details), selectinload(Card.source_ref), selectinload(Card.column))
            .filter(Card.id.in_(card_ids))
        }
    absent = [card_id for card_id in card_ids if card_id not in cards]
    proven_deleted = set(
        db.scalars(
            select(CardStageEvent.card_id).where(CardStageEvent.card_id.in_(absent), CardStageEvent.event_type == "deleted")
        )
    ) if absent else set()
    edits = _group_count(db, CardEditHistory, list(cards))
    actions = _group_count(db, CardAction, list(cards))
    last_event = _last_event_ids(db, list(cards))
    # Events written by imports and by their reverts are not commercial activity by themselves: a later
    # batch that is still applied blocks through its ledger item, and a reverted one has compensated.
    last_activity = _last_event_ids(db, list(cards), exclude_sources=(SOURCE_IMPORT, SOURCE_IMPORT_REVERT))
    later = _later_batches(db, batch_id, [c for c in card_ids if c not in proven_deleted])
    column_ids = {
        old for item in items if item.action == "updated" and item.before_json
        for old in [json.loads(item.before_json)["card"]["column_id"]]
    }
    columns = {c.id: c.board_id for c in db.query(BoardColumn).filter(BoardColumn.id.in_(column_ids))} if column_ids else {}

    plan_items, markers = [], {}
    for item in items:
        card = cards.get(item.card_id)
        marker = None
        if card is not None:
            marker = [edits.get(card.id, 0), actions.get(card.id, 0), last_event.get(card.id, 0)]
        markers[item.id] = marker
        blockers: list[Blocker] = []
        ops: list[Operation] = []
        if item.action == "observed":
            classification = OBSERVED
        elif card is None:
            if item.card_id in proven_deleted:
                classification = ALREADY_DELETED
            else:
                blockers.append(Blocker("missing_without_trace", item.card_id))
                classification = BLOCKED_ITEM
        elif item.action == "created":
            blockers += _created_blockers(item, card, edits, actions, last_activity)
            if not blockers:
                ops.append(Operation(OP_DELETE_CARD, card.id))
            classification = BLOCKED_ITEM if blockers else DELETE
        else:  # updated
            more, ops = _updated_plan(item, card, columns)
            blockers += more
            classification = BLOCKED_ITEM if blockers else (RESTORE if ops else ALREADY_REVERTED_ITEM)
        if item.card_id in later:
            blockers.append(Blocker("later_batch_decision_on_card", item.card_id, batch_ids=later[item.card_id]))
            classification, ops = BLOCKED_ITEM, []
        if blockers:
            ops = []
        plan_items.append(PlanItem(
            item.id, item.card_id, item.board_id, item.source_key, item.action, item.observation,
            classification, tuple(ops), tuple(blockers),
        ))

    return _finish_plan(batch_id, ledger, tuple(plan_items), markers, {i.id: cards.get(i.card_id) for i in items})


def _empty_plan(batch_id, status, reason, ledger) -> RevertPlan:
    return _finish_plan(batch_id, ledger, (), {}, {}, status=status, reason=reason)


def _created_blockers(item, card: Card, edits, actions, last_event) -> list[Blocker]:
    after = json.loads(item.after_json)
    current = card_snapshot(card)
    out = []
    for key, imported in sorted(after["card"].items()):
        value = current["card"].get(key)
        if value != imported:
            code = "moved_since" if key == "column_id" else "field_modified_since"
            out.append(Blocker(code, card.id, f"card.{key}", imported, value))
    if after["source_ref"] != current["source_ref"]:
        out.append(Blocker("source_ref_modified_since", card.id, "source_ref"))
    out += _bid_details_created_blockers(card, after, current)
    if edits.get(card.id):
        out.append(Blocker("edited_since", card.id, "card_edit_history"))
    if actions.get(card.id):
        out.append(Blocker("activity_since", card.id, "card_actions"))
    if (last_event.get(card.id) or 0) > (item.stage_event_max_id_after or 0):
        out.append(Blocker("stage_activity_since", card.id, "card_stage_events"))
    return out


def _bid_details_created_blockers(card: Card, after: dict, current: dict) -> list[Blocker]:
    """The details row the import created must still be exactly what the import left."""
    if after["bid_details"] == current["bid_details"]:
        return []
    if not current["bid_details"]["present"]:
        return [Blocker("bid_details_missing", card.id, "bid_details")]
    return [Blocker("bid_details_modified_after_import", card.id, "bid_details")]


def _updated_plan(item, card: Card, columns: dict) -> tuple[list[Blocker], list[Operation]]:
    before, after, current = json.loads(item.before_json), json.loads(item.after_json), card_snapshot(card)
    blockers: list[Blocker] = []
    ops: list[Operation] = []
    before_flat, after_flat, current_flat = _flat(before), _flat(after), _flat(current)

    if current["source_ref"] is None:
        blockers.append(Blocker("source_ref_missing", card.id, "source_ref"))
    if item.bid_details_created:
        if after["bid_details"] == current["bid_details"]:
            ops.append(Operation(OP_DELETE_BID_DETAILS, card.id, {"bid_details_id": after["bid_details"]["id"]}))
        else:
            blockers += _bid_details_created_blockers(card, after, current)
        skip = "bid_details."
    else:
        if not current["bid_details"]["present"]:
            blockers.append(Blocker("bid_details_missing", card.id, "bid_details"))
        skip = None

    restore, move = {}, None
    for key in sorted(before_flat.keys() | after_flat.keys()):
        old, new = before_flat.get(key), after_flat.get(key)
        if old == new or (skip and key.startswith(skip)):
            continue
        if key.startswith("source_ref.") and current["source_ref"] is None:
            continue
        if key.startswith("bid_details.") and not current["bid_details"]["present"]:
            continue
        value = current_flat.get(key)
        if value == new:
            if key == "card.column_id":
                if columns.get(old) != card.column.board_id:
                    blockers.append(Blocker("origin_column_missing", card.id, key, new, value))
                else:
                    move = {"from_column_id": value, "to_column_id": old}
            else:
                restore[key] = old
        elif value != old:
            code = "moved_since" if key == "card.column_id" else "field_modified_since"
            blockers.append(Blocker(code, card.id, key, new, value))
    if restore:
        ops.append(Operation(OP_RESTORE_FIELDS, card.id, {"fields": restore}))
    if move:
        ops.append(Operation(OP_MOVE_CARD, card.id, move))
    return blockers, ops


def _finish_plan(batch_id, ledger, items, markers, cards, *, status=None, reason=None) -> RevertPlan:
    blockers = tuple(b for item in items for b in item.blockers)
    dependent = {}
    for blocker in blockers:
        if blocker.code == "later_batch_decision_on_card":
            for other in blocker.batch_ids:
                dependent.setdefault(other, []).append(blocker.card_id)
    dependent_batches = tuple({"batch_id": b, "card_ids": sorted(set(c))} for b, c in sorted(dependent.items()))
    if status is None:
        status = BLOCKED if blockers else READY
    ops = [op for item in items for op in item.operations]
    kinds = [op.kind for op in ops]
    counts = {
        "items": len(items),
        "cards_to_delete": kinds.count(OP_DELETE_CARD),
        "cards_to_restore": len({op.card_id for op in ops if op.kind != OP_DELETE_CARD}),
        "moves_to_revert": kinds.count(OP_MOVE_CARD),
        "bid_details_to_delete": kinds.count(OP_DELETE_BID_DETAILS),
        "already_deleted": sum(i.classification == ALREADY_DELETED for i in items),
        "already_reverted": sum(i.classification == ALREADY_REVERTED_ITEM for i in items),
        "observed": sum(i.classification == OBSERVED for i in items),
        "blocked": sum(i.classification == BLOCKED_ITEM for i in items),
    }
    fingerprint = _fingerprint(batch_id, ledger, status, items, markers, cards, dependent_batches)
    return RevertPlan(
        batch_id=batch_id, status=status, reason=reason,
        ledger_version=ledger.ledger_version if ledger is not None else None,
        items=items, blockers=blockers, dependent_batches=dependent_batches, counts=counts,
        no_commercial_changes=status == READY and not ops, plan_fingerprint=fingerprint,
    )


def _fingerprint(batch_id, ledger, status, items, markers, cards, dependent_batches) -> str:
    """SHA-256 of the canonical JSON of everything the plan depends on. No timestamps, no user names."""
    body = {
        "v": FINGERPRINT_VERSION,
        "batch_id": batch_id,
        "ledger_version": ledger.ledger_version if ledger is not None else None,
        "status": status,
        "items": [
            {
                "item_id": item.item_id,
                "card_id": item.card_id,
                "action": item.action,
                "classification": item.classification,
                "state": _state_hash(cards.get(item.item_id)),
                "activity": markers.get(item.item_id),
                "blockers": [[b.code, b.field, list(b.batch_ids)] for b in item.blockers],
                "operations": [op.as_dict() for op in item.operations],
            }
            for item in items
        ],
        "dependent_batches": list(dependent_batches),
    }
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _state_hash(card: Card | None) -> str:
    if card is None:
        return "absent"
    return hashlib.sha256(json.dumps(card_snapshot(card), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


# --- SQLite lock handling -------------------------------------------------------------------------

def is_lock_error(exc: BaseException) -> bool:
    """SQLITE_BUSY / SQLITE_LOCKED, by extended result code (primary code in the low byte), else by message."""
    if not isinstance(exc, OperationalError):
        return False
    orig = exc.orig
    code = getattr(orig, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in _LOCK_CODES
    message = str(orig).lower()
    return "database is locked" in message or "database table is locked" in message


def _rollback(db: Session) -> None:
    """Roll back; if even that fails, drop the connection so no half-open transaction survives."""
    try:
        db.rollback()
    except Exception:
        logger.error("revert: rollback failed; discarding the connection")
        try:
            db.invalidate()
        except Exception:
            pass


def _engine(db: Session):
    bind = db.get_bind()
    return bind.engine if isinstance(bind, Connection) else bind


def revert_preview(db: Session, batch_id: int) -> RevertPlan:
    """build_revert_plan for the preview endpoint: a SQLite lock becomes RevertBusy("preview")."""
    try:
        return build_revert_plan(db, batch_id)
    except OperationalError as exc:
        _rollback(db)
        if is_lock_error(exc):
            _log("preview_busy", batch_id)
            raise RevertBusy("preview") from exc
        raise


def _log(event: str, batch_id: int, plan: RevertPlan | None = None, **extra) -> None:
    """Structured log of a refused or failed attempt: codes and ids only, never titles or cell content."""
    fields = {"event": event, "batch_id": batch_id, **extra}
    if plan is not None:
        fields["status"] = plan.status
        fields["blockers"] = sorted({b.code for b in plan.blockers})
        fields["card_ids"] = sorted({b.card_id for b in plan.blockers if b.card_id is not None})[:MAX_LOGGED_CARD_IDS]
    logger.warning("bid import revert %s", json.dumps(fields, sort_keys=True))


# --- execution ------------------------------------------------------------------------------------

class RevertNotAuthorized(Exception):
    """The caller's authority was revoked before the commit; the revert was rolled back."""


def execute_revert(db: Session, batch_id: int, user, plan_fingerprint: str, before_commit=None) -> RevertOutcome:
    """Revert the batch in one transaction, or change nothing. See the module docstring."""
    if db.new or db.dirty or db.deleted:
        raise ValueError("The session has unsaved changes; revert an import in a clean session")
    with db.no_autoflush:
        user_id = user.id
    now = utcnow()
    try:
        outcome = _revert(db, batch_id, user_id, now, plan_fingerprint)
    except RevertRefused:
        raise
    except BaseException as exc:
        _rollback(db)
        if isinstance(exc, RevertBusy):
            raise
        _log("failed", batch_id, error=type(exc).__name__)
        raise
    if before_commit is not None:
        try:
            before_commit()  # last look at the caller's authority
        except BaseException:
            _rollback(db)
            raise
    try:
        db.commit()
    except BaseException as exc:
        _rollback(db)
        raise _after_failed_commit(db, batch_id, exc) from exc
    logger.info("bid import revert %s", json.dumps({"event": "reverted", "batch_id": batch_id, "outcome": outcome.outcome}))
    return outcome


def _revert(db: Session, batch_id: int, user_id: int, now: datetime, expected_fingerprint: str) -> RevertOutcome:
    # 1. The claim: the first write, so the write lock is ours before any decision is taken.
    try:
        claimed = db.execute(
            update(ImportBatchLedger)
            .where(ImportBatchLedger.import_batch_id == batch_id, ImportBatchLedger.reverted_at.is_(None))
            .values(reverted_at=now, reverted_by_user_id=user_id)
            .execution_options(synchronize_session=False)
        ).rowcount
    except OperationalError as exc:
        if is_lock_error(exc):
            _log("busy", batch_id, phase="start")
            raise RevertBusy("start") from exc
        raise
    if claimed == 0:
        _rollback(db)
        code = _refusal_code(db, batch_id)
        _log("refused", batch_id, code=code)
        raise RevertRefused(code)

    # 2. The plan, rebuilt under the lock.
    plan = build_revert_plan(db, batch_id, claimed=True)
    if plan.status == NOT_REVERTIBLE:
        _rollback(db)
        _log("refused", batch_id, plan, code="not_revertible")
        raise RevertRefused("not_revertible", plan)
    if plan.plan_fingerprint != expected_fingerprint:
        _rollback(db)
        _log("refused", batch_id, plan, code="plan_changed")
        raise RevertRefused("plan_changed", plan)
    if plan.status == BLOCKED:
        _rollback(db)
        _log("refused", batch_id, plan, code="revert_blocked")
        raise RevertRefused("revert_blocked", plan)

    # 3. Write, verify, record.
    before = _capture(db)
    with stage_context(db, _User(user_id), SOURCE_IMPORT_REVERT):
        _apply(db, plan)
        db.flush()
    _verify(db, plan, before, user_id)
    return _record(db, plan)


def _refusal_code(db: Session, batch_id: int) -> str:
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        return "batch_not_found"
    ledger = db.query(ImportBatchLedger).filter(ImportBatchLedger.import_batch_id == batch_id).one_or_none()
    if ledger is None:
        return "not_revertible"
    return "already_reverted" if ledger.reverted_at is not None else "not_revertible"


@dataclass(frozen=True)
class _User:
    """What stage_context needs from a user, without loading one inside the transaction."""
    id: int


def _after_failed_commit(db: Session, batch_id: int, exc: BaseException) -> BaseException:
    """The commit raised. From a fresh session: still not reverted -> busy (lock) or the original error;
    reverted, or unknowable -> unconfirmed. Never a success."""
    try:
        with Session(_engine(db)) as fresh:
            reverted_at = fresh.execute(
                select(ImportBatchLedger.reverted_at).where(ImportBatchLedger.import_batch_id == batch_id)
            ).scalar_one_or_none()
    except Exception:
        _log("commit_unconfirmed", batch_id, error=type(exc).__name__)
        return RevertOutcomeUnconfirmed("the commit failed and the state could not be read")
    if reverted_at is not None:
        _log("commit_unconfirmed", batch_id, error=type(exc).__name__)
        return RevertOutcomeUnconfirmed("the commit failed but the batch looks reverted")
    if is_lock_error(exc):
        _log("busy", batch_id, phase="commit")
        return RevertBusy("commit")
    _log("failed", batch_id, error=type(exc).__name__, phase="commit")
    return exc


def _coerce(obj, name: str, value):
    """A snapshot (JSON) value back to the column's Python type."""
    if value is None:
        return None
    column = obj.__table__.columns[name]
    if isinstance(column.type, DateTime):
        return datetime.fromisoformat(value)
    if column.type.python_type is date:
        return date.fromisoformat(value)
    return value


def _apply(db: Session, plan: RevertPlan) -> None:
    deleted_cards = [op.card_id for op in plan.operations if op.kind == OP_DELETE_CARD]
    cards = {c.id: c for c in db.query(Card).filter(Card.id.in_({op.card_id for op in plan.operations}))} if plan.operations else {}

    for op in plan.operations:
        if op.kind != OP_RESTORE_FIELDS:
            continue
        card = cards[op.card_id]
        targets = {"card": card, "bid_details": card.bid_details, "source_ref": card.source_ref}
        for key, value in op.data["fields"].items():
            section, name = key.split(".", 1)
            setattr(targets[section], name, _coerce(targets[section], name, value))
    for op in plan.operations:
        if op.kind == OP_DELETE_BID_DETAILS:
            details = cards[op.card_id].bid_details
            if details is None or details.id != op.data["bid_details_id"]:
                raise RevertVerificationFailed("bid_details row changed under the lock")
            db.delete(details)
    db.flush()

    # Reverted moves go to the end of the origin column; positions are never reused.
    next_position = {}
    for op in plan.operations:
        if op.kind != OP_MOVE_CARD:
            continue
        target = op.data["to_column_id"]
        if target not in next_position:
            highest = db.query(func.max(Card.position)).filter(Card.column_id == target).scalar()
            next_position[target] = -1 if highest is None else highest
        next_position[target] += 1
        card = cards[op.card_id]
        card.column_id, card.position = target, next_position[target]
    for card_id in deleted_cards:
        db.delete(cards[card_id])


# --- verification (section 10) --------------------------------------------------------------------

@dataclass
class _State:
    cards: dict            # card_id -> card_snapshot dict (commercial state incl. column_id)
    columns: dict          # column_id -> [card ids by (position, id)]
    positions: dict        # card_id -> (column_id, position)
    counts: dict           # table -> row count
    undo: list             # undo_entries rows
    max_event_id: int
    ledger_items: int


def _capture(db: Session) -> _State:
    db.expire_all()
    cards = db.query(Card).options(selectinload(Card.bid_details), selectinload(Card.source_ref)).all()
    rows = sorted(db.execute(select(Card.id, Card.column_id, Card.position)).all(), key=lambda r: (r.position, r.id))
    columns: dict = {}
    for row in rows:
        columns.setdefault(row.column_id, []).append(row.id)
    return _State(
        cards={c.id: card_snapshot(c) for c in cards},
        columns=columns,
        positions={row.id: (row.column_id, row.position) for row in rows},
        counts={
            model.__tablename__: db.query(func.count()).select_from(model).scalar()
            for model in (Card, CardBidDetails, CardSourceRef)
        },
        undo=[tuple(r) for r in db.execute(select(UndoEntry).order_by(UndoEntry.id)).all()],
        max_event_id=db.query(func.max(CardStageEvent.id)).scalar() or 0,
        ledger_items=db.query(func.count()).select_from(ImportBatchItem).scalar(),
    )


def _expected_state(pre: dict, ops) -> dict:
    state = copy.deepcopy(pre)
    for op in ops:
        if op.kind == OP_RESTORE_FIELDS:
            for key, value in op.data["fields"].items():
                section, name = key.split(".", 1)
                state[section][name] = value
        elif op.kind == OP_MOVE_CARD:
            state["card"]["column_id"] = op.data["to_column_id"]
        elif op.kind == OP_DELETE_BID_DETAILS:
            state["bid_details"] = {"present": False}
    return state


def _verify(db: Session, plan: RevertPlan, pre: _State, user_id: int) -> None:
    def fail(message):
        raise RevertVerificationFailed(message)

    db.flush()
    post = _capture(db)
    ops_by_card: dict = {}
    for op in plan.operations:
        ops_by_card.setdefault(op.card_id, []).append(op)
    deleted = {c for c, ops in ops_by_card.items() if any(o.kind == OP_DELETE_CARD for o in ops)}
    moved = {op.card_id: op for ops in ops_by_card.values() for op in ops if op.kind == OP_MOVE_CARD}

    # Commercial integrity: untouched cards identical; planned cards exactly as planned.
    if set(post.cards) != set(pre.cards) - deleted:
        fail("the set of cards differs from the plan")
    for card_id, state in post.cards.items():
        expected = _expected_state(pre.cards[card_id], ops_by_card.get(card_id, ()))
        if state != expected:
            fail(f"card {card_id} differs from the plan")

    # Row counts.
    details_gone = sum(
        1 for c in deleted if pre.cards[c]["bid_details"]["present"]
    ) + sum(1 for c, ops in ops_by_card.items() if c not in deleted and any(o.kind == OP_DELETE_BID_DETAILS for o in ops))
    refs_gone = sum(1 for c in deleted if pre.cards[c]["source_ref"] is not None)
    expected_counts = {
        "cards": pre.counts["cards"] - len(deleted),
        "card_bid_details": pre.counts["card_bid_details"] - details_gone,
        "card_source_refs": pre.counts["card_source_refs"] - refs_gone,
    }
    if post.counts != expected_counts:
        fail("row counts differ from the plan")

    # Structure: no orphans, valid columns, unchanged order of the other cards, moved cards last.
    if deleted:
        for model in (CardBidDetails, CardSourceRef, CardImportChange, CardEditHistory, CardAction):
            if db.query(func.count()).select_from(model).filter(model.card_id.in_(deleted)).scalar():
                fail(f"orphan {model.__tablename__} rows")
    planned = deleted | set(moved)
    for column_id in set(pre.columns) | set(post.columns):
        stay_pre = [c for c in pre.columns.get(column_id, []) if c not in planned]
        stay_post = [c for c in post.columns.get(column_id, []) if c not in planned]
        if stay_pre != stay_post:
            fail(f"the order of the other cards in column {column_id} changed")
    columns = {c.id: c.board_id for c in db.query(BoardColumn)}
    boards = {item.card_id: item.board_id for item in plan.items}
    for card_id in {*ops_by_card, *moved} - deleted:
        column_id, _ = post.positions[card_id]
        if columns.get(column_id) != boards[card_id]:
            fail(f"card {card_id} is in a column of another board")
    by_target: dict = {}
    for card_id, op in moved.items():
        column_id, position = post.positions[card_id]
        if column_id != op.data["to_column_id"]:
            fail(f"card {card_id} was not moved back")
        by_target.setdefault(column_id, []).append(position)
    for column_id, positions in by_target.items():
        others = [p for c, (col, p) in post.positions.items() if col == column_id and c not in moved]
        if len(set(positions)) != len(positions) or (others and min(positions) <= max(others)):
            fail(f"reverted moves in column {column_id} are not at the end")
    if post.undo != pre.undo:
        fail("undo_entries changed")
    if post.ledger_items != pre.ledger_items:
        fail("ledger items changed")

    # Compensating stage events, and only those.
    events = db.query(CardStageEvent).filter(CardStageEvent.id > pre.max_event_id).all()
    if any(e.source != SOURCE_IMPORT_REVERT or e.user_id != user_id for e in events):
        fail("unexpected stage events")
    if sorted((e.card_id, e.event_type == "deleted") for e in events) != sorted(
        [(c, True) for c in deleted] + [(c, False) for c in moved]
    ):
        fail("stage events differ from the plan")
    claim = db.execute(
        select(ImportBatchLedger.reverted_at, ImportBatchLedger.reverted_by_user_id)
        .where(ImportBatchLedger.import_batch_id == plan.batch_id)
    ).one()
    if claim.reverted_at is None or claim.reverted_by_user_id != user_id:
        fail("the claim is missing")


# --- result ---------------------------------------------------------------------------------------

def _record(db: Session, plan: RevertPlan) -> RevertOutcome:
    """Store the final outcome and counts on the ledger (still inside the transaction)."""
    c = plan.counts
    no_changes = plan.no_commercial_changes
    outcome = RevertOutcome(
        batch_id=plan.batch_id,
        outcome=OUTCOME_NO_COMMERCIAL_CHANGES if no_changes else OUTCOME_REVERTED,
        no_commercial_changes=no_changes,
        cards_deleted=c["cards_to_delete"],
        cards_restored=c["cards_to_restore"],
        moves_reverted=c["moves_to_revert"],
        already_deleted=c["already_deleted"],
        bid_details_deleted=c["bid_details_to_delete"],
        already_reverted=c["already_reverted"],
        observed=c["observed"],
    )
    ledger = db.query(ImportBatchLedger).filter(ImportBatchLedger.import_batch_id == plan.batch_id).one()
    ledger.revert_outcome = outcome.outcome
    ledger.no_commercial_changes = no_changes
    ledger.cards_deleted_count = outcome.cards_deleted
    ledger.cards_restored_count = outcome.cards_restored
    ledger.moves_reverted_count = outcome.moves_reverted
    ledger.already_deleted_count = outcome.already_deleted
    ledger.result_summary_json = json.dumps({
        "items": c["items"],
        "observed": outcome.observed,
        "already_reverted": outcome.already_reverted,
        "bid_details_deleted": outcome.bid_details_deleted,
    }, sort_keys=True)
    db.flush()
    return outcome
