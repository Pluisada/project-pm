"""Read-only validation of a company database's schema (fail closed).

Contract, version COMPANY_SCHEMA_VERSION = 2 (CompanyBase.metadata: the commercial
tables plus the company-local `users` reference table, models.CompanyUser):
- The version marker is SQLite's PRAGMA user_version, which must equal 2 exactly.
  0 (never stamped), 1 (the old pipeline-core contract, which had no local users
  table), a negative or any other number is refused. Nothing upgrades a database
  between versions: an existing version-1 database needs a separately authorized
  migration, which does not exist yet.
- Every table in REQUIRED_COMPANY_SCHEMA must exist with at least the listed
  columns. Extra tables and columns are tolerated (later additive migrations).
- A stamped version with missing objects is inconsistent and refused; an
  unstamped database with the objects is refused too (never stamped here).
  Only create_company_db.py stamps, on a database it has just created.

The required set is an explicit list (the contract), not derived from the models;
test_company_schema.py guards it against drift from models.CompanyBase, so a model
change forces a conscious contract/version decision.

The validator takes only a company slug. The file comes from
database_targets.company_database_path (all protected-path and collision checks
apply, Salcomp is not exempt). It opens the file with SQLite mode=ro (never
creates), sets query_only, reads sqlite_master and PRAGMA user_version only, and
always closes the connection. It never stamps, repairs, migrates or initializes.
Errors are CompanySchemaError with messages free of paths and data. Not called
from main.py or any route.
"""
import os
import sqlite3
from urllib.parse import quote

from database_targets import company_database_path

COMPANY_SCHEMA_VERSION = 2

REQUIRED_COMPANY_SCHEMA: dict[str, frozenset[str]] = {
    "audit_events": frozenset({
        "id", "board_id", "card_id", "actor_user_id", "action", "details", "created_at"
    }),
    "boards": frozenset({
        "id", "user_id", "title", "description", "created_at", "updated_at"
    }),
    "card_actions": frozenset({
        "id", "board_id", "message_id", "action_type", "card_id", "changes", "created_at"
    }),
    "card_bid_details": frozenset({
        "id", "card_id", "plant", "customer_type", "business_type", "product_category", "product", "solution_type", "confidence", "volume_current_year", "volume_next_year", "production_start_date", "revenue_current_year_cents", "revenue_next_year_cents", "revenue_currency", "status_note", "project_lead", "cpm", "next_step", "next_step_due", "close_reason", "close_reason_note", "created_at", "updated_at"
    }),
    "card_edit_history": frozenset({
        "id", "card_id", "user_id", "changes", "created_at"
    }),
    "card_import_changes": frozenset({
        "id", "card_id", "import_batch_id", "user_id", "changes", "created_at"
    }),
    "card_source_refs": frozenset({
        "id", "card_id", "source_system", "source_key", "source_code", "source_status", "source_stage", "source_confidence", "source_created_at", "source_updated_at", "source_last_edited_at", "last_import_batch_id", "source_row_hash", "created_at", "updated_at"
    }),
    "card_stage_events": frozenset({
        "id", "card_id", "board_id", "event_type", "from_column_id", "from_stage_kind", "to_column_id", "to_stage_kind", "user_id", "source", "occurred_at", "deal_value_snapshot", "deal_currency_snapshot", "value_snapshot_taken"
    }),
    "cards": frozenset({
        "id", "column_id", "title", "details", "position", "company", "contact", "deal_value", "deal_value_next_year", "deal_year", "currency", "expected_close_date", "lead_source", "product_line", "salesperson_id", "created_at", "updated_at"
    }),
    "columns": frozenset({
        "id", "board_id", "title", "position", "stage_kind", "created_at", "updated_at"
    }),
    "conversation_messages": frozenset({
        "id", "board_id", "user_id", "role", "content", "meta_data", "created_at"
    }),
    "import_batch_items": frozenset({
        "id", "ledger_id", "card_id", "board_id", "source_key", "action", "observation", "before_json", "after_json", "bid_details_created", "stage_event_max_id_after"
    }),
    "import_batch_ledgers": frozenset({
        "id", "import_batch_id", "ledger_version", "created_at", "reverted_at", "reverted_by_user_id", "revert_outcome", "no_commercial_changes", "cards_deleted_count", "cards_restored_count", "moves_reverted_count", "already_deleted_count", "result_summary_json"
    }),
    "import_batches": frozenset({
        "id", "board_id", "user_id", "filename", "file_sha256", "mapping_json", "created_count", "updated_count", "skipped_count", "review_count", "status", "failure_kind", "failure_reason", "created_at", "updated_at", "completed_at"
    }),
    "undo_entries": frozenset({
        "id", "user_id", "board_id", "description", "steps", "created_at"
    }),
    "users": frozenset({
        "id", "username", "full_name", "created_at", "updated_at"
    }),
}


class CompanySchemaError(RuntimeError):
    """The company database is not a recognized, compatible company database."""


def validate_company_schema(slug: str, environ=None) -> None:
    """Raise unless the existing database of company `slug` carries the version-1 schema.

    DatabaseTargetError for an unknown, unconfigured or rejected company;
    CompanySchemaError for anything wrong with the file. Returns None on success.
    """
    path = company_database_path(slug, environ)
    if not os.path.isfile(path):
        raise CompanySchemaError(f"the database of company {slug!r} does not exist; it is never created here")
    uri = f"file:{quote(str(path), safe='/')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        raise CompanySchemaError(f"the database of company {slug!r} cannot be opened read-only") from None
    try:
        connection.execute("PRAGMA query_only=ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != COMPANY_SCHEMA_VERSION:
            raise CompanySchemaError(
                f"company {slug!r}: schema version {version} found, {COMPANY_SCHEMA_VERSION} expected; "
                "validation never stamps or migrates"
            )
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(set(REQUIRED_COMPANY_SCHEMA) - tables)
        if missing:
            raise CompanySchemaError(
                f"company {slug!r}: marked version {version} but required tables are missing: {', '.join(missing)}"
            )
        for table, required in REQUIRED_COMPANY_SCHEMA.items():
            columns = {r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')}
            absent = sorted(required - columns)
            if absent:
                raise CompanySchemaError(
                    f"company {slug!r}: table {table!r} is missing required columns: {', '.join(absent)}"
                )
    except sqlite3.Error:
        raise CompanySchemaError(f"the database of company {slug!r} is not a readable SQLite database") from None
    finally:
        connection.close()
