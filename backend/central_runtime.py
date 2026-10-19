"""Lazy runtime for the central identity engine, scoped to the central endpoints.

Nothing happens at import time. The engine is opened on the first central request
through identity_database.open_identity_engine, which reads only IDENTITY_DATABASE_URL,
refuses a missing or colliding file and a wrong schema version, and never creates,
initializes or stamps anything. DATABASE_URL is never a fallback. When the identity
database is unavailable the central endpoints answer 503 with a fixed message (fail
closed); the legacy application is unaffected and never falls back to legacy credentials.
"""
import logging
import threading
from collections.abc import Iterator, Mapping

from fastapi import HTTPException, status
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from identity_database import create_identity_session_factory, open_identity_engine

logger = logging.getLogger(__name__)

UNAVAILABLE_DETAIL = "Central authentication is unavailable"


class CentralIdentityRuntime:
    """Owns at most one identity engine and its session factory."""

    def __init__(self, environ: Mapping[str, str] | None = None):
        self._environ = environ  # None: read os.environ when the engine is opened
        self._lock = threading.Lock()
        self._engine: Engine | None = None
        self._factory: sessionmaker[Session] | None = None

    def _session_factory(self) -> sessionmaker[Session]:
        with self._lock:
            if self._factory is None:
                try:
                    engine = open_identity_engine(self._environ)
                except Exception as exc:  # config, target, schema or SQLite errors: all fail closed
                    logger.error("Central identity database unavailable (%s)", type(exc).__name__)
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=UNAVAILABLE_DETAIL
                    ) from None
                self._engine = engine
                self._factory = create_identity_session_factory(engine)
            return self._factory

    def get_session(self) -> Iterator[Session]:
        """FastAPI dependency: one identity session per request, always closed."""
        session = self._session_factory()()
        try:
            yield session
        finally:
            session.close()

    def dispose(self) -> None:
        """Release the engine (application shutdown). A later request may open it again."""
        with self._lock:
            engine, self._engine, self._factory = self._engine, None, None
        if engine is not None:
            engine.dispose()


central_runtime = CentralIdentityRuntime()
