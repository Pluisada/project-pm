"""Which sign-in architecture the process runs: the legacy single database or central identity.

PM_AUTH_MODE=legacy (default) keeps the existing application exactly as it is. PM_AUTH_MODE=central serves only
/api/central/*, /api/companies/* and the two unauthenticated probes (/api/mode, /api/health): every other /api/ path
(the legacy login, boards, users, imports, undo...) answers 404, so no legacy authorization path stays reachable, and
the legacy database is neither required nor opened at startup. Central mode refuses to start without
IDENTITY_DATABASE_URL and at least the company databases being configured explicitly (no fallback to DATABASE_URL).
"""
import os

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MODE_ENV = "PM_AUTH_MODE"
LEGACY, CENTRAL = "legacy", "central"
CENTRAL_PREFIXES = ("/api/central/", "/api/companies/")
CENTRAL_EXACT = frozenset({"/api/mode", "/api/health"})


class AuthModeError(RuntimeError):
    pass


def auth_mode(environ=None) -> str:
    environ = os.environ if environ is None else environ
    value = (environ.get(MODE_ENV) or LEGACY).strip().lower()
    if value not in (LEGACY, CENTRAL):
        raise AuthModeError(f"{MODE_ENV} must be '{LEGACY}' or '{CENTRAL}'")
    return value


def validate_config(environ=None) -> None:
    """Refuse to start a central deployment that is missing its explicit database configuration."""
    environ = os.environ if environ is None else environ
    if auth_mode(environ) != CENTRAL:
        return
    from database_targets import COMPANY_SLUGS, company_env_name

    missing = [name for name in ["IDENTITY_DATABASE_URL", *(company_env_name(s) for s in COMPANY_SLUGS)]
               if not (environ.get(name) or "").strip()]
    if missing:
        raise AuthModeError("central mode needs: " + ", ".join(missing))


class AuthModeMiddleware:
    """In central mode, hide every legacy /api/ endpoint."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and auth_mode() == CENTRAL:
            path = scope["path"]
            if path.startswith("/api/") and path not in CENTRAL_EXACT and not path.startswith(CENTRAL_PREFIXES):
                await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
                return
        await self.app(scope, receive, send)
