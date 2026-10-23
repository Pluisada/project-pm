"""identity_service: central users, invitations, memberships and local reference sync.

Disposable tmp_path databases only (identity and company files created by the
explicit initializers, then opened through the normal target-validated helpers).
"""
import subprocess
import sys
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import sessionmaker

import identity_service as svc
from company_database import company_session_factory, open_company_engine
from create_company_db import create_company_database
from create_identity_db import create_identity_database
from database_targets import COMPANY_SLUGS, company_env_name
from identity_database import create_identity_session_factory, open_identity_engine
from identity_models import (
    IdentityAuditEvent, IdentityAuthToken, IdentityBase, IdentityCompany, IdentityInvitation, IdentityMembership,
    IdentityUser,
)
from models import CompanyBase, CompanyUser

BACKEND = Path(__file__).parent


class Env:
    def __init__(self, tmp_path):
        self.env = {company_env_name(s): f"sqlite:///{tmp_path / (s + '.db')}" for s in COMPANY_SLUGS}
        self.env["IDENTITY_DATABASE_URL"] = f"sqlite:///{tmp_path / 'identity.db'}"
        create_identity_database(str(tmp_path / "identity.db"))
        self.engine = open_identity_engine(self.env)
        self.factory = create_identity_session_factory(self.engine)
        with self.factory() as s:
            s.add_all([IdentityCompany(slug=slug, name=slug.title()) for slug in COMPANY_SLUGS])
            s.add(IdentityUser(email="admin@example.com", is_global_admin=True))
            s.add(IdentityUser(email="plain@example.com"))
            s.commit()
        self.session = self.factory()
        self.admin = svc.get_user_by_email(self.session, "admin@example.com").id
        self.plain = svc.get_user_by_email(self.session, "plain@example.com").id
        self.tmp_path = tmp_path
        self._company = {}

    def company_session(self, slug):
        if slug not in self._company:
            create_company_database(slug, self.env)
            self._company[slug] = company_session_factory(open_company_engine(slug, self.env))
        return self._company[slug]()


@pytest.fixture
def e(tmp_path):
    env = Env(tmp_path)
    yield env
    env.session.close()
    env.engine.dispose()


def invite(e, email="new@example.com", slug="lity", role="sales_member", **kw):
    return svc.create_invitation(e.session, e.admin, email, slug, role, **kw)


# --- users and companies ---

def test_user_creation_and_lookup(e):
    user = svc.create_user(e.session, "  New.Person@Example.COM ")
    assert user.email == "new.person@example.com" and user.password_hash is None
    assert not user.is_global_admin and user.is_active and user.email_verified_at is None
    assert svc.get_user_by_email(e.session, "NEW.person@example.com").id == user.id
    assert svc.get_user(e.session, user.id).id == user.id
    with pytest.raises(svc.Conflict):
        svc.create_user(e.session, "new.person@example.com")
    for bad in ("", "nope", "a@b", "a b@c.com", "a@@c.com"):
        with pytest.raises(svc.InvalidInput):
            svc.create_user(e.session, bad)


def test_company_lookup_only_for_supported_slugs(e):
    for slug in COMPANY_SLUGS:
        assert svc.get_company(e.session, slug).slug == slug
    with pytest.raises(svc.InvalidInput):
        svc.get_company(e.session, "acme")
    with pytest.raises(svc.InvalidInput):
        svc.get_company(e.session, "lity' OR '1'='1")


# --- invitation creation ---

def test_only_an_active_global_admin_may_invite(e):
    with pytest.raises(svc.NotAuthorized):
        svc.create_invitation(e.session, e.plain, "x@example.com", "lity", "visitor")
    with pytest.raises(svc.NotAuthorized):
        svc.create_invitation(e.session, 9999, "x@example.com", "lity", "visitor")
    e.session.execute(text("UPDATE identity_users SET is_active = 0 WHERE id = :i"), {"i": e.admin})
    e.session.commit()
    with pytest.raises(svc.NotAuthorized):
        invite(e)
    assert e.session.scalar(select(IdentityInvitation.id)) is None


