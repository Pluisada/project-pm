"""Tests for authentication module."""
import pytest
from fastapi.testclient import TestClient

from auth import (
    create_access_token,
    verify_credentials,
    verify_token,
)
from main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestAuthHelpers:
    """Test authentication helper functions."""

    def test_verify_credentials_valid(self):
        """Test that valid credentials are verified."""
        assert verify_credentials("user", "password") is True

    def test_verify_credentials_invalid_username(self):
        """Test that invalid username is rejected."""
        assert verify_credentials("wrong", "password") is False

    def test_verify_credentials_invalid_password(self):
        """Test that invalid password is rejected."""
        assert verify_credentials("user", "wrong") is False

    def test_verify_credentials_empty(self):
        """Test that empty credentials are rejected."""
        assert verify_credentials("", "") is False

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


class TestLoginEndpoint:
    """Test login endpoint."""

    def test_login_valid_credentials(self, client):
        """Test login with valid credentials."""
        response = client.post(
            "/api/login", json={"username": "user", "password": "password"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["username"] == "user"

    def test_login_invalid_credentials(self, client):
        """Test login with invalid credentials."""
        response = client.post(
            "/api/login", json={"username": "wrong", "password": "wrong"}
        )
        assert response.status_code == 401
        assert "Invalid credentials" in response.json()["detail"]

    def test_login_missing_username(self, client):
        """Test login with missing username."""
        response = client.post("/api/login", json={"password": "password"})
        assert response.status_code == 422  # Validation error

    def test_login_missing_password(self, client):
        """Test login with missing password."""
        response = client.post("/api/login", json={"username": "user"})
        assert response.status_code == 422  # Validation error

    def test_login_empty_fields(self, client):
        """Test login with empty fields."""
        response = client.post(
            "/api/login", json={"username": "", "password": ""}
        )
        assert response.status_code == 401

    def test_login_response_format(self, client):
        """Test login response format is correct."""
        response = client.post(
            "/api/login", json={"username": "user", "password": "password"}
        )
        data = response.json()
        assert "access_token" in data
        assert "token_type" in data
        assert "username" in data


class TestLogoutEndpoint:
    """Test logout endpoint."""

    def test_logout_with_valid_token(self, client):
        """Test logout with valid token."""
        # First login
        login_response = client.post(
            "/api/login", json={"username": "user", "password": "password"}
        )
        token = login_response.json()["access_token"]

        # Then logout
        logout_response = client.get(
            "/api/logout", headers={"Authorization": f"Bearer {token}"}
        )
        assert logout_response.status_code == 200
        assert logout_response.json()["username"] == "user"

    def test_logout_without_token(self, client):
        """Test logout without token."""
        response = client.get("/api/logout")
        assert response.status_code == 401

    def test_logout_with_invalid_token(self, client):
        """Test logout with invalid token."""
        response = client.get("/api/logout", headers={"Authorization": "Bearer invalid"})
        assert response.status_code == 401

    def test_logout_invalid_header_format(self, client):
        """Test logout with invalid header format."""
        response = client.get("/api/logout", headers={"Authorization": "invalid"})
        assert response.status_code == 401


class TestProtectedEndpoints:
    """Test protected endpoints."""

    def test_get_user_with_valid_token(self, client):
        """Test accessing protected endpoint with valid token."""
        # Login first
        login_response = client.post(
            "/api/login", json={"username": "user", "password": "password"}
        )
        token = login_response.json()["access_token"]

        # Access protected endpoint
        response = client.get(
            "/api/user", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "user"
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
        # Create a token that's already expired
        from datetime import timedelta

        from auth import create_access_token

        token = create_access_token(
            data={"sub": "user"}, expires_delta=timedelta(seconds=-1)
        )
        response = client.get(
            "/api/user", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401


class TestAuthFlow:
    """Test complete auth flow."""

    def test_complete_login_logout_flow(self, client):
        """Test complete login -> access protected -> logout flow."""
        # 1. Login
        login_response = client.post(
            "/api/login", json={"username": "user", "password": "password"}
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]

        # 2. Access protected resource
        user_response = client.get(
            "/api/user", headers={"Authorization": f"Bearer {token}"}
        )
        assert user_response.status_code == 200

        # 3. Logout
        logout_response = client.get(
            "/api/logout", headers={"Authorization": f"Bearer {token}"}
        )
        assert logout_response.status_code == 200

        # 4. Try to access protected resource again with same token (still valid)
        # Note: in a real system, we'd invalidate the token on logout
        # For MVP, we just provide logout endpoint
        user_response2 = client.get(
            "/api/user", headers={"Authorization": f"Bearer {token}"}
        )
        # Token is still valid (would need token blacklist for true logout)
        assert user_response2.status_code == 200
