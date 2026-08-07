"""SQLAlchemy ORM models for PM database."""
from datetime import datetime
from typing import List

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class User(Base):
    """User model."""

    __tablename__ = "users"

    id: int = Column(Integer, primary_key=True, index=True)
    username: str = Column(String, unique=True, index=True, nullable=False)
    password_hash: str = Column(String, nullable=False)
    email: str = Column(String, unique=True, index=True, nullable=True)
    full_name: str = Column(String, nullable=True)
    created_at: datetime = Column(
        DateTime, default=func.now(), nullable=False, index=True
    )
    updated_at: datetime = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    boards: List["Board"] = relationship(
        "Board", back_populates="user", cascade="all, delete-orphan"
    )
    messages: List["ConversationMessage"] = relationship(
        "ConversationMessage", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User {self.username}>"


class Board(Base):
    """Kanban board model."""

    __tablename__ = "boards"

    id: int = Column(Integer, primary_key=True, index=True)
    user_id: int = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: str = Column(String, nullable=False, default="My Board")
    description: str = Column(String, nullable=True)
    created_at: datetime = Column(
        DateTime, default=func.now(), nullable=False, index=True
    )
    updated_at: datetime = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    user: User = relationship("User", back_populates="boards")
    columns: List["Column"] = relationship(
        "Column", back_populates="board", cascade="all, delete-orphan"
    )
    messages: List["ConversationMessage"] = relationship(
        "ConversationMessage", back_populates="board", cascade="all, delete-orphan"
    )
    card_actions: List["CardAction"] = relationship(
        "CardAction", back_populates="board", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Board {self.title}>"


class Column(Base):
    """Kanban column model."""

    __tablename__ = "columns"

    id: int = Column(Integer, primary_key=True, index=True)
    board_id: int = Column(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False, index=True)
    title: str = Column(String, nullable=False)
    position: int = Column(Integer, nullable=False, default=0)
    created_at: datetime = Column(
        DateTime, default=func.now(), nullable=False
    )
    updated_at: datetime = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    board: Board = relationship("Board", back_populates="columns")
    cards: List["Card"] = relationship(
        "Card", back_populates="column", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Column {self.title}>"


class Card(Base):
    """Kanban card model."""

    __tablename__ = "cards"

    id: int = Column(Integer, primary_key=True, index=True)
    column_id: int = Column(Integer, ForeignKey("columns.id", ondelete="CASCADE"), nullable=False, index=True)
    title: str = Column(String, nullable=False)
    details: str = Column(Text, nullable=True)
    position: int = Column(Integer, nullable=False, default=0)
    created_at: datetime = Column(
        DateTime, default=func.now(), nullable=False, index=True
    )
    updated_at: datetime = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    column: Column = relationship("Column", back_populates="cards")
    actions: List["CardAction"] = relationship(
        "CardAction", back_populates="card", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Card {self.title}>"


class ConversationMessage(Base):
    """Conversation message model for AI chat history."""

    __tablename__ = "conversation_messages"

    id: int = Column(Integer, primary_key=True, index=True)
    board_id: int = Column(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: int = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: str = Column(String, nullable=False)  # 'user' or 'assistant'
    content: str = Column(Text, nullable=False)
    metadata: str = Column(Text, nullable=True)  # JSON string
    created_at: datetime = Column(
        DateTime, default=func.now(), nullable=False, index=True
    )

    # Relationships
    board: Board = relationship("Board", back_populates="messages")
    user: User = relationship("User", back_populates="messages")
    actions: List["CardAction"] = relationship(
        "CardAction", back_populates="message"
    )

    def __repr__(self) -> str:
        return f"<Message {self.role}: {self.content[:30]}>"


class CardAction(Base):
    """Card action model for audit trail."""

    __tablename__ = "card_actions"

    id: int = Column(Integer, primary_key=True, index=True)
    board_id: int = Column(Integer, ForeignKey("boards.id", ondelete="CASCADE"), nullable=False, index=True)
    message_id: int = Column(Integer, ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True, index=True)
    action_type: str = Column(String, nullable=False)  # 'create', 'update', 'move', 'delete'
    card_id: int = Column(Integer, ForeignKey("cards.id", ondelete="SET NULL"), nullable=True, index=True)
    changes: str = Column(Text, nullable=True)  # JSON string
    created_at: datetime = Column(
        DateTime, default=func.now(), nullable=False, index=True
    )

    # Relationships
    board: Board = relationship("Board", back_populates="card_actions")
    message: ConversationMessage = relationship("ConversationMessage", back_populates="actions")
    card: Card = relationship("Card", back_populates="actions")

    def __repr__(self) -> str:
        return f"<CardAction {self.action_type}>"
