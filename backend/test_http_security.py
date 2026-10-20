"""CORS origins and security response headers."""
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

import http_security
from main import app


@pytest.fixture
def client():
    return TestClient(app)


DEV = "http://localhost:3000"
EVIL = "https://evil.example"


class TestCorsOrigins:
    def test_unset_defaults_to_dev_origins(self, monkeypatch):
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        assert http_security.cors_origins() == http_security.DEV_ORIGINS

    def test_empty_means_same_origin_only(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "")
        assert http_security.cors_origins() == []

    def test_explicit_list_is_normalised(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", " https://PM.example.com/ , http://localhost:3000 ")
        assert http_security.cors_origins() == ["https://pm.example.com", "http://localhost:3000"]

    @pytest.mark.parametrize(
        "value",
        ["*", "https://*.example.com", "pm.example.com", "ftp://pm.example.com",
         "https://pm.example.com/app", "https://user@pm.example.com", "null"],
    )
    def test_invalid_origin_stops_the_app(self, monkeypatch, value):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", value)
        with pytest.raises(RuntimeError):
            http_security.cors_origins()
        with pytest.raises(RuntimeError):
            http_security.validate_config()


class TestCorsBehaviour:
    def test_dev_origin_preflight_allowed(self, client):
        r = client.options(
            "/api/login",
            headers={
                "Origin": DEV,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == DEV
        assert "access-control-allow-credentials" not in r.headers
        allowed = r.headers["access-control-allow-headers"].lower()
        assert "authorization" in allowed and "content-type" in allowed

    def test_unlisted_origin_gets_no_cors_permission(self, client):
        r = client.get("/api/health", headers={"Origin": EVIL})
        assert r.status_code == 200
        assert "access-control-allow-origin" not in r.headers

    def test_unlisted_origin_preflight_rejected(self, client):
        r = client.options(
            "/api/login",
            headers={"Origin": EVIL, "Access-Control-Request-Method": "POST"},
        )
        assert r.status_code == 400
        assert "access-control-allow-origin" not in r.headers

    def test_no_wildcard_anywhere(self, client):
        r = client.get("/api/health", headers={"Origin": DEV})
        assert r.headers["access-control-allow-origin"] == DEV

    def test_setup_preflight_allows_the_setup_key_header(self, client):
        r = client.options(
            "/api/setup",
            headers={
                "Origin": DEV,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-setup-token",
            },
        )
        assert r.status_code == 200
        assert "x-setup-token" in r.headers["access-control-allow-headers"].lower()

    def test_company_preflight_allows_the_company_header(self, client):
        r = client.options(
            "/api/companies/lity/boards",
            headers={
                "Origin": DEV,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,x-company-slug",
            },
        )
        assert r.status_code == 200
        assert "x-company-slug" in r.headers["access-control-allow-headers"].lower()

    def test_unexpected_header_and_method_not_allowed(self, client):
        r = client.options(
            "/api/login",
            headers={
                "Origin": DEV,
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "x-custom",
            },
        )
        assert r.status_code == 400

    def test_configured_origin_only(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://pm.example.com")
        mini = FastAPI()
        mini.add_middleware(
            CORSMiddleware,
            allow_origins=http_security.cors_origins(),
            allow_credentials=False,
            allow_methods=http_security.CORS_METHODS,
            allow_headers=http_security.CORS_HEADERS,
        )

        @mini.get("/x")
        def x():
            return {}

        c = TestClient(mini)
        ok = c.get("/x", headers={"Origin": "https://pm.example.com"})
        assert ok.headers["access-control-allow-origin"] == "https://pm.example.com"
        assert "access-control-allow-origin" not in c.get("/x", headers={"Origin": DEV}).headers


class TestSecurityHeaders:
    def _check(self, r):
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert "permissions-policy" in r.headers

    def test_on_api_success(self, client):
        self._check(client.get("/api/health"))

    def test_on_auth_error(self, client):
        r = client.get("/api/boards")
        assert r.status_code in (401, 403)
        self._check(r)

    def test_on_not_found(self, client):
        self._check(client.get("/api/does-not-exist"))

    def test_on_preflight(self, client):
        r = client.options(
            "/api/login",
            headers={"Origin": DEV, "Access-Control-Request-Method": "POST"},
        )
        self._check(r)

    def test_csp_keeps_the_app_working(self):
        csp = http_security.CSP
        for needed in ("default-src 'self'", "script-src 'self' 'unsafe-inline'",
                       "style-src 'self' 'unsafe-inline'", "connect-src 'self'",
                       "object-src 'none'", "base-uri 'self'"):
            assert needed in csp
        assert "*" not in csp and "http:" not in csp

    def test_docs_pages_are_exempt_from_csp_only(self, client):
        r = client.get("/docs")
        assert "content-security-policy" not in r.headers
        assert r.headers["x-content-type-options"] == "nosniff"

    def test_hsts_off_by_default(self, client, monkeypatch):
        monkeypatch.delenv("HSTS_ENABLED", raising=False)
        assert "strict-transport-security" not in client.get("/api/health").headers

    def test_hsts_when_enabled(self, client, monkeypatch):
        monkeypatch.setenv("HSTS_ENABLED", "true")
        assert client.get("/api/health").headers["strict-transport-security"] == "max-age=31536000"

    def test_app_set_header_is_not_overwritten(self):
        mini = FastAPI()
        mini.add_middleware(http_security.SecurityHeadersMiddleware)

        @mini.get("/x")
        def x():
            from fastapi.responses import JSONResponse
            return JSONResponse({}, headers={"X-Frame-Options": "SAMEORIGIN"})

        r = TestClient(mini).get("/x")
        assert r.headers.get_list("x-frame-options") == ["SAMEORIGIN"]
