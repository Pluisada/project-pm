"""Value snapshots on stage events (Part 20): recorded at event time by the
real app paths, used by the KPIs instead of the card's current value, and
added to existing databases by an explicit migration. In-memory and pytest
tmp_path databases only - never pm.db."""
import hashlib
import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import kpis
import migrate_stage_event_values as migration
from ai_kanban import apply_board_actions
from database import StageEventValuesMigrationRequired, get_db, require_stage_event_value_columns
from main import app
from models import Base, Card, CardStageEvent, User
from stage_history import card_stage_history

AS_OF = datetime(2030, 1, 1)
EVERYTHING = kpis.Period(datetime(2000, 1, 1), datetime(2100, 1, 1), "custom")


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def client(test_db):
    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def env(client, test_db):
    admin = client.post("/api/setup", json={"username": "boss", "password": "password123"}).json()["access_token"]
    board = client.get("/api/boards", headers=auth(admin)).json()[0]
    detail = client.get(f"/api/boards/{board['id']}", headers=auth(admin)).json()
    return {"h": auth(admin), "board": board["id"], "cols": {c["title"]: c["id"] for c in detail["columns"]}}


def new_card(client, env, value=None, currency=None, title="Deal"):
    card_id = client.post(
        f"/api/boards/{env['board']}/cards",
        json={"column_id": env["cols"]["1st Contact"], "title": title}, headers=env["h"],
    ).json()["id"]
    if value is not None or currency is not None:
        set_value(client, env, card_id, value, currency)
    return card_id


def set_value(client, env, card_id, value, currency):
    response = client.put(
        f"/api/boards/{env['board']}/cards/{card_id}",
        json={"deal_value": value, "currency": currency}, headers=env["h"],
    )
    assert response.status_code == 200


def move(client, env, card_id, stage):
    response = client.put(
        f"/api/boards/{env['board']}/cards/{card_id}/position",
        json={"column_id": env["cols"][stage], "position": 0}, headers=env["h"],
    )
    assert response.status_code == 200


def snapshots(db, card_id):
    return [
        (e.event_type, e.deal_value_snapshot, e.deal_currency_snapshot, e.value_snapshot_taken)
        for e in card_stage_history(db, card_id)
    ]


def won(db, board_id):
    return kpis.won_lost(kpis.load_board_history(db, board_id, AS_OF), EVERYTHING)


# ---------------------------------------------------------------------------
# 1-4, 12: snapshots recorded by the app
# ---------------------------------------------------------------------------


class TestSnapshotsRecorded:
    def test_won_event_records_value(self, client, env, test_db):
        card_id = new_card(client, env, 1250.5, "EUR")
        move(client, env, card_id, "Award")
        assert snapshots(test_db, card_id)[-1] == ("won", 1250.5, "EUR", True)

    def test_lost_event_records_value(self, client, env, test_db):
        card_id = new_card(client, env, 300, "USD")
        move(client, env, card_id, "Lost")
        assert snapshots(test_db, card_id)[-1] == ("lost", 300, "USD", True)

    def test_later_edit_keeps_closing_snapshot(self, client, env, test_db):
        card_id = new_card(client, env, 1000, "EUR")
        move(client, env, card_id, "Award")
        set_value(client, env, card_id, 5, "USD")
        assert snapshots(test_db, card_id)[-1] == ("won", 1000, "EUR", True)
        result = won(test_db, env["board"])["won"]
        assert result["values"] == {"EUR": {"total": 1000.0, "count": 1}}        # history
        assert result["current_values"] == {"USD": {"total": 5.0, "count": 1}}   # today, separate

    def test_each_transition_records_value_at_that_moment(self, client, env, test_db):
        card_id = new_card(client, env)              # created without value
        set_value(client, env, card_id, 10, "BRL")
        move(client, env, card_id, "RFI")
        set_value(client, env, card_id, 20, "BRL")
        move(client, env, card_id, "Award")
        assert snapshots(test_db, card_id) == [
            ("created", None, None, True),   # recorded: it genuinely had no value
            ("moved", 10, "BRL", True),
            ("won", 20, "BRL", True),
        ]

    def test_value_edit_alone_adds_no_event(self, client, env, test_db):
        card_id = new_card(client, env, 1, "EUR")
        before = test_db.query(CardStageEvent).count()
        set_value(client, env, card_id, 2, "EUR")
        assert test_db.query(CardStageEvent).count() == before

    def test_deleted_event_keeps_last_value(self, client, env, test_db):
        card_id = new_card(client, env, 77, "VND")
        move(client, env, card_id, "Award")
        client.delete(f"/api/boards/{env['board']}/cards/{card_id}", headers=env["h"])
        assert snapshots(test_db, card_id)[-1] == ("deleted", 77, "VND", True)
        result = won(test_db, env["board"])["won"]
        assert result["values"] == {"VND": {"total": 77.0, "count": 1}}   # history survives deletion
        assert result["current_without_value"] == 1                        # the card is gone

    @pytest.mark.asyncio
    async def test_ai_and_undo_record_snapshots(self, client, env, test_db):
        card_id = new_card(client, env, 40, "EUR")
        admin = test_db.query(User).filter(User.username == "boss").one()
        await apply_board_actions(
            test_db, env["board"], [{"type": "move", "card_id": card_id, "column_id": env["cols"]["Award"]}], admin
        )
        client.post(f"/api/boards/{env['board']}/undo", headers=env["h"])
        assert [(e.event_type, e.source, e.deal_value_snapshot) for e in card_stage_history(test_db, card_id)][-2:] == [
            ("won", "ai", 40), ("reopened", "undo", 40),
        ]

    def test_exactly_one_event_per_transition(self, client, env, test_db):
        card_id = new_card(client, env, 5, "EUR")
        for stage in ("F2F Meeting", "Award", "Lost"):
            before = test_db.query(CardStageEvent).count()
            move(client, env, card_id, stage)
            assert test_db.query(CardStageEvent).count() == before + 1


