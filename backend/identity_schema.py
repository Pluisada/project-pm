"""What the identity schema must look like, checked read-only on a plain sqlite3 connection.

Shared by identity_database.py (every open) and migrate_identity_v2.py. The expected DDL is generated from the
declarative models, so the models stay the single definition.

Versions (PRAGMA user_version):
  1  memberships and invitations accept only 'sales_member' and 'visitor'.
  2  both also accept 'admin' (reserved for the future company-scoped Admin; the service still refuses to assign it).
"""
import re
import sqlite3

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateIndex

from identity_models import SCHEMA_ROLES, IdentityBase

IDENTITY_SCHEMA_VERSION = 2

# The two tables whose role CHECK changed between v1 and v2, with that constraint's name.
ROLE_CHECKS = {
    "identity_memberships": "ck_identity_memberships_role",
    "identity_invitations": "ck_identity_invitations_role",
}


def normalize_sql(sql: str | None) -> str:
    """Case, quoting and whitespace-insensitive form of a DDL statement (SQLite re-quotes names on RENAME)."""
    text = (sql or "").replace('"', "").replace("`", "").lower()
    text = re.sub(r"\s+", " ", text)
    return re.sub(r" ?([(),]) ?", r"\1", text).strip()


def expected_indexes() -> dict[str, str]:
    """Index name -> normalized CREATE INDEX statement, for every named index of the models."""
    dialect = sqlite_dialect.dialect()
    return {
        index.name: normalize_sql(str(CreateIndex(index).compile(dialect=dialect)))
        for table in IdentityBase.metadata.tables.values()
        for index in table.indexes
    }


def table_sql(conn: sqlite3.Connection) -> dict[str, str]:
    return {name: sql for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'")}


def role_check_roles(sql: str | None, constraint: str) -> tuple[str, ...] | None:
    """The roles named by a table's role CHECK, or None if the constraint is absent."""
    match = re.search(
        rf"constraint {constraint} check\(role in\(([^)]*)\)\)", normalize_sql(sql)
    )
    if match is None:
        return None
    return tuple(part.strip("'") for part in match.group(1).split(","))


def schema_problems(conn: sqlite3.Connection, roles: tuple[str, ...] = SCHEMA_ROLES) -> list[str]:
    """Every difference between the database and the expected schema (tables, role CHECKs, AUTOINCREMENT,
    indexes, foreign keys). Empty means it matches. Reads only; messages hold no row data."""
    problems = []
    tables = table_sql(conn)
    missing = sorted(set(IdentityBase.metadata.tables) - set(tables))
    if missing:
        problems.append(f"missing identity tables ({len(missing)})")
    for name in sorted(set(IdentityBase.metadata.tables) & set(tables)):
        if "autoincrement" not in normalize_sql(tables[name]):
            problems.append(f"table {name} lost AUTOINCREMENT")
    for table, constraint in ROLE_CHECKS.items():
        if table not in tables:
            continue
        found = role_check_roles(tables[table], constraint)
        if found != roles:
            problems.append(f"table {table} has role check {found}, expected {roles}")
    indexes = {name: sql for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index'")}
    for name, expected in expected_indexes().items():
        if name not in indexes:
            problems.append(f"missing index {name}")
        elif normalize_sql(indexes[name]) != expected:
            problems.append(f"index {name} differs from the expected definition")
    if not missing:
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            problems.append(f"{len(violations)} foreign key violation(s)")
    return problems
