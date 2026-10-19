"""Create a new, empty central identity database at an explicit path.

    uv run python create_identity_db.py --database /path/to/pm-identity.db

The path must not exist (nothing is overwritten or reused) and its directory
must already exist: a missing directory is far more likely a typo than an
intent, and creating parents could scatter a database in the wrong place. The
path is never taken from DATABASE_URL or IDENTITY_DATABASE_URL. Refused: the
development databases, any file named like the commercial database, and the
path DATABASE_URL names, and any path configured for a company
(PM_COMPANY_DB_*), whether or not that file exists. Any name ending in -wal,
-shm or -journal is refused whatever its base name, and names are compared
case-insensitively. The checks are database_targets', so they match the ones
applied when the database is opened; paths are only resolved and stat'ed, no
configured database is opened.
No users or companies are created here and nothing secret is printed.
"""
import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError

from database_targets import (  # noqa: F401
    BACKEND_DIR,
    PROTECTED_NAMES,
    PROTECTED_PATHS,
    SIDECAR_SUFFIXES,
    DatabaseTargetError,
    check_not_protected,
    has_parent_segment,
    commercial_database_path,
    company_database_paths,
    same_file,
)
from identity_database import create_identity_schema

class IdentityDatabaseError(Exception):
    """A refused operation; the message is safe to show."""


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def check_target(path: str) -> Path:
    """Return the validated, symlink-resolved path for a new identity database, or refuse."""
    if not path or path == ":memory:" or "://" in path or path.startswith("sqlite:"):
        raise IdentityDatabaseError("--database must be a file path, not a URL or :memory:")
    if "\x00" in path:
        raise IdentityDatabaseError("--database is not a valid path")
    if has_parent_segment(path):
        raise IdentityDatabaseError("--database must not contain '..' path segments (the file selected would be ambiguous)")
    target = Path(path).expanduser().absolute()
    parent = _real(target.parent)
    resolved = parent / target.name
    try:
        check_not_protected(resolved, "--database")
        taken = list(company_database_paths(check_identity=False).values())
        commercial = commercial_database_path()
    except DatabaseTargetError as exc:  # includes an unsupported or invalid DATABASE_URL: fail closed
        raise IdentityDatabaseError(f"Refusing a development or commercial database path ({exc})") from None
    if commercial is not None:
        taken.append(commercial)
    if any(same_file(resolved, other) for other in taken):
        raise IdentityDatabaseError("Refusing a development or commercial database path (assigned to a configured database)")
    if os.path.lexists(resolved):
        raise IdentityDatabaseError(f"{resolved} already exists; refusing to overwrite or reuse it")
    if not parent.is_dir():
        raise IdentityDatabaseError(f"Directory {target.parent} does not exist")
    return resolved  # the validated physical path: it is what gets created and what the engine opens


def _warn_leftover(reason: str) -> None:
    print(f"Warning: {reason}; an empty leftover file may remain at the requested path.", file=sys.stderr)


def _remove_created_file(target: Path, created: tuple[int, int]) -> None:
    """Unlink target only if it is still the file this invocation created.

    The device and inode recorded at creation are compared with os.lstat() (which
    does not follow a symlink) immediately before unlinking. This narrows the race
    window but cannot close it: an adversary who can write to the parent directory
    can still swap the path between the check and the unlink. It does not protect
    against process termination or machine failure either.
    """
    try:
        current = os.lstat(target)
    except FileNotFoundError:
        return  # nothing left at the path
    except OSError:
        _warn_leftover("could not inspect the file for cleanup")
        return
    if (current.st_dev, current.st_ino) != created:
        _warn_leftover("the path no longer refers to the file this command created, so it was not removed")
        return
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        _warn_leftover("could not remove the file after a failure")


def create_identity_database(path: str) -> Path:
    """Create the file exclusively, then the schema.

    If engine creation or the schema step fails, the file this call created is
    removed, but only if the path still has the device and inode recorded at
    creation (see _remove_created_file); a path that existed beforehand is never
    touched. A killed process or a machine failure cannot clean up.
    """
    target = check_target(path)
    try:
        # O_EXCL closes the race between the existence check and creation.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise IdentityDatabaseError(f"{target} already exists; refusing to overwrite or reuse it") from None
    try:
        info = os.fstat(fd)
    except BaseException:
        _warn_leftover("could not identify the newly created file")
        raise
    finally:
        os.close(fd)
    created = (info.st_dev, info.st_ino)
    engine = None
    try:
        # URL.create keeps spaces, '?' and '#' in the path from being parsed as URL syntax.
        engine = create_engine(URL.create("sqlite", database=str(target)))

        @event.listens_for(engine, "connect")
        def _foreign_keys_on(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

        create_identity_schema(engine)
        engine.dispose()
    except BaseException:
        try:
            if engine is not None:
                engine.dispose()
        except Exception:
            print("Warning: could not release the database connection pool.", file=sys.stderr)
        finally:
            _remove_created_file(target, created)
        raise
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create the central identity database.")
    parser.add_argument("--database", required=True, help="path of the new database file (must not exist)")
    args = parser.parse_args(argv)
    try:
        target = create_identity_database(args.database)
    except IdentityDatabaseError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Error: could not create the database: {exc.strerror or type(exc).__name__}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"Error: could not create the schema ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"Created empty identity database {target} (no users, no companies).")
    if not set(str(target)) & set("?#%"):
        print(f"Run the app on it with IDENTITY_DATABASE_URL=sqlite:///{target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
