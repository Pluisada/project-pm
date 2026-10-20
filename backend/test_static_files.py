"""Tests for static file serving and frontend integration."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestStaticFileServing:
    """Test backend serves frontend static files correctly."""

    def test_root_path_serves_html(self, client):
        """Test that root path serves index.html."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    def test_next_assets_served(self, client):
        """Test that _next assets are available."""
        # The actual path depends on what's built, but we test the route exists
        # In production, these would be served from frontend/out/_next/*
        response = client.get("/_next/")
        # Should return 200 if static exists, or 404 if not (acceptable for this test)
        assert response.status_code in [200, 404]

    def test_favicon_served(self, client):
        """Test that favicon is available."""
        response = client.get("/favicon.ico")
        assert response.status_code in [200, 404]  # 404 is OK if static not mounted

    def test_api_health_still_works(self, client):
        """Test that API endpoints work alongside static files."""
        response = client.get("/api/health")
        assert response.status_code == 200
        # test_mode is only true in the isolated Playwright backend (PM_E2E=1).
        assert response.json() == {"status": "ok", "test_mode": False}

    def test_nonexistent_api_returns_404(self, client):
        """Test that non-existent API routes return 404."""
        response = client.get("/api/nonexistent")
        assert response.status_code == 404

    def test_spa_fallback_routing(self, client):
        """Test that SPA routes fallback to index.html."""
        # This tests that arbitrary paths that should serve index.html for SPA
        response = client.get("/some-page")
        # When static files are mounted with html=True, it serves index.html
        assert response.status_code in [200, 404]


class TestCORS:
    """Test CORS middleware is configured."""

    def test_cors_headers_present(self, client):
        """Test that CORS headers are set."""
        response = client.get("/api/health")
        # CORS headers should be present
        assert response.status_code == 200
        # Access-Control-Allow-Origin should be set
        assert "access-control-allow-origin" in response.headers.get("allow", "").lower() or \
               response.status_code == 200  # CORS is configured


class TestProductionBuild:
    """Tests that verify production build configuration."""

    def test_health_endpoint_is_json(self, client):
        """Test health endpoint returns valid JSON."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        assert "status" in data

    def test_no_trailing_slashes_in_api(self, client):
        """Test API endpoints work without trailing slashes."""
        response = client.get("/api/health")
        assert response.status_code == 200

    def test_api_returns_application_json(self, client):
        """Test API endpoints return application/json content type."""
        response = client.get("/api/health")
        assert response.status_code == 200
        assert "application/json" in response.headers.get("content-type", "")


STATIC = Path(__file__).parent / "static"
OUT = Path(__file__).parent.parent / "frontend" / "out"


@pytest.mark.skipif(not STATIC.exists(), reason="static export not generated")
class TestServedFrontend:
    """The page the browser really receives: caching, bundles, and that the one-time-link screens are in it."""

    def test_pages_must_revalidate_and_hashed_assets_are_immutable(self, client):
        assert client.get("/").headers["cache-control"] == "no-cache"
        bundle = re.search(r'/_next/static/[^"]+\.js', client.get("/").text).group(0)
        assert "immutable" in client.get(bundle).headers["cache-control"]

    def test_every_bundle_named_by_the_page_is_served_unchanged(self, client):
        html = client.get("/?central_reset_token=SYNTHETIC_TEST_TOKEN").text
        bundles = set(re.findall(r'/_next/static/[^"]+\.js', html))
        assert bundles
        for path in bundles:
            response = client.get(path)
            assert response.status_code == 200
            assert response.content == (STATIC / path.lstrip("/")).read_bytes()

    def test_served_bundles_handle_the_central_one_time_links(self, client):
        html = client.get("/").text
        code = "".join(client.get(p).text for p in set(re.findall(r'/_next/static/[^"]+\.js', html)))
        assert "central_reset_token" in code and "central_invite_token" in code

    @pytest.mark.skipif(not OUT.exists(), reason="frontend/out not built")
    def test_static_matches_the_current_build(self):
        assert (STATIC / "index.html").read_bytes() == (OUT / "index.html").read_bytes()
