"""Tests for AI module."""
import httpx
import pytest
from unittest.mock import patch, AsyncMock
import ai
from ai import test_ai_connectivity, AIError, call_ai

# Imported production function whose name starts with "test"; keep pytest from collecting it.
test_ai_connectivity.__test__ = False


class TestAPIKeyValidation:
    """Test API key validation."""

    def test_validate_api_key_present(self, monkeypatch):
        """Test that validation passes when API key is present."""
        monkeypatch.setattr(ai, "OPENROUTER_API_KEY", "test-key")
        assert ai.validate_api_key()

    def test_validate_api_key_missing(self, monkeypatch):
        """Test that validation fails when API key is missing."""
        monkeypatch.setattr(ai, "OPENROUTER_API_KEY", None)
        assert not ai.validate_api_key()


class TestAIConnectivity:
    """Test AI connectivity."""

    @pytest.mark.asyncio
    async def test_call_ai_missing_key(self):
        """Test that call_ai raises error when API key missing."""
        with patch("ai.validate_api_key", return_value=False):
            with pytest.raises(AIError, match="not configured"):
                await call_ai([{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_test_ai_connectivity_error_handling(self):
        """Test error handling in test_ai_connectivity."""
        with patch("ai.validate_api_key", return_value=False):
            result = await test_ai_connectivity("test")

            assert result["success"] is False
            assert "error" in result
            assert result["question"] == "test"

    @pytest.mark.asyncio
    async def test_call_ai_request_error(self):
        """Test handling of request errors."""
        with patch("ai.validate_api_key", return_value=True):
            with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("Network error")):
                with pytest.raises(AIError):
                    await call_ai([{"role": "user", "content": "test"}])


class TestDiagnosticsDistinguishCauses:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status,expected", [
        (401, "rejected the API key"), (403, "rejected the API key"),
        (404, "model unavailable"), (400, "model unavailable"),
        (429, "OpenRouter API error 429"), (503, "OpenRouter API error 503"),
    ])
    async def test_status_maps_to_cause(self, status, expected):
        request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        upstream = httpx.Response(status, json={"error": "sk-leak"}, request=request)
        with patch("ai.validate_api_key", return_value=True), \
                patch("httpx.AsyncClient.post", return_value=upstream):
            with pytest.raises(AIError) as caught:
                await call_ai([{"role": "user", "content": "x"}])
        assert expected in str(caught.value) and "sk-leak" not in str(caught.value)

    @pytest.mark.asyncio
    async def test_missing_key_message_names_variable_not_value(self, monkeypatch):
        monkeypatch.setattr(ai, "OPENROUTER_API_KEY", "")
        with pytest.raises(AIError, match="OPENROUTER_API_KEY not configured"):
            await call_ai([{"role": "user", "content": "x"}])

    def test_key_is_read_from_environment_at_import(self):
        import os, subprocess, sys
        env = {**os.environ, "OPENROUTER_API_KEY": "env-key"}
        code = ("import ai; print(ai.OPENROUTER_API_KEY == 'env-key', ai.MODEL_ID, ai.OPENROUTER_BASE_URL)")
        out = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, '.'); " + code],
                             env=env, capture_output=True, text=True, check=True).stdout.split()
        assert out == ["True", "openai/gpt-oss-120b", "https://openrouter.ai/api/v1"]


class TestAIErrorHandling:
    """Test AI error handling."""

    def test_ai_error_is_exception(self):
        """Test that AIError is an Exception."""
        error = AIError("test error")
        assert isinstance(error, Exception)
        assert str(error) == "test error"


class TestUpstreamErrorsAreSanitized:
    """F9: upstream bodies and transport details are neither returned to the caller nor logged."""

    @pytest.mark.asyncio
    async def test_non_success_response_body_is_in_neither_client_error_nor_logs(self, caplog):
        request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        upstream = httpx.Response(
            500, json={"error": {"message": "SELECT secret upstream-body prompt-echo"}},
            headers={"x-request-id": "req-abc_123"}, request=request,
        )
        with patch("ai.validate_api_key", return_value=True), \
                patch("httpx.AsyncClient.post", return_value=upstream), caplog.at_level("DEBUG"):
            with pytest.raises(AIError) as caught:
                await call_ai([{"role": "user", "content": "test"}])

        assert str(caught.value) == "OpenRouter API error 500"
        for leaked in ("secret", "upstream-body", "prompt-echo"):
            assert leaked not in str(caught.value) and leaked not in caplog.text
        assert "category=upstream_http_error" in caplog.text
        assert "status=500" in caplog.text and "request_id=req-abc_123" in caplog.text

    @pytest.mark.asyncio
    async def test_unsafe_upstream_request_id_is_not_logged(self, caplog):
        request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        upstream = httpx.Response(502, headers={"x-request-id": "bad id; SELECT secret"}, request=request)
        with patch("ai.validate_api_key", return_value=True), \
                patch("httpx.AsyncClient.post", return_value=upstream), caplog.at_level("DEBUG"):
            with pytest.raises(AIError):
                await call_ai([{"role": "user", "content": "test"}])

        assert "secret" not in caplog.text and "request_id=-" in caplog.text

    @pytest.mark.asyncio
    async def test_transport_error_details_are_in_neither_client_error_nor_logs(self, caplog):
        error = httpx.ConnectError("https://openrouter.ai/x?key=sk-live-123 10.0.0.7:443 refused SELECT secret")
        with patch("ai.validate_api_key", return_value=True), \
                patch("httpx.AsyncClient.post", side_effect=error), caplog.at_level("DEBUG"):
            with pytest.raises(AIError) as caught:
                await call_ai([{"role": "user", "content": "test"}])

        assert str(caught.value) == "OpenRouter API request failed"
        for leaked in ("sk-live-123", "10.0.0.7", "secret", "openrouter.ai/x"):
            assert leaked not in str(caught.value) and leaked not in caplog.text
        assert "category=transport_error" in caplog.text and "exception=ConnectError" in caplog.text

    @pytest.mark.asyncio
    async def test_timeout_is_logged_by_class_only(self, caplog):
        error = httpx.ReadTimeout("read timed out for https://openrouter.ai/x?key=sk-live-123")
        with patch("ai.validate_api_key", return_value=True), \
                patch("httpx.AsyncClient.post", side_effect=error), caplog.at_level("DEBUG"):
            with pytest.raises(AIError, match="timeout"):
                await call_ai([{"role": "user", "content": "test"}])

        assert "sk-live-123" not in caplog.text
        assert "category=timeout" in caplog.text and "exception=ReadTimeout" in caplog.text

    @pytest.mark.asyncio
    async def test_chat_client_message_carries_no_upstream_detail(self):
        from ai_kanban import call_ai_with_board
        error = AIError("OpenRouter API error 500")
        with patch("ai_kanban.call_ai", side_effect=error):
            result = await call_ai_with_board({"columns": []}, [], "hi")
        assert result == {"success": False, "error": "OpenRouter API error 500"}
