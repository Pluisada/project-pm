"""Stage identity (columns.stage_kind), its migration, and the E2E database guard.

Every database here is in-memory or a pytest tmp_path file - never pm.db.
"""
import hashlib
import os
import sqlite3
import subprocess
import sys
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import migrate_stage_kind as migration
from auth import create_access_token, hash_password
from database import (
    DEVELOPMENT_DATABASES,
    E2EDatabaseError,
    StageKindMigrationRequired,
    check_e2e_database,
    get_db,
    require_stage_kind_column,
)
from lead_options import PIPELINE_STAGES
from main import app
from models import Base, Board, Card, Column, User, UserRole
from reports import build_board_report

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
TODAY = date(2026, 10, 13)


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


def _headers(db, username, role):
    db.add(User(username=username, password_hash=hash_password("password123"), role=role))
    db.commit()
    return {"Authorization": f"Bearer {create_access_token(data={'sub': username})}"}


@pytest.fixture
def admin(test_db):
    return _headers(test_db, "boss", UserRole.ADMIN.value)


@pytest.fixture
def member(test_db):
    return _headers(test_db, "rep", UserRole.MEMBER.value)


@pytest.fixture
def pipeline(test_db):
    """The default stages, with 2 won, 1 lost and 3 open leads (one closing soon)."""
    board = Board(user_id=1, title="Pipeline")
    test_db.add(board)
    test_db.flush()
    cols = {}
    for i, (title, kind) in enumerate(PIPELINE_STAGES):
        cols[title] = Column(board_id=board.id, title=title, position=i, stage_kind=kind)
        test_db.add(cols[title])
    test_db.flush()
    for stage, value, close in [
        ("1st Contact", 100, date(2026, 10, 20)),
        ("RFI", 200, None),
        ("Proposal", 300, None),
        ("Award", 400, None),
        ("Award", 500, None),
        ("Lost", 600, None),
    ]:
        test_db.add(Card(
            column_id=cols[stage].id, title=f"{stage} {value}", deal_value=value,
            currency="EUR", expected_close_date=close,
        ))
    test_db.commit()
    return board, cols


def _report(db, board):
    return build_board_report(db, board.id, today=TODAY)


# ---------------------------------------------------------------------------
# Reports follow stage_kind, not titles
# ---------------------------------------------------------------------------


class TestReportsUseStageKind:
    def test_baseline(self, test_db, pipeline):
        kpis = _report(test_db, pipeline[0])["kpis"]
        assert (kpis["won_count"], kpis["lost_count"], kpis["open_count"]) == (2, 1, 3)
        assert kpis["win_rate"] == pytest.approx(2 / 3)
        assert kpis["won_totals"] == {"EUR": 900}

    def test_renaming_award_keeps_won_count(self, client, admin, test_db, pipeline):
        board, cols = pipeline
        response = client.put(
            f"/api/boards/{board.id}/columns/{cols['Award'].id}", json={"title": "Fechado"}, headers=admin
        )
        assert response.status_code == 200
        assert response.json()["stage_kind"] == "won"
        kpis = _report(test_db, board)["kpis"]
        assert kpis["won_count"] == 2 and kpis["won_totals"] == {"EUR": 900}
        assert kpis["win_rate"] == pytest.approx(2 / 3)

    def test_renaming_lost_keeps_lost_count(self, client, admin, test_db, pipeline):
        board, cols = pipeline
        client.put(f"/api/boards/{board.id}/columns/{cols['Lost'].id}", json={"title": "Perdido"}, headers=admin)
        kpis = _report(test_db, board)["kpis"]
        assert kpis["lost_count"] == 1
        assert kpis["win_rate"] == pytest.approx(2 / 3)

    def test_titles_of_other_columns_dont_create_wins(self, client, admin, test_db, pipeline):
        """Naming an open stage "Award" or "Lost" doesn't make it won/lost."""
        board, cols = pipeline
        client.put(f"/api/boards/{board.id}/columns/{cols['RFI'].id}", json={"title": "Award"}, headers=admin)
        client.put(f"/api/boards/{board.id}/columns/{cols['Proposal'].id}", json={"title": "Lost"}, headers=admin)
        kpis = _report(test_db, board)["kpis"]
        assert (kpis["won_count"], kpis["lost_count"], kpis["open_count"]) == (2, 1, 3)

    def test_unknown_titles_are_open(self, test_db):
        board = Board(user_id=1, title="B")
        test_db.add(board)
        test_db.flush()
        col = Column(board_id=board.id, title="Won", position=0)  # no stage_kind given
        test_db.add(col)
        test_db.flush()
        test_db.add(Card(column_id=col.id, title="x", deal_value=1, currency="EUR"))
        test_db.commit()
        kpis = _report(test_db, board)["kpis"]
        assert col.stage_kind == "open"
        assert (kpis["won_count"], kpis["open_count"]) == (0, 1)

    def test_closing_soon_after_rename(self, client, admin, test_db, pipeline):
        board, cols = pipeline
        client.put(f"/api/boards/{board.id}/columns/{cols['1st Contact'].id}", json={"title": "Lead"}, headers=admin)
        soon = _report(test_db, board)["closing_soon"]
        assert [(lead["title"], lead["stage"]) for lead in soon] == [("1st Contact 100", "Lead")]

    def test_report_stages_expose_kind(self, test_db, pipeline):
        stages = _report(test_db, pipeline[0])["stages"]
        assert [s["stage_kind"] for s in stages] == [kind for _t, kind in PIPELINE_STAGES]


