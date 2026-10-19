"""CORS origins and security response headers (docs/SECURITY.md section 9).

In production the FastAPI app serves the frontend and the API from one origin,
so no CORS permission is needed at all. CORS exists only for development, where
`next dev` (:3000) calls the API on :8000. Origins are explicit and configurable.
"""

import os
from urllib.parse import urlsplit

import production_config
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORS_ENV = "CORS_ALLOWED_ORIGINS"
HSTS_ENV = "HSTS_ENABLED"
HSTS_MAX_AGE = 31536000  # one year

# Used only when CORS_ALLOWED_ORIGINS is unset: the local development frontend.
DEV_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]

# Methods and headers the frontend actually uses (Bearer token, JSON bodies).
CORS_METHODS = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
CORS_HEADERS = ["Authorization", "Content-Type", "X-Setup-Token"]

# The static export inlines its bootstrap scripts and React/dnd-kit set inline
# styles, so script-src/style-src need 'unsafe-inline'. Everything else is
# locked to the same origin (fonts are self-hosted by next/font at build time).
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

# Swagger/ReDoc load their assets from a CDN; the CSP would blank them.
_CSP_EXEMPT_PREFIXES = ("/docs", "/redoc")


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def cors_origins() -> list[str]:
    """Allowed CORS origins from CORS_ALLOWED_ORIGINS (comma-separated).

    Unset -> the local development origins. Set but empty -> none (same-origin
    only). Anything but a bare scheme://host[:port] origin stops the app.
    """
    raw = os.getenv(CORS_ENV)
    if raw is None:
        # Production is same-origin: never fall back to the localhost frontend.
        return [] if production_config.is_production() else list(DEV_ORIGINS)
    origins = []
    for item in raw.split(","):
        item = item.strip().rstrip("/")
        if not item:
            continue
        parts = urlsplit(item)
        if (
            "*" in item
            or parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.path
            or parts.query
            or parts.fragment
            or parts.username
        ):
            raise RuntimeError(
                f"{CORS_ENV} entries must be explicit origins like https://pm.example.com "
                f"(no '*', path or credentials); got {item!r}"
            )
        origins.append(f"{parts.scheme}://{parts.netloc}".lower())
    return origins


def hsts_enabled() -> bool:
    return _truthy(os.getenv(HSTS_ENV))


def validate_config() -> None:
    cors_origins()


class SecurityHeadersMiddleware:
    """Adds the security headers to every HTTP response (API, static, errors).

    Headers the app already set are left alone. HSTS is sent only when
    HSTS_ENABLED is true, which belongs on a deployment served over HTTPS only
    (usually the reverse proxy sets it instead, see docs/SECURITY.md).
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        headers = {
            b"x-content-type-options": b"nosniff",
            b"x-frame-options": b"DENY",
            # Reset and signup links carry a token in the query string.
            b"referrer-policy": b"no-referrer",
            b"permissions-policy": b"camera=(), microphone=(), geolocation=(), payment=()",
            b"cross-origin-opener-policy": b"same-origin",
        }
        if not path.startswith(_CSP_EXEMPT_PREFIXES):
            headers[b"content-security-policy"] = CSP.encode()
        if hsts_enabled():
            headers[b"strict-transport-security"] = f"max-age={HSTS_MAX_AGE}".encode()

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {name.lower() for name, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    (name, value) for name, value in headers.items() if name not in existing
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)
