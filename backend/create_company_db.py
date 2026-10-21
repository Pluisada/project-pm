"""Create a new company database (schema version company_schema.COMPANY_SCHEMA_VERSION).

    PM_COMPANY_DB_LITY=sqlite:////abs/path/lity.db uv run python create_company_db.py --company lity

An explicit development/test utility; nothing in the application calls it. The
target comes only from the company's own PM_COMPANY_DB_* variable through
database_targets.company_database_path, so every protected-path, sidecar,
DATABASE_URL, identity and cross-company collision check applies and the
permanent Salcomp database is not exempt: while its path is refused by policy,
this command refuses it too. There is no --database option and no fallback to
DATABASE_URL.

The file must not exist (an empty file counts as existing) and its directory
must already exist. Nothing existing is overwritten, repaired, stamped or
migrated. The file is created exclusively, the schema comes from
models.CompanyBase.metadata (commercial tables plus the local `users` reference
table, left empty: those rows are provisioned by a later identity/company
synchronization step), the version is stamped here and only here, and success is
reported only after the read-only validator and a foreign-key check pass. If a
step fails, the file is removed only when it is still the exact file this call
created. The identity database is never opened or created. Importing this module
touches no database.
"""
import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from company_schema import COMPANY_SCHEMA_VERSION, CompanySchemaError, validate_company_schema
from create_identity_db import _remove_created_file
from database_targets import COMPANY_SLUGS, DatabaseTargetError, company_database_path
from models import CompanyBase
from card_loss import create_loss
from card_reviews import create_reviews
from excel_import_ledger import create_ledger
from product_catalog import initialize_catalog


class CompanyDatabaseError(Exception):
    """A refused or failed creation; the message is safe to show."""


def _check_foreign_keys(engine) -> None:
    with engine.connect() as connection:
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar() != 1:
            raise CompanyDatabaseError("foreign keys are not enforced on the new database connection")
        if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
            raise CompanyDatabaseError("the new database has foreign-key violations")


def create_company_database(slug: str, environ=None) -> Path:
    """Create and verify the database of company `slug`; returns its path.

    DatabaseTargetError for an unknown, unconfigured or rejected target;
    CompanyDatabaseError for an existing target or a failed verification.
    """
    target = company_database_path(slug, environ)  # also rejects unknown slugs
    if os.path.lexists(target):
        raise CompanyDatabaseError(f"the database of company {slug!r} already exists; it is never overwritten or reused")
    if not target.parent.is_dir():
        raise CompanyDatabaseError(f"the directory of the database of company {slug!r} does not exist")
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # closes the check/create race
    except FileExistsError:
        raise CompanyDatabaseError(f"the database of company {slug!r} already exists; it is never overwritten or reused") from None
    try:
        info = os.fstat(fd)
    finally:
        os.close(fd)
    created = (info.st_dev, info.st_ino)
    engine = None
    try:
        engine = create_engine(URL.create("sqlite", database=str(target)))

        @event.listens_for(engine, "connect")
        def _foreign_keys_on(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

        CompanyBase.metadata.create_all(engine)
        create_ledger(engine)  # the Cards-import ledger: outside the required contract, like the product catalog
        create_loss(engine)  # loss reasons: additive, outside the required contract too
        create_reviews(engine)  # card versions and review obligations: additive, outside the required contract too
        with Session(engine) as session:  # a new company starts with the default products
            initialize_catalog(session)
            session.commit()
        with engine.begin() as connection:
            connection.exec_driver_sql(f"PRAGMA user_version = {COMPANY_SCHEMA_VERSION}")
        _check_foreign_keys(engine)
        engine.dispose()
        engine = None
        validate_company_schema(slug, environ)
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
    parser = argparse.ArgumentParser(description="Create a new company database.")
    parser.add_argument("--company", required=True, choices=COMPANY_SLUGS, help="company slug")
    args = parser.parse_args(argv)
    try:
        create_company_database(args.company)
    except (DatabaseTargetError, CompanyDatabaseError, CompanySchemaError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Error: could not create the database: {exc.strerror or type(exc).__name__}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"Error: could not create the schema ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"Created the database of company {args.company!r} (schema version {COMPANY_SCHEMA_VERSION}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
