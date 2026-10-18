"""company_schema: read-only fail-closed validation (disposable tmp_path databases only)."""
import hashlib
import sqlite3

import pytest

import company_schema
from company_schema import (
    COMPANY_SCHEMA_VERSION, REQUIRED_COMPANY_SCHEMA, CompanySchemaError, validate_company_schema,
)
from database_targets import COMPANY_SLUGS, DatabaseTargetError, company_env_name
from models import CompanyBase


def env_for(tmp_path, **extra):
    env = {company_env_name(s): f"sqlite:///{tmp_path / (s + '.db')}" for s in COMPANY_SLUGS}
    env.update(extra)
    return env


def make_db(path, version=COMPANY_SCHEMA_VERSION, drop=(), drop_columns=()):
    con = sqlite3.connect(path)
    for table, cols in REQUIRED_COMPANY_SCHEMA.items():
        if table in drop:
            continue
        cols = [c for c in sorted(cols) if (table, c) not in drop_columns]
        con.execute(f'CREATE TABLE "{table}" ({", ".join(cols)})')
    con.execute(f"PRAGMA user_version = {version}")
    con.commit()
    con.close()


def snapshot(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0], con.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    finally:
        con.close()


def test_required_schema_matches_models():
    assert COMPANY_SCHEMA_VERSION == 4
    assert set(REQUIRED_COMPANY_SCHEMA) == set(CompanyBase.metadata.tables)
    for table, cols in REQUIRED_COMPANY_SCHEMA.items():
        assert cols == {c.name for c in CompanyBase.metadata.tables[table].columns}


@pytest.mark.parametrize("slug", COMPANY_SLUGS)
def test_valid_schema_accepted(tmp_path, slug):
    make_db(tmp_path / f"{slug}.db")
    assert validate_company_schema(slug, env_for(tmp_path)) is None


def test_extra_objects_tolerated(tmp_path):
    make_db(tmp_path / "lity.db")
    con = sqlite3.connect(tmp_path / "lity.db")
    con.execute("CREATE TABLE extra (x)")
    con.commit()
    con.close()
    validate_company_schema("lity", env_for(tmp_path))


def test_empty_database_rejected(tmp_path):
    (tmp_path / "lity.db").write_bytes(b"")
    with pytest.raises(CompanySchemaError):
        validate_company_schema("lity", env_for(tmp_path))
    assert (tmp_path / "lity.db").read_bytes() == b""


def test_empty_stamped_database_rejected(tmp_path):
    make_db(tmp_path / "lity.db", drop=set(REQUIRED_COMPANY_SCHEMA))
    with pytest.raises(CompanySchemaError, match="missing"):
        validate_company_schema("lity", env_for(tmp_path))