def test_a_company_member_is_not_an_admin(e):
    token = invite(e, "member@example.com")
    svc.accept_invitation(e.session, token.token)
    member = svc.get_user_by_email(e.session, "member@example.com").id
    with pytest.raises(svc.NotAuthorized):
        svc.create_invitation(e.session, member, "x@example.com", "lity", "visitor")


def test_roles_and_companies_are_validated(e):
    for role in ("super_admin", "owner", "", "SALES_MEMBER", "Admin"):
        with pytest.raises(svc.InvalidInput):
            invite(e, role=role)
    with pytest.raises(svc.InvalidInput):
        invite(e, slug="acme")


def test_token_is_random_and_only_its_hash_is_stored(e):
    a, b = invite(e, "a@example.com"), invite(e, "b@example.com")
    assert a.token != b.token and len(a.token) >= 43
    rows = e.session.scalars(select(IdentityAuthToken)).all()
    assert {r.token_hash for r in rows} == {svc.hash_token(a.token), svc.hash_token(b.token)}
    dump = " ".join(str(v) for r in e.session.execute(text("SELECT * FROM identity_auth_tokens")).all() for v in r)
    assert a.token not in dump and b.token not in dump
    audit = " ".join(r.details for r in e.session.scalars(select(IdentityAuditEvent)))
    assert a.token not in audit and svc.hash_token(a.token) not in audit
    assert all(r.purpose == "invitation" and r.invitation_id for r in rows)


def test_a_second_open_invitation_conflicts_but_an_expired_one_is_replaced(e):
    now = svc._now()
    first = invite(e, now=now - timedelta(days=30))  # long expired
    second = invite(e, now=now)
    assert second.invitation_id != first.invitation_id
    old = e.session.get(IdentityInvitation, first.invitation_id)
    assert old.revoked_at is not None
    with pytest.raises(svc.InvitationInvalid):  # its token was invalidated too
        svc.accept_invitation(e.session, first.token)
    with pytest.raises(svc.Conflict):
        invite(e)


def test_inviting_an_existing_active_member_is_refused(e):
    svc.accept_invitation(e.session, invite(e, "m@example.com").token)
    with pytest.raises(svc.Conflict):
        invite(e, "m@example.com")
    invite(e, "m@example.com", slug="salcomp")  # a different company is fine


def test_inviting_a_global_admin_is_refused(e):
    with pytest.raises(svc.Conflict):
        invite(e, "admin@example.com")


def test_several_companies_get_one_invitation_and_token_each(e):
    issued = svc.create_invitations(e.session, e.admin, "multi@example.com", [("lity", "visitor"), ("salcomp", "sales_member")])
    assert [i.company_slug for i in issued] == ["lity", "salcomp"] and issued[0].token != issued[1].token
    for item in issued:
        svc.accept_invitation(e.session, item.token)
    uid = svc.get_user_by_email(e.session, "multi@example.com").id
    assert svc.company_access(e.session, uid, "lity").role == "visitor"
    assert svc.company_access(e.session, uid, "salcomp").role == "sales_member"
    assert svc.company_access(e.session, uid, "valor-log") is None


def test_multi_company_grants_are_all_or_none(e):
    with pytest.raises(svc.InvalidInput):
        svc.create_invitations(e.session, e.admin, "x@example.com", [("lity", "visitor"), ("lity", "visitor")])
    with pytest.raises(svc.InvalidInput):
        svc.create_invitations(e.session, e.admin, "x@example.com", [("lity", "visitor"), ("acme", "visitor")])
    with pytest.raises(svc.NotAuthorized):
        svc.create_invitations(e.session, e.plain, "x@example.com", [("lity", "visitor")])
    assert e.session.scalar(select(IdentityInvitation.id)) is None


# --- acceptance ---

