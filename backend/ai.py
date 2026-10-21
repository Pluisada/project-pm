"""AI integration with OpenRouter."""
import logging
import os
import json
import re
from typing import Optional

import httpx

# Configuration
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MODEL_ID = "openai/gpt-oss-120b"
REQUEST_TIMEOUT = 30.0

logger = logging.getLogger(__name__)

_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def _upstream_request_id(response: httpx.Response) -> str:
    """The provider's request id for support correlation, only if it is a plain token (else "-")."""
    value = response.headers.get("x-request-id", "")
    return value if _REQUEST_ID.fullmatch(value) else "-"


class AIError(Exception):
    """AI service error."""

    pass


def validate_api_key() -> bool:
    """Validate that OPENROUTER_API_KEY is configured."""
    return bool(OPENROUTER_API_KEY)


async def call_ai(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2000,
    response_format: Optional[str] = None,
) -> dict:
    """
    Call OpenRouter API with the configured model.

    Args:
        messages: List of messages in OpenAI format
        temperature: Sampling temperature (0-2)
        max_tokens: Maximum tokens in response

    Returns:
        Response dict with 'content', 'model', 'tokens_used', etc.

    Raises:
        AIError: If API call fails
    """
    if not validate_api_key():
        raise AIError("OPENROUTER_API_KEY not configured")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "PM App",
    }

    payload = {
        "model": MODEL_ID,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if response_format == "json_object":
        payload["response_format"] = {"type": "json_object"}

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )

            if response.status_code != 200:
                # Neither the upstream body (it can echo prompt text) nor the request is logged or returned:
                # only the status, a category and the provider's request id.
                logger.error(
                    "AI upstream failure: category=upstream_http_error status=%s request_id=%s",
                    response.status_code, _upstream_request_id(response),
                )
                raise AIError(f"OpenRouter API error {response.status_code}")

            data = response.json()

            # Extract response content
            if "choices" not in data or not data["choices"]:
                raise AIError("Invalid response format from OpenRouter")

            content = data["choices"][0]["message"]["content"]

            return {
                "content": content,
                "model": data.get("model", MODEL_ID),
                "tokens_used": data.get("usage", {}).get("total_tokens", 0),
                "raw_response": data,
            }

    except httpx.TimeoutException as e:
        logger.error("AI upstream failure: category=timeout exception=%s", type(e).__name__)
        raise AIError("OpenRouter API request timeout")
    except httpx.RequestError as e:
        # The exception text can carry the URL and connection details: only its class is logged.
        logger.error("AI upstream failure: category=transport_error exception=%s", type(e).__name__)
        raise AIError("OpenRouter API request failed")
    except json.JSONDecodeError:
        raise AIError("Failed to parse OpenRouter API response")


async def test_ai_connectivity(question: str = "What is 2+2?") -> dict:
    """
    Test AI connectivity with a simple question.

    Args:
        question: Question to ask the AI

    Returns:
        Response dict with answer and metadata
    """
    try:
        response = await call_ai(
            messages=[{"role": "user", "content": question}],
            temperature=0.7,
            max_tokens=200,
        )

        return {
            "success": True,
            "question": question,
            "answer": response["content"],
            "model": response["model"],
            "tokens_used": response["tokens_used"],
        }
    except AIError as e:
        return {
            "success": False,
            "error": str(e),
            "question": question,
        }