# ---------------------------------------------------------------------------
# Allowed stage_kind values and transitions
# ---------------------------------------------------------------------------


class TestStageKindRules:
    def test_member_cannot_rename_or_reclassify(self, client, member, test_db, pipeline):
        board, cols = pipeline
        url = f"/api/boards/{board.id}/columns/{cols['Award'].id}"
        assert client.put(url, json={"title": "x"}, headers=member).status_code == 403
        assert client.put(url, json={"stage_kind": "open"}, headers=member).status_code == 403
        test_db.refresh(cols["Award"])
        assert (cols["Award"].title, cols["Award"].stage_kind) == ("Award", "won")

    def test_invalid_kind_rejected(self, client, admin, pipeline):
        board, cols = pipeline
        url = f"/api/boards/{board.id}/columns/{cols['RFI'].id}"
        assert client.put(url, json={"stage_kind": "closed"}, headers=admin).status_code == 422

    def test_second_won_stage_rejected(self, client, admin, pipeline):
        board, _ = pipeline
        response = client.post(
            f"/api/boards/{board.id}/columns", json={"title": "Won too", "stage_kind": "won"}, headers=admin
        )
        assert response.status_code == 409

    def test_kind_of_column_with_cards_cannot_change(self, client, admin, test_db, pipeline):
        """No silent reclassification of the opportunities inside."""
        board, cols = pipeline
        url = f"/api/boards/{board.id}/columns/{cols['Award'].id}"
        assert client.put(url, json={"stage_kind": "open"}, headers=admin).status_code == 409
        test_db.refresh(cols["Award"])
        assert cols["Award"].stage_kind == "won"

    def test_kind_of_empty_column_can_change(self, client, admin, test_db):
        board = client.post("/api/boards", json={"title": "Fresh"}, headers=admin).json()
        col = client.post(f"/api/boards/{board['id']}/columns", json={"title": "Done"}, headers=admin).json()
        assert col["stage_kind"] == "open"
        url = f"/api/boards/{board['id']}/columns/{col['id']}"
        response = client.put(url, json={"stage_kind": "won"}, headers=admin)
        assert response.status_code == 200 and response.json()["stage_kind"] == "won"
        # And still only one won stage per board.
        second = client.post(
            f"/api/boards/{board['id']}/columns", json={"title": "Also won", "stage_kind": "won"}, headers=admin
        )
        assert second.status_code == 409

    def test_new_board_seeds_kinds(self, client):
        response = client.post("/api/setup", json={"username": "owner", "password": "password123"})
        token = response.json()["access_token"]
        board = client.get("/api/boards", headers={"Authorization": f"Bearer {token}"}).json()[0]
        detail = client.get(f"/api/boards/{board['id']}", headers={"Authorization": f"Bearer {token}"}).json()
        assert [(c["title"], c["stage_kind"]) for c in detail["columns"]] == PIPELINE_STAGES


