"""Initialize the loss-reason table of ONE existing company database (dry run by default).

    PM_COMPANY_DB_LITY=sqlite:////abs/path/lity.db uv run python init_company_card_loss.py --company lity [--apply]

Creates the additive `card_loss_events` table if it is missing (card_loss.py). Idempotent; it never touches cards or any
existing table, never moves the schema version, and affects only the company named. Until it has run, a company
database refuses a move into the lost stage (503) instead of recording no reason. The target comes only from that
company's own PM_COMPANY_DB_* variable through database_targets.company_database_path (the protected-path, DATABASE_URL,
identity and collision checks apply; the permanent Salcomp database is refused). The database must already exist and pass
the read-only company schema validation; this command never creates or stamps one. Without --apply nothing is written.
Back the database up first, as for any change to a commercial database.
"""
import argparse
import sys

from sqlalchemy.exc import SQLAlchemyError

from card_loss import create_loss, has_loss
from company_database import company_session_factory, open_company_engine
from company_schema import CompanySchemaError, validate_company_schema
from diagnose_company_schema import refuse_incompatible_tables
from database_targets import COMPANY_SLUGS, DatabaseTargetError


def init_company_card_loss(slug: str, apply: bool = False, environ=None) -> bool:
    """True when the table is (apply) or would be (dry run) created, False when it already exists."""
    validate_company_schema(slug, environ)
    engine = open_company_engine(slug, environ)
    try:
        with company_session_factory(engine)() as db:
            refuse_incompatible_tables(db, slug, "init_company_card_loss.py")
            if has_loss(db):
                return False
            if apply:
                create_loss(db.connection())
                db.commit()
            return True
    finally:
        engine.dispose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Initialize one company's loss-reason table.")
    parser.add_argument("--company", required=True, choices=COMPANY_SLUGS, help="company slug")
    parser.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    args = parser.parse_args(argv)
    try:
        created = init_company_card_loss(args.company, args.apply)
    except (DatabaseTargetError, CompanySchemaError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"Error: could not initialize the loss-reason table ({type(exc).__name__})", file=sys.stderr)
        return 1
    if not created:
        print(f"Company {args.company!r} already has the loss-reason table; nothing to do.")
    else:
        print(f"{'Created' if args.apply else 'Would create'} the loss-reason table of company {args.company!r}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
