"""commercial_import.py and db_target_guard.py on disposable databases and synthetic workbooks only."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

import commercial_import as ci
from bid_excel_reader import read_bid_excel
from bid_import_preview import RowDecision
from bid_import_revert import revert_preview
from bid_import_service import preview_bid_import
from db_target_guard import (
    TargetRefused, check_state, logical_dump, make_backup, restore_backup, sha256_file, validate_target,
)
from lead_options import PIPELINE_STAGES
from models import Base, Board, BoardColumn, ImportBatch, User
from test_bid_excel_reader import build, row as excel_row

EMAIL = "admin@example.com"
ROWS = (
    excel_row(**{"Código": "000101-01", "CPM": "Pessoa A"}),                                  # -> Proposal
    excel_row(**{"Código": "000102-01", "CAT": "WON", "Estágio": "AWARD GIVEN", "CPM": "Pessoa B"}),  # -> Award
)


class World:
    """A clean commercial-like database (one admin, one board, no cards) plus the approved inputs."""

    def __init__(self, tmp_path):
        self.dir = tmp_path
        self.db = tmp_path / "commercial.db"
        engine = create_engine(f"sqlite:///{self.db}")
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine)() as session:
            admin = User(username=EMAIL, email=EMAIL, password_hash="x", role="admin")
            session.add(admin)
            session.flush()
            board = Board(title="Pipeline", user_id=admin.id)
            session.add_all([board, *(BoardColumn(board=board, title=t, position=i, stage_kind=k)
                                             for i, (t, k) in enumerate(PIPELINE_STAGES))])
            session.commit()
        engine.dispose()
        self.xlsx = tmp_path / "bids.xlsx"
        self.xlsx.write_bytes(build(list(ROWS)))
        self.decisions = tmp_path / "decisions.json"
        self.decisions.write_text("[]")
        self.preview = tmp_path / "approved-preview.json"
        self.write_preview()
        self.pre_import_sha = sha256_file(self.db)

    def write_preview(self, mutate=None):
        engine = create_engine(f"sqlite:///{self.db}")
        with Session(engine) as session:
            result = read_bid_excel(self.xlsx.read_bytes(), "bids.xlsx")
            preview = preview_bid_import(session, result, 1, ())
        engine.dispose()
        iso = lambda v: v.isoformat() if hasattr(v, "isoformat") else v
        records = [{
            "source_row": r.row_number, "source_key": r.source_key, "action": r.action.value, "proposed_stage": r.target_stage,
            "card": dict(r.fields.card), "bid_details": {k: iso(v) for k, v in r.fields.bid_details.items()},
            "source_ref": {k: iso(v) for k, v in r.fields.source_ref.items()},
        } for r in preview.rows]
        if mutate:
            mutate(records)
        self.preview.write_text(json.dumps({"records": records}))

    def args(self, **over):
        values = dict(
            database=str(self.db), expect_db_sha256=self.pre_import_sha, admin_email=EMAIL, workbook=str(self.xlsx),
            expect_workbook_sha256=sha256_file(self.xlsx), preview_path=str(self.preview),
            expect_preview_sha256=sha256_file(self.preview), decisions_path=str(self.decisions), operator="cli:test",
        )
        values.update(over)
        return values

    def query(self, sql):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def run(world, **over):
    apply = over.pop("apply", False)
    return ci.run_import(**world.args(**over), apply=apply)


# --- the happy path, its dry run and the repeat --------------------------------------------------------

def test_dry_run_writes_nothing(world):
    before = sha256_file(world.db)
    report = run(world)
    assert report["result"] == "dry_run" and report["preview_matches_approved"] is True
    assert report["preview_actions"]["create"] == 2
    assert sha256_file(world.db) == before


def test_apply_imports_exactly_the_approved_preview_and_verifies(world):
    report = run(world, apply=True)
    assert (report["result"], report["created"]) == ("applied", 2)
    verification = ci.verify_import(str(world.db), str(world.preview))
    assert ci.verification_failures(verification) == []
    assert verification["stage_counts"]["Proposal"] == 1 and verification["stage_counts"]["Award"] == 1
    assert verification["cpm_populated"] == 2 and verification["one_baseline_import_event_per_card"] is True


def test_nothing_is_assigned_and_no_user_is_created(world):
    run(world, apply=True)
    assert world.query("SELECT COUNT(*) FROM users") == [(1,)]
    assert world.query("SELECT COUNT(*) FROM cards WHERE salesperson_id IS NOT NULL") == [(0,)]
    assert world.query("SELECT COUNT(*) FROM audit_events") == [(0,)]


def test_operator_and_administrator_are_recorded_separately(world):
    report = run(world, apply=True, operator="cli:ops")
    [(user_id, mapping)] = world.query("SELECT user_id, mapping_json FROM import_batches")
    assert user_id == report["administrator_id"] == 1 and json.loads(mapping)["operator"] == "cli:ops"


def test_a_repeat_is_a_no_op_that_leaves_the_file_byte_identical(world):
    run(world, apply=True)
    after_first = sha256_file(world.db)
    batches = world.query("SELECT COUNT(*) FROM import_batches")
    report = ci.run_import(**world.args(expect_db_sha256=after_first), apply=True)
    assert report["result"] == "no_op" and report["batch_id"] is None and report["repeat"] is True
    assert sha256_file(world.db) == after_first
    assert world.query("SELECT COUNT(*) FROM import_batches") == batches
    # and the first batch is still revertible: the repeat left no later batch on its cards
    engine = create_engine(f"sqlite:///{world.db}")
    with Session(engine) as session:
        assert revert_preview(session, 1).status == "ready"
    engine.dispose()


def test_a_repeat_dry_run_reports_nothing_to_do(world):
    run(world, apply=True)
    report = ci.run_import(**world.args(expect_db_sha256=sha256_file(world.db)))
    assert report["result"] == "nothing_to_do"


# --- refusals: each leaves the database byte-identical ------------------------------------------------

def refused(world, match, **over):
    before = sha256_file(world.db)
    with pytest.raises(Exception, match=match):
        run(world, apply=True, **over)
    assert sha256_file(world.db) == before
    assert world.query("SELECT COUNT(*) FROM cards") == [(0,)] and world.query("SELECT COUNT(*) FROM import_batches") == [(0,)]


def test_wrong_database_hash(world):
    refused(world, "SHA-256", expect_db_sha256="0" * 64)


def test_wrong_workbook_hash(world):
    refused(world, "workbook", expect_workbook_sha256="0" * 64)


def test_wrong_preview_hash(world):
    refused(world, "approved preview", expect_preview_sha256="0" * 64)


def test_wrong_administrator(world):
    refused(world, "expected administrator", admin_email="someone.else@example.com")


def test_a_second_user_is_not_the_expected_state(world):
    conn = sqlite3.connect(world.db)
    conn.execute("INSERT INTO users (username, email, password_hash, role, session_version, created_at, updated_at) VALUES ('m','m@example.com','x','member',0,'2026-01-01','2026-01-01')")
    conn.commit()
    conn.close()
    refused(world, "exactly one user", expect_db_sha256=sha256_file(world.db))


def test_a_database_without_cpm_is_refused(world):
    conn = sqlite3.connect(world.db)
    conn.execute("ALTER TABLE card_bid_details DROP COLUMN cpm")
    conn.commit()
    conn.close()
    refused(world, "cpm", expect_db_sha256=sha256_file(world.db))


def test_a_preview_that_differs_from_the_approved_one_is_refused(world):
    world.write_preview(lambda records: records[0].update(proposed_stage="Lost"))
    refused(world, "differs from the approved preview")


def test_a_changed_workbook_differs_from_the_approved_preview(world):
    world.xlsx.write_bytes(build([ROWS[0], excel_row(**{"Código": "000102-01", "Cliente": "OUTRO"})]))
    refused(world, "differs from the approved preview", expect_workbook_sha256=sha256_file(world.xlsx))


def test_a_database_that_already_holds_part_of_the_import_is_refused(world):
    engine = create_engine(f"sqlite:///{world.db}")
    with Session(engine) as session:  # import row 1 alone, through the service, then try the full approved set
        from bid_import_service import apply_bid_import
        result = read_bid_excel(build([ROWS[0]]), "x.xlsx")
        apply_bid_import(session, result, preview_bid_import(session, result, 1, ()), (), 1, session.get(User, 1))
    engine.dispose()
    pinned = sha256_file(world.db)
    with pytest.raises(TargetRefused, match="differs from the approved preview"):
        run(world, apply=True, expect_db_sha256=pinned)
    assert sha256_file(world.db) == pinned


def test_a_file_named_pm_db_is_never_the_target(world, tmp_path):
    named = tmp_path / "sub" / "pm.db"
    named.parent.mkdir()
    named.write_bytes(world.db.read_bytes())
    refused_target = {"database": str(named), "expect_db_sha256": sha256_file(named)}
    with pytest.raises(TargetRefused, match="pm.db"):
        ci.run_import(**world.args(**refused_target), apply=True)


def test_a_missing_or_symlinked_database_is_never_created_or_followed(world, tmp_path):
    with pytest.raises(TargetRefused, match="never created"):
        ci.run_import(**world.args(database=str(tmp_path / "missing.db")), apply=True)
    assert not (tmp_path / "missing.db").exists()
    link = tmp_path / "link.db"
    link.symlink_to(world.db)
    with pytest.raises(TargetRefused, match="symlink"):
        ci.run_import(**world.args(database=str(link)), apply=True)


def test_nothing_is_redirected_through_the_environment(world, monkeypatch, tmp_path):
    decoy = tmp_path / "decoy.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{decoy}")
    run(world, apply=True)
    assert not decoy.exists() and world.query("SELECT COUNT(*) FROM cards") == [(2,)]
    result = subprocess.run(
        [sys.executable, "-c", "import commercial_import, sys; print('database' in sys.modules)"],
        cwd=Path(__file__).parent, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "False"


# --- failure: all or nothing --------------------------------------------------------------------------------

def test_a_failure_before_commit_rolls_everything_back(world):
    with pytest.raises(RuntimeError, match="injected"):
        run(world, apply=True, inject_failure="injected")
    assert world.query("SELECT COUNT(*) FROM cards") == [(0,)]
    assert world.query("SELECT COUNT(*) FROM card_stage_events") == [(0,)]
    assert world.query("SELECT status, failure_kind FROM import_batches") == [("failed", "unexpected")]
    assert json.loads(world.query("SELECT mapping_json FROM import_batches")[0][0])["operator"] == "cli:test"
    assert world.query("PRAGMA integrity_check") == [("ok",)] and world.query("PRAGMA foreign_key_check") == []


# --- backup and restore ---------------------------------------------------------------------------------------

def test_backup_restore_round_trip_returns_the_exact_logical_state(world, tmp_path):
    backup = make_backup(str(world.db), str(tmp_path / "backup.db"))
    assert backup["logical_dump_equal"] is True
    before = logical_dump(str(world.db))
    run(world, apply=True)
    assert logical_dump(str(world.db)) != before
    restored = restore_backup(str(tmp_path / "backup.db"), str(tmp_path / "restored.db"))
    assert restored["logical_dump_equal"] is True and logical_dump(str(tmp_path / "restored.db")) == before
    with pytest.raises(TargetRefused, match="already exists"):
        restore_backup(str(tmp_path / "backup.db"), str(tmp_path / "restored.db"))
    with pytest.raises(TargetRefused, match="already exists"):
        make_backup(str(world.db), str(tmp_path / "backup.db"))


def test_ledger_revert_is_not_the_same_as_restoring_the_backup(world):
    """The revert removes the cards but keeps their history, the batch and its ledger."""
    from bid_import_revert import execute_revert

    run(world, apply=True)
    engine = create_engine(f"sqlite:///{world.db}")
    with Session(engine) as session:
        plan = revert_preview(session, 1)
        assert plan.status == "ready"
        execute_revert(session, 1, session.get(User, 1), plan.plan_fingerprint)
    engine.dispose()
    assert world.query("SELECT COUNT(*) FROM cards") == [(0,)]
    assert world.query("SELECT COUNT(*) FROM import_batches") == [(1,)]
    assert world.query("SELECT COUNT(*) FROM card_stage_events") == [(4,)]  # 2 baseline + 2 deleted


def test_check_state_reports_counts_and_refuses_unexpected_ones(world):
    assert check_state(str(world.db), expect_cpm=True, admin_email=EMAIL, expected_counts={"cards": 0})["users"] == 1
    with pytest.raises(TargetRefused, match="cards has 0"):
        check_state(str(world.db), expect_cpm=True, admin_email=EMAIL, expected_counts={"cards": 1})
    with pytest.raises(TargetRefused, match="expected absent"):
        check_state(str(world.db), expect_cpm=False, admin_email=None)


# --- the CLI ---------------------------------------------------------------------------------------------------

def test_cli_check_and_import_exit_codes(world, capsys):
    base = ["--database", str(world.db), "--expect-db-sha256", world.pre_import_sha, "--admin-email", EMAIL]
    assert ci.main(["check", *base, "--cpm", "present"]) == 0
    assert ci.main(["check", *base, "--cpm", "absent"]) == 1
    importing = ["import", *base, "--workbook", str(world.xlsx), "--expect-workbook-sha256", sha256_file(world.xlsx),
                 "--preview", str(world.preview), "--expect-preview-sha256", sha256_file(world.preview),
                 "--decisions", str(world.decisions)]
    assert ci.main(importing) == 0 and "dry_run" in capsys.readouterr().out
    assert world.query("SELECT COUNT(*) FROM cards") == [(0,)]
    assert ci.main([*importing, "--apply"]) == 0
    assert world.query("SELECT COUNT(*) FROM cards") == [(2,)]
    assert ci.main(["verify", "--database", str(world.db), "--preview", str(world.preview),
                    "--expect-preview-sha256", sha256_file(world.preview)]) == 0
    assert ci.main([*importing, "--apply"]) == 1  # the pinned pre-import hash no longer matches: refused
    assert "SHA-256" in capsys.readouterr().err
