"""Upgrade readiness of an existing company database: the read-only schema diagnostic and the four additive init
scripts (products, Cards-import ledger, loss reasons, card reviews) against legacy-shaped databases.

Synthetic data and disposable tmp_path databases only; no real company database is opened.
"""
import hashlib
import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

import init_company_card_reviews as reviews_script
from company_schema import CompanySchemaError, validate_company_schema
from create_company_db import create_company_database
from database_targets import company_database_path, company_env_name
from diagnose_company_schema import ADDITIVE, diagnose_company, main as diagnose_main
from init_company_card_loss import init_company_card_loss
from init_company_card_reviews import init_company_card_reviews
from init_company_excel_imports import init_company_excel_imports
from init_company_products import init_company_products
from lead_options import PRODUCT_LINES
from test_company_card_loss import LOST, losses, put_move
from test_company_card_loss import world as loss_world  # noqa: F401  (fixture)
from test_company_card_reviews import edit, pending, reviews
from test_company_card_writes import fake_ai, rig  # noqa: F401  (fixtures)

ADDITIVE_TABLES = [a.table for a in ADDITIVE]
INIT = {
    "init_company_products.py": init_company_products,
    "init_company_excel_imports.py": init_company_excel_imports,
    "init_company_card_loss.py": init_company_card_loss,
    "init_company_card_reviews.py": init_company_card_reviews,
}


@pytest.fixture
def legacy(tmp_path):
    """A freshly created version-4 company database, its env mapping and the path. Mutate it with `run`."""
    env = {company_env_name("lity"): f"sqlite:///{tmp_path / 'lity.db'}"}
    create_company_database("lity", env)
    path = company_database_path("lity", env)

    def run(*statements):
        con = sqlite3.connect(path)
        try:
            for statement in statements:
                con.execute(statement)
            con.commit()
        finally:
            con.close()

    run("INSERT INTO users (id, username, created_at, updated_at) VALUES (7, 'ana', '2026-01-01', '2026-01-01')",
        "INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (1, 7, 'b', '2026-01-01', '2026-01-01')",
        "INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
        "VALUES (1, 1, 'First', 0, 'open', '2026-01-01', '2026-01-01')",
        "INSERT INTO cards (id, column_id, title, position, created_at, updated_at) "
        "VALUES (1, 1, 'card', 1, '2026-01-01', '2026-01-01')")
    return env, path, run


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def drop_additive(run):
    for table in ADDITIVE_TABLES:
        run(f"DROP TABLE {table}")


def tables(path):
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        con.close()


# --- the diagnostic --------------------------------------------------------------------------------------------------

def test_a_current_database_is_complete(legacy):
    env, path, _ = legacy
    before = digest(path)
    result = diagnose_company("lity", env)
    assert result.complete and result.version == 4 and result.scripts() == []
    assert digest(path) == before


def test_a_pre_phase_2_database_reports_every_additive_gap_and_the_scripts(legacy):
    env, path, run = legacy
    drop_additive(run)
    before = digest(path)
    result = diagnose_company("lity", env)
    assert not result.complete and not result.problems and not result.incompatible
    assert sorted(a.table for a in result.missing) == sorted(ADDITIVE_TABLES)
    assert result.scripts() == sorted(INIT)
    assert digest(path) == before  # nothing written, not even a journal


@pytest.mark.parametrize("dropped,script", [
    (("card_versions", "card_review_obligations"), "init_company_card_reviews.py"),
    (("card_loss_events",), "init_company_card_loss.py"),
    (("card_excel_imports",), "init_company_excel_imports.py"),
    (("products",), "init_company_products.py"),
])
def test_each_missing_structure_is_reported_alone(legacy, dropped, script):
    env, _, run = legacy
    for table in dropped:
        run(f"DROP TABLE {table}")
    result = diagnose_company("lity", env)
    assert [a.table for a in result.missing] == list(dropped) and result.scripts() == [script]


def test_incompatible_additive_tables_are_reported_and_have_no_script(legacy):
    env, path, run = legacy
    run("DROP INDEX uq_card_review_pending", "ALTER TABLE card_loss_events DROP COLUMN source")
    result = diagnose_company("lity", env)
    assert result.missing == [] and result.scripts() == []
    assert any("card_review_obligations" in t and "reviewer_role" in t for t in result.incompatible)
    assert any("card_loss_events" in t and "source" in t for t in result.incompatible)
    assert not result.complete


