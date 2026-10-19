"""Central runtime and /api/central/login. Disposable tmp_path identity databases only."""
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import update

import auth
import central_auth as ca
import central_deps as cd
import central_routes
import central_runtime as cr
import identity_service as svc
import rate_limit
from auth import create_user_access_token, hash_password, password_fingerprint
from create_identity_db import create_identity_database
from identity_database import create_identity_session_factory, open_identity_engine
from identity_models import IdentityCompany, IdentityUser

GOOD = "Str0ng!Passw0rd"
BACKEND = Path(__file__).parent


class Setup:
    def __init__(self, tmp_path):
        self.env = {"IDENTITY_DATABASE_URL": f"sqlite:///{tmp_path / 'identity.db'}"}
        create_identity_database(str(tmp_path / "identity.db"))
        engine = open_identity_engine(self.env)
        self.factory = create_identity_session_factory(engine)
        self.engine = engine
        with self.factory() as s:
            s.add(IdentityCompany(slug="lity", name="Lity"))
            s.add(IdentityUser(email="ok@example.com", password_hash=hash_password(GOOD), email_verified_at=svc._now()))
            s.add(IdentityUser(email="off@example.com", is_active=False, password_hash=hash_password(GOOD),
                               email_verified_at=svc._now()))
            s.add(IdentityUser(email="unv@example.com", password_hash=hash_password(GOOD)))
            s.add(IdentityUser(email="nopw@example.com", email_verified_at=svc._now()))
            s.commit()
        self.runtime = cr.CentralIdentityRuntime(self.env)
        app = FastAPI()
        app.include_router(central_routes.build_central_router(self.runtime))
        self.client = TestClient(app)

    def login(self, email="ok@example.com", password=GOOD):
        return self.client.post("/api/central/login", json={"email": email, "password": password})


@pytest.fixture
def s(tmp_path):
    setup = Setup(tmp_path)
    yield setup
    setup.runtime.dispose()
    setup.engine.dispose()


