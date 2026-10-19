"""Identity schema v2 and migrate_identity_v2.py.

Every database here is a synthetic one created inside pytest's tmp_path (or in memory). No development,
commercial, acceptance or other real database is opened; the protected-path tests use files this test creates.
"""
import os
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateIndex, CreateTable

import migrate_identity_v2 as mig
from create_identity_db import create_identity_database
from identity_database import IdentitySchemaVersionError, open_identity_engine
from identity_models import SCHEMA_ROLES, IdentityBase
from identity_schema import IDENTITY_SCHEMA_VERSION, normalize_sql, schema_problems
from migrate_stage_kind import MigrationError

HASH = "a" * 64
T0, T1 = "2026-01-01 12:00:00", "2026-02-01 12:00:00"
OLD_CHECK = "'sales_member', 'visitor', 'admin'"
NEW_V1_CHECK = "'sales_member', 'visitor'"


def make_v1(path: Path, *, populate: bool = True) -> Path:
    """A synthetic version-1 database: the models' DDL with the v1 role CHECKs, built independently of the migration."""
    dialect = sqlite_dialect.dialect()
    conn = sqlite3.connect(path, isolation_level=None)
    for table in IdentityBase.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect)).replace(OLD_CHECK, NEW_V1_CHECK)
        conn.execute(ddl)
        for index in table.indexes:
            conn.execute(str(CreateIndex(index).compile(dialect=dialect)))
    conn.execute("PRAGMA user_version = 1")
    if populate:
        conn.executescript(f"""
            INSERT INTO identity_users (id, email, is_global_admin, is_active, session_version, created_at)
                VALUES (1, 'root@example.com', 1, 1, 0, '{T0}'), (5, 'a@example.com', 0, 1, 2, '{T0}'),
                       (9, 'b@example.com', 0, 1, 0, '{T0}');
            INSERT INTO identity_companies (id, slug, name, is_active, created_at)
                VALUES (1, 'salcomp', 'Salcomp', 1, '{T0}'), (4, 'lity', 'Lity', 1, '{T0}');
            INSERT INTO identity_memberships (id, user_id, company_id, role, is_active, created_at)
                VALUES (3, 5, 1, 'sales_member', 1, '{T0}'), (7, 5, 4, 'visitor', 0, '{T0}'),
                       (12, 9, 1, 'visitor', 1, '{T0}');
            INSERT INTO identity_invitations (id, email, company_id, role, invited_by_user_id, created_at, expires_at,
                                              accepted_at, accepted_user_id, revoked_at)
                VALUES (2, 'open@example.com', 1, 'visitor', 1, '{T0}', '{T1}', NULL, NULL, NULL),
                       (6, 'done@example.com', 1, 'sales_member', 1, '{T0}', '{T1}', '{T0}', 5, NULL),
                       (11, 'gone@example.com', 4, 'visitor', 1, '{T0}', '{T1}', NULL, NULL, '{T0}');
            INSERT INTO identity_auth_tokens (id, token_hash, purpose, user_id, invitation_id, created_at, expires_at)
                VALUES (1, '{HASH}', 'invitation', NULL, 2, '{T0}', '{T1}'),
                       (2, '{"b" * 64}', 'password_reset', 5, NULL, '{T0}', '{T1}');
            INSERT INTO identity_audit_events (id, actor_user_id, action, details, created_at)
                VALUES (1, 1, 'seed', '{{}}', '{T0}');
            -- the highest ids are deleted, so the AUTOINCREMENT counters stay above MAX(id)
            INSERT INTO identity_memberships (id, user_id, company_id, role, created_at) VALUES (40, 9, 4, 'visitor', '{T0}');
            DELETE FROM identity_memberships WHERE id = 40;
            INSERT INTO identity_invitations (id, email, company_id, role, invited_by_user_id, created_at, expires_at)
                VALUES (30, 'tmp@example.com', 4, 'visitor', 1, '{T0}', '{T1}');
            DELETE FROM identity_invitations WHERE id = 30;
        """)
    conn.close()
    return path


