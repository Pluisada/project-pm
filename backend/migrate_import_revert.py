"""Add the import revert tables to an existing database - explicitly, never at
startup (Part 23).

Creates import_batch_ledgers and import_batch_items, empty. Batches imported
before this migration get no ledger and stay not revertible. It reads no Excel
file, doesn't touch the Part 22 tables, and creates, changes or deletes no
user, board, card or history row.

The database must be named explicitly - there's no DATABASE_URL or ./pm.db
fallback - and the default is a dry run on a read-only connection:

    uv run python migrate_import_revert.py --database pm.db           # dry run
    uv run python migrate_import_revert.py --database pm.db --apply   # back up, then migrate

Stop the app before --apply. Refused (exit 1, nothing written, no backup):
- a database the earlier migrations haven't reached, the Bid import tables
  (migrate_import_tables.py) included;
- revert tables that are only partly there, or don't match the models
  (import_schema.py: columns, types, defaults, indexes, foreign keys);
- a second --apply. A dry run on a migrated database reports nothing to do.

--apply writes <name>-pre-import-revert-<time>.db.bak next to the database,
then, in one transaction, re-checks that the tables are still absent, creates
them, verifies the result with the same check init_db() applies, and verifies
every row of every existing table is unchanged - rolling back otherwise.
"""
import argparse
import sqlite3
import sys

from import_schema import ABSENT, COMPLETE, REVERT_TABLE_NAMES, import_schema_state, revert_ddl, revert_schema_state
from migrate_import_tables import _query, _snapshot, _tables, check_prerequisites
from migrate_stage_kind import MigrationError, backup_database, resolve_database_path


def revert_state(conn) -> str:
    """ABSENT or COMPLETE; raises for a partial or incompatible schema."""
    state, errors = revert_schema_state(_query(conn))
    if errors:
        raise MigrationError(f"Import revert tables are {state}; restore a backup:\n  " + "\n  ".join(errors))
    return state


def plan(conn) -> dict:
    check_prerequisites(conn)
    state, errors = import_schema_state(_query(conn))
    if state != COMPLETE:
        raise MigrationError(
            "The Bid import tables are missing or don't match the models; run migrate_import_tables.py --apply first"
            + ("\n  " + "\n  ".join(errors) if errors else "")
        )
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("users", "boards", "cards", "import_batches")
    }
    return {"state": revert_state(conn), "counts": counts}


def apply(path: str) -> tuple[dict, str]:
    """Back up, then migrate in one transaction. Returns (plan, backup_path)."""
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        result = plan(conn)
        if result["state"] == COMPLETE:
            raise MigrationError("The import revert tables already exist; nothing to do")

        backup_path = backup_database(conn, path, "import-revert")

        conn.execute("BEGIN IMMEDIATE")
        try:
            if revert_state(conn) != ABSENT:
                raise MigrationError("The database changed after the check; rolled back")
            existing = _tables(conn)
            before = _snapshot(conn, existing)
            for statement in revert_ddl():
                conn.execute(statement)
            state, errors = revert_schema_state(_query(conn))
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
    print(f"Tables to create, empty: {', '.join(REVERT_TABLE_NAMES)}")
    print("Batches imported before this migration have no ledger and stay not revertible.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Add the import revert tables.")
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
            print(f"The import revert tables already exist in {path}; nothing to do.")
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