# ---------------------------------------------------------------------------
# 5-9: KPIs use the snapshots
# ---------------------------------------------------------------------------


class TestKpiValues:
    def test_currencies_stay_separate(self, client, env, test_db):
        for value, currency in [(0.1, "EUR"), (0.2, "EUR"), (99, "USD")]:
            card_id = new_card(client, env, value, currency)
            move(client, env, card_id, "Award")
        assert won(test_db, env["board"])["won"]["values"] == {
            "EUR": {"total": 0.3, "count": 2}, "USD": {"total": 99.0, "count": 1}}

    def test_events_without_snapshot_are_unavailable_not_invented(self, client, env, test_db):
        card_id = new_card(client, env, 500, "EUR")
        move(client, env, card_id, "Award")
        # Simulate an event written before snapshots existed.
        event = card_stage_history(test_db, card_id)[-1]
        event.value_snapshot_taken, event.deal_value_snapshot, event.deal_currency_snapshot = False, None, None
        test_db.commit()
        result = won(test_db, env["board"])["won"]
        assert result["values"] == {}                    # not the current 500 EUR
        assert (result["value_unavailable"], result["without_value"]) == (1, 0)
        assert result["current_values"] == {"EUR": {"total": 500.0, "count": 1}}

    def test_recorded_without_value_is_not_unavailable(self, client, env, test_db):
        card_id = new_card(client, env)
        move(client, env, card_id, "Award")
        result = won(test_db, env["board"])["won"]
        assert (result["without_value"], result["value_unavailable"], result["values"]) == (1, 0, {})

    def test_reopen_and_win_again_uses_latest_closing_value(self, client, env, test_db):
        card_id = new_card(client, env, 100, "EUR")
        move(client, env, card_id, "Award")
        move_back = client.put(
            f"/api/boards/{env['board']}/cards/{card_id}/position",
            json={"column_id": env["cols"]["Proposal"], "position": 0}, headers=env["h"],
        )
        assert move_back.status_code == 200                       # admin may reopen
        set_value(client, env, card_id, 140, "EUR")
        move(client, env, card_id, "Award")
        result = won(test_db, env["board"])
        assert (result["won"]["events"], result["won"]["opportunities"]) == (2, 1)
        assert result["won"]["values"] == {"EUR": {"total": 140.0, "count": 1}}   # once, latest closing
        assert result["reopened"]["events"] == 1

    def test_api_returns_historical_values(self, client, env, test_db, monkeypatch):
        monkeypatch.setattr(kpis, "utcnow", lambda: AS_OF)
        card_id = new_card(client, env, 800, "USD")
        move(client, env, card_id, "Lost")
        set_value(client, env, card_id, 1, "USD")
        body = client.get(
            f"/api/boards/{env['board']}/kpis/summary",
            params={"period": "custom", "start": "2000-01-01", "end": "2099-12-31"}, headers=env["h"],
        ).json()["current"]["lost"]
        assert body["values"] == {"USD": {"total": 800.0, "count": 1}}
        assert body["current_values"] == {"USD": {"total": 1.0, "count": 1}}
        assert (body["without_value"], body["value_unavailable"]) == (0, 0)


# ---------------------------------------------------------------------------
# 10-11: migration (disposable databases only)
# ---------------------------------------------------------------------------


