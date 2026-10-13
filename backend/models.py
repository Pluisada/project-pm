"""SQLAlchemy ORM models for PM database."""
import enum
from datetime import datetime
from sqlalchemy import Boolean, Column as SQLColumn, Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class UserRole(str, enum.Enum):
    """User role. More profiles may be added in the future."""

    ADMIN = "admin"
    MEMBER = "member"


class User(Base):
    """User model."""

    __tablename__ = "users"

    id = SQLColumn(Integer, primary_key=True, index=True)
    username = SQLColumn(String, unique=True, index=True, nullable=False)
    password_hash = SQLColumn(String, nullable=False)
    role = SQLColumn(String, nullable=False, default=UserRole.MEMBER.value)
    email = SQLColumn(String, unique=True, index=True, nullable=True)
    full_name = SQLColumn(String, nullable=True)
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False, index=True)
    updated_at = SQLColumn(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    boards = relationship("Board", back_populates="user", cascade="all, delete-orphan")
    messages = relationship("ConversationMessage", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.username}>"


class Board(Base):
    """Kanban board model."""

    __tablename__ = "boards"

    id = SQLColumn(Integer, primary_key=True, index=True)
    user_id = SQLColumn(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = SQLColumn(String, nullable=False, default="My Board")
    description = SQLColumn(String, nullable=True)
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False, index=True)
    updated_at = SQLColumn(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    user = relationship("User", back_populates="boards")
    columns = relationship("BoardColumn", back_populates="board", cascade="all, delete-orphan")
    messages = relationship("ConversationMessage", back_populates="board", cascade="all, delete-orphan")
    card_actions = relationship("CardAction", back_populates="board", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Board {self.title}>"


class BoardColumn(Base):
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


class Card(Base):
    """Kanban card model."""

    __tablename__ = "cards"

    id = SQLColumn(Integer, primary_key=True, index=True)
    column_id = SQLColumn(Integer, ForeignKey("columns.id", ondelete="CASCADE"), nullable=False, index=True)
    title = SQLColumn(String, nullable=False)
    details = SQLColumn(Text, nullable=True)
    position = SQLColumn(Integer, nullable=False, default=0)
    # Sales lead fields - all optional; allowed values live in lead_options.py.
    # Existing databases get these via database._add_missing_card_columns.
    company = SQLColumn(String, nullable=True)
    contact = SQLColumn(String, nullable=True)
    deal_value = SQLColumn(Float, nullable=True)
    currency = SQLColumn(String(3), nullable=True)
    expected_close_date = SQLColumn(Date, nullable=True)
    lead_source = SQLColumn(String, nullable=True)
    product_line = SQLColumn(String, nullable=True)
    salesperson_id = SQLColumn(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False, index=True)
    updated_at = SQLColumn(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    column = relationship("BoardColumn", back_populates="cards")
    salesperson = relationship("User")
    actions = relationship("CardAction", back_populates="card", cascade="all, delete-orphan")
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

    def __repr__(self):
        return f"<Card {self.title}>"


class ConversationMessage(Base):
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
    user = relationship("User", back_populates="messages")
    actions = relationship("CardAction", back_populates="message")

    def __repr__(self):
        return f"<Message {self.role}: {self.content[:30]}>"


class CardAction(Base):
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


class CardEditHistory(Base):
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
    user = relationship("User")

    def __repr__(self):
        return f"<CardEditHistory card={self.card_id}>"


class CardStageEvent(Base):
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


class UndoEntry(Base):
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


# Alias para compatibilidade com código legado
Column = BoardColumn
