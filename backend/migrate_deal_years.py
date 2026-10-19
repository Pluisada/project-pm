"""Add the annual Deal Value columns to cards - explicitly, never at startup.

cards.deal_value stays as it is and becomes the amount for the "current" year
(cards.deal_year). Two columns are added:

- deal_value_next_year  FLOAT NOT NULL DEFAULT 0   (0 for every existing card)
- deal_year             INTEGER NOT NULL DEFAULT <--year>   (the year the existing deal_value belongs to)

The year can't be inferred from the data, so the operator states it with
--year (normally the current calendar year at the time of the migration).
Nothing else is read or changed: no card amount, currency, user, board,
history or Bid row. Bid revenue is a different concept and is not touched.

The database must be named explicitly, as an absolute path - there is no
DATABASE_URL or ./pm.db fallback - and the default is a dry run:

    uv run python migrate_deal_years.py --database /abs/path/pm-commercial.db --year 2026
    uv run python migrate_deal_years.py --database /abs/path/pm-commercial.db --year 2026 --expect-sha256 <hash> --apply

--apply requires --expect-sha256. db_target_guard refuses backend/pm.db,
backend/pm.db.bak, symlinks, a missing file, a -wal/-journal sidecar and a
hash mismatch. Stop the app before --apply. Refused (exit 1, nothing written):
a database the earlier migrations haven't reached, a cards table with only one
of the two columns, and a second --apply (a dry run on a migrated database
reports nothing to do).

--apply writes <name>-pre-deal-years-<time>.db.bak, then in one transaction
adds the columns, checks that every existing row of every table is unchanged,
every deal_value is intact, deal_value_next_year is 0 and deal_year is --year
everywhere, and rolls back otherwise. Year rollover later is
migrate_deal_year_rollover.py (docs/DATABASE.md).
"""
import argparse
import sys

from db_target_guard import open_readonly, open_readwrite, validate_target
from migrate_import_tables import _tables, check_prerequisites
from migrate_stage_kind import MigrationError, backup_database

NEW_COLUMNS = ("deal_value_next_year", "deal_year")


def card_columns(conn) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(cards)")}


def state(conn) -> str:
    """"absent" or "complete"; raises when only one column is there."""
    present = set(NEW_COLUMNS) & card_columns(conn)
    if not present:
        return "absent"
    if present == set(NEW_COLUMNS):
        return "complete"
    raise MigrationError(f"cards has only {', '.join(sorted(present))} of {', '.join(NEW_COLUMNS)}; restore a backup")


def plan(conn) -> dict:
    check_prerequisites(conn)
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("users", "boards", "cards")}
    return {"state": state(conn), "counts": counts}


def _snapshot(conn) -> dict:
    """Every row of every table by its pre-migration columns, plus every schema object but cards."""
    data = {}
    for table in _tables(conn):
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if not (table == "cards" and r[1] in NEW_COLUMNS)]
        data[table] = conn.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY rowid").fetchall()
    objects = conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE tbl_name != 'cards' ORDER BY type, name").fetchall()
    return {"data": data, "objects": objects}


def apply(path: str, year: int) -> tuple[dict, str]:
    """Back up, then migrate in one transaction. Returns (plan, backup_path)."""
    conn = open_readwrite(path, isolation_level=None)  # mode=rw: never creates a file
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        result = plan(conn)
        if result["state"] == "complete":
            raise MigrationError("The annual Deal Value columns already exist; nothing to do")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationError("The database already has foreign key violations; not migrating")

        backup_path = backup_database(conn, path, "deal-years")

        conn.execute("BEGIN IMMEDIATE")
        try:
            if state(conn) == "complete":
                raise MigrationError("The database changed after the check; rolled back")
            before = _snapshot(conn)
            conn.execute("ALTER TABLE cards ADD COLUMN deal_value_next_year FLOAT NOT NULL DEFAULT 0")
            conn.execute(f"ALTER TABLE cards ADD COLUMN deal_year INTEGER NOT NULL DEFAULT {int(year)}")
            if _snapshot(conn) != before:
                raise MigrationError("Existing data or schema changed during the migration; rolled back")
            bad = conn.execute(
                "SELECT COUNT(*) FROM cards WHERE deal_value_next_year != 0 OR deal_year != ?", (year,)
            ).fetchone()[0]
            if bad:
                raise MigrationError("The new columns aren't 0 / the given year on every card; rolled back")
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
    parser = argparse.ArgumentParser(description="Add cards.deal_value_next_year and cards.deal_year.")
    parser.add_argument("--database", required=True, help="SQLite file to migrate (required; no default)")
    parser.add_argument("--year", required=True, type=int, help="the calendar year the existing deal_value amounts belong to")
    parser.add_argument("--expect-sha256", help="the approved database file's SHA-256 (required with --apply)")
    parser.add_argument("--apply", action="store_true", help="write the change (after a backup); default is a dry run")
    args = parser.parse_args(argv)

    try:
        if not 2000 <= args.year <= 2100:
            raise MigrationError("--year must be a four-digit calendar year")
        if args.apply and not args.expect_sha256:
            raise MigrationError("--apply requires --expect-sha256 (the hash of the approved database file)")
        path = validate_target(args.database, expected_sha256=args.expect_sha256)

        if args.apply:
            print(f"Migrating {path}")
            result, backup_path = apply(path, args.year)
            print(f"Existing data (unchanged): " + ", ".join(f"{n} {t}" for t, n in result["counts"].items()))
            print(f"Applied: deal_value_next_year = 0 and deal_year = {args.year} on every card. Backup: {backup_path}")
            return 0

        conn = open_readonly(path)
        try:
            result = plan(conn)
        finally:
            conn.close()
        if result["state"] == "complete":
            print(f"The annual Deal Value columns already exist in {path}; nothing to do.")
            return 0
        print(f"Dry run on {path} - nothing was changed.")
        print("Existing data (unchanged): " + ", ".join(f"{n} {t}" for t, n in result["counts"].items()))
        print(f"Columns to add: cards.deal_value_next_year FLOAT NOT NULL DEFAULT 0, cards.deal_year INTEGER NOT NULL DEFAULT {args.year}")
        print("Every existing deal_value stays as the amount for that year; re-run with --apply to back up and migrate.")
        return 0
    except MigrationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
