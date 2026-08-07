"""Database configuration and session management."""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from models import Base

# Database URL from environment or default to SQLite
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pm.db")

# Create engine with appropriate settings for SQLite vs PostgreSQL
if "sqlite" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )
else:
    # PostgreSQL settings
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        pool_size=10,
        max_overflow=20,
    )

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """Dependency to get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database - create tables if they don't exist."""
    Base.metadata.create_all(bind=engine)


def create_sample_data():
    """Create sample data for MVP (hardcoded user and board)."""
    from models import User, Board, Column, Card

    db = SessionLocal()
    try:
        # Check if user already exists
        user = db.query(User).filter(User.username == "user").first()
        if user:
            return  # Already initialized

        # Create hardcoded user for MVP
        user = User(
            username="user",
            password_hash="password",  # Hardcoded for MVP
            full_name="Demo User",
            email="user@example.com"
        )
        db.add(user)
        db.commit()

        # Create default board
        board = Board(
            user_id=user.id,
            title="My Board",
            description="Demo Kanban board"
        )
        db.add(board)
        db.commit()

        # Create default columns
        column_titles = ["Backlog", "Discovery", "In Progress", "Review", "Done"]
        columns = []
        for i, title in enumerate(column_titles):
            col = Column(
                board_id=board.id,
                title=title,
                position=i
            )
            db.add(col)
            columns.append(col)
        db.commit()

        # Create sample cards
        sample_cards = [
            ("Align roadmap themes", "Draft quarterly themes with impact statements and metrics.", 0),
            ("Gather customer signals", "Review support tags, sales notes, and churn feedback.", 0),
            ("Prototype analytics view", "Sketch initial dashboard layout and key drill-downs.", 1),
            ("Refine status language", "Standardize column labels and tone across the board.", 2),
            ("Design card layout", "Add hierarchy and spacing for scanning dense lists.", 2),
            ("QA micro-interactions", "Verify hover, focus, and loading states.", 3),
            ("Ship marketing page", "Final copy approved and asset pack delivered.", 4),
            ("Close onboarding sprint", "Document release notes and share internally.", 4),
        ]

        for title, details, col_index in sample_cards:
            card = Card(
                column_id=columns[col_index].id,
                title=title,
                details=details,
                position=0
            )
            db.add(card)
        db.commit()

    finally:
        db.close()
