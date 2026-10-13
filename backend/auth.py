"""Authentication module for PM backend."""
import base64
import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from pydantic import BaseModel


# Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours
SIGNUP_TOKEN_EXPIRE_HOURS = 24
RESET_TOKEN_EXPIRE_MINUTES = 60
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


def verify_token(token: str) -> Optional[str]:
    """Verify JWT token and return username if valid."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        # Purpose-scoped tokens (e.g. signup links) share SECRET_KEY and a
        # `sub`, so they must never be accepted as access tokens.
        if "purpose" in payload:
            return None
        username: str = payload.get("sub")
        if username is None:
            return None
        return username
    except JWTError:
        return None


def create_purpose_token(sub: str, purpose: str, expires: timedelta, **claims) -> str:
    """Create a signed, expiring token for a single purpose (e.g. an email link).

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


def create_signup_token(email: str) -> str:
    """Create the token embedded in a signup email link."""
    return create_purpose_token(email, "signup", timedelta(hours=SIGNUP_TOKEN_EXPIRE_HOURS))


def verify_signup_token(token: str) -> Optional[str]:
    """Return the email a signup token was issued for, or None if invalid/expired."""
    payload = decode_purpose_token(token, "signup")
    return payload["sub"] if payload else None


def password_fingerprint(password_hash: str) -> str:
    """Short digest of the current password hash, embedded in reset tokens.

    Resetting the password changes the hash, which invalidates every reset
    link issued before it - that's what makes them single-use without
    storing anything.
    """
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]
