"""Test-session environment, set before any test module imports the app.

- SECRET_KEY: auth.py refuses to import without a safe key. Tests get a fresh
  random one per run, so no key is ever stored in the repository.
- DATABASE_URL: points the module-level engine at a throwaway file, so a test
  that forgets to override get_db (or runs startup events) can never touch the
  development database (backend/pm.db, the default for a cwd of backend/).
  Overridden unconditionally, even if the shell has its own DATABASE_URL.
"""
import os
import secrets
import tempfile

os.environ["SECRET_KEY"] = secrets.token_urlsafe(48)
# Signup is closed unless domains are configured; most tests sign up @example.com.
os.environ["SIGNUP_ALLOWED_DOMAINS"] = "example.com,example.test"
_test_db_dir = tempfile.mkdtemp(prefix="pm-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_test_db_dir, 'test.db')}"
# The throwaway test.db does not exist yet; init_db refuses to create a database
# unless told to. The init-guard tests unset this.
os.environ["PM_ALLOW_CREATE_DB"] = "1"
# /api/setup is closed without a setup key; tests present this one.
from auth_test_utils import SETUP_TOKEN  # noqa: E402

os.environ["PM_SETUP_TOKEN"] = SETUP_TOKEN


import pytest


@pytest.fixture(autouse=True)
def _fresh_rate_limits(monkeypatch):
    """Each test starts with empty rate-limit counters and no proxy trust,
    whatever the shell exports; test_rate_limit.py sets its own limits."""
    import rate_limit

    for name in ("RATE_LIMIT_ENABLED", "RATE_LIMIT_TRUSTED_PROXIES", rate_limit.LOGIN_BACKOFF[0], "AI_MAX_MESSAGE_CHARS", "AI_MAX_REQUEST_BYTES", *(v for v, _ in rate_limit.DEFAULT_LIMITS.values())):
        monkeypatch.delenv(name, raising=False)
    rate_limit.limiter.reset()
    rate_limit.login_backoff.reset()
