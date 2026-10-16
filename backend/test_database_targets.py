"""Database target configuration: temporary paths and disposable SQLite files only.

No protected, development or commercial database is opened; protected paths are
passed as strings and rejected before any file access.
"""
import os
import sqlite3
from pathlib import Path

import pytest

import create_identity_db
from create_identity_db import create_identity_database
from database_targets import (
    COMPANY_SLUGS,
    PROTECTED_PATHS,
    DatabaseTargetError,
    commercial_database_path,
    company_database_path,
    company_database_paths,
    company_env_name,
    identity_database_path,
    sqlite_file_path,
)
from identity_database import (
    IDENTITY_SCHEMA_VERSION,
    IdentitySchemaVersionError,
    open_identity_engine,
    require_identity_schema_version,
)


def url(path) -> str:
    return f"sqlite:///{path}"


@pytest.fixture
def identity(tmp_path):
    return create_identity_database(str(tmp_path / "identity.db"))


def env(identity_path, **extra):
    return {"IDENTITY_DATABASE_URL": url(identity_path), **extra}


def test_identity_url_required_with_no_fallback(tmp_path):
    with pytest.raises(DatabaseTargetError, match="not set"):
        identity_database_path({"DATABASE_URL": url(tmp_path / "x.db")})


def test_missing_identity_database_rejected_and_not_created(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(DatabaseTargetError, match="does not exist"):
        identity_database_path(env(missing))
    assert not missing.exists()


def test_identity_directory_rejected(tmp_path):
    with pytest.raises(DatabaseTargetError, match="regular file"):
        identity_database_path(env(tmp_path))


@pytest.mark.parametrize("bad", ["sqlite://", "sqlite:///:memory:", "postgresql://u@h/d", "not a url"])
def test_non_file_urls_rejected(bad):
    with pytest.raises(DatabaseTargetError):
        identity_database_path({"IDENTITY_DATABASE_URL": bad})


def test_existing_identity_file_accepted(identity):
    assert identity_database_path(env(identity)) == Path(os.path.realpath(identity))


def test_same_file_as_commercial_rejected_by_relative_and_absolute_alias(identity, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(DatabaseTargetError, match="DATABASE_URL"):
        identity_database_path(env(identity, DATABASE_URL="sqlite:///identity.db"))
    with pytest.raises(DatabaseTargetError, match="DATABASE_URL.*'\\.\\.'"):
        identity_database_path(env(identity, DATABASE_URL=url(tmp_path / "sub" / ".." / "identity.db")))
    with pytest.raises(DatabaseTargetError, match="DATABASE_URL"):
        identity_database_path(env(identity, DATABASE_URL=url(tmp_path / "." / "identity.db")))  # '.' is unambiguous
    with pytest.raises(DatabaseTargetError, match="DATABASE_URL"):
        identity_database_path(env("identity.db", DATABASE_URL=url(identity)))


def test_symlink_alias_rejected(identity, tmp_path):
    link = tmp_path / "link.db"
    try:
        link.symlink_to(identity)
    except OSError:
        pytest.skip("symlinks not supported")
    with pytest.raises(DatabaseTargetError, match="DATABASE_URL"):
        identity_database_path(env(identity, DATABASE_URL=url(link)))
    with pytest.raises(DatabaseTargetError, match="DATABASE_URL"):
        identity_database_path(env(link, DATABASE_URL=url(identity)))


def test_hard_link_alias_rejected(identity, tmp_path):
    link = tmp_path / "hard.db"
    try:
        os.link(identity, link)
    except OSError:
        pytest.skip("hard links not supported")
    with pytest.raises(DatabaseTargetError, match="DATABASE_URL"):
        identity_database_path(env(identity, DATABASE_URL=url(link)))


def test_identity_cannot_be_a_company_database(identity):
    with pytest.raises(DatabaseTargetError, match="PM_COMPANY_DB_LITY"):
        identity_database_path(env(identity, PM_COMPANY_DB_LITY=url(identity)))


def test_protected_names_and_sidecars_rejected_by_string(tmp_path):
    for name in ("pm.db", "PM-Commercial.DB", "identity.db-wal", "identity.db-shm", "identity.db-journal"):
        with pytest.raises(DatabaseTargetError):
            identity_database_path(env(tmp_path / name))


def test_company_slugs_are_the_three_registered_companies():
    assert COMPANY_SLUGS == ("salcomp", "lity", "valor-log")
    assert company_env_name("valor-log") == "PM_COMPANY_DB_VALOR_LOG"


def test_company_paths_map_each_slug_to_its_own_file(tmp_path):
    e = {company_env_name(s): url(tmp_path / f"{s}.db") for s in COMPANY_SLUGS}
    paths = company_database_paths(e)
    assert set(paths) == set(COMPANY_SLUGS) and len(set(paths.values())) == 3
    assert not any(p.exists() for p in paths.values())  # nothing opened or created


def test_two_companies_cannot_share_a_file_including_aliases(tmp_path):
    (tmp_path / "d").mkdir()
    e = {"PM_COMPANY_DB_SALCOMP": url(tmp_path / "a.db"), "PM_COMPANY_DB_LITY": url(tmp_path / "d" / "." / ".." / "a.db")}
    with pytest.raises(DatabaseTargetError, match="'\\.\\.'"):
        company_database_paths(e)
    e["PM_COMPANY_DB_LITY"] = url(tmp_path / "d" / "." / "a.db")
    assert company_database_paths(e)["lity"] == tmp_path / "d" / "a.db"
    e["PM_COMPANY_DB_LITY"] = url(tmp_path / "." / "a.db")
    with pytest.raises(DatabaseTargetError, match="same file"):
        company_database_paths(e)


def test_two_companies_cannot_share_a_file_via_symlink(tmp_path):
    target = tmp_path / "a.db"
    target.touch()
    link = tmp_path / "b.db"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks not supported")
    with pytest.raises(DatabaseTargetError, match="same file"):
        company_database_paths({"PM_COMPANY_DB_SALCOMP": url(target), "PM_COMPANY_DB_VALOR_LOG": url(link)})


def test_unknown_and_unconfigured_slugs_fail_closed(tmp_path):
    e = {"PM_COMPANY_DB_SALCOMP": url(tmp_path / "s.db")}
    assert company_database_path("salcomp", e).name == "s.db"
    with pytest.raises(DatabaseTargetError, match="unknown company"):
        company_database_path("acme", e)
    with pytest.raises(DatabaseTargetError, match="unknown company"):
        company_database_path("Salcomp", e)
    with pytest.raises(DatabaseTargetError, match="no database is configured"):
        company_database_path("lity", e)


def test_database_url_is_never_a_company_mapping(tmp_path):
    with pytest.raises(DatabaseTargetError, match="no database is configured"):
        company_database_path("salcomp", {"DATABASE_URL": url(tmp_path / "x.db")})


def test_protected_company_target_not_required_but_identity_cannot_use_it(tmp_path):
    with pytest.raises(DatabaseTargetError):
        identity_database_path({"IDENTITY_DATABASE_URL": url(tmp_path / "pm-commercial.db")})


def test_new_identity_database_is_stamped_and_opens_with_foreign_keys(identity):
    engine = open_identity_engine(env(identity))
    try:
        with engine.connect() as c:
            assert c.exec_driver_sql("PRAGMA user_version").scalar() == IDENTITY_SCHEMA_VERSION
            assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        engine.dispose()


def test_unstamped_existing_database_is_refused_not_stamped(tmp_path):
    path = tmp_path / "old.db"
    sqlite3.connect(path).close()
    with pytest.raises(IdentitySchemaVersionError, match="found"):
        open_identity_engine(env(path))
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        conn.close()


def test_wrong_version_is_refused(identity):
    conn = sqlite3.connect(identity)
    conn.execute(f"PRAGMA user_version = {IDENTITY_SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    with pytest.raises(IdentitySchemaVersionError):
        open_identity_engine(env(identity))


def test_version_check_function_on_unversioned_engine():
    from identity_database import create_in_memory_identity_engine

    engine = create_in_memory_identity_engine()
    try:
        with pytest.raises(IdentitySchemaVersionError):
            require_identity_schema_version(engine)
    finally:
        engine.dispose()


# --- identity / company collisions (both directions, files need not exist) ---

def test_identity_target_colliding_with_company_target_rejected_even_if_missing(tmp_path):
    shared = tmp_path / "shared.db"  # exists nowhere
    with pytest.raises(DatabaseTargetError, match="PM_COMPANY_DB_LITY"):
        identity_database_path(env(shared, PM_COMPANY_DB_LITY=url(tmp_path / "." / "shared.db")))
    assert not shared.exists()


def test_company_target_colliding_with_identity_target_rejected_even_if_missing(tmp_path):
    shared = tmp_path / "shared.db"
    e = {"IDENTITY_DATABASE_URL": url(shared), "PM_COMPANY_DB_VALOR_LOG": url(tmp_path / "SHARED.DB")}
    with pytest.raises(DatabaseTargetError, match="IDENTITY_DATABASE_URL"):
        company_database_paths(e)
    with pytest.raises(DatabaseTargetError, match="IDENTITY_DATABASE_URL"):
        company_database_path("valor-log", e)
    assert not shared.exists()


def test_company_target_aliasing_existing_identity_file_rejected(identity, tmp_path):
    hard = tmp_path / "hard.db"
    soft = tmp_path / "soft.db"
    os.link(identity, hard)
    soft.symlink_to(identity)
    for alias in (hard, soft):
        with pytest.raises(DatabaseTargetError, match="IDENTITY_DATABASE_URL"):
            company_database_paths({"IDENTITY_DATABASE_URL": url(identity), "PM_COMPANY_DB_LITY": url(alias)})


def test_company_validation_does_not_open_or_create_anything(tmp_path):
    e = {"IDENTITY_DATABASE_URL": url(tmp_path / "id.db"), "PM_COMPANY_DB_SALCOMP": url(tmp_path / "s.db")}
    assert set(company_database_paths(e)) == {"salcomp"}
    assert list(tmp_path.iterdir()) == []


# --- rejected company targets ------------------------------------------------

@pytest.mark.parametrize("name", ["pm.db", "PM.DB.BAK", "pm-commercial.db", "Pm-Commercial.DB"])
def test_company_target_with_protected_name_rejected(tmp_path, name):
    with pytest.raises(DatabaseTargetError, match="development or commercial"):
        company_database_paths({"PM_COMPANY_DB_SALCOMP": url(tmp_path / name)})


@pytest.mark.parametrize("name", ["c.db-wal", "c.db-shm", "c.db-journal", "C.DB-WAL"])
def test_company_target_with_sidecar_name_rejected(tmp_path, name):
    with pytest.raises(DatabaseTargetError, match="sidecar"):
        company_database_paths({"PM_COMPANY_DB_LITY": url(tmp_path / name)})


def test_company_target_colliding_with_database_url_rejected(tmp_path):
    e = {"DATABASE_URL": url(tmp_path / "dev.db"), "PM_COMPANY_DB_LITY": url(tmp_path / "." / "dev.db")}
    with pytest.raises(DatabaseTargetError, match="DATABASE_URL"):
        company_database_paths(e)


def test_permanent_salcomp_path_is_protected_for_company_and_identity_targets():
    permanent = [p for p in PROTECTED_PATHS if "SalcompSalesPipeline" in str(p)]
    assert permanent  # string only: the path is never opened
    with pytest.raises(DatabaseTargetError):
        company_database_paths({"PM_COMPANY_DB_SALCOMP": url(permanent[0])})
    with pytest.raises(DatabaseTargetError, match="'\\.\\.'"):
        company_database_paths({"PM_COMPANY_DB_SALCOMP": url(permanent[0].parent / "other" / ".." / "x" / ".." / permanent[0].name)})
    with pytest.raises(DatabaseTargetError):
        identity_database_path({"IDENTITY_DATABASE_URL": url(permanent[0])})


# --- unsupported URL forms, for company, identity and DATABASE_URL targets ---

BAD_FORMS = [
    "sqlite:///x.db?uri=true",
    "sqlite:///x.db?mode=ro",
    "sqlite:///x.db?",
    "sqlite:///file:x.db",
    "sqlite:///FILE:x.db",
    "sqlite:///~/x.db",
    "sqlite:///x\x00y.db",
    "sqlite+aiosqlite:///x.db",
    "sqlite+pysqlcipher:///x.db",
    "sqlite://host/x.db",
    "sqlite://user:pw@/x.db",
    "sqlite://:5432/x.db",
]


@pytest.mark.parametrize("bad", BAD_FORMS)
def test_unsupported_forms_rejected_for_company_targets(bad):
    with pytest.raises(DatabaseTargetError):
        company_database_paths({"PM_COMPANY_DB_SALCOMP": bad})


@pytest.mark.parametrize("bad", BAD_FORMS)
def test_unsupported_forms_rejected_for_identity_target(bad):
    with pytest.raises(DatabaseTargetError):
        identity_database_path({"IDENTITY_DATABASE_URL": bad})


@pytest.mark.parametrize("bad", BAD_FORMS)
def test_unsupported_sqlite_database_url_rejected(bad, tmp_path):
    with pytest.raises(DatabaseTargetError):
        commercial_database_path({"DATABASE_URL": bad})
    with pytest.raises(DatabaseTargetError):
        company_database_paths({"DATABASE_URL": bad, "PM_COMPANY_DB_LITY": url(tmp_path / "l.db")})


def test_question_mark_never_truncates_the_path_silently():
    with pytest.raises(DatabaseTargetError):
        sqlite_file_path("sqlite:////tmp/a?b.db", "X")


# --- '..' segments: the file the driver opens must be unambiguous ---

def ambiguous_alias(tmp_path):
    """tmp/real/sub/ with tmp/link -> tmp/real/sub; link/../x.db is tmp/real/x.db for the kernel, tmp/x.db lexically."""
    (tmp_path / "real" / "sub").mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "real" / "sub", target_is_directory=True)
    return f"sqlite:///{tmp_path}/link/../x.db"


def test_dotdot_after_a_symlinked_directory_is_rejected_before_any_schema_is_created(tmp_path, monkeypatch):
    ambiguous = ambiguous_alias(tmp_path)
    with pytest.raises(DatabaseTargetError, match="'\\.\\.'"):
        sqlite_file_path(ambiguous, "X")
    with pytest.raises(DatabaseTargetError, match="'\\.\\.'"):
        identity_database_path({"IDENTITY_DATABASE_URL": ambiguous})
    with pytest.raises(DatabaseTargetError, match="'\\.\\.'"):
        company_database_paths({"PM_COMPANY_DB_LITY": ambiguous})
    with pytest.raises(create_identity_db.IdentityDatabaseError, match="'\\.\\.'"):
        create_identity_database(ambiguous.removeprefix("sqlite:///"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["link", "real"]  # nothing created anywhere
    assert list((tmp_path / "real").iterdir()) == [tmp_path / "real" / "sub"]
    assert list((tmp_path / "real" / "sub").iterdir()) == []


@pytest.mark.parametrize("bad", ["sqlite:///../x.db", "sqlite:///a/b/../x.db", "sqlite:///a/..", "sqlite:///a\\..\\x.db"])
def test_any_dotdot_segment_is_rejected_but_dots_in_names_are_not(bad, tmp_path):
    with pytest.raises(DatabaseTargetError, match="'\\.\\.'"):
        sqlite_file_path(bad, "X")
    assert sqlite_file_path(url(tmp_path / "..x..db"), "X").name == "..x..db"
    assert sqlite_file_path(url(tmp_path / "." / "ok.db"), "X") == tmp_path / "ok.db"


def test_database_url_with_dotdot_cannot_bypass_collision_protection(tmp_path):
    ambiguous = ambiguous_alias(tmp_path)
    identity = create_identity_database(str(tmp_path / "real" / "x.db"))  # the file the kernel would pick
    with pytest.raises(DatabaseTargetError, match="DATABASE_URL.*'\\.\\.'"):
        commercial_database_path({"DATABASE_URL": ambiguous})
    with pytest.raises(DatabaseTargetError, match="DATABASE_URL.*'\\.\\.'"):
        identity_database_path(env(identity, DATABASE_URL=ambiguous))
    with pytest.raises(DatabaseTargetError, match="DATABASE_URL.*'\\.\\.'"):
        company_database_paths({"DATABASE_URL": ambiguous, "PM_COMPANY_DB_LITY": url(tmp_path / "other.db")})
