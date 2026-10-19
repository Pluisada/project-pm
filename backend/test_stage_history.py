"""Card stage history (card_stage_events): every path that changes a card's
stage, the transaction guarantees, undo, deletion, querying and the explicit
migration. Databases are in-memory or pytest tmp_path files - never pm.db."""
from auth_test_utils import SETUP_HEADERS, add_member
import hashlib
import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import migrate_stage_events as migration
from ai_kanban import apply_board_actions
from database import StageEventsMigrationRequired, get_db, require_stage_events_table
from main import app
from models import Base, Card, CardStageEvent, Column, User
from stage_history import card_stage_history


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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
    """Setup (admin + seeded sales board) and a member; returns ids/headers."""
    admin = client.post("/api/setup", json={"username": "boss", "password": "Password123!"}, headers=SETUP_HEADERS).json()["access_token"]
    add_member(client, "rep", "Password123!")
    member = client.post("/api/login", json={"username": "rep", "password": "Password123!"}).json()["access_token"]
    board = client.get("/api/boards", headers=auth(admin)).json()[0]
    detail = client.get(f"/api/boards/{board['id']}", headers=auth(admin)).json()
    cols = {c["title"]: c["id"] for c in detail["columns"]}
    return {
        "admin": auth(admin), "member": auth(member), "board": board["id"], "cols": cols,
        "admin_id": test_db.query(User).filter(User.username == "boss").one().id,
        "member_id": test_db.query(User).filter(User.username == "rep").one().id,
    }


def create(client, env, title="Lead", stage="1st Contact", who="member"):
    response = client.post(
        f"/api/boards/{env['board']}/cards",
        json={"column_id": env["cols"][stage], "title": title}, headers=env[who],
    )
    assert response.status_code == 201
    return response.json()["id"]


def move(client, env, card_id, stage, who="member", position=0):
    return client.put(
        f"/api/boards/{env['board']}/cards/{card_id}/position",
        json={"column_id": env["cols"][stage], "position": position}, headers=env[who],
    )


def events(db, card_id):
    return [
        (e.event_type, e.from_stage_kind, e.to_stage_kind, e.source)
        for e in card_stage_history(db, card_id)
    ]


def count(db):
    return db.query(CardStageEvent).count()


# ---------------------------------------------------------------------------
# Creation, moves, won / lost / reopened
# ---------------------------------------------------------------------------


class TestTransitions:
    def test_seeded_board_records_creation(self, env, test_db):
        seeded = test_db.query(CardStageEvent).filter(CardStageEvent.source == "seed").all()
        assert len(seeded) == test_db.query(Card).count() == 8
        assert {e.event_type for e in seeded} == {"created"}
        assert {e.user_id for e in seeded} == {env["admin_id"]}

    def test_create_records_initial_event(self, client, env, test_db):
        card_id = create(client, env)
        [event] = card_stage_history(test_db, card_id)
        assert (event.event_type, event.from_column_id, event.to_column_id) == (
            "created", None, env["cols"]["1st Contact"]
        )
        assert (event.to_stage_kind, event.source, event.user_id, event.board_id) == (
            "open", "api", env["member_id"], env["board"]
        )

    def test_move_between_open_stages(self, client, env, test_db):
        card_id = create(client, env)
        assert move(client, env, card_id, "RFI").status_code == 200
        last = card_stage_history(test_db, card_id)[-1]
        assert (last.event_type, last.from_column_id, last.to_column_id) == (
            "moved", env["cols"]["1st Contact"], env["cols"]["RFI"]
        )

    def test_move_to_won_and_lost(self, client, env, test_db):
        won, lost = create(client, env, "W"), create(client, env, "L")
        move(client, env, won, "Award")
        move(client, env, lost, "Lost")
        assert events(test_db, won)[-1] == ("won", "open", "won", "api")
        assert events(test_db, lost)[-1] == ("lost", "open", "lost", "api")

    def test_admin_reopens_a_won_deal(self, client, env, test_db):
        card_id = create(client, env)
        move(client, env, card_id, "Award")
        assert move(client, env, card_id, "Proposal", who="admin").status_code == 200
        assert events(test_db, card_id)[-1] == ("reopened", "won", "open", "api")

    def test_won_to_lost_is_a_loss(self, client, env, test_db):
        card_id = create(client, env)
        move(client, env, card_id, "Award")
        move(client, env, card_id, "Lost")  # forward for a member
        assert events(test_db, card_id)[-1] == ("lost", "won", "lost", "api")


