"""Central identity database: schema, constraints, engine and creation utility.

Every database here lives in a fresh temporary directory. The protected-path
tests pass path strings only; no commercial or development database is opened.
"""
import hashlib
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text, update
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError, OperationalError

import create_identity_db
from create_identity_db import IdentityDatabaseError, check_target, create_identity_database
from identity_database import (
    create_identity_schema,
    create_in_memory_identity_engine,
    open_identity_engine,
    create_identity_session_factory,
    identity_db_dependency,
    IdentitySchemaVersionError,
)
from identity_schema import IDENTITY_SCHEMA_VERSION
from identity_models import (
    IdentityAuditEvent,
    IdentityAuthToken,
    IdentityBase,
    IdentityCompany,
    IdentityInvitation,
    IdentityMembership,
    IdentityUser,
)

NOW = datetime(2026, 1, 1, 12, 0, 0)
BACKEND_DIR = Path(__file__).resolve().parent


def open_created(path):
    """Open a database made by create_identity_database through the validated production path."""
    return open_identity_engine({"IDENTITY_DATABASE_URL": f"sqlite:///{path}"})


def token_hash(seed: str = "a") -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


@pytest.fixture
def engine():
    eng = create_in_memory_identity_engine()
    create_identity_schema(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    session = create_identity_session_factory(engine)()
    yield session
    session.close()


def add_user(db, email="a@example.com", **kw):
    user = IdentityUser(email=email, **kw)
    db.add(user)
    db.commit()
    return user


def add_company(db, slug="salcomp", name="Salcomp"):
    company = IdentityCompany(slug=slug, name=name)
    db.add(company)
    db.commit()
    return company


def add_invitation(db, admin, company, email="new@example.com", **kw):
    values = dict(
        email=email, company_id=company.id, role="sales_member", invited_by_user_id=admin.id,
        created_at=NOW, expires_at=NOW + timedelta(days=7),
    )
    values.update(kw)
    invitation = IdentityInvitation(**values)
    db.add(invitation)
    db.commit()
    return invitation


def rejected(db, obj):
    db.add(obj)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# --- schema -----------------------------------------------------------------

def test_schema_created_in_temporary_file_database(tmp_path):
    eng = open_created(create_identity_database(str(tmp_path / "identity.db")))
    tables = set(inspect(eng).get_table_names())
    eng.dispose()
    assert tables == {
        "identity_users", "identity_companies", "identity_memberships",
        "identity_invitations", "identity_auth_tokens", "identity_audit_events",
    }


def test_foreign_keys_enforced_on_every_connection(engine):
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(text(
                "INSERT INTO identity_memberships (user_id, company_id, role, created_at) "
                "VALUES (999, 999, 'visitor', '2026-01-01')"
            ))


def test_foreign_keys_enforced_on_multiple_independent_connections(tmp_path):
    eng = open_created(create_identity_database(str(tmp_path / "identity.db")))
    insert = text("INSERT INTO identity_memberships (user_id, company_id, role, created_at) "
                  "VALUES (999, 999, 'visitor', '2026-01-01')")
    first, second = eng.connect(), eng.connect()  # two distinct DBAPI connections
    try:
        assert first.connection.dbapi_connection is not second.connection.dbapi_connection
        for conn in (first, second):
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
            with pytest.raises(IntegrityError):
                conn.execute(insert)
            conn.rollback()
    finally:
        first.close()
        second.close()
        eng.dispose()


def test_no_foreign_key_leaves_the_identity_schema():
    for table in IdentityBase.metadata.tables.values():
        for fk in table.foreign_keys:
            assert fk.column.table.name.startswith("identity_")


def test_every_table_uses_autoincrement(engine):
    with engine.connect() as conn:
        for name in IdentityBase.metadata.tables:
            sql = conn.execute(text("SELECT sql FROM sqlite_master WHERE name = :n"), {"n": name}).scalar()
            assert "AUTOINCREMENT" in sql, name


# --- users ------------------------------------------------------------------

def test_user_defaults(db):
    user = add_user(db)
    assert (user.is_active, user.is_global_admin, user.session_version) == (True, False, 0)
    assert user.password_hash is None and user.email_verified_at is None and user.created_at


def test_user_email_unique_and_lowercase(db):
    add_user(db, "a@example.com")
    rejected(db, IdentityUser(email="a@example.com"))
    rejected(db, IdentityUser(email="A@Example.com"))
    rejected(db, IdentityUser(email=""))


def test_user_ids_are_never_reused_after_deletion(db):
    first = add_user(db, "one@example.com")
    second = add_user(db, "two@example.com")
    assert second.id == first.id + 1
    db.delete(second)  # disposable database only; the app has no delete workflow
    db.commit()
    third = add_user(db, "three@example.com")
    assert third.id == second.id + 1


def test_session_version_cannot_go_negative(db):
    rejected(db, IdentityUser(email="neg@example.com", session_version=-1))


def test_user_with_memberships_cannot_be_deleted(db):
    user, company = add_user(db), add_company(db)
    db.add(IdentityMembership(user_id=user.id, company_id=company.id, role="visitor"))
    db.commit()
    db.delete(user)
    with pytest.raises(IntegrityError):
        db.commit()


def test_email_length_limit(db):
    local = "a" * (320 - len("@example.com"))
    add_user(db, local + "@example.com")  # exactly 320
    rejected(db, IdentityUser(email="b" + local + "@example.com"))  # 321


def test_boolean_columns_accept_only_zero_or_one(db):
    user, company = add_user(db), add_company(db)
    for table, column in (
        ("identity_users", "is_global_admin"),
        ("identity_users", "is_active"),
        ("identity_companies", "is_active"),
        ("identity_memberships", "is_active"),
    ):
        sets = {
            "identity_users": "UPDATE identity_users SET {c} = :v WHERE id = :id",
            "identity_companies": "UPDATE identity_companies SET {c} = :v WHERE id = :id",
            "identity_memberships": "UPDATE identity_memberships SET {c} = :v WHERE id = :id",
        }[table].format(c=column)
        if table == "identity_memberships":
            db.add(IdentityMembership(user_id=user.id, company_id=company.id, role="visitor"))
            db.commit()
            row_id = db.query(IdentityMembership).one().id
        else:
            row_id = user.id if table == "identity_users" else company.id
        for bad in (2, -1, "yes"):
            with pytest.raises(IntegrityError):
                db.execute(text(sets), {"v": bad, "id": row_id})
            db.rollback()
        for good in (0, 1):
            db.execute(text(sets), {"v": good, "id": row_id})
        db.commit()


# --- companies and memberships ----------------------------------------------

def test_company_slug_unique_and_well_formed(db):
    add_company(db, "valor-log", "Valor-Log")
    rejected(db, IdentityCompany(slug="valor-log", name="Other"))
    for bad in ("", "Lity", "a b", "-lity", "lity-", "li_ty", "lity/x"):
        rejected(db, IdentityCompany(slug=bad, name="x"))


def test_company_slug_length_limit(db):
    add_company(db, "a" * 64, "Max")
    rejected(db, IdentityCompany(slug="b" * 65, name="Too long"))


def test_membership_unique_per_user_and_company(db):
    user, company = add_user(db), add_company(db)
    other = add_company(db, "lity", "Lity")
    db.add(IdentityMembership(user_id=user.id, company_id=company.id, role="sales_member"))
    db.commit()
    rejected(db, IdentityMembership(user_id=user.id, company_id=company.id, role="visitor"))
    db.add(IdentityMembership(user_id=user.id, company_id=other.id, role="visitor"))
    db.commit()


def test_membership_role_limited_to_the_schema_roles(db):
    user, company = add_user(db), add_company(db)
    for role in ("super_admin", "global_admin", "", "Visitor", "Admin"):
        rejected(db, IdentityMembership(user_id=user.id, company_id=company.id, role=role))
    db.add(IdentityMembership(user_id=user.id, company_id=company.id, role="admin"))  # v2: reserved, accepted by the schema
    db.commit()


# --- invitations ------------------------------------------------------------

def test_one_open_invitation_per_email_and_company(db):
    admin, company = add_user(db, "admin@example.com", is_global_admin=True), add_company(db)
    lity = add_company(db, "lity", "Lity")
    first = add_invitation(db, admin, company)
    rejected(db, IdentityInvitation(
        email="new@example.com", company_id=company.id, role="visitor", invited_by_user_id=admin.id,
        created_at=NOW, expires_at=NOW + timedelta(days=1),
    ))
    add_invitation(db, admin, lity)  # another company is fine
    first.revoked_at = NOW + timedelta(hours=1)
    db.commit()
    add_invitation(db, admin, company)  # re-invite once the first is closed


def test_invitation_constraints(db):
    admin, company = add_user(db, "admin@example.com", is_global_admin=True), add_company(db)
    base = dict(company_id=company.id, invited_by_user_id=admin.id, created_at=NOW, expires_at=NOW + timedelta(days=1))
    rejected(db, IdentityInvitation(email="x@example.com", role="super_admin", **base))
    db.add(IdentityInvitation(email="x@example.com", role="admin", **base))  # v2: reserved, accepted by the schema
    db.commit()
    db.query(IdentityInvitation).delete()
    db.commit()
    rejected(db, IdentityInvitation(email="X@example.com", role="visitor", **base))
    rejected(db, IdentityInvitation(
        email="x@example.com", role="visitor", **{**base, "expires_at": NOW}))  # not after created_at
    rejected(db, IdentityInvitation(
        email="x@example.com", role="visitor", **{**base, "revoked_at": NOW, "accepted_at": NOW,
                                                  "accepted_user_id": admin.id}))
    rejected(db, IdentityInvitation(  # accepted without recording who
        email="x@example.com", role="visitor", **{**base, "accepted_at": NOW}))
    rejected(db, IdentityInvitation(  # email over 320 characters
        email="a" * 310 + "@example.com", role="visitor", **base))


# --- tokens -----------------------------------------------------------------

def make_reset(user, seed="a", **kw):
    values = dict(token_hash=token_hash(seed), purpose="password_reset", user_id=user.id,
                  created_at=NOW, expires_at=NOW + timedelta(hours=1))
    values.update(kw)
    return IdentityAuthToken(**values)


def test_token_hash_unique(db):
    user = add_user(db)
    db.add(make_reset(user))
    db.commit()
    rejected(db, make_reset(user))


def test_token_must_be_a_sha256_hex_digest_never_a_plain_token(db):
    user = add_user(db)
    for bad in ("short", "x" * 64, "A" * 64, token_hash()[:-1], token_hash() + "0"):
        rejected(db, make_reset(user, token_hash=bad))


def test_token_purpose_scoped_to_its_subject(db):
    admin, company = add_user(db, "admin@example.com"), add_company(db)
    invitation = add_invitation(db, admin, company)
    now = dict(created_at=NOW, expires_at=NOW + timedelta(hours=1))
    db.add(IdentityAuthToken(token_hash=token_hash("i"), purpose="invitation", invitation_id=invitation.id, **now))
    db.add(make_reset(admin, "r"))
    db.commit()
    rejected(db, IdentityAuthToken(token_hash=token_hash("1"), purpose="signup", user_id=admin.id, **now))
    rejected(db, IdentityAuthToken(token_hash=token_hash("2"), purpose="reset", user_id=admin.id, **now))
    rejected(db, IdentityAuthToken(token_hash=token_hash("3"), purpose="invitation", user_id=admin.id, **now))
    rejected(db, IdentityAuthToken(token_hash=token_hash("4"), purpose="password_reset",
                                   invitation_id=invitation.id, **now))
    rejected(db, IdentityAuthToken(token_hash=token_hash("5"), purpose="invitation", **now))
    rejected(db, IdentityAuthToken(token_hash=token_hash("6"), purpose="password_reset", user_id=admin.id,
                                   invitation_id=invitation.id, **now))


def test_token_expiry_and_single_use_representation(db):
    user = add_user(db)
    rejected(db, make_reset(user, "e", expires_at=NOW))  # must expire after creation
    rejected(db, make_reset(user, "c", consumed_at=NOW - timedelta(seconds=1)))
    token = make_reset(user, "ok")
    db.add(token)
    db.commit()
    assert token.consumed_at is None and token.invalidated_at is None
    # Single use is one conditional UPDATE: only the first caller changes a row.
    consume = (update(IdentityAuthToken)
               .where(IdentityAuthToken.id == token.id, IdentityAuthToken.consumed_at.is_(None))
               .values(consumed_at=NOW + timedelta(minutes=5)))
    assert db.execute(consume).rowcount == 1
    assert db.execute(consume).rowcount == 0


def test_token_timestamp_constraints(db):
    """Each new check is violated once. They keep rows coherent; they do not make a token single-use."""
    user = add_user(db)
    later, earlier = NOW + timedelta(minutes=5), NOW - timedelta(seconds=1)
    rejected(db, make_reset(user, "a", consumed_at=earlier))  # consumed before created
    rejected(db, make_reset(user, "b", consumed_at=NOW + timedelta(hours=2)))  # consumed after expiry
    rejected(db, make_reset(user, "c", invalidated_at=earlier))  # invalidated before created
    rejected(db, make_reset(user, "d", consumed_at=later, invalidated_at=later))  # both outcomes
    # Boundaries and each single outcome are allowed.
    db.add(make_reset(user, "e", consumed_at=NOW))
    db.add(make_reset(user, "f", consumed_at=NOW + timedelta(hours=1)))  # exactly at expiry
    db.add(make_reset(user, "g", invalidated_at=NOW))
    db.add(make_reset(user, "h", invalidated_at=NOW + timedelta(days=1)))  # invalidation may follow expiry
    db.commit()


def test_token_cannot_be_invalidated_after_consumption_or_the_reverse(db):
    user = add_user(db)
    consumed, invalidated = make_reset(user, "x", consumed_at=NOW), make_reset(user, "y", invalidated_at=NOW)
    db.add_all([consumed, invalidated])
    db.commit()
    for token, column in ((consumed, "invalidated_at"), (invalidated, "consumed_at")):
        with pytest.raises(IntegrityError):
            db.execute(text(f"UPDATE identity_auth_tokens SET {column} = :t WHERE id = :id"),
                       {"t": NOW + timedelta(minutes=1), "id": token.id})
        db.rollback()


# --- audit ------------------------------------------------------------------

def test_audit_events_outlive_the_users_they_mention(db):
    user = add_user(db)
    db.add(IdentityAuditEvent(actor_user_id=user.id, target_user_id=user.id, action="deactivate"))
    db.add(IdentityAuditEvent(actor_user_id=424242, action="login_failed"))  # no FK: unknown id accepted
    db.commit()
    assert db.query(IdentityAuditEvent).count() == 2
    assert db.query(IdentityAuditEvent).first().details == "{}"
    rejected(db, IdentityAuditEvent(action=""))


# --- engine and settings ----------------------------------------------------

def test_no_url_string_helper_bypasses_database_targets():
    import identity_database

    assert not hasattr(identity_database, "identity_database_url")
    assert not hasattr(identity_database, "create_identity_engine")  # no public URL-string constructor


def test_session_dependency_yields_and_closes(engine):
    dependency = identity_db_dependency(create_identity_session_factory(engine))
    gen = dependency()
    session = next(gen)
    session.add(IdentityUser(email="dep@example.com"))
    session.commit()
    with pytest.raises(StopIteration):
        next(gen)
    assert session.in_transaction() is False


def test_independent_of_commercial_base_and_engine():
    import models  # commercial Base; importing it does not touch a database file

    assert IdentityBase is not models.Base
    assert not set(IdentityBase.metadata.tables) & set(models.Base.metadata.tables)
    assert all(t.startswith("identity_") for t in IdentityBase.metadata.tables)
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]);"
         "import identity_models, identity_database, create_identity_db;"
         "bad = {'models', 'database', 'deps', 'routes'} & set(sys.modules);"
         "sys.exit(f'imports commercial modules: {bad}' if bad else 0)", str(BACKEND_DIR)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


