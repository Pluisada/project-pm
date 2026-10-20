"""Engine and session factory for one company's commercial database.

Infrastructure only. Importing this module and creating an engine open nothing,
create nothing and run no SQL. The file comes exclusively from
database_targets.company_database_path(slug), which owns every path check
(protected paths, collisions, URL forms); this module never parses the raw
environment string, never reads DATABASE_URL and never exempts a company. A
rejected target (including the permanent Salcomp database) propagates as
DatabaseTargetError before any engine exists.

The engine URL is a file: URI built from the validated resolved path with
mode=rw, so SQLite refuses to create a missing file at first use.

Deferred, required before production use: company-schema validation. The company
schema and its version marker are not defined yet, so nothing here claims that an
existing file is a valid company database, stamps a version, creates tables or
migrates. Not called from main.py or any route.
"""
from urllib.parse import quote
from weakref import WeakKeyDictionary

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from database_targets import company_database_path

COMPANY_SLUG_INFO_KEY = "company_slug"
_ENGINE_SLUGS: "WeakKeyDictionary[Engine, str]" = WeakKeyDictionary()  # Engine has no .info


def open_company_engine(slug: str, environ=None) -> Engine:
    """SQLite engine for the existing database of `slug`; connects to nothing yet.

    Raises DatabaseTargetError for an unknown, unconfigured or rejected company.
    The engine is tagged with its slug and enforces foreign keys per connection.
    """
    path = company_database_path(slug, environ)
    url = URL.create("sqlite", database=f"file:{quote(str(path), safe='/')}", query={"mode": "rw", "uri": "true"})
    engine = create_engine(url, echo=False, connect_args={"check_same_thread": False})
    _ENGINE_SLUGS[engine] = slug

    @event.listens_for(engine, "connect")
    def _foreign_keys_on(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine


def company_session_factory(engine: Engine) -> sessionmaker[Session]:
    """A session factory permanently bound to a company engine from open_company_engine.

    Refuses any other engine. Callers must not pass bind= to the factory; the
    bound engine's slug is exposed as factory.kw["info"]["company_slug"].
    """
    slug = _ENGINE_SLUGS.get(engine)
    if slug is None:
        raise ValueError("engine was not created by open_company_engine")
    return sessionmaker(autocommit=False, autoflush=False, bind=engine, info={COMPANY_SLUG_INFO_KEY: slug})


def begin_write(session: Session) -> None:
    """Take SQLite's write lock now, before the caller reads the state its decision depends on.

    pysqlite opens a transaction only when the first DML runs, so a read-compute-write route would otherwise decide
    on a snapshot another request can invalidate before this one writes (duplicate positions, two won stages, a card
    lost to a column delete). `BEGIN IMMEDIATE` makes the whole route a single serialized unit, also across
    processes. Call it first in the route, after sync_company_user committed: the session then has no open SQLite
    transaction. commit()/rollback()/close() release the lock as for any transaction. When another connection holds
    the lock past the busy timeout this raises OperationalError, which main.database_error_handler answers as 503
    with Retry-After; nothing was read or written. If a DML already ran in this transaction the lock is held and
    this does nothing.
    """
    connection = session.connection()
    if connection.connection.driver_connection.in_transaction:
        return
    connection.exec_driver_sql("BEGIN IMMEDIATE")
    session.expire_all()  # nothing loaded before the lock may be trusted