def _part18_db(path):
    """A database as it was after Part 19: events without value columns."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(path)
    for column in ("deal_value_snapshot", "deal_currency_snapshot", "value_snapshot_taken"):
        conn.execute(f"ALTER TABLE card_stage_events DROP COLUMN {column}")
    conn.execute("INSERT INTO users (id, username, password_hash, role, created_at, updated_at) "
                 "VALUES (1, 'a', 'x', 'admin', '2026-01-01', '2026-01-01')")
    conn.execute("INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (1, 1, 'B', '2026-01-01', '2026-01-01')")
    conn.execute("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
                 "VALUES (1, 1, 'Award', 0, 'won', '2026-01-01', '2026-01-01')")
    conn.execute("INSERT INTO cards (id, column_id, title, position, deal_value, currency, created_at, updated_at) "
                 "VALUES (1, 1, 'lead', 0, 999, 'EUR', '2026-01-01', '2026-01-01')")
    for event_id, kind in [(1, "baseline"), (2, "won")]:
        conn.execute("INSERT INTO card_stage_events (id, card_id, board_id, event_type, to_column_id, to_stage_kind, "
                     "source, occurred_at) VALUES (?, 1, 1, ?, 1, 'won', 'migration', '2026-10-13 18:00:00')",
                     (event_id, kind))
    conn.commit()
    conn.close()


def _dump(path):
    conn = sqlite3.connect(path)
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


class TestMigration:
    def test_app_refuses_unmigrated_events_table(self, tmp_path):
        db = tmp_path / "old.db"
        _part18_db(db)
        engine = create_engine(f"sqlite:///{db}")
        with pytest.raises(StageEventValuesMigrationRequired):
            require_stage_event_value_columns(engine)
        engine.dispose()

    def test_dry_run_changes_nothing(self, tmp_path, capsys):
        db = tmp_path / "old.db"
        _part18_db(db)
        before = _sha(db)
        assert migration.main(["--database", str(db)]) == 0
        assert _sha(db) == before
        assert "nothing was changed" in capsys.readouterr().out

    def test_apply_preserves_everything_and_invents_nothing(self, tmp_path):
        db = tmp_path / "old.db"
        _part18_db(db)
        original = _dump(db)
        conn = sqlite3.connect(db)
        events_before = conn.execute("SELECT * FROM card_stage_events ORDER BY id").fetchall()
        counts_before = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in migration.TABLES]
        conn.close()

        assert migration.main(["--database", str(db), "--apply"]) == 0

        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT id, card_id, board_id, event_type, from_column_id, from_stage_kind, to_column_id, "
            "to_stage_kind, user_id, source, occurred_at FROM card_stage_events ORDER BY id"
        ).fetchall()
        assert rows == events_before
        assert [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in migration.TABLES] == counts_before
        assert conn.execute(
            "SELECT deal_value_snapshot, deal_currency_snapshot, value_snapshot_taken FROM card_stage_events"
        ).fetchall() == [(None, None, 0), (None, None, 0)]   # not the card's 999 EUR
        conn.close()
        [backup] = tmp_path.glob("old-pre-stage-event-values-*.db.bak")
        assert _dump(backup) == original

        engine = create_engine(f"sqlite:///{db}")
        require_stage_event_value_columns(engine)
        session = sessionmaker(bind=engine)()
        result = kpis.won_lost(kpis.load_board_history(session, 1, AS_OF), EVERYTHING)["won"]
        assert (result["values"], result["value_unavailable"]) == ({}, 1)
        session.close()
        engine.dispose()

    def test_second_apply_refused(self, tmp_path):
        db = tmp_path / "old.db"
        _part18_db(db)
        assert migration.main(["--database", str(db), "--apply"]) == 0
        before = _sha(db)
        assert migration.main(["--database", str(db), "--apply"]) == 1
        assert _sha(db) == before

    def test_rollback_and_backup_restore(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _part18_db(db)
        original = _dump(db)
        real = migration._snapshot
        calls = {"n": 0}

        def changed(conn, columns):
            calls["n"] += 1
            counts, rows = real(conn, columns)
            return (counts, rows) if calls["n"] == 1 else ({**counts, "cards": -1}, rows)

        monkeypatch.setattr(migration, "_snapshot", changed)
        with pytest.raises(migration.MigrationError, match="rolled back"):
            migration.apply(str(db))
        assert _dump(db) == original                    # ALTERs undone

        monkeypatch.setattr(migration, "_snapshot", real)
        _, backup = migration.apply(str(db))
        db.write_bytes(open(backup, "rb").read())       # recovery
        assert _dump(db) == original

    def test_requires_events_table(self, tmp_path):
        db = tmp_path / "older.db"
        _part18_db(db)
        conn = sqlite3.connect(db)
        conn.execute("DROP TABLE card_stage_events")
        conn.commit()
        conn.close()
        assert migration.main(["--database", str(db), "--apply"]) == 1
        assert not list(tmp_path.glob("*.db.bak"))


def test_part18_migration_baselines_record_value_at_their_own_instant(tmp_path):
    """migrate_stage_events.py writes a baseline "now", so the card's value now
    IS its value at that event - a recorded fact, unlike older events."""
    import migrate_stage_events

    db = tmp_path / "pre18.db"
    _part18_db(db)
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE card_stage_events")
    conn.commit()
    conn.close()
    assert migrate_stage_events.main(["--database", str(db), "--apply"]) == 0
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT event_type, deal_value_snapshot, deal_currency_snapshot, value_snapshot_taken FROM card_stage_events"
    ).fetchall() == [("baseline", 999.0, "EUR", 1)]
    conn.close()
    # Already has the value columns: the Part 20 migration has nothing to do.
    assert migration.main(["--database", str(db), "--apply"]) == 1
