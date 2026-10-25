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
- `card_review_acknowledgments`: one row per (obligation, required reviewer user) -- the per-user CLEAR state. The
  obligation stays one row per (card, reviewer role); its required reviewers are the active company Admins ("admin")
  or the active global administrator(s) ("super_admin"), resolved from the identity database through the session's
  reviewer provider (session.info["review_reviewers"], set by the company routes). Each reviewer clears only their own
  row; the obligation is "cleared" only when no required reviewer is still pending. A new card version resets every
  acknowledgment to pending for that version. Reviewer changes are reconciled lazily under the write lock (new Admin ->
  pending row for the still-pending obligations; removed reviewer -> their pending row is voided, never cleared).
  An obligation whose every reviewer was removed is voided ("no_required_reviewer"), never presented as cleared.
  Cleared role-level rows written before this table existed are history and are left untouched; a role-level
  obligation still pending at migration simply has no acknowledgment rows yet and gets pending ones for the current
  reviewers on first touch, so nothing is silently acknowledged.
The underlying history stays where it already lives (card_edit_history, card_stage_events, audit_events).

Everything is written from one before_flush listener so that the card change, its version, the obligations and the
trigger commit or roll back together. The listener acts only on company sessions (session.info["company_slug"]);
the legacy single-database runtime never reaches it. If a company database lacks the tables or they are malformed
(review_schema_problem), every tracked card change or deletion is refused (ReviewsNotInitialized) instead of being
committed without its version.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import (
    Column as SQLColumn, DateTime, Index, Integer, String, Text, event, inspect, insert, select, text, update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, declarative_base

from models import Card, CardBidDetails
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
KIND_LOST_MOVE = "lost_move"

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


class ReviewAcknowledgment(ReviewBase):
    """One required reviewer's own CLEAR state for one obligation (plain integers, no cross-table foreign keys)."""

    __tablename__ = "card_review_acknowledgments"
    __table_args__ = (
        Index("uq_card_review_ack_reviewer", "obligation_id", "reviewer_user_id", unique=True),
    )

    id = SQLColumn(Integer, primary_key=True)
    obligation_id = SQLColumn(Integer, nullable=False, index=True)
    reviewer_user_id = SQLColumn(Integer, nullable=False)  # canonical identity_users.id
    status = SQLColumn(String, nullable=False)  # pending | cleared | void
    card_version = SQLColumn(Integer, nullable=False)  # the version this row is pending for / was cleared at
    created_at = SQLColumn(DateTime, nullable=False)
    updated_at = SQLColumn(DateTime, nullable=False)
    cleared_at = SQLColumn(DateTime, nullable=True)
    closed_reason = SQLColumn(String, nullable=True)


_versions = CardVersion.__table__
_obligations = ReviewObligation.__table__
_acks = ReviewAcknowledgment.__table__
_REVIEWERS = "review_reviewers"


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


# --- reading versions ----------------------------------------------------------------------------------------

def card_version(db, card_id: int) -> int:
    """The card's current version: 0 when the version tables are valid and the card has no row (never changed since
    it was created). Raises ReviewsNotInitialized when the tables are absent, malformed or cannot be inspected."""
    return card_versions(db, [card_id]).get(card_id, 0)


def card_versions(db, card_ids) -> dict[int, int]:
    """{card_id: version} for the given ids, 0 for a card without a row. Read-only.

    The version schema is validated on every call (one PRAGMA round, not one per card) and a missing or malformed
    schema raises ReviewsNotInitialized: it is never read as "every card is at version 0".

    Callers that hand versions to a client together with card content must call this BEFORE loading the cards: a
    write in between then leaves the content newer than the version (a false conflict later), never older.
    """
    ids = list(card_ids)
    conn = db.connection()
    _require_version_schema(conn)
    versions = {card_id: 0 for card_id in ids}
    for start in range(0, len(ids), 500):
        rows = conn.execute(select(_versions.c.card_id, _versions.c.version)
                            .where(_versions.c.card_id.in_(ids[start:start + 500])))
        versions.update({card_id: version for card_id, version in rows})
    return versions


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- schema availability -------------------------------------------------------------------------------------

def create_reviews(bind) -> None:
    """Create the tables if missing (idempotent). `bind` is an engine or connection."""
    ReviewBase.metadata.create_all(bind)


def _inspection_failed(exc: SQLAlchemyError) -> None:
    """Called from an `except` when the schema could not be read. A recognized SQLite lock contention is not a schema
    problem: it propagates unchanged so the caller's busy handling (503 database_busy / import_busy with Retry-After)
    applies. Any other failure is fail-closed: ReviewsNotInitialized."""
    from bid_import_revert import is_lock_error  # lazy: that module sits above this one in the import graph
    if is_lock_error(exc):
        raise exc
    raise ReviewsNotInitialized("The card version tables could not be inspected") from None


def version_schema_problem(conn) -> str | None:
    """None when card_versions exists with every column of the model and `card_id` as its sole primary key;
    otherwise what is wrong. Raises ReviewsNotInitialized when the schema cannot be inspected: an unreadable schema
    is never reported as valid or as merely absent."""
    try:
        inspector = inspect(conn)
        if not inspector.has_table(_versions.name):
            return "table card_versions is missing"
        columns = {c["name"] for c in inspector.get_columns(_versions.name)}
        absent = sorted({c.name for c in _versions.columns} - columns)
        if absent:
            return f"table card_versions is missing columns: {', '.join(absent)}"
        if inspector.get_pk_constraint(_versions.name).get("constrained_columns") != ["card_id"]:
            return "table card_versions does not have card_id as its primary key"
    except SQLAlchemyError as exc:
        _inspection_failed(exc)
    return None


def _require_version_schema(conn) -> None:
    if version_schema_problem(conn) is not None:
        raise ReviewsNotInitialized("The card version tables are not set up correctly for this company")


def review_schema_problem(conn) -> str | None:
    """None when both tables a card change must write (card_versions as version_schema_problem requires it, and
    card_review_obligations with every column of its model) are present and well formed; otherwise what is wrong.
    The one structural check shared by the readers, the routes' gate and the flush listener. Raises
    ReviewsNotInitialized when the schema cannot be inspected."""
    problem = version_schema_problem(conn)
    if problem is not None:
        return problem
    try:
        inspector = inspect(conn)
        if not inspector.has_table(_obligations.name):
            return "table card_review_obligations is missing"
        absent = sorted({c.name for c in _obligations.columns} - {c["name"] for c in inspector.get_columns(_obligations.name)})
        if absent:
            return f"table card_review_obligations is missing columns: {', '.join(absent)}"
        if not inspector.has_table(_acks.name):
            return "table card_review_acknowledgments is missing (run init_company_card_reviews.py --apply)"
        absent = sorted({c.name for c in _acks.columns} - {c["name"] for c in inspector.get_columns(_acks.name)})
    except SQLAlchemyError as exc:
        _inspection_failed(exc)
    return f"table card_review_acknowledgments is missing columns: {', '.join(absent)}" if absent else None


def has_reviews(db) -> bool:
    """Both tables are present and well formed (review_schema_problem; read-only reflection of the session's connection)."""
    return review_schema_problem(db.connection()) is None


def require_reviews(db) -> None:
    if is_company_session(db) and not has_reviews(db):
        raise ReviewsNotInitialized("The review workflow is not set up for this company yet")


def is_company_session(db) -> bool:
    return db.info.get(_COMPANY_KEY) is not None


def _tables_ready(conn) -> bool:
    names = {r[0] for r in conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('card_versions','card_review_obligations','card_review_acknowledgments')")}
    return len(names) == 3


def has_pending(db, card_id: int) -> bool:
    """Whether the card has a pending obligation (company sessions whose database has the review tables only)."""
    conn = db.connection()
    if not is_company_session(db) or not _tables_ready(conn):
        return False
    if db.info.get(_REVIEWERS) is not None:
        reconcile(db, card_id)  # a removed reviewer must not keep an obligation (and so an undo) blocked
    return conn.execute(select(_obligations.c.id).where(
        _obligations.c.card_id == card_id, _obligations.c.status == STATUS_PENDING).limit(1)).first() is not None


# --- required reviewers and their acknowledgments ---------------------------------------------------------------

def set_reviewer_provider(db, provider) -> None:
    """`provider(role) -> list[int]`: the user ids currently required to review for `role` ("admin" | "super_admin"),
    read from the identity database each time it is called. Set by the company routes on every company session."""
    db.info[_REVIEWERS] = provider


def _reviewer_ids(session: Session, role: str) -> list[int]:
    provider = session.info.get(_REVIEWERS)
    if provider is None:
        raise ReviewsNotInitialized("The required reviewers of this company could not be determined")
    return sorted({int(i) for i in provider(role)})


def _sync_acks(conn, obligation_id: int, version: int, reviewers: list[int], now: datetime, *, reset: bool) -> None:
    """Make the acknowledgment rows of one pending obligation match `reviewers`.

    A required reviewer without a row gets a pending one (a void row of a returning reviewer is revived). A pending row
    of someone no longer required is voided with a reason, never cleared. With `reset` (the obligation moved to a new
    version) every other row of a required reviewer is pending again for `version` and a cleared row of someone no
    longer required is voided too; without it, cleared rows are kept as they are."""
    rows = {r.reviewer_user_id: r for r in conn.execute(select(_acks).where(_acks.c.obligation_id == obligation_id))}
    for user_id in reviewers:
        row = rows.get(user_id)
        if row is None:
            conn.execute(insert(_acks).values(
                obligation_id=obligation_id, reviewer_user_id=user_id, status=STATUS_PENDING, card_version=version,
                created_at=now, updated_at=now))
        elif row.status == STATUS_VOID or (reset and (row.status != STATUS_PENDING or row.card_version != version)):
            conn.execute(update(_acks).where(_acks.c.id == row.id).values(
                status=STATUS_PENDING, card_version=version, cleared_at=None, closed_reason=None, updated_at=now))
    for user_id, row in rows.items():
        if user_id not in reviewers and row.status != STATUS_VOID and (reset or row.status == STATUS_PENDING):
            conn.execute(update(_acks).where(_acks.c.id == row.id).values(
                status=STATUS_VOID, closed_reason="no_longer_required", updated_at=now))


def _settle(conn, obligation_id: int, now: datetime) -> None:
    """Close a pending obligation nobody is waiting on any more: cleared when at least one required reviewer cleared
    it and none is pending; void ("no_required_reviewer") when no reviewer is left. Never invents an acknowledgment."""
    acks = [r.status for r in conn.execute(select(_acks.c.status).where(_acks.c.obligation_id == obligation_id))]
    if STATUS_PENDING in acks:
        return
    if STATUS_CLEARED in acks:
        last = conn.execute(select(_acks.c.reviewer_user_id, _acks.c.cleared_at).where(
            _acks.c.obligation_id == obligation_id, _acks.c.status == STATUS_CLEARED
        ).order_by(_acks.c.cleared_at.desc(), _acks.c.id.desc())).first()
        conn.execute(update(_obligations).where(_obligations.c.id == obligation_id, _obligations.c.status == STATUS_PENDING)
                     .values(status=STATUS_CLEARED, cleared_by_user_id=last.reviewer_user_id,
                             cleared_at=last.cleared_at or now, updated_at=now))
    else:
        conn.execute(update(_obligations).where(_obligations.c.id == obligation_id, _obligations.c.status == STATUS_PENDING)
                     .values(status=STATUS_VOID, closed_reason="no_required_reviewer", updated_at=now))


def reconcile(db, card_id: int | None = None) -> None:
    """Bring the acknowledgments of the pending obligations (of one card, or of all) in line with the CURRENT required
    reviewers, then settle the ones nobody is waiting on. The caller holds the write lock and commits. Idempotent."""
    if not is_company_session(db):
        return
    conn = db.connection()
    if not _tables_ready(conn):
        return
    query = select(_obligations).where(_obligations.c.status == STATUS_PENDING)
    if card_id is not None:
        query = query.where(_obligations.c.card_id == card_id)
    now = utcnow()
    cache: dict[str, list[int]] = {}
    for obligation in conn.execute(query).all():
        if obligation.reviewer_role not in cache:
            cache[obligation.reviewer_role] = _reviewer_ids(db, obligation.reviewer_role)
        _sync_acks(conn, obligation.id, obligation.card_version, cache[obligation.reviewer_role], now, reset=False)
        _settle(conn, obligation.id, now)


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


def note_lost_entry(db, card: Card, user, from_column, to_column) -> None:
    """A company Admin moved `card` into a LOST stage from another stage: only the Super Admin reviews it.

    The Super Admin's own moves create nothing (no self-review), and neither do a Sales Member's or a Visitor's. The
    caller has already established that the card is entering LOST, so updating a card that is already LOST never
    reaches this. The role is the one the company context reports, never a client value."""
    if not is_company_session(db) or getattr(user, "is_global_admin", True) or not getattr(user, "can_manage_cards", False):
        return
    require_reviews(db)
    entry = {
        "kind": KIND_LOST_MOVE, "user_id": user.id, "at": utcnow().isoformat(),
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


def board_card_versions(db, board_id: int) -> dict[int, int]:
    """{card_id: version} for every card of a board that has a row (read-only; absent cards are version 0).

    Same fail-closed schema validation as card_versions. Read it before loading the board's cards.
    """
    conn = db.connection()
    _require_version_schema(conn)
    rows = conn.exec_driver_sql(
        "SELECT v.card_id, v.version FROM card_versions v JOIN cards c ON c.id = v.card_id "
        "JOIN columns col ON col.id = c.column_id WHERE col.board_id = ?", (board_id,))
    return {card_id: version for card_id, version in rows}


def board_version_snapshot(db, board_id: int) -> dict[int, int]:
    """{card_id: version} for EVERY card of the board (0 for a card without a row), for a caller that must later prove
    it acts on the state it was shown (the AI assistant). Read the snapshot before loading the cards it describes.
    Fails closed like card_versions."""
    versions = board_card_versions(db, board_id)
    ids = [row[0] for row in db.connection().exec_driver_sql(
        "SELECT c.id FROM cards c JOIN columns col ON col.id = c.column_id WHERE col.board_id = ?", (board_id,))]
    return {card_id: versions.get(card_id, 0) for card_id in ids}


# Content columns of the Bid-details row. Its timestamps are bookkeeping, not card content.
_BID_FIELDS = tuple(c.key for c in CardBidDetails.__table__.columns
                    if c.key not in ("id", "card_id", "created_at", "updated_at"))


def _bid_details_changed(details: CardBidDetails, new: bool) -> bool:
    """Whether a pending Bid-details row carries a real content change (a new row counts only if it holds a value)."""
    state = inspect(details)
    for field in _BID_FIELDS:
        history = state.attrs[field].history
        if new:
            if history.added and history.added[0] is not None:
                return True
        elif history.added and (not history.deleted or history.deleted[0] != history.added[0]):
            return True
    return False


def _bid_details_card_ids(session: Session, deleted_ids: set) -> set:
    """Ids of persisted cards whose Bid-details row is inserted, changed or deleted in this flush.

    A card that is itself new or deleted in the flush is left to the card's own handling (a new card starts at
    version 0; a deleted card's pending reviews are closed by the deleted-card branch).
    """
    owners = []
    for details in session.new:
        if isinstance(details, CardBidDetails) and _bid_details_changed(details, new=True):
            owners.append(details)
    for details in session.dirty:
        if isinstance(details, CardBidDetails) and details not in session.deleted and _bid_details_changed(details, new=False):
            owners.append(details)
    owners.extend(d for d in session.deleted if isinstance(d, CardBidDetails))
    ids = set()
    for details in owners:
        card = details.card
        if card is not None:
            if card.id is not None and card not in session.new and card not in session.deleted:
                ids.add(card.id)
        elif details.card_id is not None and details.card_id not in deleted_ids:
            ids.add(details.card_id)
    return ids


def _bump(conn, card_id: int, now: datetime) -> int:
    """Raise the card's version by one. The UPDATE takes the write lock first, so the read that follows is exact."""
    conn.execute(insert(_versions).prefix_with("OR IGNORE").values(card_id=card_id, version=0, updated_at=now))
    conn.execute(update(_versions).where(_versions.c.card_id == card_id)
                 .values(version=_versions.c.version + 1, updated_at=now))
    return conn.execute(select(_versions.c.version).where(_versions.c.card_id == card_id)).scalar_one()


def _upsert_pending(conn, card_id: int, role: str, version: int, entry: dict, now: datetime, reviewers: list[int]) -> None:
    row = conn.execute(select(_obligations.c.id, _obligations.c.context_json, _obligations.c.refresh_count)
                       .where(_obligations.c.card_id == card_id, _obligations.c.reviewer_role == role,
                              _obligations.c.status == STATUS_PENDING)).first()
    entry = {**entry, "version": version}
    if row is None:
        if not reviewers:
            return  # nobody holds this role in the company: nothing to acknowledge, nothing is faked
        obligation_id = conn.execute(insert(_obligations).values(
            card_id=card_id, reviewer_role=role, status=STATUS_PENDING, card_version=version,
            trigger_user_id=entry.get("user_id"), context_json=json.dumps([entry]), refresh_count=0,
            created_at=now, updated_at=now)).inserted_primary_key[0]
    else:
        obligation_id = row.id
        context = (json.loads(row.context_json) + [entry])[-CONTEXT_LIMIT:]
        conn.execute(update(_obligations).where(_obligations.c.id == row.id).values(
            card_version=version, trigger_user_id=entry.get("user_id"), context_json=json.dumps(context),
            refresh_count=row.refresh_count + 1, updated_at=now))
    _sync_acks(conn, obligation_id, version, reviewers, now, reset=True)
    _settle(conn, obligation_id, now)


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
    for card_id in _bid_details_card_ids(session, {c.id for c in deleted}):
        changed.setdefault(card_id, None)  # a Bid-details row is part of its card: one bump per card per flush
    created = any(isinstance(c, Card) for c in session.new)
    if not changed and not deleted and not created:
        return
    conn = session.connection()
    # A card change must never commit without its version: an absent or malformed table refuses the flush (the whole
    # transaction rolls back), whether or not the change also needs a review obligation. The same holds for creating a
    # card, although it gets no row (a new card is implicitly version 0): a card must not come into being while its
    # version schema is invalid, or the first read of its version would fail for a card nobody could have prevented.
    if review_schema_problem(conn) is not None:
        raise ReviewsNotInitialized("The review workflow is not set up for this company yet")
    now = utcnow()
    versions = {card_id: _bump(conn, card_id, now) for card_id in changed}
    triggered: dict[int, set[str]] = {}  # the roles a card's own trigger writes below; every other pending role just follows
    for intent in intents:
        triggered.setdefault(intent["card"].id, set()).update(intent["roles"])
    for card_id, version in versions.items():
        # Any change that does not write a role's obligation itself (an Admin edit, a forward move, an import, or a trigger
        # of ANOTHER role: an Admin's backward move writes only the Super Admin's) moves that role's pending obligation to
        # the new version, so a reviewer can still clear the latest state.
        own = triggered.get(card_id, set())
        pending_ids = select(_obligations.c.id).where(
            _obligations.c.card_id == card_id, _obligations.c.status == STATUS_PENDING,
            _obligations.c.reviewer_role.notin_(list(own)))
        # a new version: every acknowledgment of the old one is pending again (voided rows stay void)
        conn.execute(update(_acks).where(_acks.c.obligation_id.in_(pending_ids), _acks.c.status != STATUS_VOID)
                     .values(status=STATUS_PENDING, card_version=version, cleared_at=None, updated_at=now))
        conn.execute(update(_obligations).where(
            _obligations.c.card_id == card_id, _obligations.c.status == STATUS_PENDING,
            _obligations.c.reviewer_role.notin_(list(own)),
        ).values(card_version=version, updated_at=now, refresh_count=_obligations.c.refresh_count + 1))
    reviewers: dict[str, list[int]] = {}
    for intent in intents:
        card_id = intent["card"].id
        for role in intent["roles"]:
            if role not in reviewers:
                reviewers[role] = _reviewer_ids(session, role)
            _upsert_pending(conn, card_id, role, versions[card_id], intent["entry"], now, reviewers[role])
    session.info.pop(_INTENTS, None)
    for card in deleted:
        gone = select(_obligations.c.id).where(_obligations.c.card_id == card.id, _obligations.c.status == STATUS_PENDING)
        conn.execute(update(_acks).where(_acks.c.obligation_id.in_(gone), _acks.c.status == STATUS_PENDING)
                     .values(status=STATUS_VOID, closed_reason="card_deleted", updated_at=now))
        conn.execute(update(_obligations).where(_obligations.c.card_id == card.id, _obligations.c.status == STATUS_PENDING)
                     .values(status=STATUS_VOID, closed_reason="card_deleted", updated_at=now))


@event.listens_for(Session, "after_soft_rollback")
def _forget_intents(session: Session, previous_transaction) -> None:
    session.info.pop(_INTENTS, None)


# --- reading and clearing ------------------------------------------------------------------------------------

def _public(row, card_title: str, column_title: str, board_id: int, mine: str, total: int, done: int) -> dict:
    return {
        "id": row.id, "board_id": board_id, "card_id": row.card_id, "card_title": card_title, "column_title": column_title,
        "reviewer_role": row.reviewer_role, "card_version": row.card_version, "status": row.status,
        "created_at": row.created_at, "updated_at": row.updated_at, "refresh_count": row.refresh_count,
        "triggers": json.loads(row.context_json),
        "my_status": mine, "reviewers_total": total, "reviewers_cleared": done,
    }


def pending_for_role(db, board_id: int, role: str, user_id: int) -> list[dict]:
    """The obligations of `role` on one board that `user_id` still has to clear, oldest first (read-only).

    Each item carries the caller's own status ("pending") and how many of the required reviewers have cleared it. A
    reviewer who already cleared an obligation does not see it again, whatever the others did; a current reviewer
    without a row yet (a newly added Admin, or an obligation older than the acknowledgment table) sees it pending."""
    from models import BoardColumn

    rows = db.execute(
        select(_obligations, Card.title, BoardColumn.title.label("column_title"))
        .join(Card, Card.id == _obligations.c.card_id)
        .join(BoardColumn, BoardColumn.id == Card.column_id)
        .where(_obligations.c.reviewer_role == role, _obligations.c.status == STATUS_PENDING,
               BoardColumn.board_id == board_id)
        .order_by(_obligations.c.created_at, _obligations.c.id)
    ).all()
    if not rows:
        return []
    current = _reviewer_ids(db, role)
    acks: dict[int, dict[int, str]] = {}
    for a in db.execute(select(_acks.c.obligation_id, _acks.c.reviewer_user_id, _acks.c.status)
                        .where(_acks.c.obligation_id.in_([r.id for r in rows]))):
        acks.setdefault(a.obligation_id, {})[a.reviewer_user_id] = a.status
    items = []
    for r in rows:
        by_user = acks.get(r.id, {})
        # required: every current reviewer, plus a former reviewer's completed acknowledgment (it still counts)
        required = set(current) | {u for u, st in by_user.items() if st == STATUS_CLEARED}
        mine = by_user.get(user_id)
        if user_id in current and mine in (None, STATUS_VOID):
            mine = STATUS_PENDING  # newly responsible: the obligation is still open
        if mine != STATUS_PENDING or user_id not in current:
            continue
        done = sum(1 for u in required if by_user.get(u) == STATUS_CLEARED)
        items.append(_public(r, r.title, r.column_title, board_id, STATUS_PENDING, len(required), done))
    return items


def clear_review(db, card: Card, role: str, version: int, reviewer_id: int) -> dict:
    """Clear `reviewer_id`'s own acknowledgment of the pending `role` obligation of `card`, only if `version` is the
    card's current version. The obligation itself is cleared only when no required reviewer is still pending.

    The caller holds the write lock (begin_write) and commits; nothing is changed on a refusal.
    ReviewNotFound: nothing pending for this role on this card, or the caller is not (or no longer) a required
    reviewer, or already cleared it. ReviewStale: the card changed since the reviewer saw it.
    """
    if role not in REVIEW_ROLES:
        raise ReviewNotFound("No such review")
    conn = db.connection()
    reconcile(db, card.id)  # reviewer changes since the obligation was written are applied before anything is decided
    row = conn.execute(select(_obligations).where(
        _obligations.c.card_id == card.id, _obligations.c.reviewer_role == role,
        _obligations.c.status == STATUS_PENDING)).first()
    if row is None:
        raise ReviewNotFound("Nothing to review for this card")
    mine = conn.execute(select(_acks).where(
        _acks.c.obligation_id == row.id, _acks.c.reviewer_user_id == reviewer_id)).first()
    if mine is None or mine.status != STATUS_PENDING:
        raise ReviewNotFound("Nothing to review for this card")
    current = conn.execute(select(_versions.c.version).where(_versions.c.card_id == card.id)).scalar()
    if version != row.card_version or current != row.card_version or mine.card_version != row.card_version:
        raise ReviewStale("The card changed since you opened it. Review the latest version.")
    now = utcnow()
    result = conn.execute(update(_acks).where(
        _acks.c.id == mine.id, _acks.c.status == STATUS_PENDING, _acks.c.card_version == version,
    ).values(status=STATUS_CLEARED, cleared_at=now, updated_at=now))
    if result.rowcount != 1:
        raise ReviewStale("The review was already completed or the card changed.")
    _settle(conn, row.id, now)
    completed = conn.execute(select(_obligations.c.status).where(_obligations.c.id == row.id)).scalar() == STATUS_CLEARED
    return {"id": row.id, "card_id": card.id, "reviewer_role": role, "card_version": version, "cleared_at": now,
            "triggers": json.loads(row.context_json), "completed": completed}
