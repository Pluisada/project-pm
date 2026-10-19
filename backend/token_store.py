"""Persistent, single-use, purpose-scoped emailed tokens (signup and password reset).

The raw token is random (256 bits) and exists only in the email; the database
holds its SHA-256. Consumption is a single conditional UPDATE, so two
simultaneous requests cannot both succeed. `consume` does not commit: the caller
applies its change (create the user, set the password) in the same transaction,
so a failure rolls the consumption back too.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from models import AuthToken

PURPOSE_SIGNUP = "signup"
PURPOSE_RESET = "reset"


def _now() -> datetime:
    # Naive UTC: SQLite DateTime columns store no timezone.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue(
    db: Session, purpose: str, ttl: timedelta, *, user_id: int | None = None, email: str | None = None
) -> str:
    """Create a token for a user (reset) or a pending email (signup); return the raw value.

    Earlier unused tokens for the same purpose and subject are invalidated, so
    only the newest link works. Caller commits.
    """
    now = _now()
    previous = update(AuthToken).where(
        AuthToken.purpose == purpose,
        AuthToken.consumed_at.is_(None),
        AuthToken.invalidated_at.is_(None),
    )
    previous = previous.where(AuthToken.user_id == user_id) if user_id is not None else previous.where(AuthToken.email == email)
    db.execute(previous.values(invalidated_at=now))

    raw = secrets.token_urlsafe(32)
    db.add(AuthToken(
        token_hash=hash_token(raw), purpose=purpose, user_id=user_id, email=email,
        created_at=now, expires_at=now + ttl,
    ))
    return raw


def consume(db: Session, raw: str, purpose: str) -> AuthToken | None:
    """Atomically mark a valid token used and return it, or None.

    None covers every failure alike (unknown, wrong purpose, expired, already
    used, invalidated). Caller commits or rolls back.
    """
    now = _now()
    token_hash = hash_token(raw)
    claimed = db.execute(
        update(AuthToken)
        .where(
            AuthToken.token_hash == token_hash,
            AuthToken.purpose == purpose,
            AuthToken.consumed_at.is_(None),
            AuthToken.invalidated_at.is_(None),
            AuthToken.expires_at > now,
        )
        .values(consumed_at=now)
    )
    if claimed.rowcount != 1:
        return None
    return db.query(AuthToken).filter(AuthToken.token_hash == token_hash).one()


def invalidate_for_user(db: Session, user_id: int) -> None:
    """Revoke every unused token of a user (e.g. after a password change). Caller commits."""
    db.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user_id, AuthToken.consumed_at.is_(None), AuthToken.invalidated_at.is_(None))
        .values(invalidated_at=_now())
    )
