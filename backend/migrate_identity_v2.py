"""Upgrade a central identity database from schema version 1 to 2 - explicitly, never at startup.

    uv run python migrate_identity_v2.py --database /abs/path/identity.db           # dry run (default)
    uv run python migrate_identity_v2.py --database /abs/path/identity.db --apply --confirm-app-stopped

Version 2 lets `identity_memberships.role` and `identity_invitations.role` hold 'admin' (reserved for the future
company-scoped Admin). Nothing grants it: the service still assigns only COMPANY_ROLES, so after this migration
no company Admin exists and none has any privilege.

Both tables are rebuilt (SQLite cannot alter a CHECK) in ONE transaction: create the new table, copy every row with
its primary key, drop the old one, rename, recreate its indexes. Rows, ids, AUTOINCREMENT counters
(sqlite_sequence never decreases, even when the highest id was deleted) and the foreign keys of
identity_auth_tokens are preserved; no other table is touched. foreign_key_check and integrity_check must be clean
and PRAGMA user_version = 2 is set in the same transaction, or the whole change is rolled back.

Rules:
- The path is explicit and absolute; there is no environment or .env fallback, and the paths named by
  DATABASE_URL, IDENTITY_DATABASE_URL and PM_COMPANY_DB_* in THIS process are refused. pm.db, pm-commercial.db,
  sidecar files, symlinks, the local acceptance data directory, and any file with a -wal/-journal sidecar are refused.
- Dry run (the default) opens the file read-only, runs the whole migration on an in-memory copy and prints the
  before/after report. Nothing is written.
- --apply needs --confirm-app-stopped: stop the application first (this tool never stops a process). It writes a
  verified backup next to the file (<name>-pre-identity-v2-<time>.db.bak) before changing anything.
- Refused, nothing written: a database already at version 2, any other version, or a version-1 database whose
  schema is not exactly the v1 schema.
- Rollback: there is no reverse migration. Stop the application, restore the backup and deploy the previous code.
- Test it on a disposable synthetic database first (see test_migrate_identity_v2.py); never on a real one before
  that has been approved.
The report holds counts and ids only: no emails, hashes or tokens.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateIndex, CreateTable

import database_targets
from db_target_guard import TargetRefused, validate_target
from identity_models import SCHEMA_ROLES, IdentityBase
from identity_schema import IDENTITY_SCHEMA_VERSION, ROLE_CHECKS, role_check_roles, schema_problems
from migrate_stage_kind import MigrationError, backup_database

FROM_VERSION = 1
V1_ROLES = tuple(role for role in SCHEMA_ROLES if role != "admin")
# Order is irrelevant: foreign keys are off during the rebuild and checked before the commit.
REBUILT_TABLES = tuple(ROLE_CHECKS)
ACCEPTANCE_DATA_DIR = Path.home() / "IA" / "pm-products-accept"  # the local acceptance environment is never a target


def check_target(path: str) -> str:
    """The validated absolute path of a database this tool may open, or MigrationError. Opens nothing."""
    if not path or "://" in path or path.startswith("sqlite:") or path == ":memory:":
        raise MigrationError("--database must be an absolute file path, not a URL or :memory:")
    try:
        resolved = validate_target(path)  # absolute, no symlink, regular single-link SQLite file, no sidecar, not pm.db
        database_targets.check_not_protected(Path(resolved), "--database")
        taken = [database_targets.commercial_database_path(), *database_targets.company_database_paths(check_identity=False).values(), database_targets._configured_identity_path(os.environ)]
    except (TargetRefused, database_targets.DatabaseTargetError) as exc:
        raise MigrationError(f"Refusing this database: {exc}") from None
    if any(other is not None and database_targets.same_file(Path(resolved), other) for other in taken):
        raise MigrationError("Refusing this database: it is assigned to a configured database in this process")
    root = os.path.realpath(ACCEPTANCE_DATA_DIR)
    if os.path.commonpath([os.path.realpath(resolved), root]) == root:
        raise MigrationError("Refusing this database: it is inside the acceptance environment")
    return resolved


def _create_sql(table, name: str) -> str:
    """The model's CREATE TABLE under a temporary name (the final name is restored by the rename)."""
    sql = str(CreateTable(table).compile(dialect=sqlite_dialect.dialect())).strip()
    return sql.replace(f"CREATE TABLE {table.name} ", f"CREATE TABLE {name} ", 1)