def test_acceptance_creates_user_membership_and_closes_everything(e):
    issued = invite(e)
    result = svc.accept_invitation(e.session, issued.token)
    assert (result.company_slug, result.role, result.local_sync_required) == ("lity", "sales_member", True)
    user = svc.get_user(e.session, result.user_id)
    assert user.email == "new@example.com" and user.password_hash is None and user.email_verified_at
    assert svc.company_access(e.session, user.id, "lity").role == "sales_member"
    invitation = e.session.get(IdentityInvitation, issued.invitation_id)
    assert invitation.accepted_user_id == user.id and invitation.accepted_at
    assert e.session.scalar(select(IdentityAuthToken.consumed_at)) is not None


def test_a_token_is_single_use(e):
    issued = invite(e)
    svc.accept_invitation(e.session, issued.token)
    with pytest.raises(svc.InvitationInvalid):
        svc.accept_invitation(e.session, issued.token)
    assert e.session.query(IdentityMembership).count() == 1


@pytest.mark.parametrize("token", ["", "nonsense", "x" * 200])
def test_unknown_tokens_are_invalid(e, token):
    invite(e)
    with pytest.raises(svc.InvitationInvalid):
        svc.accept_invitation(e.session, token)
    assert e.session.query(IdentityMembership).count() == 0


def test_expired_invitations_cannot_be_accepted(e):
    issued = invite(e, now=svc._now() - timedelta(days=8))
    with pytest.raises(svc.InvitationInvalid):
        svc.accept_invitation(e.session, issued.token)
    assert e.session.query(IdentityMembership).count() == 0 and e.session.query(IdentityUser).count() == 2
    assert svc.revoke_expired_invitations(e.session) == 1
    assert svc.revoke_expired_invitations(e.session) == 0


def test_revoked_invitations_cannot_be_accepted(e):
    issued = invite(e)
    svc.revoke_invitation(e.session, e.admin, issued.invitation_id)
    with pytest.raises(svc.InvitationInvalid):
        svc.accept_invitation(e.session, issued.token)
    with pytest.raises(svc.Conflict):
        svc.revoke_invitation(e.session, e.admin, issued.invitation_id)
    with pytest.raises(svc.NotFound):
        svc.revoke_invitation(e.session, e.admin, 9999)
    invite(e)  # revoking frees the (email, company) slot


def test_only_admins_revoke_and_accepted_invitations_stay_accepted(e):
    issued = invite(e)
    with pytest.raises(svc.NotAuthorized):
        svc.revoke_invitation(e.session, e.plain, issued.invitation_id)
    svc.accept_invitation(e.session, issued.token)
    with pytest.raises(svc.Conflict):
        svc.revoke_invitation(e.session, e.admin, issued.invitation_id)
    assert e.session.get(IdentityInvitation, issued.invitation_id).revoked_at is None


def test_acceptance_for_a_different_identity_is_refused_and_keeps_the_token(e):
    issued = invite(e, "intended@example.com")
    with pytest.raises(svc.InvitationInvalid):
        svc.accept_invitation(e.session, issued.token, user_id=e.plain)
    assert e.session.scalar(select(IdentityAuthToken.consumed_at)) is None  # rolled back, still usable
    right = svc.create_user(e.session, "intended@example.com")
    assert svc.accept_invitation(e.session, issued.token, user_id=right.id).user_id == right.id


def test_acceptance_is_refused_when_the_inviter_lost_admin_or_user_is_inactive(e):
    issued = invite(e)
    e.session.execute(text("UPDATE identity_users SET is_global_admin = 0 WHERE id = :i"), {"i": e.admin})
    e.session.commit()
    with pytest.raises(svc.InvitationInvalid):
        svc.accept_invitation(e.session, issued.token)
    e.session.execute(text("UPDATE identity_users SET is_global_admin = 1 WHERE id = :i"), {"i": e.admin})
    svc.create_user(e.session, "new@example.com")
    e.session.execute(text("UPDATE identity_users SET is_active = 0 WHERE email = 'new@example.com'"))
    e.session.commit()
    with pytest.raises(svc.InvitationInvalid):
        svc.accept_invitation(e.session, issued.token)


