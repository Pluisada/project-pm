"""Company Admin limit (2, pending invitations reserve a seat) and single global administrator.

Synthetic identities and disposable tmp_path databases only; no company database is opened."""
import threading
from datetime import timedelta

import pytest
from sqlalchemy import select, text

import central_auth as ca
import identity_admin_cli as cli
import identity_service as svc
from identity_models import IdentityAuditEvent, IdentityInvitation, IdentityMembership, IdentityUser
from test_central_login import GOOD
from test_identity_admin_cli import session  # noqa: F401  (fixture)
from test_membership_management import add, membership, s  # noqa: F401  (fixture/helpers)


def invite_admin(s, email, slug="lity"):
    return svc.create_invitation(s.session, s.super_id, email, slug, "admin")


def race(s, ops):
    barrier, outcomes = threading.Barrier(len(ops)), []

    def run(op):
        session = s.factory()
        try:
            barrier.wait()
            op(session)
            outcomes.append("ok")
        except svc.IdentityServiceError as exc:
            outcomes.append(type(exc).__name__)
        finally:
            session.close()

    threads = [threading.Thread(target=run, args=(op,)) for op in ops]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return outcomes


# --- company Admin limit ---------------------------------------------------------------------------------------

def test_first_and_second_admin_are_allowed_and_a_third_is_refused_everywhere(s):
    add(s, "a1@example.com", role="admin")
    invite_admin(s, "a2@example.com")  # a pending invitation takes the second seat
    m = add(s, "m@example.com")
    with pytest.raises(svc.Conflict, match="maximum of 2 Admins"):
        invite_admin(s, "a3@example.com")
    with pytest.raises(svc.Conflict, match="maximum of 2 Admins"):
        svc.change_membership_role(s.session, s.super_id, "lity", m, "admin")
    assert membership(s, m).role == "sales_member"
    assert s.session.scalar(select(IdentityInvitation).where(IdentityInvitation.email == "a3@example.com")) is None


def test_the_limit_is_per_company_and_the_super_admin_uses_no_seat(s):
    add(s, "a1@example.com", role="admin")
    add(s, "a2@example.com", role="admin")
    invite_admin(s, "other@example.com", slug="salcomp")  # another company is unaffected
    assert svc.active_global_admin_count(s.session) == 1  # and the Super Admin is not among the two Admins
    assert svc.admin_capacity_used(s.session, svc.get_company(s.session, "lity").id, svc._now()) == 2


def test_expired_revoked_and_accepted_invitations_do_not_reserve_a_seat(s):
    add(s, "a1@example.com", role="admin")
    old = svc._now() - timedelta(days=30)
    expired = svc.create_invitation(s.session, s.super_id, "x@example.com", "lity", "admin", now=old)
    revoked = invite_admin(s, "y@example.com")
    assert expired.expires_at < svc._now()
    with pytest.raises(svc.Conflict):  # a1 + y
        invite_admin(s, "z@example.com")
    svc.revoke_invitation(s.session, s.super_id, revoked.invitation_id)
    invite_admin(s, "z@example.com")  # the expired and the revoked ones reserved nothing
    with pytest.raises(svc.Conflict):  # a1 + z
        invite_admin(s, "w@example.com")


def test_accepting_an_admin_invitation_converts_the_reservation_without_exceeding_the_limit(s):
    add(s, "a1@example.com", role="admin")
    token = invite_admin(s, "a2@example.com").token
    company_id = svc.get_company(s.session, "lity").id
    assert svc.admin_capacity_used(s.session, company_id, svc._now()) == 2
    uid = ca.accept_invitation_with_password(s.session, token, GOOD).user_id
    assert membership(s, uid).role == "admin"
    assert svc.admin_capacity_used(s.session, company_id, svc._now()) == 2
    with pytest.raises(svc.Conflict):
        invite_admin(s, "a3@example.com")


def test_accepting_is_refused_when_the_company_was_pushed_over_the_limit_meanwhile(s):
    add(s, "a1@example.com", role="admin")
    token = invite_admin(s, "a2@example.com").token
    company_id = svc.get_company(s.session, "lity").id
    m = add(s, "m@example.com")  # forced past the cap by a direct write, as a data fix or an old row could
    s.session.execute(text("UPDATE identity_memberships SET role='admin' WHERE user_id=:u"), {"u": m})
    s.session.commit()
    with pytest.raises(ca.InvitationInvalid):
        ca.accept_invitation_with_password(s.session, token, GOOD)
    assert s.session.scalar(select(IdentityUser).where(IdentityUser.email == "a2@example.com")) is None
    assert svc.admin_capacity_used(s.session, company_id, svc._now()) == 3  # nothing was demoted


