"""Card review obligations of one company database (central mode only).

A change that needs a second pair of eyes leaves a persisted, role-specific obligation that only that role clears:
- a Sales Member's successful edit of a card -> one obligation for the company Admin ("admin") and one for the
  Super Admin ("super_admin"), independent of each other;
- an Admin-level user (company Admin or Super Admin) moving a card backward -> one obligation for the Super Admin only.
Forward moves, Admin edits and Super Admin edits create none.

Storage (additive tables on their own declarative base, like the Cards-import ledger and the product catalog: not part
of the required company schema contract, no schema version moves; init_company_card_reviews.py adds them to an existing
database and create_company_db.py to a new one):
- `card_versions`: one counter per card, raised by every change of a reviewable field or of the card's column,
  whoever makes it (REST, AI, undo, import). Row creation and the bump are one UPDATE under SQLite's write lock, so
  two concurrent changes always get distinct versions.
- `card_review_obligations`: at most ONE pending row per (card, reviewer role) (partial unique index). Its
  `card_version` always equals the card's current version: every later change of the card moves it along, so a
  reviewer can only ever approve the latest state, and approving needs the version the reviewer saw. A later
  member edit or backward move refreshes the same pending row (the triggers since the last review accumulate in
  `context_json`, newest last, capped); a cleared row is history and is never reopened or inherited: the next trigger
  inserts a new pending row. Rows are never deleted; a deleted card's pending rows are closed as "void".
The underlying history stays where it already lives (card_edit_history, card_stage_events, audit_events).

Everything is written from one before_flush listener so that the card change, its version, the obligations and the
trigger commit or roll back together. The listener acts only on company sessions (session.info["company_slug"]);
the legacy single-database runtime never reaches it. If a company database lacks the tables, a change that needs an
obligation is refused (ReviewsNotInitialized) instead of silently skipping it; other changes are unaffected.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import (
    Column as SQLColumn, DateTime, Index, Integer, String, Text, event, inspect, insert, select, text, update,
)
from sqlalchemy.orm import Session, declarative_base

from models import Card
from schemas import CardUpdate

ReviewBase = declarative_base()

ROLE_ADMIN = "admin"
ROLE_SUPER_ADMIN = "super_admin"
REVIEW_ROLES = (ROLE_ADMIN, ROLE_SUPER_ADMIN)

STATUS_PENDING = "pending"
STATUS_CLEARED = "cleared"
STATUS_VOID = "void"

KIND_MEMBER_EDIT = "member_edit"
KIND_BACKWARD_MOVE = "backward_move"

CONTEXT_LIMIT = 20  # triggers kept per pending row; the full history is in card_edit_history / card_stage_events
# The editable card columns (CardUpdate also names legacy Bid fields that are not columns of `cards`) and the stage.
TRACKED_FIELDS = tuple(f for f in CardUpdate.model_fields if f in Card.__table__.columns) + ("column_id",)
_INTENTS = "card_review_intents"
_COMPANY_KEY = "company_slug"


class CardVersion(ReviewBase):
    __tablename__ = "card_versions"

    card_id = SQLColumn(Integer, primary_key=True)  # plain integer: card ids are never reused (stage_history)
    version = SQLColumn(Integer, nullable=False)
    updated_at = SQLColumn(DateTime, nullable=False)


class ReviewObligation(ReviewBase):
    __tablename__ = "card_review_obligations"
    __table_args__ = (
        Index("uq_card_review_pending", "card_id", "reviewer_role", unique=True, sqlite_where=text("status = 'pending'")),
    )

    id = SQLColumn(Integer, primary_key=True)
    card_id = SQLColumn(Integer, nullable=False, index=True)
    reviewer_role = SQLColumn(String, nullable=False)  # "admin" | "super_admin"
    status = SQLColumn(String, nullable=False)  # pending | cleared | void
    card_version = SQLColumn(Integer, nullable=False)  # pending: the card's current version; cleared: the one reviewed
    trigger_user_id = SQLColumn(Integer, nullable=True)  # who made the latest triggering change
    context_json = SQLColumn(Text, nullable=False)  # [{"kind","version","user_id","at",...}], newest last
    refresh_count = SQLColumn(Integer, nullable=False, default=0)
    created_at = SQLColumn(DateTime, nullable=False)
    updated_at = SQLColumn(DateTime, nullable=False)
    cleared_by_user_id = SQLColumn(Integer, nullable=True)
    cleared_at = SQLColumn(DateTime, nullable=True)
    closed_reason = SQLColumn(String, nullable=True)


_versions = CardVersion.__table__
_obligations = ReviewObligation.__table__


class ReviewError(Exception):
    """A review action cannot be applied; `code` is stable for clients."""

    status = 409
    code = "review_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ReviewNotFound(ReviewError):
    status = 404
    code = "review_not_found"


class ReviewStale(ReviewError):
    status = 409
    code = "review_stale"


class ReviewsNotInitialized(ReviewError):
    status = 503
    code = "review_not_initialized"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- schema availability -------------------------------------------------------------------------------------

def create_reviews(bind) -> None:
    """Create the tables if missing (idempotent). `bind` is an engine or connection."""
    ReviewBase.metadata.create_all(bind)


def has_reviews(db) -> bool:
    """Both tables exist with every column of the current model (read-only reflection of the session's connection)."""
    inspector = inspect(db.connection())
    for table in (_versions, _obligations):
        if not inspector.has_table(table.name):
            return False
        if not {c.name for c in table.columns} <= {c["name"] for c in inspector.get_columns(table.name)}:
            return False
    return True


def require_reviews(db) -> None:
    if is_company_session(db) and not has_reviews(db):
        raise ReviewsNotInitialized("The review workflow is not set up for this company yet")


def is_company_session(db) -> bool:
    return db.info.get(_COMPANY_KEY) is not None


def _tables_ready(conn) -> bool:
    names = {r[0] for r in conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('card_versions','card_review_obligations')")}
    return len(names) == 2


# --- triggers (recorded by the callers, written by the flush listener) ---------------------------------------

def _record_intent(db, card: Card, roles: tuple[str, ...], entry: dict) -> None:
    db.info.setdefault(_INTENTS, []).append({"card": card, "roles": roles, "entry": entry})


def note_member_edit(db, card: Card, user, changes: dict) -> None:
    """A Sales Member changed `card` (`changes` is the {field: {old, new}} diff): both roles must review it."""
    if not is_company_session(db) or not changes:
        return
    require_reviews(db)
    entry = {"kind": KIND_MEMBER_EDIT, "user_id": user.id, "at": utcnow().isoformat(), "changes": changes}
    _record_intent(db, card, (ROLE_ADMIN, ROLE_SUPER_ADMIN), entry)


def note_backward_move(db, card: Card, user, from_column, to_column) -> None:
    """An Admin-level user moved `card` to an earlier column: only the Super Admin reviews it."""
    if not is_company_session(db):
        return
    require_reviews(db)
    entry = {
        "kind": KIND_BACKWARD_MOVE, "user_id": user.id, "at": utcnow().isoformat(),
        "from_column_id": from_column.id, "from_title": from_column.title,
        "to_column_id": to_column.id, "to_title": to_column.title,
    }
    _record_intent(db, card, (ROLE_SUPER_ADMIN,), entry)


# --- the flush listener --------------------------------------------------------------------------------------

def _tracked_change(card: Card) -> bool:
    state = inspect(card)
    for field in TRACKED_FIELDS:
        history = state.attrs[field].history
        if history.added and (not history.deleted or history.deleted[0] != history.added[0]):
            return True
    return False


def _bump(conn, card_id: int, now: datetime) -> int:
    """Raise the card's version by one. The UPDATE takes the write lock first, so the read that follows is exact."""
    conn.execute(insert(_versions).prefix_with("OR IGNORE").values(card_id=card_id, version=0, updated_at=now))
    conn.execute(update(_versions).where(_versions.c.card_id == card_id)
                 .values(version=_versions.c.version + 1, updated_at=now))
    return conn.execute(select(_versions.c.version).where(_versions.c.card_id == card_id)).scalar_one()


def _upsert_pending(conn, card_id: int, role: str, version: int, entry: dict, now: datetime) -> None:
    row = conn.execute(select(_obligations.c.id, _obligations.c.context_json, _obligations.c.refresh_count)
                       .where(_obligations.c.card_id == card_id, _obligations.c.reviewer_role == role,
                              _obligations.c.status == STATUS_PENDING)).first()
    entry = {**entry, "version": version}
    if row is None:
        conn.execute(insert(_obligations).values(
            card_id=card_id, reviewer_role=role, status=STATUS_PENDING, card_version=version,
            trigger_user_id=entry.get("user_id"), context_json=json.dumps([entry]), refresh_count=0,
            created_at=now, updated_at=now))
        return
    context = (json.loads(row.context_json) + [entry])[-CONTEXT_LIMIT:]
    conn.execute(update(_obligations).where(_obligations.c.id == row.id).values(
        card_version=version, trigger_user_id=entry.get("user_id"), context_json=json.dumps(context),
        refresh_count=row.refresh_count + 1, updated_at=now))


@event.listens_for(Session, "before_flush")
def _review_before_flush(session: Session, flush_context, instances) -> None:
    if session.info.get(_COMPANY_KEY) is None:
        return
    intents = session.info.get(_INTENTS, [])
    deleted = [c for c in session.deleted if isinstance(c, Card)]
    changed = {c.id: c for c in session.dirty
               if isinstance(c, Card) and c not in session.deleted and c.id is not None and _tracked_change(c)}
    for intent in intents:
        changed.setdefault(intent["card"].id, intent["card"])
    if not changed and not deleted:
        return
    conn = session.connection()
    if not _tables_ready(conn):
        if intents:
            raise ReviewsNotInitialized("The review workflow is not set up for this company yet")
        return
    now = utcnow()
    versions = {card_id: _bump(conn, card_id, now) for card_id in changed}
    triggered = {intent["card"].id for intent in intents}
    for card_id, version in versions.items():
        if card_id in triggered:
            continue
        # Any other change (an Admin edit, a forward move, an import...) moves the pending reviews to the new version.
        conn.execute(update(_obligations).where(_obligations.c.card_id == card_id, _obligations.c.status == STATUS_PENDING)
                     .values(card_version=version, updated_at=now, refresh_count=_obligations.c.refresh_count + 1))
    for intent in intents:
        card_id = intent["card"].id
        for role in intent["roles"]:
            _upsert_pending(conn, card_id, role, versions[card_id], intent["entry"], now)
    session.info.pop(_INTENTS, None)
    for card in deleted:
        conn.execute(update(_obligations).where(_obligations.c.card_id == card.id, _obligations.c.status == STATUS_PENDING)
                     .values(status=STATUS_VOID, closed_reason="card_deleted", updated_at=now))


@event.listens_for(Session, "after_soft_rollback")
def _forget_intents(session: Session, previous_transaction) -> None:
    session.info.pop(_INTENTS, None)


# --- reading and clearing ------------------------------------------------------------------------------------

def _public(row, card_title: str, column_title: str, board_id: int) -> dict:
    return {
        "id": row.id, "board_id": board_id, "card_id": row.card_id, "card_title": card_title, "column_title": column_title,
        "reviewer_role": row.reviewer_role, "card_version": row.card_version, "status": row.status,
        "created_at": row.created_at, "updated_at": row.updated_at, "refresh_count": row.refresh_count,
        "triggers": json.loads(row.context_json),
    }


def pending_for_role(db, board_id: int, role: str) -> list[dict]:
    """The pending obligations of `role` on one board, oldest first."""
    from models import BoardColumn

    rows = db.execute(
        select(_obligations, Card.title, BoardColumn.title.label("column_title"))
        .join(Card, Card.id == _obligations.c.card_id)
        .join(BoardColumn, BoardColumn.id == Card.column_id)
        .where(_obligations.c.reviewer_role == role, _obligations.c.status == STATUS_PENDING,
               BoardColumn.board_id == board_id)
        .order_by(_obligations.c.created_at, _obligations.c.id)
    ).all()
    return [_public(r, r.title, r.column_title, board_id) for r in rows]


def clear_review(db, card: Card, role: str, version: int, reviewer_id: int) -> dict:
    """Clear `role`'s pending obligation of `card`, only if `version` is the card's current version.

    The caller holds the write lock (begin_write) and commits; nothing is changed on a refusal.
    ReviewNotFound: nothing pending for this role on this card. ReviewStale: the card changed since the reviewer saw it.
    """
    if role not in REVIEW_ROLES:
        raise ReviewNotFound("No such review")
    conn = db.connection()
    row = conn.execute(select(_obligations).where(
        _obligations.c.card_id == card.id, _obligations.c.reviewer_role == role,
        _obligations.c.status == STATUS_PENDING)).first()
    if row is None:
        raise ReviewNotFound("Nothing to review for this card")
    current = conn.execute(select(_versions.c.version).where(_versions.c.card_id == card.id)).scalar()
    if version != row.card_version or current != row.card_version:
        raise ReviewStale("The card changed since you opened it. Review the latest version.")
    now = utcnow()
    result = conn.execute(update(_obligations).where(
        _obligations.c.id == row.id, _obligations.c.status == STATUS_PENDING, _obligations.c.card_version == version,
    ).values(status=STATUS_CLEARED, cleared_by_user_id=reviewer_id, cleared_at=now, updated_at=now))
    if result.rowcount != 1:
        raise ReviewStale("The review was already completed or the card changed.")
    return {"id": row.id, "card_id": card.id, "reviewer_role": role, "card_version": version, "cleared_at": now,
            "triggers": json.loads(row.context_json)}