def snapshot(conn: sqlite3.Connection) -> dict:
    """Counts, ids and counters only."""
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name")]
    sequences = dict(conn.execute("SELECT name, seq FROM sqlite_sequence")) if _has_sequence(conn) else {}
    return {
        "user_version": conn.execute("PRAGMA user_version").fetchone()[0],
        "rows": {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables},
        "max_id": {t: conn.execute(f'SELECT MAX(id) FROM "{t}"').fetchone()[0] for t in tables},
        "sequences": sequences,
        "indexes": sorted(row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")),
        "role_checks": {t: _roles(conn, t) for t in REBUILT_TABLES},
        "foreign_key_violations": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
    }


def _roles(conn, table):
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return role_check_roles(sql[0] if sql else None, ROLE_CHECKS[table])


def _has_sequence(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone() is not None


def require_version_1(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == IDENTITY_SCHEMA_VERSION:
        raise MigrationError(f"The database is already at identity schema version {version}; nothing to do")
    if version != FROM_VERSION:
        raise MigrationError(f"Identity schema version {version} found; only version {FROM_VERSION} can be migrated")
    problems = schema_problems(conn, roles=V1_ROLES)
    if problems:
        raise MigrationError(f"The database is marked version 1 but is not the v1 schema ({'; '.join(problems)})")


def migrate(conn: sqlite3.Connection, *, _after_step=None) -> None:
    """Rebuild the two tables inside one transaction on `conn`; rolls back and re-raises on any failure.

    `conn` must be opened with isolation_level=None (explicit transactions). _after_step(name) is a test hook
    called after each step so a failure can be injected.
    """
    step = _after_step or (lambda name: None)
    conn.execute("PRAGMA foreign_keys = OFF")  # cannot be changed inside a transaction; the check is run explicitly below
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            require_version_1(conn)  # re-checked under the write lock
            sequences = dict(conn.execute("SELECT name, seq FROM sqlite_sequence")) if _has_sequence(conn) else {}
            for name in REBUILT_TABLES:
                table = IdentityBase.metadata.tables[name]
                columns = ", ".join(c.name for c in table.columns)
                if [row[1] for row in conn.execute(f"PRAGMA table_info({name})")] != [c.name for c in table.columns]:
                    raise MigrationError(f"{name} does not have the expected columns")
                conn.execute(_create_sql(table, f"{name}_v2"))
                conn.execute(f"INSERT INTO {name}_v2 ({columns}) SELECT {columns} FROM {name}")
                step(f"copied {name}")
                conn.execute(f"DROP TABLE {name}")
                conn.execute(f"ALTER TABLE {name}_v2 RENAME TO {name}")
                for index in table.indexes:
                    conn.execute(str(CreateIndex(index).compile(dialect=sqlite_dialect.dialect())).strip())
                # The copy set the counter to MAX(id); restore the original if it was higher (deleted top ids).
                if name in sequences:
                    updated = conn.execute(
                        "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = ?", (sequences[name], name)
                    ).rowcount
                    if not updated:
                        conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (name, sequences[name]))
                step(f"rebuilt {name}")
            conn.execute(f"PRAGMA user_version = {IDENTITY_SCHEMA_VERSION}")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise MigrationError("foreign_key_check found violations after the rebuild")
            if (result := conn.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
                raise MigrationError(f"integrity_check failed after the rebuild ({result})")
            problems = schema_problems(conn)
            if problems:
                raise MigrationError(f"the rebuilt schema is not the v2 schema ({'; '.join(problems)})")
            step("verified")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def dry_run(path: str) -> tuple[dict, dict]:
    """(before, after) from running the migration on an in-memory copy of a read-only view of the file."""
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(":memory:", isolation_level=None)
        source.backup(copy)
    finally:
        source.close()
    try:
        before = snapshot(copy)
        migrate(copy)
        return before, snapshot(copy)
    finally:
        copy.close()


def apply(path: str) -> tuple[dict, dict, str]:
    """Back up, migrate in place, verify. Returns (before, after, backup path)."""
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        before = snapshot(conn)
        require_version_1(conn)
        backup_path = backup_database(conn, path, "identity-v2")
        migrate(conn)
        return before, snapshot(conn), backup_path
    finally:
        conn.close()


def report(before: dict, after: dict) -> str:
    lines = [f"  schema version: {before['user_version']} -> {after['user_version']}"]
    for table in sorted(before["rows"]):
        lines.append(f"  {table}: {before['rows'][table]} rows -> {after['rows'].get(table)} rows, max id {before['max_id'][table]} -> {after['max_id'].get(table)}")
    for table in REBUILT_TABLES:
        lines.append(f"  {table} roles: {before['role_checks'][table]} -> {after['role_checks'][table]}")
    lines.append("  sqlite_sequence: " + ", ".join(f"{t} {before['sequences'].get(t)} -> {after['sequences'].get(t)}" for t in sorted(after["sequences"])))
    lines.append(f"  indexes: {len(before['indexes'])} -> {len(after['indexes'])}")
    lines.append(f"  foreign key violations: {before['foreign_key_violations']} -> {after['foreign_key_violations']}")
    lines.append(f"  integrity_check: {before['integrity']} -> {after['integrity']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Upgrade the identity database from schema version 1 to 2.")
    parser.add_argument("--database", required=True, help="absolute path of the identity database (required; no default)")
    parser.add_argument("--apply", action="store_true", help="write the change (after a backup); default is a dry run")
    parser.add_argument("--confirm-app-stopped", action="store_true", help="required with --apply: the application is stopped")
    args = parser.parse_args(argv)
    try:
        path = check_target(args.database)
        if args.apply:
            if not args.confirm_app_stopped:
                raise MigrationError("--apply needs --confirm-app-stopped (stop the application first; this tool never stops it)")
            before, after, backup_path = apply(path)
            print(f"Migrated {path} to identity schema version {IDENTITY_SCHEMA_VERSION}\n{report(before, after)}")
            print(f"Applied. Backup of the previous state: {backup_path}")
            return 0
        before, after = dry_run(path)
        print(f"Dry run on {path} (an in-memory copy was migrated; nothing was written):\n{report(before, after)}")
        return 0
    except (MigrationError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