def test_required_gaps_are_reported_as_problems_not_init_scripts(legacy):
    env, _, run = legacy
    run("PRAGMA user_version = 3", "ALTER TABLE cards DROP COLUMN confidence_level", "DROP TABLE card_edit_history")
    result = diagnose_company("lity", env)
    text = " ".join(result.problems)
    assert "version 3" in text and "migrate_confidence_level.py" in text
    assert "card_edit_history is missing" in text and "confidence_level" in text
    assert result.scripts() == [] and not result.complete


def test_the_report_exposes_no_data_or_path(legacy, capsys, monkeypatch):
    env, path, run = legacy
    run("UPDATE cards SET title = 'SECRET-TITLE-123'")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    drop_after = ["DROP TABLE card_loss_events"]
    run(*drop_after)
    assert diagnose_main(["--company", "lity"]) == 1
    out = capsys.readouterr()
    assert "SECRET-TITLE-123" not in out.out + out.err and str(path.parent) not in out.out + out.err
    assert "init_company_card_loss.py" in out.out and "INCOMPLETE" in out.out


def test_exit_codes_complete_and_unrunnable(legacy, tmp_path, monkeypatch, capsys):
    env, path, _ = legacy
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(company_env_name("valor-log"), raising=False)
    assert diagnose_main(["--company", "lity"]) == 0 and "Schema complete." in capsys.readouterr().out
    assert diagnose_main(["--company", "valor-log"]) == 2  # not configured: fails closed
    path.unlink()
    assert diagnose_main(["--company", "lity"]) == 2 and not path.exists()  # never creates the file
    path.write_bytes(b"this is not a sqlite database" * 50)
    assert diagnose_main(["--company", "lity"]) == 2
    assert "not a readable" in capsys.readouterr().err


# --- the existing scripts against a legacy database -------------------------------------------------------------------

def test_the_scripts_refuse_a_database_below_version_4_and_write_nothing(legacy):
    env, path, run = legacy
    drop_additive(run)
    run("PRAGMA user_version = 3")
    before = digest(path)
    for init in INIT.values():
        with pytest.raises(CompanySchemaError, match="schema version 3"):
            init("lity", apply=True, environ=env)
    assert digest(path) == before and not (tables(path) & set(ADDITIVE_TABLES))


def test_the_scripts_refuse_a_missing_database_and_never_create_it(legacy):
    env, path, _ = legacy
    path.unlink()
    for init in INIT.values():
        with pytest.raises(CompanySchemaError, match="does not exist"):
            init("lity", apply=True, environ=env)
    assert not path.exists()


def test_dry_run_of_every_script_changes_nothing_on_a_legacy_database(legacy):
    env, path, run = legacy
    drop_additive(run)
    before = digest(path)
    for init in INIT.values():
        assert init("lity", apply=False, environ=env)  # truthy: something would be created/added
    assert digest(path) == before


@pytest.mark.parametrize("order", [list(INIT), list(reversed(INIT))])
def test_applying_all_four_in_any_order_completes_the_schema_and_is_repeatable(legacy, order):
    env, path, run = legacy
    run("INSERT INTO products (name, created_at, updated_at) VALUES ('Adapters', '2026-01-01', '2026-01-01')")
    drop_additive_except_products = [t for t in ADDITIVE_TABLES if t != "products"]
    for table in drop_additive_except_products:
        run(f"DROP TABLE {table}")
    cards_before = sqlite3.connect(path).execute("SELECT * FROM cards").fetchall()
    version_before = sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0]
    assert not diagnose_company("lity", env).complete
    for name in order:
        INIT[name]("lity", apply=True, environ=env)
    result = diagnose_company("lity", env)
    assert result.complete
    validate_company_schema("lity", env)
    con = sqlite3.connect(path)
    assert con.execute("SELECT * FROM cards").fetchall() == cards_before
    assert con.execute("PRAGMA user_version").fetchone()[0] == version_before
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert con.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert [r[0] for r in con.execute("SELECT name FROM products ORDER BY id")] == ["Chargers", "Power supplies", "Adapters"]  # kept
    con.close()
    after = digest(path)
    for name in order:
        again = INIT[name]("lity", apply=True, environ=env)
        assert again in (False, []), name  # False: tables exist; []: no default product to add
    assert digest(path) == after  # a second full run changes not one byte


