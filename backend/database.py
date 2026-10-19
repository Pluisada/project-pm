"""Database configuration and session management."""
import os
import sqlite3
import tempfile
from datetime import date
from urllib.request import pathname2url

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from import_schema import ABSENT, CPM_MISSING, PARTIAL, import_schema_state, revert_schema_state
from models import STAGE_EVENT_VALUE_COLUMNS, Base

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

def enable_sqlite_foreign_keys(engine):
    """Turn on FK enforcement for every SQLite connection `engine` opens.

    SQLite ignores declared foreign keys unless PRAGMA foreign_keys=ON is set
    per connection, so it runs on each new DBAPI connection (pooled ones
    included), before any transaction starts. Returns the engine.
    """

    @event.listens_for(engine, "connect")
    def _foreign_keys_on(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine


# Create engine with appropriate settings for SQLite vs PostgreSQL
if "sqlite" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    enable_sqlite_foreign_keys(engine)
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


class ExistingDatabaseRequired(RuntimeError):
    """PM_REQUIRE_EXISTING_DB=1 and the configured database is missing or unusable."""


def _read_schema(path: str) -> dict[str, set[str]]:
    """Table name -> column names, read through a read-only connection."""
    with sqlite3.connect(f"file:{pathname2url(path)}?mode=ro", uri=True) as conn:
        actual = {}
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            actual[table] = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    return actual


def _schema_problems(actual: dict[str, set[str]]) -> list[str]:
    """Tables and columns the models expect that `actual` lacks."""
    problems = []
    for table in Base.metadata.sorted_tables:
        if table.name not in actual:
            problems.append(f"missing table {table.name}")
        else:
            missing = sorted(c.name for c in table.columns if c.name not in actual[table.name])
            if missing:
                problems.append(f"table {table.name} lacks columns {', '.join(missing)}")
    return problems


def check_existing_database(url) -> str:
    """Return the path of a ready commercial database, or refuse with the cause.

    Used when PM_REQUIRE_EXISTING_DB=1 (docker-compose sets it whenever
    PM_DB_HOST_PATH is). The database must already exist and carry the whole
    schema: nothing is created, migrated or repaired here, and it is opened
    read-only so a wrong path can never make SQLite create an empty file.
    """
    hint = " See docs/DEPLOYMENT.md, section 'Startup errors'."
    if not url or not url.startswith("sqlite:///") or url in ("sqlite:///", "sqlite:///:memory:"):
        raise ExistingDatabaseRequired(
            f"PM_REQUIRE_EXISTING_DB=1 needs DATABASE_URL to be a SQLite file URL (got {url!r}); "
            "inside Docker that is PM_DATABASE_URL, e.g. sqlite:////data/pm-commercial.db "
            "(four slashes for an absolute path)." + hint
        )
    path = os.path.abspath(url[len("sqlite:///"):].split("?", 1)[0])
    if os.path.isdir(path):
        raise ExistingDatabaseRequired(f"{path} is a directory, not a database file. Point the URL at the file inside it." + hint)
    if not os.path.isfile(path):
        folder = os.path.dirname(path)
        where = "the file is missing from its directory" if os.path.isdir(folder) else f"the directory {folder} does not exist"
        raise ExistingDatabaseRequired(
            f"Commercial database {path} not found ({where}). Check PM_DATABASE_URL and that "
            "PM_DB_HOST_PATH / PM_DB_CONTAINER_PATH mount the directory that holds it; "
            "create it once with create_commercial_db.py." + hint
        )
    try:
        actual = _read_schema(path)
    except sqlite3.DatabaseError as exc:
        raise ExistingDatabaseRequired(f"{path} is not a readable SQLite database ({exc})." + hint) from exc
    if not actual:
        raise ExistingDatabaseRequired(
            f"{path} exists but has no tables (an empty file, e.g. from `touch` or an earlier "
            "wrong start). Delete it and run create_commercial_db.py, or restore a backup." + hint
        )
    problems = _schema_problems(actual)
    if problems:
        raise ExistingDatabaseRequired(
            f"{path} has an incomplete schema:\n  " + "\n  ".join(problems)
            + "\nRun the migrations in docs/DATABASE.md on a backup, or restore one; the app does not repair it." + hint
        )
    return path


class DatabaseCreationNotAllowed(RuntimeError):
    """The database file is missing or holds no tables, and creating it wasn't authorised."""


class DatabaseSchemaIncomplete(RuntimeError):
    """An existing database lacks tables or columns the models expect; no migration guard names it."""


def _sqlite_file_path(url) -> str | None:
    """The file behind a sqlite file URL, or None for in-memory and non-SQLite URLs."""
    if not url or not url.startswith("sqlite:///") or url == "sqlite:///" or url.startswith("sqlite:///:memory:"):
        return None
    return os.path.abspath(url[len("sqlite:///"):].split("?", 1)[0])


def _engine_url() -> str:
    """The URL the engine really uses, so the file inspected is the file the guards open."""
    url = engine.url
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        return f"sqlite:///{url.database}"
    return url.render_as_string(hide_password=False)


def _creation_authorised() -> bool:
    return E2E_MODE or os.getenv("PM_ALLOW_CREATE_DB") == "1"


def _inspect_database_file(path: str) -> bool:
    """Read-only look at a SQLite file; True if it has tables (an existing database).

    A missing file, a zero-byte file and a SQLite file without tables are all
    "nothing there yet": creating the schema is allowed only when authorised
    (PM_ALLOW_CREATE_DB=1, or the isolated PM_E2E=1). Nothing is opened with
    the engine before this returns, because connecting would create the file.
    """
    hint = " Create a commercial database with create_commercial_db.py, or set PM_ALLOW_CREATE_DB=1 to create it on purpose."
    if os.path.isdir(path):
        raise DatabaseCreationNotAllowed(f"{path} is a directory, not a database file.")
    if not os.path.exists(path):
        state = "does not exist"
    elif os.path.getsize(path) == 0:
        state = "is an empty (zero-byte) file"
    else:
        try:
            if _read_schema(path):
                return True
        except sqlite3.DatabaseError as exc:
            raise DatabaseCreationNotAllowed(f"{path} is not a readable SQLite database ({exc}).") from exc
        state = "has no tables"
    if not _creation_authorised():
        raise DatabaseCreationNotAllowed(f"Database {path} {state}; refusing to create it.{hint}")
    return False


def init_db():
    """Initialize the database: validate everything read-only, then create_all.

    Every refusal happens before the first write. An existing database is
    never altered here: missing tables or columns are refused, to be fixed
    by an explicit migration. Only a database that does not exist yet (and
    creation is authorised) is built by create_all.
    """
    if E2E_MODE:
        check_e2e_database(_engine_url())
    if os.getenv("PM_REQUIRE_EXISTING_DB") == "1":
        # Always the strictest: it needs a complete database, whatever else is set.
        check_existing_database(_engine_url())
        existing = True
    else:
        path = _sqlite_file_path(_engine_url())
        existing = _inspect_database_file(path) if path else None
    if existing is not False:
        # In-memory and non-SQLite databases (existing is None) are checked
        # through the engine like any other; an empty one passes every guard.
        require_auth_security_schema(engine)
        require_stage_events_table(engine)
        require_import_tables(engine)
        require_import_revert_tables(engine)
        require_stage_kind_column(engine)
        require_stage_event_value_columns(engine)
        require_deal_year_columns(engine)
        require_complete_schema(engine)
    Base.metadata.create_all(bind=engine)


def require_complete_schema(bind) -> None:
    """Refuse an existing database that lacks tables or columns the models expect
    (for example the lead columns of an older `cards` table). Never repaired here."""
    inspector = inspect(bind)
    tables = inspector.get_table_names()
    if not tables:
        return
    actual = {name: {col["name"] for col in inspector.get_columns(name)} for name in tables}
    problems = _schema_problems(actual)
    if problems:
        raise DatabaseSchemaIncomplete(
            "This database has an incomplete schema:\n  " + "\n  ".join(problems)
            + "\nBack it up and migrate it explicitly (docs/DATABASE.md); the app does not repair it."
        )


class ImportTablesMigrationRequired(RuntimeError):
    """An existing database has none of the Salcomp Bid import tables yet."""


class BidCpmMigrationRequired(RuntimeError):
    """The Bid import tables are complete except for card_bid_details.cpm."""


class ImportTablesSchemaError(RuntimeError):
    """The Bid import tables are only partly there, or don't match the models."""


def require_import_tables(bind) -> None:
    """Refuse an existing database whose Bid import tables aren't exactly the
    models' (import_schema.py - the same check the migration applies).

    - empty database: nothing to check, create_all builds everything;
    - none of the import tables: run migrate_import_tables.py;
    - only some, or all three with other columns/indexes/foreign keys:
      refused, never repaired - restore the backup the migration took.
    """
    if not inspect(bind).get_table_names():
        return
    with bind.connect() as conn:
        state, errors = import_schema_state(lambda sql: conn.exec_driver_sql(sql).fetchall())
    if state == ABSENT:
        raise ImportTablesMigrationRequired(
            "This database has no Salcomp Bid import tables yet. Back it up, then run "
            "`uv run python migrate_import_tables.py --database <path>` (dry run) and "
            "add `--apply` from backend/ - see docs/DATABASE.md."
        )
    if errors == CPM_MISSING:
        raise BidCpmMigrationRequired(
            "card_bid_details has no cpm column yet. Back it up, then run "
            "`uv run python migrate_bid_cpm.py --database <path>` (dry run) and "
            "add `--apply` from backend/ - see docs/DATABASE.md."
        )
    if errors:
        problem = "is partly migrated" if state == PARTIAL else "has Bid import tables that don't match the models"
        raise ImportTablesSchemaError(
            f"This database {problem}:\n  " + "\n  ".join(errors)
            + "\nRestore the backup taken before migrate_import_tables.py - see docs/DATABASE.md."
        )


class AuthSecurityMigrationRequired(RuntimeError):
    """An existing database predates session versions, email verification and persistent tokens."""


AUTH_SECURITY_USER_COLUMNS = ("session_version", "email_verified_at")
AUTH_SECURITY_TABLES = ("auth_tokens", "audit_events")


def require_auth_security_schema(bind) -> None:
    """Refuse an existing database without users.session_version / email_verified_at
    or the auth_tokens / audit_events tables; migrate_auth_security.py adds them
    explicitly. An empty database is built by create_all."""
    inspector = inspect(bind)
    tables = inspector.get_table_names()
    if not tables:
        return
    missing = [t for t in AUTH_SECURITY_TABLES if t not in tables]
    if "users" in tables:
        user_columns = {c["name"] for c in inspector.get_columns("users")}
        missing += [f"users.{c}" for c in AUTH_SECURITY_USER_COLUMNS if c not in user_columns]
    if missing:
        raise AuthSecurityMigrationRequired(
            f"This database predates the session/token security changes (missing {', '.join(missing)}). "
            "Back it up, then run `uv run python migrate_auth_security.py --database <path>` (dry run) "
            "and add `--apply` from backend/ - see docs/DATABASE.md."
        )


class ImportRevertMigrationRequired(RuntimeError):
    """An existing database has the Bid import tables but not the Part 23 ones."""


class ImportRevertSchemaError(RuntimeError):
    """The import revert tables are only partly there, or don't match the models."""


def require_import_revert_tables(bind) -> None:
    """Refuse an existing database whose import revert tables (Part 23) aren't
    exactly the models'. Runs after require_import_tables, so the Part 22
    tables are known to be fine. Same policy: an empty database is built by
    create_all, a missing group needs migrate_import_revert.py, anything
    partial or different is refused, never repaired."""
    if not inspect(bind).get_table_names():
        return
    with bind.connect() as conn:
        state, errors = revert_schema_state(lambda sql: conn.exec_driver_sql(sql).fetchall())
    if state == ABSENT:
        raise ImportRevertMigrationRequired(
            "This database has no Bid import revert tables yet. Back it up, then run "
            "`uv run python migrate_import_revert.py --database <path>` (dry run) and "
            "add `--apply` from backend/ - see docs/DATABASE.md."
        )
    if errors:
        problem = "is partly migrated" if state == PARTIAL else "has import revert tables that don't match the models"
        raise ImportRevertSchemaError(
            f"This database {problem}:\n  " + "\n  ".join(errors)
            + "\nRestore the backup taken before migrate_import_revert.py - see docs/DATABASE.md."
        )


class StageEventValuesMigrationRequired(RuntimeError):
    """card_stage_events predates the value snapshot columns; the app refuses to start."""


def require_stage_event_value_columns(bind) -> None:
    """Refuse a card_stage_events table without the value snapshot columns.

    create_all never adds columns to an existing table, and the stage-history
    listener writes them on every event - so an unmigrated table would make
    every card change fail. Migrate explicitly with migrate_stage_event_values.py.
    """
    if "card_stage_events" not in inspect(bind).get_table_names():
        return  # absent table: reported by require_complete_schema
    existing = {col["name"] for col in inspect(bind).get_columns("card_stage_events")}
    if not set(STAGE_EVENT_VALUE_COLUMNS) <= existing:
        raise StageEventValuesMigrationRequired(
            "card_stage_events has no value snapshot columns yet. Back the database up, then run "
            "`uv run python migrate_stage_event_values.py --database <path>` (dry run) and add "
            "`--apply` from backend/ - see docs/DATABASE.md."
        )


class DealYearsMigrationRequired(RuntimeError):
    """cards predates the annual Deal Value columns; the app refuses to start."""


DEAL_YEAR_COLUMNS = ("deal_value_next_year", "deal_year")


def require_deal_year_columns(bind) -> None:
    """Refuse a cards table without deal_value_next_year / deal_year.

    deal_year needs the year the existing amounts belong to, which only an
    operator can state, so migrate_deal_years.py takes it as --year.
    """
    if "cards" not in inspect(bind).get_table_names():
        return  # absent table: reported by require_complete_schema
    existing = {col["name"] for col in inspect(bind).get_columns("cards")}
    if not set(DEAL_YEAR_COLUMNS) <= existing:
        raise DealYearsMigrationRequired(
            "cards has no annual Deal Value columns yet. Back the database up, then run "
            "`uv run python migrate_deal_years.py --database <path> --year <current year>` (dry run) "
            "and add `--expect-sha256 <hash> --apply` from backend/ - see docs/DATABASE.md."
        )


class StageEventsMigrationRequired(RuntimeError):
    """An existing database predates card_stage_events; the app refuses to start."""


def require_stage_events_table(bind) -> None:
    """Refuse an existing database that has no card_stage_events table yet.

    A brand-new database (no cards table) gets every table from create_all.
    An existing one must be migrated explicitly with migrate_stage_events.py,
    which also records each card's current stage as a "baseline" event -
    letting create_all add an empty table would leave existing cards with no
    starting point in their history.
    """
    tables = set(inspect(bind).get_table_names())
    if "cards" in tables and "card_stage_events" not in tables:
        raise StageEventsMigrationRequired(
            "This database has no card_stage_events table yet. Back it up, then run "
            "`uv run python migrate_stage_events.py --database <path>` (dry run) and "
            "add `--apply` from backend/ - see docs/DATABASE.md."
        )


class StageKindMigrationRequired(RuntimeError):
    """The database predates columns.stage_kind; the app refuses to start."""


def require_stage_kind_column(bind) -> None:
    """Refuse to run against a database without columns.stage_kind.

    Unlike the nullable lead columns, stage_kind decides which opportunities
    count as won or lost, so it's never added automatically: an operator runs
    migrate_stage_kind.py, reviews its classification, and applies it.
    """
    if "columns" not in inspect(bind).get_table_names():
        return  # absent table: reported by require_complete_schema
    existing = {col["name"] for col in inspect(bind).get_columns("columns")}
    if "stage_kind" not in existing:
        raise StageKindMigrationRequired(
            "This database has no columns.stage_kind yet. Back it up, then run "
            "`uv run python migrate_stage_kind.py` (dry run) and "
            "`uv run python migrate_stage_kind.py --apply` from backend/ - see docs/DATABASE.md."
        )


def _add_missing_card_columns(bind) -> None:
    """Add Card columns that an older database's `cards` table lacks.

    Not called at startup any more: init_db refuses a database with missing
    columns (require_complete_schema). Kept only as the ALTER TABLE
    building block for an explicit migration, which does not exist yet.
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
     dict(company="Nordic Mobile Oy", contact="Aino Virtanen", deal_value=195000, currency="USD",
          expected_close_date=date(2027, 3, 31), lead_source="Trade show", product_line="Chargers")),
    ("Laptop adapter refresh", "Replace legacy 90W barrel adapters with USB-C across the range.", 0,
     dict(company="Lumen Computing", contact="Rahul Mehta", deal_value=420000, currency="USD",
          expected_close_date=date(2027, 1, 15), lead_source="Website", product_line="Power supplies")),
    ("Smart speaker PSU", "Internal power supply for a new smart speaker line.", 1,
     dict(company="SoundHome Ltda", contact="Mariana Souza", deal_value=180000, currency="USD",
          expected_close_date=date(2026, 12, 20), lead_source="Referral", product_line="Power supplies")),
    ("Wireless charging pad", "Qi2 pad bundled with mid-range handsets.", 2,
     dict(company="Shenzhen Brightwave", contact="Li Wei", deal_value=360000, currency="USD",
          expected_close_date=date(2026, 11, 30), lead_source="Existing customer", product_line="Chargers")),
    ("E-bike charger", "36V charger for an e-bike range launching in Europe.", 3,
     dict(company="VeloVolt GmbH", contact="Jonas Weber", deal_value=260000, currency="USD",
          expected_close_date=date(2026, 10, 31), lead_source="Cold outreach", product_line="Chargers")),
    ("Tablet fast charger", "30W charger for a budget tablet line.", 3,
     dict(company="Indus Devices", contact="Priya Nair", deal_value=370000, currency="USD",
          expected_close_date=date(2026, 11, 15), lead_source="Website", product_line="Chargers")),
    ("Router power adapters", "12V adapters for home routers, three-year frame agreement.", 4,
     dict(company="Mekong Networks", contact="Nguyen Thi Lan", deal_value=150000, currency="USD",
          expected_close_date=date(2026, 9, 30), lead_source="Existing customer", product_line="Power supplies")),
    ("Wearable charging dock", "Lost on price to a local supplier.", 5,
     dict(company="FitTrack Inc", contact="Sarah Cole", deal_value=90000, currency="USD",
          expected_close_date=date(2026, 8, 31), lead_source="Trade show", product_line="Chargers")),
]


def seed_stages(db: Session, board) -> list:
    """Create the pipeline's stage columns on `board` (no cards) and return them."""
    from lead_options import PIPELINE_STAGES
    from models import Column

    columns = [
        Column(board_id=board.id, title=title, position=i, stage_kind=kind)
        for i, (title, kind) in enumerate(PIPELINE_STAGES)
    ]
    db.add_all(columns)
    db.flush()
    return columns


def seed_pipeline(db: Session, board, salesperson) -> None:
    """Create the sales stage columns on `board` and fill them with sample leads."""
    from models import Card

    columns = seed_stages(db, board)

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


# Registers the card stage-history flush listener (stage_history.py) for every
# session, so no code path can change a card's stage without recording it.
import stage_history  # noqa: E402,F401
