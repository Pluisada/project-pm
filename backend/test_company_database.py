"""company_database: path-validated, no-create engine/session factory (disposable files only)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

import company_database
from company_database import company_session_factory, open_company_engine
from database_targets import COMPANY_SLUGS, DatabaseTargetError, company_database_path, company_env_name

BACKEND = Path(__file__).resolve().parent


def env_for(tmp_path, **extra):
    env = {company_env_name(s): f"sqlite:///{tmp_path / (s + '.db')}" for s in COMPANY_SLUGS}
    env.update(extra)
    return env


@pytest.fixture
def no_engine(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("an engine was created")
    monkeypatch.setattr(company_database, "create_engine", boom)


@pytest.mark.parametrize("slug", COMPANY_SLUGS)
def test_each_slug_resolves_through_authoritative_path(tmp_path, slug, monkeypatch):
    calls = []
    real = company_database.company_database_path
    monkeypatch.setattr(company_database, "company_database_path", lambda s, e=None: calls.append(s) or real(s, e))
    engine = open_company_engine(slug, env_for(tmp_path))
    assert calls == [slug]
    assert engine.url.database.startswith("file:")
    assert Path(engine.url.database[len("file:"):]).name == f"{slug}.db"
    assert dict(engine.url.query) == {"mode": "rw", "uri": "true"}
    assert str(company_database_path(slug, env_for(tmp_path))) in engine.url.database.replace("%20", " ")
    engine.dispose()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("slug", ["", "Salcomp", "acme", "valor_log", "../x", "salcomp "])
def test_unknown_slugs_rejected(tmp_path, slug, no_engine):
    with pytest.raises(DatabaseTargetError):
        open_company_engine(slug, env_for(tmp_path))


def test_missing_configuration_rejected(no_engine):
    with pytest.raises(DatabaseTargetError):
        open_company_engine("lity", {})


def test_database_url_is_never_a_fallback(tmp_path, no_engine):
    with pytest.raises(DatabaseTargetError):
        open_company_engine("lity", {"DATABASE_URL": f"sqlite:///{tmp_path / 'x.db'}"})


def test_missing_database_is_not_created(tmp_path):
    engine = open_company_engine("lity", env_for(tmp_path))
    with pytest.raises(OperationalError):
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    engine.dispose()
    assert list(tmp_path.iterdir()) == []


def test_existing_database_opens_with_foreign_keys_and_is_not_altered(tmp_path):
    import sqlite3
    db = tmp_path / "lity.db"
    sqlite3.connect(db).close()
    engine = open_company_engine("lity", env_for(tmp_path))
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA user_version")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM sqlite_master")).scalar() == 0
    engine.dispose()
    assert {p.name for p in tmp_path.iterdir()} == {"lity.db"}


@pytest.mark.parametrize("bad", ["pm.db", "pm-commercial.db", "x.db-wal", "PM.DB"])
def test_protected_path_rejected_before_engine(tmp_path, bad, no_engine):
    env = env_for(tmp_path, **{company_env_name("salcomp"): f"sqlite:///{tmp_path / bad}"})
    with pytest.raises(DatabaseTargetError):
        open_company_engine("salcomp", env)
    assert list(tmp_path.iterdir()) == []


def test_collisions_rejected_before_engine(tmp_path, no_engine):
    shared = f"sqlite:///{tmp_path / 'shared.db'}"
    env = {company_env_name("salcomp"): shared, company_env_name("lity"): shared}
    with pytest.raises(DatabaseTargetError):
        open_company_engine("lity", env)
    env = {company_env_name("lity"): shared, "DATABASE_URL": shared}
    with pytest.raises(DatabaseTargetError):
        open_company_engine("lity", env)
    env = {company_env_name("lity"): shared, "IDENTITY_DATABASE_URL": shared}
    with pytest.raises(DatabaseTargetError):
        open_company_engine("lity", env)


def test_raw_url_with_query_rejected(tmp_path, no_engine):
    env = env_for(tmp_path, **{company_env_name("lity"): f"sqlite:///{tmp_path / 'l.db'}?mode=rwc"})
    with pytest.raises(DatabaseTargetError):
        open_company_engine("lity", env)


def test_companies_do_not_share_engines_or_factories(tmp_path):
    env = env_for(tmp_path)
    a, b = open_company_engine("lity", env), open_company_engine("valor-log", env)
    fa, fb = company_session_factory(a), company_session_factory(b)
    assert a is not b and fa is not fb
    assert a.url != b.url
    assert fa.kw["bind"] is a and fb.kw["bind"] is b
    assert fa().get_bind() is a and fb().get_bind() is b
    assert fa.kw["info"] == {"company_slug": "lity"} and fb.kw["info"] == {"company_slug": "valor-log"}
    assert open_company_engine("lity", env) is not a  # no hidden cache
    a.dispose(); b.dispose()


def test_session_factory_refuses_foreign_engine():
    from sqlalchemy import create_engine
    with pytest.raises(ValueError):
        company_session_factory(create_engine("sqlite://"))


def test_import_has_no_side_effects(tmp_path):
    code = (
        "import sqlite3, sys\n"
        "def boom(*a, **k): raise AssertionError('connect')\n"
        "sqlite3.connect = boom\n"
        "import company_database\n"
        "assert not hasattr(company_database, 'Base')\n"
        "assert 'database' not in sys.modules and 'models' not in sys.modules\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PM_", "DATABASE_URL", "IDENTITY"))}
    env["PYTHONPATH"] = str(BACKEND)
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, env={**env, "PYTHONPATH": str(BACKEND)},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_module_runs_no_schema_creation():
    source = Path(company_database.__file__).read_text()
    assert "create_all" not in source.replace("Base.metadata.create_all()", "")
    assert "alembic" not in source.lower()
