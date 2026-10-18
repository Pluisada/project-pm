"""migrate_legacy_bid_confidence.py and the legacy startup guard. Disposable tmp_path SQLite files only, invented data."""
import datetime
import hashlib
import os
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import database
import migrate_confidence_level as company_tool
import migrate_legacy_bid_confidence as migration
import migrate_stage_kind
from database import ImportTablesSchemaError
from import_schema import COMPLETE, import_schema_state
from models import Base, Board, Card, CardBidDetails, CardSourceRef, Column, User, UserRole


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def rows(path, sql):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def cols(path, table):
    return [r[1] for r in rows(path, f"PRAGMA table_info({table})")]


def apply_cli(path):
    return migration.main(["--database", str(path), "--apply", "--expect-sha256", sha(path)])


def make_legacy(tmp_path, with_level=True):
    """A legacy file as it is today: the current schema plus the two obsolete columns (with values), optionally
    without cards.confidence_level."""
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
        db.add(CardSourceRef(card_id=card.id, source_system="bid", source_key="k", source_code="000101-01"))
        db.commit()
    engine.dispose()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE card_bid_details ADD COLUMN confidence INTEGER")
    conn.execute("ALTER TABLE card_source_refs ADD COLUMN source_confidence VARCHAR")
    conn.execute("UPDATE card_bid_details SET confidence = 40")
    conn.execute("UPDATE card_source_refs SET source_confidence = 'MEDIUM (40%)'")
    if not with_level:
        conn.execute("ALTER TABLE cards DROP COLUMN confidence_level")
    conn.commit()
    conn.close()
    return path


def test_startup_refuses_the_obsolete_columns_without_touching_the_file(tmp_path, monkeypatch):
    path = make_legacy(tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "E2E_MODE", False)
    monkeypatch.delenv("PM_REQUIRE_EXISTING_DB", raising=False)
    before = sha(path)
    with pytest.raises(ImportTablesSchemaError):
        database.init_db()
    engine.dispose()
    assert sha(path) == before