def test_missing_database_rejected_and_not_created(tmp_path):
    with pytest.raises(CompanySchemaError, match="does not exist"):
        validate_company_schema("lity", env_for(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_unrelated_sqlite_file_rejected(tmp_path):
    con = sqlite3.connect(tmp_path / "lity.db")
    con.execute("CREATE TABLE notes (a)")
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()
    with pytest.raises(CompanySchemaError):
        validate_company_schema("lity", env_for(tmp_path))


def test_non_sqlite_file_rejected(tmp_path):
    (tmp_path / "lity.db").write_text("this is not sqlite, " * 20)
    with pytest.raises(CompanySchemaError, match="not a readable"):
        validate_company_schema("lity", env_for(tmp_path))


@pytest.mark.parametrize("version", [0, 1, 2, 3, 5, 99, -1])  # 1 = pipeline-core, 2 = before cards.confidence_level, 3 = still the obsolete Bid columns
def test_bad_version_rejected(tmp_path, version):
    make_db(tmp_path / "lity.db", version=version)
    with pytest.raises(CompanySchemaError, match="schema version"):
        validate_company_schema("lity", env_for(tmp_path))


@pytest.mark.parametrize("table", sorted(REQUIRED_COMPANY_SCHEMA))
def test_missing_table_rejected(tmp_path, table):
    make_db(tmp_path / "lity.db", drop={table})
    with pytest.raises(CompanySchemaError, match=table):
        validate_company_schema("lity", env_for(tmp_path))


def test_missing_column_rejected(tmp_path):
    make_db(tmp_path / "lity.db", drop_columns={("columns", "stage_kind")})
    with pytest.raises(CompanySchemaError, match="stage_kind"):
        validate_company_schema("lity", env_for(tmp_path))


@pytest.mark.parametrize("version", [COMPANY_SCHEMA_VERSION, 0, 7])
def test_validation_does_not_modify_database(tmp_path, version):
    path = tmp_path / "lity.db"
    make_db(path, version=version)
    before_bytes = hashlib.sha256(path.read_bytes()).hexdigest()
    before = snapshot(path)
    try:
        validate_company_schema("lity", env_for(tmp_path))
    except CompanySchemaError:
        pass
    assert snapshot(path) == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_bytes
    assert sorted(p.name for p in tmp_path.iterdir()) == ["lity.db"]


def _spy_connect(monkeypatch, record):
    real = sqlite3.connect

    class Spy:
        def __init__(self, con):
            self._con = con
        def __getattr__(self, name):
            return getattr(self._con, name)
        def close(self):
            record.append("closed")
            self._con.close()

    def connect(database, *args, **kwargs):
        record.append(database)
        return Spy(real(database, *args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", connect)


def test_connection_closed_on_success_and_failure(tmp_path, monkeypatch):
    make_db(tmp_path / "lity.db")
    make_db(tmp_path / "salcomp.db", version=5)
    record = []
    _spy_connect(monkeypatch, record)
    validate_company_schema("lity", env_for(tmp_path))
    with pytest.raises(CompanySchemaError):
        validate_company_schema("salcomp", env_for(tmp_path))
    assert record.count("closed") == 2


def test_database_is_opened_read_only(tmp_path, monkeypatch):
    make_db(tmp_path / "lity.db")
    record = []
    _spy_connect(monkeypatch, record)
    validate_company_schema("lity", env_for(tmp_path))
    assert record[0].endswith("?mode=ro")


@pytest.mark.parametrize("slug", ["", "Salcomp", "acme", "valor_log", "../x"])
def test_unknown_slug_rejected(tmp_path, slug):
    with pytest.raises(DatabaseTargetError):
        validate_company_schema(slug, env_for(tmp_path))


def test_unconfigured_company_rejected(tmp_path):
    make_db(tmp_path / "lity.db")
    with pytest.raises(DatabaseTargetError, match="no database is configured"):
        validate_company_schema("lity", {})


@pytest.mark.parametrize("name", ["pm.db", "pm.db.bak", "pm-commercial.db", "x.db-wal"])
def test_protected_paths_rejected_without_opening(tmp_path, name, monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("opened"))
    env = env_for(tmp_path, **{company_env_name("lity"): f"sqlite:///{tmp_path / name}"})
    with pytest.raises(DatabaseTargetError):
        validate_company_schema("lity", env)


def test_collision_rejected(tmp_path):
    env = env_for(tmp_path, **{company_env_name("lity"): f"sqlite:///{tmp_path / 'salcomp.db'}"})
    make_db(tmp_path / "salcomp.db")
    with pytest.raises(DatabaseTargetError, match="same file"):
        validate_company_schema("salcomp", env)


def test_salcomp_has_no_exemption(tmp_path):
    env = env_for(tmp_path, **{company_env_name("salcomp"): f"sqlite:///{tmp_path / 'pm-commercial.db'}"})
    with pytest.raises(DatabaseTargetError):
        validate_company_schema("salcomp", env)


def test_validation_is_per_company(tmp_path):
    make_db(tmp_path / "lity.db")  # valid
    (tmp_path / "valor-log.db").write_bytes(b"")  # invalid
    validate_company_schema("lity", env_for(tmp_path))
    with pytest.raises(CompanySchemaError):
        validate_company_schema("valor-log", env_for(tmp_path))
    with pytest.raises(CompanySchemaError, match="does not exist"):
        validate_company_schema("salcomp", env_for(tmp_path))


def test_errors_do_not_leak_paths(tmp_path):
    make_db(tmp_path / "lity.db", version=2)
    with pytest.raises(CompanySchemaError) as exc:
        validate_company_schema("lity", env_for(tmp_path))
    assert str(tmp_path) not in str(exc.value)
