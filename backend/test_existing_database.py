"""PM_REQUIRE_EXISTING_DB=1 (docker-compose sets it with PM_DB_HOST_PATH): the
app must refuse a missing, wrong or incomplete database instead of creating an
empty one. Every database here is a temp file; the development one is untouched."""
import os
import sqlite3
import subprocess
import sys

import pytest

import create_commercial_db as ccd
import database
from database import ExistingDatabaseRequired, check_existing_database

BACKEND = os.path.dirname(os.path.abspath(__file__))


def url(path):
    return f"sqlite:///{path}"


@pytest.fixture
def good_db(tmp_path):
    path = ccd.create_commercial_database(str(tmp_path / "commercial.db"), "boss@example.com")
    return path


def test_complete_database_passes(good_db):
    assert check_existing_database(url(good_db)) == good_db


def test_missing_file_is_refused_and_not_created(tmp_path):
    path = tmp_path / "typo.db"
    with pytest.raises(ExistingDatabaseRequired, match="not found"):
        check_existing_database(url(path))
    assert not path.exists()


def test_missing_directory_is_named(tmp_path):
    with pytest.raises(ExistingDatabaseRequired, match="directory .* does not exist"):
        check_existing_database(url(tmp_path / "nope" / "c.db"))


def test_directory_instead_of_file(tmp_path):
    # What docker creates when a missing host file is bind-mounted.
    with pytest.raises(ExistingDatabaseRequired, match="is a directory"):
        check_existing_database(url(tmp_path))


def test_empty_file_is_refused(tmp_path):
    path = tmp_path / "empty.db"
    path.touch()
    with pytest.raises(ExistingDatabaseRequired, match="no tables"):
        check_existing_database(url(path))
    assert path.stat().st_size == 0


def test_not_a_sqlite_file(tmp_path):
    path = tmp_path / "junk.db"
    path.write_bytes(b"this is not a database" * 100)
    with pytest.raises(ExistingDatabaseRequired, match="not a readable SQLite"):
        check_existing_database(url(path))


def test_missing_table_is_listed(good_db):
    with sqlite3.connect(good_db) as conn:
        conn.execute("DROP TABLE card_stage_events")
    with pytest.raises(ExistingDatabaseRequired, match="missing table card_stage_events"):
        check_existing_database(url(good_db))


def test_missing_column_is_listed(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE columns (id INTEGER PRIMARY KEY, title TEXT)")
    with pytest.raises(ExistingDatabaseRequired, match="table columns lacks columns .*stage_kind"):
        check_existing_database(url(path))


@pytest.mark.parametrize("bad", [None, "", "sqlite:///", "sqlite:///:memory:", "postgresql://u@h/db"])
def test_non_file_urls_are_refused(bad):
    with pytest.raises(ExistingDatabaseRequired, match="SQLite file URL"):
        check_existing_database(bad)


def test_check_does_not_modify_database(good_db):
    before = open(good_db, "rb").read()
    check_existing_database(url(good_db))
    assert open(good_db, "rb").read() == before


def test_init_db_enforces_only_when_flag_is_set(tmp_path, monkeypatch):
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(database, "DATABASE_URL", url(missing))
    monkeypatch.delenv("PM_REQUIRE_EXISTING_DB", raising=False)
    monkeypatch.setenv("PM_ALLOW_CREATE_DB", "1")
    # Flag off, creation authorised (development): init_db builds the database.
    monkeypatch.setattr(database, "engine", database.create_engine(url(missing)))
    database.init_db()
    assert missing.exists()
    missing.unlink()
    monkeypatch.setenv("PM_REQUIRE_EXISTING_DB", "1")
    with pytest.raises(ExistingDatabaseRequired):
        database.init_db()
    assert not missing.exists()


def _start_app(db_url):
    env = {**os.environ, "DATABASE_URL": db_url, "PM_REQUIRE_EXISTING_DB": "1"}
    script = "from fastapi.testclient import TestClient\nfrom main import app\nwith TestClient(app) as c:\n    print(c.get('/api/health').status_code)"
    return subprocess.run([sys.executable, "-I", "-c", script], env=env, capture_output=True, text=True, cwd=BACKEND)


def test_app_fails_to_start_on_wrong_path_and_starts_on_good_one(tmp_path, good_db):
    wrong = tmp_path / "wrong.db"
    result = _start_app(url(wrong))
    assert result.returncode != 0
    assert "ExistingDatabaseRequired" in result.stderr and "not found" in result.stderr
    assert not wrong.exists()

    ok = _start_app(url(good_db))
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout.strip().endswith("200")
