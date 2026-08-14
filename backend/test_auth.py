"""Tests for authentication module."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import (
    create_access_token,
    hash_password,
    verify_password,
    verify_token,
)
from database import get_db
from main import app
from models import Base


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


def create_admin(client, username="admin", password="adminpass123"):
    """Run first-run setup, returning the admin's access token."""
    response = client.post(
        "/api/setup", json={"username": username, "password": password}
    )
    assert response.status_code == 201
    return response.json()["access_token"]


class TestAuthHelpers:
    """Test authentication helper functions."""

    def test_hash_password_produces_different_hash_than_input(self):
        """Test that hashing never stores the plaintext password."""
        assert hash_password("password") != "password"

    def test_verify_password_valid(self):
        """Test that a correct password verifies against its hash."""
        hashed = hash_password("password")
        assert verify_password("password", hashed) is True

    def test_verify_password_invalid(self):
        """Test that a wrong password is rejected."""
        hashed = hash_password("password")
        assert verify_password("wrong", hashed) is False

    def test_create_access_token(self):
        """Test that access token is created."""
        token = create_access_token(data={"sub": "user"})
        assert isinstance(token, str)
        assert len(token) > 0

    def test_verify_token_valid(self):
        """Test that valid token is verified."""
        token = create_access_token(data={"sub": "testuser"})
        username = verify_token(token)
        assert username == "testuser"

    def test_verify_token_invalid(self):
        """Test that invalid token is rejected."""
        username = verify_token("invalid.token.here")
        assert username is None

    def test_verify_token_empty(self):
        """Test that empty token is rejected."""
        username = verify_token("")
        assert username is None


class TestSetupEndpoint:
    """Test the first-run admin setup endpoint."""

    def test_setup_status_before_setup(self, client):
        """Test setup status reports needs_setup while no users exist."""
        response = client.get("/api/setup/status")
        assert response.status_code == 200
        assert response.json() == {"needs_setup": True}

    def test_setup_creates_admin(self, client):
        """Test that setup creates the first user as admin."""
        response = client.post(
            "/api/setup", json={"username": "admin", "password": "adminpass123"}
        )
        assert response.status_code == 201
        data = response.json()
        assert "access_token" in data
        assert data["username"] == "admin"
        assert data["role"] == "admin"

    def test_setup_status_after_setup(self, client):
        """Test setup status reports completed after the first user exists."""
        create_admin(client)
        response = client.get("/api/setup/status")
        assert response.json() == {"needs_setup": False}

    def test_setup_twice_is_rejected(self, client):
        """Test that setup can only run once."""
        create_admin(client)
        response = client.post(
            "/api/setup", json={"username": "someone_else", "password": "password123"}
        )
        assert response.status_code == 409

    def test_setup_short_password_rejected(self, client):
        """Test that a too-short password fails validation."""
        response = client.post(
            "/api/setup", json={"username": "admin", "password": "short"}
        )
        assert response.status_code == 422


class TestLoginEndpoint:
    """Test login endpoint."""

    def test_login_valid_credentials(self, client):
        """Test login with valid credentials."""
        create_admin(client, "admin", "adminpass123")
        response = client.post(
            "/api/login", json={"username": "admin", "password": "adminpass123"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["username"] == "admin"
        assert data["role"] == "admin"

    def test_login_invalid_credentials(self, client):
        """Test login with invalid credentials."""
        create_admin(client, "admin", "adminpass123")
        response = client.post(
            "/api/login", json={"username": "admin", "password": "wrong"}
        )
        assert response.status_code == 401
        assert "Invalid credentials" in response.json()["detail"]

    def test_login_unknown_user(self, client):
        """Test login with a username that doesn't exist."""
        response = client.post(
            "/api/login", json={"username": "nobody", "password": "whatever123"}
        )
        assert response.status_code == 401

    def test_login_missing_username(self, client):
        """Test login with missing username."""
        response = client.post("/api/login", json={"password": "password123"})
        assert response.status_code == 422  # Validation error

    def test_login_missing_password(self, client):
        """Test login with missing password."""
        response = client.post("/api/login", json={"username": "admin"})
        assert response.status_code == 422  # Validation error

    def test_login_response_format(self, client):
        """Test login response format is correct."""
        create_admin(client, "admin", "adminpass123")
        response = client.post(
            "/api/login", json={"username": "admin", "password": "adminpass123"}
        )
        data = response.json()
        assert "access_token" in data
        assert "token_type" in data
        assert "username" in data
        assert "role" in data


class TestLogoutEndpoint:
    """Test logout endpoint."""

    def test_logout_with_valid_token(self, client):
        """Test logout with valid token."""
        token = create_admin(client)

        logout_response = client.post(
            "/api/logout", headers={"Authorization": f"Bearer {token}"}
        )
        assert logout_response.status_code == 200
        assert logout_response.json()["username"] == "admin"

    def test_logout_without_token(self, client):
        """Test logout without token."""
        response = client.post("/api/logout")
        assert response.status_code == 401

    def test_logout_with_invalid_token(self, client):
        """Test logout with invalid token."""
        response = client.post("/api/logout", headers={"Authorization": "Bearer invalid"})
        assert response.status_code == 401

    def test_logout_invalid_header_format(self, client):
        """Test logout with invalid header format."""
        response = client.post("/api/logout", headers={"Authorization": "invalid"})
        assert response.status_code == 401


class TestProtectedEndpoints:
    """Test protected endpoints."""

    def test_get_user_with_valid_token(self, client):
        """Test accessing protected endpoint with valid token."""
        token = create_admin(client)

        response = client.get(
            "/api/user", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"
        assert data["authenticated"] is True

    def test_get_user_without_token(self, client):
        """Test accessing protected endpoint without token."""
        response = client.get("/api/user")
        assert response.status_code == 401

    def test_get_user_with_invalid_token(self, client):
        """Test accessing protected endpoint with invalid token."""
        response = client.get(
            "/api/user", headers={"Authorization": "Bearer invalid.token"}
        )
        assert response.status_code == 401

    def test_get_user_with_expired_token(self, client):
        """Test accessing protected endpoint with expired token."""
        from datetime import timedelta

        create_admin(client)
        token = create_access_token(
            data={"sub": "admin"}, expires_delta=timedelta(seconds=-1)
        )
        response = client.get(
            "/api/user", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401

    def test_get_user_for_deleted_or_unknown_user(self, client):
        """Test a token for a username that no longer/never existed."""
        token = create_access_token(data={"sub": "ghost"})
        response = client.get(
            "/api/user", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401


class TestAuthFlow:
    """Test complete auth flow."""

    def test_complete_setup_login_logout_flow(self, client):
        """Test complete setup -> login -> access protected -> logout flow."""
        # 1. First-run setup
        create_admin(client, "admin", "adminpass123")

        # 2. Login
        login_response = client.post(
            "/api/login", json={"username": "admin", "password": "adminpass123"}
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]

        # 3. Access protected resource
        user_response = client.get(
            "/api/user", headers={"Authorization": f"Bearer {token}"}
        )
        assert user_response.status_code == 200

        # 4. Logout
        logout_response = client.post(
            "/api/logout", headers={"Authorization": f"Bearer {token}"}
        )
        assert logout_response.status_code == 200

        # 5. Try to access protected resource again with same token (still valid)
        # Note: in a real system, we'd invalidate the token on logout
        # For MVP, we just provide logout endpoint
        user_response2 = client.get(
            "/api/user", headers={"Authorization": f"Bearer {token}"}
        )
        # Token is still valid (would need token blacklist for true logout)
        assert user_response2.status_code == 200
