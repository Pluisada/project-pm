"""Tests for email-based self signup."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from auth import ALGORITHM, SECRET_KEY, create_signup_token
from database import get_db
from main import app
from models import Base, User


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
def setup_done(client):
    response = client.post(
        "/api/setup", json={"username": "admin", "password": "adminpass123"}
    )
    assert response.status_code == 201


def token_from(body: str) -> str:
    link = next(word for word in body.split() if "signup_token=" in word)
    return parse_qs(urlparse(link).query)["signup_token"][0]


def test_full_signup_flow(client, sent, setup_done, test_db):
    response = client.post("/api/signup", json={"email": "New.User@Example.com"})
    assert response.status_code == 202
    assert len(sent) == 1
    to, body = sent[0]
    assert to == "new.user@example.com"

    response = client.post(
        "/api/signup/complete",
        json={"token": token_from(body), "password": "newpass123"},
    )
    assert response.status_code == 201

    user = test_db.query(User).filter(User.email == "new.user@example.com").one()
    assert user.username == "new.user@example.com"
    assert user.role == "member"

    response = client.post(
        "/api/login", json={"username": "New.User@example.com", "password": "newpass123"}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "member"


def test_token_is_single_use(client, setup_done):
    token = create_signup_token("a@example.com")
    payload = {"token": token, "password": "newpass123"}
    assert client.post("/api/signup/complete", json=payload).status_code == 201
    assert client.post("/api/signup/complete", json=payload).status_code == 409


def test_tampered_token_rejected(client, setup_done):
    token = create_signup_token("a@example.com") + "x"
    response = client.post(
        "/api/signup/complete", json={"token": token, "password": "newpass123"}
    )
    assert response.status_code == 400


def test_expired_token_rejected(client, setup_done):
    token = jwt.encode(
        {
            "sub": "a@example.com",
            "purpose": "signup",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    response = client.post(
        "/api/signup/complete", json={"token": token, "password": "newpass123"}
    )
    assert response.status_code == 400


def test_access_token_cannot_complete_signup(client, setup_done):
    access = client.post(
        "/api/login", json={"username": "admin", "password": "adminpass123"}
    ).json()["access_token"]
    response = client.post(
        "/api/signup/complete", json={"token": access, "password": "newpass123"}
    )
    assert response.status_code == 400


def test_existing_email_sends_nothing(client, sent, setup_done):
    client.post(
        "/api/signup/complete",
        json={"token": create_signup_token("a@example.com"), "password": "newpass123"},
    )
    response = client.post("/api/signup", json={"email": "a@example.com"})
    assert response.status_code == 202
    assert sent == []


def test_signup_before_setup_rejected(client, sent):
    response = client.post("/api/signup", json={"email": "a@example.com"})
    assert response.status_code == 409
    assert sent == []


def test_invalid_email_rejected(client, sent, setup_done):
    response = client.post("/api/signup", json={"email": "not-an-email"})
    assert response.status_code == 422


def test_short_password_rejected(client, setup_done):
    response = client.post(
        "/api/signup/complete",
        json={"token": create_signup_token("a@example.com"), "password": "short"},
    )
    assert response.status_code == 422


def test_smtp_failure_returns_502(client, setup_done, monkeypatch):
    def fail(to, subject, body):
        raise OSError("connection refused")

    monkeypatch.setattr(main, "send_email", fail)
    response = client.post("/api/signup", json={"email": "a@example.com"})
    assert response.status_code == 502


def test_signup_token_is_not_an_access_token(client, setup_done):
    """A signup link token must not authenticate API requests."""
    token = create_signup_token("a@example.com")
    client.post("/api/signup/complete", json={"token": token, "password": "newpass123"})
    response = client.get("/api/user", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
