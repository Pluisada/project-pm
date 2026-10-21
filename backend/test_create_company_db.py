"""create_company_db: explicit, exclusive creation of a versioned company database.

Disposable tmp_path databases only; targets are supplied through explicit environments.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import create_company_db
from company_schema import COMPANY_SCHEMA_VERSION, REQUIRED_COMPANY_SCHEMA, validate_company_schema
from create_company_db import CompanyDatabaseError, create_company_database
from database_targets import COMPANY_SLUGS, DatabaseTargetError, company_env_name

BACKEND = Path(__file__).parent


def env_for(tmp_path, slug="lity", **extra):
    env = {company_env_name(slug): f"sqlite:///{tmp_path / (slug + '.db')}"}
    env.update(extra)
    return env


def tables(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}, \
            con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


@pytest.mark.parametrize("slug", COMPANY_SLUGS)
def test_creates_the_complete_versioned_schema(tmp_path, slug):
    env = env_for(tmp_path, slug)
    path = create_company_database(slug, env)
    assert path == (tmp_path / f"{slug}.db").resolve()
    names, version = tables(path)
    assert version == COMPANY_SCHEMA_VERSION == 4
    # products and the Cards-import ledger: optional tables, outside the required contract
    assert names == set(REQUIRED_COMPANY_SCHEMA) | {"products", "card_excel_imports", "card_versions", "card_review_obligations"} and "users" in names
    assert validate_company_schema(slug, env) is None
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{slug}.db"]  # no sidecars left behind


def test_no_local_user_reference_is_created(tmp_path):
    path = create_company_database("lity", env_for(tmp_path))
    con = sqlite3.connect(path)
    try:
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    finally:
        con.close()


def test_foreign_keys_are_enforced_on_every_connection_used(tmp_path, monkeypatch):
    engines = []
    real = create_company_db.create_engine

    def recording(*a, **k):
        engines.append(real(*a, **k))
        return engines[-1]

    monkeypatch.setattr(create_company_db, "create_engine", recording)
    create_company_database("lity", env_for(tmp_path))
    assert len(engines) == 1
    # the engine's pooled connections enforced foreign keys (checked inside the initializer too)
    with real(f"sqlite:///{tmp_path / 'lity.db'}").connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []


def test_the_new_database_rejects_orphans(tmp_path):
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.exc import IntegrityError

    path = create_company_database("lity", env_for(tmp_path))
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", lambda c, _r: c.execute("PRAGMA foreign_keys=ON"))
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(text("INSERT INTO boards (user_id, title, created_at, updated_at) "
                                "VALUES (99, 'x', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
    engine.dispose()


def test_existing_file_is_refused_and_untouched(tmp_path):
    target = tmp_path / "lity.db"
    target.write_bytes(b"keep")
    with pytest.raises(CompanyDatabaseError, match="already exists"):
        create_company_database("lity", env_for(tmp_path))
    assert target.read_bytes() == b"keep"


def test_existing_empty_file_is_refused(tmp_path):
    target = tmp_path / "lity.db"
    target.touch()
    with pytest.raises(CompanyDatabaseError, match="already exists"):
        create_company_database("lity", env_for(tmp_path))
    assert target.read_bytes() == b""


def test_existing_valid_database_is_not_stamped_or_changed(tmp_path):
    path = create_company_database("lity", env_for(tmp_path))
    before = path.read_bytes()
    with pytest.raises(CompanyDatabaseError):
        create_company_database("lity", env_for(tmp_path))
    assert path.read_bytes() == before


def test_missing_directory_is_refused_and_not_created(tmp_path):
    env = {company_env_name("lity"): f"sqlite:///{tmp_path / 'sub' / 'lity.db'}"}
    with pytest.raises(CompanyDatabaseError, match="directory"):
        create_company_database("lity", env)
    assert list(tmp_path.iterdir()) == []


def test_unknown_slug_and_missing_configuration(tmp_path):
    with pytest.raises(DatabaseTargetError, match="unknown company"):
        create_company_database("acme", env_for(tmp_path))
    with pytest.raises(DatabaseTargetError, match="no database is configured"):
        create_company_database("valor-log", env_for(tmp_path))  # only lity is configured
    assert list(tmp_path.iterdir()) == []


def test_database_url_is_never_a_fallback(tmp_path):
    env = {"DATABASE_URL": f"sqlite:///{tmp_path / 'fallback.db'}"}
    with pytest.raises(DatabaseTargetError, match="no database is configured"):
        create_company_database("lity", env)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["pm.db", "pm.db.bak", "pm-commercial.db", "x.db-wal", "x.db-shm", "x.db-journal"])
def test_protected_and_sidecar_targets_are_refused(tmp_path, name):
    env = {company_env_name("lity"): f"sqlite:///{tmp_path / name}"}
    with pytest.raises(DatabaseTargetError):
        create_company_database("lity", env)
    assert list(tmp_path.iterdir()) == []


def test_salcomp_has_no_exemption(tmp_path):
    env = {company_env_name("salcomp"): f"sqlite:///{tmp_path / 'pm-commercial.db'}"}
    with pytest.raises(DatabaseTargetError):
        create_company_database("salcomp", env)
    assert list(tmp_path.iterdir()) == []


def test_identity_and_company_collisions_are_refused(tmp_path):
    shared = f"sqlite:///{tmp_path / 'one.db'}"
    with pytest.raises(DatabaseTargetError, match="IDENTITY_DATABASE_URL"):
        create_company_database("lity", {company_env_name("lity"): shared, "IDENTITY_DATABASE_URL": shared})
    with pytest.raises(DatabaseTargetError, match="same file"):
        create_company_database("lity", {company_env_name("lity"): shared, company_env_name("valor-log"): shared})
    with pytest.raises(DatabaseTargetError, match="DATABASE_URL"):
        create_company_database("lity", {company_env_name("lity"): shared, "DATABASE_URL": shared})
    assert list(tmp_path.iterdir()) == []


def test_identity_database_is_neither_created_nor_touched(tmp_path):
    identity = tmp_path / "identity.db"
    env = env_for(tmp_path, IDENTITY_DATABASE_URL=f"sqlite:///{identity}")
    create_company_database("lity", env)
    assert not identity.exists()


def test_failure_removes_only_the_file_it_created(tmp_path, monkeypatch):
    def boom(_engine):
        raise RuntimeError("boom")

    monkeypatch.setattr(create_company_db.CompanyBase.metadata, "create_all", boom)
    with pytest.raises(RuntimeError, match="boom"):
        create_company_database("lity", env_for(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_failed_verification_removes_the_new_file(tmp_path, monkeypatch):
    def refuse(*_a, **_k):
        raise create_company_db.CompanySchemaError("nope")

    monkeypatch.setattr(create_company_db, "validate_company_schema", refuse)
    with pytest.raises(create_company_db.CompanySchemaError):
        create_company_database("lity", env_for(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_failure_does_not_delete_a_file_that_replaced_it(tmp_path, monkeypatch, capsys):
    target = tmp_path / "lity.db"

    def swap_and_fail(_engine):
        target.unlink()
        target.write_bytes(b"someone else's")
        raise RuntimeError("boom")

    monkeypatch.setattr(create_company_db.CompanyBase.metadata, "create_all", swap_and_fail)
    with pytest.raises(RuntimeError):
        create_company_database("lity", env_for(tmp_path))
    assert target.read_bytes() == b"someone else's"
    assert "was not removed" in capsys.readouterr().err


def test_a_race_for_the_path_is_refused_without_deleting(tmp_path, monkeypatch):
    target = tmp_path / "lity.db"
    real_open = os.open

    def racing_open(path, *a, **k):
        target.write_bytes(b"winner")
        return real_open(path, *a, **k)

    monkeypatch.setattr(create_company_db.os, "open", racing_open)
    with pytest.raises(CompanyDatabaseError, match="already exists"):
        create_company_database("lity", env_for(tmp_path))
    assert target.read_bytes() == b"winner"


def test_cli_has_no_path_or_url_option(tmp_path):
    with pytest.raises(SystemExit):
        create_company_db.main(["--company", "lity", "--database", str(tmp_path / "x.db")])
    with pytest.raises(SystemExit):
        create_company_db.main(["--company", "acme"])
    assert list(tmp_path.iterdir()) == []


def test_cli_creates_and_reports(tmp_path, monkeypatch, capsys):
    for slug in COMPANY_SLUGS:
        monkeypatch.delenv(company_env_name(slug), raising=False)
    monkeypatch.setenv(company_env_name("lity"), f"sqlite:///{tmp_path / 'lity.db'}")
    assert create_company_db.main(["--company", "lity"]) == 0
    assert "schema version 4" in capsys.readouterr().out
    assert create_company_db.main(["--company", "lity"]) == 1  # now exists
    assert "already exists" in capsys.readouterr().err


def test_cli_unconfigured_company_fails_closed(tmp_path, monkeypatch, capsys):
    for slug in COMPANY_SLUGS:
        monkeypatch.delenv(company_env_name(slug), raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fallback.db'}")
    assert create_company_db.main(["--company", "lity"]) == 1
    assert list(tmp_path.iterdir()) == []


def test_importing_has_no_side_effects(tmp_path):
    code = (
        f"import sys; sys.path.insert(0, {str(BACKEND)!r})\n"
        "import sqlite3\n"
        "def boom(*a, **k): raise AssertionError('connect')\n"
        "sqlite3.connect = boom\n"
        "import create_company_db\n"
        "assert 'database' not in sys.modules and 'main' not in sys.modules\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_nothing_in_the_application_calls_the_initializer():
    callers = [
        p.name for p in BACKEND.glob("*.py")
        if not p.name.startswith("test_") and p.name != "create_company_db.py"
        and any(f"{kw} create_company_db" in p.read_text() for kw in ("import", "from"))
    ]
    assert callers == []
