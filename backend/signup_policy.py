"""Who may self-sign-up: SIGNUP_ALLOWED_DOMAINS plus SIGNUP_ALLOWED_EMAILS.

Fail closed: with both settings unset or empty, public signup is disabled, so a
deployment never accepts arbitrary addresses by omission. Domain matching is exact;
"example.com" does not allow "sub.example.com" or "evil-example.com". Individual
exceptions are whole addresses (SIGNUP_ALLOWED_EMAILS) and never open their domain.
The setting is read on each use (like rate_limit.py) and validated at startup.
"""
import logging
import os
import re

ENV_VAR = "SIGNUP_ALLOWED_DOMAINS"
EMAILS_ENV_VAR = "SIGNUP_ALLOWED_EMAILS"
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class InvalidSignupDomainsError(RuntimeError):
    """SIGNUP_ALLOWED_DOMAINS has a malformed entry; the app refuses to start."""


def normalize_domain(domain: str) -> str | None:
    """Lowercase ASCII (punycode) form of a plain domain name, or None if invalid.

    Rejects '@', wildcards, ports, whitespace inside the name, empty labels and
    single-label names (no TLD-only entries such as "com").
    """
    domain = domain.strip().lower()
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = domain.split(".")
    if len(domain) > 253 or len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        return None
    return domain


def allowed_domains() -> frozenset[str]:
    """The configured domains; empty (signup closed) when unset. Raises on a bad entry."""
    raw = os.getenv(ENV_VAR, "")
    domains = set()
    for entry in raw.split(","):
        if not entry.strip():
            continue
        domain = normalize_domain(entry)
        if domain is None:
            raise InvalidSignupDomainsError(
                f"{ENV_VAR} has an invalid entry {entry.strip()!r}: use a comma-separated "
                "list of plain domains such as 'salcomp.com' (no '@', wildcards or subdomain patterns)"
            )
        domains.add(domain)
    return frozenset(domains)


def normalize_email(email: str) -> str | None:
    """Lowercase address with an IDNA-normalized domain, or None if malformed.

    No provider-specific rewriting: dots and '+tags' are significant.
    """
    local, sep, domain = email.strip().rpartition("@")
    normalized = normalize_domain(domain) if sep else None
    if normalized is None or not local or any(c.isspace() or c in "@,;<>*" for c in local):
        return None
    return f"{local.lower()}@{normalized}"


def allowed_emails() -> frozenset[str]:
    """The individually allowed addresses; empty when unset. Raises on a bad entry."""
    emails = set()
    for entry in os.getenv(EMAILS_ENV_VAR, "").split(","):
        if not entry.strip():
            continue
        email = normalize_email(entry)
        if email is None:
            raise InvalidSignupDomainsError(
                f"{EMAILS_ENV_VAR} has an invalid entry {entry.strip()!r}: use a comma-separated "
                "list of full addresses such as 'person@example.com'"
            )
        emails.add(email)
    return frozenset(emails)


def validate_config() -> None:
    """Fail at startup on a malformed setting; warn when signup is closed."""
    if not (allowed_domains() | allowed_emails()):
        logging.getLogger(__name__).warning(
            "%s and %s are not set: public signup is disabled", ENV_VAR, EMAILS_ENV_VAR
        )


def is_email_allowed(email: str) -> bool:
    """True if the email's domain is exactly a configured domain, or the address is listed."""
    normalized = normalize_email(email)
    if normalized is None:
        return False
    return normalized.rpartition("@")[2] in allowed_domains() or normalized in allowed_emails()