def test_dry_run_changes_nothing_and_lists_the_work(tmp_path, capsys):
    path = make_legacy(tmp_path, with_level=False)
    before = sha(path)
    assert migration.main(["--database", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and "cards.confidence_level VARCHAR(6) NULL" in out
    assert "card_bid_details.confidence" in out and "card_source_refs.source_confidence" in out
    assert sha(path) == before


@pytest.mark.parametrize("with_level", [True, False])
def test_apply_drops_the_obsolete_columns_adds_the_level_and_keeps_everything_else(tmp_path, with_level):
    path = make_legacy(tmp_path, with_level)
    kept = {t: rows(path, f"SELECT {', '.join(c for c in cols(path, t) if c not in ('confidence', 'source_confidence', 'confidence_level'))} FROM {t} ORDER BY rowid")
            for t in ("users", "boards", "columns", "cards", "card_bid_details", "card_source_refs")}
    assert apply_cli(path) == 0
    assert "confidence" not in cols(path, "card_bid_details") and "source_confidence" not in cols(path, "card_source_refs")
    assert rows(path, "SELECT COUNT(*), COUNT(confidence_level) FROM cards") == [(1, 0)]
    for t, old in kept.items():
        assert rows(path, f"SELECT {', '.join(c for c in cols(path, t) if c != 'confidence_level')} FROM {t} ORDER BY rowid") == old
    conn = sqlite3.connect(path)
    assert import_schema_state(lambda sql: conn.execute(sql).fetchall())[0] == COMPLETE
    conn.close()
    assert rows(path, "PRAGMA integrity_check") == [("ok",)] and rows(path, "PRAGMA foreign_key_check") == []
    assert len(list(tmp_path.glob("legacy-pre-bid-confidence-*.db.bak"))) == 1


def test_a_migrated_database_opens_with_the_application_models(tmp_path, monkeypatch):
    path = make_legacy(tmp_path)
    apply_cli(path)
    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "E2E_MODE", False)
    monkeypatch.delenv("PM_REQUIRE_EXISTING_DB", raising=False)
    database.init_db()  # every startup guard passes
    with sessionmaker(bind=engine)() as db:
        assert db.query(Card).one().confidence_level is None
        assert db.query(CardBidDetails).one().plant == "P1"
    engine.dispose()


def test_an_already_migrated_database_is_left_alone(tmp_path, capsys):
    path = make_legacy(tmp_path)
    apply_cli(path)
    after = sha(path)
    assert migration.main(["--database", str(path)]) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert apply_cli(path) == 1 and sha(path) == after and len(list(tmp_path.glob("*.bak"))) == 1


def test_apply_needs_the_expected_hash_and_a_wrong_hash_writes_nothing(tmp_path, capsys):
    path = make_legacy(tmp_path)
    before = sha(path)
    assert migration.main(["--database", str(path), "--apply"]) == 1
    assert migration.main(["--database", str(path), "--apply", "--expect-sha256", "0" * 64]) == 1
    assert sha(path) == before and not list(tmp_path.glob("*.bak"))


def test_other_schema_differences_roll_back(tmp_path):
    path = make_legacy(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE card_bid_details ADD COLUMN stray TEXT")
    conn.commit()
    conn.close()
    before = sha(path)
    assert apply_cli(path) == 1
    assert "confidence" in cols(path, "card_bid_details")  # nothing was dropped
    assert rows(path, "SELECT COUNT(*) FROM cards") == [(1,)] and "stray" in cols(path, "card_bid_details")
    assert len(list(tmp_path.glob("legacy-pre-bid-confidence-*[0-9].db.bak"))) == 1  # detected after the DDL: verified backup kept


def test_a_missing_file_is_never_created(tmp_path):
    assert migration.main(["--database", str(tmp_path / "missing.db")]) == 1
    assert not (tmp_path / "missing.db").exists()


def test_a_failure_after_the_schema_changes_rolls_everything_back(tmp_path, monkeypatch):
    path = make_legacy(tmp_path, with_level=False)

    def dump(p):
        tables = [r[0] for r in rows(p, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        return (rows(p, "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"),
                rows(p, "PRAGMA user_version"),
                {t: rows(p, f"SELECT * FROM {t} ORDER BY rowid") for t in tables})

    original = dump(path)
    def fail_before_commit(query):
        # Called after ADD COLUMN and both DROP COLUMNs, still inside the open transaction.
        raise RuntimeError("injected failure before COMMIT")

    monkeypatch.setattr(migration, "import_schema_state", fail_before_commit)
    with pytest.raises(RuntimeError, match="injected failure before COMMIT"):
        migration.apply(str(path), sha(path))

    assert dump(path) == original  # schema text, schema version, and every row restored
    assert "confidence" in cols(path, "card_bid_details") and "source_confidence" in cols(path, "card_source_refs")
    assert "confidence_level" not in cols(path, "cards")
    assert rows(path, "SELECT COUNT(*) FROM cards") == [(1,)] and rows(path, "SELECT COUNT(*) FROM card_bid_details") == [(1,)]
    assert rows(path, "PRAGMA foreign_key_check") == [] and rows(path, "PRAGMA integrity_check") == [("ok",)]
    # The backup is written before the transaction opens and is a legitimate leftover; it holds the original schema.
    backups = list(tmp_path.glob("legacy-pre-bid-confidence-*.db.bak"))
    assert len(backups) == 1 and dump(backups[0]) == original


def dump(p):
    tables = [r[0] for r in rows(p, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    return (rows(p, "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"), rows(p, "PRAGMA user_version"),
            {t: rows(p, f"SELECT * FROM {t} ORDER BY rowid") for t in tables})


class FixedNow(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 1, 2, 3, 4, 5)


@pytest.fixture
def fixed_clock(monkeypatch):
    monkeypatch.setattr(migrate_stage_kind, "datetime", FixedNow)


def test_the_backup_is_exclusive_private_and_equivalent_to_the_approved_state(tmp_path):
    path = make_legacy(tmp_path)
    original = dump(path)
    conn = sqlite3.connect(path)
    approved = company_tool.content_fingerprint(conn)
    conn.close()
    assert apply_cli(path) == 0
    (backup,) = tmp_path.glob("legacy-pre-bid-confidence-*.db.bak")
    assert (backup.stat().st_mode & 0o777) == 0o600  # created with O_EXCL, mode 0600
    assert dump(backup) == original  # the obsolete columns and their values are in the backup
    assert rows(backup, "SELECT confidence FROM card_bid_details") == [(40,)]
    conn = sqlite3.connect(backup)
    assert company_tool.fingerprint_differences(approved, company_tool.content_fingerprint(conn)) == []
    conn.close()
    assert rows(backup, "PRAGMA integrity_check") == [("ok",)]
    assert not list(tmp_path.glob("*UNVERIFIED*"))


def test_an_existing_backup_is_never_overwritten_and_nothing_is_migrated(tmp_path, fixed_clock, capsys):
    path = make_legacy(tmp_path)
    existing = tmp_path / "legacy-pre-bid-confidence-20260102030405.db.bak"
    existing.write_bytes(b"an earlier backup")
    before = sha(path)
    assert apply_cli(path) == 1
    assert existing.read_bytes() == b"an earlier backup"
    assert sha(path) == before and "confidence_level" in cols(path, "cards")
    assert "already exists" in capsys.readouterr().err
    assert sorted(f.name for f in tmp_path.iterdir()) == [existing.name, "legacy.db"]  # no new artifact of any kind


def _corrupt_backup_after_copy(monkeypatch, damage):
    real = company_tool.backup_database

    def backup_then_damage(*args, **kwargs):
        backup_path = real(*args, **kwargs)
        conn = sqlite3.connect(backup_path)
        conn.execute(damage)
        conn.commit()
        conn.close()
        return backup_path

    monkeypatch.setattr(company_tool, "backup_database", backup_then_damage)


@pytest.mark.parametrize("damage", ["UPDATE card_bid_details SET confidence = 41", "DROP TABLE card_source_refs"])
def test_a_backup_that_fails_verification_is_quarantined_and_blocks_the_migration(tmp_path, monkeypatch, capsys, damage):
    path = make_legacy(tmp_path)
    original, before = dump(path), sha(path)
    _corrupt_backup_after_copy(monkeypatch, damage)
    assert apply_cli(path) == 1
    err = capsys.readouterr().err
    assert "does not match the approved database" in err and "UNVERIFIED" in err and "do not restore from it" in err
    assert sha(path) == before and dump(path) == original  # no destructive change
    assert not list(tmp_path.glob("legacy-pre-bid-confidence-*[0-9].db.bak"))  # nothing is left looking like a valid backup
    (quarantined,) = tmp_path.glob("*.UNVERIFIED*.db.bak")  # the unverified artifact is preserved, named as such
    assert quarantined.stat().st_size > 0


def test_a_quarantine_name_that_exists_is_not_overwritten(tmp_path, monkeypatch, fixed_clock):
    path = make_legacy(tmp_path)
    taken = tmp_path / "legacy-pre-bid-confidence-20260102030405.UNVERIFIED.db.bak"
    taken.write_bytes(b"earlier unverified artifact")
    _corrupt_backup_after_copy(monkeypatch, "UPDATE card_bid_details SET confidence = 41")
    assert apply_cli(path) == 1
    assert taken.read_bytes() == b"earlier unverified artifact"
    assert (tmp_path / "legacy-pre-bid-confidence-20260102030405.UNVERIFIED2.db.bak").exists()
    assert "confidence" in cols(path, "card_bid_details")


def test_a_failed_backup_copy_leaves_a_quarantined_artifact_and_the_database_untouched(tmp_path, monkeypatch):
    path = make_legacy(tmp_path)
    original = dump(path)

    class Reader:  # a read-only connection whose backup() fails after the destination file exists
        def __init__(self, conn):
            self.conn = conn

        def backup(self, target, **kwargs):
            raise sqlite3.OperationalError("injected copy failure")

        def close(self):
            self.conn.close()

    monkeypatch.setattr(company_tool, "open_readonly", lambda p: Reader(sqlite3.connect(f"file:{p}?mode=ro", uri=True)))
    assert apply_cli(path) == 1
    assert dump(path) == original
    assert len(list(tmp_path.glob("*.UNVERIFIED*.db.bak"))) == 1 and not list(tmp_path.glob("legacy-pre-bid-confidence-*[0-9].db.bak"))


def test_the_hash_is_rechecked_under_the_write_lock(tmp_path, monkeypatch, capsys):
    path = make_legacy(tmp_path)
    before = sha(path)
    real, calls = migration._hash_of, []

    def changes_after_the_first_check(*args):
        calls.append(1)
        return real(*args) if len(calls) == 1 else "0" * 64

    monkeypatch.setattr(migration, "_hash_of", changes_after_the_first_check)
    assert apply_cli(path) == 1
    assert len(calls) == 2 and sha(path) == before
    assert "SHA-256 differs" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.bak"))  # refused before any backup was written


def test_a_file_replaced_after_validation_is_refused(tmp_path, monkeypatch, capsys):
    path = make_legacy(tmp_path)
    before = sha(path)
    real = migration.open_readwrite

    def swap_then_open(p, **kwargs):  # another file takes the validated name just before SQLite opens it
        os.rename(p, p + ".moved")
        with open(p + ".moved", "rb") as src, open(p, "wb") as dst:
            dst.write(src.read())
        return real(p, **kwargs)

    monkeypatch.setattr(migration, "open_readwrite", swap_then_open)
    assert apply_cli(path) == 1
    assert sha(path) == before and sha(str(path) + ".moved") == before
    assert "different file" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.bak"))


def test_a_failure_after_the_backup_keeps_the_verified_backup_and_says_so(tmp_path, monkeypatch):
    path = make_legacy(tmp_path)
    original, before = dump(path), sha(path)

    def fail(conn):
        raise RuntimeError("injected")

    monkeypatch.setattr(migration, "_snapshot", fail)  # first used right after the backup, before any schema change
    with pytest.raises(RuntimeError) as caught:
        migration.apply(str(path), before)
    assert any("rolled back, database unchanged; verified backup of the approved state kept at" in n for n in caught.value.__notes__)
    assert dump(path) == original and sha(path) == before
    (backup,) = tmp_path.glob("legacy-pre-bid-confidence-*.db.bak")
    assert dump(backup) == original


@pytest.mark.parametrize("kind", ["symlink", "sidecar", "protected_name", "relative", "not_sqlite", "directory"])
def test_invalid_targets_are_refused_without_writing(tmp_path, kind):
    real = make_legacy(tmp_path)
    if kind == "symlink":
        target = tmp_path / "link.db"
        target.symlink_to(real)
    elif kind == "sidecar":
        target = real
        (tmp_path / "legacy.db-journal").write_bytes(b"")
    elif kind == "protected_name":
        target = tmp_path / "pm.db"
        real.rename(target)
    elif kind == "relative":
        target = os.path.relpath(real)
    elif kind == "not_sqlite":
        target = tmp_path / "text.db"
        target.write_bytes(b"not a database")
    else:
        target = tmp_path / "dir.db"
        target.mkdir()
    snapshot = sorted((f.name, f.is_file() and f.stat().st_size) for f in tmp_path.iterdir())
    for args in ([], ["--apply", "--expect-sha256", "0" * 64]):
        assert migration.main(["--database", str(target), *args]) == 1
    assert sorted((f.name, f.is_file() and f.stat().st_size) for f in tmp_path.iterdir()) == snapshot


def test_database_url_is_never_a_fallback(tmp_path, monkeypatch):
    path = make_legacy(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    before = sha(path)
    with pytest.raises(SystemExit):
        migration.main([])
    with pytest.raises(SystemExit):
        migration.main(["--apply"])
    assert sha(path) == before and not list(tmp_path.glob("*.bak"))


def test_a_wal_database_is_refused_before_anything_is_written(tmp_path, capsys):
    path = make_legacy(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.close()  # the last connection checkpoints and removes the sidecars; the header stays in WAL mode
    assert not (tmp_path / "legacy.db-wal").exists()
    before = sha(path)
    assert apply_cli(path) == 1
    assert "WAL mode" in capsys.readouterr().err
    assert sha(path) == before and not list(tmp_path.glob("*.bak"))


def test_existing_foreign_key_violations_are_refused_before_any_backup(tmp_path, capsys):
    path = make_legacy(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO cards (column_id, title, position, created_at, updated_at) VALUES (999, 'orphan', 1, '2026-01-01', '2026-01-01')")
    conn.commit()
    conn.close()
    before = sha(path)
    assert apply_cli(path) == 1
    assert "foreign key violation" in capsys.readouterr().err
    assert sha(path) == before and not list(tmp_path.glob("*.bak"))
