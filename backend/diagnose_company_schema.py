"""Read-only diagnostic of ONE existing company database: which required and additive schema elements are missing.

    PM_COMPANY_DB_LITY=sqlite:////abs/path/lity.db uv run python diagnose_company_schema.py --company lity

Exit status: 0 complete (every element present), 1 incomplete or refused, 2 not runnable (unknown/unconfigured/rejected
company, missing or unreadable file). Unlike company_schema.validate_company_schema, which stops at the first problem, it
collects everything and names the script that fixes each additive gap.

It opens the file with SQLite mode=ro (never creates), sets query_only, reads only sqlite_master, PRAGMA user_version,
PRAGMA table_info and PRAGMA index_list/index_info, and always closes the connection. It never runs, imports or calls
an init or migration script, never stamps and never writes. The target comes only from the company's own PM_COMPANY_DB_*
variable through database_targets.company_database_path (all protected-path and collision checks apply). The report names
tables, columns, indexes and versions only: never a path, a row or a value.

Two groups are checked:
- required: company_schema.REQUIRED_COMPANY_SCHEMA and COMPANY_SCHEMA_VERSION. A gap here is not fixed by an init script
  (a version below 4 needs migrate_confidence_level.py; version 1 has no migration; anything else is refused).
- additive: tables created by init_company_*.py and by create_company_db.py (products, card_excel_imports,
  card_loss_events, card_versions, card_review_obligations), with their columns and the unique guards the runtime relies
  on. An init script only creates a MISSING table: it never adds a column or index to an existing one, so such a gap is
  reported as "incompatible" and has no script.
"""
import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from urllib.parse import quote

from card_loss import CardLossEvent
from card_reviews import CardVersion, ReviewObligation
from company_schema import COMPANY_SCHEMA_VERSION, REQUIRED_COMPANY_SCHEMA
from database_targets import COMPANY_SLUGS, DatabaseTargetError, company_database_path
from excel_import_ledger import ExcelImport
from product_catalog import Product


@dataclass(frozen=True)
class Additive:
    table: str
    columns: frozenset[str]
    unique_guards: tuple[tuple[str, ...], ...]  # column lists that must be covered by a unique index/constraint
    script: str


def _additive(model, guards, script) -> Additive:
    return Additive(model.__tablename__, frozenset(c.name for c in model.__table__.columns), guards, script)


ADDITIVE: tuple[Additive, ...] = (
    _additive(Product, (), "init_company_products.py"),
    _additive(ExcelImport, (("board_id", "repeat_guard"),), "init_company_excel_imports.py"),
    _additive(CardLossEvent, (), "init_company_card_loss.py"),
    _additive(CardVersion, (), "init_company_card_reviews.py"),
    _additive(ReviewObligation, (("card_id", "reviewer_role"),), "init_company_card_reviews.py"),
)


class DiagnosticError(RuntimeError):
    """The database cannot be inspected; the message is safe to show."""


@dataclass
class Diagnosis:
    version: int | None = None
    problems: list[str] = field(default_factory=list)   # required-schema gaps and refusals: no init script fixes these
    missing: list[Additive] = field(default_factory=list)       # additive table absent: the script creates it
    incompatible: list[str] = field(default_factory=list)       # additive table present but wrong: no script repairs it

    @property
    def complete(self) -> bool:
        return not (self.problems or self.missing or self.incompatible)

    def scripts(self) -> list[str]:
        return sorted({a.script for a in self.missing})


def _unique_column_sets(connection, table: str) -> list[tuple[str, ...]]:
    sets = []
    for _seq, name, unique, *_ in connection.execute(f'PRAGMA index_list("{table}")'):
        if unique:
            sets.append(tuple(r[2] for r in connection.execute(f'PRAGMA index_info("{name}")')))
    return sets


def diagnose_connection(connection: sqlite3.Connection) -> Diagnosis:
    """Inspect an open connection (read-only queries only)."""
    result = Diagnosis()
    result.version = connection.execute("PRAGMA user_version").fetchone()[0]
    if result.version != COMPANY_SCHEMA_VERSION:
        result.problems.append(
            f"schema version {result.version} found, {COMPANY_SCHEMA_VERSION} expected"
            + (" (migrate_confidence_level.py upgrades 2 or 3 to 4)" if result.version in (2, 3) else "")
        )
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in sorted(REQUIRED_COMPANY_SCHEMA):
        if table not in tables:
            result.problems.append(f"required table {table} is missing")
            continue
        columns = {r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')}
        absent = sorted(REQUIRED_COMPANY_SCHEMA[table] - columns)
        if absent:
            result.problems.append(f"required table {table} is missing columns: {', '.join(absent)}")
    for item in ADDITIVE:
        if item.table not in tables:
            result.missing.append(item)
            continue
        columns = {r[1] for r in connection.execute(f'PRAGMA table_info("{item.table}")')}
        absent = sorted(item.columns - columns)
        if absent:
            result.incompatible.append(f"table {item.table} is missing columns: {', '.join(absent)}")
        unique = _unique_column_sets(connection, item.table)
        for guard in item.unique_guards:
            if guard not in unique:
                result.incompatible.append(f"table {item.table} lacks the unique guard on ({', '.join(guard)})")
    return result


def diagnose_company(slug: str, environ=None) -> Diagnosis:
    """DatabaseTargetError for an unknown, unconfigured or rejected company; DiagnosticError for the file."""
    path = company_database_path(slug, environ)
    if not os.path.isfile(path):
        raise DiagnosticError(f"the database of company {slug!r} does not exist; it is never created here")
    try:
        connection = sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode=ro", uri=True)
    except sqlite3.Error:
        raise DiagnosticError(f"the database of company {slug!r} cannot be opened read-only") from None
    try:
        connection.execute("PRAGMA query_only=ON")
        return diagnose_connection(connection)
    except sqlite3.Error:
        raise DiagnosticError(f"the database of company {slug!r} is not a readable SQLite database") from None
    finally:
        connection.close()


def render(slug: str, result: Diagnosis) -> str:
    lines = [f"Company {slug!r}: schema version {result.version} (expected {COMPANY_SCHEMA_VERSION})."]
    for problem in result.problems:
        lines.append(f"  PROBLEM (no init script fixes this): {problem}")
    for item in result.missing:
        lines.append(f"  MISSING table {item.table}: run {item.script}")
    for text in result.incompatible:
        lines.append(f"  INCOMPATIBLE (no script repairs it; separately authorized, backed-up task): {text}")
    lines.append("Schema complete." if result.complete else "Schema INCOMPLETE.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only schema diagnostic of one company database.")
    parser.add_argument("--company", required=True, choices=COMPANY_SLUGS, help="company slug")
    args = parser.parse_args(argv)
    try:
        result = diagnose_company(args.company)
    except (DatabaseTargetError, DiagnosticError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(render(args.company, result))
    return 0 if result.complete else 1


if __name__ == "__main__":
    sys.exit(main())
