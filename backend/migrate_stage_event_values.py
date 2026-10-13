"""Add value snapshot columns to card_stage_events - explicitly, never at startup.

Adds deal_value_snapshot, deal_currency_snapshot and value_snapshot_taken
(column types generated from models.CardStageEvent). Every existing event
gets value_snapshot_taken = 0 and no value: what the card was worth when
those events happened was never recorded, and the current value is not
evidence of it, so nothing is back-filled. From then on the app records the
value with every new event.

Requires the card_stage_events table (migrate_stage_events.py) first.

    uv run python migrate_stage_event_values.py --database pm.db           # dry run (read-only)
    uv run python migrate_stage_event_values.py --database pm.db --apply   # back up, then migrate

--apply: refuses if the columns already exist; writes a backup copy next to
the database (<name>-pre-stage-event-values-<time>.db.bak) first; then, in one
transaction, adds the columns and checks that every table's row count and
every existing event row are unchanged and that no event claims a snapshot -
rolling back otherwise.
"""
import argparse
import sqlite3
import sys

from sqlalchemy.dialects import sqlite as sqlite_dialect

from migrate_stage_kind import MigrationError, backup_database, resolve_database_path
from models import STAGE_EVENT_VALUE_COLUMNS, CardStageEvent

TABLE = CardStageEvent.__table__
TABLES = ("users", "boards", "columns", "cards", "card_stage_events")


def event_columns(conn) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE.name})")}


def ddl() -> list[str]:
    dialect = sqlite_dialect.dialect()
    statements = []
    for name in STAGE_EVENT_VALUE_COLUMNS:
        column = TABLE.c[name]
        clause = f"ALTER TABLE {TABLE.name} ADD COLUMN {name} {column.type.compile(dialect=dialect)}"
        if column.server_default is not None:
            clause += f" NOT NULL DEFAULT {column.server_default.arg}"
        statements.append(clause)
    return statements


def _check_ready(conn) -> None:
    columns = event_columns(conn)
    if not columns:
        raise MigrationError(f"{TABLE.name} is missing; run migrate_stage_events.py --apply first")
    if set(STAGE_EVENT_VALUE_COLUMNS) <= columns:
        raise MigrationError("The value snapshot columns already exist; nothing to do")
    if columns & set(STAGE_EVENT_VALUE_COLUMNS):
        raise MigrationError("Only some value snapshot columns exist; restore a backup and retry")


def plan(conn) -> dict:
    by_type = dict(conn.execute(
        f"SELECT event_type, COUNT(*) FROM {TABLE.name} GROUP BY event_type ORDER BY event_type"
    ).fetchall())
    return {"events": sum(by_type.values()), "by_type": by_type}


def _snapshot(conn, columns):
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    rows = conn.execute(f"SELECT {', '.join(sorted(columns))} FROM {TABLE.name} ORDER BY id").fetchall()
    return counts, rows


def apply(path: str) -> tuple[dict, str]:
    """Back up, then migrate in one transaction. Returns (plan, backup_path)."""
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        _check_ready(conn)
        result = plan(conn)
        original_columns = event_columns(conn)

        backup_path = backup_database(conn, path, "stage-event-values")

        before = _snapshot(conn, original_columns)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in ddl():
                conn.execute(statement)
            claimed = conn.execute(
                f"SELECT COUNT(*) FROM {TABLE.name} WHERE value_snapshot_taken != 0 "
                "OR deal_value_snapshot IS NOT NULL OR deal_currency_snapshot IS NOT NULL"
            ).fetchone()[0]
            if _snapshot(conn, original_columns) != before:
                raise MigrationError("Existing data changed during the migration; rolled back")
            if claimed:
                raise MigrationError("Existing events would claim a value snapshot; rolled back")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return result, backup_path
    finally:
        conn.close()


def _print_plan(result: dict) -> None:
    detail = ", ".join(f"{count} {kind}" for kind, count in result["by_type"].items()) or "none"
    print(f"Existing events: {result['events']} ({detail}) - all marked value_snapshot_taken = 0 (value unknown)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Add value snapshot columns to card_stage_events.")
    parser.add_argument("--database", help="SQLite file (default: from DATABASE_URL, else ./pm.db)")
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
            columns = event_columns(conn)
            if columns and set(STAGE_EVENT_VALUE_COLUMNS) <= columns:
                print("The value snapshot columns already exist; nothing to do.")
                return 0
            _check_ready(conn)
            result = plan(conn)
        finally:
            conn.close()
        print(f"Dry run on {path} - nothing was changed.")
        _print_plan(result)
        print("Re-run with --apply to back up the database and add the columns.")
        return 0
    except MigrationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
