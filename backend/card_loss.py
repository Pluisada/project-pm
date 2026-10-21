"""Loss reasons: why a card entered the lost stage (central mode only).

Moving a card into a column whose `stage_kind` is "lost" needs a reason, validated here and nowhere else (the REST
move, the AI move and the Cards workbook import all go through it). Each entry into LOST writes one row of
`card_loss_events` in the same flush as the move, so the reason, the actor, the time and the two columns commit or roll
back with the stage change. Rows are never updated or deleted: moving a card out of LOST, editing it, undoing the move
or losing it again leaves earlier rows as they are, and a later loss is a new row with its own reason.

Storage: one additive table on its own declarative base, like the review tables and the Cards-import ledger (not part of
the required company schema contract, no schema version moves). init_company_card_loss.py adds it to an existing database
and create_company_db.py to a new one. Without it a move into LOST is refused (503) instead of recording no reason.
The legacy single-database runtime never reaches any of this: only company sessions (session.info["company_slug"]) do.
"""
from datetime import datetime, timezone

from sqlalchemy import Column as SQLColumn, DateTime, Integer, String, Text, event, inspect, insert
from sqlalchemy.orm import Session, declarative_base

from lead_options import STAGE_LOST

LossBase = declarative_base()

MAX_REASON_LENGTH = 1000
_INTENTS = "card_loss_intents"
_COMPANY_KEY = "company_slug"


class CardLossEvent(LossBase):
    __tablename__ = "card_loss_events"

    id = SQLColumn(Integer, primary_key=True)
    card_id = SQLColumn(Integer, nullable=False, index=True)  # plain integer: card ids are never reused (stage_history)
    board_id = SQLColumn(Integer, nullable=False, index=True)
    from_column_id = SQLColumn(Integer, nullable=True)
    to_column_id = SQLColumn(Integer, nullable=False)
    reason = SQLColumn(Text, nullable=False)
    user_id = SQLColumn(Integer, nullable=True)
    source = SQLColumn(String, nullable=False)  # api | ai
    occurred_at = SQLColumn(DateTime, nullable=False)


_table = CardLossEvent.__table__


class LossError(Exception):
    status = 422
    code = "loss_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class LossReasonRequired(LossError):
    code = "loss_reason_required"


class LossReasonTooLong(LossError):
    code = "loss_reason_too_long"


class LossNotInitialized(LossError):
    status = 503
    code = "loss_not_initialized"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def clean_reason(raw) -> str:
    """The trimmed reason, or LossReasonRequired / LossReasonTooLong. Never trusts a client-side check."""
    if not isinstance(raw, str) or not raw.strip():
        raise LossReasonRequired("A reason is required to move a card to the lost stage")
    reason = raw.strip()
    if len(reason) > MAX_REASON_LENGTH:
        raise LossReasonTooLong(f"The loss reason can have at most {MAX_REASON_LENGTH} characters")
    return reason


def enters_lost(card, target) -> bool:
    """Whether moving `card` to column `target` is an entry into LOST (judged by stage_kind, never by title)."""
    return target.stage_kind == STAGE_LOST and card.column_id != target.id


def is_company_session(db) -> bool:
    return db.info.get(_COMPANY_KEY) is not None


def create_loss(bind) -> None:
    """Create the table if missing (idempotent). `bind` is an engine or connection."""
    LossBase.metadata.create_all(bind)


def has_loss(db) -> bool:
    inspector = inspect(db.connection())
    if not inspector.has_table(_table.name):
        return False
    return {c.name for c in _table.columns} <= {c["name"] for c in inspector.get_columns(_table.name)}


def require_loss(db) -> None:
    if is_company_session(db) and not has_loss(db):
        raise LossNotInitialized("Loss reasons are not set up for this company yet")


def note_loss(db, card, user, reason: str, from_column, to_column, source: str) -> None:
    """Record, with the move being made in this session, why `card` enters LOST (`reason` is already cleaned)."""
    if not is_company_session(db):
        return
    db.info.setdefault(_INTENTS, []).append({
        "card_id": card.id, "board_id": to_column.board_id, "from_column_id": from_column.id if from_column else None,
        "to_column_id": to_column.id, "reason": reason, "user_id": user.id, "source": source,
    })


@event.listens_for(Session, "before_flush")
def _write_loss_events(session: Session, flush_context, instances) -> None:
    if session.info.get(_COMPANY_KEY) is None:
        return
    intents = session.info.pop(_INTENTS, None)
    if not intents:
        return
    conn = session.connection()
    now = utcnow()
    for intent in intents:
        conn.execute(insert(_table).values(occurred_at=now, **intent))


@event.listens_for(Session, "after_soft_rollback")
def _forget_intents(session: Session, previous_transaction) -> None:
    session.info.pop(_INTENTS, None)


def has_event(db, card_id: int, to_column_id: int) -> bool:
    """Whether `card_id` has a recorded entry into the lost column `to_column_id` (false when the table is missing)."""
    from sqlalchemy import select

    if not has_loss(db):
        return False
    return db.execute(select(_table.c.id).where(_table.c.card_id == card_id, _table.c.to_column_id == to_column_id)
                      .limit(1)).first() is not None


def events_for_card(db, card_id: int) -> list[dict]:
    """Every entry into LOST of one card, oldest first."""
    from sqlalchemy import select

    from models import CompanyUser

    rows = db.execute(
        select(_table, CompanyUser.username).outerjoin(CompanyUser, CompanyUser.id == _table.c.user_id)
        .where(_table.c.card_id == card_id).order_by(_table.c.id)
    ).all()
    return [{"id": r.id, "card_id": r.card_id, "reason": r.reason, "recorded_by": r.username, "source": r.source,
             "from_column_id": r.from_column_id, "to_column_id": r.to_column_id, "occurred_at": r.occurred_at} for r in rows]
