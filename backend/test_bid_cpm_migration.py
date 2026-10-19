"""card_bid_details.cpm: model, migrate_bid_cpm.py and the init_db() guard.
Every database here is a temp file under tmp_path; no real database is opened."""
import hashlib
import sqlite3

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import create_commercial_db as ccd
import database
import migrate_bid_cpm as migration
from database import BidCpmMigrationRequired, ImportTablesSchemaError
from import_schema import COMPLETE, CPM_MISSING, import_schema_state
from models import Base, Board, Card, CardBidDetails, Column, User, UserRole


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def apply_cli(path, *extra):
    """--apply is only accepted with the approved file's hash; pin the file as it is now."""
    return migration.main(["--database", str(path), "--apply", "--expect-sha256", _sha(path), *extra])


def _query(path, sql):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _columns(path):
    return [row[1] for row in _query(path, "PRAGMA table_info(card_bid_details)")]


def _state(path):
    conn = sqlite3.connect(path)
    try:
        return import_schema_state(lambda sql: conn.execute(sql).fetchall())
    finally:
        conn.close()


def _dump_without_cpm(path):
    """Every row of every table by the pre-migration columns, plus the schema of every other object."""
    conn = sqlite3.connect(path)
    try:
        data = {}
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[1] != "cpm"]
            data[table] = conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY rowid").fetchall()
        objects = conn.execute("SELECT type, name, sql FROM sqlite_master WHERE tbl_name != 'card_bid_details' ORDER BY type, name").fetchall()
        return data, objects
    finally:
        conn.close()


@pytest.fixture
def legacy(tmp_path):
    """A database as it was before cpm: every table, the import ones included, with data, and no cpm column."""
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        user = User(username="old", password_hash="h", role=UserRole.ADMIN)
        db.add(user)
        db.flush()
        board = Board(user_id=user.id, title="B")
        db.add(board)
        db.flush()
        column = Column(board_id=board.id, title="Proposal", position=0, stage_kind="open")
        db.add(column)
        db.flush()
        card = Card(column_id=column.id, title="Lead", position=0)
        db.add(card)
        db.flush()
        db.add(CardBidDetails(card_id=card.id, project_lead="Pessoa B", plant="P1"))
        db.commit()
    engine.dispose()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE card_bid_details DROP COLUMN cpm")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def point_engine(monkeypatch):
    def point(path):
        engine = create_engine(f"sqlite:///{path}")
        monkeypatch.setattr(database, "engine", engine)
        monkeypatch.setattr(database, "E2E_MODE", False)
        monkeypatch.delenv("PM_REQUIRE_EXISTING_DB", raising=False)
        return engine
    return point


def test_legacy_fixture_is_only_missing_cpm(legacy):
    assert "cpm" not in _columns(legacy)
    assert _state(legacy) == ("incompatible", CPM_MISSING)


def test_startup_refuses_it_without_touching_it(legacy, point_engine):
    engine = point_engine(legacy)
    before = _sha(legacy)
    with pytest.raises(BidCpmMigrationRequired, match="migrate_bid_cpm.py"):
        database.init_db()
    engine.dispose()
    assert _sha(legacy) == before


def test_dry_run_changes_nothing(legacy, capsys):
    before = _sha(legacy)
    assert migration.main(["--database", str(legacy)]) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and "card_bid_details.cpm VARCHAR NULL" in out
    assert _sha(legacy) == before


def test_apply_adds_only_a_null_cpm_and_preserves_everything(legacy, capsys):
    before = _dump_without_cpm(legacy)
    assert apply_cli(legacy) == 0
    assert "Applied" in capsys.readouterr().out

    assert _state(legacy) == (COMPLETE, [])
    assert "cpm" in _columns(legacy)
    assert _query(legacy, "SELECT cpm, project_lead, plant FROM card_bid_details") == [(None, "Pessoa B", "P1")]
    assert _dump_without_cpm(legacy) == before
    assert _query(legacy, "PRAGMA foreign_key_check") == []
    assert _query(legacy, "PRAGMA integrity_check") == [("ok",)]


