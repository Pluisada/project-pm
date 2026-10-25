"""Operator tool for the central identity database (run on the server; not reachable over HTTP).

    IDENTITY_DATABASE_URL=sqlite:////abs/identity.db uv run python identity_admin_cli.py bootstrap --admin-email owner@example.com
    ... identity_admin_cli.py send-activation --email owner@example.com

`bootstrap` registers the three companies (salcomp, lity, valor-log) and creates the global administrator with NO
password. Both steps are idempotent: an existing company or the same administrator is left as is, and a different
existing account with that email is refused (it is never promoted here). The operator attests the email by running the
command, so the account is marked verified; the owner proves control of the mailbox by using the emailed link.
`send-activation` issues a single-use one-hour reset link and emails it through mailer.py (SMTP_* and APP_BASE_URL
must be configured); the link is never printed or logged. Passwords are never accepted as arguments.
The identity database must already exist (create_identity_db.py); this tool never creates it.
"""
import argparse
import sys

import central_auth
import identity_service as svc
from database_targets import COMPANY_SLUGS
from identity_database import create_identity_session_factory, open_identity_engine
from identity_models import IdentityCompany

NAMES = {"salcomp": "Salcomp", "lity": "Lity", "valor-log": "Valor Log"}


def bootstrap(session, admin_email: str) -> dict:
    email = svc.normalize_email(admin_email)
    created_companies = []
    for slug in COMPANY_SLUGS:
        if session.query(IdentityCompany).filter_by(slug=slug).first() is None:
            session.add(IdentityCompany(slug=slug, name=NAMES.get(slug, slug)))
            created_companies.append(slug)
    session.flush()
    _, created_admin = svc.grant_global_admin(session, email)  # enforces the single-global-administrator limit
    return {"companies_created": created_companies, "admin_created": created_admin}


def send_activation(session, email: str, send=None) -> bool:
    """Email a fresh reset link to an active, verified account. False when the account is not eligible."""
    import mailer
    from auth import APP_BASE_URL
    raw = central_auth.issue_password_reset(session, email)
    if raw is None:
        return False
    (send or mailer.send_email)(
        svc.normalize_email(email), "Set your Sales Pipeline password",
        f"Open this link to choose your password (valid for one hour):\n\n{APP_BASE_URL}/?central_reset_token={raw}\n")
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap").add_argument("--admin-email", required=True)
    sub.add_parser("send-activation").add_argument("--email", required=True)
    args = parser.parse_args(argv)
    try:
        engine = open_identity_engine()
    except Exception as exc:
        print(f"REFUSED: {type(exc).__name__}", file=sys.stderr)
        return 1
    try:
        with create_identity_session_factory(engine)() as session:
            if args.command == "bootstrap":
                print(bootstrap(session, args.admin_email))
            else:
                print("sent" if send_activation(session, args.email) else "not eligible (nothing sent)")
    except svc.IdentityServiceError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
