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
_test_db_dir = tempfile.mkdtemp(prefix="pm-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_test_db_dir, 'test.db')}"
