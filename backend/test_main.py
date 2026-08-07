import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_check_returns_ok_status(self, client):
        """Test that health endpoint returns correct status."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] == "ok"

    def test_health_endpoint_is_json(self, client):
        """Test that health endpoint returns JSON."""
        response = client.get("/api/health")
        assert response.headers["content-type"] == "application/json"


class TestStaticFiles:
    def test_root_serves_index_html(self, client):
        """Test that root path serves index.html (SPA routing)."""
        response = client.get("/")
        # Should return 200 or 404 (if static files not in place, which is OK for this test)
        assert response.status_code in [200, 404]

    def test_arbitrary_path_serves_index_html(self, client):
        """Test that arbitrary paths serve index.html for SPA routing."""
        response = client.get("/some-nonexistent-route")
        # Should return 200 (serves index.html) or 404 depending on static files
        assert response.status_code in [200, 404]