def test_apply_takes_a_backup_of_the_previous_state(legacy):
    before = _dump_without_cpm(legacy)
    apply_cli(legacy)
    backups = list(legacy.parent.glob("legacy-pre-bid-cpm-*.db.bak"))
    assert len(backups) == 1
    assert "cpm" not in _columns(backups[0]) and _dump_without_cpm(backups[0]) == before  # byte layout differs; content doesn't


def test_second_apply_is_refused_and_writes_nothing(legacy, capsys):
    apply_cli(legacy)
    after = _sha(legacy)
    assert apply_cli(legacy) == 1
    assert "nothing to do" in capsys.readouterr().err
    assert _sha(legacy) == after
    assert len(list(legacy.parent.glob("*.db.bak"))) == 1


def test_migrated_database_starts(legacy, point_engine):
    apply_cli(legacy)
    engine = point_engine(legacy)
    database.init_db()
    engine.dispose()


def test_other_schema_differences_are_refused(legacy, capsys):
    conn = sqlite3.connect(legacy)
    conn.execute("ALTER TABLE card_bid_details DROP COLUMN plant")
    conn.commit()
    conn.close()
    before = _sha(legacy)
    assert apply_cli(legacy) == 1
    assert "plant" in capsys.readouterr().err
    assert _sha(legacy) == before and not list(legacy.parent.glob("*.db.bak"))


def test_import_tables_absent_is_refused(legacy, capsys):
    conn = sqlite3.connect(legacy)
    conn.executescript("DROP TABLE import_batch_items; DROP TABLE import_batch_ledgers; DROP TABLE card_import_changes; "
                       "DROP TABLE card_source_refs; DROP TABLE card_bid_details; DROP TABLE import_batches;")
    conn.close()
    before = _sha(legacy)
    assert apply_cli(legacy) == 1
    assert _sha(legacy) == before


def test_existing_foreign_key_violation_is_refused(legacy, capsys):
    conn = sqlite3.connect(legacy)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO card_bid_details (card_id, revenue_currency, created_at, updated_at) VALUES (9999, 'BRL', '2025-01-01', '2025-01-01')")
    conn.commit()
    conn.close()
    before = _sha(legacy)
    assert apply_cli(legacy) == 1
    assert "foreign key violation" in capsys.readouterr().err
    assert _sha(legacy) == before


def test_missing_database_is_refused(tmp_path, capsys):
    assert migration.main(["--database", str(tmp_path / "nope.db")]) == 1
    assert not (tmp_path / "nope.db").exists()


def test_post_check_failure_rolls_back(legacy, monkeypatch):
    """If the migrated schema doesn't verify, the transaction rolls back and the column is not there."""
    monkeypatch.setattr(migration, "ADD_COLUMN", "ALTER TABLE card_bid_details ADD COLUMN cpm INTEGER")
    before = _dump_without_cpm(legacy)
    assert apply_cli(legacy) == 1
    assert "cpm" not in _columns(legacy)
    assert _dump_without_cpm(legacy) == before


def test_fresh_commercial_database_has_cpm_and_starts(tmp_path, point_engine):
    path = tmp_path / "fresh.db"
    assert ccd.main(["--database", str(path), "--email", "boss@example.com"]) == 0
    assert _state(path) == (COMPLETE, [])
    assert _query(path, "SELECT type, \"notnull\" FROM pragma_table_info('card_bid_details') WHERE name='cpm'") == [("VARCHAR", 0)]
    engine = point_engine(path)
    database.init_db()
    engine.dispose()
    # nothing for the migration to do on it
    assert migration.main(["--database", str(path)]) == 0


def test_cpm_roundtrip_with_foreign_keys_enforced(tmp_path):
    path = tmp_path / "fresh.db"
    ccd.main(["--database", str(path), "--email", "boss@example.com"])
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _fk_on(conn, _record):
        conn.execute("PRAGMA foreign_keys=ON")

    with sessionmaker(bind=engine)() as db:
        column = db.query(Column).first()
        card = Card(column_id=column.id, title="Lead", position=0)
        card.bid_details = CardBidDetails(cpm="Pessoa A")
        db.add(card)
        db.commit()
        assert db.query(CardBidDetails).one().cpm == "Pessoa A"
        assert db.query(User).count() == 1 and card.salesperson_id is None  # no user created, no assignment
    engine.dispose()