# ---------------------------------------------------------------------------
# Changes that are not stage changes, and invalid requests
# ---------------------------------------------------------------------------


class TestNoEvent:
    def test_lead_field_edit(self, client, env, test_db):
        card_id = create(client, env)
        before = count(test_db)
        response = client.put(
            f"/api/boards/{env['board']}/cards/{card_id}",
            json={"title": "Renamed", "company": "Acme", "deal_value": 10, "currency": "USD"},
            headers=env["member"],
        )
        assert response.status_code == 200
        assert count(test_db) == before

    def test_reorder_within_same_column(self, client, env, test_db):
        card_id = create(client, env)
        before = count(test_db)
        assert move(client, env, card_id, "1st Contact", position=3).status_code == 200
        assert count(test_db) == before

    def test_invalid_requests(self, client, env, test_db):
        card_id = create(client, env)
        move(client, env, card_id, "Proposal")
        before = count(test_db)
        url = f"/api/boards/{env['board']}/cards/{card_id}/position"
        assert client.put(url, json={"column_id": 9999, "position": 0}, headers=env["member"]).status_code == 404
        assert move(client, env, card_id, "RFI").status_code == 403          # member backward
        assert client.put(url, json={"column_id": env["cols"]["Award"]}).status_code == 401
        assert client.delete(f"/api/boards/{env['board']}/cards/{card_id}", headers=env["member"]).status_code == 403
        assert client.put(url, json={"column_id": "x"}, headers=env["member"]).status_code == 422
        assert count(test_db) == before
        assert test_db.get(Card, card_id).column_id == env["cols"]["Proposal"]


# ---------------------------------------------------------------------------
# Transaction guarantees
# ---------------------------------------------------------------------------


class TestTransactions:
    def test_event_write_failure_rolls_back_the_move(self, client, env, test_db):
        card_id = create(client, env)
        test_db.execute(text("DROP TABLE card_stage_events"))
        test_db.commit()
        assert move(client, env, card_id, "RFI").status_code == 500
        test_db.rollback()
        test_db.expire_all()
        assert test_db.get(Card, card_id).column_id == env["cols"]["1st Contact"]

    def test_event_write_failure_rolls_back_the_create(self, client, env, test_db):
        before = test_db.query(Card).count()
        test_db.execute(text("DROP TABLE card_stage_events"))
        test_db.commit()
        response = client.post(
            f"/api/boards/{env['board']}/cards",
            json={"column_id": env["cols"]["1st Contact"], "title": "Never saved"},
            headers=env["member"],
        )
        assert response.status_code == 500
        test_db.rollback()
        assert test_db.query(Card).count() == before

    def test_exactly_one_event_per_move(self, client, env, test_db):
        card_id = create(client, env)
        for stage in ("F2F Meeting", "RFI", "Proposal", "Award"):
            before = count(test_db)
            move(client, env, card_id, stage)
            assert count(test_db) == before + 1
        assert [e[0] for e in events(test_db, card_id)] == ["created", "moved", "moved", "moved", "won"]


# ---------------------------------------------------------------------------
# AI path
# ---------------------------------------------------------------------------


class TestAiPath:
    @pytest.mark.asyncio
    async def test_ai_create_and_move(self, env, test_db):
        rep = test_db.get(User, env["member_id"])
        result = await apply_board_actions(
            test_db, env["board"],
            [{"type": "create", "column_id": env["cols"]["1st Contact"], "title": "From AI"}],
            rep,
        )
        card_id = result["successful"][0]["card_id"]
        await apply_board_actions(
            test_db, env["board"],
            [{"type": "move", "card_id": card_id, "column_id": env["cols"]["Award"]}], rep,
        )
        assert events(test_db, card_id) == [
            ("created", None, "open", "ai"), ("won", "open", "won", "ai"),
        ]
        assert {e.user_id for e in card_stage_history(test_db, card_id)} == {env["member_id"]}

    @pytest.mark.asyncio
    async def test_ai_cannot_backward_move_for_member(self, client, env, test_db):
        card_id = create(client, env)
        move(client, env, card_id, "Proposal")
        before = count(test_db)
        rep = test_db.get(User, env["member_id"])
        result = await apply_board_actions(
            test_db, env["board"],
            [{"type": "move", "card_id": card_id, "column_id": env["cols"]["1st Contact"]}], rep,
        )
        assert result["failed"] and count(test_db) == before


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