def connect(path) -> sqlite3.Connection:
    return sqlite3.connect(path, isolation_level=None)


def dump(path) -> dict:
    """Everything observable about a database, for before/after equality."""
    conn = connect(path)
    try:
        return {
            "version": conn.execute("PRAGMA user_version").fetchone()[0],
            "schema": sorted(conn.execute("SELECT type, name, sql FROM sqlite_master")),
            "sequences": sorted(conn.execute("SELECT name, seq FROM sqlite_sequence")),
            "rows": {
                t: conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall() for t in IdentityBase.metadata.tables
            },
        }
    finally:
        conn.close()


def normalized_schema(path) -> dict:
    conn = connect(path)
    try:
        return {
            (kind, name): normalize_sql(sql)
            for kind, name, sql in conn.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite\\_%' ESCAPE '\\'")
        }
    finally:
        conn.close()


@pytest.fixture
def v1(tmp_path):
    return make_v1(tmp_path / "identity-v1.db")


def run(path):
    return mig.apply(str(path))


# --- 1, 3: the fresh schema -------------------------------------------------

def test_fresh_database_has_the_v2_schema(tmp_path):
    path = create_identity_database(str(tmp_path / "fresh.db"))
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == IDENTITY_SCHEMA_VERSION == 2
    assert schema_problems(conn) == []
    for table, constraint in mig.ROLE_CHECKS.items():
        assert mig.role_check_roles(conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0], constraint) == SCHEMA_ROLES
    conn.close()
    open_identity_engine({"IDENTITY_DATABASE_URL": f"sqlite:///{path}"}).dispose()


def assert_admin_accepted_and_invalid_roles_rejected(path):
    """Roles are exercised in both rebuilt tables; the rows are rolled back so the database is unchanged."""
    conn = connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    user, company = conn.execute("SELECT MIN(id) FROM identity_users").fetchone()[0], conn.execute("SELECT MIN(id) FROM identity_companies").fetchone()[0]
    membership = "INSERT INTO identity_memberships (user_id, company_id, role, created_at) VALUES (?, ?, ?, ?)"
    invitation = ("INSERT INTO identity_invitations (email, company_id, role, invited_by_user_id, created_at, expires_at) "
                  "VALUES ('role-probe@example.com', ?, ?, ?, ?, ?)")
    conn.execute("BEGIN")
    try:
        # a user/company pair that has no membership yet, so only the role can fail
        probe_user = conn.execute("INSERT INTO identity_users (email, created_at) VALUES ('probe@example.com', ?)", (T0,)).lastrowid
        conn.execute(membership, (probe_user, company, "admin", T0))
        conn.execute(invitation, (company, "admin", user, T0, T1))
        for table_sql, args in ((membership, lambda r: (probe_user, company, r, T0)), (invitation, lambda r: (company, r, user, T0, T1))):
            for role in ("super_admin", "global_admin", "owner", "Admin", ""):
                with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                    conn.execute("SAVEPOINT s")
                    try:
                        conn.execute(table_sql.replace("role-probe@example.com", "role-bad@example.com"), args(role))
                    finally:
                        conn.execute("ROLLBACK TO s")
    finally:
        conn.execute("ROLLBACK")
    conn.close()


def test_fresh_database_accepts_admin_and_rejects_other_roles(tmp_path):
    fresh = create_identity_database(str(tmp_path / "fresh.db"))
    conn = connect(fresh)
    conn.executescript(f"""
        INSERT INTO identity_users (email, created_at) VALUES ('a@example.com', '{T0}');
        INSERT INTO identity_companies (slug, name, created_at) VALUES ('salcomp', 'S', '{T0}');
    """)
    conn.close()
    assert_admin_accepted_and_invalid_roles_rejected(fresh)


# --- 2-12: a successful migration -------------------------------------------