def test_a_lapsed_admin_membership_cannot_be_reactivated_into_a_third_seat(s):
    a1 = add(s, "a1@example.com", role="admin")
    svc.deactivate_membership(s.session, s.super_id, "lity", a1)
    add(s, "a2@example.com", role="admin")
    add(s, "a3@example.com", role="admin")
    with pytest.raises(svc.Conflict):
        invite_admin(s, "a1@example.com")  # reactivation only happens through an invitation: refused at the source
    assert membership(s, a1).is_active is False
    # a non-Admin invitation still reactivates it without taking an Admin seat
    token = svc.create_invitation(s.session, s.super_id, "a1@example.com", "lity", "visitor").token
    svc.accept_invitation(s.session, token, user_id=a1)
    assert membership(s, a1).is_active and membership(s, a1).role == "visitor"


def test_deactivating_or_cancelling_an_admin_frees_the_seat(s):
    a1 = add(s, "a1@example.com", role="admin")
    a2 = add(s, "a2@example.com", role="admin")
    with pytest.raises(svc.Conflict):
        invite_admin(s, "a3@example.com")
    svc.deactivate_membership(s.session, s.super_id, "lity", a1)
    invite_admin(s, "a3@example.com")
    ca.cancel_user(s.session, s.super_id, a2)
    invite_admin(s, "a4@example.com")  # a2's membership was ended by the cancellation: one seat free again


def test_concurrent_invitations_and_promotions_never_exceed_the_limit(s):
    add(s, "a1@example.com", role="admin")
    members = [add(s, f"m{i}@example.com") for i in range(3)]
    ops = [lambda x, i=i: svc.create_invitation(x, s.super_id, f"c{i}@example.com", "lity", "admin") for i in range(2)]
    ops += [lambda x, m=m: svc.change_membership_role(x, s.super_id, "lity", m, "admin") for m in members]
    outcomes = race(s, ops)
    assert outcomes.count("ok") == 1 and set(outcomes) <= {"ok", "Conflict"}, outcomes
    s.session.expire_all()
    assert svc.admin_capacity_used(s.session, svc.get_company(s.session, "lity").id, svc._now()) == 2


def test_concurrent_acceptances_of_one_admin_invitation_on_a_full_company_succeed_once(s):
    token = invite_admin(s, "a1@example.com").token
    add(s, "a0@example.com", role="admin")  # the company is now full: one member plus the pending invitation
    outcomes = race(s, [lambda x: ca.accept_invitation_with_password(x, token, GOOD)] * 2)
    assert outcomes.count("ok") == 1
    s.session.expire_all()
    assert svc.admin_capacity_used(s.session, svc.get_company(s.session, "lity").id, svc._now()) == 2


def test_over_limit_data_is_reported_and_never_changed(s):
    for i in range(2):
        add(s, f"a{i}@example.com", role="admin")
    extra = add(s, "extra@example.com")
    s.session.execute(text("UPDATE identity_memberships SET role='admin' WHERE user_id=:u"), {"u": extra})
    s.session.commit()
    before = s.session.execute(text("SELECT id, role, is_active FROM identity_memberships ORDER BY id")).all()
    report = svc.over_limit_report(s.session)
    assert report["companies_over_admin_limit"] == [
        {"company": "lity", "active_admins": 3, "pending_admin_invitations": 0}]
    assert report["active_global_admins"] == []
    assert s.session.execute(text("SELECT id, role, is_active FROM identity_memberships ORDER BY id")).all() == before


# --- single global administrator -------------------------------------------------------------------------------

def test_zero_global_admins_is_allowed_and_the_first_can_be_granted(session):
    assert svc.active_global_admin_count(session) == 0
    assert svc.over_limit_report(session)["active_global_admins"] == []
    user, created = svc.grant_global_admin(session, "Owner@Example.com")
    assert created and user.is_global_admin and svc.active_global_admin_count(session) == 1
    again, created = svc.grant_global_admin(session, "owner@example.com")
    assert not created and again.id == user.id


def test_a_second_global_admin_cannot_be_created_through_bootstrap(session):
    cli.bootstrap(session, "owner@example.com")
    with pytest.raises(svc.Conflict, match="maximum of 1 global administrator"):
        cli.bootstrap(session, "second@example.com")
    assert session.query(IdentityUser).count() == 1