# --- creation utility -------------------------------------------------------

def test_create_database_in_new_file(tmp_path):
    path = tmp_path / "pm-identity.db"
    assert create_identity_database(str(path)) == path
    eng = open_created(path)
    assert "identity_users" in inspect(eng).get_table_names()
    with eng.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM identity_users")).scalar() == 0
    eng.dispose()
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_refuses_to_overwrite_existing_file_and_leaves_it_untouched(tmp_path):
    path = tmp_path / "existing.db"
    path.write_bytes(b"precious")
    with pytest.raises(IdentityDatabaseError, match="already exists"):
        create_identity_database(str(path))
    assert path.read_bytes() == b"precious"


def test_refuses_existing_symlink_even_if_dangling(tmp_path):
    link = tmp_path / "link.db"
    link.symlink_to(tmp_path / "nowhere.db")
    with pytest.raises(IdentityDatabaseError, match="already exists"):
        check_target(str(link))
    assert not (tmp_path / "nowhere.db").exists()


def test_does_not_create_missing_parent_directory(tmp_path):
    with pytest.raises(IdentityDatabaseError, match="does not exist"):
        create_identity_database(str(tmp_path / "missing" / "id.db"))
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("value", ["", ":memory:", "sqlite:///x.db", "sqlite:////tmp/x.db", "postgresql://h/db"])
def test_refuses_urls_and_memory(value):
    with pytest.raises(IdentityDatabaseError, match="file path"):
        check_target(value)