def test_v1_database_migrates_to_v2(v1):
    before, after, backup = run(v1)
    assert before["user_version"] == 1 and after["user_version"] == 2
    assert before["role_checks"] == {t: ("sales_member", "visitor") for t in mig.ROLE_CHECKS}
    assert after["role_checks"] == {t: SCHEMA_ROLES for t in mig.ROLE_CHECKS}
    conn = connect(v1)
    assert schema_problems(conn) == []
    conn.close()
    open_identity_engine({"IDENTITY_DATABASE_URL": f"sqlite:///{v1}"}).dispose()  # the application accepts it
    assert os.path.isfile(backup) and dump(backup)["version"] == 1  # a verified copy of the previous state


def test_migrated_schema_matches_a_fresh_v2_schema(v1, tmp_path):
    run(v1)
    fresh = create_identity_database(str(tmp_path / "fresh.db"))
    assert normalized_schema(v1) == normalized_schema(fresh)


def test_all_rows_ids_and_values_are_preserved(tmp_path):
    original = make_v1(tmp_path / "original.db")
    target = make_v1(tmp_path / "target.db")
    before = dump(original)
    run(target)
    after = dump(target)
    assert after["rows"] == before["rows"]  # users, companies, memberships, invitations, tokens, audit: all rows, all values
    assert [r[0] for r in after["rows"]["identity_memberships"]] == [3, 7, 12]
    assert [r[0] for r in after["rows"]["identity_invitations"]] == [2, 6, 11]
    assert [r[0] for r in after["rows"]["identity_users"]] == [1, 5, 9]


def test_auth_token_references_stay_valid(v1):
    run(v1)
    conn = connect(v1)
    conn.execute("PRAGMA foreign_keys = ON")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute(
        "SELECT t.id, i.email FROM identity_auth_tokens t JOIN identity_invitations i ON i.id = t.invitation_id"
    ).fetchall() == [(1, "open@example.com")]
    fks = {row[2] for row in conn.execute("PRAGMA foreign_key_list(identity_auth_tokens)")}
    assert fks == {"identity_users", "identity_invitations"}  # still pointing at the final table names, not the temporary ones
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):  # and still enforced
        conn.execute("INSERT INTO identity_auth_tokens (token_hash, purpose, invitation_id, created_at, expires_at) VALUES (?, 'invitation', 999, ?, ?)", ("c" * 64, T0, T1))
    conn.close()


def test_required_indexes_are_present_including_the_partial_unique_one(v1):
    run(v1)
    conn = connect(v1)
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}
    assert {"ix_identity_memberships_company_id", "ix_identity_invitations_company_id", "uq_identity_invitations_open",
            "ix_identity_auth_tokens_user_id", "ix_identity_auth_tokens_invitation_id"} <= names
    (sql,) = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'uq_identity_invitations_open'").fetchone()
    assert "UNIQUE" in sql and "WHERE accepted_at IS NULL AND revoked_at IS NULL" in sql
    with pytest.raises(sqlite3.IntegrityError):  # the open-invitation uniqueness still holds
        conn.execute("INSERT INTO identity_invitations (email, company_id, role, invited_by_user_id, created_at, expires_at) VALUES ('open@example.com', 1, 'visitor', 1, ?, ?)", (T0, T1))
    conn.close()


def test_admin_is_accepted_and_invalid_roles_stay_rejected_after_migration(v1):
    run(v1)
    assert_admin_accepted_and_invalid_roles_rejected(v1)


def test_v1_rejects_admin_before_migration(v1):
    conn = connect(v1)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute("INSERT INTO identity_memberships (user_id, company_id, role, created_at) VALUES (9, 4, 'admin', ?)", (T0,))
    conn.close()


def test_integrity_and_foreign_keys_are_clean(v1):
    _, after, _ = run(v1)
    assert after["integrity"] == "ok" and after["foreign_key_violations"] == 0
    conn = connect(v1)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_sequences_never_decrease_even_when_the_highest_id_was_deleted(v1):
    before = dump(v1)["sequences"]
    seq = dict(before)
    assert seq["identity_memberships"] == 40 and seq["identity_invitations"] == 30  # above MAX(id) 12 and 11
    run(v1)
    after = dict(dump(v1)["sequences"])
    assert after == seq  # every counter, rebuilt or not, is at least (here exactly) what it was
    conn = connect(v1)
    new_id = conn.execute("INSERT INTO identity_memberships (user_id, company_id, role, created_at) VALUES (9, 4, 'visitor', ?)", (T0,)).lastrowid
    assert new_id == 41  # a deleted id is never reused
    conn.close()