def test_a_failure_halfway_leaves_a_state_the_diagnostic_names_and_a_rerun_completes(legacy, monkeypatch):
    env, path, run = legacy
    run("DROP TABLE card_review_obligations", "DROP TABLE card_versions")
    real = reviews_script.create_reviews

    def half_then_fail(bind):
        from card_reviews import _versions
        _versions.create(bind)
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(reviews_script, "create_reviews", half_then_fail)
    with pytest.raises(RuntimeError, match="simulated failure"):
        init_company_card_reviews("lity", apply=True, environ=env)
    # Documented limit: the failure leaves the first table only (the script commits nothing else).
    assert "card_versions" in tables(path)
    assert "card_review_obligations" not in tables(path)
    partial = diagnose_company("lity", env)
    assert not partial.complete
    assert [a.table for a in partial.missing] == ["card_review_obligations"]
    assert partial.problems == [] and partial.incompatible == []
    validate_company_schema("lity", env)  # the required contract is untouched
    monkeypatch.setattr(reviews_script, "create_reviews", real)
    assert init_company_card_reviews("lity", apply=True, environ=env) is True
    assert diagnose_company("lity", env).complete
    assert init_company_card_reviews("lity", apply=True, environ=env) is False


def test_the_command_reports_a_database_error_without_a_path_and_exits_1(legacy, monkeypatch, capsys):
    env, path, run = legacy
    run("DROP TABLE card_loss_events")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    from sqlalchemy.exc import OperationalError
    import init_company_card_loss as script

    def boom(_bind):
        raise OperationalError("CREATE", {}, Exception(str(path)))

    monkeypatch.setattr(script, "create_loss", boom)
    assert script.main(["--company", "lity", "--apply"]) == 1
    err = capsys.readouterr().err
    assert "OperationalError" in err and str(path) not in err
    assert "card_loss_events" not in tables(path)


def test_a_read_only_file_makes_apply_fail_and_leaves_the_database_unchanged(legacy):
    env, path, run = legacy
    run("DROP TABLE card_loss_events")
    before = digest(path)
    path.chmod(0o400)
    try:
        with pytest.raises(OperationalError):
            init_company_card_loss("lity", apply=True, environ=env)
    finally:
        path.chmod(0o600)
    assert digest(path) == before
    assert init_company_card_loss("lity", apply=True, environ=env) is True  # recovers once writable


# --- Card, review and loss behaviour on an upgraded database -------------------------------------------------------------

def test_an_upgraded_legacy_database_supports_card_review_and_loss_operations(loss_world):
    rig, uid, mtoken, aid, atoken, stoken = loss_world
    env = rig.e.env
    for table in ADDITIVE_TABLES:
        rig.sql(f"DROP TABLE {table}")
    rig.runtime.dispose()

    # before: operations that need the new tables are refused (503), ordinary reads still work
    assert rig.call("put", "/cards/1", mtoken, json={"title": "x"}).status_code == 503
    assert put_move(rig, atoken, 1, LOST, loss_reason="r").status_code == 503
    assert rig.call("get", "", atoken).status_code == 200
    assert not diagnose_company("lity", env).complete

    for init in INIT.values():
        init("lity", apply=True, environ=env)
    rig.runtime.dispose()
    assert diagnose_company("lity", env).complete

    # after: a member edit creates the two independent obligations, a loss needs and records its reason
    edit(rig, mtoken, 1, title="after upgrade")
    assert pending(rig, 1) == [("admin", 1), ("super_admin", 1)]
    assert reviews(rig, atoken).status_code == 200 and reviews(rig, atoken).json()["items"]
    assert put_move(rig, mtoken, 1, LOST).status_code == 422  # reason mandatory
    assert put_move(rig, mtoken, 1, LOST, loss_reason="  budget cut  ").status_code == 200
    assert losses(rig) == [(1, 1, LOST, "budget cut", uid, "api")]
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("after upgrade",)]
    assert rig.call("get", "", atoken).status_code == 200


