"""Engine and session provider for the central identity database.

Nothing happens at import time: no engine, no file access. The URL comes only
from IDENTITY_DATABASE_URL, validated only by database_targets.identity_database_path
(open_identity_engine); DATABASE_URL, the commercial database setting, is never a
fallback. There is deliberately no URL-string helper or public engine constructor here.
"""
from collections.abc import Callable, Iterator
from urllib.parse import quote

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from database_targets import identity_database_path
from identity_models import IdentityBase
from identity_schema import IDENTITY_SCHEMA_VERSION, schema_problems

IDENTITY_DATABASE_URL_ENV = "IDENTITY_DATABASE_URL"


class IdentityDatabaseNotConfigured(RuntimeError):
    """No usable identity database URL was supplied."""


def _build_engine(url: URL | str) -> Engine:
    """SQLite engine with foreign keys enforced. Private: callers must pass a validated URL.

    Production reaches this only through open_identity_engine(). An in-memory URL
    gets a single shared connection so every session sees the same database.
    Creating the engine opens nothing.
    """
    url = make_url(url)
    if url.get_backend_name() != "sqlite":
        raise IdentityDatabaseNotConfigured("the identity database must be SQLite")
    in_memory = url.database in (None, "", ":memory:")
    kwargs = {"poolclass": StaticPool} if in_memory else {}
    engine = create_engine(url, echo=False, connect_args={"check_same_thread": False}, **kwargs)

    @event.listens_for(engine, "connect")
    def _foreign_keys_on(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine


def create_in_memory_identity_engine() -> Engine:
    """Test-only: a throwaway in-memory engine. It takes no URL, so it cannot reach a file."""
    return _build_engine("sqlite://")


def create_identity_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


class IdentitySchemaVersionError(RuntimeError):
    """The identity database does not carry the schema version this code expects."""


def create_identity_schema(engine: Engine) -> None:
    """Create the identity tables on an empty database and stamp it (create_identity_db.py, tests).

    Only a database with no objects at all (no tables, views, triggers or indexes
    besides SQLite's internal ones) and PRAGMA user_version 0 is initialized. A
    database that has any object must already carry the recognized identity schema
    (version and tables) and is then left untouched. Anything else, including a
    nonzero version on an empty file, is refused: an existing database is never
    stamped, repaired or extended here.
    """
    with engine.connect() as connection:
        has_objects = connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite\\_%' ESCAPE '\\' LIMIT 1"
        ).first() is not None
        version = connection.exec_driver_sql("PRAGMA user_version").scalar()
    if has_objects:
        require_identity_schema_version(engine)
        return
    if version != 0:
        raise IdentitySchemaVersionError(
            f"the database has no objects but carries schema version {version}; "
            "it is not a new database and is never stamped"
        )
    IdentityBase.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"PRAGMA user_version = {IDENTITY_SCHEMA_VERSION}")


def require_identity_schema_version(engine: Engine) -> None:
    """Refuse a database that is not the recognized identity schema; read-only.

    Needs PRAGMA user_version == IDENTITY_SCHEMA_VERSION and a schema that matches the models: every table,
    both role CHECK constraints, AUTOINCREMENT, every index (including the partial unique open-invitation
    index) and no foreign-key violation. A database from an older version is refused, not upgraded:
    migrate_identity_v2.py does that, explicitly. (PRAGMA application_id is deliberately not used: stamping
    it on existing databases would be a separate explicit migration step, deferred.)
    """
    with engine.connect() as connection:
        found = connection.exec_driver_sql("PRAGMA user_version").scalar()
        if found != IDENTITY_SCHEMA_VERSION:
            raise IdentitySchemaVersionError(
                f"identity schema version {found} found, {IDENTITY_SCHEMA_VERSION} expected; "
                "the application never stamps or migrates it (see migrate_identity_v2.py)"
            )
        problems = schema_problems(connection.connection.driver_connection)
    if problems:
        raise IdentitySchemaVersionError(
            f"the database is marked version {IDENTITY_SCHEMA_VERSION} but does not match that schema "
            f"({'; '.join(problems)}); it is never repaired"
        )


def open_identity_engine(environ=None) -> Engine:
    """Validate the configured target, open the existing identity database, check its version."""
    path = identity_database_path(environ)
    # A file: URI built from the validated path only (percent-encoded, so '?', '#' and '%' stay
    # literal). mode=rw makes SQLite refuse to create the file if it vanished after validation.
    engine = _build_engine(
        URL.create("sqlite", database=f"file:{quote(str(path), safe='/')}", query={"mode": "rw", "uri": "true"})
    )
    try:
        require_identity_schema_version(engine)
    except BaseException:
        engine.dispose()
        raise
    return engine


def identity_db_dependency(factory: sessionmaker[Session]) -> Callable[[], Iterator[Session]]:
    """A FastAPI-style dependency that yields a session from `factory` and closes it."""

    def get_identity_db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    return get_identity_db