# --- target safeguards (db_target_guard): every case is refused before anything is written -----------

def test_apply_requires_the_approved_hash(legacy, capsys):
    before = _sha(legacy)
    assert migration.main(["--database", str(legacy), "--apply"]) == 1
    assert "--expect-sha256" in capsys.readouterr().err
    assert _sha(legacy) == before and "cpm" not in _columns(legacy)


def test_a_hash_mismatch_is_refused(legacy, capsys):
    before = _sha(legacy)
    assert migration.main(["--database", str(legacy), "--apply", "--expect-sha256", "0" * 64]) == 1
    assert "SHA-256" in capsys.readouterr().err
    assert _sha(legacy) == before and not list(legacy.parent.glob("*.bak"))


def test_a_relative_path_is_refused(legacy, monkeypatch, capsys):
    monkeypatch.chdir(legacy.parent)
    assert migration.main(["--database", legacy.name]) == 1
    assert "absolute" in capsys.readouterr().err


def test_a_missing_database_is_never_created(tmp_path, capsys):
    missing = tmp_path / "missing.db"
    assert migration.main(["--database", str(missing), "--apply", "--expect-sha256", "0" * 64]) == 1
    assert not missing.exists() and not list(tmp_path.iterdir())


def test_a_symlink_is_refused(legacy, tmp_path, capsys):
    link = tmp_path / "link.db"
    link.symlink_to(legacy)
    before = _sha(legacy)
    assert migration.main(["--database", str(link), "--apply", "--expect-sha256", before]) == 1
    assert "symlink" in capsys.readouterr().err
    assert _sha(legacy) == before


def test_a_protected_development_database_is_refused_by_path_inode_and_name(legacy, tmp_path):
    from db_target_guard import TargetRefused, validate_target

    protected = tmp_path / "devdir" / "pm.db"       # stands in for backend/pm.db
    protected.parent.mkdir()
    protected.write_bytes(legacy.read_bytes())
    with pytest.raises(TargetRefused, match="protected"):
        validate_target(str(protected), protected=(protected,))
    hard = tmp_path / "other-name.db"               # same inode under another name
    hard.hardlink_to(protected)
    with pytest.raises(TargetRefused):
        validate_target(str(hard), protected=(protected,))
    with pytest.raises(TargetRefused, match="pm.db"):  # by name alone, with no known protected path
        validate_target(str(protected), protected=())
    assert _sha(protected) == _sha(legacy)


def test_the_real_development_databases_are_protected_paths():
    from pathlib import Path

    from db_target_guard import PROTECTED_PATHS

    backend = Path(migration.__file__).resolve().parent
    assert {p.name for p in PROTECTED_PATHS} == {"pm.db", "pm.db.bak"}
    assert {backend / "pm.db", backend / "pm.db.bak", backend.parent / "pm.db", backend.parent / "pm.db.bak"} == set(PROTECTED_PATHS)


def test_a_sidecar_file_means_an_unexpected_state(legacy, capsys):
    (legacy.parent / (legacy.name + "-wal")).write_bytes(b"")
    before = _sha(legacy)
    assert migration.main(["--database", str(legacy), "--apply", "--expect-sha256", before]) == 1
    assert "Unexpected state" in capsys.readouterr().err
    assert _sha(legacy) == before


def test_something_that_is_not_sqlite_is_refused(tmp_path, capsys):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database")
    assert migration.main(["--database", str(junk)]) == 1
    assert "isn't a SQLite database" in capsys.readouterr().err


def test_only_the_allowed_path_is_accepted(legacy, tmp_path):
    from db_target_guard import TargetRefused, validate_target

    assert validate_target(str(legacy), allowed=[str(legacy)]) == str(legacy)
    other = tmp_path / "other.db"
    other.write_bytes(legacy.read_bytes())
    with pytest.raises(TargetRefused, match="approved"):
        validate_target(str(other), allowed=[str(legacy)])
