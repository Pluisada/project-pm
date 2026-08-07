"""Tests for AI module."""
import pytest
from unittest.mock import patch, AsyncMock
from ai import validate_api_key, test_ai_connectivity, AIError, call_ai


class TestAPIKeyValidation:
    """Test API key validation."""

    def test_validate_api_key_present(self):
        """Test that validation passes when API key is present."""
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            # Reload module to pick up env var
            import importlib
            import ai
            importlib.reload(ai)
            assert ai.validate_api_key()

    def test_validate_api_key_missing(self):
        """Test that validation fails when API key is missing."""
        with patch.dict("os.environ", {}, clear=True):
            import importlib
            import ai
            importlib.reload(ai)
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
            with patch("httpx.AsyncClient.post", side_effect=Exception("Network error")):
                with pytest.raises(AIError):
                    await call_ai([{"role": "user", "content": "test"}])


class TestAIErrorHandling:
    """Test AI error handling."""

    def test_ai_error_is_exception(self):
        """Test that AIError is an Exception."""
        error = AIError("test error")
        assert isinstance(error, Exception)
        assert str(error) == "test error"
