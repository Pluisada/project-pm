"""SQLAlchemy ORM models for PM database."""
from datetime import datetime
from sqlalchemy import Column as SQLColumn, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class User(Base):
    """User model."""

    __tablename__ = "users"

    id = SQLColumn(Integer, primary_key=True, index=True)
    username = SQLColumn(String, unique=True, index=True, nullable=False)
    password_hash = SQLColumn(String, nullable=False)
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
    created_at = SQLColumn(DateTime, default=func.now(), nullable=False, index=True)
    updated_at = SQLColumn(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    column = relationship("BoardColumn", back_populates="cards")
    actions = relationship("CardAction", back_populates="card", cascade="all, delete-orphan")

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


# Alias para compatibilidade com código legado
Column = BoardColumn