def test_successful_login_token_claims(s):
    r = s.login()
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"access_token", "token_type"} and body["token_type"] == "bearer"
    claims = jwt.decode(body["access_token"], auth.load_secret_key(), algorithms=[auth.ALGORITHM])
    with s.factory() as session:
        user = svc.get_user_by_email(session, "ok@example.com")
        assert claims["purpose"] == ca.ACCESS_TOKEN_PURPOSE
        assert claims["sub"] == str(user.id)
        assert claims["sv"] == user.session_version
        assert claims["pwh"] == password_fingerprint(user.password_hash)
        assert ca.user_for_token(session, body["access_token"]).id == user.id
    lifetime = claims["exp"] - claims.get("iat", claims["exp"] - auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    assert timedelta(seconds=lifetime) == timedelta(hours=8)


def test_email_is_case_insensitive(s):
    assert s.login("  OK@Example.com ").status_code == 200


@pytest.mark.parametrize("email,password", [
    ("ok@example.com", "Wrong!Passw0rd1"), ("nobody@example.com", GOOD), ("not-an-email", GOOD),
    ("off@example.com", GOOD), ("unv@example.com", GOOD), ("nopw@example.com", GOOD), ("nopw@example.com", ""),
])
def test_rejections_are_generic(s, email, password):
    r = s.login(email, password)
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid credentials"}


def test_legacy_token_is_not_a_central_token(s):
    class Legacy:
        id, username, role, session_version = 1, "ok@example.com", "member", 0
        password_hash = hash_password(GOOD)
        password_fingerprint = password_fingerprint(password_hash)
    token = create_user_access_token(Legacy)
    with s.factory() as session, pytest.raises(ca.AuthenticationFailed):
        ca.user_for_token(session, token)
    app = FastAPI()
    get_ctx = cd.company_context_dependency(s.runtime.get_session)

    @app.get("/c/{company_slug}")
    def route(ctx=Depends(get_ctx)):
        return {}
    assert TestClient(app).get("/c/lity", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_central_token_is_not_a_legacy_token(s):
    token = s.login().json()["access_token"]
    assert auth.decode_access_token(token) is None


def test_token_dies_after_logout(s):
    token = s.login().json()["access_token"]
    with s.factory() as session:
        ca.logout(session, svc.get_user_by_email(session, "ok@example.com").id)
    with s.factory() as session, pytest.raises(ca.AuthenticationFailed):
        ca.user_for_token(session, token)


# --- fail closed ---------------------------------------------------------

def _unavailable(client):
    r = client.post("/api/central/login", json={"email": "a@example.com", "password": GOOD})
    assert r.status_code == 503 and r.json() == {"detail": cr.UNAVAILABLE_DETAIL}


def _client(runtime):
    app = FastAPI()
    app.include_router(central_routes.build_central_router(runtime))
    return TestClient(app)


def test_unset_configuration_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("IDENTITY_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'legacy.db'}")
    _unavailable(_client(cr.CentralIdentityRuntime()))
    assert list(tmp_path.iterdir()) == []


def test_missing_file_is_not_created(tmp_path):
    runtime = cr.CentralIdentityRuntime({"IDENTITY_DATABASE_URL": f"sqlite:///{tmp_path / 'missing.db'}"})
    _unavailable(_client(runtime))
    assert list(tmp_path.iterdir()) == []


def test_unversioned_database_is_refused_untouched(tmp_path):
    path = tmp_path / "empty.db"
    path.write_bytes(b"")
    runtime = cr.CentralIdentityRuntime({"IDENTITY_DATABASE_URL": f"sqlite:///{path}"})
    _unavailable(_client(runtime))
    assert path.read_bytes() == b"" and [p.name for p in tmp_path.iterdir()] == ["empty.db"]


def test_database_url_is_never_a_fallback(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.db"
    legacy.write_bytes(b"")
    env = {"DATABASE_URL": f"sqlite:///{legacy}"}
    _unavailable(_client(cr.CentralIdentityRuntime(env)))
    assert legacy.read_bytes() == b""


def test_failed_open_is_retried_not_cached(tmp_path):
    env = {"IDENTITY_DATABASE_URL": f"sqlite:///{tmp_path / 'identity.db'}"}
    runtime = cr.CentralIdentityRuntime(env)
    client = _client(runtime)
    _unavailable(client)
    create_identity_database(str(tmp_path / "identity.db"))
    assert client.post("/api/central/login", json={"email": "a@example.com", "password": GOOD}).status_code == 401
    runtime.dispose()


# --- lifecycle -----------------------------------------------------------

def test_session_closed_after_request_and_on_error(s):
    closed = []
    real = s.runtime._session_factory()

    def factory():
        session = real()
        original = session.close
        session.close = lambda: (closed.append(1), original())[1]
        return session
    s.runtime._factory = factory
    s.login()
    s.login("nobody@example.com")
    assert len(closed) == 2


def test_dispose_releases_engine_and_allows_reopen(s):
    s.login()
    engine = s.runtime._engine
    assert engine is not None
    s.runtime.dispose()
    assert s.runtime._engine is None and s.runtime._factory is None
    s.runtime.dispose()  # idempotent
    assert s.login().status_code == 200 and s.runtime._engine is not engine


def test_foreign_keys_enabled(s):
    s.login()
    with s.runtime._engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_runtime_never_touches_company_tables_or_users(s):
    before = {u.id: u.session_version for u in s.factory().query(IdentityUser)}
    s.login()
    s.login(password="bad")
    assert {u.id: u.session_version for u in s.factory().query(IdentityUser)} == before


# --- application wiring --------------------------------------------------

def test_import_has_no_database_access():
    code = (
        "import os, sys, tempfile\n"
        "d = tempfile.mkdtemp(); os.chdir(d)\n"
        "os.environ.pop('IDENTITY_DATABASE_URL', None); os.environ.pop('DATABASE_URL', None)\n"
        "os.environ['SECRET_KEY'] = 'x' * 40\n"
        f"sys.path.insert(0, {str(BACKEND)!r})\n"
        "import central_runtime, central_routes\n"
        "assert central_runtime.central_runtime._engine is None\n"
        "assert os.listdir(d) == [], os.listdir(d)\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_main_app_registers_route_and_disposes_on_shutdown():
    import main
    assert any(getattr(r, "path", "") == "/api/central/login" for r in main.app.routes)
    assert any(getattr(r, "path", "") == "/api/login" for r in main.app.routes)
    main.central_runtime.dispose()  # what the shutdown hook calls; safe when never opened
