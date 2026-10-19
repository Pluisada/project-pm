"""Company schema version 4 (cards.confidence_level, no obsolete Bid confidence) and migrate_confidence_level.py.

Disposable tmp_path SQLite databases only, with invented data. Nothing here touches a persistent database; the
tool's own target rules (migrate_identity_v2.check_target) refuse protected and configured paths, which is tested.
"""
import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import migrate_confidence_level as mig
from company_runtime import CompanyRuntime
from company_schema import COMPANY_SCHEMA_VERSION, REQUIRED_COMPANY_SCHEMA, CompanySchemaError, validate_company_schema
from create_company_db import create_company_database
from database_targets import COMPANY_SLUGS, company_env_name
from migrate_stage_kind import MigrationError
from models import (
    AuditEvent, Board, BoardColumn, Card, CardBidDetails, CardSourceRef, CompanyUser, ImportBatch, ImportBatchItem,
    ImportBatchLedger, UndoEntry,
)

NOW = datetime(2026, 1, 1)


def env_for(tmp_path, slug="lity"):
    return {company_env_name(slug): f"sqlite:///{tmp_path / (slug + '.db')}"}


def seed(path):
    """Synthetic company data: cards with lead fields, Bid details, a source ref, an import batch with ledger."""
    engine = create_engine(f"sqlite:///{path}")
    with Session(engine) as db:
        db.add(CompanyUser(id=7, username="ana@example.test"))
        db.add(Board(id=1, user_id=7, title="Board"))
        db.add(BoardColumn(id=1, board_id=1, title="Proposal", position=0, stage_kind="open"))
        for i in (1, 2, 3):
            db.add(Card(id=i, column_id=1, title=f"Card {i}", position=i, company="Acme", deal_value=1000.0 * i,
                        lead_source="Referral", product_line="Chargers", salesperson_id=7))
        db.flush()
        db.add(CardBidDetails(card_id=1, next_step="Call"))
        db.add(CardSourceRef(card_id=1, source_system="bid", source_key="k1", source_code="000101-01"))
        batch = ImportBatch(id=1, board_id=1, user_id=7, filename="r.xlsx", file_sha256="a" * 64, mapping_json="{}",
                            status="completed", created_count=1)
        db.add(batch)
        db.flush()
        ledger = ImportBatchLedger(id=1, import_batch_id=1, ledger_version=1)
        db.add(ledger)
        db.flush()
        db.add(ImportBatchItem(ledger_id=1, card_id=1, board_id=1, source_key="k1", action="create",
                               before_json=None, after_json=json.dumps({"bid_details.next_step": "Call"})))
        db.add(AuditEvent(board_id=1, card_id=1, actor_user_id=7, action="assign", details="{}", created_at=NOW))
        db.add(UndoEntry(user_id=7, board_id=1, description="d", steps="[]"))
        db.commit()
    engine.dispose()


def make_old(tmp_path, version=2, slug="lity"):
    """A pre-v4 company database as it exists today: the v4 schema plus the two obsolete Bid columns (with invented
    values), minus cards.confidence_level for version 2, stamped `version`, with data."""
    env = env_for(tmp_path, slug)
    path = create_company_database(slug, env)
    seed(path)
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE card_bid_details ADD COLUMN confidence INTEGER")
    con.execute("ALTER TABLE card_source_refs ADD COLUMN source_confidence VARCHAR")
    con.execute("UPDATE card_bid_details SET confidence = 70")
    con.execute("UPDATE card_source_refs SET source_confidence = 'HIGH (70%)'")
    if version == 2:
        con.execute("ALTER TABLE cards DROP COLUMN confidence_level")
    else:
        con.execute("UPDATE cards SET confidence_level = 'LOW' WHERE id = 2")  # a level the team already set
    con.execute(f"PRAGMA user_version = {version}")
    con.commit()
    con.close()
    return env, str(path)


def make_v2(tmp_path, slug="lity"):
    return make_old(tmp_path, 2, slug)


def columns_of(path, table):
    return [r[1] for r in rows(path, f"PRAGMA table_info({table})")]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows(path, sql):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def run(path, *flags, company="lity"):
    return mig.main(["--company", company, "--database", path, *flags])


# --- schema version 4 -------------------------------------------------------

def test_contract_is_version_4_requires_the_level_and_has_no_obsolete_columns():
    assert COMPANY_SCHEMA_VERSION == 4
    assert "confidence_level" in REQUIRED_COMPANY_SCHEMA["cards"]
    assert "confidence" not in REQUIRED_COMPANY_SCHEMA["card_bid_details"]
    assert "source_confidence" not in REQUIRED_COMPANY_SCHEMA["card_source_refs"]


@pytest.mark.parametrize("slug", COMPANY_SLUGS)
def test_fresh_databases_are_version_4_with_a_nullable_level_and_no_obsolete_columns(tmp_path, slug):
    env = env_for(tmp_path, slug)
    path = create_company_database(slug, env)
    assert rows(path, "PRAGMA user_version") == [(4,)]
    column = next(r for r in rows(path, "PRAGMA table_info(cards)") if r[1] == "confidence_level")
    assert column[2].upper() == "VARCHAR(6)" and column[3] == 0  # nullable
    assert "confidence" not in columns_of(path, "card_bid_details")
    assert "source_confidence" not in columns_of(path, "card_source_refs")
    validate_company_schema(slug, env)


@pytest.mark.parametrize("version", [2, 3])
def test_an_older_database_is_refused_before_any_card_access(tmp_path, version):
    env, path = make_old(tmp_path, version)
    with pytest.raises(CompanySchemaError, match=f"schema version {version} found, 4 expected"):
        validate_company_schema("lity", env)
    queried = []
    runtime = CompanyRuntime(env)
    with pytest.raises(HTTPException) as refused:
        runtime._session_factory("lity")  # what every company request does first
    assert refused.value.status_code == 503 and queried == []
    assert not runtime._factories and not runtime._engines  # nothing was opened or cached


def test_extra_columns_are_still_tolerated(tmp_path):
    env = env_for(tmp_path)
    path = create_company_database("lity", env)
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE cards ADD COLUMN later_addition TEXT")
    con.commit()
    con.close()
    validate_company_schema("lity", env)


# --- dry run ----------------------------------------------------------------

