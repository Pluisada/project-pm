"""Phase 1.5: table ownership between identity and company metadata.

Pure metadata/mapper inspection plus disposable tmp_path SQLite files: no real
database is opened or created.
"""
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import configure_mappers, sessionmaker

from company_schema import REQUIRED_COMPANY_SCHEMA
from identity_models import IdentityBase
from model_ownership import (
    COMMERCIAL_TABLES, COMPANY_TABLES, LEGACY_ONLY_TABLES, LOCAL_USER_TABLE, USER_REFERENCE_FOREIGN_KEYS,
)
from models import Base, Board, BoardColumn, Card, CompanyBase, CompanyUser, ConversationMessage, ImportBatch, User

BACKEND = Path(__file__).parent


def test_company_metadata_holds_exactly_the_company_tables():
    assert set(CompanyBase.metadata.tables) == COMPANY_TABLES
    assert LOCAL_USER_TABLE in COMPANY_TABLES and LOCAL_USER_TABLE not in COMMERCIAL_TABLES


def test_identity_metadata_holds_only_central_identity_tables():
    identity = set(IdentityBase.metadata.tables)
    assert identity and all(name.startswith("identity_") for name in identity)
    for table in IdentityBase.metadata.tables.values():
        for fk in table.foreign_keys:
            assert fk.column.table.name in identity


def test_company_and_identity_metadata_are_disjoint():
    assert CompanyBase.metadata is not IdentityBase.metadata
    assert set(CompanyBase.metadata.tables).isdisjoint(IdentityBase.metadata.tables)


def test_no_company_foreign_key_leaves_the_company_metadata():
    for table in CompanyBase.metadata.tables.values():
        for fk in table.foreign_keys:
            assert fk.column.table.metadata is CompanyBase.metadata
            assert not fk.column.table.name.startswith("identity_")


def test_local_user_reference_carries_no_authentication_or_authorization_state():
    columns = set(CompanyBase.metadata.tables["users"].columns.keys())
    assert columns == {"id", "username", "full_name", "created_at", "updated_at"}
    assert not columns & {"password_hash", "role", "email", "session_version", "email_verified_at", "is_active"}


def test_local_user_reference_takes_the_canonical_central_id():
    id_column = CompanyBase.metadata.tables["users"].c.id
    assert id_column.primary_key and id_column.autoincrement is False


def test_the_nine_user_foreign_keys_keep_their_semantics():
    found = {}
    for table in CompanyBase.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name == LOCAL_USER_TABLE:
                found[(table.name, fk.parent.name)] = (fk.parent.nullable, fk.ondelete)
    assert len(USER_REFERENCE_FOREIGN_KEYS) == 9
    assert found == {key: (ref.nullable, ref.ondelete) for key, ref in USER_REFERENCE_FOREIGN_KEYS.items()}


def test_audit_events_has_no_foreign_keys():
    assert not CompanyBase.metadata.tables["audit_events"].foreign_keys


def test_existing_table_names_and_key_columns_are_stable():
    tables = CompanyBase.metadata.tables
    assert set(tables["cards"].columns.keys()) >= {"id", "column_id", "salesperson_id", "deal_value", "deal_year"}
    assert set(tables["boards"].columns.keys()) >= {"id", "user_id", "title"}
    assert set(tables["card_stage_events"].columns.keys()) >= {"card_id", "board_id", "event_type", "user_id"}
    assert set(tables["audit_events"].columns.keys()) >= {"board_id", "card_id", "actor_user_id", "action"}


def test_orm_relationships_resolve_inside_the_company_registry():
    configure_mappers()
    for mapper in CompanyBase.registry.mappers:
        for rel in mapper.relationships:
            assert rel.mapper.class_.registry is CompanyBase.registry
    assert Card.salesperson.property.mapper.class_ is CompanyUser
    assert Board.user.property.mapper.class_ is CompanyUser
    assert ConversationMessage.user.property.mapper.class_ is CompanyUser
    assert ImportBatch.user.property.mapper.class_ is CompanyUser
    assert {r.key for r in CompanyUser.__mapper__.relationships} == {"boards", "messages", "import_batches"}


def test_legacy_base_keeps_the_single_database_schema():
    # Transitional: the legacy runtime still builds everything from Base.metadata.
    assert set(Base.metadata.tables) == COMPANY_TABLES | LEGACY_ONLY_TABLES
    assert {t.name for t in Base.metadata.sorted_tables if t.name == "users"} == {"users"}
    assert "password_hash" in Base.metadata.tables["users"].columns
    for name in COMMERCIAL_TABLES:  # copies, same shape
        assert Base.metadata.tables[name] is not CompanyBase.metadata.tables[name]
        assert Base.metadata.tables[name].columns.keys() == CompanyBase.metadata.tables[name].columns.keys()
    assert set(User.__mapper__.relationships.keys()) == set()


def _engine(path):
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def test_company_schema_alone_builds_on_a_disposable_database(tmp_path):
    engine = _engine(tmp_path / "company.db")
    CompanyBase.metadata.create_all(engine)
    assert set(inspect(engine).get_table_names()) == COMPANY_TABLES
    assert "password_hash" not in {c["name"] for c in inspect(engine).get_columns("users")}
    with sessionmaker(bind=engine)() as db:
        db.add(CompanyUser(id=41, username="Ana"))  # canonical central id, not generated
        db.commit()
        db.add(Board(user_id=41, title="Pipeline"))
        db.commit()
        assert db.get(CompanyUser, 41).boards[0].title == "Pipeline"
        with pytest.raises(IntegrityError):  # a missing local row is refused by the real foreign key
            db.add(Board(user_id=999, title="orphan"))
            db.commit()
        db.rollback()
        with pytest.raises(SQLAlchemyError):  # ids are never generated here
            db.add(CompanyUser(username="No id"))
            db.commit()
    engine.dispose()


def test_salesperson_name_lookup_and_set_null_on_delete(tmp_path):
    engine = _engine(tmp_path / "company.db")
    CompanyBase.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add_all([CompanyUser(id=7, username="Bruno"), CompanyUser(id=8, username="Carla")])
        db.flush()
        board = Board(user_id=8, title="P")
        column = BoardColumn(board=board, title="1st Contact", position=0, stage_kind="open")
        card = Card(column=column, title="Lead", position=0, salesperson_id=7)
        db.add(card)
        db.commit()
        assert card.salesperson_name == "Bruno"
        db.delete(db.get(CompanyUser, 7))
        db.commit()
        db.refresh(card)
        assert card.salesperson_id is None and card.salesperson_name is None
    engine.dispose()


def test_company_schema_core_is_company_owned_and_in_company_metadata():
    assert set(REQUIRED_COMPANY_SCHEMA) == COMPANY_TABLES
    for table, cols in REQUIRED_COMPANY_SCHEMA.items():
        assert cols <= set(CompanyBase.metadata.tables[table].columns.keys())


def test_importing_models_has_no_side_effects(tmp_path):
    code = (
        f"import sys; sys.path.insert(0, {str(BACKEND)!r})\n"
        "import sqlite3\n"
        "def boom(*a, **k): raise AssertionError('connect')\n"
        "sqlite3.connect = boom\n"
        "import models, identity_models, model_ownership, company_schema\n"
        "assert 'database' not in sys.modules\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
