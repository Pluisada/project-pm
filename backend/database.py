"""Database configuration and session management."""
import os
import tempfile
from datetime import date

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from models import Base

# Database URL from environment or default to SQLite
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pm.db")

# E2E mode (PM_E2E=1, set by the Playwright config) must never touch real data.
E2E_MODE = os.getenv("PM_E2E") == "1"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVELOPMENT_DATABASES = (
    os.path.join(_REPO_ROOT, "backend", "pm.db"),
    os.path.join(_REPO_ROOT, "pm.db"),
)


class E2EDatabaseError(RuntimeError):
    """E2E mode was asked for without an isolated, throwaway database."""


def check_e2e_database(url) -> str:
    """Return the E2E database path, or refuse if it isn't safely isolated.

    Requires an explicit sqlite file URL whose real path is inside the system
    temp directory - so it can be neither development database (repo-root or
    backend/pm.db) nor anything else long-lived. There is no fallback: an
    incomplete config fails here instead of quietly using pm.db.
    """
    if not url:
        raise E2EDatabaseError("PM_E2E=1 requires an explicit DATABASE_URL")
    if not url.startswith("sqlite:///") or url in ("sqlite:///", "sqlite:///:memory:"):
        raise E2EDatabaseError("PM_E2E=1 requires DATABASE_URL to be a SQLite file URL")
    path = os.path.realpath(url[len("sqlite:///"):])
    if path in {os.path.realpath(p) for p in DEVELOPMENT_DATABASES}:
        raise E2EDatabaseError("PM_E2E=1 refuses the development database")
    temp_root = os.path.realpath(tempfile.gettempdir())
    if os.path.commonpath([path, temp_root]) != temp_root:
        raise E2EDatabaseError("PM_E2E=1 requires the database to live in the system temp directory")
    return path


if E2E_MODE:
    check_e2e_database(os.getenv("DATABASE_URL"))

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
    _add_missing_card_columns(engine)
    require_stage_kind_column(engine)


class StageKindMigrationRequired(RuntimeError):
    """The database predates columns.stage_kind; the app refuses to start."""


def require_stage_kind_column(bind) -> None:
    """Refuse to run against a database without columns.stage_kind.

    Unlike the nullable lead columns, stage_kind decides which opportunities
    count as won or lost, so it's never added automatically: an operator runs
    migrate_stage_kind.py, reviews its classification, and applies it.
    """
    existing = {col["name"] for col in inspect(bind).get_columns("columns")}
    if "stage_kind" not in existing:
        raise StageKindMigrationRequired(
            "This database has no columns.stage_kind yet. Back it up, then run "
            "`uv run python migrate_stage_kind.py` (dry run) and "
            "`uv run python migrate_stage_kind.py --apply` from backend/ - see docs/DATABASE.md."
        )


def _add_missing_card_columns(bind) -> None:
    """Add Card columns that an older database's `cards` table lacks.

    There's no migration tool here and create_all never alters an existing
    table, so the lead fields added to Card later are added with plain
    ALTER TABLE ADD COLUMN (all of them are nullable). Safe to run on every
    startup.
    """
    from models import Card

    existing = {col["name"] for col in inspect(bind).get_columns("cards")}
    missing = [col for col in Card.__table__.columns if col.name not in existing]
    if not missing:
        return
    with bind.begin() as conn:
        for col in missing:
            col_type = col.type.compile(dialect=bind.dialect)
            conn.execute(text(f"ALTER TABLE cards ADD COLUMN {col.name} {col_type}"))


# Example leads for a fresh board: (title, details, stage index, lead fields)
SAMPLE_LEADS = [
    ("65W GaN charger for flagship phone", "OEM wants a slim 65W USB-C PD charger for next year's launch.", 0,
     dict(company="Nordic Mobile Oy", contact="Aino Virtanen", deal_value=180000, currency="EUR",
          expected_close_date=date(2027, 3, 31), lead_source="Trade show", product_line="Chargers")),
    ("Laptop adapter refresh", "Replace legacy 90W barrel adapters with USB-C across the range.", 0,
     dict(company="Lumen Computing", contact="Rahul Mehta", deal_value=420000, currency="USD",
          expected_close_date=date(2027, 1, 15), lead_source="Website", product_line="Power supplies")),
    ("Smart speaker PSU", "Internal power supply for a new smart speaker line.", 1,
     dict(company="SoundHome Ltda", contact="Mariana Souza", deal_value=950000, currency="BRL",
          expected_close_date=date(2026, 12, 20), lead_source="Referral", product_line="Power supplies")),
    ("Wireless charging pad", "Qi2 pad bundled with mid-range handsets.", 2,
     dict(company="Shenzhen Brightwave", contact="Li Wei", deal_value=2600000, currency="CNY",
          expected_close_date=date(2026, 11, 30), lead_source="Existing customer", product_line="Chargers")),
    ("E-bike charger", "36V charger for an e-bike range launching in Europe.", 3,
     dict(company="VeloVolt GmbH", contact="Jonas Weber", deal_value=240000, currency="EUR",
          expected_close_date=date(2026, 10, 31), lead_source="Cold outreach", product_line="Chargers")),
    ("Tablet fast charger", "30W charger for a budget tablet line.", 3,
     dict(company="Indus Devices", contact="Priya Nair", deal_value=31000000, currency="INR",
          expected_close_date=date(2026, 11, 15), lead_source="Website", product_line="Chargers")),
    ("Router power adapters", "12V adapters for home routers, three-year frame agreement.", 4,
     dict(company="Mekong Networks", contact="Nguyen Thi Lan", deal_value=150000, currency="USD",
          expected_close_date=date(2026, 9, 30), lead_source="Existing customer", product_line="Power supplies")),
    ("Wearable charging dock", "Lost on price to a local supplier.", 5,
     dict(company="FitTrack Inc", contact="Sarah Cole", deal_value=90000, currency="USD",
          expected_close_date=date(2026, 8, 31), lead_source="Trade show", product_line="Chargers")),
]


def seed_pipeline(db: Session, board, salesperson) -> None:
    """Create the sales stage columns on `board` and fill them with sample leads."""
    from lead_options import PIPELINE_STAGES
    from models import Card, Column

    columns = [
        Column(board_id=board.id, title=title, position=i, stage_kind=kind)
        for i, (title, kind) in enumerate(PIPELINE_STAGES)
    ]
    db.add_all(columns)
    db.flush()

    for title, details, stage, fields in SAMPLE_LEADS:
        db.add(Card(
            column_id=columns[stage].id,
            title=title,
            details=details,
            position=0,
            salesperson_id=salesperson.id,
            **fields,
        ))
    db.commit()


def seed_default_board(db: Session, creator) -> None:
    """Seed the shared default board with the sales stages and sample leads.

    Called once, from the admin setup endpoint, since that's the first point
    a user (and therefore an owner for the board) is guaranteed to exist.
    Idempotent: does nothing if a board already exists.
    """
    from models import Board

    if db.query(Board).first():
        return  # Already initialized

    board = Board(
        user_id=creator.id,
        title="Salcomp Sales Pipeline",
        description="Shared sales lead board"
    )
    db.add(board)
    db.commit()

    seed_pipeline(db, board, creator)
