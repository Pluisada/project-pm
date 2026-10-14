"""Shared FastAPI dependencies for authentication and authorization."""
import hmac
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from auth import decode_access_token, password_fingerprint
from database import get_db
from models import User, UserRole
from stage_history import SOURCE_API, set_stage_context


def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the authenticated user from the Authorization header.

    This is the single source of truth for "who is making this request" -
    every protected route, board-related or not, depends on it.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(parts[1])
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.username == payload["sub"]).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Tokens carry a fingerprint of the password they were issued under, so a
    # password change or reset ends every earlier session of this user only.
    # The claim is mandatory: a token without it is rejected.
    pwh = payload.get("pwh")
    if not isinstance(pwh, str) or not hmac.compare_digest(
        pwh, password_fingerprint(user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Stage changes in this request are attributed to this user, as an API
    # change unless the AI or undo path narrows the source.
    set_stage_context(db, user, SOURCE_API)
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Require the authenticated user to be an admin."""
    if current_user.role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user
