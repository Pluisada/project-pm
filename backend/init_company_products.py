"""Initialize the product catalog of ONE existing company database (dry run by default).

    PM_COMPANY_DB_LITY=sqlite:////abs/path/lity.db uv run python init_company_products.py --company lity [--apply]

Creates the `products` table if it is missing and adds the built-in defaults
(lead_options.PRODUCT_LINES: Chargers, Power supplies) that are not yet present.
Idempotent; it never renames, removes or overwrites a product, never touches cards,
and never copies anything between companies: each run affects the single company
named. The target comes only from that company's own PM_COMPANY_DB_* variable through
database_targets.company_database_path, so the protected-path, DATABASE_URL, identity
and collision checks apply and a protected database (the permanent Salcomp one) is
refused. The database must already exist and pass the read-only company schema
validation; this command never creates or stamps one. Without --apply nothing is
written.
"""
import argparse
import sys

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from company_database import company_session_factory, open_company_engine
from company_schema import CompanySchemaError, validate_company_schema
from database_targets import COMPANY_SLUGS, DatabaseTargetError
from lead_options import PRODUCT_LINES
from product_catalog import Product, has_catalog, initialize_catalog


def init_company_products(slug: str, apply: bool = False, environ=None) -> list[str]:
    """The default products that are (apply) or would be (dry run) added to company `slug`."""
    validate_company_schema(slug, environ)
    engine = open_company_engine(slug, environ)
    try:
        with company_session_factory(engine)() as db:
            if not apply:
                present = {n.lower() for n in db.scalars(select(Product.name))} if has_catalog(db) else set()
                return [n for n in PRODUCT_LINES if n.lower() not in present]
            try:
                added = initialize_catalog(db)
                db.commit()
            except BaseException:
                db.rollback()
                raise
            return added
    finally:
        engine.dispose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Initialize one company's product catalog.")
    parser.add_argument("--company", required=True, choices=COMPANY_SLUGS, help="company slug")
    parser.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    args = parser.parse_args(argv)
    try:
        added = init_company_products(args.company, args.apply)
    except (DatabaseTargetError, CompanySchemaError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"Error: could not initialize the catalog ({type(exc).__name__})", file=sys.stderr)
        return 1
    verb = "Added" if args.apply else "Would add"
    print(f"{verb} {len(added)} product(s) to company {args.company!r}: {', '.join(added) or 'nothing to do'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
