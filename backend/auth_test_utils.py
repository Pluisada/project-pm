"""Shared test helpers: accounts created directly in the database must be marked
email-verified, because the application grants no access without it."""
from datetime import datetime

VERIFIED = datetime(2026, 1, 1)


def create_signup_token(db, email, ttl=None):
    """A raw, valid signup token for `email`, stored (hashed) in `db`."""
    from datetime import timedelta

    import token_store

    raw = token_store.issue(db, token_store.PURPOSE_SIGNUP, ttl or timedelta(hours=24), email=email)
    db.commit()
    return raw


def create_reset_token(db, user, ttl=None):
    """A raw, valid reset token for `user`, stored (hashed) in `db`."""
    from datetime import timedelta

    import token_store

    raw = token_store.issue(db, token_store.PURPOSE_RESET, ttl or timedelta(hours=1), user_id=user.id)
    db.commit()
    return raw


# /api/setup is closed unless PM_SETUP_TOKEN is set (conftest sets it per run).
SETUP_TOKEN = "t" * 8 + "setup-key-for-tests-only-0123456789"
SETUP_HEADERS = {"X-Setup-Token": SETUP_TOKEN}


def add_member(client, username, password):
    """Create a verified member straight in the test database. The admin API no
    longer takes a password (invitees set it from an emailed link)."""
    from auth import hash_password
    from database import get_db
    from main import app
    from models import User, UserRole

    db = next(app.dependency_overrides[get_db]())
    db.add(User(username=username, password_hash=hash_password(password), role=UserRole.MEMBER.value, email_verified_at=VERIFIED))
    db.commit()
