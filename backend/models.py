"""SQLAlchemy ORM models for PM database."""
import enum
from datetime import date, datetime
from sqlalchemy import Boolean, Column as SQLColumn, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import backref, relationship

# Company-owned commercial tables. Each company database holds exactly these
# (plus its local `users` reference table, CompanyUser); nothing here is
# identity or authentication state.
CompanyBase = declarative_base()
# LEGACY single-database runtime: the pre-split `users` and `auth_tokens` tables.
# Base.metadata is completed at the bottom of this module with copies of the
# CompanyBase tables so the current runtime and tests, which still create the
# whole schema from Base.metadata in one database, keep working. Nothing new
# should be built on it (see model_ownership.py).
Base = declarative_base()

SOURCE_SYSTEM_SALCOMP_BIDS = "salcomp-bids"


class UserRole(str, enum.Enum):
    """User role. More profiles may be added in the future."""

    ADMIN = "admin"
    MEMBER = "member"


class CompanyUser(CompanyBase):
    """Local reference to a person in the central identity database.

    `id` is the canonical identity_users.id, supplied by the caller (never
    generated here). Only what commercial rows need to point at someone and
    show a name. No password, token, role or membership: those are central and
    authoritative there, and a row here grants no access to the company. Rows
    are kept when the central user is deactivated, so history and foreign keys
    survive. Not a cross-database foreign key: the link is the shared number.

    `username` is the display name (the legacy username today). The table keeps
    the name `users` and a column subset of the legacy table, so the existing
    commercial foreign keys (and a legacy database) still resolve.
    """

    __tablename__ = "users"

    id = SQLColumn(Integer, primary_key=True, autoincrement=False)
    username = SQLColumn(String, nullable=False)
    full_name = SQLColumn(String, nullable=True)
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False)
    updated_at = SQLColumn(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    boards = relationship("Board", back_populates="user", cascade="all, delete-orphan")
    messages = relationship("ConversationMessage", back_populates="user", cascade="all, delete-orphan")
    import_batches = relationship("ImportBatch", back_populates="user")

    def __repr__(self):
        return f"<CompanyUser {self.id}>"


class Board(CompanyBase):
    """Kanban board model."""

    __tablename__ = "boards"

    id = SQLColumn(Integer, primary_key=True, index=True)
    user_id = SQLColumn(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = SQLColumn(String, nullable=False, default="My Board")
    description = SQLColumn(String, nullable=True)
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False, index=True)
    updated_at = SQLColumn(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    user = relationship("CompanyUser", back_populates="boards")
    columns = relationship("BoardColumn", back_populates="board", cascade="all, delete-orphan")
    messages = relationship("ConversationMessage", back_populates="board", cascade="all, delete-orphan")
    card_actions = relationship("CardAction", back_populates="board", cascade="all, delete-orphan")
    import_batches = relationship("ImportBatch", back_populates="board", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Board {self.title}>"


class BoardColumn(CompanyBase):
    """Kanban column model."""

    __tablename__ = "columns"

    id = SQLColumn(Integer, primary_key=True, index=True)
    board_id = SQLColumn(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False, index=True)
    title = SQLColumn(String, nullable=False)
    position = SQLColumn(Integer, nullable=False, default=0)
    # Commercial identity of the stage: "open", "won" or "lost"
    # (lead_options.STAGE_KINDS). Reports key on this, never on the title.
    # Existing databases get it via migrate_stage_kind.py, run explicitly.
    stage_kind = SQLColumn(String, nullable=False, default="open", server_default="open")
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False)
    updated_at = SQLColumn(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    board = relationship("Board", back_populates="columns")
    cards = relationship("Card", back_populates="column", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<BoardColumn {self.title}>"


class Card(CompanyBase):
    """Kanban card model."""

    __tablename__ = "cards"

    id = SQLColumn(Integer, primary_key=True, index=True)
    column_id = SQLColumn(Integer, ForeignKey("columns.id", ondelete="CASCADE"), nullable=False, index=True)
    title = SQLColumn(String, nullable=False)
    details = SQLColumn(Text, nullable=True)
    position = SQLColumn(Integer, nullable=False, default=0)
    # Sales lead fields - all optional; allowed values live in lead_options.py.
    # init_db() does not add these to an older cards table; it refuses such a database
    # (DatabaseSchemaIncomplete) until an explicit migration, not yet written, is applied.
    company = SQLColumn(String, nullable=True)
    contact = SQLColumn(String, nullable=True)
    # The contact's email and telephone, optional (added by migrate_card_contact.py; schemas.py validates them).
    contact_email = SQLColumn(String(254), nullable=True)
    contact_phone = SQLColumn(String(40), nullable=True)
    # deal_value is the amount for deal_year (the "current year"); the next
    # year's amount is deal_value_next_year (0 = none). Both share `currency`.
    # deal_year is stamped when the card is created and only changes through
    # migrate_deal_year_rollover.py, so the stored amounts never change year
    # meaning because the calendar did. Separate from Bid revenue.
    deal_value = SQLColumn(Float, nullable=True)
    deal_value_next_year = SQLColumn(Float, nullable=False, default=0, server_default=text("0"))
    deal_year = SQLColumn(
        Integer, nullable=False, default=lambda: date.today().year,
        server_default=text("(CAST(strftime('%Y', 'now') AS INTEGER))"),
    )
    currency = SQLColumn(String(3), nullable=True)
    expected_close_date = SQLColumn(Date, nullable=True)
    lead_source = SQLColumn(String, nullable=True)
    product_line = SQLColumn(String, nullable=True)
    # LOW | MEDIUM | HIGH set by the sales team; NULL = not assigned.
    confidence_level = SQLColumn(String(6), nullable=True)
    salesperson_id = SQLColumn(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False, index=True)
    updated_at = SQLColumn(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    column = relationship("BoardColumn", back_populates="cards")
    salesperson = relationship("CompanyUser")
    actions = relationship("CardAction", back_populates="card", cascade="all, delete-orphan")
    bid_details = relationship("CardBidDetails", back_populates="card", cascade="all, delete-orphan", uselist=False)
    source_ref = relationship("CardSourceRef", back_populates="card", cascade="all, delete-orphan", uselist=False)
    # Import history, deliberately apart from edit_history (which backs the "M" badge).
    import_changes = relationship("CardImportChange", back_populates="card", cascade="all, delete-orphan")
    edit_history = relationship(
        "CardEditHistory",
        back_populates="card",
        cascade="all, delete-orphan",
        order_by="CardEditHistory.created_at.desc()",
    )

    @property
    def has_member_edits(self) -> bool:
        """Whether a member has ever edited this card's content.

        Derived from the presence of history rows rather than a denormalized
        flag, so there's a single source of truth: CardEditHistory rows are
        only ever written for member edits (see routes.update_card).
        """
        return bool(self.edit_history)

    @property
    def salesperson_name(self):
        return self.salesperson.username if self.salesperson else None

    # Action / Next Action Date live in card_bid_details (Bid report columns
    # "Proximo passo" / "Deadline"); exposed here so the shared update, diff
    # and undo paths treat them like any other card field. A details row is
    # created only when a value is first set.
    def _bid_value(self, name):
        return getattr(self.bid_details, name) if self.bid_details else None

    def _set_bid_value(self, name, value):
        if self.bid_details is None:
            if value is None:
                return
            self.bid_details = CardBidDetails()
        setattr(self.bid_details, name, value)

    next_step = property(lambda self: self._bid_value("next_step"), lambda self, v: self._set_bid_value("next_step", v))
    next_step_due = property(
        lambda self: self._bid_value("next_step_due"), lambda self, v: self._set_bid_value("next_step_due", v)
    )

    def __repr__(self):
        return f"<Card {self.title}>"


class ConversationMessage(CompanyBase):
    """Conversation message model for AI chat history."""

    __tablename__ = "conversation_messages"

    id = SQLColumn(Integer, primary_key=True, index=True)
    board_id = SQLColumn(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = SQLColumn(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = SQLColumn(String, nullable=False)  # 'user' or 'assistant'
    content = SQLColumn(Text, nullable=False)
    meta_data = SQLColumn(Text, nullable=True)  # JSON string
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False, index=True)

    # Relationships
    board = relationship("Board", back_populates="messages")
    user = relationship("CompanyUser", back_populates="messages")
    actions = relationship("CardAction", back_populates="message")

    def __repr__(self):
        return f"<Message {self.role}: {self.content[:30]}>"


class CardAction(CompanyBase):
    """Card action model for audit trail."""

    __tablename__ = "card_actions"

    id = SQLColumn(Integer, primary_key=True, index=True)
    board_id = SQLColumn(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False, index=True)
    message_id = SQLColumn(Integer, ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True, index=True)
    action_type = SQLColumn(String, nullable=False)  # 'create', 'update', 'move', 'delete'
    card_id = SQLColumn(Integer, ForeignKey("cards.id", ondelete="SET NULL"), nullable=True, index=True)
    changes = SQLColumn(Text, nullable=True)  # JSON string
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False, index=True)

    # Relationships
    board = relationship("Board", back_populates="card_actions")
    message = relationship("ConversationMessage", back_populates="actions")
    card = relationship("Card", back_populates="actions")

    def __repr__(self):
        return f"<CardAction {self.action_type}>"


class CardEditHistory(CompanyBase):
    """History of card content edits made by member users.

    Only written for edits made by members (see routes.update_card) - admin
    edits are not tracked here. This is what backs the "M" badge and its
    detail modal in the UI, and is a distinct concern from CardAction (which
    audits AI-driven changes).
    """

    __tablename__ = "card_edit_history"

    id = SQLColumn(Integer, primary_key=True, index=True)
    card_id = SQLColumn(Integer, ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = SQLColumn(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    changes = SQLColumn(Text, nullable=False)  # JSON string: {"title": {"old", "new"}, "details": {"old", "new"}}
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False, index=True)

    # Relationships
    card = relationship("Card", back_populates="edit_history")
    user = relationship("CompanyUser")

    def __repr__(self):
        return f"<CardEditHistory card={self.card_id}>"


class ImportBatch(CompanyBase):
    """One Salcomp Bid import attempt (metadata only): a completed import, or a
    failed attempt that wrote nothing. Written by bid_import_service.py."""

    __tablename__ = "import_batches"

    id = SQLColumn(Integer, primary_key=True, index=True)
    board_id = SQLColumn(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = SQLColumn(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    filename = SQLColumn(String, nullable=False)
    file_sha256 = SQLColumn(String(64), nullable=False, index=True)
    mapping_json = SQLColumn(Text, nullable=True)
    created_count = SQLColumn(Integer, nullable=False, default=0, server_default="0")
    updated_count = SQLColumn(Integer, nullable=False, default=0, server_default="0")
    skipped_count = SQLColumn(Integer, nullable=False, default=0, server_default="0")
    review_count = SQLColumn(Integer, nullable=False, default=0, server_default="0")
    # completed: written with its cards in one transaction. failed: an attempt
    # that wrote nothing; recorded afterwards in its own transaction, with
    # failure_kind (validation | conflict | unexpected) and a short reason that
    # never contains row content. pending exists only inside the import's
    # transaction.
    status = SQLColumn(String, nullable=False, default="pending", server_default="pending")
    failure_kind = SQLColumn(String, nullable=True)
    failure_reason = SQLColumn(String(500), nullable=True)
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False, index=True)
    updated_at = SQLColumn(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    completed_at = SQLColumn(DateTime, nullable=True)

    board = relationship("Board", back_populates="import_batches")
    user = relationship("CompanyUser", back_populates="import_batches")
    source_refs = relationship("CardSourceRef", back_populates="last_import_batch")
    card_changes = relationship("CardImportChange", back_populates="import_batch", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ImportBatch {self.filename}>"


class ImportBatchLedger(CompanyBase):
    """Durable record of what one completed Bid import did, kept so the batch
    can be reverted (Part 23). Its existence makes a batch revertible; batches
    imported before Part 23 have none. Unlike card_import_changes (a UI diff
    that goes away with the card) it's never deleted with a card.
    """

    __tablename__ = "import_batch_ledgers"

    id = SQLColumn(Integer, primary_key=True, index=True)
    import_batch_id = SQLColumn(
        Integer, ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    ledger_version = SQLColumn(Integer, nullable=False, default=1, server_default="1")
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False)
    # Set by the revert's claim inside its transaction; only final after COMMIT.
    reverted_at = SQLColumn(DateTime, nullable=True)
    reverted_by_user_id = SQLColumn(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    # Final result of the revert (NULL until reverted). Counts only; no cell content.
    revert_outcome = SQLColumn(String, nullable=True)
    no_commercial_changes = SQLColumn(Boolean, nullable=False, default=False, server_default="0")
    cards_deleted_count = SQLColumn(Integer, nullable=False, default=0, server_default="0")
    cards_restored_count = SQLColumn(Integer, nullable=False, default=0, server_default="0")
    moves_reverted_count = SQLColumn(Integer, nullable=False, default=0, server_default="0")
    already_deleted_count = SQLColumn(Integer, nullable=False, default=0, server_default="0")
    result_summary_json = SQLColumn(Text, nullable=True)

    import_batch = relationship(
        "ImportBatch", backref=backref("ledger", uselist=False, cascade="all, delete-orphan")
    )
    items = relationship("ImportBatchItem", back_populates="ledger", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ImportBatchLedger batch={self.import_batch_id}>"


class ImportBatchItem(CompanyBase):
    """One card a batch created, updated or observed. card_id and board_id are
    plain integers, not foreign keys: the item must outlive the card."""

    __tablename__ = "import_batch_items"
    __table_args__ = (
        UniqueConstraint("ledger_id", "card_id", name="uq_import_batch_items_ledger_card"),
    )

    id = SQLColumn(Integer, primary_key=True, index=True)
    ledger_id = SQLColumn(Integer, ForeignKey("import_batch_ledgers.id", ondelete="CASCADE"), nullable=False)
    card_id = SQLColumn(Integer, nullable=False, index=True)
    board_id = SQLColumn(Integer, nullable=False)
    source_key = SQLColumn(String, nullable=False)
    # created | updated | observed
    action = SQLColumn(String, nullable=False)
    # observed only: unchanged | older_source | skipped_by_decision | update_not_confirmed
    observation = SQLColumn(String, nullable=True)
    before_json = SQLColumn(Text, nullable=True)  # NULL for created and observed
    after_json = SQLColumn(Text, nullable=True)   # NULL for observed
    bid_details_created = SQLColumn(Boolean, nullable=False, default=False, server_default="0")
    stage_event_max_id_after = SQLColumn(Integer, nullable=True)

    ledger = relationship("ImportBatchLedger", back_populates="items")

    def __repr__(self):
        return f"<ImportBatchItem card={self.card_id} {self.action}>"


class CardBidDetails(CompanyBase):
    """Commercial fields from the Salcomp Bid report, linked 1:1 to a card."""

    __tablename__ = "card_bid_details"

    id = SQLColumn(Integer, primary_key=True, index=True)
    card_id = SQLColumn(Integer, ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    plant = SQLColumn(String, nullable=True)
    customer_type = SQLColumn(String, nullable=True)
    business_type = SQLColumn(String, nullable=True)
    product_category = SQLColumn(String, nullable=True)
    product = SQLColumn(String, nullable=True)
    solution_type = SQLColumn(String, nullable=True)
    volume_current_year = SQLColumn(Integer, nullable=True)
    volume_next_year = SQLColumn(Integer, nullable=True)
    production_start_date = SQLColumn(Date, nullable=True)
    # Annual revenue in integer cents (bid_money.py converts exactly; NULL =
    # not informed). Separate from cards.deal_value, never summed with it.
    revenue_current_year_cents = SQLColumn(Integer, nullable=True)
    revenue_next_year_cents = SQLColumn(Integer, nullable=True)
    revenue_currency = SQLColumn(String(3), nullable=False, default="BRL", server_default="BRL")
    status_note = SQLColumn(Text, nullable=True)
    project_lead = SQLColumn(String, nullable=True)
    # The report's CPM column, verbatim. A source name like project_lead: never matched to a user or salesperson.
    cpm = SQLColumn(String, nullable=True)
    next_step = SQLColumn(Text, nullable=True)
    next_step_due = SQLColumn(Date, nullable=True)
    close_reason = SQLColumn(String, nullable=True)
    close_reason_note = SQLColumn(Text, nullable=True)
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False, index=True)
    updated_at = SQLColumn(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    card = relationship("Card", back_populates="bid_details")

    def __repr__(self):
        return f"<CardBidDetails card={self.card_id}>"


class CardImportChange(CompanyBase):
    """What one import batch actually changed on one existing card.

    Separate from card_edit_history on purpose: that table means "a member
    edited this" (the "M" badge), and an import is not a manual edit. Written
    only by bid_import_service.py, in the same transaction as the change, for
    confirmed updates that changed at least one field - never for created,
    unchanged, skipped or pending rows. At most one row per card and batch.
    """

    __tablename__ = "card_import_changes"
    __table_args__ = (
        UniqueConstraint("card_id", "import_batch_id", name="uq_card_import_changes_card_batch"),
    )

    id = SQLColumn(Integer, primary_key=True, index=True)
    card_id = SQLColumn(Integer, ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True)
    import_batch_id = SQLColumn(Integer, ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = SQLColumn(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    # JSON {"card.title": {"old", "new"}, "bid_details.next_step": {...}, ...}:
    # only the fields that changed (dates as ISO text; the row hash is left out).
    changes = SQLColumn(Text, nullable=False)
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False, index=True)

    card = relationship("Card", back_populates="import_changes")
    import_batch = relationship("ImportBatch", back_populates="card_changes")
    user = relationship("CompanyUser")

    def __repr__(self):
        return f"<CardImportChange card={self.card_id} batch={self.import_batch_id}>"


class CardSourceRef(CompanyBase):
    """Source identity for an imported card: at most one per card, and the
    database enforces that (source_system, source_key) is unique.

    For Salcomp Bids the planned key is Codigo + Cliente (Codigo alone is not
    unique in the report); the normalization will be defined with the
    importer, which doesn't exist yet.
    """

    __tablename__ = "card_source_refs"
    __table_args__ = (
        UniqueConstraint("source_system", "source_key", name="uq_card_source_refs_system_key"),
    )

    id = SQLColumn(Integer, primary_key=True, index=True)
    card_id = SQLColumn(Integer, ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    source_system = SQLColumn(
        String,
        nullable=False,
        default=SOURCE_SYSTEM_SALCOMP_BIDS,
        server_default=SOURCE_SYSTEM_SALCOMP_BIDS,
        index=True,
    )
    source_key = SQLColumn(String, nullable=False)
    source_code = SQLColumn(String, nullable=False)
    source_status = SQLColumn(String, nullable=True)
    source_stage = SQLColumn(String, nullable=True)
    source_created_at = SQLColumn(DateTime, nullable=True)
    source_updated_at = SQLColumn(DateTime, nullable=True)
    source_last_edited_at = SQLColumn(DateTime, nullable=True)
    last_import_batch_id = SQLColumn(Integer, ForeignKey("import_batches.id", ondelete="SET NULL"), nullable=True, index=True)
    source_row_hash = SQLColumn(String(64), nullable=True)
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False, index=True)
    updated_at = SQLColumn(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    card = relationship("Card", back_populates="source_ref")
    last_import_batch = relationship("ImportBatch", back_populates="source_refs")

    def __repr__(self):
        return f"<CardSourceRef {self.source_system}:{self.source_key}>"


class CardStageEvent(CompanyBase):
    """Append-only history of a card's stage: creation, moves, wins, losses,
    reopenings and deletion. Written only by stage_history.py's flush
    listener, in the same transaction as the change it records.

    card_id, board_id and the column ids are deliberately plain integers, not
    foreign keys, and Card has no relationship to this table: the history
    must outlive deleted cards and columns (a card's last event is then
    "deleted"). Stage kinds are copied at event time so the history stays
    meaningful after a column is deleted. Titles are never stored.
    """

    __tablename__ = "card_stage_events"

    id = SQLColumn(Integer, primary_key=True, index=True)
    card_id = SQLColumn(Integer, nullable=False, index=True)
    board_id = SQLColumn(Integer, nullable=False, index=True)
    # created | moved | won | lost | reopened | deleted | baseline
    event_type = SQLColumn(String, nullable=False)
    from_column_id = SQLColumn(Integer, nullable=True)
    from_stage_kind = SQLColumn(String, nullable=True)
    to_column_id = SQLColumn(Integer, nullable=True)
    to_stage_kind = SQLColumn(String, nullable=True)
    user_id = SQLColumn(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # api | ai | undo | seed | migration | system
    source = SQLColumn(String, nullable=False)
    occurred_at = SQLColumn(DateTime, nullable=False, index=True)
    # The card's deal value and currency at the moment of the event (Part 20),
    # copied from the card in the same transaction. value_snapshot_taken says
    # whether they were recorded at all: false for events written before
    # snapshots existed, whose historical value is unknown (never back-filled).
    # When true, null value/currency means the card genuinely had none.
    # Float, like cards.deal_value: the snapshot is an exact copy of it.
    deal_value_snapshot = SQLColumn(Float, nullable=True)
    deal_currency_snapshot = SQLColumn(String(3), nullable=True)
    value_snapshot_taken = SQLColumn(Boolean, nullable=False, default=False, server_default="0")

    def __repr__(self):
        return f"<CardStageEvent card={self.card_id} {self.event_type}>"


# The value snapshot columns added to an existing card_stage_events by
# migrate_stage_event_values.py (and checked at startup by database.py).
STAGE_EVENT_VALUE_COLUMNS = ("deal_value_snapshot", "deal_currency_snapshot", "value_snapshot_taken")


class UndoEntry(CompanyBase):
    """A user's single level of undo: the inverse of their last board change.

    One row per user, replaced by each new change and removed once used.
    `steps` is a JSON list of inverse operations (see undo.py), applied in
    reverse order - several for one AI request, one for a UI action.
    """

    __tablename__ = "undo_entries"

    id = SQLColumn(Integer, primary_key=True, index=True)
    user_id = SQLColumn(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    board_id = SQLColumn(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False, index=True)
    description = SQLColumn(String, nullable=False)
    steps = SQLColumn(Text, nullable=False)  # JSON list
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False)


class User(Base):
    """LEGACY identity + authentication record (single-database runtime).

    Not company-owned: central identity is identity_models.IdentityUser. The
    commercial foreign keys target the `users` table, which CompanyUser maps
    for company use; this class only carries the fuller legacy columns.
    """

    __tablename__ = "users"

    id = SQLColumn(Integer, primary_key=True, index=True)
    username = SQLColumn(String, unique=True, index=True, nullable=False)
    password_hash = SQLColumn(String, nullable=False)
    role = SQLColumn(String, nullable=False, default=UserRole.MEMBER.value)
    email = SQLColumn(String, unique=True, index=True, nullable=True)
    full_name = SQLColumn(String, nullable=True)
    # Bumped on logout and on every password change; access tokens carry the
    # value they were issued under (`sv`), so a bump ends all of the user's sessions.
    session_version = SQLColumn(Integer, nullable=False, default=0, server_default="0")
    # Set when the user proves control of their email (consuming a signup or
    # reset link). No application access while it is NULL.
    email_verified_at = SQLColumn(DateTime, nullable=True)
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False, index=True)
    updated_at = SQLColumn(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<User {self.username}>"


class AuthToken(Base):
    """A single-use emailed token (signup verification or password reset).

    Only the SHA-256 of the random token is stored, never the token itself.
    A signup token has no user yet and is bound to `email`; a reset token is
    bound to `user_id`. Consumption is one conditional UPDATE (token_store.py).
    """

    __tablename__ = "auth_tokens"

    id = SQLColumn(Integer, primary_key=True, index=True)
    token_hash = SQLColumn(String(64), nullable=False, unique=True)
    purpose = SQLColumn(String(20), nullable=False)  # "signup" | "reset"
    user_id = SQLColumn(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    email = SQLColumn(String, nullable=True, index=True)
    created_at = SQLColumn(DateTime, nullable=False)
    expires_at = SQLColumn(DateTime, nullable=False)
    consumed_at = SQLColumn(DateTime, nullable=True)
    invalidated_at = SQLColumn(DateTime, nullable=True)


class AuditEvent(CompanyBase):
    """Audit trail of opportunity assignment changes and deletions.

    No foreign key to cards or users: the row must outlive a deleted card and
    a removed actor. `details` is JSON (never card content beyond the title).
    """

    __tablename__ = "audit_events"

    id = SQLColumn(Integer, primary_key=True, index=True)
    board_id = SQLColumn(Integer, nullable=False, index=True)
    card_id = SQLColumn(Integer, nullable=True, index=True)
    actor_user_id = SQLColumn(Integer, nullable=True, index=True)
    action = SQLColumn(String(20), nullable=False)  # "assign" | "delete"
    details = SQLColumn(Text, nullable=False)
    created_at = SQLColumn(DateTime, nullable=False, index=True)


# Alias para compatibilidade com código legado
Column = BoardColumn

# Legacy runtime schema: the company tables joined to the legacy `users` table
# in Base.metadata. CompanyUser is skipped on purpose: the foreign keys of the
# copies resolve to the legacy User table instead. Pure metadata; touches no database.
for _table in CompanyBase.metadata.sorted_tables:
    if _table.name != "users":
        _table.to_metadata(Base.metadata)
del _table