# ---------------------------------------------------------------------------
# Migration of an existing database (never the development one)
# ---------------------------------------------------------------------------


def _legacy_db(path, titles_by_board):
    """A database in the pre-stage_kind schema, with one card per column."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE columns DROP COLUMN stage_kind")
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at, updated_at) "
        "VALUES (1, 'admin', 'x', 'admin', '2026-01-01', '2026-01-01')"
    )
    col_id = card_id = 0
    for board_id, titles in titles_by_board.items():
        conn.execute(
            "INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (?, 1, 'B', '2026-01-01', '2026-01-01')",
            (board_id,),
        )
        for position, title in enumerate(titles):
            col_id += 1
            conn.execute(
                "INSERT INTO columns (id, board_id, title, position, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, '2026-01-01', '2026-01-01')",
                (col_id, board_id, title, position),
            )
            card_id += 1
            conn.execute(
                "INSERT INTO cards (id, column_id, title, position, company, created_at, updated_at) "
                "VALUES (?, ?, ?, 0, 'Acme', '2026-01-01', '2026-01-01')",
                (card_id, col_id, f"lead {card_id}"),
            )
    conn.commit()
    conn.close()


def _digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _kinds(path):
    conn = sqlite3.connect(path)
    try:
        return dict(conn.execute("SELECT title || '#' || id, stage_kind FROM columns").fetchall())
    finally:
        conn.close()


STANDARD = {1: [title for title, _ in PIPELINE_STAGES]}


class TestMigration:
    def test_app_refuses_unmigrated_database(self, tmp_path):
        db = tmp_path / "old.db"
        _legacy_db(db, STANDARD)
        engine = create_engine(f"sqlite:///{db}")
        with pytest.raises(StageKindMigrationRequired):
            require_stage_kind_column(engine)
        engine.dispose()

    def test_dry_run_changes_nothing(self, tmp_path, capsys):
        db = tmp_path / "old.db"
        _legacy_db(db, STANDARD)
        before = _digest(db)
        assert migration.main(["--database", str(db)]) == 0
        assert _digest(db) == before
        assert "nothing was changed" in capsys.readouterr().out

    def test_apply_classifies_and_preserves_cards(self, tmp_path):
        db = tmp_path / "old.db"
        _legacy_db(db, STANDARD)
        conn = sqlite3.connect(db)
        cards_before = conn.execute("SELECT id, column_id, title, company FROM cards ORDER BY id").fetchall()
        conn.close()

        assert migration.main(["--database", str(db), "--apply"]) == 0

        kinds = _kinds(db)
        assert kinds == {
            "1st Contact#1": "open", "F2F Meeting#2": "open", "RFI#3": "open",
            "Proposal#4": "open", "Award#5": "won", "Lost#6": "lost",
        }
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT id, column_id, title, company FROM cards ORDER BY id").fetchall() == cards_before
        conn.close()
        backups = list(tmp_path.glob("old-pre-stage-kind-*.db.bak"))
        assert len(backups) == 1

        # The migrated database satisfies the app's startup check.
        engine = create_engine(f"sqlite:///{db}")
        require_stage_kind_column(engine)
        engine.dispose()

    def test_apply_twice_is_refused(self, tmp_path):
        db = tmp_path / "old.db"
        _legacy_db(db, STANDARD)
        assert migration.main(["--database", str(db), "--apply"]) == 0
        assert migration.main(["--database", str(db), "--apply"]) == 1

    def test_unknown_and_lookalike_titles_stay_open(self, tmp_path):
        db = tmp_path / "old.db"
        _legacy_db(db, {1: ["New", "award", " Lost ", "Won", "Fechado"]})
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        plan = migration.plan(conn)
        conn.close()
        assert {c["title"]: c["kind"] for c in plan["columns"]} == {
            "New": "open", "award": "open", " Lost ": "open", "Won": "open", "Fechado": "open",
        }
        assert len(plan["warnings"]) == 3 and plan["errors"] == []

    def test_duplicate_titles_are_ambiguous_until_set(self, tmp_path):
        db = tmp_path / "old.db"
        _legacy_db(db, {1: ["Award", "Award", "Lost"]})
        before = _digest(db)
        assert migration.main(["--database", str(db)]) == 2
        assert migration.main(["--database", str(db), "--apply"]) == 1
        assert _digest(db) == before  # refused before any change

        assert migration.main(["--database", str(db), "--apply", "--set", "1=won", "--set", "2=open"]) == 0
        assert _kinds(db) == {"Award#1": "won", "Award#2": "open", "Lost#3": "lost"}

    def test_overrides_cannot_create_two_won_stages(self, tmp_path):
        db = tmp_path / "old.db"
        _legacy_db(db, STANDARD)
        assert migration.main(["--database", str(db), "--apply", "--set", "3=won"]) == 1
        assert "stage_kind" not in {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(columns)")}

    def test_each_board_classified_separately(self, tmp_path):
        db = tmp_path / "old.db"
        _legacy_db(db, {1: ["Award", "Lost"], 2: ["Award", "Lost"]})
        assert migration.main(["--database", str(db), "--apply"]) == 0
        assert sorted(_kinds(db).values()) == ["lost", "lost", "won", "won"]


# ---------------------------------------------------------------------------
# E2E database guard (PM_E2E=1)
# ---------------------------------------------------------------------------


class TestE2EGuard:
    @pytest.mark.parametrize("url", [None, "", "sqlite:///:memory:", "postgresql://x/y"])
    def test_rejects_missing_or_non_file_urls(self, url):
        with pytest.raises(E2EDatabaseError):
            check_e2e_database(url)

    @pytest.mark.parametrize("db", DEVELOPMENT_DATABASES)
    def test_rejects_development_databases(self, db):
        with pytest.raises(E2EDatabaseError):
            check_e2e_database(f"sqlite:///{db}")

    def test_rejects_paths_outside_temp(self):
        with pytest.raises(E2EDatabaseError):
            check_e2e_database(f"sqlite:///{os.path.join(BACKEND_DIR, 'e2e.db')}")

    def test_accepts_temp_file(self, tmp_path):
        assert check_e2e_database(f"sqlite:///{tmp_path / 'e2e.db'}").endswith("e2e.db")

    def _import_main(self, extra_env):
        env = {**os.environ, **extra_env}
        return subprocess.run(
            [sys.executable, "-c", "import main"], cwd=BACKEND_DIR, env=env,
            capture_output=True, text=True, timeout=60,
        )

    def test_backend_refuses_e2e_mode_with_default_database(self):
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        env["PM_E2E"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", "import main"], cwd=BACKEND_DIR, env=env,
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode != 0
        assert "explicit DATABASE_URL" in result.stderr

    def test_backend_refuses_e2e_mode_with_dev_database(self):
        result = self._import_main({"PM_E2E": "1", "DATABASE_URL": "sqlite:///./pm.db"})
        assert result.returncode != 0
        assert "refuses the development database" in result.stderr

    def test_backend_accepts_e2e_mode_with_temp_database(self, tmp_path):
        result = self._import_main({"PM_E2E": "1", "DATABASE_URL": f"sqlite:///{tmp_path / 'e2e.db'}"})
        assert result.returncode == 0, result.stderr

    def test_health_reports_test_mode_off_normally(self, client):
        assert client.get("/api/health").json() == {"status": "ok", "test_mode": False}
