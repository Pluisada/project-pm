"""Bring a legacy single-database file to the probability-level schema - explicitly, never at startup.

The sales team's own `cards.confidence_level` (LOW / MEDIUM / HIGH, NULL = not assigned) replaced the Bid report's
confidence percentage. This migration, in one transaction:
- adds `cards.confidence_level VARCHAR(6)` (NULL for every card) when it is missing;
- drops the obsolete `card_bid_details.confidence` and `card_source_refs.source_confidence`; their values are
  deliberately discarded (the sales team reviews opportunities manually after rollout).
Nothing else changes: every other column of every table is verified identical. (A company database uses
migrate_confidence_level.py, which also moves its schema version to 4; the legacy database has no version marker.)

The database must be named explicitly, as an absolute path - there is no DATABASE_URL or ./pm.db fallback - and the
default is a dry run on a read-only connection:

    uv run python migrate_legacy_bid_confidence.py --database /abs/path/pm-commercial.db                      # dry run
    uv run python migrate_legacy_bid_confidence.py --database /abs/path/pm-commercial.db --expect-sha256 <hash> --apply

--apply requires --expect-sha256 (the approved file's hash). Whatever the mode, db_target_guard refuses
backend/pm.db and backend/pm.db.bak, symlinks, a missing file (never created), a -wal/-journal sidecar and a hash
mismatch. Stop the app before --apply. Refused (exit 1, nothing written, no backup): a database the earlier migrations
(including migrate_import_tables.py and migrate_bid_cpm.py) have not reached, a Bid import table that differs from the
models in any way other than the obsolete columns, and a database that needs nothing (dry run: reports so).

--apply works in one transaction with foreign keys enforced. Under the write lock (BEGIN IMMEDIATE), before any change, it
re-checks that the path still names the same file (st_dev/st_ino, no symlink, no sidecar), that the file and the lock
holder's own view of it hash to --expect-sha256, and refuses a WAL database. It then writes
<name>-pre-bid-confidence-<time>.db.bak next to the database, created exclusively (an existing file of that name stops the
run untouched), and verifies it with the check migrate_confidence_level.py uses: same schema objects, header metadata and
every column of every table (the columns about to be dropped included), integrity_check ok, and a source that still hashes
to the approved value. A backup that fails is renamed *.UNVERIFIED.db.bak (never over another file) and nothing is migrated.
Only then does it change the schema, verify it with the check init_db() applies (import_schema.py), that every surviving
value is unchanged and foreign_key_check is clean - rolling back otherwise and keeping the verified backup.
"""
import argparse
import os
import sqlite3
import sys

from db_target_guard import open_readonly, open_readwrite, validate_target
from import_schema import COMPLETE, import_schema_state
from migrate_confidence_level import (
    _backup_approved_state, _check_unchanged, _hash_of, _identity, _refuse_wal, _serialized_sha256, content_fingerprint,
)
from migrate_import_tables import _query, _tables, check_prerequisites
from migrate_stage_kind import MigrationError

LEVEL = ("cards", "confidence_level")
ADD_LEVEL = "ALTER TABLE cards ADD COLUMN confidence_level VARCHAR(6)"
OBSOLETE = (("card_bid_details", "confidence"), ("card_source_refs", "source_confidence"))
CHANGED = {LEVEL, *OBSOLETE}


def _has(conn, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def plan(conn) -> dict:
    """What is to do: whether the level column is missing and which obsolete columns exist."""
    check_prerequisites(conn)
    tables = set(_tables(conn))
    if not {"card_bid_details", "card_source_refs"} <= tables:
        raise MigrationError("The Bid import tables are missing; run migrate_import_tables.py --apply first")
    add_level = not _has(conn, *LEVEL)
    drop = [f"{t}.{c}" for t, c in OBSOLETE if _has(conn, t, c)]
    counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("users", "boards", "cards", "card_bid_details")}
    return {"add_level": add_level, "drop": drop, "counts": counts, "nothing_to_do": not add_level and not drop}


def _snapshot(conn) -> dict:
    """Every row of every table by the columns this migration leaves alone."""
    return {
        table: conn.execute(
            f"SELECT {', '.join(c[1] for c in conn.execute(f'PRAGMA table_info({table})') if (table, c[1]) not in CHANGED)} "
            f"FROM {table} ORDER BY rowid"
        ).fetchall()
        for table in _tables(conn)
    }