def test_dry_run_changes_nothing_and_says_so(tmp_path, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    assert run(path) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and "nothing was written" in out and "2 -> 4" in out
    assert f"database SHA-256: {before}" in out
    assert sha(path) == before and rows(path, "PRAGMA user_version") == [(2,)]
    assert "confidence_level" not in [r[1] for r in rows(path, "PRAGMA table_info(cards)")]
    assert not list(tmp_path.glob("*.bak"))


# --- apply ------------------------------------------------------------------

def test_apply_adds_the_level_drops_the_obsolete_columns_and_keeps_every_other_value(tmp_path, capsys):
    env, path = make_v2(tmp_path)
    tables = [r[0] for r in rows(path, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    dropped = {("card_bid_details", "confidence"), ("card_source_refs", "source_confidence")}
    old = {t: rows(path, f'SELECT {", ".join(c for c in columns_of(path, t) if (t, c) not in dropped)} FROM "{t}" ORDER BY rowid') for t in tables}
    assert run(path, "--apply", "--confirm-app-stopped", "--expect-sha256", sha(path)) == 0
    assert rows(path, "PRAGMA user_version") == [(4,)]
    column = next(r for r in rows(path, "PRAGMA table_info(cards)") if r[1] == "confidence_level")
    assert column[2].upper() == "VARCHAR(6)" and column[3] == 0
    assert rows(path, "SELECT COUNT(*), COUNT(confidence_level) FROM cards") == [(3, 0)]  # every card "not assigned"
    assert "confidence" not in columns_of(path, "card_bid_details")
    assert "source_confidence" not in columns_of(path, "card_source_refs")
    for t in tables:
        kept = [c for c in columns_of(path, t) if not (t == "cards" and c == "confidence_level")]
        assert rows(path, f'SELECT {", ".join(kept)} FROM "{t}" ORDER BY rowid') == old[t]  # every surviving value identical
    validate_company_schema("lity", env)
    assert "Applied" in capsys.readouterr().out


def test_a_version_3_database_only_loses_the_obsolete_columns_and_keeps_its_levels(tmp_path, capsys):
    env, path = make_old(tmp_path, 3)
    assert run(path, "--apply", "--confirm-app-stopped", "--expect-sha256", sha(path)) == 0
    assert rows(path, "PRAGMA user_version") == [(4,)]
    assert rows(path, "SELECT id, confidence_level FROM cards WHERE confidence_level IS NOT NULL") == [(2, "LOW")]
    assert "confidence" not in columns_of(path, "card_bid_details") and "source_confidence" not in columns_of(path, "card_source_refs")
    validate_company_schema("lity", env)


def test_import_records_and_the_other_bid_fields_are_intact(tmp_path):
    env, path = make_v2(tmp_path)
    run(path, "--apply", "--confirm-app-stopped", "--expect-sha256", sha(path))
    assert rows(path, "SELECT next_step FROM card_bid_details") == [("Call",)]
    assert rows(path, "SELECT source_code FROM card_source_refs") == [("000101-01",)]
    assert rows(path, "SELECT status, created_count FROM import_batches") == [("completed", 1)]
    assert rows(path, "SELECT COUNT(*) FROM import_batch_ledgers WHERE reverted_at IS NULL") == [(1,)]
    assert rows(path, "PRAGMA integrity_check") == [("ok",)] and rows(path, "PRAGMA foreign_key_check") == []


def test_a_verified_backup_of_the_previous_state_is_made_first(tmp_path, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    run(path, "--apply", "--confirm-app-stopped", "--expect-sha256", sha(path))
    backups = list(tmp_path.glob("lity-pre-confidence-level-*.db.bak"))
    assert len(backups) == 1
    assert rows(backups[0], "PRAGMA user_version") == [(2,)]
    assert "confidence_level" not in [r[1] for r in rows(backups[0], "PRAGMA table_info(cards)")]
    assert rows(backups[0], "SELECT confidence FROM card_bid_details") == [(70,)]  # the backup is the previous state
    assert rows(backups[0], "PRAGMA integrity_check") == [("ok",)]
    assert rows(backups[0], "SELECT COUNT(*) FROM cards") == [(3,)]
    assert sha(path) != before  # the live file moved on, the backup did not


def test_apply_without_the_stop_confirmation_is_refused_and_writes_nothing(tmp_path, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    assert run(path, "--apply", "--expect-sha256", before) == 1
    assert "--confirm-app-stopped" in capsys.readouterr().err
    assert sha(path) == before and not list(tmp_path.glob("*.bak"))


def test_a_second_apply_is_refused_without_a_new_backup_or_change(tmp_path, capsys):
    env, path = make_v2(tmp_path)
    run(path, "--apply", "--confirm-app-stopped", "--expect-sha256", sha(path))
    after_first, backups = sha(path), list(tmp_path.glob("*.bak"))
    assert run(path, "--apply", "--confirm-app-stopped", "--expect-sha256", sha(path)) == 1
    assert "nothing to do" in capsys.readouterr().err
    assert run(path) == 1  # a dry run says the same
    assert sha(path) == after_first and list(tmp_path.glob("*.bak")) == backups


def test_the_backup_verification_failing_stops_before_any_change(tmp_path, monkeypatch):
    env, path = make_v2(tmp_path)
    before = sha(path)
    monkeypatch.setattr(mig, "verify_backup", lambda *a: (_ for _ in ()).throw(MigrationError("backup mismatch")))
    assert run(path, "--apply", "--confirm-app-stopped", "--expect-sha256", sha(path)) == 1
    assert sha(path) == before and rows(path, "PRAGMA user_version") == [(2,)]


# --- failure safety ---------------------------------------------------------

@pytest.mark.parametrize("fail_at", ["column added", "obsolete columns dropped", "version set", "verified"])
def test_a_failure_rolls_everything_back_and_never_leaves_a_false_version(tmp_path, fail_at):
    env, path = make_v2(tmp_path)
    conn = sqlite3.connect(path, isolation_level=None)

    def boom(name):
        if name == fail_at:
            raise RuntimeError("injected")
    with pytest.raises(RuntimeError):
        mig.migrate(conn, _after_step=boom)
    conn.close()
    assert rows(path, "PRAGMA user_version") == [(2,)]
    assert "confidence_level" not in [r[1] for r in rows(path, "PRAGMA table_info(cards)")]
    assert "confidence" in columns_of(path, "card_bid_details") and "source_confidence" in columns_of(path, "card_source_refs")
    assert rows(path, "SELECT COUNT(*) FROM cards") == [(3,)]


def test_a_level_assigned_during_the_migration_aborts_it(tmp_path):
    env, path = make_v2(tmp_path)
    conn = sqlite3.connect(path, isolation_level=None)

    def sneaky(name):
        if name == "version set":
            conn.execute("UPDATE cards SET confidence_level = 'HIGH' WHERE id = 1")
    with pytest.raises(MigrationError, match="assigned"):
        mig.migrate(conn, _after_step=sneaky)
    conn.close()
    assert rows(path, "PRAGMA user_version") == [(2,)]


@pytest.mark.parametrize("state", ["column_without_version", "version_without_column", "wrong_version", "missing_table", "already_migrated"])
def test_inconsistent_or_unexpected_states_are_refused_unchanged(tmp_path, state, capsys):
    env, path = make_v2(tmp_path)
    con = sqlite3.connect(path)
    if state == "column_without_version":
        con.execute("ALTER TABLE cards ADD COLUMN confidence_level VARCHAR(6)")
    elif state == "version_without_column":
        con.execute("PRAGMA user_version = 3")
    elif state == "wrong_version":
        con.execute("PRAGMA user_version = 1")
    elif state == "already_migrated":
        con.execute("ALTER TABLE cards ADD COLUMN confidence_level VARCHAR(6)")
        con.execute("ALTER TABLE card_bid_details DROP COLUMN confidence")
        con.execute("ALTER TABLE card_source_refs DROP COLUMN source_confidence")
        con.execute("PRAGMA user_version = 4")
    else:
        con.execute("DROP TABLE undo_entries")
    con.commit()
    con.close()
    before = sha(path)
    assert run(path, "--apply", "--confirm-app-stopped", "--expect-sha256", sha(path)) == 1
    assert sha(path) == before and not list(tmp_path.glob("*.bak"))
    assert run(path) == 1


# --- targets ------------------------------------------------------------------

def test_unsafe_or_ambiguous_targets_are_refused(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    real = Path(path)
    protected = tmp_path / "pm-commercial.db"
    protected.write_bytes(real.read_bytes())
    link = tmp_path / "link.db"
    link.symlink_to(real)
    sidecar_db = tmp_path / "sidecar.db"
    sidecar_db.write_bytes(real.read_bytes())
    (tmp_path / "sidecar.db-wal").write_bytes(b"")
    for target in ("relative.db", "sqlite:///x.db", ":memory:", str(protected), str(link), str(sidecar_db), str(tmp_path / "missing.db")):
        assert run(target, "--apply", "--confirm-app-stopped", "--expect-sha256", "0" * 64) == 1, target
    assert not list(tmp_path.glob("*.bak"))
    # a path that this process has configured for a database is refused as well
    monkeypatch.setenv(company_env_name("lity"), f"sqlite:///{real}")
    assert run(path) == 1
    assert "configured database" in capsys.readouterr().err
    monkeypatch.delenv(company_env_name("lity"))
    assert run(path) == 0  # and it is fine once nothing configures it


def test_the_protected_name_is_refused_even_for_the_salcomp_label_and_other_labels_run(tmp_path, capsys):
    env, path = make_v2(tmp_path)
    protected = tmp_path / "pm.db"
    protected.write_bytes(Path(path).read_bytes())
    assert run(str(protected), company="salcomp") == 1
    assert run(path, company="salcomp") == 0  # a non-protected company file may carry any company label, dry run
    with pytest.raises(SystemExit):
        mig.main(["--company", "acme", "--database", path])


def test_a_legacy_single_database_or_a_foreign_file_is_refused(tmp_path, capsys):
    legacy = tmp_path / "legacy.db"
    con = sqlite3.connect(legacy)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT)")
    con.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, title TEXT)")
    con.execute("PRAGMA user_version = 2")
    con.commit()
    con.close()
    foreign = tmp_path / "foreign.db"
    con = sqlite3.connect(foreign)
    con.execute("CREATE TABLE t (x)")
    con.commit()
    con.close()
    before = (sha(legacy), sha(foreign))
    for target in (legacy, foreign):
        assert run(str(target), "--apply", "--confirm-app-stopped", "--expect-sha256", sha(target)) == 1
    assert (sha(legacy), sha(foreign)) == before and not list(tmp_path.glob("*.bak"))


def test_importing_the_tool_and_the_app_does_not_migrate_anything(tmp_path):
    env, path = make_v2(tmp_path)
    before = sha(path)
    import main  # noqa: F401  (startup never migrates a company database)
    import company_runtime  # noqa: F401
    assert sha(path) == before


# --- SHA-256 protection of --apply -------------------------------------------

def apply_with(path, digest):
    return run(path, "--apply", "--confirm-app-stopped", "--expect-sha256", digest)


def assert_untouched(path, before, tmp_path, backups=0):
    assert sha(path) == before and rows(path, "PRAGMA user_version") == [(2,)]
    assert "confidence" in columns_of(path, "card_bid_details") and "confidence_level" not in columns_of(path, "cards")
    assert len(list(tmp_path.glob("*.db.bak"))) == backups


def test_apply_without_the_hash_is_refused_and_writes_nothing(tmp_path, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    assert run(path, "--apply", "--confirm-app-stopped") == 1
    assert "--expect-sha256" in capsys.readouterr().err
    assert_untouched(path, before, tmp_path)


def test_a_wrong_hash_is_refused_and_writes_nothing(tmp_path, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    assert apply_with(path, "0" * 64) == 1
    assert "SHA-256 differs" in capsys.readouterr().err
    assert_untouched(path, before, tmp_path)


def test_the_hash_printed_by_the_dry_run_is_the_one_apply_accepts(tmp_path, capsys):
    env, path = make_v2(tmp_path)
    assert run(path) == 0
    printed = next(line.split(": ")[1] for line in capsys.readouterr().out.splitlines() if "database SHA-256" in line)
    assert apply_with(path, printed.upper()) == 0 and rows(path, "PRAGMA user_version") == [(4,)]


def test_a_file_changed_after_the_dry_run_is_refused(tmp_path, capsys):
    env, path = make_v2(tmp_path)
    run(path)
    approved = sha(path)
    con = sqlite3.connect(path)
    con.execute("UPDATE cards SET title = 'Edited later' WHERE id = 1")
    con.commit()
    con.close()
    changed = sha(path)
    assert apply_with(path, approved) == 1
    assert sha(path) == changed and not list(tmp_path.glob("*.db.bak")) and rows(path, "PRAGMA user_version") == [(2,)]


def flip_a_byte(path):
    """A writer that ignores SQLite's locks: bumps the file change counter in the header."""
    with open(path, "r+b") as handle:
        handle.seek(24)
        counter = int.from_bytes(handle.read(4), "big") + 1
        handle.seek(24)
        handle.write(counter.to_bytes(4, "big"))


def test_a_change_between_validation_and_the_transaction_is_refused(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    approved = sha(path)
    real = mig.open_readwrite

    def change_then_open(target, **kwargs):
        flip_a_byte(target)
        return real(target, **kwargs)
    monkeypatch.setattr(mig, "open_readwrite", change_then_open)
    assert apply_with(path, approved) == 1
    assert "changed since it was approved" in capsys.readouterr().err
    assert rows(path, "PRAGMA user_version") == [(2,)] and "confidence" in columns_of(path, "card_bid_details")
    assert not list(tmp_path.glob("*.db.bak"))  # no backup of a state nobody approved


def test_a_change_while_the_backup_is_written_is_refused_before_any_ddl(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    approved = sha(path)
    real = mig.backup_database

    def backup_then_change(conn, target, tag, **kwargs):
        result = real(conn, target, tag, **kwargs)
        flip_a_byte(target)
        return result
    monkeypatch.setattr(mig, "backup_database", backup_then_change)
    assert apply_with(path, approved) == 1
    assert "while it was being backed up" in capsys.readouterr().err
    assert rows(path, "PRAGMA user_version") == [(2,)] and "confidence" in columns_of(path, "card_bid_details")
    assert "confidence_level" not in columns_of(path, "cards")


@pytest.mark.parametrize("swap", ["rename", "symlink", "sidecar"])
def test_a_replaced_linked_or_sidecar_file_is_refused(tmp_path, monkeypatch, capsys, swap):
    env, path = make_v2(tmp_path)
    approved = sha(path)
    moved = tmp_path / "moved.db"
    real = mig.open_readwrite

    def swap_then_open(target, **kwargs):
        if swap == "rename":  # a different file with the same bytes takes the name
            os.rename(target, moved)
            Path(target).write_bytes(moved.read_bytes())
        elif swap == "symlink":
            os.rename(target, moved)
            os.symlink(moved, target)
        else:
            Path(target + "-journal").write_bytes(b"")
        return real(target, **kwargs)
    monkeypatch.setattr(mig, "open_readwrite", swap_then_open)
    assert apply_with(path, approved) == 1
    err = capsys.readouterr().err
    assert ("Unexpected state" in err) if swap == "sidecar" else ("not a regular file" in err or "no longer a regular file" in err or "different file" in err)
    assert not list(tmp_path.glob("*.db.bak"))
    original = moved if swap != "sidecar" else Path(path)
    assert rows(original, "PRAGMA user_version") == [(2,)] and "confidence" in columns_of(original, "card_bid_details")


@pytest.mark.parametrize("sidecar", ["-wal", "-shm", "-journal"])
def test_a_sidecar_present_at_the_start_is_refused(tmp_path, sidecar, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    Path(path + sidecar).write_bytes(b"")
    assert apply_with(path, before) == 1
    assert sha(path) == before and not list(tmp_path.glob("*.db.bak"))


def test_a_wal_mode_database_is_refused(tmp_path, capsys):
    env, path = make_v2(tmp_path)
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA journal_mode = wal").fetchone()[0] == "wal"
    con.close()
    assert not Path(path + "-wal").exists()  # closed cleanly: only the header says WAL
    before = sha(path)
    assert apply_with(path, before) == 1
    assert "WAL" in capsys.readouterr().err
    assert sha(path) == before and not list(tmp_path.glob("*.db.bak")) and not Path(path + "-wal").exists()
    assert rows(path, "PRAGMA user_version") == [(2,)]


def test_a_missing_target_is_never_created_by_apply(tmp_path):
    missing = tmp_path / "missing.db"
    assert apply_with(str(missing), "0" * 64) == 1 and not missing.exists()


def test_a_failure_after_the_ddl_rolls_everything_back_with_the_hash_protocol(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    approved = sha(path)
    real = mig.migrate

    def failing(conn, **kwargs):
        def boom(name):
            if name == "version set":
                raise RuntimeError("injected")
        return real(conn, _after_step=boom, **kwargs)
    monkeypatch.setattr(mig, "migrate", failing)
    with pytest.raises(RuntimeError, match="injected"):
        apply_with(path, approved)
    assert rows(path, "PRAGMA user_version") == [(2,)]
    assert "confidence" in columns_of(path, "card_bid_details") and "source_confidence" in columns_of(path, "card_source_refs")
    assert "confidence_level" not in columns_of(path, "cards")
    assert rows(path, "SELECT COUNT(*) FROM cards") == [(3,)] and rows(path, "PRAGMA integrity_check") == [("ok",)]
    backups = list(tmp_path.glob("*.db.bak"))  # written under the lock from the approved state, before the failure
    assert len(backups) == 1 and rows(backups[0], "PRAGMA user_version") == [(2,)]
    assert rows(backups[0], "SELECT confidence FROM card_bid_details") == [(70,)]


# --- hardened backup: content equivalence, failure artifacts, collisions, locking ----------------------------------

import subprocess
import sys
import threading

import migrate_stage_kind
from migrate_stage_kind import BackupError, backup_database

APPLY = ("--apply", "--confirm-app-stopped", "--expect-sha256")


def valid_backups(tmp_path):
    return [p for p in tmp_path.glob("*.db.bak") if "UNVERIFIED" not in p.name]


def unverified(tmp_path):
    return list(tmp_path.glob("*UNVERIFIED*.db.bak"))


def fingerprint(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return mig.content_fingerprint(con)
    finally:
        con.close()


def add_schema_objects(path):
    con = sqlite3.connect(path)
    con.execute("CREATE INDEX ix_cards_company ON cards(company)")
    con.execute("CREATE VIEW v_cards AS SELECT id, title FROM cards")
    con.execute("CREATE TRIGGER trg_noop AFTER DELETE ON cards BEGIN SELECT 1; END")
    con.commit()
    con.close()


def test_the_backup_is_content_equivalent_including_dropped_columns_and_schema_objects(tmp_path):
    env, path = make_v2(tmp_path)
    add_schema_objects(path)
    approved = fingerprint(path)
    assert run(path, *APPLY, sha(path)) == 0
    (backup,) = valid_backups(tmp_path)
    assert mig.fingerprint_differences(approved, fingerprint(backup)) == []
    assert rows(backup, "SELECT confidence FROM card_bid_details") == [(70,)]
    assert rows(backup, "SELECT source_confidence FROM card_source_refs") == [("HIGH (70%)",)]
    names = {r[0] for r in rows(backup, "SELECT name FROM sqlite_master")}
    assert {"ix_cards_company", "v_cards", "trg_noop"} <= names
    assert mig.fingerprint_differences(approved, fingerprint(path)) != []  # the migrated file is, as intended, different


def test_the_fingerprint_distinguishes_types_nulls_blobs_and_float_zero(tmp_path):
    def db(name, values):
        con = sqlite3.connect(tmp_path / name)
        con.execute("CREATE TABLE t (v)")  # no affinity: every storage class is kept as given
        con.executemany("INSERT INTO t VALUES (?)", [(v,) for v in values])
        con.commit()
        try:
            return mig.content_fingerprint(con)["digests"]["t"]
        finally:
            con.close()
    base = db("a.db", [1, 1.5, "1", b"1", None, "", b"", 0.0])
    assert db("same.db", [1, 1.5, "1", b"1", None, "", b"", 0.0]) == base
    for i, other in enumerate([1.0, 1, 1, "1", b"1", "", None, 0.0]):  # change one value to something that prints alike
        values = [1, 1.5, "1", b"1", None, "", b"", 0.0]
        values[i] = other if i != 6 else None
        if values != [1, 1.5, "1", b"1", None, "", b"", 0.0]:
            assert db(f"v{i}.db", values) != base, i
    assert db("negzero.db", [1, 1.5, "1", b"1", None, "", b"", -0.0]) != base
    assert db("blob_text.db", [1, 1.5, "1", "1", None, "", b"", 0.0]) != base


@pytest.mark.parametrize("tamper", ["dropped_column_value", "schema_object", "row_removed", "pragma"])
def test_verify_backup_rejects_a_copy_that_differs_anywhere_even_where_the_migration_does_not_look(tmp_path, tamper):
    env, path = make_v2(tmp_path)
    approved = fingerprint(path)
    copy = tmp_path / "copy.db"
    src = sqlite3.connect(path)
    dst = sqlite3.connect(copy)
    src.backup(dst)
    src.close()
    mig.verify_backup(str(copy), approved)  # a faithful copy passes
    if tamper == "dropped_column_value":
        dst.execute("UPDATE card_bid_details SET confidence = 71")
    elif tamper == "schema_object":
        dst.execute("CREATE INDEX extra ON cards(title)")
    elif tamper == "row_removed":
        dst.execute("DELETE FROM undo_entries")
    else:
        dst.execute("PRAGMA application_id = 5")
    dst.commit()
    dst.close()
    with pytest.raises(MigrationError, match="does not match the approved database"):
        mig.verify_backup(str(copy), approved)


def test_a_failed_verification_quarantines_the_new_artifact_and_names_it(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    real = mig.backup_database

    def backup_then_tamper(conn, target, tag, **kwargs):
        made = real(conn, target, tag, **kwargs)
        con = sqlite3.connect(made)
        con.execute("UPDATE card_bid_details SET confidence = 1")  # a column the migration drops: the old check missed this
        con.commit()
        con.close()
        return made
    monkeypatch.setattr(mig, "backup_database", backup_then_tamper)
    assert run(path, *APPLY, before) == 1
    err = capsys.readouterr().err
    (quarantined,) = unverified(tmp_path)
    assert valid_backups(tmp_path) == []
    assert "does not match the approved database" in err and "UNVERIFIED" in err and str(quarantined) in err and "do not restore" in err
    assert sha(path) == before and rows(path, "PRAGMA user_version") == [(2,)]


def test_a_backup_that_cannot_be_written_leaves_only_a_quarantined_partial_file(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)

    class Broken:
        def backup(self, target):
            target.execute("CREATE TABLE half (x)")
            raise sqlite3.OperationalError("disk full")

        def close(self):
            pass
    monkeypatch.setattr(mig, "open_readonly", lambda p: Broken())
    assert run(path, *APPLY, before) == 1
    err = capsys.readouterr().err
    assert "disk full" in err and "UNVERIFIED" in err and "database was not changed" in err
    assert len(unverified(tmp_path)) == 1 and valid_backups(tmp_path) == []
    assert sha(path) == before


def test_the_backup_file_changing_during_verification_is_not_trusted(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    real = mig.verify_backup

    def verify_then_change_the_backup(backup_path, source):
        real(backup_path, source)  # the content is fine...
        flip_a_byte(backup_path)  # ...but the file's bytes moved afterwards
    monkeypatch.setattr(mig, "verify_backup", verify_then_change_the_backup)
    assert run(path, *APPLY, before) == 1
    assert "changed while it was being verified" in capsys.readouterr().err
    assert len(unverified(tmp_path)) == 1 and valid_backups(tmp_path) == []
    assert sha(path) == before and rows(path, "PRAGMA user_version") == [(2,)]


def test_a_source_change_during_the_backup_quarantines_the_artifact(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    approved = sha(path)
    real = mig.backup_database

    def backup_then_change(conn, target, tag, **kwargs):
        made = real(conn, target, tag, **kwargs)
        flip_a_byte(target)
        return made
    monkeypatch.setattr(mig, "backup_database", backup_then_change)
    assert run(path, *APPLY, approved) == 1
    err = capsys.readouterr().err
    assert "while it was being backed up" in err and "UNVERIFIED" in err
    assert len(unverified(tmp_path)) == 1 and valid_backups(tmp_path) == []
    assert rows(path, "PRAGMA user_version") == [(2,)] and "confidence_level" not in columns_of(path, "cards")


def test_a_failure_after_the_ddl_says_the_backup_is_verified_and_the_database_unchanged(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    approved = sha(path)
    real = mig.migrate

    def failing(conn, **kwargs):
        def boom(name):
            if name == "version set":
                raise MigrationError("injected after DDL")
        return real(conn, _after_step=boom, **kwargs)
    monkeypatch.setattr(mig, "migrate", failing)
    assert run(path, *APPLY, approved) == 1
    err = capsys.readouterr().err
    (backup,) = valid_backups(tmp_path)
    assert "injected after DDL" in err and "rolled back, database unchanged" in err and "verified backup" in err and str(backup) in err
    assert unverified(tmp_path) == [] and sha(path) == approved
    assert mig.fingerprint_differences(fingerprint(path), fingerprint(backup)) == []  # the kept backup equals the untouched database


class _Frozen:
    @staticmethod
    def now():
        return datetime(2026, 5, 6, 7, 8, 9)


def test_a_pre_existing_backup_name_is_never_overwritten(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    monkeypatch.setattr(migrate_stage_kind, "datetime", _Frozen)
    existing = tmp_path / "lity-pre-confidence-level-20260506070809.db.bak"
    existing.write_bytes(b"an earlier, valid backup")
    assert run(path, *APPLY, before) == 1
    assert "already exists" in capsys.readouterr().err
    assert existing.read_bytes() == b"an earlier, valid backup"
    assert list(tmp_path.glob("*.bak")) == [existing] and sha(path) == before and rows(path, "PRAGMA user_version") == [(2,)]


def test_two_backups_in_the_same_second_the_second_is_refused_and_the_first_is_unchanged(tmp_path):
    env, path = make_v2(tmp_path)
    src = sqlite3.connect(path)
    first = backup_database(src, path, "confidence-level", now=_Frozen.now, exclusive=True)
    first_hash = sha(first)
    with pytest.raises(MigrationError, match="already exists"):
        backup_database(src, path, "confidence-level", now=_Frozen.now, exclusive=True)
    src.close()
    assert sha(first) == first_hash and len(list(tmp_path.glob("*.db.bak"))) == 1


def test_concurrent_attempts_at_the_same_name_create_exactly_one_backup(tmp_path):
    env, path = make_v2(tmp_path)
    outcomes = []
    barrier = threading.Barrier(6)

    def attempt():
        src = sqlite3.connect(path)
        barrier.wait()
        try:
            outcomes.append(backup_database(src, path, "confidence-level", now=_Frozen.now, exclusive=True))
        except MigrationError as exc:
            outcomes.append(exc)
        finally:
            src.close()
    threads = [threading.Thread(target=attempt) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(isinstance(o, str) for o in outcomes) == 1 and sum(isinstance(o, MigrationError) for o in outcomes) == 5
    (backup,) = tmp_path.glob("*.db.bak")
    assert rows(backup, "PRAGMA integrity_check") == [("ok",)] and rows(backup, "SELECT COUNT(*) FROM cards") == [(3,)]


def test_a_failed_copy_reports_the_artifact_it_created(tmp_path):
    env, path = make_v2(tmp_path)

    class Broken:
        def backup(self, target):
            raise sqlite3.OperationalError("boom")
    with pytest.raises(BackupError) as failed:
        backup_database(Broken(), path, "confidence-level", now=_Frozen.now, exclusive=True)
    assert Path(failed.value.path).exists() and failed.value.path in str(failed.value)


WRITER = """
import sqlite3, sys
con = sqlite3.connect(sys.argv[1], timeout=0, isolation_level=None)
try:
    con.execute("UPDATE cards SET title = 'written by another process'")
    print("WROTE")
except sqlite3.OperationalError as exc:
    print("REFUSED:", exc)
try:
    print("READ", con.execute("SELECT COUNT(*) FROM cards").fetchone()[0])
except sqlite3.OperationalError as exc:
    print("READ REFUSED:", exc)
"""


def test_a_real_second_process_cannot_write_at_any_point_while_the_migration_holds_its_lock(tmp_path, monkeypatch):
    """Regression for a found defect: hashing the database with open()/close() in the migrating process dropped SQLite's POSIX
    locks, so the write lock was gone by the time the backup was made. Probes run in a separate OS process at three points."""
    env, path = make_v2(tmp_path)
    approved = sha(path)
    titles = rows(path, "SELECT id, title FROM cards ORDER BY id")
    seen = {}

    def probe(label):
        seen[label] = subprocess.run([sys.executable, "-c", WRITER, path], capture_output=True, text=True, timeout=60).stdout

    real_backup, real_verify, real_migrate = mig.backup_database, mig.verify_backup, mig.migrate

    def backup(conn, target, tag, **kwargs):  # under BEGIN IMMEDIATE, after the hash checks, before the copy
        probe("before the copy")
        return real_backup(conn, target, tag, **kwargs)

    def verify(backup_path, source):  # after the copy and the fingerprint
        probe("after the copy")
        real_verify(backup_path, source)

    def migrating(conn, **kwargs):
        def step(name):
            if name == "version set":  # DDL done, not yet committed
                probe("after the DDL")
        return real_migrate(conn, _after_step=step, **kwargs)
    monkeypatch.setattr(mig, "backup_database", backup)
    monkeypatch.setattr(mig, "verify_backup", verify)
    monkeypatch.setattr(mig, "migrate", migrating)
    assert run(path, *APPLY, approved) == 0
    assert set(seen) == {"before the copy", "after the copy", "after the DDL"}
    for label, out in seen.items():
        assert "REFUSED: database is locked" in out and "WROTE" not in out, (label, out)
    assert "READ 3" in seen["before the copy"]  # a reader may read while only the write lock is held
    assert rows(path, "SELECT id, title FROM cards ORDER BY id") == titles  # the other process changed nothing
    assert rows(path, "PRAGMA user_version") == [(4,)]


@pytest.mark.parametrize("journal_mode", ["delete", "truncate", "persist", "memory", "off"])
def test_serialize_equals_the_file_bytes_under_the_write_lock_in_every_supported_journal_mode(tmp_path, journal_mode):
    env, path = make_v2(tmp_path)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute(f"PRAGMA journal_mode = {journal_mode}")
    conn.execute("BEGIN IMMEDIATE")
    try:
        assert mig._serialized_sha256(conn) == sha(path)
    finally:
        conn.execute("ROLLBACK")
        conn.close()


def test_serialize_is_not_the_file_in_wal_mode_which_is_why_wal_stays_refused(tmp_path):
    env, path = make_v2(tmp_path)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode = wal")
    conn.execute("PRAGMA wal_autocheckpoint = 0")
    conn.execute("UPDATE cards SET title = 'in the wal only' WHERE id = 1")  # committed to the -wal file, not the main file
    assert mig._serialized_sha256(conn) != sha(path)
    conn.close()


def test_a_locked_view_that_differs_from_the_approved_file_is_refused_before_any_backup(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    monkeypatch.setattr(mig, "_serialized_sha256", lambda conn: "0" * 64)
    assert run(path, *APPLY, before) == 1
    assert "serialized SHA-256 differs" in capsys.readouterr().err
    assert sha(path) == before and not list(tmp_path.glob("*.bak"))


def test_the_migration_requests_the_exclusive_backup_and_the_default_for_other_migrations_is_unchanged(tmp_path, monkeypatch):
    env, path = make_v2(tmp_path)
    calls = []
    real = mig.backup_database
    monkeypatch.setattr(mig, "backup_database", lambda conn, target, tag, **kw: calls.append(kw) or real(conn, target, tag, **kw))
    assert run(path, *APPLY, sha(path)) == 0 and calls == [{"exclusive": True}]
    src = sqlite3.connect(path)  # the shared helper's default still overwrites, as the older migrations expect
    first = backup_database(src, path, "other", now=_Frozen.now)
    assert backup_database(src, path, "other", now=_Frozen.now) == first
    src.close()


# --- M1: the lifecycle of the backup file after it exists --------------------------------------------------------------

import errno


class _Interrupted:
    """A read-only source whose copy writes something into the destination and is then interrupted."""
    def backup(self, target):
        target.execute("CREATE TABLE half (x)")
        raise KeyboardInterrupt

    def close(self):
        pass


class _Failing:
    def backup(self, target):
        target.execute("CREATE TABLE half (x)")
        raise sqlite3.OperationalError("disk full")

    def close(self):
        pass


def notes_of(exc):
    return " | ".join(getattr(exc, "__notes__", []))


def test_a_destination_that_cannot_be_opened_leaves_a_quarantined_empty_file_not_a_backup(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)

    class NoConnect:
        @staticmethod
        def connect(*args, **kwargs):
            raise sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(migrate_stage_kind, "sqlite3", NoConnect)
    assert run(path, *APPLY, before) == 1
    err = capsys.readouterr().err
    (artifact,) = unverified(tmp_path)
    assert "unable to open database file" in err and "UNVERIFIED" in err and "database was not changed" in err
    assert artifact.stat().st_size == 0 and valid_backups(tmp_path) == []
    assert sha(path) == before and rows(path, "PRAGMA user_version") == [(2,)]


def test_backup_database_reports_the_artifact_when_the_destination_cannot_be_opened(tmp_path, monkeypatch):
    env, path = make_v2(tmp_path)

    class NoConnect:
        @staticmethod
        def connect(*args, **kwargs):
            raise sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(migrate_stage_kind, "sqlite3", NoConnect)
    with pytest.raises(BackupError) as failed:
        backup_database(None, path, "confidence-level", now=_Frozen.now, exclusive=True)
    assert Path(failed.value.path).exists() and isinstance(failed.value.__cause__, sqlite3.OperationalError)


def test_an_interrupt_during_the_copy_quarantines_the_partial_backup_and_keeps_the_interrupt(tmp_path, monkeypatch):
    env, path = make_v2(tmp_path)
    before = sha(path)
    monkeypatch.setattr(mig, "open_readonly", lambda p: _Interrupted())
    with pytest.raises(KeyboardInterrupt) as interrupted:  # the original exception, not a wrapper
        run(path, *APPLY, before)
    notes = notes_of(interrupted.value)
    assert "UNVERIFIED" in notes and "quarantined" in notes
    assert "rolled back, database unchanged" in notes  # the interrupt happened under the lock, before any DDL
    assert len(unverified(tmp_path)) == 1 and valid_backups(tmp_path) == []
    assert sha(path) == before and rows(path, "PRAGMA user_version") == [(2,)]


def test_when_the_quarantine_itself_fails_the_error_names_the_artifact_that_stayed(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    monkeypatch.setattr(mig, "open_readonly", lambda p: _Failing())

    def refuse(src, dst, **kwargs):
        raise PermissionError(errno.EPERM, "Operation not permitted")
    monkeypatch.setattr(os, "link", refuse)
    assert run(path, *APPLY, before) == 1
    err = capsys.readouterr().err
    (left,) = tmp_path.glob("*.db.bak")  # still under its normal name: nothing could rename it
    assert "disk full" in err and str(left) in err and "Operation not permitted" in err
    assert "not a valid backup" in err and "do not restore" in err
    assert sha(path) == before


def test_when_the_original_name_cannot_be_removed_both_names_are_reported(tmp_path, monkeypatch):
    artifact = tmp_path / "x-pre-confidence-level-20260101000000.db.bak"
    artifact.write_bytes(b"partial")
    real_unlink = os.unlink

    def selective(target, *args, **kwargs):
        if str(target) == str(artifact):
            raise PermissionError(errno.EPERM, "Operation not permitted")
        return real_unlink(target, *args, **kwargs)
    monkeypatch.setattr(os, "unlink", selective)
    message = mig._quarantine(str(artifact))
    both = sorted(tmp_path.glob("*.db.bak"))
    assert len(both) == 2 and all(str(p) in message for p in both)
    assert "neither is a valid backup" in message


def test_the_quarantine_never_overwrites_an_existing_unverified_file(tmp_path):
    artifact = tmp_path / "x-pre-confidence-level-20260101000000.db.bak"
    artifact.write_bytes(b"new partial")
    earlier = tmp_path / "x-pre-confidence-level-20260101000000.UNVERIFIED.db.bak"
    earlier.write_bytes(b"an earlier quarantined file")
    message = mig._quarantine(str(artifact))
    assert earlier.read_bytes() == b"an earlier quarantined file"
    assert (tmp_path / "x-pre-confidence-level-20260101000000.UNVERIFIED2.db.bak").read_bytes() == b"new partial"
    assert "UNVERIFIED2" in message and not artifact.exists()


def test_an_os_error_creating_the_backup_creates_nothing_and_says_so(tmp_path, monkeypatch):
    env, path = make_v2(tmp_path)
    real_open = os.open

    def selective(target, *args, **kwargs):
        if str(target).endswith(".db.bak"):
            raise PermissionError(errno.EACCES, "Permission denied")
        return real_open(target, *args, **kwargs)
    monkeypatch.setattr(os, "open", selective)
    src = sqlite3.connect(path)
    with pytest.raises(MigrationError, match=r"could not be created \(Permission denied\); no backup file was created") as failed:
        backup_database(src, path, "confidence-level", now=_Frozen.now, exclusive=True)
    src.close()
    assert not isinstance(failed.value, BackupError) and isinstance(failed.value.__cause__, PermissionError)
    assert list(tmp_path.glob("*.bak")) == []


# --- M2: what the error says about the database ----------------------------------------------------------------------

class ConnProxy:
    """A real connection behind a wrapper that can fail around chosen statements (sqlite3.Connection cannot be patched)."""
    def __init__(self, conn, before=None, after=None, close_error=None):
        self._conn, self._before, self._after, self._close_error = conn, before, after, close_error

    def execute(self, sql, *args):
        if self._before:
            self._before(sql)
        result = self._conn.execute(sql, *args)
        if self._after:
            self._after(sql)
        return result

    def close(self):
        self._conn.close()
        if self._close_error:
            raise self._close_error

    def __getattr__(self, name):
        return getattr(self._conn, name)


def use_proxy(monkeypatch, **hooks):
    real = mig.open_readwrite
    monkeypatch.setattr(mig, "open_readwrite", lambda target, **kw: ConnProxy(real(target, **kw), **hooks))


def hook_on(statement, error):
    def hook(sql):
        if sql == statement:
            raise error
    return hook


def test_a_failure_before_the_commit_is_tagged_rolled_back_only_after_the_rollback_succeeded(tmp_path):
    env, path = make_v2(tmp_path)
    conn = sqlite3.connect(path, isolation_level=None)

    def boom(name):
        if name == "version set":
            raise RuntimeError("injected")
    with pytest.raises(RuntimeError) as failed:
        mig.migrate(conn, _after_step=boom)
    assert failed.value.db_state == mig.ROLLED_BACK and not conn.in_transaction
    conn.close()
    assert rows(path, "PRAGMA user_version") == [(2,)]


def test_a_failure_right_after_the_commit_says_the_database_is_migrated(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    approved = sha(path)
    real = mig.migrate

    def failing(conn, **kwargs):
        def boom(name):
            if name == "committed":
                raise MigrationError("injected after the commit")
        return real(conn, _after_step=boom, **kwargs)
    monkeypatch.setattr(mig, "migrate", failing)
    assert run(path, *APPLY, approved) == 1
    err = capsys.readouterr().err
    (backup,) = valid_backups(tmp_path)
    assert "injected after the commit" in err and "COMMITTED" in err and "IS migrated" in err and str(backup) in err
    assert "unchanged" not in err and "rolled back" not in err
    assert rows(path, "PRAGMA user_version") == [(4,)] and "confidence_level" in columns_of(path, "cards")  # the claim is true


def test_a_failure_building_the_report_after_the_commit_is_not_called_a_rollback(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    approved = sha(path)
    committed = {"done": False}
    real_migrate, real_snapshot = mig.migrate, mig.snapshot

    def migrating(conn, **kwargs):
        real_migrate(conn, **kwargs)
        committed["done"] = True

    def snapshot(conn):
        if committed["done"]:
            raise sqlite3.OperationalError("snapshot boom")
        return real_snapshot(conn)
    monkeypatch.setattr(mig, "migrate", migrating)
    monkeypatch.setattr(mig, "snapshot", snapshot)
    assert run(path, *APPLY, approved) == 1
    err = capsys.readouterr().err
    (backup,) = valid_backups(tmp_path)
    assert "snapshot boom" in err and "COMMITTED" in err and str(backup) in err
    assert "unchanged" not in err and "rolled back" not in err
    assert rows(path, "PRAGMA user_version") == [(4,)]


def test_a_commit_that_fails_leaving_the_transaction_open_is_rolled_back_with_evidence(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    approved = sha(path)
    use_proxy(monkeypatch, before=hook_on("COMMIT", sqlite3.OperationalError("database is locked")))
    assert run(path, *APPLY, approved) == 1
    err = capsys.readouterr().err
    (backup,) = valid_backups(tmp_path)
    assert "database is locked" in err and "rolled back, database unchanged" in err and str(backup) in err
    assert sha(path) == approved


def test_a_commit_whose_outcome_cannot_be_confirmed_does_not_presume_a_state(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    approved = sha(path)
    # the COMMIT really reaches the file, then the call reports an error and no transaction is left: the tool cannot know
    use_proxy(monkeypatch, after=hook_on("COMMIT", sqlite3.OperationalError("disk I/O error")))
    assert run(path, *APPLY, approved) == 1
    err = capsys.readouterr().err
    (backup,) = valid_backups(tmp_path)
    assert "disk I/O error" in err and "NOT confirmed" in err and "may or may not" in err and str(backup) in err
    assert "database unchanged" not in err and "rolled back" not in err and "IS migrated" not in err
    assert rows(path, "PRAGMA user_version") == [(4,)]  # in this injection it was in fact committed: the message did not claim otherwise


def test_a_failed_rollback_does_not_hide_the_original_error_nor_claim_a_state(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    approved = sha(path)

    def before(sql):
        if sql == "COMMIT":
            raise sqlite3.OperationalError("database is locked")
        if sql == "ROLLBACK":
            raise sqlite3.OperationalError("rollback failed")
    use_proxy(monkeypatch, before=before)
    assert run(path, *APPLY, approved) == 1
    err = capsys.readouterr().err
    assert err.splitlines()[0].endswith("database is locked")  # the original error is the one reported
    assert "ROLLBACK itself failed" in err and "NOT confirmed" in err and "database unchanged" not in err
    assert rows(path, "PRAGMA user_version") == [(2,)]  # closing the connection discarded the open transaction


def test_a_rollback_that_fails_before_any_commit_says_so_and_does_not_blame_the_commit(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    approved = sha(path)
    issued = []
    states = []
    real_annotate = mig._annotate
    monkeypatch.setattr(mig, "_annotate", lambda exc, backup_path: (states.append(getattr(exc, "db_state", None)), real_annotate(exc, backup_path))[1])

    def before(sql):
        issued.append(sql)
        if sql == f"PRAGMA user_version = {mig.COMPANY_SCHEMA_VERSION}":
            raise sqlite3.OperationalError("disk full")
        if sql == "ROLLBACK":
            raise sqlite3.OperationalError("rollback failed")
    use_proxy(monkeypatch, before=before)
    assert run(path, *APPLY, approved) == 1
    err = capsys.readouterr().err
    (backup,) = valid_backups(tmp_path)
    assert states == [mig.ROLLBACK_FAILED]  # the state recorded on the exception, not just the wording
    assert "ROLLBACK" in issued and "COMMIT" not in issued  # the ROLLBACK was tried and no COMMIT was ever issued
    assert err.splitlines()[0].endswith("disk full")  # the original error is the one reported
    assert "ROLLBACK itself failed" in err and "no COMMIT was attempted" in err and "NOT confirmed" in err and str(backup) in err
    assert "outcome of the COMMIT" not in err and "database unchanged" not in err and "IS migrated" not in err
    assert rows(path, "PRAGMA user_version") == [(2,)]  # closing the connection discarded the open transaction


def test_an_interrupt_right_after_the_commit_is_reported_as_unknown_and_stays_an_interrupt(tmp_path, monkeypatch):
    env, path = make_v2(tmp_path)
    use_proxy(monkeypatch, after=hook_on("COMMIT", KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt) as interrupted:
        run(path, *APPLY, sha(path))
    assert interrupted.value.db_state == mig.UNKNOWN
    notes = notes_of(interrupted.value)
    assert "NOT confirmed" in notes and "Do not run the migration again" in notes and "unchanged" not in notes


def test_a_begin_that_fails_is_reported_as_nothing_written(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    use_proxy(monkeypatch, before=hook_on("BEGIN IMMEDIATE", sqlite3.OperationalError("database is locked")))
    assert run(path, *APPLY, before) == 1
    err = capsys.readouterr().err
    assert "database is locked" in err and "no write was attempted" in err and "no verified backup was made" in err
    assert sha(path) == before and not list(tmp_path.glob("*.bak"))


def test_a_close_that_fails_during_another_error_is_a_note_not_a_replacement(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    use_proxy(monkeypatch, close_error=sqlite3.OperationalError("close failed"))
    monkeypatch.setattr(mig, "verify_backup", lambda *a: (_ for _ in ()).throw(MigrationError("backup mismatch")))
    assert run(path, *APPLY, before) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: backup mismatch")  # the real cause leads; the failed close is only a note
    assert "closing the database connection also failed" in err and "close failed" in err
    assert sha(path) == before


def test_a_close_that_fails_after_a_successful_migration_is_reported_as_committed(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    use_proxy(monkeypatch, close_error=sqlite3.OperationalError("close failed"))
    assert run(path, *APPLY, sha(path)) == 1
    err = capsys.readouterr().err
    assert "close failed" in err and "COMMITTED" in err and "unchanged" not in err
    assert rows(path, "PRAGMA user_version") == [(4,)]


# --- M3: file-system errors --------------------------------------------------------------------------------------------

def test_a_read_error_while_hashing_before_the_lock_is_explained_and_nothing_is_written(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)

    def broken(fd, size, offset):
        raise OSError(errno.EIO, "Input/output error")
    monkeypatch.setattr(os, "pread", broken)
    assert run(path, *APPLY, before) == 1
    err = capsys.readouterr().err
    assert "could not read" in err and path in err and "Input/output error" in err and "after 0 bytes" in err
    assert "the database was not changed (no write was attempted)" in err
    assert sha(path) == before and not list(tmp_path.glob("*.bak"))


def test_a_read_error_while_hashing_under_the_lock_rolls_back_before_any_backup(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    hashed = {"calls": 0}
    real_hash, real_pread = mig._hash_of, os.pread

    def counting(*args):
        hashed["calls"] += 1
        return real_hash(*args)

    def pread(fd, size, offset):
        if hashed["calls"] >= 2:  # the first hash (before the lock) works, the one under the lock fails
            raise OSError(errno.EIO, "Input/output error")
        return real_pread(fd, size, offset)
    monkeypatch.setattr(mig, "_hash_of", counting)
    monkeypatch.setattr(os, "pread", pread)
    assert run(path, *APPLY, before) == 1
    err = capsys.readouterr().err
    assert "Input/output error" in err and "rolled back, database unchanged" in err and "no verified backup was made" in err
    assert sha(path) == before and not list(tmp_path.glob("*.bak"))


def test_a_file_that_cannot_be_opened_for_hashing_is_explained(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    real_open = os.open

    def selective(target, *args, **kwargs):
        if str(target) == path:
            raise PermissionError(errno.EACCES, "Permission denied")
        return real_open(target, *args, **kwargs)
    monkeypatch.setattr(os, "open", selective)
    assert run(path, *APPLY, before) == 1
    err = capsys.readouterr().err
    assert "could not open" in err and "Permission denied" in err and "the database was not changed" in err
    assert sha(path) == before


def test_a_read_error_verifying_the_backup_quarantines_it_and_keeps_the_cause(tmp_path, monkeypatch, capsys):
    env, path = make_v2(tmp_path)
    before = sha(path)
    real = mig.sha256_file

    def selective(target):
        if str(target).endswith(".db.bak"):
            raise OSError(errno.EIO, "Input/output error", str(target))
        return real(target)
    monkeypatch.setattr(mig, "sha256_file", selective)
    assert run(path, *APPLY, before) == 1
    err = capsys.readouterr().err
    assert "Input/output error" in err and "UNVERIFIED" in err and "database was not changed" in err
    assert len(unverified(tmp_path)) == 1 and valid_backups(tmp_path) == [] and sha(path) == before


def test_a_programming_error_is_not_turned_into_a_filesystem_message(tmp_path, monkeypatch):
    env, path = make_v2(tmp_path)
    before = sha(path)

    def buggy(*args):
        raise TypeError("a bug, not an I/O problem")
    monkeypatch.setattr(mig, "_hash_of", buggy)
    with pytest.raises(TypeError, match="a bug") as failed:
        run(path, *APPLY, before)
    assert failed.value.db_state == mig.UNCHANGED  # only what is known: no write had been attempted
    assert sha(path) == before