def test_sequences_of_empty_tables_are_kept(tmp_path):
    path = make_v1(tmp_path / "empty-v1.db", populate=False)
    conn = connect(path)
    conn.executescript(f"""
        INSERT INTO identity_users (id, email, created_at) VALUES (1, 'a@example.com', '{T0}');
        INSERT INTO identity_companies (id, slug, name, created_at) VALUES (1, 'salcomp', 'S', '{T0}');
        INSERT INTO identity_memberships (id, user_id, company_id, role, created_at) VALUES (8, 1, 1, 'visitor', '{T0}');
        DELETE FROM identity_memberships;
    """)
    conn.close()
    run(path)
    assert ("identity_memberships", 8) in dump(path)["sequences"]
    assert dict(dump(path)["sequences"]).get("identity_invitations", 0) == 0  # never used: no counter invented (SQLite may record 0)


def test_an_empty_v1_database_migrates(tmp_path):
    path = make_v1(tmp_path / "empty-v1.db", populate=False)
    run(path)
    conn = connect(path)
    assert schema_problems(conn) == [] and conn.execute("PRAGMA user_version").fetchone()[0] == 2
    conn.close()


# --- 13: a failure leaves nothing behind -------------------------------------

@pytest.mark.parametrize("failing_step", ["copied identity_memberships", "rebuilt identity_memberships", "copied identity_invitations", "rebuilt identity_invitations", "verified"])
def test_a_forced_failure_rolls_everything_back(v1, failing_step):
    before = dump(v1)

    def boom(step):
        if step == failing_step:
            raise RuntimeError("injected failure")

    conn = connect(v1)
    with pytest.raises(RuntimeError, match="injected"):
        mig.migrate(conn, _after_step=boom)
    assert not conn.in_transaction
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # restored
    conn.close()
    assert dump(v1) == before  # schema text, version, sequences and every row, unchanged
    connect(v1).close()
    # and the database is still migratable afterwards
    run(v1)
    assert dump(v1)["version"] == 2


def test_a_dirty_foreign_key_aborts_and_rolls_back(v1):
    conn = connect(v1)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO identity_memberships (id, user_id, company_id, role, created_at) VALUES (50, 999, 1, 'visitor', ?)", (T0,))
    conn.close()
    before = dump(v1)
    with pytest.raises(MigrationError, match="foreign key"):
        run(v1)
    assert dump(v1) == before


# --- 14, 15: repeat runs and dry run -----------------------------------------

def test_a_second_migration_is_refused_and_changes_nothing(v1):
    run(v1)
    done = dump(v1)
    with pytest.raises(MigrationError, match="already at identity schema version 2"):
        run(v1)
    assert dump(v1) == done


@pytest.mark.parametrize("version", [0, 3])
def test_other_versions_are_refused(tmp_path, version):
    path = make_v1(tmp_path / "x.db")
    conn = connect(path)
    conn.execute(f"PRAGMA user_version = {version}")
    conn.close()
    before = dump(path)
    with pytest.raises(MigrationError, match=f"version {version}"):
        run(path)
    assert dump(path) == before


def test_a_v1_marker_on_a_foreign_schema_is_refused(tmp_path):
    path = tmp_path / "foreign.db"
    conn = connect(path)
    conn.execute("CREATE TABLE identity_users (id INTEGER)")
    conn.execute("PRAGMA user_version = 1")
    conn.close()
    before = dump_raw(path)
    with pytest.raises(MigrationError, match="not the v1 schema"):
        run(path)
    assert dump_raw(path) == before