def undo(client, env, who="member"):
    return client.post(f"/api/boards/{env['board']}/undo", headers=env[who])


class TestUndo:
    def test_undo_move_adds_compensating_event(self, client, env, test_db):
        card_id = create(client, env)
        move(client, env, card_id, "Award")
        assert undo(client, env).status_code == 200
        history = events(test_db, card_id)
        assert history == [
            ("created", None, "open", "api"),
            ("won", "open", "won", "api"),          # original kept
            ("reopened", "won", "open", "undo"),    # compensation
        ]

    def test_undo_of_edit_records_nothing(self, client, env, test_db):
        card_id = create(client, env)
        client.put(f"/api/boards/{env['board']}/cards/{card_id}", json={"company": "X"}, headers=env["member"])
        before = count(test_db)
        assert undo(client, env).status_code == 200
        assert count(test_db) == before

    def test_undo_of_create_records_deletion(self, client, env, test_db):
        card_id = create(client, env)
        assert undo(client, env).status_code == 200
        assert test_db.get(Card, card_id) is None
        assert events(test_db, card_id) == [("created", None, "open", "api"), ("deleted", "open", None, "undo")]

    def test_undo_of_delete_records_new_creation(self, client, env, test_db):
        card_id = create(client, env, "Gone", who="admin")
        client.delete(f"/api/boards/{env['board']}/cards/{card_id}", headers=env["admin"])
        assert undo(client, env, who="admin").status_code == 200
        recreated = test_db.query(Card).filter(Card.title == "Gone").one()
        # Never the deleted card's id: SQLite would reuse it (it was the
        # highest), splicing the old history onto the new card.
        assert recreated.id != card_id
        assert events(test_db, card_id)[-1] == ("deleted", "open", None, "api")
        assert events(test_db, recreated.id) == [("created", None, "open", "undo")]

    def test_undo_after_someone_else_moved_the_card(self, client, env, test_db):
        card_id = create(client, env)
        move(client, env, card_id, "RFI")                    # member's change
        move(client, env, card_id, "Proposal", who="admin")  # someone else's, later
        before = card_stage_history(test_db, card_id)
        # Undo would overwrite the admin's later move, so it's refused (Part 22D
        # follow-up: undo checks the card is still as the change left it).
        assert undo(client, env).status_code == 409
        assert test_db.get(Card, card_id).column_id == env["cols"]["Proposal"]
        assert card_stage_history(test_db, card_id) == before  # no compensating event


# ---------------------------------------------------------------------------
# Deletion keeps history
# ---------------------------------------------------------------------------


class TestDeletion:
    def test_card_delete_keeps_history(self, client, env, test_db):
        card_id = create(client, env)
        move(client, env, card_id, "Award")
        client.delete(f"/api/boards/{env['board']}/cards/{card_id}", headers=env["admin"])
        assert test_db.get(Card, card_id) is None
        assert events(test_db, card_id) == [
            ("created", None, "open", "api"), ("won", "open", "won", "api"), ("deleted", "won", None, "api"),
        ]

    def test_column_delete_records_each_card(self, client, env, test_db):
        ids = [create(client, env, f"C{i}", stage="RFI") for i in range(3)]
        rfi = env["cols"]["RFI"]
        assert client.delete(f"/api/boards/{env['board']}/columns/{rfi}", headers=env["admin"]).status_code == 204
        for card_id in ids:
            last = card_stage_history(test_db, card_id)[-1]
            assert (last.event_type, last.from_column_id, last.board_id) == ("deleted", rfi, env["board"])

    def test_board_delete_records_every_card(self, client, env, test_db):
        cards = [c.id for c in test_db.query(Card).all()]
        assert client.delete(f"/api/boards/{env['board']}", headers=env["admin"]).status_code == 204
        deleted = {e.card_id for e in test_db.query(CardStageEvent).filter(CardStageEvent.event_type == "deleted")}
        assert deleted == set(cards)

    def test_every_event_points_to_a_card_or_ends_in_deleted(self, client, env, test_db):
        """No orphans: each card id in the history either exists or its last event is "deleted"."""
        card_id = create(client, env)
        client.delete(f"/api/boards/{env['board']}/cards/{card_id}", headers=env["admin"])
        for (cid,) in test_db.query(CardStageEvent.card_id).distinct():
            if test_db.get(Card, cid) is None:
                assert card_stage_history(test_db, cid)[-1].event_type == "deleted"


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------


