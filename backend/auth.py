"""Authentication module for PM backend."""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from pydantic import BaseModel


# Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours

# Hardcoded credentials for MVP
VALID_USERNAME = "user"
VALID_PASSWORD = "password"


class LoginRequest(BaseModel):
    """Login request payload."""

    username: str
    password: str


class LoginResponse(BaseModel):
    """Login response payload."""

    access_token: str
    token_type: str
    username: str


class TokenData(BaseModel):
    """Token payload data."""

    username: Optional[str] = None


def verify_credentials(username: str, password: str) -> bool:
    """Verify user credentials against hardcoded values."""
    return username == VALID_USERNAME and password == VALID_PASSWORD


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
        username: str = payload.get("sub")
        if username is None:
            return None
        return username
    except JWTError:
        return None