# --- incompatible existing tables ------------------------------------------------------------------------------------

@pytest.mark.parametrize("script,damage,gap", [
    ("init_company_card_reviews.py", "DROP INDEX uq_card_review_pending", "reviewer_role"),
    ("init_company_card_loss.py", "ALTER TABLE card_loss_events DROP COLUMN source", "source"),
    ("init_company_excel_imports.py", "ALTER TABLE card_excel_imports DROP COLUMN unchanged_count", "unchanged_count"),
    ("init_company_products.py", "ALTER TABLE products DROP COLUMN updated_at", "updated_at"),
])
@pytest.mark.parametrize("apply", [False, True])
def test_a_script_refuses_an_incompatible_existing_table_and_writes_nothing(legacy, script, damage, gap, apply):
    env, path, run = legacy
    run(damage)
    before = digest(path)
    with pytest.raises(CompanySchemaError, match=gap) as excinfo:
        INIT[script]("lity", apply=apply, environ=env)
    assert script in str(excinfo.value) and "nothing was written" in str(excinfo.value)
    assert digest(path) == before
    assert not diagnose_company("lity", env).complete  # still reported, never "fixed"


def test_the_command_exits_1_with_the_gap_and_never_says_created(legacy, monkeypatch, capsys):
    import init_company_card_loss as script
    env, path, run = legacy
    run("ALTER TABLE card_loss_events DROP COLUMN source")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    before = digest(path)
    for flags in ([], ["--apply"]):
        assert script.main(["--company", "lity", *flags]) == 1
        out = capsys.readouterr()
        assert "source" in out.err and "init_company_card_loss.py" in out.err
        assert "Created" not in out.out and "Would create" not in out.out and "nothing to do" not in out.out
    assert digest(path) == before


def test_a_missing_table_next_to_an_incompatible_one_is_still_refused(legacy):
    env, path, run = legacy
    run("DROP INDEX uq_card_review_pending", "DROP TABLE card_versions")  # missing card_versions, damaged obligations
    with pytest.raises(CompanySchemaError, match="reviewer_role"):
        init_company_card_reviews("lity", apply=True, environ=env)
    assert "card_versions" not in tables(path)


# --- the products script -----------------------------------------------------------------------------------------------

def product_rows(path):
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute("SELECT name FROM products ORDER BY id")]
    finally:
        con.close()


def test_products_creates_the_missing_table_and_seeds_the_defaults(legacy):
    env, path, run = legacy
    run("DROP TABLE products")
    assert init_company_products("lity", apply=False, environ=env) == list(PRODUCT_LINES)
    assert "products" not in tables(path)  # dry run writes nothing
    assert init_company_products("lity", apply=True, environ=env) == list(PRODUCT_LINES)
    assert product_rows(path) == list(PRODUCT_LINES)
    con = sqlite3.connect(path)
    columns = {r[1] for r in con.execute('PRAGMA table_info("products")')}
    con.close()
    assert {"id", "name", "created_at", "updated_at"} <= columns
    assert diagnose_company("lity", env).complete
    assert init_company_products("lity", apply=True, environ=env) == []  # repeat: nothing added
    assert product_rows(path) == list(PRODUCT_LINES)


def test_products_keeps_custom_data_and_only_adds_the_missing_defaults(legacy):
    env, path, run = legacy
    run("DELETE FROM products",
        "INSERT INTO products (name, created_at, updated_at) VALUES ('Custom A', '2026-01-01', '2026-01-01')",
        f"INSERT INTO products (name, created_at, updated_at) VALUES ('{PRODUCT_LINES[0].upper()}', '2026-01-01', '2026-01-01')")
    expected = ["Custom A", PRODUCT_LINES[0].upper(), *PRODUCT_LINES[1:]]  # defaults match case-insensitively
    assert init_company_products("lity", apply=True, environ=env) == list(PRODUCT_LINES[1:])
    assert product_rows(path) == expected
    assert init_company_products("lity", apply=True, environ=env) == []
    assert product_rows(path) == expected
