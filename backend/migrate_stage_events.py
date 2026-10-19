"""Add card_stage_events to an existing database - explicitly, never at startup.

Creates the stage-history table (DDL generated from models.CardStageEvent, so
it can't drift from the app) and records one "baseline" event per existing
card: the stage it is in at migration time. Baselines are a reference point,
not history - nothing is known about moves made before tracking started, and
nothing is invented: no "created", "won" or "lost" events are back-filled,
and occurred_at is the migration time, not the card's creation date.

Requires the stage_kind migration (migrate_stage_kind.py) first.

    uv run python migrate_stage_events.py --database pm.db           # dry run (read-only)
    uv run python migrate_stage_events.py --database pm.db --apply   # back up, then migrate

--apply: refuses if the table already exists; writes a backup copy next to
the database (<name>-pre-stage-events-<time>.db.bak) first; then, in one
transaction, creates the table and the baselines and checks that users,
boards, columns, cards and card-column links are unchanged and that every
card got exactly one baseline - rolling back otherwise.
"""
import argparse
import sqlite3
import sys

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from lead_options import STAGE_KINDS
from migrate_stage_kind import MigrationError, backup_database, has_stage_kind, resolve_database_path
from models import CardStageEvent
from stage_history import utcnow

TABLE = CardStageEvent.__table__


def has_events_table(conn) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE.name,)
    ).fetchone() is not None


def ddl() -> list[str]:
    dialect = sqlite_dialect.dialect()
    statements = [str(CreateTable(TABLE).compile(dialect=dialect))]
    statements += [str(CreateIndex(index).compile(dialect=dialect)) for index in TABLE.indexes]
    return statements


def plan(conn) -> dict:
    """What a migration would do. Cards whose column is missing can't be
    placed in a stage and are reported, not given a baseline."""
    cards = conn.execute(
        "SELECT k.id, k.column_id, c.board_id, c.stage_kind "
        "FROM cards k LEFT JOIN columns c ON c.id = k.column_id ORDER BY k.id"
    ).fetchall()
    baselines = [row for row in cards if row[2] is not None]
    orphans = [row[0] for row in cards if row[2] is None]
    return {
        "cards": len(cards),
        "baselines": len(baselines),
        "by_kind": {kind: sum(1 for row in baselines if row[3] == kind) for kind in STAGE_KINDS},
        "warnings": [f"Card {card_id} points to a missing column; it gets no baseline" for card_id in orphans],
    }


def _snapshot(conn):
    count = lambda table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return (
        {table: count(table) for table in ("users", "boards", "columns", "cards")},
        sorted(conn.execute("SELECT id, column_id FROM cards").fetchall()),
    )


def apply(path: str, now=None) -> tuple[dict, str]:
    """Back up, then migrate in one transaction. Returns (plan, backup_path)."""
    now = now or utcnow()
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        if not has_stage_kind(conn):
            raise MigrationError("columns.stage_kind is missing; run migrate_stage_kind.py --apply first")
        if has_events_table(conn):
            raise MigrationError(f"{TABLE.name} already exists; nothing to do")
        result = plan(conn)

        backup_path = backup_database(conn, path, "stage-events")

        before = _snapshot(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in ddl():
                conn.execute(statement)
            conn.execute(
                # The value snapshot is the card's value now - which is the
                # value at the baseline's own timestamp, so it is a recorded
                # fact, not a reconstruction.
                f"INSERT INTO {TABLE.name} (card_id, board_id, event_type, from_column_id, from_stage_kind, "
                "to_column_id, to_stage_kind, user_id, source, occurred_at, "
                "deal_value_snapshot, deal_currency_snapshot, value_snapshot_taken) "
                "SELECT k.id, c.board_id, 'baseline', NULL, NULL, k.column_id, c.stage_kind, NULL, 'migration', ?, "
                "k.deal_value, k.currency, 1 "
                "FROM cards k JOIN columns c ON c.id = k.column_id",
                (now.isoformat(sep=" "),),
            )
            written = conn.execute(
                f"SELECT COUNT(*), COUNT(DISTINCT card_id) FROM {TABLE.name} WHERE event_type = 'baseline'"
            ).fetchone()
            if _snapshot(conn) != before:
                raise MigrationError("Existing data changed during the migration; rolled back")
            if written != (result["baselines"], result["baselines"]):
                raise MigrationError("Baseline count doesn't match the cards; rolled back")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return result, backup_path
    finally:
        conn.close()


def _print_plan(result: dict) -> None:
    kinds = ", ".join(f"{count} {kind}" for kind, count in result["by_kind"].items())
    print(f"Cards: {result['cards']}; baseline events: {result['baselines']} ({kinds})")
    for warning in result["warnings"]:
        print(f"WARNING: {warning}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Add the card stage-history table with baselines.")
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
            if not has_stage_kind(conn):
                raise MigrationError("columns.stage_kind is missing; run migrate_stage_kind.py --apply first")
            if has_events_table(conn):
                print(f"{TABLE.name} already exists; nothing to do.")
                return 0
            result = plan(conn)
        finally:
            conn.close()
        print(f"Dry run on {path} - nothing was changed.")
        _print_plan(result)
        print("Re-run with --apply to back up the database, create the table and write the baselines.")
        return 0
    except MigrationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
