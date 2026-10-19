"""Card stage history: one listener records every stage change.

Every way a card can change stage - REST routes, AI actions, undo, a column
or board deletion cascading to its cards, seeding - ends in an ORM flush of a
Card insert, a column_id change or a delete. A single after_flush listener
turns those into CardStageEvent rows on the same connection, inside the same
transaction: the card's stage and its history commit or roll back together,
and no code path can forget (or double-record) an event.

Entry points only say who acted and how, with set_stage_context().

Event types (stage kinds come from the columns, never from titles):
- created   card inserted into a column (from_* empty)
- moved     between two "open" stages
- won       into the won stage from any other stage
- lost      into the lost stage from any other stage
- reopened  from won or lost back into an open stage
- deleted   card deleted (to_* empty); the history is kept
- baseline  a reference point, not an observed move: the stage each existing
            card was in when history tracking started (migrate_stage_events.py),
            or the stage a card was imported into (a Card inserted while the
            session's source is SOURCE_IMPORT gets "baseline" instead of
            "created" - the import is not the opportunity's commercial creation)
A reorder within the same column, or an edit of lead fields, is not a stage
change and records nothing.

Every event also records the card's deal value and currency at that moment
(deal_value_snapshot / deal_currency_snapshot, value_snapshot_taken = true),
so historical KPIs never depend on the card's current value.
"""
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session

from lead_options import STAGE_LOST, STAGE_WON
from models import BoardColumn, Card, CardStageEvent

SOURCE_API = "api"          # REST routes (the UI and any direct API client)
SOURCE_AI = "ai"            # actions applied from the AI chat
SOURCE_UNDO = "undo"        # a user's undo
SOURCE_SEED = "seed"        # sample leads created with a new board
SOURCE_MIGRATION = "migration"
SOURCE_IMPORT = "import"    # cards created/updated by the Salcomp Bid import (bid_import_service.py)
SOURCE_IMPORT_REVERT = "import_revert"  # compensating events of a reverted Bid import (bid_import_revert.py)
SOURCE_SYSTEM = "system"    # anything else (scripts, tests) - no context set

_CONTEXT_KEY = "stage_history_context"


def utcnow() -> datetime:
    """Naive UTC "now": the clock for event timestamps (and for KPIs)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def set_stage_context(db: Session, user=None, source: str = SOURCE_SYSTEM) -> None:
    """Attribute the stage events of this session's next flushes."""
    db.info[_CONTEXT_KEY] = {"user_id": user.id if user is not None else None, "source": source}


@contextmanager
def stage_context(db: Session, user=None, source: str = SOURCE_SYSTEM):
    """set_stage_context for a block, restoring the session's previous context
    afterwards (a request session keeps its own attribution)."""
    previous = db.info.get(_CONTEXT_KEY)
    set_stage_context(db, user, source)
    try:
        yield
    finally:
        if previous is None:
            db.info.pop(_CONTEXT_KEY, None)
        else:
            db.info[_CONTEXT_KEY] = previous


def transition_type(from_kind: str, to_kind: str) -> str:
    if to_kind == STAGE_WON:
        return "won"
    if to_kind == STAGE_LOST:
        return "lost"
    if from_kind in (STAGE_WON, STAGE_LOST):
        return "reopened"
    return "moved"


def _column_info(session: Session, column_ids: set) -> dict:
    """{column_id: (board_id, stage_kind)}, including columns deleted in this
    flush (no longer in the table, but still in memory)."""
    info = {
        obj.id: (obj.board_id, obj.stage_kind)
        for obj in session.deleted
        if isinstance(obj, BoardColumn)
    }
    missing = column_ids - info.keys()
    if missing:
        rows = session.connection().execute(
            select(BoardColumn.id, BoardColumn.board_id, BoardColumn.stage_kind).where(
                BoardColumn.id.in_(missing)
            )
        )
        info.update({row.id: (row.board_id, row.stage_kind) for row in rows})
    return info


@event.listens_for(Session, "before_flush")
def _allocate_card_ids(session: Session, flush_context, instances) -> None:
    """Give new cards ids that have never had a history.

    The cards table has no AUTOINCREMENT, so SQLite hands out max(id) + 1 and
    reuses the id of a just-deleted card with the highest id. That would
    splice the deleted card's history onto an unrelated new card (and let a
    stale undo step act on the wrong card). Allocating above both the live
    cards and every card id in the history prevents reuse without rebuilding
    the existing table.
    """
    new_cards = [obj for obj in session.new if isinstance(obj, Card) and obj.id is None]
    if not new_cards:
        return
    conn = session.connection()
    highest = max(
        conn.execute(select(func.max(Card.id))).scalar() or 0,
        conn.execute(select(func.max(CardStageEvent.card_id))).scalar() or 0,
    )
    for card in new_cards:
        highest += 1
        card.id = highest


@event.listens_for(Session, "after_flush")
def _record_stage_events(session: Session, flush_context) -> None:
    # (card, event_type, from_column_id, to_column_id)
    changes = []
    context = session.info.get(_CONTEXT_KEY, {})
    inserted = "baseline" if context.get("source") == SOURCE_IMPORT else "created"
    for obj in session.new:
        if isinstance(obj, Card):
            changes.append((obj, inserted, None, obj.column_id))
    for obj in session.dirty:
        if isinstance(obj, Card) and obj not in session.deleted:
            history = inspect(obj).attrs.column_id.history
            if history.deleted and history.added and history.deleted[0] != history.added[0]:
                changes.append((obj, None, history.deleted[0], history.added[0]))
    for obj in session.deleted:
        if isinstance(obj, Card):
            # The stage it was in before this flush, even if column_id changed too.
            history = inspect(obj).attrs.column_id.history
            from_column = history.deleted[0] if history.deleted else obj.column_id
            changes.append((obj, "deleted", from_column, None))
    if not changes:
        return

    columns = _column_info(session, {c for _, _, f, t in changes for c in (f, t) if c is not None})
    now = utcnow()
    rows = []
    for card, event_type, from_column, to_column in changes:
        from_board, from_kind = columns.get(from_column, (None, None))
        to_board, to_kind = columns.get(to_column, (None, None))
        if event_type is None:
            event_type = transition_type(from_kind, to_kind)
        rows.append({
            "card_id": card.id,
            "board_id": to_board if to_board is not None else from_board,
            "event_type": event_type,
            "from_column_id": from_column,
            "from_stage_kind": from_kind,
            "to_column_id": to_column,
            "to_stage_kind": to_kind,
            "user_id": context.get("user_id"),
            "source": context.get("source", SOURCE_SYSTEM),
            "occurred_at": now,
            # The card's value as of this flush - the same transaction as the
            # stage change, so a won/lost event keeps the value it closed at
            # even if the card is edited (or deleted) later.
            "deal_value_snapshot": card.deal_value,
            "deal_currency_snapshot": card.currency,
            "value_snapshot_taken": True,
        })
    session.connection().execute(CardStageEvent.__table__.insert(), rows)


def card_stage_history(db: Session, card_id: int) -> list[CardStageEvent]:
    """A card's stage events, oldest first (ties broken by insertion order).
    Works for deleted cards too: their history is never removed."""
    return (
        db.query(CardStageEvent)
        .filter(CardStageEvent.card_id == card_id)
        .order_by(CardStageEvent.occurred_at, CardStageEvent.id)
        .all()
    )
