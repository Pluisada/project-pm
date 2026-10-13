"""Tests for the forgot-password / reset-password flow."""
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from auth import create_purpose_token, create_signup_token, password_fingerprint
from database import get_db
from main import app
from models import Base, User

EMAIL = "member@example.com"
OLD_PASSWORD = "oldpass123"
NEW_PASSWORD = "newpass123"


@pytest.fixture(scope="function")
def test_db():
    """Create an isolated in-memory database per test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(test_db):
    """Test client wired to the isolated database."""
    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def sent(monkeypatch):
    """Capture outgoing emails instead of sending them."""
    messages = []
    monkeypatch.setattr(
        main, "send_email", lambda to, subject, body: messages.append((to, body))
    )
    return messages


@pytest.fixture
def member(client):
    """An admin without an email (as setup creates) plus a self-signed-up member."""
    client.post("/api/setup", json={"username": "admin", "password": "adminpass123"})
    response = client.post(
        "/api/signup/complete",
        json={"token": create_signup_token(EMAIL), "password": OLD_PASSWORD},
    )
    assert response.status_code == 201


def token_from(body: str) -> str:
    link = next(word for word in body.split() if "reset_token=" in word)
    return parse_qs(urlparse(link).query)["reset_token"][0]


def login(client, password):
    return client.post("/api/login", json={"username": EMAIL, "password": password})


@pytest.mark.parametrize("identifier", [EMAIL, "Member@Example.com", f"  {EMAIL} "])
def test_forgot_sends_link_to_account_email(client, sent, member, identifier):
    response = client.post("/api/password/forgot", json={"identifier": identifier})
    assert response.status_code == 200
    assert response.json()["email"] == EMAIL
    assert [to for to, _ in sent] == [EMAIL]


def test_unknown_account_returns_404(client, sent, member):
    response = client.post("/api/password/forgot", json={"identifier": "nobody@example.com"})
    assert response.status_code == 404
    assert sent == []


def test_account_without_email_returns_400(client, sent, member):
    response = client.post("/api/password/forgot", json={"identifier": "admin"})
    assert response.status_code == 400
    assert sent == []


def test_full_reset_flow(client, sent, member):
    client.post("/api/password/forgot", json={"identifier": EMAIL})
    token = token_from(sent[0][1])

    response = client.post(
        "/api/password/reset", json={"token": token, "password": NEW_PASSWORD}
    )
    assert response.status_code == 200
    assert login(client, NEW_PASSWORD).status_code == 200
    assert login(client, OLD_PASSWORD).status_code == 401


def test_reset_link_is_single_use(client, sent, member):
    client.post("/api/password/forgot", json={"identifier": EMAIL})
    payload = {"token": token_from(sent[0][1]), "password": NEW_PASSWORD}
    assert client.post("/api/password/reset", json=payload).status_code == 200
    assert client.post("/api/password/reset", json=payload).status_code == 400


def _reset_token(test_db, **overrides):
    user = test_db.query(User).filter(User.username == EMAIL).one()
    args = {
        "sub": EMAIL,
        "purpose": "reset",
        "expires": timedelta(hours=1),
        "pwh": password_fingerprint(user.password_hash),
    }
    args.update(overrides)
    return create_purpose_token(
        args.pop("sub"), args.pop("purpose"), args.pop("expires"), **args
    )


@pytest.mark.parametrize(
    "make_token",
    [
        lambda db: _reset_token(db) + "x",
        lambda db: _reset_token(db, expires=timedelta(minutes=-1)),
        lambda db: _reset_token(db, purpose="signup"),
        lambda db: _reset_token(db, pwh="0" * 16),
        lambda db: _reset_token(db, sub="nobody@example.com"),
    ],
    ids=["tampered", "expired", "wrong-purpose", "stale-fingerprint", "unknown-user"],
)
def test_bad_reset_tokens_rejected(client, member, test_db, make_token):
    response = client.post(
        "/api/password/reset",
        json={"token": make_token(test_db), "password": NEW_PASSWORD},
    )
    assert response.status_code == 400
    assert login(client, OLD_PASSWORD).status_code == 200


def test_access_token_cannot_reset(client, member):
    access = login(client, OLD_PASSWORD).json()["access_token"]
    response = client.post(
        "/api/password/reset", json={"token": access, "password": NEW_PASSWORD}
    )
    assert response.status_code == 400


def test_reset_token_is_not_an_access_token(client, member, test_db):
    response = client.get(
        "/api/user", headers={"Authorization": f"Bearer {_reset_token(test_db)}"}
    )
    assert response.status_code == 401


def test_short_password_rejected(client, member, test_db):
    response = client.post(
        "/api/password/reset", json={"token": _reset_token(test_db), "password": "short"}
    )
    assert response.status_code == 422


def test_smtp_failure_returns_502(client, member, monkeypatch):
    def fail(to, subject, body):
        raise OSError("connection refused")

    monkeypatch.setattr(main, "send_email", fail)
    response = client.post("/api/password/forgot", json={"identifier": EMAIL})
    assert response.status_code == 502
