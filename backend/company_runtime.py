"""Lazy per-company engines for company-scoped requests.

Nothing happens at import time. A company engine is opened on the first request
that was already authorized for that company (the session dependency below
depends on the CompanyContext, so FastAPI resolves authorization first and a
rejected request never reaches this module). The file comes only from
company_database.open_company_engine / database_targets; DATABASE_URL is never a
fallback. The schema is checked read-only once per company (company_schema); this
module never creates, stamps, migrates or initializes a database. Any failure
answers a fixed 503 and caches nothing, so the next request retries.
"""
import logging
import threading
from collections.abc import Iterator, Mapping

from fastapi import HTTPException, status
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from central_deps import CompanyContext
from company_database import COMPANY_SLUG_INFO_KEY, company_session_factory, open_company_engine, session_company_slug
from company_schema import validate_company_schema

logger = logging.getLogger(__name__)

UNAVAILABLE_DETAIL = "Company data is unavailable"


class CompanyRuntime:
    """Owns at most one engine and session factory per company slug."""

    def __init__(self, environ: Mapping[str, str] | None = None):
        self._environ = environ  # None: read os.environ when an engine is opened
        self._lock = threading.Lock()
        self._engines: dict[str, Engine] = {}
        self._factories: dict[str, sessionmaker[Session]] = {}

    def _session_factory(self, slug: str) -> sessionmaker[Session]:
        with self._lock:
            factory = self._factories.get(slug)
            if factory is None:
                engine = None
                try:
                    validate_company_schema(slug, self._environ)
                    engine = open_company_engine(slug, self._environ)
                    factory = company_session_factory(engine)
                except Exception as exc:  # target, schema or SQLite errors: all fail closed
                    if engine is not None:
                        engine.dispose()
                    logger.error("Company database unavailable (%s)", type(exc).__name__)
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=UNAVAILABLE_DETAIL
                    ) from None
                self._engines[slug] = engine
                self._factories[slug] = factory
            return factory

    def session_for(self, context: CompanyContext) -> Iterator[Session]:
        """One session on the authorized company's database, always closed."""
        session = self._session_factory(context.company_slug)()
        # The authorization was decided for context.company_slug: the session must be bound to exactly that company's
        # database (label and engine), or nothing may be read or written through it.
        if session.info.get(COMPANY_SLUG_INFO_KEY) != context.company_slug or session_company_slug(session) != context.company_slug:
            session.close()
            logger.error("Company session does not match the authorized company")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=UNAVAILABLE_DETAIL)
        try:
            yield session
        finally:
            session.close()

    def dispose(self) -> None:
        """Release every engine (application shutdown). A later request may open them again."""
        with self._lock:
            engines = list(self._engines.values())
            self._engines.clear()
            self._factories.clear()
        for engine in engines:
            engine.dispose()


company_runtime = CompanyRuntime()
