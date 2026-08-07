"""AI integration with OpenRouter."""
import os
import json
from typing import Optional

import httpx

# Configuration
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MODEL_ID = "openai/gpt-oss-120b"
REQUEST_TIMEOUT = 30.0


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
                error_detail = response.text
                try:
                    error_data = response.json()
                    error_detail = error_data.get("error", {}).get("message", error_detail)
                except Exception:
                    pass

                raise AIError(
                    f"OpenRouter API error {response.status_code}: {error_detail}"
                )

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

    except httpx.TimeoutException:
        raise AIError("OpenRouter API request timeout")
    except httpx.RequestError as e:
        raise AIError(f"OpenRouter API request failed: {str(e)}")
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
