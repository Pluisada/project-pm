"""Add columns.stage_kind to an existing database - explicitly, never at startup.

Reports used to decide won/lost by comparing a column's title with exactly
"Award" or "Lost". This migration records that same classification in a
stable stage_kind column ("open" / "won" / "lost"), so renaming a column can
no longer change the reports.

Default is a dry run against a read-only connection: it prints how every
column would be classified and why, and changes nothing.

    uv run python migrate_stage_kind.py                       # dry run, DATABASE_URL or ./pm.db
    uv run python migrate_stage_kind.py --database other.db   # dry run on another file
    uv run python migrate_stage_kind.py --set 12=won --set 14=lost   # resolve ambiguity
    uv run python migrate_stage_kind.py --apply               # back up, then migrate

Classification (identical to the old report logic - nothing is reinterpreted):
- title exactly "Award" -> won; exactly "Lost" -> lost; anything else -> open.
- If a board has more than one column titled exactly "Award" (or "Lost"),
  none of them is classified automatically: the run stops and asks for an
  explicit --set COLUMN_ID=KIND, since only one won and one lost stage are
  allowed per board.
- Titles that merely look like a won/lost stage ("award", " Lost", "Won",
  "Perdido", ...) were counted as open by the old reports, so they stay open,
  but are flagged so an operator can decide with --set.

--apply: refuses if stage_kind already exists or ambiguities are unresolved;
writes a backup copy next to the database (<name>-pre-stage-kind-<time>.db.bak)
first; then, in one transaction, adds the column and sets the kinds, checks
that cards, columns and card-column links are unchanged, and rolls back if not.
"""
import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

from lead_options import STAGE_KINDS, STAGE_LOST, STAGE_OPEN, STAGE_WON

# The exact titles the reports used before stage_kind existed.
LEGACY_TITLES = {"Award": STAGE_WON, "Lost": STAGE_LOST}
# Near-misses worth a human look; they were (and stay) "open".
LOOKALIKES = {"award", "awarded", "won", "win", "closed won", "ganho", "lost", "closed lost", "perdido", "perdida"}


class MigrationError(Exception):
    """The migration can't proceed; the message says why."""


def sqlite_path_from_url(url: str) -> str:
    if not url.startswith("sqlite:///"):
        raise MigrationError("Only SQLite databases are supported")
    path = url[len("sqlite:///"):]
    if not path or path == ":memory:":
        raise MigrationError("An in-memory database has nothing to migrate")
    return os.path.abspath(path)


def resolve_database_path(argument) -> str:
    """The --database argument, else DATABASE_URL, else ./pm.db - as an
    absolute path to an existing file. Shared by every migration script."""
    path = os.path.abspath(argument) if argument else sqlite_path_from_url(
        os.getenv("DATABASE_URL", "sqlite:///./pm.db")
    )
    if not os.path.isfile(path):
        raise MigrationError(f"No database file at {path}")
    return path


def backup_database(conn, path: str, tag: str, now=datetime.now) -> str:
    """Copy the open database next to itself as <name>-pre-<tag>-<time>.db.bak
    (the suffix .gitignore ignores) and return the copy's path."""
    base, _ = os.path.splitext(path)
    backup_path = f"{base}-pre-{tag}-{now():%Y%m%d%H%M%S}.db.bak"
    backup = sqlite3.connect(backup_path)
    with backup:
        conn.backup(backup)
    backup.close()
    return backup_path


def has_stage_kind(conn) -> bool:
    return any(row[1] == "stage_kind" for row in conn.execute("PRAGMA table_info(columns)"))


