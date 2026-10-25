"""Initialize the card-review tables of ONE existing company database (dry run by default).

    PM_COMPANY_DB_LITY=sqlite:////abs/path/lity.db uv run python init_company_card_reviews.py --company lity [--apply]

Creates the additive `card_versions`, `card_review_obligations` and `card_review_acknowledgments` (per-reviewer CLEAR
state) tables if any is missing (card_reviews.py). A database that has the first two only gains the third; no existing row is
changed and no pending review is acknowledged: pending role-level obligations simply get pending per-reviewer rows on
their first touch.
Idempotent; it never touches cards or any existing table, never moves the schema version, and affects only the company
named. Until it has run, a company database refuses the changes that need a review obligation (a Sales Member's edit, a
backward move) with 503 instead of skipping the review. The target comes only from that company's own PM_COMPANY_DB_*
variable through database_targets.company_database_path (the protected-path, DATABASE_URL, identity and collision checks
apply; the permanent Salcomp database is refused). The database must already exist and pass the read-only company schema
validation; this command never creates or stamps one. Without --apply nothing is written. Back the database up first, as
for any change to a commercial database.
"""
import argparse
import sys

from sqlalchemy.exc import SQLAlchemyError

from card_reviews import create_reviews, has_reviews
from company_database import company_session_factory, open_company_engine
from company_schema import CompanySchemaError, validate_company_schema
from diagnose_company_schema import refuse_incompatible_tables
from database_targets import COMPANY_SLUGS, DatabaseTargetError


def init_company_card_reviews(slug: str, apply: bool = False, environ=None) -> bool:
    """True when the tables are (apply) or would be (dry run) created, False when they already exist."""
    validate_company_schema(slug, environ)
    engine = open_company_engine(slug, environ)
    try:
        with company_session_factory(engine)() as db:
            refuse_incompatible_tables(db, slug, "init_company_card_reviews.py")
            if has_reviews(db):
                return False
            if apply:
                create_reviews(db.connection())
                db.commit()
            return True
    finally:
        engine.dispose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Initialize one company's card-review tables.")
    parser.add_argument("--company", required=True, choices=COMPANY_SLUGS, help="company slug")
    parser.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    args = parser.parse_args(argv)
    try:
        created = init_company_card_reviews(args.company, args.apply)
    except (DatabaseTargetError, CompanySchemaError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"Error: could not initialize the review tables ({type(exc).__name__})", file=sys.stderr)
        return 1
    if not created:
        print(f"Company {args.company!r} already has the card-review tables; nothing to do.")
    else:
        print(f"{'Created' if args.apply else 'Would create'} the card-review tables of company {args.company!r}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
