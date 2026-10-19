"""Roll the annual Deal Values forward one calendar year - explicitly, never automatically.

On 1 January the "next year" amount becomes the current one. For every card
whose deal_year is --from-year this script sets

    deal_value           = deal_value_next_year   (NULL when that is 0)
    deal_value_next_year = 0
    deal_year            = --from-year + 1

The previous current-year amount is dropped from the card (it stays in the
pre-rollover backup this script writes). Currency, every other card column and
every other table are untouched. Cards of another deal_year are left alone.
Nothing here runs by itself: until an operator runs it, the stored amounts keep
the year they were entered for, and the UI labels them with the card's own
deal_year rather than the calendar.

Refused: a --from-year that isn't over yet (--from-year + 1 must not be later
than today's year), a database without the annual columns, and (like every
migration) backend/pm.db, a missing file or a hash mismatch. Dry run by default:

    uv run python migrate_deal_year_rollover.py --database /abs/path/pm-commercial.db --from-year 2026
    uv run python migrate_deal_year_rollover.py --database /abs/path/pm-commercial.db --from-year 2026 --expect-sha256 <hash> --apply

Running --apply twice for the same --from-year finds no card left and does nothing.
Stop the app first. Stage-history value snapshots are past events and are not rewritten.
"""
import argparse
import sys
from datetime import date

from db_target_guard import open_readonly, open_readwrite, validate_target
from migrate_deal_years import state
from migrate_import_tables import _tables
from migrate_stage_kind import MigrationError, backup_database

CHANGED = ("deal_value", "deal_value_next_year", "deal_year", "updated_at")


def _today() -> date:
    return date.today()


def plan(conn, from_year: int) -> dict:
    if state(conn) != "complete":
        raise MigrationError("cards has no annual Deal Value columns; run migrate_deal_years.py first")
    if from_year + 1 > _today().year:
        raise MigrationError(f"{from_year} isn't over yet (today is in {_today().year}); nothing rolled")
    count = conn.execute("SELECT COUNT(*) FROM cards WHERE deal_year = ?", (from_year,)).fetchone()[0]
    others = conn.execute("SELECT COUNT(*) FROM cards WHERE deal_year != ?", (from_year,)).fetchone()[0]
    return {"to_roll": count, "left_alone": others}


def _snapshot(conn) -> dict:
    data = {}
    for table in _tables(conn):
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if not (table == "cards" and r[1] in CHANGED)]
        data[table] = conn.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY rowid").fetchall()
    return data


def apply(path: str, from_year: int) -> tuple[dict, str]:
    conn = open_readwrite(path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        result = plan(conn, from_year)
        if result["to_roll"] == 0:
            raise MigrationError(f"No card has deal_year {from_year}; nothing to do")
        backup_path = backup_database(conn, path, "deal-year-rollover")

        conn.execute("BEGIN IMMEDIATE")
        try:
            before = _snapshot(conn)
            expected = {
                row[0]: (row[1] if row[1] else None)
                for row in conn.execute("SELECT id, deal_value_next_year FROM cards WHERE deal_year = ?", (from_year,))
            }
            conn.execute(
                "UPDATE cards SET deal_value = CASE WHEN deal_value_next_year > 0 THEN deal_value_next_year END, "
                "deal_value_next_year = 0, deal_year = ? WHERE deal_year = ?",
                (from_year + 1, from_year),
            )
            if _snapshot(conn) != before:
                raise MigrationError("Columns other than the annual amounts changed; rolled back")
            for card_id, value in expected.items():
                got = conn.execute("SELECT deal_value, deal_value_next_year, deal_year FROM cards WHERE id = ?", (card_id,)).fetchone()
                if got != (value, 0, from_year + 1):
                    raise MigrationError(f"Card {card_id} didn't roll as expected; rolled back")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise MigrationError("Foreign key violations after the rollover; rolled back")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return result, backup_path
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Roll cards' annual Deal Values forward one year.")
    parser.add_argument("--database", required=True, help="SQLite file (required; no default)")
    parser.add_argument("--from-year", required=True, type=int, help="the year that has ended; its cards move to the next one")
    parser.add_argument("--expect-sha256", help="the approved database file's SHA-256 (required with --apply)")
    parser.add_argument("--apply", action="store_true", help="write the change (after a backup); default is a dry run")
    args = parser.parse_args(argv)

    try:
        if args.apply and not args.expect_sha256:
            raise MigrationError("--apply requires --expect-sha256 (the hash of the approved database file)")
        path = validate_target(args.database, expected_sha256=args.expect_sha256)
        if args.apply:
            result, backup_path = apply(path, args.from_year)
            print(f"Rolled {result['to_roll']} card(s) from {args.from_year} to {args.from_year + 1}; "
                  f"{result['left_alone']} left alone. Backup: {backup_path}")
            return 0
        conn = open_readonly(path)
        try:
            result = plan(conn, args.from_year)
        finally:
            conn.close()
        print(f"Dry run on {path} - nothing was changed.")
        print(f"{result['to_roll']} card(s) with deal_year {args.from_year} would roll to {args.from_year + 1}; "
              f"{result['left_alone']} left alone.")
        return 0
    except MigrationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
