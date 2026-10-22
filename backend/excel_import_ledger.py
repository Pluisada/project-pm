"""Ledger of the applied Cards-workbook imports of one company database (`card_excel_imports`).

Like the product catalog, the table lives on its own declarative base: it is not part of the required company schema
contract (company_schema.REQUIRED_COMPANY_SCHEMA), so company databases created before it keep validating and no schema
version moves. A database without the table is "not initialized": the Cards import is refused (it could not be made
safe against a repeated request) until init_company_excel_imports.py has created it. New company databases get it from
create_company_db.py. It is a separate ledger from the Bid import's `import_batches` (a different format, a different
revert story); nothing here is shared with it.

One row per applied import (never for a preview, a refusal or a rollback; it is written in the same transaction as the
card changes, so it exists if and only if the changes do):
- `fingerprint`: SHA-256 of the normalized, validated workbook content scoped to company + board (see
  cards_excel_import.fingerprint). Informational for every row.
- `repeat_guard`: the same value for every import (NULL only in rows written by older versions).
  UNIQUE(board_id, repeat_guard) is the database-level backstop against applying one logical workbook twice (two
  concurrent confirmations, a retry after an uncertain outcome, or re-importing an older workbook after a later one).
  The ledger is permanent: deleting cards does not release a workbook, and nothing here purges it.
- `changes_json`: ids of the created cards and the old/new values of every updated field, for diagnosis and manual
  recovery. board_id and user_id are plain integers (a separate metadata cannot hold foreign keys, and the row must
  outlive both).
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint, inspect, select
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session

ExcelImportBase = declarative_base()


class ExcelImport(ExcelImportBase):
    __tablename__ = "card_excel_imports"
    __table_args__ = (UniqueConstraint("board_id", "repeat_guard", name="uq_card_excel_imports_board_guard"),)

    id = Column(Integer, primary_key=True)
    board_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=True)
    filename = Column(String(255), nullable=False)
    file_sha256 = Column(String(64), nullable=False)
    fingerprint = Column(String(64), nullable=False, index=True)
    repeat_guard = Column(String(64), nullable=True)
    created_count = Column(Integer, nullable=False)
    updated_count = Column(Integer, nullable=False)
    unchanged_count = Column(Integer, nullable=False)
    changes_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)


def has_ledger(db: Session) -> bool:
    return inspect(db.connection()).has_table(ExcelImport.__tablename__)  # the session's own connection


def ledger_compatible(db: Session) -> bool:
    """True when the existing table has every column of the current model and the unique (board_id, repeat_guard)
    guard. Read-only (reflection of the session's own connection). A table written by an older build may lack them;
    the import is then refused (fail closed) instead of failing half way. Never alters or rewrites the table."""
    inspector = inspect(db.connection())
    table = ExcelImport.__tablename__
    have = {column["name"] for column in inspector.get_columns(table)}
    if not {column.name for column in ExcelImport.__table__.columns} <= have:
        return False
    guards = [c["column_names"] for c in inspector.get_unique_constraints(table)]
    guards += [i["column_names"] for i in inspector.get_indexes(table) if i.get("unique")]
    return ["board_id", "repeat_guard"] in guards


def create_ledger(bind) -> None:
    """Create the table if it is missing (idempotent). `bind` is an engine or connection."""
    ExcelImportBase.metadata.create_all(bind)


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def summary(entry: ExcelImport) -> dict:
    """What a caller may be told about an applied import: counts and time, never content."""
    return {
        "import_id": entry.id,
        "created": entry.created_count,
        "updated": entry.updated_count,
        "unchanged": entry.unchanged_count,
        "applied_at": entry.created_at.isoformat(),
    }


def find_by_fingerprint(db: Session, board_id: int, fingerprint: str) -> ExcelImport | None:
    """The latest applied import of this board with this fingerprint."""
    return db.scalars(
        select(ExcelImport)
        .where(ExcelImport.board_id == board_id, ExcelImport.fingerprint == fingerprint)
        .order_by(ExcelImport.id.desc())
    ).first()


def find_guard(db: Session, board_id: int, fingerprint: str) -> ExcelImport | None:
    """The applied import that already created the cards of this workbook on this board, if any."""
    return db.scalars(
        select(ExcelImport).where(ExcelImport.board_id == board_id, ExcelImport.repeat_guard == fingerprint)
    ).first()


def all_by_fingerprint(db: Session, board_id: int, fingerprint: str) -> list[ExcelImport]:
    """Every applied import of this workbook content on this board (ordinary and recovery), oldest first. Read-only."""
    return list(db.scalars(
        select(ExcelImport).where(ExcelImport.board_id == board_id, ExcelImport.fingerprint == fingerprint).order_by(ExcelImport.id)
    ))
