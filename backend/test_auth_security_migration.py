"""migrate_auth_security.py and the startup guard. Legacy databases are built in
tmp_path; backend/pm.db is never opened."""
import hashlib
import sqlite3

import pytest
from sqlalchemy import create_engine

import database
import migrate_auth_security as mig
from database import AuthSecurityMigrationRequired
from models import Base


def digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


@pytest.fixture
def legacy(tmp_path):
    """A database as it was before this change: no session_version / email_verified_at, no auth tables."""
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(path)
    conn.executescript(
        "DROP TABLE auth_tokens; DROP TABLE audit_events;"
        "DROP INDEX ix_users_created_at;"
        "ALTER TABLE users DROP COLUMN session_version; ALTER TABLE users DROP COLUMN email_verified_at;"
        "INSERT INTO users (username, password_hash, role, created_at, updated_at) VALUES ('old', 'h', 'admin', '2025-01-01 10:00:00', '2025-01-01 10:00:00');"
        "CREATE INDEX ix_users_created_at ON users (created_at);"
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture(autouse=True)
def point_engine(legacy, monkeypatch):
    engine = create_engine(f"sqlite:///{legacy}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "E2E_MODE", False)
    monkeypatch.delenv("PM_REQUIRE_EXISTING_DB", raising=False)
    yield
    engine.dispose()


def test_startup_refuses_a_legacy_database_without_touching_it(legacy):
    before = digest(legacy)
    with pytest.raises(AuthSecurityMigrationRequired, match="migrate_auth_security.py"):
        database.init_db()
    assert digest(legacy) == before


def test_dry_run_changes_nothing(legacy, capsys):
    before = digest(legacy)
    assert mig.main(["--database", str(legacy)]) == 0
    assert "Dry run" in capsys.readouterr().out and digest(legacy) == before


def test_apply_migrates_backs_up_and_marks_existing_users_verified(legacy, tmp_path):
    assert mig.main(["--database", str(legacy), "--apply"]) == 0
    assert len(list(tmp_path.glob("legacy-pre-auth-security-*.db.bak"))) == 1
    conn = sqlite3.connect(legacy)
    assert conn.execute("SELECT username, session_version, email_verified_at FROM users").fetchall() == [("old", 0, "2025-01-01 10:00:00")]
    assert conn.execute("SELECT COUNT(*) FROM auth_tokens").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone() == (0,)
    conn.close()
    database.init_db()  # the app now accepts it


def test_second_apply_is_refused(legacy, capsys):
    assert mig.main(["--database", str(legacy), "--apply"]) == 0
    after = digest(legacy)
    assert mig.main(["--database", str(legacy), "--apply"]) == 1
    assert digest(legacy) == after


def test_partly_migrated_database_is_refused(legacy):
    sqlite3.connect(legacy).execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0").connection.commit()
    before = digest(legacy)
    assert mig.main(["--database", str(legacy), "--apply"]) == 1
    assert digest(legacy) == before


def test_database_argument_is_required():
    with pytest.raises(SystemExit):
        mig.main([])
