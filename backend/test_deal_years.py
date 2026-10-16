"""Annual Deal Value columns: migrate_deal_years.py, the init_db() guard and
migrate_deal_year_rollover.py. Every database here is a temp file under tmp_path."""
import hashlib
import sqlite3
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import database
import migrate_deal_year_rollover as rollover
import migrate_deal_years as migration
from database import DealYearsMigrationRequired
from models import Base, Board, Card, Column, User, UserRole


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _cli(module, path, *extra, apply=True):
    args = ["--database", str(path), *extra]
    if apply:
        args += ["--apply", "--expect-sha256", _sha(path)]
    return module.main(args)


def _q(path, sql):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


@pytest.fixture
def legacy(tmp_path):
    """A database from before the annual columns, with amounts in two currencies and one empty."""
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
        db.add_all([
            Card(column_id=column.id, title="BRL", position=0, deal_value=100000.0, currency="BRL"),
            Card(column_id=column.id, title="USD", position=1, deal_value=2500.5, currency="USD"),
            Card(column_id=column.id, title="Empty", position=2),
        ])
        db.commit()
    engine.dispose()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE cards DROP COLUMN deal_value_next_year")
    conn.execute("ALTER TABLE cards DROP COLUMN deal_year")
    conn.commit()
    conn.close()
    return path


def _old_dump(path):
    conn = sqlite3.connect(path)
    try:
        data = {}
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[1] not in migration.NEW_COLUMNS]
            data[table] = conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY rowid").fetchall()
        return data
    finally:
        conn.close()


def test_startup_refuses_it_without_touching_it(legacy, monkeypatch):
    engine = create_engine(f"sqlite:///{legacy}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "E2E_MODE", False)
    monkeypatch.delenv("PM_REQUIRE_EXISTING_DB", raising=False)
    before = _sha(legacy)
    with pytest.raises(DealYearsMigrationRequired, match="migrate_deal_years.py"):
        database.init_db()
    engine.dispose()
    assert _sha(legacy) == before


def test_dry_run_changes_nothing(legacy, capsys):
    before = _sha(legacy)
    assert _cli(migration, legacy, "--year", "2026", apply=False) == 0
    assert "Dry run" in capsys.readouterr().out
    assert _sha(legacy) == before


def test_apply_keeps_existing_amounts_as_current_year_and_adds_zero_next_year(legacy):
    before = _old_dump(legacy)
    assert _cli(migration, legacy, "--year", "2026") == 0

    assert _q(legacy, "SELECT title, deal_value, currency, deal_value_next_year, deal_year FROM cards ORDER BY id") == [
        ("BRL", 100000.0, "BRL", 0.0, 2026),
        ("USD", 2500.5, "USD", 0.0, 2026),
        ("Empty", None, None, 0.0, 2026),
    ]
    assert _old_dump(legacy) == before
    assert _q(legacy, "PRAGMA foreign_key_check") == []
    assert len(list(legacy.parent.glob("legacy-pre-deal-years-*.db.bak"))) == 1


def test_migrated_database_passes_the_startup_guard_and_a_new_card_gets_the_current_year(legacy, monkeypatch):
    _cli(migration, legacy, "--year", "2026")
    engine = create_engine(f"sqlite:///{legacy}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "E2E_MODE", False)
    monkeypatch.delenv("PM_REQUIRE_EXISTING_DB", raising=False)
    database.init_db()
    with sessionmaker(bind=engine)() as db:
        column = db.query(Column).first()
        card = Card(column_id=column.id, title="New", position=9)
        db.add(card)
        db.commit()
        assert (card.deal_value_next_year, card.deal_year) == (0, date.today().year)
    engine.dispose()


def test_second_apply_and_a_bad_year_are_refused(legacy, capsys):
    assert _cli(migration, legacy, "--year", "1999") == 1
    assert _cli(migration, legacy, "--year", "2026") == 0
    after = _sha(legacy)
    assert _cli(migration, legacy, "--year", "2026") == 1
    assert "nothing to do" in capsys.readouterr().err
    assert _sha(legacy) == after


def test_apply_needs_the_hash(legacy):
    assert migration.main(["--database", str(legacy), "--year", "2026", "--apply"]) == 1
    assert migration.main(["--database", str(legacy), "--year", "2026", "--apply", "--expect-sha256", "0" * 64]) == 1


# --- explicit year rollover ---

@pytest.fixture
def migrated(legacy, monkeypatch):
    _cli(migration, legacy, "--year", "2026")
    conn = sqlite3.connect(legacy)
    conn.execute("UPDATE cards SET deal_value_next_year = 50000 WHERE title = 'BRL'")
    conn.commit()
    conn.close()
    monkeypatch.setattr(rollover, "_today", lambda: date(2027, 1, 2))
    return legacy


def test_rollover_dry_run_changes_nothing(migrated, capsys):
    before = _sha(migrated)
    assert _cli(rollover, migrated, "--from-year", "2026", apply=False) == 0
    assert "3 card(s)" in capsys.readouterr().out
    assert _sha(migrated) == before


def test_rollover_moves_next_year_into_current_and_keeps_currency(migrated):
    assert _cli(rollover, migrated, "--from-year", "2026") == 0
    assert _q(migrated, "SELECT title, deal_value, currency, deal_value_next_year, deal_year FROM cards ORDER BY id") == [
        ("BRL", 50000.0, "BRL", 0.0, 2027),
        ("USD", None, "USD", 0.0, 2027),
        ("Empty", None, None, 0.0, 2027),
    ]
    assert len(list(migrated.parent.glob("legacy-pre-deal-year-rollover-*.db.bak"))) == 1
    # Applying it again for the same year finds no card left.
    assert _cli(rollover, migrated, "--from-year", "2026") == 1


def test_rollover_refuses_a_year_that_is_not_over(migrated, monkeypatch, capsys):
    monkeypatch.setattr(rollover, "_today", lambda: date(2026, 12, 31))
    before = _sha(migrated)
    assert _cli(rollover, migrated, "--from-year", "2026") == 1
    assert "isn't over yet" in capsys.readouterr().err
    assert _sha(migrated) == before


def test_rollover_refuses_an_unmigrated_database(legacy, monkeypatch, capsys):
    monkeypatch.setattr(rollover, "_today", lambda: date(2027, 1, 2))
    assert _cli(rollover, legacy, "--from-year", "2026", apply=False) == 1
    assert "migrate_deal_years.py" in capsys.readouterr().err
