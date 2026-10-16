"""AI chat: size limits, per-user rate limits, input validation, normal behaviour."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import ai_limits
import routes
from auth import create_user_access_token, hash_password
from database import get_db
from main import app
from models import Base, Board, ConversationMessage, User
from auth_test_utils import VERIFIED


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all([
        User(email_verified_at=VERIFIED, username="admin", password_hash=hash_password("Adminpass123!"), role="admin"),
        User(email_verified_at=VERIFIED, username="member@example.com", email="member@example.com",
             password_hash=hash_password("Memberpass123!"), role="member"),
    ])
    db.commit()
    db.add(Board(user_id=1, title="B"))
    db.commit()
    yield db
    db.close()


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def ai_calls(monkeypatch):
    calls = []

    async def fake(board_state, history, message):
        calls.append(message)
        return {"success": True, "response": "ok", "actions": [], "confidence": 1, "tokens_used": 1}

    monkeypatch.setattr(routes, "call_ai_with_board", fake)
    return calls


def auth(name="admin"):
    db = app.dependency_overrides[get_db]()
    user = db.query(User).filter(User.username == name).one()
    return {"Authorization": f"Bearer {create_user_access_token(user)}"}


def chat(client, body=None, headers=None, **kw):
    return client.post("/api/boards/1/ai", json=body if body is not None else {"message": "hi"},
                       headers=headers or auth(), **kw)


# --- normal behaviour -------------------------------------------------------

def test_normal_chat_still_works_and_saves_messages(client, session, ai_calls):
    response = chat(client, {"message": "move the Acme card"})
    assert response.status_code == 200
    assert response.json()["response"] == "ok"
    assert ai_calls == ["move the Acme card"]
    assert [m.role for m in session.query(ConversationMessage).all()] == ["user", "assistant"]


def test_message_at_the_limit_is_accepted(client, ai_calls, monkeypatch):
    monkeypatch.setenv("AI_MAX_MESSAGE_CHARS", "50")
    assert chat(client, {"message": "x" * 50}).status_code == 200


def test_requires_authentication(client, ai_calls):
    assert client.post("/api/boards/1/ai", json={"message": "hi"}).status_code == 401
    assert ai_calls == []


# --- message size and invalid input ----------------------------------------

def test_too_long_message_is_rejected_without_echo_or_model_call(client, session, ai_calls, monkeypatch):
    monkeypatch.setenv("AI_MAX_MESSAGE_CHARS", "50")
    response = chat(client, {"message": "SECRETTEXT" * 10})
    assert response.status_code == 422
    assert response.json()["detail"] == "Message too long (maximum 50 characters)"
    assert "SECRETTEXT" not in response.text
    assert ai_calls == []
    assert session.query(ConversationMessage).count() == 0


@pytest.mark.parametrize("body", [{}, {"message": ""}, {"message": "   \n"}])
def test_empty_message_rejected(client, ai_calls, body):
    response = chat(client, body)
    assert response.status_code == 422
    assert ai_calls == []


@pytest.mark.parametrize("body", [{"message": 123}, {"message": ["a"]}, {"message": None}, ["hi"], "hi"])
def test_non_string_message_or_body_rejected(client, ai_calls, body):
    assert chat(client, body).status_code == 422
    assert ai_calls == []


def test_malformed_json_rejected(client, ai_calls):
    response = client.post("/api/boards/1/ai", content=b"{not json",
                           headers={**auth(), "Content-Type": "application/json"})
    assert response.status_code == 422
    assert ai_calls == []


@pytest.mark.parametrize("body", [
    {"message": 123456789},
    {"message": ["SECRETTEXT"]},
    {"message": {"k": "SECRETTEXT"}},
    {"message": None, "extra": "SECRETTEXT"},
    ["SECRETTEXT"],
    "SECRETTEXT",
])
def test_validation_error_does_not_echo_input(client, ai_calls, body):
    response = chat(client, body)
    assert response.status_code == 422
    assert response.json() == {"detail": ai_limits.INVALID_REQUEST}
    assert "SECRETTEXT" not in response.text
    assert "123456789" not in response.text
    assert ai_calls == []


def test_malformed_json_error_does_not_echo_body(client, ai_calls):
    response = client.post("/api/boards/1/ai", content=b'{"message": SECRETTEXT',
                           headers={**auth(), "Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json() == {"detail": ai_limits.INVALID_REQUEST}
    assert "SECRETTEXT" not in response.text
    assert ai_calls == []


def test_validation_handler_leaves_other_routes_default(client):
    response = client.post("/api/boards", json={"title": 123456789}, headers=auth())
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


# --- request body size ------------------------------------------------------

def test_oversized_body_gets_413_before_auth_or_parsing(client, ai_calls, monkeypatch):
    monkeypatch.setenv("AI_MAX_REQUEST_BYTES", "200")
    response = client.post("/api/boards/1/ai", content=b"x" * 500,
                           headers={**auth(), "Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json() == {"detail": ai_limits.TOO_LARGE}
    assert ai_calls == []


def test_oversized_chunked_body_gets_413(client, ai_calls, monkeypatch):
    monkeypatch.setenv("AI_MAX_REQUEST_BYTES", "200")

    def body():
        for _ in range(10):
            yield b"x" * 100

    response = client.post("/api/boards/1/ai", content=body(),
                           headers={**auth(), "Content-Type": "application/json"})
    assert response.status_code == 413
    assert ai_calls == []


def test_body_within_cap_passes_through_the_middleware(client, ai_calls, monkeypatch):
    monkeypatch.setenv("AI_MAX_REQUEST_BYTES", "200")
    assert chat(client, {"message": "short"}).status_code == 200


def test_size_cap_only_applies_to_the_chat_path(client, monkeypatch):
    monkeypatch.setenv("AI_MAX_REQUEST_BYTES", "10")
    response = client.post("/api/login", json={"username": "admin", "password": "x" * 100})
    assert response.status_code != 413


# --- rate limiting ----------------------------------------------------------

def test_burst_limit_returns_429_with_retry_after(client, ai_calls, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AI_USER", "2/60")
    assert chat(client).status_code == 200
    assert chat(client).status_code == 200
    response = chat(client)
    assert response.status_code == 429
    assert response.json() == {"detail": "Too many requests. Try again later."}
    assert int(response.headers["Retry-After"]) >= 1
    assert len(ai_calls) == 2


def test_hourly_ceiling(client, ai_calls, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AI_USER_HOUR", "2/3600")
    assert chat(client).status_code == 200
    assert chat(client).status_code == 200
    assert chat(client).status_code == 429


def test_limit_is_per_user(client, ai_calls, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AI_USER", "1/60")
    assert chat(client).status_code == 200
    assert chat(client).status_code == 429
    assert chat(client, headers=auth("member@example.com")).status_code == 200


def test_invalid_messages_count_against_the_limit(client, ai_calls, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AI_USER", "2/60")
    assert chat(client, {"message": ""}).status_code == 422
    assert chat(client, {"message": ""}).status_code == 422
    assert chat(client).status_code == 429


def test_anonymous_requests_do_not_use_the_counters(client, ai_calls, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AI_USER", "1/60")
    for _ in range(3):
        assert client.post("/api/boards/1/ai", json={"message": "hi"}).status_code == 401
    assert chat(client).status_code == 200


def test_rate_limit_can_be_disabled(client, ai_calls, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AI_USER", "1/60")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    assert chat(client).status_code == 200
    assert chat(client).status_code == 200


# --- configuration ----------------------------------------------------------

@pytest.mark.parametrize("var", ["AI_MAX_MESSAGE_CHARS", "AI_MAX_REQUEST_BYTES"])
@pytest.mark.parametrize("value", ["0", "-5", "abc", "1.5"])
def test_malformed_size_settings_fail_validation(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ValueError, match=var):
        ai_limits.validate_config()


def test_defaults_are_valid():
    ai_limits.validate_config()
    assert ai_limits.max_message_chars() == 4000
    assert ai_limits.max_request_bytes() == 32768
