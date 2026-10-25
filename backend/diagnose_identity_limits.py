"""Read-only report of identity data above the access limits: more than two active Admins (plus pending Admin
invitations) in a company, or more than one active global administrator.

    IDENTITY_DATABASE_URL=sqlite:////abs/path/identity.db uv run python diagnose_identity_limits.py

Nothing is changed: no one is demoted, deactivated or deleted. Resolving an over-limit condition is an explicit decision
(deactivate a membership or revoke an invitation through the Super Admin screens). Exit 0: within limits; 1: over a
limit; 2: the database could not be inspected. Names no email, token or path.
"""
import sys

import identity_service as svc
from identity_database import create_identity_session_factory, open_identity_engine


def main() -> int:
    try:
        engine = open_identity_engine()
    except Exception as exc:
        print(f"Error: {type(exc).__name__}", file=sys.stderr)
        return 2
    try:
        with create_identity_session_factory(engine)() as session:
            report = svc.over_limit_report(session)
    finally:
        engine.dispose()
    for item in report["companies_over_admin_limit"]:
        print(f"OVER LIMIT company {item['company']}: {item['active_admins']} active Admins + "
              f"{item['pending_admin_invitations']} pending Admin invitations (maximum {svc.MAX_COMPANY_ADMINS})")
    if report["active_global_admins"]:
        print(f"OVER LIMIT: {len(report['active_global_admins'])} active global administrators, user ids "
              f"{report['active_global_admins']} (maximum {svc.MAX_GLOBAL_ADMINS})")
    over = bool(report["companies_over_admin_limit"] or report["active_global_admins"])
    if not over:
        print("Within limits.")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
