"""Safeguards for any script that writes to a real database - the commercial one.

Every write to the commercial database (migrate_bid_cpm.py --apply, commercial_import.py) calls
validate_target() first. It never opens the file for writing and never creates one; it refuses, with
TargetRefused (a MigrationError, so the CLIs exit 1 with nothing written), when:
- the path isn't absolute, or contains a symlink, or isn't a regular single-link SQLite file;
- it is one of the development databases (backend/pm.db, backend/pm.db.bak, ./pm.db, ./pm.db.bak) by
  path, by inode or by file name;
- a -wal / -journal sidecar exists (an unfinished transaction: the state isn't what was approved);
- it isn't one of the explicitly allowed paths, or its SHA-256 isn't the approved one.
There is no DATABASE_URL or ./pm.db fallback anywhere in this module.

check_state() then checks, read-only, that the database is the expected one: integrity and foreign
keys clean, the Bid import schema in the expected state (cpm present or absent), the single expected
administrator and no other user, and optional table row counts.

open_readonly() / open_readwrite() use SQLite's mode=ro / mode=rw URIs, which can't create a file.
"""
import hashlib
import os
import sqlite3
import stat
from pathlib import Path

from import_schema import COMPLETE, CPM_MISSING, import_schema_state
from migrate_stage_kind import MigrationError

BACKEND_DIR = Path(__file__).resolve().parent
PROTECTED_NAMES = frozenset({"pm.db", "pm.db.bak"})
PROTECTED_PATHS = tuple(
    directory / name for directory in (BACKEND_DIR, BACKEND_DIR.parent) for name in sorted(PROTECTED_NAMES)
)
SQLITE_HEADER = b"SQLite format 3\x00"
SIDECAR_SUFFIXES = ("-wal", "-journal", "-shm")


class TargetRefused(MigrationError):
    """The target database isn't the approved, expected one. Nothing was written."""


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _uri(path: str, mode: str) -> str:
    return f"{Path(path).as_uri()}?mode={mode}"


def open_readonly(path: str) -> sqlite3.Connection:
    return sqlite3.connect(_uri(path, "ro"), uri=True)


def open_readwrite(path: str, **kwargs) -> sqlite3.Connection:
    """mode=rw: SQLite refuses to create a missing file."""
    return sqlite3.connect(_uri(path, "rw"), uri=True, **kwargs)


def validate_target(path, *, allowed=None, expected_sha256: str | None = None, protected=PROTECTED_PATHS) -> str:
    """The resolved path of a database that is safe to open for writing, or TargetRefused.

    allowed: when given, the only paths accepted (compared after resolving).
    expected_sha256: when given, the file must hash to it.
    """
    path = str(path)
    if not os.path.isabs(path):
        raise TargetRefused("The database path must be absolute")
    if os.path.realpath(path) != os.path.abspath(path):
        raise TargetRefused(f"{path} is, or sits behind, a symlink or a relative segment; pass the real path")
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise TargetRefused(f"No database file at {path} (a missing database is never created)") from None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise TargetRefused(f"{path} isn't a regular file with a single link")
    for protected_path in protected:
        if os.path.exists(protected_path) and os.path.samefile(protected_path, path):
            raise TargetRefused(f"{path} is a protected development database")
        if os.path.abspath(protected_path) == path:
            raise TargetRefused(f"{path} is a protected development database")
    if os.path.basename(path) in PROTECTED_NAMES:
        raise TargetRefused("Files named pm.db / pm.db.bak are never a target")
    with open(path, "rb") as handle:
        if handle.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            raise TargetRefused(f"{path} isn't a SQLite database")
    sidecars = [path + suffix for suffix in SIDECAR_SUFFIXES if os.path.exists(path + suffix)]
    if sidecars:
        raise TargetRefused(f"Unexpected state: {', '.join(sidecars)} exist (an unfinished transaction?)")
    if allowed is not None and path not in {os.path.realpath(p) for p in allowed}:
        raise TargetRefused(f"{path} isn't the explicitly approved database")
    if expected_sha256 is not None and sha256_file(path) != expected_sha256:
        raise TargetRefused("The database's SHA-256 differs from the approved one")
    return path


def check_state(path: str, *, expect_cpm: bool | None, admin_email: str | None, expected_counts: dict | None = None) -> dict:
    """Read-only identity/schema/state checks; TargetRefused on any difference. Returns the table counts."""
    conn = open_readonly(path)
    try:
        if [row[0] for row in conn.execute("PRAGMA integrity_check")] != ["ok"]:
            raise TargetRefused("integrity_check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise TargetRefused("foreign_key_check reports violations")
        if expect_cpm is not None:
            state, errors = import_schema_state(lambda sql: conn.execute(sql).fetchall())
            actual = "present" if state == COMPLETE else "absent" if errors == CPM_MISSING else None
            if actual is None:
                raise TargetRefused("The Bid import tables don't match the models in more than cpm: " + "; ".join(errors or [state]))
            if actual != ("present" if expect_cpm else "absent"):
                raise TargetRefused(f"card_bid_details.cpm is {actual}, expected {'present' if expect_cpm else 'absent'}")
        if admin_email is not None:
            users = conn.execute("SELECT username, email, role FROM users ORDER BY id").fetchall()
            if len(users) != 1 or users[0][2] != "admin" or admin_email.lower() not in (str(users[0][0]).lower(), str(users[0][1]).lower()):
                raise TargetRefused("The database must have exactly one user: the expected administrator")
        counts = {
            name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        }
    finally:
        conn.close()
    for table, expected in (expected_counts or {}).items():
        if counts.get(table) != expected:
            raise TargetRefused(f"{table} has {counts.get(table)} row(s), expected {expected}")
    return counts


def logical_dump(path: str) -> str:
    """Schema and every row in a stable order: the 'same logical content' comparison."""
    conn = open_readonly(path)
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def _verified_copy(source: str, dest: str) -> dict:
    if os.path.exists(dest):
        raise TargetRefused(f"{dest} already exists; never overwritten")
    src, dst = open_readonly(source), sqlite3.connect(dest)
    try:
        with dst:
            src.backup(dst)  # SQLite's online backup API: a consistent snapshot
    finally:
        src.close()
        dst.close()
    state = {"path": dest, "sha256": sha256_file(dest), "logical_dump_equal": logical_dump(source) == logical_dump(dest)}
    check_state(dest, expect_cpm=None, admin_email=None)
    if not state["logical_dump_equal"]:
        raise TargetRefused(f"{dest} differs logically from {source}")
    return state


def make_backup(source: str, dest: str) -> dict:
    """Consistent backup of source into a new file, verified (integrity, foreign keys, logical equality)."""
    return _verified_copy(source, dest)


def restore_backup(backup: str, dest: str) -> dict:
    """Restore a backup into a NEW, separate file and verify it - never over an existing file."""
    return _verified_copy(backup, dest)
