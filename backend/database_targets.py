"""Where the central identity database and the per-company databases live.

Pure configuration and path checks: nothing here opens, reads, creates or
stats the contents of a database. The only filesystem calls are lstat/stat on
paths (existence, file type, same-file identity), never open().

Company databases are mapped by stable slug through one environment variable
each (PM_COMPANY_DB_SALCOMP, PM_COMPANY_DB_LITY, PM_COMPANY_DB_VALOR_LOG), each
a SQLite file URL. There is no default and no fallback: DATABASE_URL is never
read as a company mapping, so the permanent Salcomp path must be configured
explicitly. An unset or unknown slug fails closed.

Accepted URL forms are plain filesystem paths only: sqlite:///relative.db,
sqlite:////absolute.db (. segments allowed) on the default driver
("sqlite" or "sqlite+pysqlite"). Everything the driver could interpret
differently from this module is refused: query parameters (uri=true, mode=,
...), file: URIs, ".." segments (symlink + ".." resolves differently in the
kernel/driver than lexically, so the file chosen would be ambiguous), a leading "~" (the driver does not expand it), other drivers,
and host/credential parts. Every company file is also refused if it is a
protected, development or sidecar path or the DATABASE_URL file; the Salcomp
company is not exempt, so the permanent commercial database cannot be mapped
until a separate decision allows it.
"""
import os
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

BACKEND_DIR = Path(__file__).resolve().parent
PROTECTED_NAMES = frozenset({"pm.db", "pm.db.bak", "pm-commercial.db"})  # lower case
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
PROTECTED_PATHS = (
    BACKEND_DIR / "pm.db",
    BACKEND_DIR.parent / "pm.db",
    Path.home() / "Library" / "Application Support" / "SalcompSalesPipeline" / "data" / "pm-commercial.db",
)
IDENTITY_DATABASE_URL_ENV = "IDENTITY_DATABASE_URL"
COMMERCIAL_DATABASE_URL_ENV = "DATABASE_URL"
COMPANY_SLUGS = ("salcomp", "lity", "valor-log")


class DatabaseTargetError(RuntimeError):
    """A refused database configuration; the message is safe to show."""


def company_env_name(slug: str) -> str:
    return "PM_COMPANY_DB_" + slug.upper().replace("-", "_")


_ALLOWED_DRIVERS = ("sqlite", "sqlite+pysqlite")


def has_parent_segment(path: str) -> bool:
    """True if a filesystem path has a '..' segment (either separator)."""
    return ".." in path.replace("\\", "/").split("/")


def sqlite_file_path(url: str, label: str) -> Path:
    """The absolute, symlink-resolved file behind a plain SQLite file URL, or refuse.

    Fail closed on any form whose physical target the sqlite3 driver could
    resolve differently from this function (see the module docstring).
    """
    try:
        parsed = make_url(url)
    except ArgumentError:
        raise DatabaseTargetError(f"{label} is not a valid database URL") from None
    if parsed.get_backend_name() != "sqlite" or parsed.database in (None, "", ":memory:"):
        raise DatabaseTargetError(f"{label} must be a SQLite file URL (not in-memory)")
    database = parsed.database
    if parsed.drivername not in _ALLOWED_DRIVERS:
        raise DatabaseTargetError(f"{label} must use the default SQLite driver")
    if parsed.host or parsed.port or parsed.username or parsed.password:
        raise DatabaseTargetError(f"{label} must not carry a host or credentials")
    if parsed.query or "?" in url:  # "sqlite:///a?b.db" parses to the path "a" with an empty query
        raise DatabaseTargetError(f"{label} must not carry query parameters (they can change the file opened)")
    if database.casefold().startswith("file:"):
        raise DatabaseTargetError(f"{label} must be a filesystem path, not a file: URI")
    if database.startswith("~"):
        raise DatabaseTargetError(f"{label} must not start with '~' (it is not expanded by the driver)")
    if "\x00" in database:
        raise DatabaseTargetError(f"{label} is not a valid path")
    if has_parent_segment(database):
        raise DatabaseTargetError(f"{label} must not contain '..' path segments (the file selected would be ambiguous)")
    return Path(os.path.realpath(Path(database).absolute()))


def _same_file(a: Path, b: Path) -> bool:
    if str(a).casefold() == str(b).casefold():
        return True
    try:
        return os.path.samefile(a, b)  # hard links and case-insensitive volumes
    except OSError:
        return False  # one of them does not exist


def same_file(a: Path, b: Path) -> bool:
    """True if two resolved paths name the same file (case-insensitive, hard links); opens nothing."""
    return _same_file(a, b)


def check_not_protected(path: Path, label: str) -> None:
    """Refuse a protected, development or sidecar path (a resolved path; nothing is opened)."""
    _check_not_protected(path, label)


