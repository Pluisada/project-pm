"""PM_AUTH_MODE: legacy default, central hides every legacy /api path and skips the legacy database."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_mode


def app():
    a = FastAPI()
    a.add_middleware(auth_mode.AuthModeMiddleware)
    for path in ("/api/login", "/api/boards", "/api/central/login", "/api/companies/lity/boards", "/api/mode", "/api/health", "/other"):
        a.add_api_route(path, lambda: {"ok": True}, methods=["GET", "POST"])
    return TestClient(a)


def test_legacy_mode_is_the_default_and_hides_nothing(monkeypatch):
    monkeypatch.delenv("PM_AUTH_MODE", raising=False)
    assert auth_mode.auth_mode() == "legacy"
    assert app().post("/api/login").status_code == 200


def test_central_mode_hides_legacy_api_but_not_central_probes_or_static(monkeypatch):
    monkeypatch.setenv("PM_AUTH_MODE", "central")
    c = app()
    for path in ("/api/login", "/api/boards", "/api/boards/1/cards", "/api/users", "/api/ai/test"):
        assert c.post(path).status_code == 404 and c.get(path).status_code == 404
    for path in ("/api/central/login", "/api/companies/lity/boards", "/api/mode", "/api/health", "/other"):
        assert c.get(path).status_code == 200


def test_invalid_mode_is_refused(monkeypatch):
    with pytest.raises(auth_mode.AuthModeError):
        auth_mode.auth_mode({"PM_AUTH_MODE": "both"})


def test_central_mode_needs_explicit_databases():
    with pytest.raises(auth_mode.AuthModeError, match="IDENTITY_DATABASE_URL"):
        auth_mode.validate_config({"PM_AUTH_MODE": "central"})
    env = {"PM_AUTH_MODE": "central", "IDENTITY_DATABASE_URL": "x", "PM_COMPANY_DB_SALCOMP": "x", "PM_COMPANY_DB_LITY": "x"}
    with pytest.raises(auth_mode.AuthModeError, match="PM_COMPANY_DB_VALOR_LOG"):
        auth_mode.validate_config(env)
    auth_mode.validate_config({**env, "PM_COMPANY_DB_VALOR_LOG": "x"})
    auth_mode.validate_config({})  # legacy: nothing required


def test_real_app_mode_endpoint(monkeypatch):
    import main
    monkeypatch.setenv("PM_AUTH_MODE", "central")
    c = TestClient(main.app)  # no startup events: the legacy database is not touched
    assert c.get("/api/mode").json() == {"auth": "central"}
    assert c.post("/api/login", json={"username": "a", "password": "b"}).status_code == 404
    assert c.get("/api/boards").status_code == 404
    monkeypatch.setenv("PM_AUTH_MODE", "legacy")
    assert c.get("/api/mode").json() == {"auth": "legacy"}


def test_production_central_mode_does_not_require_the_legacy_database(monkeypatch):
    import production_config
    for name, value in {"PM_ENV": "production", "APP_BASE_URL": "https://pm.example.com", "SMTP_HOST": "smtp.example.com",
                        "SMTP_FROM": "PM <noreply@example.com>", "CORS_ALLOWED_ORIGINS": "https://pm.example.com"}.items():
        monkeypatch.setenv(name, value)
    for name in ("DATABASE_URL", "PM_REQUIRE_EXISTING_DB", "PM_AUTH_MODE", "PM_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    assert any("DATABASE_URL" in p for p in production_config.problems())  # legacy still demands it
    monkeypatch.setenv("PM_AUTH_MODE", "central")
    assert production_config.problems() == []
