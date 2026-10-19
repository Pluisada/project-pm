"""create_commercial_db.py - tests use only throwaway databases in tmp_path."""
import hashlib
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text

import create_commercial_db as ccd
from auth import UNUSABLE_PASSWORD_HASH, verify_password
from database import DEVELOPMENT_DATABASES
from lead_options import PIPELINE_STAGES

EMAIL = "boss@example.com"
NEW_PASSWORD = "Correct-horse-battery-1!"


def run(path, email=EMAIL):
    return ccd.main(["--database", str(path), "--email", email])


def rows(path, sql):
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).fetchall()
    finally:
        engine.dispose()


def test_creates_schema_admin_board_and_stages_without_cards(tmp_path):
    path = tmp_path / "commercial.db"
    assert run(path) == 0

    ((username, email, pw_hash, role, verified),) = rows(
        path, "SELECT username, email, password_hash, role, email_verified_at FROM users"
    )
    assert (username, email, role) == (EMAIL, EMAIL, "admin")
    # No usable initial password, and the email is unverified until the owner resets it.
    assert pw_hash == UNUSABLE_PASSWORD_HASH and verified is None
    assert not verify_password("", pw_hash) and not verify_password(UNUSABLE_PASSWORD_HASH, pw_hash)

    ((board_id, title),) = rows(path, "SELECT id, title FROM boards")
    assert title == "Salcomp Sales Pipeline"
    stages = rows(path, f"SELECT title, stage_kind FROM columns WHERE board_id = {board_id} ORDER BY position")
    assert stages == PIPELINE_STAGES

    for table in ("cards", "card_stage_events", "card_edit_history", "undo_entries",
                  "import_batches", "card_bid_details", "card_source_refs", "card_import_changes"):
        assert rows(path, f"SELECT COUNT(*) FROM {table}") == [(0,)], table


def test_refuses_existing_file_and_leaves_it_untouched(tmp_path, capsys):
    path = tmp_path / "existing.db"
    path.write_bytes(b"precious")
    assert run(path) == 1
    assert "already exists" in capsys.readouterr().err
    assert path.read_bytes() == b"precious"


def test_refuses_existing_symlink(tmp_path):
    target = tmp_path / "target.db"
    link = tmp_path / "link.db"
    link.symlink_to(target)
    assert run(link) == 1
    assert not target.exists()


@pytest.mark.parametrize("bad", ["sqlite:///x.db", ":memory:"])
def test_refuses_urls_and_memory(bad):
    assert run(bad) == 1


def test_refuses_missing_directory(tmp_path):
    assert run(tmp_path / "nope" / "c.db") == 1


def test_refuses_development_database_path():
    with pytest.raises(ccd.CommercialDatabaseError):
        ccd.check_target(DEVELOPMENT_DATABASES[0])


@pytest.mark.parametrize("bad", ["ab", "boss", "Boss@Example.com", "e2e_admin"])
def test_invalid_admin_identity_leaves_no_file(tmp_path, bad):
    path = tmp_path / "c.db"
    assert run(path, email=bad) == 1
    assert not path.exists()


def test_no_password_option_exists():
    # Neither an argument, a prompt nor stdin: the admin never gets an initial password.
    for flag in ("--password", "--password-stdin", "--username"):
        with pytest.raises(SystemExit):
            ccd.main(["--database", "x", "--email", EMAIL, flag, "secret"])


def test_app_runs_on_new_database_and_dev_database_is_untouched(tmp_path):
    """A separate process pointed at the new file by DATABASE_URL starts with
    no migration, reports setup done, logs the admin in and sees empty stages."""
    dev_hashes = {
        p: hashlib.sha256(open(p, "rb").read()).hexdigest()
        for p in DEVELOPMENT_DATABASES if os.path.exists(p)
    }
    path = tmp_path / "commercial.db"
    assert run(path) == 0

    script = """
import json
from datetime import timedelta
from fastapi.testclient import TestClient
import token_store
from database import SessionLocal
from main import app
from models import User
with TestClient(app) as client:
    status = client.get("/api/setup/status").json()
    before = client.post("/api/login", json={"username": %r, "password": ""}).status_code
    # The owner's reset flow (the emailed token is simulated by issuing it directly).
    with SessionLocal() as db:
        user = db.query(User).one()
        raw = token_store.issue(db, "reset", timedelta(hours=1), user_id=user.id)
        db.commit()
    reset = client.post("/api/password/reset", json={"token": raw, "password": %r}).status_code
    login = client.post("/api/login", json={"username": %r, "password": %r})
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    board_id = client.get("/api/boards", headers=headers).json()[0]["id"]
    detail = client.get(f"/api/boards/{board_id}", headers=headers).json()
    print(json.dumps([status, before, reset, login.status_code, detail]))
""" % (EMAIL, NEW_PASSWORD, EMAIL, NEW_PASSWORD)
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{path}"}
    result = subprocess.run([sys.executable, "-I", "-c", script], env=env, capture_output=True,
                            text=True, cwd=os.path.dirname(os.path.abspath(__file__)),
                            )
    assert result.returncode == 0, result.stderr
    status, before, reset, login_status, detail = json.loads(result.stdout.strip().splitlines()[-1])
    assert status["needs_setup"] is False
    assert (before, reset, login_status) == (401, 200, 200)
    assert [(c["title"], c["stage_kind"]) for c in detail["columns"]] == PIPELINE_STAGES
    assert all(c["cards"] == [] for c in detail["columns"])

    for p, digest in dev_hashes.items():
        assert hashlib.sha256(open(p, "rb").read()).hexdigest() == digest
