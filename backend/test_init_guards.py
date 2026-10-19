"""init_db(): every refusal happens before the first write.

Missing, zero-byte and table-less database files are refused unless creation
is authorised (PM_ALLOW_CREATE_DB=1 or the isolated PM_E2E=1);
PM_REQUIRE_EXISTING_DB=1 always wins. The five migration guards and the
schema-completeness check run before create_all, and a refused database is
byte-identical afterwards. Every database here is a temp file; backend/pm.db
is only ever hashed.
"""
import hashlib
import os
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine

import create_commercial_db as ccd
import database
from database import (
    DEVELOPMENT_DATABASES,
    DatabaseCreationNotAllowed,
    DatabaseSchemaIncomplete,
    E2EDatabaseError,
    ExistingDatabaseRequired,
    ImportRevertMigrationRequired,
    ImportTablesMigrationRequired,
    StageEventsMigrationRequired,
    StageEventValuesMigrationRequired,
    StageKindMigrationRequired,
)
from import_schema import IMPORT_TABLE_NAMES, REVERT_TABLE_NAMES
from models import Base

BACKEND = os.path.dirname(os.path.abspath(__file__))


def url(path):
    return f"sqlite:///{path}"


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def snapshot(path):
    """File hash plus schema and data, read through a read-only connection."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        schema = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
        data = {name: conn.execute(f'SELECT * FROM "{name}"').fetchall() for (t, name, _) in schema if t == "table"}
    return digest(path), schema, data


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """init_db() pointed at tmp_path/target.db with no creation permission, and
    create_all / ALTER TABLE rigged to fail if a refusal ever reaches them."""
    for name in ("PM_ALLOW_CREATE_DB", "PM_REQUIRE_EXISTING_DB", "PM_E2E"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(database, "E2E_MODE", False)
    target = tmp_path / "target.db"
    monkeypatch.setattr(database, "DATABASE_URL", url(target))
    engine = create_engine(url(target))
    monkeypatch.setattr(database, "engine", engine)
    yield target
    engine.dispose()


@pytest.fixture
def no_writes(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("a refused database reached create_all / ALTER TABLE")

    monkeypatch.setattr(Base.metadata, "create_all", boom)
    monkeypatch.setattr(database, "_add_missing_card_columns", boom, raising=False)


@pytest.fixture
def good_db(tmp_path):
    return ccd.create_commercial_database(str(tmp_path / "good.db"), "boss@example.com")


def tables(path):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return {n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def copy_of(good_db, tmp_path, name="broken.db"):
    target = tmp_path / name
    with sqlite3.connect(good_db) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    return str(target)


def sql(path, *statements):
    conn = sqlite3.connect(path)
    for s in statements:
        conn.execute(s)
    conn.commit()
    conn.close()


# --- nothing there yet ------------------------------------------------------


def test_missing_file_is_refused_and_not_created(isolated, tmp_path, no_writes):
    before = sorted(os.listdir(tmp_path))
    with pytest.raises(DatabaseCreationNotAllowed, match="does not exist"):
        database.init_db()
    assert not isolated.exists() and sorted(os.listdir(tmp_path)) == before


def test_zero_byte_file_is_refused_unchanged(isolated, tmp_path, no_writes):
    isolated.write_bytes(b"")
    before = digest(isolated)
    with pytest.raises(DatabaseCreationNotAllowed, match="zero-byte"):
        database.init_db()
    assert digest(isolated) == before and isolated.stat().st_size == 0
    assert sorted(os.listdir(tmp_path)) == ["target.db"]


def test_sqlite_without_tables_is_refused_unchanged(isolated, tmp_path, no_writes):
    sql(str(isolated), "PRAGMA user_version = 7")  # a real SQLite file, no tables
    assert isolated.stat().st_size > 0 and not tables(isolated)
    before = snapshot(str(isolated))
    with pytest.raises(DatabaseCreationNotAllowed, match="no tables"):
        database.init_db()
    assert snapshot(str(isolated)) == before
    assert sorted(os.listdir(tmp_path)) == ["target.db"]


def test_non_sqlite_file_is_refused_unchanged(isolated, no_writes):
    isolated.write_bytes(b"this is not a database, just text" * 10)
    before = digest(isolated)
    with pytest.raises(DatabaseCreationNotAllowed, match="not a readable SQLite"):
        database.init_db()
    assert digest(isolated) == before


def test_directory_is_refused(isolated, no_writes):
    isolated.mkdir()
    with pytest.raises(DatabaseCreationNotAllowed, match="directory"):
        database.init_db()


# --- the five guards and the schema check, all before any write -------------

BROKEN = [
    ("card_stage_events table", ["DROP TABLE card_stage_events"], StageEventsMigrationRequired),
    ("import tables", [f"DROP TABLE {t}" for t in (*REVERT_TABLE_NAMES, *IMPORT_TABLE_NAMES)], ImportTablesMigrationRequired),
    ("import revert tables", [f"DROP TABLE {t}" for t in REVERT_TABLE_NAMES], ImportRevertMigrationRequired),
    ("columns.stage_kind", ["ALTER TABLE columns DROP COLUMN stage_kind"], StageKindMigrationRequired),
    ("stage event value columns", ["ALTER TABLE card_stage_events DROP COLUMN deal_value_snapshot"], StageEventValuesMigrationRequired),
    ("a cards lead column", ["ALTER TABLE cards DROP COLUMN company"], DatabaseSchemaIncomplete),
    ("a whole unrelated table", ["DROP TABLE card_actions"], DatabaseSchemaIncomplete),
]


@pytest.mark.parametrize("what, statements, error", BROKEN, ids=[b[0] for b in BROKEN])
def test_guard_refuses_before_any_write(what, statements, error, good_db, tmp_path, isolated, monkeypatch, no_writes):
    broken = copy_of(good_db, tmp_path)
    sql(broken, *statements)
    monkeypatch.setattr(database, "DATABASE_URL", url(broken))
    monkeypatch.setattr(database, "engine", create_engine(url(broken)))
    # Authorising creation must not matter for a database that exists.
    monkeypatch.setenv("PM_ALLOW_CREATE_DB", "1")
    before = snapshot(broken)
    with pytest.raises(error):
        database.init_db()
    assert snapshot(broken) == before
    assert not [f for f in os.listdir(tmp_path) if f.endswith(("-journal", "-wal", "-shm"))]


def test_old_cards_table_is_not_altered_any_more(good_db, tmp_path, monkeypatch, no_writes):
    broken = copy_of(good_db, tmp_path)
    sql(broken, "ALTER TABLE cards DROP COLUMN product_line")
    monkeypatch.setattr(database, "DATABASE_URL", url(broken))
    monkeypatch.setattr(database, "engine", create_engine(url(broken)))
    with pytest.raises(DatabaseSchemaIncomplete, match="cards lacks columns product_line"):
        database.init_db()
    with sqlite3.connect(broken) as conn:
        assert "product_line" not in {r[0] for r in conn.execute("SELECT name FROM pragma_table_info('cards')")}


# --- valid and authorised paths ---------------------------------------------


def test_valid_database_starts_without_a_single_write(good_db, isolated, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_URL", url(good_db))
    monkeypatch.setattr(database, "engine", create_engine(url(good_db)))
    before = snapshot(good_db)
    database.init_db()
    assert snapshot(good_db) == before


def expected_tables():
    return {t.name for t in Base.metadata.sorted_tables}


@pytest.mark.parametrize("state", ["missing", "zero-byte", "no-tables"])
def test_explicit_authorisation_creates_the_schema(state, isolated, monkeypatch):
    if state == "zero-byte":
        isolated.write_bytes(b"")
    elif state == "no-tables":
        sql(str(isolated), "PRAGMA user_version = 7")
    monkeypatch.setenv("PM_ALLOW_CREATE_DB", "1")
    database.init_db()
    assert tables(isolated) >= expected_tables()
    database.init_db()  # and the result is a database the guards accept


@pytest.mark.parametrize("state", ["missing", "zero-byte", "no-tables"])
def test_require_existing_prevails_over_allow_create(state, isolated, tmp_path, monkeypatch, no_writes):
    if state == "zero-byte":
        isolated.write_bytes(b"")
    elif state == "no-tables":
        sql(str(isolated), "PRAGMA user_version = 7")
    monkeypatch.setenv("PM_ALLOW_CREATE_DB", "1")
    monkeypatch.setenv("PM_REQUIRE_EXISTING_DB", "1")
    monkeypatch.setattr(database, "E2E_MODE", True)  # not even E2E overrides it
    existed = isolated.exists()
    before = digest(isolated) if existed else None
    with pytest.raises(ExistingDatabaseRequired):
        database.init_db()
    assert isolated.exists() == existed
    if existed:
        assert digest(isolated) == before


# --- PM_E2E=1 ---------------------------------------------------------------


def test_e2e_mode_may_create_its_temp_database(isolated, monkeypatch):
    monkeypatch.setattr(database, "E2E_MODE", True)
    database.init_db()
    assert tables(isolated) >= expected_tables()


@pytest.mark.parametrize("dev_db", DEVELOPMENT_DATABASES)
def test_e2e_mode_cannot_reach_the_development_database(dev_db, monkeypatch, no_writes):
    existed = os.path.exists(dev_db)
    before = digest(dev_db) if existed else None
    monkeypatch.setattr(database, "E2E_MODE", True)
    monkeypatch.setattr(database, "DATABASE_URL", url(dev_db))
    monkeypatch.setattr(database, "engine", create_engine(url(dev_db)))  # lazy: no connection is made
    with pytest.raises(E2EDatabaseError, match="development database"):
        database.init_db()
    assert os.path.exists(dev_db) == existed
    if existed:
        assert digest(dev_db) == before


def _start(tmp_path, extra_env, db_name="e2e.db"):
    env = {k: v for k, v in os.environ.items() if k not in ("PM_ALLOW_CREATE_DB", "PM_REQUIRE_EXISTING_DB", "PM_E2E")}
    env.update(extra_env, DATABASE_URL=url(tmp_path / db_name))
    script = (
        "from fastapi.testclient import TestClient\nfrom main import app\n"
        "with TestClient(app) as c:\n    print(c.get('/api/health').json())"
    )
    return subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, cwd=BACKEND, timeout=120)


def test_app_in_e2e_mode_starts_on_a_new_temp_database_without_other_flags(tmp_path):
    result = _start(tmp_path, {"PM_E2E": "1"})
    assert result.returncode == 0, result.stderr
    assert "'test_mode': True" in result.stdout
    assert tables(tmp_path / "e2e.db") >= expected_tables()


def test_app_by_default_refuses_to_create_a_database(tmp_path):
    result = _start(tmp_path, {})
    assert result.returncode != 0
    assert "DatabaseCreationNotAllowed" in result.stderr
    assert not (tmp_path / "e2e.db").exists()


def test_app_starts_when_creation_is_explicitly_allowed(tmp_path):
    result = _start(tmp_path, {"PM_ALLOW_CREATE_DB": "1"})
    assert result.returncode == 0, result.stderr
    assert "'test_mode': False" in result.stdout
