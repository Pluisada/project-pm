"""Add card_bid_details.cpm to an existing database - explicitly, never at
startup.

The Salcomp report's CPM column is kept verbatim as source data in a nullable
text column next to project_lead. The migration adds that one column, NULL for
every existing row; it reads no Excel file and creates, changes or deletes no
user, board, card, history or import row, and no other table or index.

The database must be named explicitly, as an absolute path - there's no DATABASE_URL or ./pm.db
fallback - and the default is a dry run on a read-only connection:

    uv run python migrate_bid_cpm.py --database /abs/path/pm-commercial.db                       # dry run
    uv run python migrate_bid_cpm.py --database /abs/path/pm-commercial.db --expect-sha256 <hash> --apply

--apply requires --expect-sha256 (the approved file's hash). Whatever the mode, db_target_guard
refuses backend/pm.db and backend/pm.db.bak (by path, inode or name), symlinks, a missing file
(it is never created), a -wal/-journal sidecar and a hash mismatch.

Stop the app before --apply. Refused (exit 1, nothing written, no backup):
- a database the earlier migrations haven't reached, the Bid import tables
  (migrate_import_tables.py) included;
- Bid import tables that differ from the models in any way other than the
  missing cpm column (import_schema.py);
- a second --apply. A dry run on a migrated database reports nothing to do.

--apply writes <name>-pre-bid-cpm-<time>.db.bak next to the database, then, in
one transaction with foreign keys enforced, re-checks that cpm is still absent,
adds it, verifies the result with the same check init_db() applies, and
verifies that every row of every existing table, and every other schema
object, is unchanged and that foreign_key_check is clean - rolling back
otherwise.
"""
import argparse
import sqlite3
import sys

from db_target_guard import open_readonly, open_readwrite, validate_target
from import_schema import COMPLETE, CPM_MISSING, import_schema_state
from migrate_import_tables import _query, _tables, check_prerequisites
from migrate_stage_kind import MigrationError, backup_database

ADD_COLUMN = "ALTER TABLE card_bid_details ADD COLUMN cpm VARCHAR"


def cpm_state(conn) -> str:
    """"absent" or COMPLETE; raises for any other schema difference."""
    state, errors = import_schema_state(_query(conn))
    if state == COMPLETE:
        return COMPLETE
    if errors == CPM_MISSING:
        return "absent"
    raise MigrationError(
        "The Bid import tables are missing or don't match the models in more than cpm; "
        "run migrate_import_tables.py --apply or restore a backup:\n  " + "\n  ".join(errors or [state])
    )


def plan(conn) -> dict:
    check_prerequisites(conn)
    state = cpm_state(conn)  # before counting: card_bid_details may not exist
    counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("users", "boards", "cards", "card_bid_details")}
    return {"state": state, "counts": counts}


def _snapshot(conn) -> dict:
    """Every row of every table by its pre-migration columns, plus every schema object but card_bid_details."""
    data = {}
    for table in _tables(conn):
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})") if not (table == "card_bid_details" and row[1] == "cpm")]
        data[table] = conn.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY rowid").fetchall()
    objects = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE tbl_name != 'card_bid_details' ORDER BY type, name"
    ).fetchall()
    return {"data": data, "objects": objects}


def apply(path: str) -> tuple[dict, str]:
    """Back up, then migrate in one transaction. Returns (plan, backup_path)."""
    conn = open_readwrite(path, isolation_level=None)  # mode=rw: never creates a file
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        result = plan(conn)
        if result["state"] == COMPLETE:
            raise MigrationError("card_bid_details.cpm already exists; nothing to do")
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise MigrationError(f"The database already has {len(violations)} foreign key violation(s); not migrating")

        backup_path = backup_database(conn, path, "bid-cpm")

        conn.execute("BEGIN IMMEDIATE")
        try:
            if cpm_state(conn) == COMPLETE:
                raise MigrationError("The database changed after the check; rolled back")
            before = _snapshot(conn)
            conn.execute(ADD_COLUMN)
            state, errors = import_schema_state(_query(conn))
            if state != COMPLETE:
                raise MigrationError("Migrated schema doesn't match the models; rolled back:\n  " + "\n  ".join(errors))
            if _snapshot(conn) != before:
                raise MigrationError("Existing data or schema changed during the migration; rolled back")
            if conn.execute("SELECT COUNT(*) FROM card_bid_details WHERE cpm IS NOT NULL").fetchone()[0]:
                raise MigrationError("The new cpm column isn't empty; rolled back")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise MigrationError("Foreign key violations after the migration; rolled back")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return result, backup_path
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Add card_bid_details.cpm.")
    parser.add_argument("--database", required=True, help="SQLite file to migrate (required; no default)")
    parser.add_argument("--expect-sha256", help="the approved database file's SHA-256 (required with --apply)")
    parser.add_argument("--apply", action="store_true", help="write the change (after a backup); default is a dry run")
    args = parser.parse_args(argv)

    try:
        if args.apply and not args.expect_sha256:
            raise MigrationError("--apply requires --expect-sha256 (the hash of the approved database file)")
        path = validate_target(args.database, expected_sha256=args.expect_sha256)

        if args.apply:
            print(f"Migrating {path}")
            result, backup_path = apply(path)
            counts = ", ".join(f"{count} {table}" for table, count in result["counts"].items())
            print(f"Existing data (unchanged): {counts}")
            print(f"Applied: card_bid_details.cpm added, NULL for every row. Backup of the previous state: {backup_path}")
            return 0

        conn = open_readonly(path)
        try:
            result = plan(conn)
        finally:
            conn.close()
        if result["state"] == COMPLETE:
            print(f"card_bid_details.cpm already exists in {path}; nothing to do.")
            return 0
        counts = ", ".join(f"{count} {table}" for table, count in result["counts"].items())
        print(f"Dry run on {path} - nothing was changed.")
        print(f"Existing data (unchanged): {counts}")
        print("Column to add: card_bid_details.cpm VARCHAR NULL")
        print("Re-run with --apply to back up the database and add the column.")
        return 0
    except MigrationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