def plan(conn, overrides: dict | None = None) -> dict:
    """Classify every column. Returns {"columns": [...], "errors": [...], "warnings": [...]}."""
    overrides = overrides or {}
    rows = conn.execute(
        "SELECT c.id, c.board_id, c.position, c.title, "
        "(SELECT COUNT(*) FROM cards k WHERE k.column_id = c.id) "
        "FROM columns c ORDER BY c.board_id, c.position, c.id"
    ).fetchall()
    known_ids = {row[0] for row in rows}
    errors, warnings = [], []
    for column_id, kind in overrides.items():
        if column_id not in known_ids:
            errors.append(f"--set {column_id}={kind}: no column with id {column_id}")
        if kind not in STAGE_KINDS:
            errors.append(f"--set {column_id}={kind}: kind must be one of {', '.join(STAGE_KINDS)}")

    # Exact legacy matches per board, to spot duplicates.
    matches = defaultdict(list)
    for column_id, board_id, _pos, title, _cards in rows:
        if title in LEGACY_TITLES:
            matches[(board_id, LEGACY_TITLES[title])].append(column_id)

    columns = []
    for column_id, board_id, position, title, cards in rows:
        kind, reason = STAGE_OPEN, "not a won/lost title"
        legacy = LEGACY_TITLES.get(title)
        if legacy and len(matches[(board_id, legacy)]) == 1:
            kind, reason = legacy, f'title is exactly "{title}"'
        elif legacy:
            kind, reason = None, f'{len(matches[(board_id, legacy)])} columns on board {board_id} are titled "{title}"'
            if column_id not in overrides:
                errors.append(
                    f'Ambiguous: column {column_id} ("{title}", {cards} cards) - choose with --set {column_id}=KIND'
                )
        elif title.strip().casefold() in LOOKALIKES:
            reason = "looks like a won/lost stage but isn't an exact match (old reports: open)"
            if column_id not in overrides:
                warnings.append(
                    f'Column {column_id} "{title}" stays open; use --set {column_id}=won|lost if that is wrong'
                )
        if column_id in overrides:
            kind, reason = overrides[column_id], "set explicitly with --set"
        columns.append({
            "id": column_id, "board_id": board_id, "position": position,
            "title": title, "cards": cards, "kind": kind, "reason": reason,
        })

    # At most one won and one lost stage per board after overrides.
    per_board = defaultdict(list)
    for col in columns:
        if col["kind"] in (STAGE_WON, STAGE_LOST):
            per_board[(col["board_id"], col["kind"])].append(col["id"])
    for (board_id, kind), ids in per_board.items():
        if len(ids) > 1:
            errors.append(f'Board {board_id} would have {len(ids)} "{kind}" stages (columns {ids}); only one is allowed')
    return {"columns": columns, "errors": errors, "warnings": warnings}


def _snapshot(conn):
    return (
        conn.execute("SELECT COUNT(*) FROM columns").fetchone()[0],
        sorted(conn.execute("SELECT id, column_id FROM cards").fetchall()),
    )


def apply(path: str, overrides: dict | None = None, now=datetime.now) -> tuple[dict, str]:
    """Back up, then migrate in one transaction. Returns (plan, backup_path)."""
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        if has_stage_kind(conn):
            raise MigrationError("columns.stage_kind already exists; nothing to do")
        result = plan(conn, overrides)
        if result["errors"]:
            raise MigrationError("Unresolved problems:\n  " + "\n  ".join(result["errors"]))

        backup_path = backup_database(conn, path, "stage-kind", now)

        before = _snapshot(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("ALTER TABLE columns ADD COLUMN stage_kind VARCHAR NOT NULL DEFAULT 'open'")
            for col in result["columns"]:
                if col["kind"] != STAGE_OPEN:
                    conn.execute("UPDATE columns SET stage_kind = ? WHERE id = ?", (col["kind"], col["id"]))
            if _snapshot(conn) != before:
                raise MigrationError("Cards or columns changed during the migration; rolled back")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return result, backup_path
    finally:
        conn.close()


def _print_plan(result: dict) -> None:
    print(f"{'board':>5} {'col':>4} {'pos':>3}  {'kind':<6} {'cards':>5}  title  (reason)")
    for col in result["columns"]:
        kind = col["kind"] or "?"
        print(f"{col['board_id']:>5} {col['id']:>4} {col['position']:>3}  {kind:<6} {col['cards']:>5}  {col['title']}  ({col['reason']})")
    for warning in result["warnings"]:
        print(f"WARNING: {warning}")
    for error in result["errors"]:
        print(f"ERROR: {error}")


def _parse_overrides(values) -> dict:
    overrides = {}
    for value in values or []:
        column_id, sep, kind = value.partition("=")
        if not sep or not column_id.strip().isdigit():
            raise MigrationError(f"--set expects COLUMN_ID=KIND, got {value!r}")
        overrides[int(column_id)] = kind.strip()
    return overrides


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Add a stable stage_kind to board columns.")
    parser.add_argument("--database", help="SQLite file (default: from DATABASE_URL, else ./pm.db)")
    parser.add_argument("--set", action="append", metavar="COLUMN_ID=KIND", help="classify a column explicitly")
    parser.add_argument("--apply", action="store_true", help="write the change (after a backup); default is a dry run")
    args = parser.parse_args(argv)

    try:
        path = resolve_database_path(args.database)
        overrides = _parse_overrides(args.set)

        if args.apply:
            result, backup_path = apply(path, overrides)
            _print_plan(result)
            print(f"Applied. Backup of the previous state: {backup_path}")
            return 0

        # Dry run: read-only connection, so it can't write even by accident.
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if has_stage_kind(conn):
                print("columns.stage_kind already exists; nothing to do.")
                return 0
            result = plan(conn, overrides)
        finally:
            conn.close()
        print(f"Dry run on {path} - nothing was changed.")
        _print_plan(result)
        if result["errors"]:
            print("Resolve the errors above (with --set) before --apply.")
            return 2
        print("Re-run with --apply to back up the database and write this classification.")
        return 0
    except MigrationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
