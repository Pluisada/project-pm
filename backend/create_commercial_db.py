"""Create the clean commercial database - a new SQLite file with the full
schema, the Salcomp board with its stages, and the first administrator.

    uv run python create_commercial_db.py --database /path/to/pm-commercial.db --email admin@example.com

The path is required (no DATABASE_URL or ./pm.db fallback) and must not exist:
an existing file or symlink is refused, never overwritten or reused. Nothing
else is created: no cards, no sample leads, no import batches. The administrator
is provisioned WITHOUT a usable password (there is no password argument, prompt
or constant): after the transition the owner of the address requests a reset
through the application, which also verifies the email. If anything fails
after the file is created, the file is removed again.

This script never opens the development database. To run the app on the result
set DATABASE_URL=sqlite:////absolute/path/to/pm-commercial.db (see
docs/DATABASE.md).
"""
import argparse
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from admin_cli import ProvisioningError, create_admin
from database import (
    enable_sqlite_foreign_keys,
    DEVELOPMENT_DATABASES,
    require_auth_security_schema,
    require_import_revert_tables,
    require_import_tables,
    require_stage_event_value_columns,
    require_stage_events_table,
    require_stage_kind_column,
    seed_stages,
)
from models import Base, Board

BOARD_TITLE = "Salcomp Sales Pipeline"
BOARD_DESCRIPTION = "Shared sales lead board"


class CommercialDatabaseError(Exception):
    """A refused operation; the message is safe to show."""


def check_target(path: str) -> str:
    """Return the absolute path for a new database, or refuse."""
    if not path or path == ":memory:" or path.startswith("sqlite:"):
        raise CommercialDatabaseError("--database must be a file path, not a URL or :memory:")
    path = os.path.abspath(path)
    if os.path.lexists(path):
        raise CommercialDatabaseError(f"{path} already exists; refusing to overwrite or reuse it")
    if os.path.realpath(path) in {os.path.realpath(p) for p in DEVELOPMENT_DATABASES}:
        raise CommercialDatabaseError("Refusing the development database path")
    if not os.path.isdir(os.path.dirname(path)):
        raise CommercialDatabaseError(f"Directory {os.path.dirname(path)} does not exist")
    return path


def create_commercial_database(path: str, email: str) -> str:
    """Build the database at `path` and return its absolute path."""
    path = check_target(path)
    open(path, "x").close()  # exclusive: loses cleanly to a concurrent creator
    engine = enable_sqlite_foreign_keys(create_engine(f"sqlite:///{path}"))
    try:
        Base.metadata.create_all(bind=engine)
        # The same startup checks the app applies: the result must be a
        # database the app accepts without any migration.
        require_auth_security_schema(engine)
        require_stage_events_table(engine)
        require_import_tables(engine)
        require_import_revert_tables(engine)
        require_stage_kind_column(engine)
        require_stage_event_value_columns(engine)

        with sessionmaker(bind=engine, autoflush=False)() as db:
            admin = create_admin(db, email, None)
            board = Board(user_id=admin.id, title=BOARD_TITLE, description=BOARD_DESCRIPTION)
            db.add(board)
            db.flush()
            seed_stages(db, board)
            db.commit()
    except BaseException:
        engine.dispose()
        os.remove(path)
        raise
    engine.dispose()
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create the clean commercial PM database.")
    parser.add_argument("--database", required=True, help="path of the new database file (must not exist)")
    parser.add_argument(
        "--email",
        required=True,
        help="email address of the first administrator (also its username); created without a usable password",
    )
    args = parser.parse_args(argv)
    try:
        check_target(args.database)
        path = create_commercial_database(args.database, args.email.strip())
    except (CommercialDatabaseError, ProvisioningError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f'Created {path} with admin "{args.email.strip()}" (no usable password) and the {BOARD_TITLE} stages; no cards.')
    print(f"Run the app on it with DATABASE_URL=sqlite:///{path}")
    print("The administrator sets a password with 'Forgot my password?' once the app can send email.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
