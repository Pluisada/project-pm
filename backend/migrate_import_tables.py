"""Add the Salcomp Bid import tables to an existing database - explicitly,
never at startup (Part 22).

Creates import_batches, card_bid_details, card_source_refs and
card_import_changes, empty. It reads no Excel file and creates, changes or
deletes no user, board, card or history row (card_edit_history included).

The database must be named explicitly - there's no DATABASE_URL or ./pm.db
fallback - and the default is a dry run on a read-only connection:

    uv run python migrate_import_tables.py --database pm.db           # dry run
    uv run python migrate_import_tables.py --database pm.db --apply   # back up, then migrate

Stop the app before --apply. Refused (exit 1, nothing written, no backup):
- a database the earlier migrations haven't reached: columns.stage_kind
  (migrate_stage_kind.py), card_stage_events (migrate_stage_events.py) and its
  value snapshot columns (migrate_stage_event_values.py) must all be there;
- import tables that are only partly there, or don't match the models
  (import_schema.py: columns, types, defaults, indexes, foreign keys);
- a second --apply. A dry run on a migrated database reports nothing to do.

--apply writes <name>-pre-import-tables-<time>.db.bak next to the database,
then, in one transaction, re-checks that the tables are still absent, creates
them, verifies the result with the same check init_db() applies, and verifies
every row of every existing table is unchanged - rolling back otherwise.
"""
import argparse
import sqlite3
import sys

from import_schema import ABSENT, COMPLETE, CPM_MISSING, IMPORT_TABLE_NAMES, ddl, import_schema_state
from migrate_stage_kind import MigrationError, backup_database, has_stage_kind, resolve_database_path
from models import STAGE_EVENT_VALUE_COLUMNS

BASE_TABLES = ("users", "boards", "columns", "cards")


def _query(conn):
    return lambda sql: conn.execute(sql).fetchall()


def _tables(conn) -> list[str]:
    return [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]


def check_prerequisites(conn) -> None:
    tables = set(_tables(conn))
    missing = [table for table in BASE_TABLES if table not in tables]
    if missing:
        raise MigrationError(f"Not a Sales Pipeline database (missing {', '.join(missing)})")
    if not has_stage_kind(conn):
        raise MigrationError("columns.stage_kind is missing; run migrate_stage_kind.py --apply first")
    if "card_stage_events" not in tables:
        raise MigrationError("card_stage_events is missing; run migrate_stage_events.py --apply first")
    event_columns = {row[1] for row in conn.execute("PRAGMA table_info(card_stage_events)")}
    if not set(STAGE_EVENT_VALUE_COLUMNS) <= event_columns:
        raise MigrationError(
            "card_stage_events has no value snapshot columns; run migrate_stage_event_values.py --apply first"
        )


def import_state(conn) -> str:
    """ABSENT or COMPLETE; raises for a partial or incompatible schema."""
    state, errors = import_schema_state(_query(conn))
    if errors == CPM_MISSING:
        raise MigrationError("card_bid_details.cpm is missing; run migrate_bid_cpm.py --apply")
    if errors:
        raise MigrationError(f"Bid import tables are {state}; restore a backup:\n  " + "\n  ".join(errors))
    return state


def plan(conn) -> dict:
    check_prerequisites(conn)
    counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("users", "boards", "cards")}
    return {"state": import_state(conn), "counts": counts}


def _snapshot(conn, tables) -> dict:
    return {table: conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables}


def apply(path: str) -> tuple[dict, str]:
    """Back up, then migrate in one transaction. Returns (plan, backup_path)."""
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        result = plan(conn)
        if result["state"] == COMPLETE:
            raise MigrationError("The Bid import tables already exist; nothing to do")

        backup_path = backup_database(conn, path, "import-tables")

        conn.execute("BEGIN IMMEDIATE")
        try:
            if import_state(conn) != ABSENT:
                raise MigrationError("The database changed after the check; rolled back")
            existing = _tables(conn)
            before = _snapshot(conn, existing)
            for statement in ddl():
                conn.execute(statement)
            state, errors = import_schema_state(_query(conn))
            if state != COMPLETE:
                raise MigrationError("Created schema doesn't match the models; rolled back:\n  " + "\n  ".join(errors))
            if _snapshot(conn, existing) != before:
                raise MigrationError("Existing data changed during the migration; rolled back")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return result, backup_path
    finally:
        conn.close()


def _print_plan(result: dict) -> None:
    counts = ", ".join(f"{count} {table}" for table, count in result["counts"].items())
    print(f"Existing data (unchanged): {counts}")
    print(f"Tables to create, empty: {', '.join(IMPORT_TABLE_NAMES)}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Add the Salcomp Bid import tables.")
    parser.add_argument("--database", required=True, help="SQLite file to migrate (required; no default)")
    parser.add_argument("--apply", action="store_true", help="write the change (after a backup); default is a dry run")
    args = parser.parse_args(argv)

    try:
        path = resolve_database_path(args.database)

        if args.apply:
            print(f"Migrating {path}")
            result, backup_path = apply(path)
            _print_plan(result)
            print(f"Applied. Backup of the previous state: {backup_path}")
            return 0

        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = plan(conn)
        finally:
            conn.close()
        if result["state"] == COMPLETE:
            print(f"The Bid import tables already exist in {path}; nothing to do.")
            return 0
        print(f"Dry run on {path} - nothing was changed.")
        _print_plan(result)
        print("Re-run with --apply to back up the database and create the tables.")
        return 0
    except MigrationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