def dump_raw(path):
    conn = connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0], conn.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
    finally:
        conn.close()


def test_dry_run_does_not_mutate_the_database_or_leave_files(v1, capsys):
    before_bytes = v1.read_bytes()
    before_listing = sorted(os.listdir(v1.parent))
    assert mig.main(["--database", str(v1)]) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and "nothing was written" in out and "schema version: 1 -> 2" in out
    assert "@example.com" not in out and HASH not in out  # counts and ids only
    assert v1.read_bytes() == before_bytes
    assert sorted(os.listdir(v1.parent)) == before_listing  # no backup, no journal


def test_apply_needs_the_explicit_flags(v1, capsys):
    before = v1.read_bytes()
    assert mig.main(["--database", str(v1), "--apply"]) == 1
    assert "--confirm-app-stopped" in capsys.readouterr().err
    assert v1.read_bytes() == before
    assert mig.main(["--database", str(v1), "--apply", "--confirm-app-stopped"]) == 0
    assert dump(v1)["version"] == 2
    assert mig.main(["--database", str(v1), "--apply", "--confirm-app-stopped"]) == 1  # second run refused
    assert mig.main(["--database", str(v1)]) == 1  # and a dry run on v2 says so


def test_database_argument_is_required():
    with pytest.raises(SystemExit):
        mig.main([])


# --- 16: protected targets ---------------------------------------------------

@pytest.mark.parametrize("name", ["pm.db", "PM.DB", "pm.db.bak", "pm-commercial.db"])
def test_protected_names_are_refused_in_any_directory(tmp_path, name):
    path = make_v1(tmp_path / name)
    before = path.read_bytes()
    with pytest.raises(MigrationError, match="Refusing"):
        mig.check_target(str(path))
    assert mig.main(["--database", str(path), "--apply", "--confirm-app-stopped"]) == 1
    assert path.read_bytes() == before


def test_the_primary_development_database_path_is_refused_without_being_opened(monkeypatch):
    for protected in database_targets_paths():
        with pytest.raises(MigrationError):
            mig.check_target(str(protected))


def database_targets_paths():
    import database_targets
    return database_targets.PROTECTED_PATHS


def test_relative_paths_urls_missing_files_and_symlinks_are_refused(v1, tmp_path):
    for bad in ("identity.db", "sqlite:////tmp/x.db", ":memory:", "", str(tmp_path / "missing.db")):
        with pytest.raises(MigrationError):
            mig.check_target(bad)
    link = tmp_path / "link.db"
    link.symlink_to(v1)
    with pytest.raises(MigrationError, match="symlink"):
        mig.check_target(str(link))


def test_sidecar_files_make_the_target_unusable(v1):
    Path(str(v1) + "-wal").write_bytes(b"")
    with pytest.raises(MigrationError, match="Refusing"):
        mig.check_target(str(v1))
    Path(str(v1) + "-wal").unlink()
    Path(str(v1) + "-journal").write_bytes(b"")
    with pytest.raises(MigrationError, match="Refusing"):
        mig.check_target(str(v1))


@pytest.mark.parametrize("variable", ["IDENTITY_DATABASE_URL", "DATABASE_URL", "PM_COMPANY_DB_SALCOMP"])
def test_a_database_configured_in_this_process_is_refused(v1, monkeypatch, variable):
    monkeypatch.setenv(variable, f"sqlite:///{v1}")
    with pytest.raises(MigrationError, match="Refusing"):
        mig.check_target(str(v1))
    assert mig.main(["--database", str(v1), "--apply", "--confirm-app-stopped"]) == 1
    assert dump(v1)["version"] == 1


def test_the_environment_never_supplies_a_target(v1, monkeypatch):
    monkeypatch.setenv("IDENTITY_DATABASE_URL", f"sqlite:///{v1}")
    with pytest.raises(SystemExit):
        mig.main([])