def apply(path: str, expected_sha256: str) -> tuple[dict, str]:
    """Verify, back up and migrate in place under one write lock. Returns (plan, verified backup path).

    Under BEGIN IMMEDIATE, before any DDL: same file (st_dev/st_ino), no sidecar, file hash == expected, the connection's
    own serialization == expected, then the verified exclusive backup of migrate_confidence_level (a backup that is not
    content-equivalent is quarantined as *.UNVERIFIED.db.bak). Any failure rolls back, with a note naming the verified
    backup that was kept, if one was made."""
    kept: dict = {}
    phase = ["before the write transaction"]
    try:
        return _apply(path, expected_sha256, kept, phase)
    except BaseException as exc:
        backup = kept.get("path")
        kept_note = f"verified backup of the approved state kept at {backup}" if backup else "no verified backup was made"
        if phase[0] == "committing":
            exc.add_note(f"the outcome of the COMMIT is NOT confirmed; inspect the schema before re-running; {kept_note}")
        else:
            exc.add_note(f"rolled back, database unchanged; {kept_note}")
        raise


def _apply(path: str, expected_sha256: str, kept: dict, phase: list) -> tuple[dict, str]:
    try:
        identity = _identity(path)
    except FileNotFoundError:
        raise MigrationError(f"{path} does not exist; not migrating") from None
    _refuse_wal(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)  # kept open until SQLite is done: see _hash_of
    except OSError as exc:
        raise MigrationError(f"could not open {path} read-only to hash it ({exc.strerror or exc})") from exc
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != identity:
            raise MigrationError(f"{path} is a different file than the one that was validated; not migrating")
        if _hash_of(path, identity, fd) != expected_sha256:
            raise MigrationError("The database's SHA-256 differs from the approved one; not migrating")
        conn = open_readwrite(path, isolation_level=None)  # mode=rw: never creates a file
        try:
            _check_unchanged(path, identity)  # the connection opened the validated file, not a replacement
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            phase[0] = "in the write transaction"
            try:
                # Re-validated under the write lock, which closes the window since the first hash.
                _check_unchanged(path, identity)
                if _hash_of(path, identity, fd) != expected_sha256:
                    raise MigrationError("The database changed since it was approved (SHA-256 differs)")
                if _serialized_sha256(conn) != expected_sha256:
                    raise MigrationError("The locked connection does not see the approved database (serialized SHA-256 differs)")
                result = plan(conn)
                if result["nothing_to_do"]:
                    raise MigrationError("The database already has the probability-level schema; nothing to do")
                violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise MigrationError(f"The database already has {len(violations)} foreign key violation(s); not migrating")

                source = content_fingerprint(conn)
                kept["path"] = _backup_approved_state(path, identity, fd, expected_sha256, conn, source, tag="bid-confidence")

                before = _snapshot(conn)
                if result["add_level"]:
                    conn.execute(ADD_LEVEL)
                for table, column in OBSOLETE:
                    if _has(conn, table, column):
                        conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
                state, errors = import_schema_state(_query(conn))
                if state != COMPLETE:
                    raise MigrationError("Migrated schema doesn't match the models; rolled back:\n  " + "\n  ".join(errors))
                if _snapshot(conn) != before:
                    raise MigrationError("Existing data changed during the migration; rolled back")
                if result["add_level"] and conn.execute("SELECT COUNT(*) FROM cards WHERE confidence_level IS NOT NULL").fetchone()[0]:
                    raise MigrationError("The new confidence_level column isn't empty; rolled back")
                if conn.execute("PRAGMA foreign_key_check").fetchall():
                    raise MigrationError("Foreign key violations after the migration; rolled back")
                phase[0] = "committing"
                conn.execute("COMMIT")
                phase[0] = "committed"
            except BaseException:
                if phase[0] != "committing" and conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            return result, kept["path"]
        finally:
            conn.close()
    finally:
        os.close(fd)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Add cards.confidence_level and drop the obsolete Bid confidence columns.")
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
            result, backup_path = apply(path, args.expect_sha256.lower())
            counts = ", ".join(f"{count} {table}" for table, count in result["counts"].items())
            print(f"Existing data (unchanged): {counts}")
            print(f"Applied: {'cards.confidence_level added; ' if result['add_level'] else ''}dropped {', '.join(result['drop']) or 'nothing'}. "
                  f"Backup of the previous state: {backup_path}")
            return 0

        conn = open_readonly(path)
        try:
            result = plan(conn)
        finally:
            conn.close()
        if result["nothing_to_do"]:
            print(f"{path} already has the probability-level schema; nothing to do.")
            return 0
        counts = ", ".join(f"{count} {table}" for table, count in result["counts"].items())
        print(f"Dry run on {path} - nothing was changed.")
        print(f"Existing data (unchanged): {counts}")
        print(f"Column to add: {'cards.confidence_level VARCHAR(6) NULL' if result['add_level'] else 'none (already there)'}")
        print(f"Columns to drop: {', '.join(result['drop']) or 'none'}")
        print("Re-run with --apply to back up the database and make the change.")
        return 0
    except (MigrationError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        for note in getattr(exc, "__notes__", []):
            print(f"  {note}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
