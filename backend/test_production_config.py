"""PM_ENV=production startup validation (public URL, CORS, E2E mode, SMTP)."""
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

import http_security
from database import ExistingDatabaseRequired, check_existing_database
from production_config import _sqlite_file_name, behind_proxy, database_problems, docs_kwargs, is_production, problems, smtp_problems, validate_config

GOOD = "https://pm.example.com"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("PM_ENV", "PM_E2E", "APP_BASE_URL", "PM_PUBLIC_URL", "CORS_ALLOWED_ORIGINS",
                "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
                "PM_BEHIND_PROXY", "RATE_LIMIT_TRUSTED_PROXIES", "DATABASE_URL", "PM_REQUIRE_EXISTING_DB"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def prod(monkeypatch):
    monkeypatch.setenv("PM_ENV", "production")
    monkeypatch.setenv("APP_BASE_URL", GOOD)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "")
    monkeypatch.setenv("DATABASE_URL", "sqlite:////data/pm-commercial.db")
    monkeypatch.setenv("PM_REQUIRE_EXISTING_DB", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_FROM", "PM <noreply@example.com>")


def test_not_checked_outside_production(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("PM_E2E", "1")
    assert problems() == []
    validate_config()
    assert smtp_problems()  # structurally incomplete, yet not enforced outside production
    assert http_security.cors_origins() == http_security.DEV_ORIGINS


def test_valid_https_url_passes(prod):
    assert problems() == []
    validate_config()


def test_https_url_with_port_passes(prod, monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://pm.example.com:8443")
    assert problems() == []


@pytest.mark.parametrize("url", [
    "http://pm.example.com",
    "https://localhost",
    "https://localhost:8000",
    "https://app.localhost",
    "https://127.0.0.1",
    "https://[::1]",
    "https://0.0.0.0",
    "https://pm.example.com/",
    "https://pm.example.com/app",
    "https://user:pw@pm.example.com",
    "https://pm.example.com?x=1",
    "pm.example.com",
    "https://",
])
def test_invalid_production_urls_are_rejected(prod, monkeypatch, url):
    monkeypatch.setenv("APP_BASE_URL", url)
    assert problems()
    with pytest.raises(RuntimeError, match="Invalid production configuration"):
        validate_config()


@pytest.mark.parametrize("url", [
    "sqlite:///./pm.db",
    "sqlite:///pm-commercial.db",
    "sqlite:////app/pm.db",
    "sqlite:////data/pm.db?mode=rw",
    "sqlite:///:memory:",
    "postgresql://u@h/db",
    "",
])
def test_production_rejects_relative_dev_or_non_sqlite_database(prod, monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    assert any("DATABASE_URL" in p for p in problems())


def test_production_requires_existing_database_mode(prod, monkeypatch):
    monkeypatch.delenv("PM_REQUIRE_EXISTING_DB")
    assert any("PM_REQUIRE_EXISTING_DB" in p for p in problems())
    monkeypatch.setenv("PM_REQUIRE_EXISTING_DB", "0")
    assert any("PM_REQUIRE_EXISTING_DB" in p for p in problems())


def test_database_is_not_checked_outside_production(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./pm.db")
    assert problems() == []


def test_missing_url_is_rejected(prod, monkeypatch):
    monkeypatch.delenv("APP_BASE_URL")
    assert any("APP_BASE_URL" in p and "not set" in p for p in problems())


def test_inconsistent_public_url_is_rejected(prod, monkeypatch):
    monkeypatch.setenv("PM_PUBLIC_URL", "https://other.example.com")
    assert any("differ" in p for p in problems())


def test_matching_public_url_is_accepted(prod, monkeypatch):
    monkeypatch.setenv("PM_PUBLIC_URL", GOOD + "/")
    assert problems() == []


def test_e2e_mode_is_rejected_in_production(prod, monkeypatch):
    monkeypatch.setenv("PM_E2E", "1")
    assert any("PM_E2E" in p for p in problems())


def test_unset_cors_is_same_origin_only_in_production(prod, monkeypatch):
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS")
    assert http_security.cors_origins() == []
    assert problems() == []


@pytest.mark.parametrize("origin", ["http://pm.example.com", "https://localhost:3000"])
def test_insecure_or_local_cors_origin_is_rejected_in_production(prod, monkeypatch, origin):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", origin)
    assert any("CORS_ALLOWED_ORIGINS" in p for p in problems())


def test_all_problems_are_reported_together(prod, monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("PM_E2E", "1")
    assert len(problems()) == 2


def test_api_docs_are_served_outside_production(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from production_config import docs_kwargs

    client = TestClient(FastAPI(**docs_kwargs()))
    assert all(client.get(path).status_code == 200 for path in ("/docs", "/redoc", "/openapi.json"))


def test_api_docs_are_off_in_production(prod):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from production_config import docs_kwargs

    client = TestClient(FastAPI(**docs_kwargs()))
    assert all(client.get(path).status_code == 404 for path in ("/docs", "/redoc", "/openapi.json"))


def test_main_app_is_built_with_the_docs_switch():
    import inspect
    import main

    assert "docs_kwargs()" in inspect.getsource(main).split("app = FastAPI(")[1].split("\n")[0]


@pytest.mark.parametrize("value", ["0.0.0.0/0", "::/0", "172.18.0.1,0.0.0.0/0"])
def test_catch_all_trusted_proxy_is_rejected_in_production(prod, monkeypatch, value):
    monkeypatch.setenv("RATE_LIMIT_TRUSTED_PROXIES", value)
    assert any("RATE_LIMIT_TRUSTED_PROXIES" in p for p in problems())


def test_narrow_trusted_proxy_is_accepted_in_production(prod, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_TRUSTED_PROXIES", "172.18.0.1,10.0.0.0/24")
    assert problems() == []


def test_production_without_proxy_declaration_needs_no_trusted_proxies(prod):
    assert problems() == []


@pytest.mark.parametrize("value", ["not-an-ip", "10.0.0.0/99", "10.0.0.1,,bogus"])
def test_malformed_trusted_proxy_is_rejected_in_production(prod, monkeypatch, value):
    monkeypatch.setenv("RATE_LIMIT_TRUSTED_PROXIES", value)
    assert any("RATE_LIMIT_TRUSTED_PROXIES" in p for p in problems())


@pytest.mark.parametrize("flag", ["1", "true", "TRUE", "yes", "on"])
def test_declared_proxy_without_trusted_proxies_is_rejected(prod, monkeypatch, flag):
    monkeypatch.setenv("PM_BEHIND_PROXY", flag)
    assert any("PM_BEHIND_PROXY" in p for p in problems())


def test_declared_proxy_with_trusted_proxies_is_accepted(prod, monkeypatch):
    monkeypatch.setenv("PM_BEHIND_PROXY", "true")
    monkeypatch.setenv("RATE_LIMIT_TRUSTED_PROXIES", "192.0.2.10")
    assert problems() == []


def test_proxy_not_declared_false_values_are_direct_access(prod, monkeypatch):
    monkeypatch.setenv("PM_BEHIND_PROXY", "false")
    assert problems() == []


def test_proxy_declaration_is_not_checked_outside_production(monkeypatch):
    monkeypatch.setenv("PM_BEHIND_PROXY", "true")
    assert problems() == []


def test_development_without_smtp_is_accepted(monkeypatch):
    assert problems() == []
    monkeypatch.setenv("PM_ENV", "development")
    assert problems() == []


def _smtp(problems_):
    return [p for p in problems_ if "SMTP" in p]


def test_valid_smtp_with_credentials_passes(prod, monkeypatch):
    monkeypatch.setenv("SMTP_USER", "mailer@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "dummy-password")
    monkeypatch.delenv("SMTP_FROM")  # sender falls back to SMTP_USER
    assert problems() == []


def test_unauthenticated_relay_passes(prod, monkeypatch):
    monkeypatch.delenv("SMTP_PORT")  # mailer default (587)
    assert problems() == []


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_smtp_host_is_rejected(prod, monkeypatch, value):
    monkeypatch.delenv("SMTP_HOST")
    if value is not None:
        monkeypatch.setenv("SMTP_HOST", value)
    assert any("SMTP_HOST" in p for p in problems())
    with pytest.raises(RuntimeError, match="SMTP_HOST"):
        validate_config()


@pytest.mark.parametrize("port", ["", "abc", "0", "-1", "65536", "58.7"])
def test_invalid_smtp_port_is_rejected(prod, monkeypatch, port):
    monkeypatch.setenv("SMTP_PORT", port)
    assert any("SMTP_PORT" in p for p in problems())


@pytest.mark.parametrize("port", ["1", "25", "465", "65535"])
def test_smtp_port_range_is_accepted(prod, monkeypatch, port):
    monkeypatch.setenv("SMTP_PORT", port)
    assert problems() == []


def test_missing_sender_is_rejected(prod, monkeypatch):
    monkeypatch.delenv("SMTP_FROM")
    assert any("sender" in p for p in problems())


@pytest.mark.parametrize("sender", ["noreply", "noreply@", "@example.com", "noreply@localhost", "a b@example.com", "a@b@example.com"])
def test_malformed_sender_is_rejected(prod, monkeypatch, sender):
    monkeypatch.setenv("SMTP_FROM", sender)
    assert any("valid email address" in p for p in problems())


def test_smtp_user_with_empty_password_is_rejected(prod, monkeypatch):
    monkeypatch.setenv("SMTP_USER", "mailer@example.com")
    assert any("SMTP_PASSWORD" in p for p in problems())
    monkeypatch.setenv("SMTP_PASSWORD", "")
    assert any("SMTP_PASSWORD" in p for p in problems())


def test_smtp_validation_makes_no_network_connection(prod, monkeypatch):
    import smtplib

    def boom(*a, **k):
        raise AssertionError("network access during validation")

    monkeypatch.setattr(smtplib.SMTP, "__init__", boom)
    monkeypatch.setattr("socket.create_connection", boom)
    assert problems() == []


# --- fail-closed parsing of PM_ENV and PM_BEHIND_PROXY ---

@pytest.mark.parametrize("value", ["production", " Production ", "PRODUCTION"])
def test_pm_env_production_values_are_accepted(monkeypatch, value):
    monkeypatch.setenv("PM_ENV", value)
    assert is_production() is True


@pytest.mark.parametrize("value", [None, "", "  ", "development", "Development"])
def test_pm_env_unset_or_development_is_not_production(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("PM_ENV", value)
    assert is_production() is False


@pytest.mark.parametrize("value", ["prod", "staging", "prodution", "1", "true"])
def test_unsupported_pm_env_fails_instead_of_acting_as_development(monkeypatch, value):
    monkeypatch.setenv("PM_ENV", value)
    with pytest.raises(RuntimeError, match="PM_ENV"):
        is_production()
    with pytest.raises(RuntimeError, match="PM_ENV"):
        problems()
    with pytest.raises(RuntimeError, match="PM_ENV"):
        validate_config()
    with pytest.raises(RuntimeError, match="PM_ENV"):
        docs_kwargs()  # runs while main.py builds the app, before startup validation


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_pm_behind_proxy_true_values(monkeypatch, value):
    monkeypatch.setenv("PM_BEHIND_PROXY", value)
    assert behind_proxy() is True


@pytest.mark.parametrize("value", [None, "", " ", "0", "false", "no", "off", "False"])
def test_pm_behind_proxy_unset_or_false_values(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("PM_BEHIND_PROXY", value)
    assert behind_proxy() is False


@pytest.mark.parametrize("value", ["ture", "2", "enabled", "y", "nope"])
def test_invalid_pm_behind_proxy_fails_in_production_and_development(prod, monkeypatch, value):
    monkeypatch.setenv("PM_BEHIND_PROXY", value)
    with pytest.raises(RuntimeError, match="PM_BEHIND_PROXY"):
        validate_config()
    monkeypatch.delenv("PM_ENV")
    with pytest.raises(RuntimeError, match="PM_BEHIND_PROXY"):
        validate_config()


# --- development database file name ---

@pytest.mark.parametrize("url", [
    "sqlite:////data/PM.DB",
    "sqlite:////data/Pm.Db",
    "sqlite:////data/pm.db?mode=rw",
    "sqlite:////data/sub/../pm.db",
    "sqlite:////data/./pm.db",
    "sqlite:////data//pm.db",
    "sqlite:////data/%50M.db",
    "sqlite:////pm.db",
])
def test_development_database_name_is_rejected_in_any_spelling(prod, monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    assert any("pm.db" in p for p in problems()), url


@pytest.mark.parametrize("url", [
    "sqlite:////data/pm-commercial.db",
    "sqlite:////data/pm.db/pm-commercial.db",
    "sqlite:////data/xpm.db",
    "sqlite:////data/pm.db.bak",
])
def test_other_database_names_are_accepted(prod, monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    assert problems() == []


# --- invalid configuration stops the app in a clean interpreter ---

BACKEND_DIR = str(Path(__file__).resolve().parent)

# Runs under `python -I -B`: isolated mode ignores PYTHON* variables, the user
# site and the current directory, so the backend directory is added explicitly.
# The third-party imports come first so a missing dependency is a different
# failure (exit 4) from the configuration error (exit 3) these tests expect.
_CHILD = """
import sys
sys.path.insert(0, {backend!r})
import fastapi, sqlalchemy
try:
    import main
    if {validate}:
        import production_config
        production_config.validate_config()
except RuntimeError as exc:
    print("CONFIG_ERROR:", exc, file=sys.stderr)
    sys.exit(3)
except BaseException as exc:
    print("OTHER_ERROR:", type(exc).__name__, file=sys.stderr)
    sys.exit(4)
print("IMPORTED")
"""


def _run_isolated(tmp_path, extra_env, validate=False):
    """Run the app import in a fresh interpreter with an explicit, minimal environment."""
    secret = secrets.token_urlsafe(48)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "SECRET_KEY": secret,
        "DATABASE_URL": f"sqlite:///{tmp_path / 'never-created.db'}",
        "PM_REQUIRE_EXISTING_DB": "1",
        **extra_env,
    }
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _CHILD.format(backend=BACKEND_DIR, validate=validate)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )
    # Neither stream may leak the secret, and the database path was never opened.
    assert secret not in result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []
    return result


def test_isolated_import_with_valid_configuration_succeeds(tmp_path):
    """Control: the harness itself imports the app, so the failures below come from the values."""
    result = _run_isolated(tmp_path, {"PM_BEHIND_PROXY": "true"}, validate=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "IMPORTED"


def test_importing_app_with_unsupported_pm_env_fails(tmp_path):
    result = _run_isolated(tmp_path, {"PM_ENV": "prod"})
    assert result.returncode == 3, result.stderr
    assert "IMPORTED" not in result.stdout
    assert "PM_ENV must be 'production' or 'development'" in result.stderr
    assert "'prod'" in result.stderr


def test_invalid_pm_behind_proxy_fails_startup_validation(tmp_path):
    # Importing main does not read PM_BEHIND_PROXY; startup_event calls
    # production_config.validate_config() first, which is what this runs.
    imported = _run_isolated(tmp_path, {"PM_BEHIND_PROXY": "ture"}, validate=False)
    assert imported.returncode == 0, imported.stderr

    result = _run_isolated(tmp_path, {"PM_BEHIND_PROXY": "ture"}, validate=True)
    assert result.returncode == 3, result.stderr
    assert "IMPORTED" not in result.stdout
    assert "PM_BEHIND_PROXY must be one of" in result.stderr
    assert "'ture'" in result.stderr


def test_pm_env_prod_is_reported_before_proxy_typo(tmp_path):
    result = _run_isolated(tmp_path, {"PM_ENV": "prod", "PM_BEHIND_PROXY": "ture"}, validate=True)
    assert result.returncode == 3, result.stderr
    assert "PM_ENV must be" in result.stderr


# --- _sqlite_file_name edge cases ---
# Unsafe: the helper must see pm.db, so production refuses the URL.
# Safe: the helper sees another name; the file SQLAlchemy opens is literally
# that other name (it does not percent-decode), or the path is refused later.

@pytest.mark.parametrize("url, name", [
    ("sqlite:////data/pm.db/", "pm.db"),                 # trailing slash
    ("sqlite:////data/pm.db/.", "pm.db"),
    ("sqlite:////data/pm.db?mode=ro", "pm.db"),          # query
    ("sqlite:////data/pm.db?a=b/c", "pm.db"),            # slash inside the query
    ("sqlite:////data/%2e%2e/pm.db", "pm.db"),           # encoded dot-dot segment
    ("sqlite:////data/a#b/pm.db", "pm.db"),              # fragment character in a directory name
    ("sqlite:////data/PM.Db", "pm.db"),
])
def test_sqlite_file_name_resolves_to_development_name(url, name):
    assert _sqlite_file_name(url) == name


@pytest.mark.parametrize("url, name", [
    ("sqlite:////data/pm.db%00", "pm.db\x00"),          # encoded NUL: a different name
    ("sqlite:////data/pm%00.db", "pm\x00.db"),
    ("sqlite:////data/pm.db#frag", "pm.db#frag"),        # fragment is part of the file name
    ("sqlite:////data/pm.db%20", "pm.db "),              # trailing space
    ("sqlite:////data/pm.db.", "pm.db."),
    ("sqlite:////data/", "data"),                        # directory-like
    ("sqlite:////", ""),                                 # empty name
])
def test_sqlite_file_name_keeps_other_names_distinct(url, name):
    assert _sqlite_file_name(url) == name


def test_sqlite_file_name_never_opens_anything(tmp_path):
    missing = tmp_path / "pm.db"
    _sqlite_file_name(f"sqlite:///{missing}")
    assert not missing.exists()


@pytest.mark.parametrize("url", [
    "sqlite:////data/pm.db/",
    "sqlite:////data/pm.db?mode=ro",
    "sqlite:////data/a#b/pm.db",
    "sqlite:////data/pm.db ",     # literal trailing space is stripped like the other whitespace
    " sqlite:////data/pm.db",
    "\tsqlite:////data/pm.db\n",
])
def test_unsafe_spellings_of_development_database_are_refused(prod, monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    assert any("pm.db" in p for p in database_problems()), url


@pytest.mark.parametrize("url", [
    "sqlite:////data/pm.db%00",
    "sqlite:////data/pm.db%20",
    "sqlite:////data/pm.db#frag",
    "sqlite:////data/",
    "sqlite:////",
])
def test_names_that_are_not_pm_db_pass_the_name_check(prod, monkeypatch, url):
    # Not the development file, so the name check has nothing to refuse; the
    # existing-database check at init_db() refuses missing files and directories.
    monkeypatch.setenv("DATABASE_URL", url)
    assert not any("pm.db, the development" in p for p in database_problems()), url


def test_surrounding_whitespace_is_stripped_by_validation_but_not_by_database_module(prod, monkeypatch):
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    monkeypatch.setenv("DATABASE_URL", "  sqlite:////data/pm-commercial.db\n")
    assert database_problems() == []  # validation reads the stripped value
    # database.py passes the raw value to create_engine, which refuses leading
    # whitespace at import (fail-closed) and would keep a trailing space in the name.
    with pytest.raises(ArgumentError):
        make_url("  sqlite:////data/pm-commercial.db")
    assert make_url("sqlite:////data/pm-commercial.db ").database == "/data/pm-commercial.db "


@pytest.mark.parametrize("name", ["", "missing-dir/", "trailing-space.db ", "pm.db%00", "pm.db#frag"])
def test_odd_names_are_refused_later_when_nothing_exists(tmp_path, name):
    url = f"sqlite:///{tmp_path}/{name}"
    with pytest.raises(ExistingDatabaseRequired):
        check_existing_database(url)
    assert list(tmp_path.iterdir()) == []  # refusing created nothing


def test_embedded_nul_never_reaches_the_filesystem(tmp_path):
    # SQLAlchemy keeps %00 as literal characters; a real NUL is rejected by sqlite3 itself.
    import sqlite3

    with pytest.raises(ValueError):
        sqlite3.connect(str(tmp_path / "x.db") + "\x00junk")
    assert list(tmp_path.iterdir()) == []


# --- central mode (PM_AUTH_MODE=central): four explicit databases, no legacy DATABASE_URL ---

CENTRAL_VARS = ("IDENTITY_DATABASE_URL", "PM_COMPANY_DB_SALCOMP", "PM_COMPANY_DB_LITY", "PM_COMPANY_DB_VALOR_LOG")


@pytest.fixture
def central(prod, monkeypatch, tmp_path):
    """Production + central mode over empty placeholder files in tmp_path (existence is all that is checked)."""
    monkeypatch.setenv("PM_AUTH_MODE", "central")
    monkeypatch.delenv("DATABASE_URL")  # central must not need the legacy database
    monkeypatch.delenv("PM_REQUIRE_EXISTING_DB")
    for name, var in zip(("identity", "salcomp", "lity", "valor-log"), CENTRAL_VARS):
        path = tmp_path / f"{name}.db"
        path.touch()
        monkeypatch.setenv(var, f"sqlite:///{path}")
    return tmp_path


def test_central_production_passes_without_database_url(central):
    assert problems() == []
    validate_config()


def test_central_production_ignores_legacy_database_requirements(central, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./pm.db")  # a stray development value is not validated or used
    assert problems() == []


@pytest.mark.parametrize("var", CENTRAL_VARS)
def test_central_production_fails_when_a_database_variable_is_missing(central, monkeypatch, var):
    monkeypatch.delenv(var)
    found = problems()
    assert found and any(var in item or "identity" in item.lower() for item in found)


@pytest.mark.parametrize("var", CENTRAL_VARS)
def test_central_production_fails_when_a_database_file_is_missing(central, monkeypatch, var):
    monkeypatch.setenv(var, f"sqlite:///{central}/absent.db")
    assert problems()


def test_central_production_refuses_a_shared_file_and_protected_names(central, monkeypatch):
    monkeypatch.setenv("PM_COMPANY_DB_LITY", f"sqlite:///{central}/salcomp.db")
    assert any("same file" in item for item in problems())
    monkeypatch.setenv("PM_COMPANY_DB_LITY", f"sqlite:///{central}/lity.db")
    (central / "pm-commercial.db").touch()
    monkeypatch.setenv("PM_COMPANY_DB_VALOR_LOG", f"sqlite:///{central}/pm-commercial.db")
    assert any("development or commercial" in item for item in problems())


def test_central_production_does_not_apply_in_legacy_mode(prod, monkeypatch):
    monkeypatch.delenv("PM_AUTH_MODE", raising=False)
    monkeypatch.delenv("DATABASE_URL")
    assert any("DATABASE_URL" in item for item in problems())  # legacy still demands its database


def test_central_compose_file_selects_central_mode_without_legacy_database():
    """docker-compose.central.yml is standalone: production + central, the four databases required, no legacy fallback."""
    text = (Path(__file__).resolve().parent.parent / "docker-compose.central.yml").read_text()
    env = [line.strip()[2:] for line in text.splitlines() if line.strip().startswith("- ") and "=" in line]
    names = {item.split("=", 1)[0] for item in env}
    assert {"PM_ENV=production", "PM_AUTH_MODE=central"} <= set(env)
    assert {"IDENTITY_DATABASE_URL", "PM_COMPANY_DB_SALCOMP", "PM_COMPANY_DB_LITY", "PM_COMPANY_DB_VALOR_LOG"} <= names
    assert not names & {"DATABASE_URL", "PM_REQUIRE_EXISTING_DB", "PM_SETUP_TOKEN", "SIGNUP_ALLOWED_DOMAINS"}
    for item in env:
        name, value = item.split("=", 1)
        if name in ("IDENTITY_DATABASE_URL", "SECRET_KEY", "APP_BASE_URL", "SMTP_HOST", "SMTP_FROM") or name.startswith("PM_COMPANY_DB_"):
            assert ":?" in value, f"{name} must be required with no default"
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "pm.db" not in code and "pm-commercial" not in code  # no development/commercial database mount or default
