"""Seed a disposable CENTRAL-mode environment for the Playwright central spec: identity database, the three company
databases, a global administrator, a company Admin and a Sales Member (all with a known test password), and a board with cards in
Lity. Run by playwright.central.config.ts with PYTHONPATH=backend; writes only inside the directory it is given.

Usage: seed_central.py <empty directory>
"""
import sqlite3
import sys
from pathlib import Path

import central_auth as ca
import identity_service as svc
from auth import hash_password
from create_company_db import create_company_database
from create_identity_db import create_identity_database
from database_targets import COMPANY_SLUGS, company_database_path, company_env_name
from identity_database import create_identity_session_factory, open_identity_engine
from identity_models import IdentityCompany, IdentityUser
from lead_options import PIPELINE_STAGES

PASSWORD = "Str0ng!Passw0rd"  # a test value for this disposable environment only
ADMIN_EMAIL = "e2e-admin@example.com"
OTHER_EMAIL = "e2e-other@example.com"
MEMBER_EMAIL = "e2e-member@example.com"
NOW = "2026-01-01 00:00:00"
CARDS = ["E2E Edit", "E2E Stale Edit", "E2E Stale Move", "E2E Stale Delete", "E2E Undo Conflict", "E2E Review Card", "E2E Review Back"]


def environment(directory: Path) -> dict[str, str]:
    env = {company_env_name(s): f"sqlite:///{directory / (s + '.db')}" for s in COMPANY_SLUGS}
    env["IDENTITY_DATABASE_URL"] = f"sqlite:///{directory / 'identity.db'}"
    return env


def main(directory: Path) -> None:
    directory = directory.resolve()
    if any(directory.iterdir()):
        raise SystemExit("the seed directory must be empty")
    env = environment(directory)
    create_identity_database(str(directory / "identity.db"))
    engine = open_identity_engine(env)
    session = create_identity_session_factory(engine)()
    session.add_all([IdentityCompany(slug=s, name=s.title()) for s in COMPANY_SLUGS])
    session.add(IdentityUser(email=ADMIN_EMAIL, is_global_admin=True, password_hash=hash_password(PASSWORD),
                             email_verified_at=svc._now()))
    session.commit()
    admin_id = svc.get_user_by_email(session, ADMIN_EMAIL).id
    token = svc.create_invitation(session, admin_id, OTHER_EMAIL, "lity", "sales_member").token
    other_id = ca.accept_invitation_with_password(session, token, PASSWORD).user_id
    svc.change_membership_role(session, admin_id, "lity", other_id, "admin")  # a second person who may edit any card
    member_token = svc.create_invitation(session, admin_id, MEMBER_EMAIL, "lity", "sales_member").token
    member_id = ca.accept_invitation_with_password(session, member_token, PASSWORD).user_id  # a Sales Member of Lity
    session.close()
    engine.dispose()

    for slug in COMPANY_SLUGS:
        create_company_database(slug, env)
    con = sqlite3.connect(company_database_path("lity", env))
    try:
        con.execute("INSERT INTO users (id, username, created_at, updated_at) VALUES (?, ?, ?, ?)", (admin_id, ADMIN_EMAIL, NOW, NOW))
        con.execute("INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (1, ?, 'Lity Pipeline', ?, ?)", (admin_id, NOW, NOW))
        for index, (title, kind) in enumerate(PIPELINE_STAGES):
            con.execute("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?, ?)",
                        (index + 1, title, index, kind, NOW, NOW))
        con.execute("INSERT INTO users (id, username, created_at, updated_at) VALUES (?, ?, ?, ?)", (member_id, MEMBER_EMAIL, NOW, NOW))
        for index, title in enumerate(CARDS):
            owner = member_id if title == "E2E Review Card" else None  # the Sales Member's own card: their edit needs review
            con.execute("INSERT INTO cards (id, column_id, title, position, salesperson_id, created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?, ?)",
                        (index + 1, title, index, owner, NOW, NOW))
        con.commit()
    finally:
        con.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
