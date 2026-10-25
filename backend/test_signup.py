"""Tests for email-based self signup."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from auth_test_utils import SETUP_HEADERS, create_signup_token
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
        "/api/setup", json={"username": "admin", "password": "Adminpass123!"}, headers=SETUP_HEADERS
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
        json={"token": token_from(body), "password": "Newpass123!"},
    )
    assert response.status_code == 201

    user = test_db.query(User).filter(User.email == "new.user@example.com").one()
    assert user.username == "new.user@example.com"
    assert user.role == "member"

    response = client.post(
        "/api/login", json={"username": "New.User@example.com", "password": "Newpass123!"}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "member"


def test_signup_link_is_stored_hashed_and_account_is_verified(client, sent, setup_done, test_db):
    from models import AuthToken
    from token_store import hash_token

    client.post("/api/signup", json={"email": "a@example.com"})
    raw = token_from(sent[0][1])
    row = test_db.query(AuthToken).one()
    assert row.token_hash == hash_token(raw) and raw not in (row.token_hash, row.email)
    assert row.purpose == "signup" and row.email == "a@example.com" and row.user_id is None

    client.post("/api/signup/complete", json={"token": raw, "password": "Newpass123!"})
    assert test_db.query(User).filter(User.email == "a@example.com").one().email_verified_at is not None
    assert test_db.query(AuthToken).one().consumed_at is not None


def test_no_user_and_no_access_before_the_link_is_used(client, sent, setup_done, test_db):
    client.post("/api/signup", json={"email": "a@example.com"})
    assert test_db.query(User).filter(User.email == "a@example.com").count() == 0
    assert client.post("/api/login", json={"username": "a@example.com", "password": "Newpass123!"}).status_code == 401


def test_token_is_single_use(client, setup_done, test_db):
    token = create_signup_token(test_db, "a@example.com")
    payload = {"token": token, "password": "Newpass123!"}
    assert client.post("/api/signup/complete", json=payload).status_code == 201
    assert client.post("/api/signup/complete", json=payload).status_code == 400


def test_tampered_token_rejected(client, setup_done, test_db):
    token = create_signup_token(test_db, "a@example.com") + "x"
    response = client.post(
        "/api/signup/complete", json={"token": token, "password": "Newpass123!"}
    )
    assert response.status_code == 400


def test_expired_token_rejected(client, setup_done, test_db):
    token = create_signup_token(test_db, "a@example.com", ttl=timedelta(minutes=-1))
    response = client.post(
        "/api/signup/complete", json={"token": token, "password": "Newpass123!"}
    )
    assert response.status_code == 400
    assert test_db.query(User).filter(User.email == "a@example.com").count() == 0


def test_newer_link_invalidates_the_older_one(client, sent, setup_done):
    client.post("/api/signup", json={"email": "a@example.com"})
    client.post("/api/signup", json={"email": "a@example.com"})
    old, new = (token_from(body) for _, body in sent)
    assert client.post("/api/signup/complete", json={"token": old, "password": "Newpass123!"}).status_code == 400
    assert client.post("/api/signup/complete", json={"token": new, "password": "Newpass123!"}).status_code == 201


def test_access_token_cannot_complete_signup(client, setup_done):
    access = client.post(
        "/api/login", json={"username": "admin", "password": "Adminpass123!"}
    ).json()["access_token"]
    response = client.post(
        "/api/signup/complete", json={"token": access, "password": "Newpass123!"}
    )
    assert response.status_code == 400


def test_reset_token_cannot_complete_signup(client, sent, setup_done, test_db):
    from auth_test_utils import create_reset_token

    admin = test_db.query(User).filter(User.username == "admin").one()
    response = client.post(
        "/api/signup/complete",
        json={"token": create_reset_token(test_db, admin), "password": "Newpass123!"},
    )
    assert response.status_code == 400


def test_existing_email_sends_nothing(client, sent, setup_done, test_db):
    client.post(
        "/api/signup/complete",
        json={"token": create_signup_token(test_db, "a@example.com"), "password": "Newpass123!"},
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


def test_weak_password_rejected_and_link_stays_usable(client, setup_done, test_db):
    token = create_signup_token(test_db, "a@example.com")
    response = client.post("/api/signup/complete", json={"token": token, "password": "short"})
    assert response.status_code == 422
    assert client.post("/api/signup/complete", json={"token": token, "password": "Newpass123!"}).status_code == 201


def test_smtp_failure_is_not_disclosed(client, setup_done, monkeypatch):
    def fail(to, subject, body):
        raise main.EmailDeliveryError("Could not send email")

    monkeypatch.setattr(main, "send_email", fail)
    response = client.post("/api/signup", json={"email": "a@example.com"})
    assert response.status_code == 202


def test_signup_token_is_not_an_access_token(client, setup_done, test_db):
    """A signup link token must not authenticate API requests."""
    token = create_signup_token(test_db, "a@example.com")
    client.post("/api/signup/complete", json={"token": token, "password": "Newpass123!"})
    response = client.get("/api/user", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
