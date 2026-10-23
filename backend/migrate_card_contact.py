"""Add the optional customer-contact columns `cards.contact_email` and `cards.contact_phone` (dry run by default).

    PM_COMPANY_DB_LITY=sqlite:////abs/path/lity.db uv run python migrate_card_contact.py --company lity [--apply]

Two nullable TEXT columns are added with ALTER TABLE ADD COLUMN: existing rows keep NULL, nothing is rewritten, the
schema version (PRAGMA user_version) is not touched, and the two ALTERs run in one transaction (a failure leaves the
database as it was). Idempotent: a database that already has both columns is reported and left alone. The target comes
only from the company's own PM_COMPANY_DB_* variable through database_targets.company_database_path (the protected-path
checks apply). The database must already exist and have a `cards` table; this command never creates one.

Until it has run, the company database fails the schema validation (company_schema.REQUIRED_COMPANY_SCHEMA now lists the
two columns), so the company answers 503 instead of failing on a query. Back the database up first, as for any change to
a commercial database. Without --apply nothing is written.
"""
import argparse
import os
import sqlite3
import sys
from urllib.parse import quote

from database_targets import COMPANY_SLUGS, DatabaseTargetError, company_database_path

COLUMNS = (("contact_email", "VARCHAR(254)"), ("contact_phone", "VARCHAR(40)"))


def migrate_card_contact(path, apply: bool = False) -> list[str]:
    """Names of the columns added (apply) or that would be added (dry run); [] when both exist."""
    if not os.path.isfile(path):
        raise FileNotFoundError("the database does not exist; it is never created here")
    mode = "rw" if apply else "ro"
    conn = sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode={mode}", uri=True, isolation_level=None)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(cards)")}
        if not existing:
            raise RuntimeError("the database has no cards table")
        missing = [(name, ddl) for name, ddl in COLUMNS if name not in existing]
        if apply and missing:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for name, ddl in missing:
                    conn.execute(f"ALTER TABLE cards ADD COLUMN {name} {ddl}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return [name for name, _ in missing]
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Add the card contact email and telephone columns.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--company", choices=COMPANY_SLUGS, help="company slug (PM_COMPANY_DB_* names the file)")
    target.add_argument("--database", help="absolute path of a single-database (legacy) file")
    parser.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    args = parser.parse_args(argv)
    try:
        path = company_database_path(args.company) if args.company else args.database
        if args.database and not os.path.isabs(path):
            raise RuntimeError("--database needs an absolute path")
        added = migrate_card_contact(path, args.apply)
    except (DatabaseTargetError, FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"Error: could not migrate ({type(exc).__name__})", file=sys.stderr)
        return 1
    if not added:
        print("The cards table already has the contact columns; nothing to do.")
    else:
        print(f"{'Added' if args.apply else 'Would add'} cards.{', cards.'.join(added)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