def test_inactive_company_blocks_acceptance(e):
    issued = invite(e)
    e.session.execute(text("UPDATE identity_companies SET is_active = 0 WHERE slug = 'lity'"))
    e.session.commit()
    with pytest.raises(svc.InvitationInvalid):
        svc.accept_invitation(e.session, issued.token)


def test_no_duplicate_membership_and_a_lapsed_one_is_reactivated(e):
    first = invite(e, "m@example.com", role="visitor")
    uid = svc.accept_invitation(e.session, first.token).user_id
    e.session.execute(text("UPDATE identity_memberships SET is_active = 0"))
    e.session.commit()
    assert svc.company_access(e.session, uid, "lity") is None
    again = invite(e, "m@example.com", role="sales_member")
    svc.accept_invitation(e.session, again.token)
    assert e.session.query(IdentityMembership).count() == 1
    assert svc.company_access(e.session, uid, "lity").role == "sales_member"


def test_concurrent_acceptances_succeed_exactly_once(e):
    issued = invite(e)
    barrier, outcomes = threading.Barrier(4), []

    def run():
        session = e.factory()
        try:
            barrier.wait()
            outcomes.append(svc.accept_invitation(session, issued.token).user_id)
        except svc.InvitationInvalid:
            outcomes.append("invalid")
        finally:
            session.close()

    threads = [threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(map(str, outcomes)).count("invalid") == 3 and len(outcomes) == 4
    assert e.session.query(IdentityMembership).count() == 1 and e.session.query(IdentityUser).count() == 3


# --- roles ---

def test_company_roles_are_isolated(e):
    uid = svc.accept_invitation(e.session, invite(e, "m@example.com", "lity", "sales_member").token).user_id
    svc.accept_invitation(e.session, invite(e, "m@example.com", "salcomp", "visitor").token)
    assert svc.require_company_role(e.session, uid, "lity", ("sales_member",)).role == "sales_member"
    with pytest.raises(svc.NotAuthorized):
        svc.require_company_role(e.session, uid, "salcomp", ("sales_member",))
    with pytest.raises(svc.NotAuthorized):
        svc.require_company_role(e.session, uid, "valor-log", ("sales_member", "visitor"))
    with pytest.raises(svc.NotAuthorized):
        svc.require_company_role(e.session, uid, "lity", (svc.SUPER_ADMIN_ROLE,))
    assert svc.require_company_role(e.session, e.admin, "valor-log", (svc.SUPER_ADMIN_ROLE,)).role == "super_admin"
    assert svc.SUPER_ADMIN_ROLE == "super_admin" and svc.ADMIN_ROLE == "admin"


def test_deactivated_users_and_memberships_lose_access(e):
    uid = svc.accept_invitation(e.session, invite(e).token).user_id
    e.session.execute(text("UPDATE identity_users SET is_active = 0 WHERE id = :i"), {"i": uid})
    e.session.commit()
    assert svc.company_access(e.session, uid, "lity") is None


# --- local reference ---

def test_sync_creates_the_reference_only_after_central_membership(e):
    company = e.company_session("lity")
    with pytest.raises(svc.NotAuthorized):
        svc.sync_company_user(e.session, company, e.plain, "lity")  # no membership
    assert company.query(CompanyUser).count() == 0
    uid = svc.accept_invitation(e.session, invite(e, "m@example.com").token).user_id
    local = svc.sync_company_user(e.session, company, uid, "lity")
    assert (local.id, local.username) == (uid, "m@example.com")
    svc.sync_company_user(e.session, company, uid, "lity")  # idempotent
    assert company.query(CompanyUser).count() == 1


def test_a_local_row_alone_grants_nothing(e):
    uid = svc.create_user(e.session, "ghost@example.com").id
    company = e.company_session("lity")
    company.add(CompanyUser(id=uid, username="ghost@example.com"))
    company.commit()
    assert svc.company_access(e.session, uid, "lity") is None
    with pytest.raises(svc.NotAuthorized):
        svc.require_company_role(e.session, uid, "lity", ("sales_member", "visitor"))
    with pytest.raises(svc.NotAuthorized):
        svc.sync_company_user(e.session, company, uid, "lity")


def test_sync_refuses_a_session_of_another_company_and_never_syncs_implicitly(e):
    uid = svc.accept_invitation(e.session, invite(e, "m@example.com", "lity").token).user_id
    other = e.company_session("salcomp")
    with pytest.raises(svc.InvalidInput):
        svc.sync_company_user(e.session, other, uid, "lity")
    assert other.query(CompanyUser).count() == 0
    assert e.company_session("lity").query(CompanyUser).count() == 0  # acceptance synced nothing


def test_sync_failure_is_controlled_and_retry_succeeds(e):
    uid = svc.accept_invitation(e.session, invite(e, "m@example.com").token).user_id
    company = e.company_session("lity")
    company.execute(text("ALTER TABLE users RENAME TO users_away"))
    company.commit()
    with pytest.raises(svc.LocalSyncError) as exc:
        svc.sync_company_user(e.session, company, uid, "lity")
    assert "m@example.com" not in str(exc.value) and str(e.tmp_path) not in str(exc.value)
    assert svc.company_access(e.session, uid, "lity").role == "sales_member"  # central membership unaffected
    company.execute(text("ALTER TABLE users_away RENAME TO users"))
    company.commit()
    assert svc.sync_company_user(e.session, company, uid, "lity").id == uid


def test_local_references_survive_deactivation_and_username_refreshes(e):
    uid = svc.accept_invitation(e.session, invite(e, "m@example.com").token).user_id
    company = e.company_session("lity")
    svc.sync_company_user(e.session, company, uid, "lity")
    company.execute(text("UPDATE users SET username = 'stale'"))
    company.commit()
    svc.sync_company_user(e.session, company, uid, "lity")
    assert company.get(CompanyUser, uid).username == "m@example.com"
    e.session.execute(text("UPDATE identity_users SET is_active = 0 WHERE id = :i"), {"i": uid})
    e.session.commit()
    assert company.get(CompanyUser, uid) is not None  # history keeps its person


# --- structure ---

def test_company_user_holds_no_credentials_or_authorization():
    assert set(CompanyBase.metadata.tables["users"].columns.keys()) == {"id", "username", "full_name", "created_at", "updated_at"}


def test_no_foreign_key_crosses_the_databases():
    identity = set(IdentityBase.metadata.tables)
    for table in CompanyBase.metadata.tables.values():
        assert all(fk.column.table.name not in identity for fk in table.foreign_keys)
    for table in IdentityBase.metadata.tables.values():
        assert all(fk.column.table.name in identity for fk in table.foreign_keys)


def test_identity_schema_is_untouched(e):
    assert set(inspect(e.engine).get_table_names()) >= set(IdentityBase.metadata.tables)
    assert e.session.execute(text("PRAGMA user_version")).scalar() == 2


def test_importing_has_no_side_effects(tmp_path):
    code = (
        f"import sys; sys.path.insert(0, {str(BACKEND)!r})\n"
        "import sqlite3\n"
        "def boom(*a, **k): raise AssertionError('connect')\n"
        "sqlite3.connect = boom\n"
        "import identity_service\n"
        "assert 'database' not in sys.modules and 'main' not in sys.modules\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_nothing_in_the_application_imports_the_service():
    callers = [
        p.name for p in BACKEND.glob("*.py")
        if not p.name.startswith("test_") and p.name not in ("identity_service.py", "central_auth.py", "central_deps.py", "identity_admin_cli.py", "e2e_central_seed.py", "seed_synthetic_homolog.py")
        and any(f"{kw} identity_service" in p.read_text() for kw in ("import", "from"))
    ]
    assert callers == []


# --- Phase 2C: invitation authority is re-read, never assumed ---

def _force_admin_invitation(e, issued, role="admin"):
    """The schema stores the reserved admin role; only a direct write can plant one."""
    e.session.execute(text("UPDATE identity_invitations SET role = :r WHERE id = :i"), {"r": role, "i": issued.invitation_id})
    e.session.commit()


def test_super_admin_issues_all_three_company_roles_and_super_admin_is_never_invitable(e):
    assert invite(e, "a@example.com", role="sales_member").role == "sales_member"
    assert invite(e, "b@example.com", role="visitor").role == "visitor"
    assert invite(e, "d@example.com", role="admin").role == "admin"
    for role in (svc.SUPER_ADMIN_ROLE, svc.ADMIN_ROLE, "sales_member", "visitor", ""):
        assert svc.can_grant_admin(role) is (role == svc.SUPER_ADMIN_ROLE)  # only the Super Admin may issue Admin invitations
    for role in ("super_admin", "global_admin", "owner", "Admin", ""):
        with pytest.raises(svc.InvalidInput):
            invite(e, "c@example.com", role=role)
    assert e.session.query(IdentityInvitation).count() == 3


def test_a_company_admin_cannot_issue_an_admin_invitation(e):
    uid = svc.create_user(e.session, "ca@example.com").id
    e.session.add(IdentityMembership(
        user_id=uid, company_id=svc.get_company(e.session, "lity").id, role="admin", created_at=svc._now()))
    e.session.commit()
    for role in ("admin", "visitor", "sales_member"):
        with pytest.raises(svc.NotAuthorized):
            svc.create_invitation(e.session, uid, "x@example.com", "lity", role)
    assert e.session.query(IdentityInvitation).count() == 0


def test_an_admin_invitation_is_accepted_as_admin_and_the_membership_role_is_persisted(e):
    issued = invite(e, "newadmin@example.com", role="admin")
    uid = svc.create_user(e.session, "newadmin@example.com").id
    acceptance = svc.accept_invitation(e.session, issued.token, user_id=uid)
    assert acceptance.role == "admin"
    membership = e.session.scalar(select(IdentityMembership).where(IdentityMembership.user_id == uid))
    assert membership.role == "admin" and membership.is_active
    access = svc.company_access(e.session, uid, "lity")
    assert access.role == "admin" and not svc.can_manage_members(access.role) and not svc.can_grant_admin(access.role)
    assert not svc.is_super(access.role)  # an Admin is not a Super Admin: no member, board, product, import or delete power


def test_company_admin_membership_cannot_invite(e):
    uid = svc.create_user(e.session, "ca@example.com").id
    e.session.add(IdentityMembership(
        user_id=uid, company_id=svc.get_company(e.session, "lity").id, role="admin", created_at=svc._now()))
    e.session.commit()
    assert svc.can_manage_members(svc.company_access(e.session, uid, "lity").role) is False
    with pytest.raises(svc.NotAuthorized):
        svc.create_invitation(e.session, uid, "x@example.com", "lity", "visitor")
    assert e.session.query(IdentityInvitation).count() == 0


def test_inviter_demoted_or_deactivated_cannot_issue(e):
    e.session.execute(text("UPDATE identity_users SET is_global_admin = 0 WHERE id = :i"), {"i": e.admin})
    e.session.commit()
    with pytest.raises(svc.NotAuthorized):
        invite(e)
    assert e.session.query(IdentityInvitation).count() == 0


def test_acceptance_fails_after_inviter_is_deactivated_and_nothing_is_claimed(e):
    issued = invite(e)
    e.session.execute(text("UPDATE identity_users SET is_active = 0 WHERE id = :i"), {"i": e.admin})
    e.session.commit()
    with pytest.raises(svc.InvitationInvalid):
        svc.accept_invitation(e.session, issued.token)
    assert e.session.query(IdentityMembership).count() == 0
    assert e.session.scalar(select(IdentityInvitation.accepted_at)) is None
    assert e.session.scalar(select(IdentityAuthToken.consumed_at)) is None
    assert e.session.query(IdentityUser).count() == 2  # no user was created for the invitee


def test_an_admin_invitation_planted_under_a_non_super_admin_inviter_is_never_accepted(e):
    issued = invite(e)
    _force_admin_invitation(e, issued, "admin")
    other = svc.create_user(e.session, "ca@example.com").id
    e.session.add(IdentityMembership(
        user_id=other, company_id=svc.get_company(e.session, "lity").id, role="admin", created_at=svc._now()))
    e.session.execute(text("UPDATE identity_invitations SET invited_by_user_id = :u WHERE id = :i"), {"u": other, "i": issued.invitation_id})
    e.session.commit()
    with pytest.raises(svc.InvitationInvalid):
        svc.accept_invitation(e.session, issued.token)
    assert e.session.query(IdentityMembership).count() == 1  # only the planting company Admin's own


def test_a_super_admin_role_planted_in_an_invitation_is_never_accepted(e):
    issued = invite(e)
    e.session.execute(text("PRAGMA ignore_check_constraints = ON"))
    _force_admin_invitation(e, issued, "super_admin")
    e.session.execute(text("PRAGMA ignore_check_constraints = OFF"))
    with pytest.raises(svc.InvitationInvalid):
        svc.accept_invitation(e.session, issued.token)
    assert e.session.query(IdentityMembership).count() == 0


def test_a_lapsed_admin_membership_comes_back_only_with_the_role_the_invitation_names(e):
    uid = svc.create_user(e.session, "old@example.com").id
    e.session.add(IdentityMembership(
        user_id=uid, company_id=svc.get_company(e.session, "lity").id, role="admin", is_active=False,
        created_at=svc._now()))
    e.session.commit()
    issued = invite(e, "old@example.com")  # an ordinary Sales Member invitation
    svc.accept_invitation(e.session, issued.token, user_id=uid)
    membership = e.session.scalar(select(IdentityMembership))
    assert membership.role == "sales_member" and membership.is_active  # the old Admin role is not revived


def test_company_mismatch_and_dangling_references_fail_closed(e):
    issued = invite(e)
    other = svc.get_company(e.session, "valor-log")
    # a token that points at no invitation, and an invitation whose company is not a supported slug
    e.session.execute(text("PRAGMA foreign_keys = OFF"))
    e.session.execute(text("UPDATE identity_companies SET slug = 'rogue' WHERE id = :i"), {"i": other.id})
    e.session.execute(text("UPDATE identity_invitations SET company_id = :c WHERE id = :i"),
                      {"c": other.id, "i": issued.invitation_id})
    e.session.commit()
    with pytest.raises(svc.InvitationInvalid):
        svc.accept_invitation(e.session, issued.token)
    assert e.session.query(IdentityMembership).count() == 0


def test_failed_acceptances_reveal_nothing_about_accounts(e):
    issued = invite(e, "known@example.com")
    errors = set()
    for token, uid in [("nope", None), (issued.token, e.plain), (issued.token, 9999), (issued.token, e.admin)]:
        with pytest.raises(svc.InvitationInvalid) as exc:
            svc.accept_invitation(e.session, token, user_id=uid)
        errors.add(str(exc.value))
    assert errors == {"the invitation is not valid"}
    svc.accept_invitation(e.session, issued.token)  # the rightful acceptance still works after the failures


def test_concurrent_acceptance_after_revoked_authority_creates_nothing(e):
    issued = invite(e)
    e.session.execute(text("UPDATE identity_users SET is_global_admin = 0 WHERE id = :i"), {"i": e.admin})
    e.session.commit()
    outcomes, barrier = [], threading.Barrier(3)

    def run():
        session = e.factory()
        try:
            barrier.wait()
            svc.accept_invitation(session, issued.token)
            outcomes.append("ok")
        except svc.InvitationInvalid:
            outcomes.append("invalid")
        finally:
            session.close()

    threads = [threading.Thread(target=run) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes == ["invalid"] * 3
    assert e.session.query(IdentityMembership).count() == 0
