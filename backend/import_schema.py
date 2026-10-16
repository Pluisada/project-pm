"""Expected schema of the Salcomp Bid import tables, and a check of a live
SQLite database against it.

database.init_db() and migrate_import_tables.py both use import_schema_state,
so startup and the migration accept exactly the same schema. The expectation
is never written out by hand: it's read back (via PRAGMA) from an in-memory
database built with the DDL that ddl() generates from the models, and the live
database is read back the same way - columns (type, NOT NULL, default,
primary key), indexes (columns, uniqueness, origin) and foreign keys
(target, ON DELETE / ON UPDATE).
"""
import sqlite3
from functools import cache

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from models import CardBidDetails, CardImportChange, CardSourceRef, ImportBatch, ImportBatchItem, ImportBatchLedger

# Creation order: card_source_refs and card_import_changes reference import_batches.
IMPORT_TABLES = (ImportBatch.__table__, CardBidDetails.__table__, CardSourceRef.__table__, CardImportChange.__table__)
IMPORT_TABLE_NAMES = tuple(table.name for table in IMPORT_TABLES)

# Part 23 (import revert): a separate group with its own state, so a database
# that has the Part 22 tables but not these isn't reported as incompatible.
REVERT_TABLES = (ImportBatchLedger.__table__, ImportBatchItem.__table__)
REVERT_TABLE_NAMES = tuple(table.name for table in REVERT_TABLES)

ABSENT = "absent"              # none of the tables: the migration hasn't run
PARTIAL = "partial"            # some of them: an interrupted or manual change
INCOMPATIBLE = "incompatible"  # all of them, but not as the models define them
COMPLETE = "complete"


def _ddl(tables) -> list[str]:
    dialect = sqlite_dialect.dialect()
    statements = []
    for table in tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip())
        for index in sorted(table.indexes, key=lambda i: i.name):
            statements.append(str(CreateIndex(index).compile(dialect=dialect)))
    return statements


def ddl() -> list[str]:
    """CREATE TABLE / CREATE INDEX statements, identical to what create_all emits."""
    return _ddl(IMPORT_TABLES)


def revert_ddl() -> list[str]:
    """The same for the Part 23 tables."""
    return _ddl(REVERT_TABLES)


def describe(query, table: str) -> dict:
    """Columns, indexes and foreign keys of `table`, as SQLite reports them.
    `query(sql)` runs one statement and returns its rows."""
    columns = {
        name: (type_.upper(), bool(notnull), default, pk)
        for _cid, name, type_, notnull, default, pk in query(f"PRAGMA table_info({table})")
    }
    indexes = set()
    for _seq, name, unique, origin, _partial in query(f"PRAGMA index_list({table})"):
        indexed = tuple(row[2] for row in query(f"PRAGMA index_info({name})"))
        # Auto-index names (sqlite_autoindex_*) depend on declaration order; only explicit ones are compared.
        indexes.add((name if origin == "c" else None, indexed, bool(unique), origin))
    foreign_keys = {
        (from_, target, to, on_update.upper(), on_delete.upper())
        for _id, _seq, target, from_, to, on_update, on_delete, _match in query(f"PRAGMA foreign_key_list({table})")
    }
    return {"columns": columns, "indexes": indexes, "foreign_keys": foreign_keys}


@cache
def expected_schema() -> dict:
    conn = sqlite3.connect(":memory:")
    try:
        for statement in ddl() + revert_ddl():
            conn.execute(statement)
        query = lambda sql: conn.execute(sql).fetchall()
        return {name: describe(query, name) for name in IMPORT_TABLE_NAMES + REVERT_TABLE_NAMES}
    finally:
        conn.close()


def _differences(table: str, expected: dict, actual: dict) -> list[str]:
    errors = []
    for name in sorted(expected["columns"].keys() - actual["columns"].keys()):
        errors.append(f"{table}.{name} is missing")
    for name in sorted(actual["columns"].keys() - expected["columns"].keys()):
        errors.append(f"{table}.{name} is not in the model")
    for name in sorted(expected["columns"].keys() & actual["columns"].keys()):
        if expected["columns"][name] != actual["columns"][name]:
            errors.append(
                f"{table}.{name} is (type, not null, default, pk) {actual['columns'][name]}, "
                f"expected {expected['columns'][name]}"
            )
    for kind, label in (("indexes", "index"), ("foreign_keys", "foreign key")):
        for item in sorted(expected[kind] - actual[kind], key=repr):
            errors.append(f"{table}: missing {label} {item}")
        for item in sorted(actual[kind] - expected[kind], key=repr):
            errors.append(f"{table}: unexpected {label} {item}")
    return errors


def _schema_state(query, names) -> tuple[str, list[str]]:
    tables = {row[0] for row in query("SELECT name FROM sqlite_master WHERE type = 'table'")}
    present = [name for name in names if name in tables]
    if not present:
        return ABSENT, []
    if len(present) < len(names):
        return PARTIAL, [f"{name} is missing" for name in names if name not in tables]
    expected = expected_schema()
    errors = [
        error
        for name in names
        for error in _differences(name, expected[name], describe(query, name))
    ]
    return (INCOMPATIBLE if errors else COMPLETE), errors


CPM_MISSING = ["card_bid_details.cpm is missing"]


def import_schema_state(query) -> tuple[str, list[str]]:
    """(ABSENT | PARTIAL | INCOMPATIBLE | COMPLETE, error messages) for the Part 22 tables."""
    return _schema_state(query, IMPORT_TABLE_NAMES)


def revert_schema_state(query) -> tuple[str, list[str]]:
    """The same for the Part 23 tables (import_batch_ledgers, import_batch_items)."""
    return _schema_state(query, REVERT_TABLE_NAMES)