@pytest.mark.parametrize("rel", [
    "pm.db", "pm.db.bak", "pm.db-wal", "pm-commercial.db", "pm-commercial.db-shm",
    "PM.DB", "Pm.Db.Bak", "PM-Commercial.DB", "pm-commercial.db-WAL",
])
def test_refuses_protected_names_in_any_directory(tmp_path, rel):
    with pytest.raises(IdentityDatabaseError, match="development or commercial"):
        check_target(str(tmp_path / rel))


@pytest.mark.parametrize("name", [
    "anything.db-wal", "x-shm", "identity.sqlite-journal", "ID.DB-WAL", "id.db-Shm", "id.db-JOURNAL", "-wal",
])
def test_refuses_any_sidecar_suffix_regardless_of_base_name(tmp_path, name):
    with pytest.raises(IdentityDatabaseError, match="development or commercial"):
        check_target(str(tmp_path / name))
    assert not (tmp_path / name).exists()


def test_sidecar_like_text_in_the_middle_of_a_name_is_allowed(tmp_path):
    assert check_target(str(tmp_path / "id-wal.db")) == tmp_path / "id-wal.db"


def test_refuses_development_and_commercial_paths_by_string():
    for protected in create_identity_db.PROTECTED_PATHS:
        for suffix in ("", "-wal"):
            with pytest.raises(IdentityDatabaseError, match="development or commercial"):
                check_target(str(protected) + suffix)
    with pytest.raises(IdentityDatabaseError, match="'\\.\\.'"):
        check_target(str(BACKEND_DIR / "static" / ".." / "pm.db"))
    with pytest.raises(IdentityDatabaseError, match="development or commercial"):
        check_target(str(BACKEND_DIR / "static" / "." / "pm.db"))


