"""Startup checks that apply only when PM_ENV=production.

Production is an explicit opt-in, so local development and the isolated E2E
run (PM_E2E=1) behave as before. In production the public URL that goes into
signup/reset emails must be an HTTPS origin that is not local, and the
settings that can contradict it must agree, and SMTP must be structurally
usable (checked without connecting). All problems are reported at once
and stop the app before it serves anything.
"""
import ipaddress
import os
import posixpath
from email.utils import parseaddr
from urllib.parse import unquote, urlsplit

ENV_VAR = "PM_ENV"
ENV_VALUES = {"production", "development"}


def is_production() -> bool:
    """True for PM_ENV=production. Unset or empty is development; any other value is an error.

    Raising here (not only in validate_config) matters because main.py calls
    docs_kwargs() while building the app, before any startup validation, so a
    typo such as "prod" can never silently skip the production checks.
    """
    value = os.getenv(ENV_VAR, "").strip().lower()
    if value and value not in ENV_VALUES:
        raise RuntimeError(f"{ENV_VAR} must be 'production' or 'development' (or unset), got {value!r}")
    return value == "production"


PROXY_VAR = "PM_BEHIND_PROXY"
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def behind_proxy() -> bool:
    """True when the operator explicitly declares a reverse proxy in front of the app.

    Unset or empty means no proxy; anything that is not a recognised true or
    false word is an error rather than a silent "no proxy".
    """
    value = os.getenv(PROXY_VAR, "").strip().lower()
    if value in _TRUE:
        return True
    if value and value not in _FALSE:
        raise RuntimeError(f"{PROXY_VAR} must be one of {sorted(_TRUE | _FALSE)} (or unset), got {value!r}")
    return False


def docs_kwargs() -> dict:
    """FastAPI arguments that switch off /docs, /redoc and /openapi.json in production."""
    if is_production():
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {}


def _is_local_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def https_origin_problem(value: str, name: str) -> str | None:
    """Why `value` is not a usable public HTTPS origin (scheme://host[:port]), or None."""
    parts = urlsplit(value)
    if parts.scheme != "https":
        return f"{name} must use https (got {parts.scheme or 'no scheme'!r})"
    try:
        host, _ = parts.hostname, parts.port
    except ValueError:
        return f"{name} has an invalid port"
    if not host:
        return f"{name} has no host"
    if _is_local_host(host):
        return f"{name} must not point at localhost or a loopback address"
    if parts.path or parts.query or parts.fragment or parts.username or parts.password:
        return f"{name} must be a bare origin like https://pm.example.com (no path, query or credentials)"
    return None


def smtp_problems() -> list[str]:
    """Structural SMTP checks matching what mailer.send_email reads. No network access.

    Credentials stay optional (an unauthenticated relay is legitimate), but a
    username without a password would only fail at the first email.
    """
    found = []
    if not os.getenv("SMTP_HOST", "").strip():
        found.append("SMTP_HOST is not set; signup and password-reset emails could not be sent")

    port = os.getenv("SMTP_PORT")  # unset means the mailer's default (587)
    if port is not None:
        try:
            valid_port = 1 <= int(port) <= 65535
        except ValueError:
            valid_port = False
        if not valid_port:
            found.append("SMTP_PORT must be an integer between 1 and 65535")

    sender = (os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "").strip()
    if not sender:
        found.append("SMTP_FROM (or SMTP_USER) must provide the sender address")
    else:
        _, address = parseaddr(sender)
        local, at, domain = address.rpartition("@")
        if not (local and at and "." in domain) or any(c.isspace() for c in address) or address.count("@") != 1:
            found.append("SMTP_FROM (or SMTP_USER) is not a valid email address")

    if os.getenv("SMTP_USER", "").strip() and not os.getenv("SMTP_PASSWORD"):
        found.append("SMTP_USER is set but SMTP_PASSWORD is empty")
    return found


def _sqlite_file_name(url: str) -> str:
    """Lower-cased file name of an absolute SQLite URL, after normalising the path.

    Pure string handling: nothing is opened or resolved on disk. Normalising
    collapses "a/./b", "a//b" and "a/x/../b"; the name is lower-cased because
    the development file must be refused on case-insensitive filesystems too.
    """
    path = posixpath.normpath(unquote(url.split("?", 1)[0].removeprefix("sqlite://")))
    return posixpath.basename(path).lower()


def database_problems() -> list[str]:
    """Production must name an existing database explicitly, never fall back to the development one.

    The default URL (sqlite:///./pm.db) is relative and every mount of the
    development file ends in pm.db, so both are refused here. The database
    itself is checked read-only at init_db() because PM_REQUIRE_EXISTING_DB=1.
    """
    found = []
    url = os.getenv("DATABASE_URL", "").strip()
    if not url.startswith("sqlite:////"):
        found.append("DATABASE_URL must be an absolute SQLite file URL (sqlite:////path/to/file.db, four slashes); in Docker set PM_DATABASE_URL")
    elif _sqlite_file_name(url) == "pm.db":
        found.append("DATABASE_URL names pm.db, the development database file; use the commercial database")
    if os.getenv("PM_REQUIRE_EXISTING_DB") != "1":
        found.append("PM_REQUIRE_EXISTING_DB=1 is required so a wrong path can never create an empty database")
    return found


def problems() -> list[str]:
    if not is_production():
        behind_proxy()  # an invalid declaration is an error in every environment
        return []
    found = []
    if os.getenv("PM_E2E") == "1":
        found.append("PM_E2E=1 (the throwaway test mode) must not be set in production")

    base = os.getenv("APP_BASE_URL", "").strip()
    if not base:
        found.append("APP_BASE_URL (the public https URL used in emailed links) is not set")
    elif (problem := https_origin_problem(base, "APP_BASE_URL")):
        found.append(problem)
    public = os.getenv("PM_PUBLIC_URL", "").strip()
    if public and base and public.rstrip("/").lower() != base.rstrip("/").lower():
        found.append("PM_PUBLIC_URL and APP_BASE_URL are both set and differ; set one value (docker-compose maps PM_PUBLIC_URL to APP_BASE_URL)")

    found.extend(database_problems())
    found.extend(smtp_problems())

    # Imported here to avoid a circular import (http_security uses is_production).
    from http_security import cors_origins

    for origin in cors_origins():
        if (problem := https_origin_problem(origin, f"CORS_ALLOWED_ORIGINS entry {origin!r}")):
            found.append(problem)

    # Malformed or catch-all trusted proxies are rejected by trusted_proxies().
    # Production is not required to use a proxy, but a deployment that declares
    # one (PM_BEHIND_PROXY) must say which addresses to trust; otherwise every
    # user would share the proxy's address in the IP limits.
    from rate_limit import trusted_proxies

    try:
        proxies = trusted_proxies()
    except ValueError as exc:
        found.append(str(exc))
    else:
        if behind_proxy() and not proxies:
            found.append(f"{PROXY_VAR} is set but RATE_LIMIT_TRUSTED_PROXIES is empty; list the proxy address(es), or unset {PROXY_VAR} for direct access")
    return found


def validate_config() -> None:
    found = problems()
    if found:
        raise RuntimeError("Invalid production configuration:\n- " + "\n- ".join(found))
