"""Authentication module for PM backend."""
import base64
import functools
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from pydantic import BaseModel


# Values that must never sign tokens: the old hardcoded fallback (public in
# this repository's history) and the .env.example placeholder.
INSECURE_SECRET_KEYS = {
    "dev-secret-key-change-in-production",
    "replace-with-a-long-random-value",
}
MIN_SECRET_KEY_LENGTH = 32


class InsecureSecretKeyError(RuntimeError):
    """SECRET_KEY is missing or unsafe; the app refuses to start."""


def load_secret_key() -> str:
    """Read SECRET_KEY from the environment, refusing missing or unsafe values.

    Every access token is signed with it, so a
    guessable key lets anyone forge a login as any user. There is deliberately
    no fallback. The value itself is never included in the error.
    """
    key = os.getenv("SECRET_KEY", "").strip()
    if not key:
        problem = "SECRET_KEY is not set"
    elif key in INSECURE_SECRET_KEYS:
        problem = "SECRET_KEY is a known insecure default"
    elif len(key) < MIN_SECRET_KEY_LENGTH:
        problem = f"SECRET_KEY is shorter than {MIN_SECRET_KEY_LENGTH} characters"
    else:
        return key
    raise InsecureSecretKeyError(
        f"{problem}. Set a long random value in the environment (e.g. in .env); "
        "see docs/SECURITY.md for how to generate one without printing it."
    )


# Configuration - fails at import, so neither the app nor any script that
# uses auth can start with an unsafe key.
SECRET_KEY = load_secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours: the maximum session length
SIGNUP_TOKEN_EXPIRE_HOURS = 24
RESET_TOKEN_EXPIRE_MINUTES = 60
# password_hash of an account with no usable password: not a bcrypt hash, so
# verify_password always fails. Such an account can only be entered via a reset link.
UNUSABLE_PASSWORD_HASH = "!"
# Created by the Playwright suite with a password that is public in the repository.
E2E_ONLY_USERNAMES = frozenset({"e2e_admin", "e2e_member"})
# Where links in outgoing emails point: the Next dev server locally, the
# FastAPI-served static export in Docker.
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:3000")


class LoginRequest(BaseModel):
    """Login request payload."""

    username: str
    password: str


class LoginResponse(BaseModel):
    """Login response payload."""

    access_token: str
    token_type: str
    username: str
    role: str


class TokenData(BaseModel):
    """Token payload data."""

    username: Optional[str] = None


def _prepare_password(password: str) -> bytes:
    """Pre-hash with SHA-256 (base64-encoded) before handing off to bcrypt.

    bcrypt only looks at the first 72 bytes of its input and silently
    truncates anything past that - a long or unicode-heavy password would
    otherwise be hashed on a prefix, quietly hashing less entropy than the
    schema's max_length promises. Pre-hashing gives bcrypt a fixed 44-byte
    input regardless of the original password's length/encoding.
    """
    return base64.b64encode(hashlib.sha256(password.encode()).digest())


def hash_password(password: str) -> str:
    """Hash a plaintext password for storage."""
    return bcrypt.hashpw(_prepare_password(password), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored hash."""
    try:
        return bcrypt.checkpw(_prepare_password(password), password_hash.encode())
    except ValueError:
        # Malformed/non-bcrypt hash - treat as a failed login, not a crash.
        return False


@functools.lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    # A real bcrypt hash at the production cost, built once on first use.
    return hash_password(secrets.token_urlsafe(16))


def verify_login_password(password: str, password_hash: Optional[str]) -> bool:
    """Password check for login that costs one bcrypt verification in every case.

    An unknown account (`password_hash` None) or one with no usable password
    (UNUSABLE_PASSWORD_HASH) is checked against a dummy hash, so response time
    does not reveal which usernames exist or which accounts are not activated.
    The dummy can never match: its password is discarded.
    """
    if not password_hash or password_hash == UNUSABLE_PASSWORD_HASH:
        verify_password(password, _dummy_password_hash())
        return False
    return verify_password(password, password_hash)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=ACCESS_TOKEN_EXPIRE_MINUTES
        )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """Return an access token's payload, or None if invalid, expired or purpose-scoped."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    # Anything carrying a `purpose` claim is not an access token.
    if "purpose" in payload or payload.get("sub") is None:
        return None
    return payload


def verify_token(token: str) -> Optional[str]:
    """Verify JWT token and return username if valid."""
    payload = decode_access_token(token)
    return payload["sub"] if payload else None


def create_purpose_token(sub: str, purpose: str, expires: timedelta, **claims) -> str:
    """Create a signed, expiring token for a single non-session purpose (e.g. the
    Bid import preview token). Signup and reset links are NOT these: they are
    persistent, hashed and single-use (token_store.py).

    The `purpose` claim is what keeps these out of `verify_token`, so they can
    never be used as access tokens.
    """
    payload = {"sub": sub, "purpose": purpose, "exp": datetime.now(timezone.utc) + expires}
    return jwt.encode({**payload, **claims}, SECRET_KEY, algorithm=ALGORITHM)


def decode_purpose_token(token: str, purpose: str) -> Optional[dict]:
    """Return a purpose token's payload, or None if invalid, expired or for another purpose."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if payload.get("purpose") != purpose or not payload.get("sub"):
        return None
    return payload


def create_user_access_token(user, expires_delta: Optional[timedelta] = None) -> str:
    """Access token for `user`, bound to their current password (`pwh`) and
    session version (`sv`). Either changing ends the session."""
    return create_access_token(
        {
            "sub": user.username,
            "pwh": password_fingerprint(user.password_hash),
            "sv": user.session_version,
        },
        expires_delta,
    )


def password_fingerprint(password_hash: str) -> str:
    """Short digest of the current password hash, embedded in access tokens (`pwh`),
    so changing the password ends every earlier session."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]