def test_refuses_the_path_named_by_database_url_without_using_it_as_default(tmp_path, monkeypatch):
    commercial = tmp_path / "some-commercial-name.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{commercial}")
    with pytest.raises(IdentityDatabaseError, match="development or commercial"):
        check_target(str(commercial))
    assert not commercial.exists()
    assert check_target(str(tmp_path / "other.db")) == tmp_path / "other.db"


def test_database_url_path_is_compared_case_insensitively(tmp_path, monkeypatch):
    commercial = tmp_path / "Some-Commercial.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{commercial}")
    with pytest.raises(IdentityDatabaseError, match="development or commercial"):
        check_target(str(tmp_path / "SOME-COMMERCIAL.DB"))


def test_refuses_a_protected_name_reached_through_a_symlinked_directory(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (tmp_path / "alias").symlink_to(real_dir)
    with pytest.raises(IdentityDatabaseError, match="development or commercial"):
        check_target(str(tmp_path / "alias" / "pm.db"))


def test_failed_schema_creation_removes_the_new_file(tmp_path, monkeypatch):
    def boom(_engine):
        raise RuntimeError("boom")

    monkeypatch.setattr(create_identity_db, "create_identity_schema", boom)
    path = tmp_path / "id.db"
    with pytest.raises(RuntimeError):
        create_identity_database(str(path))
    assert not path.exists()


@pytest.mark.parametrize("name", ["with space.db", "what?.db", "hash#tag.db", "50%.db", "a b?c#d.db"])
def test_paths_with_special_characters_create_the_exact_file(tmp_path, name):
    target = create_identity_database(str(tmp_path / name))
    assert target == tmp_path / name
    assert sorted(p.name for p in tmp_path.iterdir()) == [name]  # nothing created under a mangled name
    eng = create_engine(URL.create("sqlite", database=str(target)))
    assert "identity_users" in inspect(eng).get_table_names()
    eng.dispose()


def test_cleanup_removes_only_the_file_this_call_created(tmp_path, monkeypatch):
    bystander = tmp_path / "bystander.db"
    bystander.write_bytes(b"keep")

    def boom(_engine):
        raise RuntimeError("boom")

    monkeypatch.setattr(create_identity_db, "create_identity_schema", boom)
    with pytest.raises(RuntimeError):
        create_identity_database(str(tmp_path / "new.db"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bystander.db"]
    assert bystander.read_bytes() == b"keep"


def test_cleanup_does_not_delete_a_file_substituted_for_the_created_one(tmp_path, monkeypatch, capsys):
    target = tmp_path / "new.db"

    def swap_then_fail(_engine):
        target.unlink()
        target.write_bytes(b"substitute")  # a different file now sits at the path
        raise RuntimeError("boom")

    monkeypatch.setattr(create_identity_db, "create_identity_schema", swap_then_fail)
    with pytest.raises(RuntimeError):
        create_identity_database(str(target))
    assert target.read_bytes() == b"substitute"
    err = capsys.readouterr().err
    assert "leftover" in err and str(tmp_path) not in err


def test_cleanup_does_not_follow_a_symlink_substituted_for_the_created_file(tmp_path, monkeypatch):
    target = tmp_path / "new.db"
    victim = tmp_path / "victim.db"
    victim.write_bytes(b"keep")

    def swap_then_fail(_engine):
        target.unlink()
        target.symlink_to(victim)
        raise RuntimeError("boom")

    monkeypatch.setattr(create_identity_db, "create_identity_schema", swap_then_fail)
    with pytest.raises(RuntimeError):
        create_identity_database(str(target))
    assert victim.read_bytes() == b"keep" and target.is_symlink()


def test_engine_creation_failure_removes_the_new_file(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise OperationalError("connect", {}, Exception("x"))

    monkeypatch.setattr(create_identity_db, "create_engine", boom)
    assert create_identity_db.main(["--database", str(tmp_path / "x.db")]) == 1
    assert list(tmp_path.iterdir()) == []


def test_cleanup_still_runs_when_engine_dispose_raises(tmp_path, monkeypatch, capsys):
    def bad_engine(*_args, **_kwargs):
        engine = create_engine("sqlite://")  # a real engine (the creator attaches a listener); nothing is opened

        def dispose(*_a, **_k):
            raise RuntimeError("dispose failed")

        engine.dispose = dispose
        return engine

    def boom(_engine):
        raise RuntimeError("boom")

    monkeypatch.setattr(create_identity_db, "create_engine", bad_engine)
    monkeypatch.setattr(create_identity_db, "create_identity_schema", boom)
    with pytest.raises(RuntimeError, match="boom"):
        create_identity_database(str(tmp_path / "x.db"))
    assert list(tmp_path.iterdir()) == []
    assert "connection pool" in capsys.readouterr().err


def test_unlink_failure_warns_without_traceback_or_path(tmp_path, monkeypatch, capsys):
    def broken(_engine):
        raise OperationalError("CREATE TABLE", {}, Exception("disk I/O error"))

    def no_unlink(self, *a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(create_identity_db, "create_identity_schema", broken)
    monkeypatch.setattr(Path, "unlink", no_unlink)
    assert create_identity_db.main(["--database", str(tmp_path / "x.db")]) == 1
    err = capsys.readouterr().err
    assert "Error: could not create the schema (OperationalError)" in err
    assert "leftover file may remain" in err or "leftover" in err
    assert "Traceback" not in err and str(tmp_path) not in err


def test_existing_path_is_never_removed_when_creation_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "existing.db"
    path.write_bytes(b"precious")
    called = []
    monkeypatch.setattr(create_identity_db, "create_identity_schema", lambda e: called.append(e))
    with pytest.raises(IdentityDatabaseError):
        create_identity_database(str(path))
    assert path.read_bytes() == b"precious" and not called


def test_cli_reports_os_errors_concisely_without_traceback(tmp_path, capsys, monkeypatch):
    def denied(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(create_identity_db.os, "open", denied)
    assert create_identity_db.main(["--database", str(tmp_path / "x.db")]) == 1
    err = capsys.readouterr().err
    assert err.strip() == "Error: could not create the database: Permission denied"
    assert "Traceback" not in err and str(tmp_path) not in err
    assert not (tmp_path / "x.db").exists()


def test_cli_reports_schema_errors_concisely_and_cleans_up(tmp_path, capsys, monkeypatch):
    def broken(_engine):
        raise OperationalError("CREATE TABLE secret_detail", {}, Exception("disk I/O error"))

    monkeypatch.setattr(create_identity_db, "create_identity_schema", broken)
    assert create_identity_db.main(["--database", str(tmp_path / "x.db")]) == 1
    err = capsys.readouterr().err
    assert err.strip() == "Error: could not create the schema (OperationalError)"
    assert not (tmp_path / "x.db").exists()


def test_cli_success_and_failure_output(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "cli.db"
    assert create_identity_db.main(["--database", str(path)]) == 0
    out = capsys.readouterr().out
    assert str(path) in out and "SECRET" not in out.upper()
    assert create_identity_db.main(["--database", str(path)]) == 1
    assert "already exists" in capsys.readouterr().err
    with pytest.raises(SystemExit):  # --database is required: no implicit path
        create_identity_db.main([])


# --- schema recognition and special-character paths -------------------------

def test_create_schema_refuses_foreign_non_empty_database(tmp_path):
    path = tmp_path / "foreign.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE other (id INTEGER)")
    conn.commit()
    conn.close()
    before = path.read_bytes()
    eng = create_engine(URL.create("sqlite", database=str(path)))
    try:
        with pytest.raises(IdentitySchemaVersionError):
            create_identity_schema(eng)
    finally:
        eng.dispose()
    assert path.read_bytes() == before  # nothing created, stamped or repaired


def test_create_schema_is_idempotent_on_a_recognized_database(tmp_path):
    eng = open_created(create_identity_database(str(tmp_path / "id.db")))
    try:
        create_identity_schema(eng)
        with eng.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA user_version").scalar() == IDENTITY_SCHEMA_VERSION
    finally:
        eng.dispose()


def test_foreign_file_with_the_current_user_version_is_refused_and_unchanged(tmp_path):
    path = tmp_path / "foreign.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE other (id INTEGER)")
    conn.execute(f"PRAGMA user_version = {IDENTITY_SCHEMA_VERSION}")
    conn.commit()
    conn.close()
    before = path.read_bytes()
    with pytest.raises(IdentitySchemaVersionError, match="missing identity tables"):
        open_created(path)
    assert path.read_bytes() == before


def test_partial_identity_schema_with_the_current_version_is_refused(tmp_path):
    path = tmp_path / "partial.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE identity_users (id INTEGER)")
    conn.execute(f"PRAGMA user_version = {IDENTITY_SCHEMA_VERSION}")
    conn.commit()
    conn.close()
    with pytest.raises(IdentitySchemaVersionError):
        open_created(path)


def test_non_empty_unversioned_database_is_refused_by_create_schema(tmp_path):
    eng = create_in_memory_identity_engine()
    try:
        with eng.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE identity_users (id INTEGER)")  # tables, but version 0
        with pytest.raises(IdentitySchemaVersionError):
            create_identity_schema(eng)
        with eng.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA user_version").scalar() == 0
            assert inspect(conn).get_table_names() == ["identity_users"]
    finally:
        eng.dispose()


def test_special_character_path_is_created_validated_and_opened(tmp_path):
    special = tmp_path / "what?#% and space.db"
    assert create_identity_database(str(special)) == special
    link = tmp_path / "plain.db"  # a plain URL can name it only through a link: '?' is URL syntax
    link.symlink_to(special)
    assert os.path.realpath(special).endswith("what?#% and space.db")
    engine = open_created(link)
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert conn.exec_driver_sql("SELECT count(*) FROM identity_users").scalar() == 0
    finally:
        engine.dispose()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plain.db", "what?#% and space.db"]


def test_cli_refuses_a_path_assigned_to_a_company_even_if_missing(tmp_path, monkeypatch):
    company = tmp_path / "lity.db"
    monkeypatch.setenv("PM_COMPANY_DB_LITY", f"sqlite:///{company}")
    with pytest.raises(IdentityDatabaseError, match="configured database"):
        check_target(str(company))
    with pytest.raises(IdentityDatabaseError, match="configured database"):
        check_target(str(tmp_path / "." / "LITY.DB"))
    assert not company.exists()
    assert check_target(str(tmp_path / "other.db")) == tmp_path / "other.db"


@pytest.mark.parametrize("value", ["sqlite:///x.db?mode=ro", "sqlite+aiosqlite:///x.db", "sqlite:///~/x.db", "sqlite:///file:x.db"])
def test_cli_fails_closed_on_unsupported_sqlite_database_url(tmp_path, monkeypatch, value):
    monkeypatch.setenv("DATABASE_URL", value)
    with pytest.raises(IdentityDatabaseError, match="Refusing"):
        check_target(str(tmp_path / "new.db"))


def test_cli_rejects_nul_in_path(tmp_path):
    with pytest.raises(IdentityDatabaseError, match="not a valid path"):
        check_target(str(tmp_path / "a\x00b.db"))


# --- creating a schema only on a truly new database ---

def _engine_for(path):
    return create_engine(URL.create("sqlite", database=str(path)))


def _make(path, *statements, version=0):
    conn = sqlite3.connect(path)
    for statement in statements:
        conn.execute(statement)
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()


def test_create_schema_initializes_an_empty_database_with_version_zero(tmp_path):
    path = tmp_path / "empty.db"
    _make(path)
    eng = _engine_for(path)
    try:
        create_identity_schema(eng)
    finally:
        eng.dispose()
    open_created(path).dispose()  # recognized now


def test_create_schema_refuses_an_empty_database_with_unexpected_version(tmp_path):
    path = tmp_path / "v7.db"
    _make(path, version=7)
    before = path.read_bytes()
    eng = _engine_for(path)
    try:
        with pytest.raises(IdentitySchemaVersionError, match="version 7"):
            create_identity_schema(eng)
    finally:
        eng.dispose()
    assert path.read_bytes() == before


@pytest.mark.parametrize("name,statements", [
    ("view", ["CREATE VIEW v AS SELECT 1 AS x"]),
    ("trigger", ["CREATE TABLE t (id INTEGER)", "CREATE TRIGGER trg AFTER INSERT ON t BEGIN SELECT 1; END"]),
    ("index", ["CREATE TABLE t (id INTEGER)", "CREATE INDEX ix ON t (id)"]),
])
def test_create_schema_refuses_a_database_with_other_objects(tmp_path, name, statements):
    path = tmp_path / f"{name}.db"
    _make(path, *statements)
    before = path.read_bytes()
    eng = _engine_for(path)
    try:
        with pytest.raises(IdentitySchemaVersionError):
            create_identity_schema(eng)
    finally:
        eng.dispose()
    assert path.read_bytes() == before  # nothing created, stamped or repaired


def test_create_schema_leaves_a_valid_existing_identity_schema_byte_identical(tmp_path):
    path = create_identity_database(str(tmp_path / "id.db"))
    before = path.read_bytes()
    eng = _engine_for(path)
    try:
        create_identity_schema(eng)
    finally:
        eng.dispose()
    assert path.read_bytes() == before


# --- runtime opening never creates a file ---

def test_open_fails_without_creating_a_replacement_when_the_database_disappears(tmp_path, monkeypatch):
    # Simulates the race deterministically: validation (stubbed) passes for a path whose file is gone.
    gone = tmp_path / "gone.db"
    monkeypatch.setattr("identity_database.identity_database_path", lambda environ=None: gone)
    with pytest.raises(OperationalError):
        open_identity_engine({})
    assert not gone.exists()
    assert list(tmp_path.iterdir()) == []


def test_open_uses_rw_mode_and_keeps_foreign_keys_for_special_characters(tmp_path):
    path = create_identity_database(str(tmp_path / "what?#% x.db"))
    link = tmp_path / "plain.db"
    link.symlink_to(path)
    eng = open_created(link)
    try:
        assert eng.url.query["mode"] == "rw"
        with eng.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        eng.dispose()
