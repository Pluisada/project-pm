"""Size limits for the AI chat endpoint (POST /api/boards/{id}/ai).

Every chat message is a paid OpenRouter call, so the request body and the
message are bounded in the backend; the frontend never has to be trusted.
Limits come from the environment on each use and are checked at startup by
`validate_config()`. Frequency limits live in `rate_limit.py`.
"""
import math
import os
import re

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_MESSAGE_CHARS_VAR = "AI_MAX_MESSAGE_CHARS"
MAX_REQUEST_BYTES_VAR = "AI_MAX_REQUEST_BYTES"
DEFAULT_MAX_MESSAGE_CHARS = 4000
# Room for 4000 characters even when JSON escapes each as \uXXXX (24 KB).
DEFAULT_MAX_REQUEST_BYTES = 32768

TOO_LARGE = "Request too large."
INVALID_REQUEST = "Invalid request."
_CHAT_PATH = re.compile(r"^/api/boards/[^/]+/ai/?$")


def _positive_int(var: str, default: int) -> int:
    raw = os.getenv(var, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        if value < 1:
            raise ValueError
    except ValueError:
        raise ValueError(f"{var} must be a positive integer, got {raw!r}") from None
    return value


def max_message_chars() -> int:
    return _positive_int(MAX_MESSAGE_CHARS_VAR, DEFAULT_MAX_MESSAGE_CHARS)


def max_request_bytes() -> int:
    return _positive_int(MAX_REQUEST_BYTES_VAR, DEFAULT_MAX_REQUEST_BYTES)


def validate_config() -> None:
    """Fail at startup on a malformed setting instead of on the first request."""
    max_message_chars()
    max_request_bytes()


def _non_finite(value) -> bool:
    # JSONResponse refuses inf/nan, so an echoed one would turn a 422 into a 500.
    return isinstance(value, float) and not math.isfinite(value)


async def chat_validation_handler(request: Request, exc: RequestValidationError):
    """FastAPI's default 422 echoes the rejected input and parser details back.
    On the chat path, answer with a fixed message instead; other routes keep
    the default, except that the rejected `input` is dropped from errors on
    password and token fields so a secret is never echoed back, and when it is
    a non-finite number, which JSON cannot carry."""
    if request.method == "POST" and _CHAT_PATH.match(request.url.path):
        return JSONResponse({"detail": INVALID_REQUEST}, status_code=422)
    errors = [
        {k: v for k, v in err.items() if k not in ("input", "ctx")}
        if any(part in ("password", "token") for part in err.get("loc", ()))
        or _non_finite(err.get("input"))
        else err
        for err in exc.errors()
    ]
    return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)


class ChatBodyLimitMiddleware:
    """Answers 413 to a chat POST whose body exceeds AI_MAX_REQUEST_BYTES.

    The declared Content-Length is checked first; a body without one (chunked)
    is read up to the limit and then replayed to the app, so the cap holds
    either way and nothing larger is ever parsed.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or not _CHAT_PATH.match(scope["path"]):
            await self.app(scope, receive, send)
            return

        limit = max_request_bytes()
        declared = dict(scope["headers"]).get(b"content-length")
        too_large = False
        if declared is not None:
            try:
                too_large = int(declared) > limit
            except ValueError:
                too_large = True
        chunks: list[Message] = []
        if not too_large:
            total = 0
            while True:
                message = await receive()
                if message["type"] != "http.request":  # client disconnected
                    return
                chunks.append(message)
                total += len(message.get("body", b""))
                if total > limit:
                    too_large = True
                    break
                if not message.get("more_body", False):
                    break
        if too_large:
            response = JSONResponse({"detail": TOO_LARGE}, status_code=413)
            await response(scope, receive, send)
            return

        replay = iter(chunks)

        async def replay_receive() -> Message:
            return next(replay, None) or await receive()

        await self.app(scope, replay_receive, send)