def test_an_ordinary_member_is_never_promoted_to_global_admin(session):
    cli.bootstrap(session, "owner@example.com")
    owner = session.query(IdentityUser).one().id
    token = svc.create_invitation(session, owner, "m@example.com", "lity", "admin").token
    ca.accept_invitation_with_password(session, token, GOOD)
    with pytest.raises(svc.Conflict):
        svc.grant_global_admin(session, "m@example.com")
    assert session.query(IdentityUser).filter_by(is_global_admin=True).count() == 1
    assert session.query(IdentityMembership).filter_by(role="admin").count() == 1  # a company Admin is no Super Admin


def test_disabling_the_global_admin_leaves_consistent_state_and_allows_a_replacement(session):
    cli.bootstrap(session, "owner@example.com")
    owner = session.query(IdentityUser).one().id
    session.execute(text("UPDATE identity_users SET is_active = 0 WHERE id = :i"), {"i": owner})
    session.commit()
    assert svc.company_access(session, owner, "lity") is None  # an inactive Super Admin has no access anywhere
    assert svc.active_global_admin_count(session) == 0
    svc.grant_global_admin(session, "next@example.com")  # zero active: a new one may be granted
    assert svc.active_global_admin_count(session) == 1
    with pytest.raises(svc.Conflict):
        svc.grant_global_admin(session, "third@example.com")


def test_multiple_existing_global_admins_are_reported_not_demoted(session):
    cli.bootstrap(session, "owner@example.com")
    session.add(IdentityUser(email="legacy2@example.com", is_global_admin=True))
    session.commit()
    ids = [u.id for u in session.query(IdentityUser).order_by(IdentityUser.id)]
    assert svc.over_limit_report(session)["active_global_admins"] == ids
    assert session.query(IdentityUser).filter_by(is_global_admin=True, is_active=True).count() == 2
    with pytest.raises(svc.Conflict):
        svc.grant_global_admin(session, "third@example.com")


def test_concurrent_bootstraps_create_exactly_one_global_admin(s):
    s.session.execute(text("UPDATE identity_users SET is_active = 0"))
    s.session.commit()
    ops = [lambda x, i=i: svc.grant_global_admin(x, f"g{i}@example.com") for i in range(4)]
    outcomes = race(s, ops)
    assert outcomes.count("ok") == 1 and set(outcomes) <= {"ok", "Conflict"}, outcomes
    s.session.expire_all()
    assert svc.active_global_admin_count(s.session) == 1


def test_concurrent_acceptance_and_promotions_never_exceed_the_limit(s):
    token = invite_admin(s, "pending@example.com").token  # one seat reserved by the invitation
    m, n = add(s, "m@example.com"), add(s, "n@example.com")
    ops = [lambda x: ca.accept_invitation_with_password(x, token, GOOD),
           lambda x: svc.change_membership_role(x, s.super_id, "lity", m, "admin"),
           lambda x: svc.change_membership_role(x, s.super_id, "lity", n, "admin")]
    outcomes = race(s, ops)
    assert outcomes.count("ok") == 2 and set(outcomes) <= {"ok", "Conflict", "InvitationInvalid"}, outcomes
    s.session.expire_all()
    admins = s.session.scalars(select(IdentityMembership).where(
        IdentityMembership.role == "admin", IdentityMembership.is_active.is_(True))).all()
    assert len(admins) == 2 and svc.admin_capacity_used(s.session, svc.get_company(s.session, "lity").id, svc._now()) == 2


def test_no_production_module_grants_global_admin_outside_the_central_mechanism():
    """grant_global_admin is the only writer of is_global_admin = True; seeds, CLIs and routes must go through it."""
    import re
    from pathlib import Path
    backend = Path(__file__).parent
    pattern = re.compile(r"is_global_admin\s*(=|:=)\s*True|is_global_admin\.is_\(True\)\s*,\s*#grant", re.I)
    offenders = []
    for path in backend.glob("*.py"):
        if path.name.startswith("test_") or path.name in ("conftest.py", "admin_cli.py", "main.py", "models.py", "routes.py"):
            continue  # the legacy single-database runtime has its own admin role and no identity users
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line) and "==" not in line and path.name != "identity_service.py":
                offenders.append(f"{path.name}:{number}")
    assert offenders == [], offenders
    text = (backend / "identity_service.py").read_text()
    assert len(re.findall(r"IdentityUser\(email=email, is_global_admin=True", text)) == 1  # exactly one grant site
