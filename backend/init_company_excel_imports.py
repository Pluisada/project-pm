"""Initialize the Cards-import ledger of ONE existing company database (dry run by default).

    PM_COMPANY_DB_LITY=sqlite:////abs/path/lity.db uv run python init_company_excel_imports.py --company lity [--apply]

Creates the additive `card_excel_imports` table if it is missing (excel_import_ledger.py). Idempotent; it never touches
cards, products or any existing table, never moves the schema version, and affects only the company named. The target
comes only from that company's own PM_COMPANY_DB_* variable through database_targets.company_database_path (the
protected-path, DATABASE_URL, identity and collision checks apply; the permanent Salcomp database is refused). The
database must already exist and pass the read-only company schema validation; this command never creates or stamps
one. Without --apply nothing is written. Back the database up first, as for any change to a commercial database.
"""
import argparse
import sys

from sqlalchemy.exc import SQLAlchemyError

from company_database import company_session_factory, open_company_engine
from company_schema import CompanySchemaError, validate_company_schema
from database_targets import COMPANY_SLUGS, DatabaseTargetError
from excel_import_ledger import create_ledger, has_ledger


def init_company_excel_imports(slug: str, apply: bool = False, environ=None) -> bool:
    """True when the table is (apply) or would be (dry run) created, False when it already exists."""
    validate_company_schema(slug, environ)
    engine = open_company_engine(slug, environ)
    try:
        with company_session_factory(engine)() as db:
            if has_ledger(db):
                return False
            if apply:
                create_ledger(db.connection())
                db.commit()
            return True
    finally:
        engine.dispose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Initialize one company's Cards-import ledger.")
    parser.add_argument("--company", required=True, choices=COMPANY_SLUGS, help="company slug")
    parser.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    args = parser.parse_args(argv)
    try:
        created = init_company_excel_imports(args.company, args.apply)
    except (DatabaseTargetError, CompanySchemaError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"Error: could not initialize the ledger ({type(exc).__name__})", file=sys.stderr)
        return 1
    if not created:
        print(f"Company {args.company!r} already has the Cards-import ledger; nothing to do.")
    else:
        print(f"{'Created' if args.apply else 'Would create'} the Cards-import ledger of company {args.company!r}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