class TestIdReuse:
    def test_new_card_never_reuses_a_deleted_cards_id(self, client, env, test_db):
        last = create(client, env, "Highest id", who="admin")
        client.delete(f"/api/boards/{env['board']}/cards/{last}", headers=env["admin"])
        newer = create(client, env, "Next")
        assert newer > last
        assert events(test_db, last)[-1][0] == "deleted"
        assert [e[0] for e in events(test_db, newer)] == ["created"]


class TestQuery:
    def test_chronological_order_with_insertion_tie_break(self, test_db):
        t0 = datetime(2026, 10, 1, 12, 0, 0)
        for occurred, kind in [(t0 + timedelta(hours=2), "won"), (t0, "created"), (t0, "moved")]:
            test_db.add(CardStageEvent(
                card_id=42, board_id=1, event_type=kind, to_column_id=1, to_stage_kind="open",
                source="system", occurred_at=occurred,
            ))
        test_db.commit()
        assert [e.event_type for e in card_stage_history(test_db, 42)] == ["created", "moved", "won"]

    def test_history_is_per_card(self, client, env, test_db):
        a, b = create(client, env, "A"), create(client, env, "B")
        move(client, env, a, "RFI")
        assert {e.card_id for e in card_stage_history(test_db, a)} == {a}
        assert len(card_stage_history(test_db, b)) == 1


# ---------------------------------------------------------------------------
# Migration of an existing database (temp files only)
# ---------------------------------------------------------------------------


