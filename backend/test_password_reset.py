"""Tests for the forgot-password / reset-password flow."""
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from auth import hash_password
from auth_test_utils import SETUP_HEADERS, create_reset_token, create_signup_token
from database import get_db
from main import app
from models import Base, User

EMAIL = "member@example.com"
OLD_PASSWORD = "Oldpass123!"
NEW_PASSWORD = "Newpass123!"


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
def member(client, test_db):
    """An admin without an email (as setup creates) plus a self-signed-up member."""
    client.post("/api/setup", json={"username": "admin", "password": "Adminpass123!"}, headers=SETUP_HEADERS)
    response = client.post(
        "/api/signup/complete",
        json={"token": create_signup_token(test_db, EMAIL), "password": OLD_PASSWORD},
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
    assert response.json() == {"message": main.RESET_SENT_MESSAGE}
    assert [to for to, _ in sent] == [EMAIL]


def test_unknown_account_gets_the_same_response_and_no_email(client, sent, member):
    known = client.post("/api/password/forgot", json={"identifier": EMAIL})
    unknown = client.post("/api/password/forgot", json={"identifier": "nobody@example.com"})
    assert (unknown.status_code, unknown.json()) == (known.status_code, known.json())
    assert set(unknown.headers) - {"content-length"} == set(known.headers) - {"content-length"}
    assert [to for to, _ in sent] == [EMAIL]


def test_account_without_email_gets_the_same_response(client, sent, member):
    known = client.post("/api/password/forgot", json={"identifier": EMAIL})
    no_email = client.post("/api/password/forgot", json={"identifier": "admin"})
    assert (no_email.status_code, no_email.json()) == (known.status_code, known.json())
    assert len(sent) == 1


def test_forgot_response_never_echoes_the_submitted_address(client, sent, member):
    for identifier in (EMAIL, "nobody@example.com"):
        assert identifier not in client.post("/api/password/forgot", json={"identifier": identifier}).text


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


def _user(test_db):
    return test_db.query(User).filter(User.username == EMAIL).one()


@pytest.mark.parametrize(
    "make_token",
    [
        lambda db: create_reset_token(db, _user(db)) + "x",
        lambda db: create_reset_token(db, _user(db), ttl=timedelta(minutes=-1)),
        lambda db: create_signup_token(db, EMAIL),
        lambda db: "not-a-token",
    ],
    ids=["tampered", "expired", "wrong-purpose", "unknown"],
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
        "/api/user", headers={"Authorization": f"Bearer {create_reset_token(test_db, _user(test_db))}"}
    )
    assert response.status_code == 401


def test_weak_password_rejected_and_token_not_consumed(client, member, test_db):
    token = create_reset_token(test_db, _user(test_db))
    response = client.post("/api/password/reset", json={"token": token, "password": "Sh0rt!x"})
    assert response.status_code == 422
    assert "Sh0rt" not in response.text  # the rejected value is not echoed back
    assert client.post("/api/password/reset", json={"token": token, "password": NEW_PASSWORD}).status_code == 200


def test_newer_reset_link_invalidates_the_older_one(client, sent, member):
    client.post("/api/password/forgot", json={"identifier": EMAIL})
    client.post("/api/password/forgot", json={"identifier": EMAIL})
    old, new = (token_from(body) for _, body in sent)
    assert client.post("/api/password/reset", json={"token": old, "password": NEW_PASSWORD}).status_code == 400
    assert client.post("/api/password/reset", json={"token": new, "password": NEW_PASSWORD}).status_code == 200


def test_smtp_failure_is_not_disclosed(client, member, monkeypatch):
    def fail(to, subject, body):
        raise main.EmailDeliveryError("Could not send email")

    monkeypatch.setattr(main, "send_email", fail)
    response = client.post("/api/password/forgot", json={"identifier": EMAIL})
    assert response.status_code == 200
    assert response.json() == {"message": main.RESET_SENT_MESSAGE}


# Access-token invalidation after a reset

def _bearer(response):
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _reset(client, sent):
    client.post("/api/password/forgot", json={"identifier": EMAIL})
    response = client.post(
        "/api/password/reset", json={"token": token_from(sent[-1][1]), "password": NEW_PASSWORD}
    )
    assert response.status_code == 200


def test_reset_invalidates_earlier_access_tokens(client, sent, member):
    old = _bearer(login(client, OLD_PASSWORD))
    assert client.get("/api/boards", headers=old).status_code == 200

    _reset(client, sent)

    assert client.get("/api/boards", headers=old).status_code == 401
    assert client.post("/api/logout", headers=old).status_code == 401


def test_login_after_reset_gets_working_token(client, sent, member):
    _reset(client, sent)
    new = _bearer(login(client, NEW_PASSWORD))
    assert client.get("/api/boards", headers=new).status_code == 200


def test_reset_leaves_other_users_sessions_valid(client, sent, member):
    admin = _bearer(client.post("/api/login", json={"username": "admin", "password": "Adminpass123!"}))
    _reset(client, sent)
    assert client.get("/api/boards", headers=admin).status_code == 200


def test_setup_token_works_and_dies_on_admin_reset(client, test_db):
    response = client.post("/api/setup", json={"username": "admin", "password": "Adminpass123!"}, headers=SETUP_HEADERS)
    headers = _bearer(response)
    assert client.get("/api/boards", headers=headers).status_code == 200

    admin = test_db.query(User).filter(User.username == "admin").one()
    admin.password_hash = hash_password("Changed12345!")
    test_db.commit()

    assert client.get("/api/boards", headers=headers).status_code == 401


def test_token_with_wrong_fingerprint_is_rejected(client, member):
    from auth import create_access_token

    forged = {"Authorization": "Bearer " + create_access_token({"sub": EMAIL, "pwh": "0" * 16, "sv": 0})}
    assert client.get("/api/boards", headers=forged).status_code == 401


def test_token_without_fingerprint_is_rejected(client, member):
    from auth import create_access_token

    legacy = {"Authorization": "Bearer " + create_access_token({"sub": EMAIL})}
    assert client.get("/api/boards", headers=legacy).status_code == 401