def test_the_acceptance_environment_directory_is_refused(tmp_path, monkeypatch):
    acceptance = tmp_path / "pm-products-accept"
    (acceptance / "data").mkdir(parents=True)
    path = make_v1(acceptance / "data" / "identity.db")
    monkeypatch.setattr(mig, "ACCEPTANCE_DATA_DIR", acceptance)
    before = path.read_bytes()
    with pytest.raises(MigrationError, match="acceptance"):
        mig.check_target(str(path))
    assert mig.main(["--database", str(path), "--apply", "--confirm-app-stopped"]) == 1
    assert path.read_bytes() == before


def test_the_real_acceptance_directory_constant_points_at_the_local_acceptance_environment():
    assert mig.ACCEPTANCE_DATA_DIR == Path.home() / "IA" / "pm-products-accept"


# --- 17: strict validation ---------------------------------------------------

def test_v2_marker_with_old_check_constraints_is_rejected(v1):
    conn = connect(v1)
    conn.execute("PRAGMA user_version = 2")  # stamped v2 but the tables are still v1
    conn.close()
    with pytest.raises(IdentitySchemaVersionError, match="role check"):
        open_identity_engine({"IDENTITY_DATABASE_URL": f"sqlite:///{v1}"})


@pytest.mark.parametrize("table", sorted(mig.ROLE_CHECKS))
def test_each_rebuilt_table_is_checked_on_its_own(tmp_path, table):
    path = create_identity_database(str(tmp_path / "id.db"))
    conn = connect(path)
    conn.execute("PRAGMA writable_schema = ON")
    (sql,) = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    conn.execute("UPDATE sqlite_master SET sql = ? WHERE name = ?", (sql.replace(OLD_CHECK, NEW_V1_CHECK), table))
    conn.close()
    with pytest.raises(IdentitySchemaVersionError, match=table):
        open_identity_engine({"IDENTITY_DATABASE_URL": f"sqlite:///{path}"})


def test_a_missing_required_index_is_rejected(tmp_path):
    path = create_identity_database(str(tmp_path / "id.db"))
    conn = connect(path)
    conn.execute("DROP INDEX uq_identity_invitations_open")
    conn.close()
    with pytest.raises(IdentitySchemaVersionError, match="uq_identity_invitations_open"):
        open_identity_engine({"IDENTITY_DATABASE_URL": f"sqlite:///{path}"})


def test_a_non_partial_open_invitation_index_is_rejected(tmp_path):
    path = create_identity_database(str(tmp_path / "id.db"))
    conn = connect(path)
    conn.execute("DROP INDEX uq_identity_invitations_open")
    conn.execute("CREATE UNIQUE INDEX uq_identity_invitations_open ON identity_invitations (email, company_id)")
    conn.close()
    with pytest.raises(IdentitySchemaVersionError, match="differs"):
        open_identity_engine({"IDENTITY_DATABASE_URL": f"sqlite:///{path}"})


def test_a_foreign_key_violation_is_rejected_on_open(tmp_path):
    path = create_identity_database(str(tmp_path / "id.db"))
    conn = connect(path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO identity_memberships (user_id, company_id, role, created_at) VALUES (1, 1, 'visitor', ?)", (T0,))
    conn.close()
    with pytest.raises(IdentitySchemaVersionError, match="foreign key"):
        open_identity_engine({"IDENTITY_DATABASE_URL": f"sqlite:///{path}"})


def test_a_v1_database_is_refused_by_the_application_and_left_untouched(v1):
    before = v1.read_bytes()
    with pytest.raises(IdentitySchemaVersionError, match="version 1 found, 2 expected"):
        open_identity_engine({"IDENTITY_DATABASE_URL": f"sqlite:///{v1}"})
    assert v1.read_bytes() == before


def test_company_admin_stays_unassignable_by_the_service():
    import identity_models
    import identity_service as svc
    assert "admin" not in identity_models.COMPANY_ROLES and "admin" in identity_models.SCHEMA_ROLES
    assert svc.ADMIN_ROLE == "admin" and svc.SUPER_ADMIN_ROLE == "super_admin"
    with pytest.raises(svc.InvalidInput):
        svc._check_role("admin")