def _check_not_protected(path: Path, label: str) -> None:
    name = path.name.casefold()
    if name.endswith(SIDECAR_SUFFIXES):
        raise DatabaseTargetError(f"{label} must not be a SQLite sidecar file")
    if name in PROTECTED_NAMES or any(_same_file(path, Path(os.path.realpath(p))) for p in PROTECTED_PATHS):
        raise DatabaseTargetError(f"{label} must not point at a development or commercial database")


def commercial_database_path(environ: Mapping[str, str] | None = None) -> Path | None:
    """The file DATABASE_URL names, or None if unset, non-SQLite or in-memory.

    A SQLite DATABASE_URL in an unsupported form is refused rather than guessed.
    """
    environ = os.environ if environ is None else environ
    url = (environ.get(COMMERCIAL_DATABASE_URL_ENV) or "").strip()
    if not url:
        return None
    try:
        parsed = make_url(url)
    except ArgumentError:
        raise DatabaseTargetError(f"{COMMERCIAL_DATABASE_URL_ENV} is not a valid database URL") from None
    if parsed.get_backend_name() != "sqlite" or parsed.database in (None, "", ":memory:"):
        return None
    return sqlite_file_path(url, COMMERCIAL_DATABASE_URL_ENV)


def _configured_identity_path(environ: Mapping[str, str]) -> Path | None:
    """The path IDENTITY_DATABASE_URL names, parsed like the identity resolver; None if unset.

    The file need not exist and is not opened.
    """
    url = (environ.get(IDENTITY_DATABASE_URL_ENV) or "").strip()
    return sqlite_file_path(url, IDENTITY_DATABASE_URL_ENV) if url else None


def company_database_paths(
    environ: Mapping[str, str] | None = None, *, check_identity: bool = True
) -> dict[str, Path]:
    """Configured company databases by slug. Unconfigured companies are absent.

    Each slug reads only its own variable. Refuses a file that is protected, a
    sidecar, the DATABASE_URL file, the IDENTITY_DATABASE_URL file (even if that
    file does not exist yet) or shared with another company. Does not require
    the files to exist and opens none of them. check_identity=False is for the
    identity creation CLI, which takes its target from --database instead.
    """
    environ = os.environ if environ is None else environ
    commercial = commercial_database_path(environ)
    identity = _configured_identity_path(environ) if check_identity else None
    paths: dict[str, Path] = {}
    for slug in COMPANY_SLUGS:
        name = company_env_name(slug)
        url = (environ.get(name) or "").strip()
        if not url:
            continue
        path = sqlite_file_path(url, name)
        _check_not_protected(path, name)
        if commercial is not None and _same_file(path, commercial):
            raise DatabaseTargetError(f"{name} points at the same file as {COMMERCIAL_DATABASE_URL_ENV}")
        if identity is not None and _same_file(path, identity):
            raise DatabaseTargetError(f"{name} points at the same file as {IDENTITY_DATABASE_URL_ENV}")
        for other_slug, other in paths.items():
            if _same_file(path, other):
                raise DatabaseTargetError(f"{name} and {company_env_name(other_slug)} point at the same file")
        paths[slug] = path
    return paths


def company_database_path(slug: str, environ: Mapping[str, str] | None = None) -> Path:
    """The database file of one registered company; unknown or unconfigured slugs fail closed."""
    if slug not in COMPANY_SLUGS:
        raise DatabaseTargetError("unknown company")
    try:
        return company_database_paths(environ)[slug]
    except KeyError:
        raise DatabaseTargetError(f"no database is configured for company {slug!r} ({company_env_name(slug)})") from None


def identity_database_path(environ: Mapping[str, str] | None = None) -> Path:
    """The existing identity database file, checked against every commercial target.

    Requires IDENTITY_DATABASE_URL (no fallback), an existing regular file, and
    a path that is not the DATABASE_URL file, a company file, a protected path
    or a sidecar, under any relative/absolute alias or symlink. The collision
    checks run before the existence check, so they hold for a file not yet created.
    """
    environ = os.environ if environ is None else environ
    url = (environ.get(IDENTITY_DATABASE_URL_ENV) or "").strip()
    if not url:
        raise DatabaseTargetError(f"{IDENTITY_DATABASE_URL_ENV} is not set")
    path = sqlite_file_path(url, IDENTITY_DATABASE_URL_ENV)
    _check_not_protected(path, IDENTITY_DATABASE_URL_ENV)
    commercial = commercial_database_path(environ)
    if commercial is not None and _same_file(path, commercial):
        raise DatabaseTargetError(f"{IDENTITY_DATABASE_URL_ENV} points at the same file as DATABASE_URL")
    for slug, other in company_database_paths(environ).items():  # also checks each against the identity file
        if _same_file(path, other):
            raise DatabaseTargetError(f"{IDENTITY_DATABASE_URL_ENV} points at the same file as {company_env_name(slug)}")
    if not os.path.isfile(path):  # follows links; a directory, device or missing path fails
        raise DatabaseTargetError(
            "the identity database does not exist or is not a regular file; "
            "it is never created at startup (use create_identity_db.py)"
        )
    return path
