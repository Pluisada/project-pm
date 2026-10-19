"""Add session versions, email verification and persistent tokens to an existing
database - explicitly, never at startup.

Changes, all in one transaction after a backup:
- users.session_version INTEGER NOT NULL DEFAULT 0 (access tokens carry it; logout bumps it)
- users.email_verified_at DATETIME, set to users.created_at for every EXISTING user:
  they already had access, and the application grants none without a verified email
- table auth_tokens (hashed, single-use signup/reset tokens), empty
- table audit_events (assignment changes and deletions), empty

No other row of any table is changed. Existing access tokens stop working
(they have no `sv` claim), so everyone signs in again.

The database must be named explicitly - there's no DATABASE_URL or ./pm.db
fallback - and the default is a dry run on a read-only connection:

    uv run python migrate_auth_security.py --database pm.db           # dry run
    uv run python migrate_auth_security.py --database pm.db --apply   # back up, then migrate

Stop the app before --apply. Refused (exit 1, nothing written): a database with
no users table, one that is already (or partly) migrated, and a second --apply.
Writes <name>-pre-auth-security-<time>.db.bak next to the database first.
"""
import argparse
import sqlite3
import sys

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from database import AUTH_SECURITY_TABLES, AUTH_SECURITY_USER_COLUMNS
from migrate_stage_kind import MigrationError, backup_database, resolve_database_path
from models import AuditEvent, AuthToken

USER_COLUMN_DDL = {
    "session_version": "ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0",
    "email_verified_at": "ALTER TABLE users ADD COLUMN email_verified_at DATETIME",
}


def _state(conn) -> tuple[list[str], list[str]]:
    """(tables, user columns) of the auth-security group that are already present."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "users" not in tables:
        raise MigrationError("This database has no users table; nothing to migrate")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    return (
        [t for t in AUTH_SECURITY_TABLES if t in tables],
        [c for c in AUTH_SECURITY_USER_COLUMNS if c in columns],
    )


def plan(conn) -> dict:
    present_tables, present_columns = _state(conn)
    if len(present_tables) == len(AUTH_SECURITY_TABLES) and len(present_columns) == len(AUTH_SECURITY_USER_COLUMNS):
        return {"state": "complete", "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]}
    if present_tables or present_columns:
        raise MigrationError(
            "The database is partly migrated "
            f"(present: {', '.join(present_tables + present_columns)}); restore a backup"
        )
    return {"state": "absent", "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]}


def _ddl() -> list[str]:
    dialect = sqlite_dialect.dialect()
    statements = []
    for model in (AuthToken, AuditEvent):
        table = model.__table__
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip())
        statements += [str(CreateIndex(index).compile(dialect=dialect)).strip() for index in table.indexes]
    return statements


def _snapshot_other_data(conn) -> dict:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'users' AND name NOT LIKE 'sqlite_%'")]
    return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}


def apply(path: str) -> tuple[dict, str]:
    """Back up, then migrate in one transaction. Returns (plan, backup_path)."""
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        result = plan(conn)
        if result["state"] == "complete":
            raise MigrationError("The database already has the auth security schema; nothing to do")
        backup_path = backup_database(conn, path, "auth-security")
        conn.execute("BEGIN IMMEDIATE")
        try:
            if plan(conn)["state"] != "absent":
                raise MigrationError("The database changed after the check; rolled back")
            before = _snapshot_other_data(conn)
            for statement in USER_COLUMN_DDL.values():
                conn.execute(statement)
            conn.execute("UPDATE users SET email_verified_at = created_at")
            for statement in _ddl():
                conn.execute(statement)
            if plan(conn)["state"] != "complete":
                raise MigrationError("Created schema is incomplete; rolled back")
            after = _snapshot_other_data(conn)
            if {t: after.get(t) for t in before} != before or conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] != result["users"]:
                raise MigrationError("Existing data changed during the migration; rolled back")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return result, backup_path
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Add session versions, email verification and persistent tokens.")
    parser.add_argument("--database", required=True, help="SQLite file to migrate (required; no default)")
    parser.add_argument("--apply", action="store_true", help="write the change (after a backup); default is a dry run")
    args = parser.parse_args(argv)
    try:
        path = resolve_database_path(args.database)
        if args.apply:
            print(f"Migrating {path}")
            result, backup_path = apply(path)
            print(f"Added users.session_version and users.email_verified_at ({result['users']} existing users marked verified)")
            print(f"Created tables: {', '.join(AUTH_SECURITY_TABLES)}")
            print(f"Applied. Backup of the previous state: {backup_path}")
            return 0
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = plan(conn)
        finally:
            conn.close()
        if result["state"] == "complete":
            print(f"{path} already has the auth security schema; nothing to do.")
        else:
            print(f"Dry run on {path}: would add users.session_version and users.email_verified_at "
                  f"({result['users']} existing users marked verified), and create {', '.join(AUTH_SECURITY_TABLES)}. Nothing written.")
        return 0
    except (MigrationError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
