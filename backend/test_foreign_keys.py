"""SQLite foreign keys are enforced on every application connection.

SQLite ignores declared foreign keys unless PRAGMA foreign_keys=ON is set on
each connection, so these tests check the setting where the app gets its
connections (database.engine, create_commercial_db) and that invalid references
are really rejected. Every database here is a temporary file.
"""
import pathlib
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import create_commercial_db as ccd
import database
from auth import create_user_access_token, hash_password
from auth_test_utils import VERIFIED
from database import enable_sqlite_foreign_keys, get_db
from main import app
from models import Base, Board, Card, Column, User, UserRole


def fk_pragma(conn):
    return conn.execute(text("PRAGMA foreign_keys")).scalar()


@pytest.fixture
def fk_engine(tmp_path):
    engine = enable_sqlite_foreign_keys(create_engine(f"sqlite:///{tmp_path / 'fk.db'}"))
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_negative_control_plain_engine_does_not_enforce(tmp_path):
    """Without the helper SQLite leaves foreign keys off: the problem being fixed."""
    engine = create_engine(f"sqlite:///{tmp_path / 'plain.db'}")
    with engine.connect() as conn:
        assert fk_pragma(conn) == 0
    engine.dispose()


def test_application_engine_enables_foreign_keys():
    """The module-level engine (pointed at a temp file by conftest) enforces them."""
    assert "pm-tests-" in str(database.engine.url)
    with database.engine.connect() as conn:
        assert fk_pragma(conn) == 1


def test_every_pooled_and_new_connection_enforces(fk_engine):
    first, second = fk_engine.connect(), fk_engine.connect()  # two distinct pooled connections
    try:
        assert first.connection.dbapi_connection is not second.connection.dbapi_connection
        assert fk_pragma(first) == fk_pragma(second) == 1
    finally:
        first.close()
        second.close()
    with fk_engine.connect() as reused:  # returned to the pool and checked out again
        assert fk_pragma(reused) == 1
    fk_engine.dispose()
    with fk_engine.connect() as fresh:  # brand-new connection after the pool is discarded
        assert fk_pragma(fresh) == 1


def test_invalid_reference_is_rejected_and_valid_accepted(fk_engine):
    Session = sessionmaker(bind=fk_engine)
    with Session() as db:
        db.add(Card(column_id=9999, title="orphan", details="", position=0))
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            db.commit()
    with Session() as db:
        user = User(username="u", password_hash="x", email_verified_at=VERIFIED, role=UserRole.ADMIN.value)
        db.add(user)
        db.flush()
        board = Board(user_id=user.id, title="b")
        db.add(board)
        db.flush()
        col = Column(board_id=board.id, title="c", position=0)
        db.add(col)
        db.flush()
        db.add(Card(column_id=col.id, title="ok", details="", position=0))
        db.commit()
        assert db.query(Card).count() == 1  # only the valid card exists
        assert db.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def test_commercial_database_creation_enforces_foreign_keys(tmp_path, monkeypatch):
    engines = []

    def recording_create_engine(*args, **kwargs):
        engines.append(create_engine(*args, **kwargs))
        return engines[-1]

    monkeypatch.setattr(ccd, "create_engine", recording_create_engine)
    path = ccd.create_commercial_database(str(tmp_path / "new.db"), "admin@example.com")
    assert len(engines) == 1
    with engines[0].connect() as conn:  # a new connection on the engine that built the database
        assert fk_pragma(conn) == 1
        assert conn.execute(text("PRAGMA foreign_key_check")).fetchall() == []
    engines[0].dispose()
    assert pathlib.Path(path).exists()


def test_no_application_module_builds_an_unprotected_sqlite_engine():
    """Every non-test module that calls create_engine must enable foreign keys.

    That means the shared helper, or its own "connect" listener that runs
    PRAGMA foreign_keys=ON (commercial_import._engine needs mode=rw, so it builds
    its own; test_commercial_import_engine_enforces_foreign_keys proves it).
    """
    offenders = []
    for source in pathlib.Path(__file__).parent.glob("*.py"):
        if source.name.startswith("test_") or source.name == "conftest.py":
            continue
        code = source.read_text()
        protected = "enable_sqlite_foreign_keys(" in code or (
            'listens_for(engine, "connect")' in code and "PRAGMA foreign_keys=ON" in code
        )
        if re.search(r"\bcreate_engine\(", code) and not protected:
            offenders.append(source.name)
    assert offenders == []


def test_commercial_import_engine_enforces_foreign_keys(tmp_path):
    """The engine the commercial import really uses rejects orphans and cascades deletes."""
    import sqlite3

    import commercial_import

    path = tmp_path / "import-fk.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
        "CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL REFERENCES parent(id) ON DELETE CASCADE);"
    )
    raw.close()
    engine = commercial_import._engine(str(path))
    try:
        first, second = engine.connect(), engine.connect()  # two distinct pooled connections
        try:
            assert first.connection.dbapi_connection is not second.connection.dbapi_connection
            assert fk_pragma(first) == fk_pragma(second) == 1
        finally:
            first.close()
            second.close()
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO parent (id) VALUES (1)"))
            conn.execute(text("INSERT INTO child (id, parent_id) VALUES (1, 1)"))  # valid reference
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO child (id, parent_id) VALUES (2, 999)"))  # orphan
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM parent WHERE id = 1"))
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM child")).scalar() == 0  # cascade ran
    finally:
        engine.dispose()


@pytest.fixture
def fk_client(fk_engine):
    """The real routes against a database that enforces foreign keys."""
    Session = sessionmaker(bind=fk_engine)
    db = Session()
    admin = User(username="admin", password_hash=hash_password("Adminpass123!"), email_verified_at=VERIFIED, role=UserRole.ADMIN.value)
    db.add(admin)
    db.commit()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {create_user_access_token(admin)}"})
    yield client, db, admin
    app.dependency_overrides.clear()
    db.close()


def test_supported_operations_work_with_foreign_keys_on(fk_client):
    client, db, admin = fk_client
    assert fk_pragma(db.connection()) == 1
    board = client.post("/api/boards", json={"title": "Pipeline"}).json()
    cols = [
        client.post(f"/api/boards/{board['id']}/columns", json={"title": t, "position": i}).json()
        for i, t in enumerate(("One", "Two"))
    ]
    card = client.post(
        f"/api/boards/{board['id']}/cards",
        json={"column_id": cols[0]["id"], "title": "Lead", "details": "", "position": 0},
    ).json()
    assert client.put(f"/api/boards/{board['id']}/cards/{card['id']}", json={"title": "Lead 2"}).status_code == 200
    moved = client.put(
        f"/api/boards/{board['id']}/cards/{card['id']}/position", json={"column_id": cols[1]["id"], "position": 0}
    )
    assert moved.status_code == 200
    assert client.delete(f"/api/boards/{board['id']}/cards/{card['id']}").status_code == 204
    assert client.delete(f"/api/boards/{board['id']}/columns/{cols[1]['id']}").status_code == 204
    assert client.delete(f"/api/boards/{board['id']}").status_code == 204
    assert db.execute(text("PRAGMA foreign_key_check")).fetchall() == []