def _pre_part18_db(path, with_stage_kind=True, orphan=False):
    """A database as it was after Part 17: no card_stage_events table."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE card_stage_events")
    if not with_stage_kind:
        conn.execute("ALTER TABLE columns DROP COLUMN stage_kind")
    conn.execute("INSERT INTO users (id, username, password_hash, role, created_at, updated_at) "
                 "VALUES (1, 'a', 'x', 'admin', '2026-01-01', '2026-01-01')")
    conn.execute("INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (1, 1, 'B', '2026-01-01', '2026-01-01')")
    kinds = [("New", "open"), ("Award", "won"), ("Lost", "lost")]
    for i, (title, kind) in enumerate(kinds, start=1):
        if with_stage_kind:
            conn.execute("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
                         "VALUES (?, 1, ?, ?, ?, '2026-01-01', '2026-01-01')", (i, title, i, kind))
        else:
            conn.execute("INSERT INTO columns (id, board_id, title, position, created_at, updated_at) "
                         "VALUES (?, 1, ?, ?, '2026-01-01', '2026-01-01')", (i, title, i))
    for card_id, column_id in [(1, 1), (2, 1), (3, 2), (4, 3)] + ([(5, 99)] if orphan else []):
        conn.execute("INSERT INTO cards (id, column_id, title, position, created_at, updated_at) "
                     "VALUES (?, ?, 'lead', 0, '2026-01-01', '2026-01-01')", (card_id, column_id))
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
    def test_app_refuses_existing_database_without_table(self, tmp_path):
        db = tmp_path / "old.db"
        _pre_part18_db(db)
        engine = create_engine(f"sqlite:///{db}")
        with pytest.raises(StageEventsMigrationRequired):
            require_stage_events_table(engine)
        assert "card_stage_events" not in {r[0] for r in sqlite3.connect(db).execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        engine.dispose()

    def test_fresh_database_is_not_refused(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'new.db'}")
        require_stage_events_table(engine)  # no cards table yet: create_all will make everything
        engine.dispose()

    def test_dry_run_changes_nothing(self, tmp_path, capsys):
        db = tmp_path / "old.db"
        _pre_part18_db(db)
        before = _sha(db)
        assert migration.main(["--database", str(db)]) == 0
        assert _sha(db) == before
        out = capsys.readouterr().out
        assert "nothing was changed" in out and "baseline events: 4" in out

    def test_apply_creates_baselines_and_preserves_data(self, tmp_path):
        db = tmp_path / "old.db"
        _pre_part18_db(db)
        original = _dump(db)
        assert migration.main(["--database", str(db), "--apply"]) == 0

        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT card_id, event_type, from_column_id, to_column_id, to_stage_kind, source, user_id "
            "FROM card_stage_events ORDER BY card_id"
        ).fetchall()
        assert rows == [
            (1, "baseline", None, 1, "open", "migration", None),
            (2, "baseline", None, 1, "open", "migration", None),
            (3, "baseline", None, 2, "won", "migration", None),
            (4, "baseline", None, 3, "lost", "migration", None),
        ]
        assert conn.execute("SELECT id, column_id FROM cards ORDER BY id").fetchall() == [(1, 1), (2, 1), (3, 2), (4, 3)]
        conn.close()

        [backup] = tmp_path.glob("old-pre-stage-events-*.db.bak")
        assert _dump(backup) == original

        engine = create_engine(f"sqlite:///{db}")
        require_stage_events_table(engine)  # the app accepts it now
        session = sessionmaker(bind=engine)()
        assert [e.event_type for e in card_stage_history(session, 3)] == ["baseline"]
        session.close()
        engine.dispose()

    def test_migrated_database_records_new_events(self, tmp_path):
        db = tmp_path / "old.db"
        _pre_part18_db(db)
        migration.main(["--database", str(db), "--apply"])
        engine = create_engine(f"sqlite:///{db}")
        session = sessionmaker(bind=engine)()
        card = session.get(Card, 1)
        card.column_id = 2
        session.commit()
        assert [(e.event_type, e.from_stage_kind) for e in card_stage_history(session, 1)] == [
            ("baseline", None), ("won", "open"),
        ]
        session.close()
        engine.dispose()

    def test_second_apply_refused(self, tmp_path):
        db = tmp_path / "old.db"
        _pre_part18_db(db)
        assert migration.main(["--database", str(db), "--apply"]) == 0
        before = _sha(db)
        assert migration.main(["--database", str(db), "--apply"]) == 1
        assert _sha(db) == before

    def test_requires_stage_kind_first(self, tmp_path):
        db = tmp_path / "older.db"
        _pre_part18_db(db, with_stage_kind=False)
        before = _sha(db)
        assert migration.main(["--database", str(db), "--apply"]) == 1
        assert _sha(db) == before
        assert not list(tmp_path.glob("*.db.bak"))

    def test_orphan_card_reported_not_invented(self, tmp_path, capsys):
        db = tmp_path / "old.db"
        _pre_part18_db(db, orphan=True)
        assert migration.main(["--database", str(db), "--apply"]) == 0
        assert "Card 5 points to a missing column" in capsys.readouterr().out
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM card_stage_events WHERE card_id = 5").fetchone()[0] == 0
        conn.close()

    def test_failure_rolls_back_and_backup_restores(self, tmp_path, monkeypatch):
        db = tmp_path / "old.db"
        _pre_part18_db(db)
        original = _dump(db)
        real = migration._snapshot
        calls = {"n": 0}

        def changed_snapshot(conn):
            calls["n"] += 1
            snap = real(conn)
            return snap if calls["n"] == 1 else ({**snap[0], "cards": -1}, snap[1])

        monkeypatch.setattr(migration, "_snapshot", changed_snapshot)
        with pytest.raises(migration.MigrationError, match="rolled back"):
            migration.apply(str(db))
        assert _dump(db) == original  # table creation and baselines undone

        # Recovery path: an applied migration is undone by restoring its backup.
        monkeypatch.setattr(migration, "_snapshot", real)
        _, backup = migration.apply(str(db))
        (tmp_path / "old.db").write_bytes(open(backup, "rb").read())
        assert _dump(db) == original
