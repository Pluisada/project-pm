"""Phase 2D: member and invitation listing, membership deactivation, role changes.

Synthetic identities and disposable tmp_path databases only. No company database is ever created or opened here
(a guard fails the test if one is), which is the "central only" requirement.
"""
import json
import threading

import pytest
from sqlalchemy import func, select, text

import central_auth as ca
import company_database
import identity_service as svc
import mailer
import rate_limit
from auth import hash_password
from card_rules import is_admin as cd_is_admin
from central_deps import CompanyContext
from identity_models import (
    IdentityAuditEvent, IdentityAuthToken, IdentityCompany, IdentityInvitation, IdentityMembership, IdentityUser,
)
from test_central_login import GOOD, Setup


@pytest.fixture
def s(tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a company database was opened")
    monkeypatch.setattr(company_database, "open_company_engine", refuse)
    monkeypatch.setattr(mailer, "send_email", lambda *a: None)
    setup = Setup(tmp_path)
    with setup.factory() as session:
        for slug, name in (("salcomp", "Salcomp"), ("valor-log", "Valor Log")):
            session.add(IdentityCompany(slug=slug, name=name))
        session.add(IdentityUser(email="super@example.com", is_global_admin=True, password_hash=hash_password(GOOD),
                                 email_verified_at=svc._now()))
        session.commit()
    setup.session = setup.factory()
    setup.super_id = svc.get_user_by_email(setup.session, "super@example.com").id
    yield setup
    setup.session.close()
    setup.runtime.dispose()
    setup.engine.dispose()


def add(s, email, slug="lity", role="sales_member"):
    """A synthetic active member with a password, created through the real invitation flow."""
    token = svc.create_invitation(s.session, s.super_id, email, slug, "sales_member").token
    uid = ca.accept_invitation_with_password(s.session, token, GOOD).user_id
    if role != "sales_member":
        svc.change_membership_role(s.session, s.super_id, slug, uid, role)
    return uid


def membership(s, uid, slug="lity"):
    s.session.expire_all()
    return s.session.scalar(select(IdentityMembership).join(IdentityCompany).where(
        IdentityMembership.user_id == uid, IdentityCompany.slug == slug))


def hdr(s, email):
    return {"Authorization": "Bearer " + s.login(email, GOOD).json()["access_token"]}


def audit(s, action):
    s.session.expire_all()
    return s.session.scalars(select(IdentityAuditEvent).where(IdentityAuditEvent.action == action)).all()


# --- listing ---------------------------------------------------------------

def test_super_admin_lists_members_and_pending_invitations_of_any_company(s):
    m = add(s, "m@example.com")
    add(s, "other@example.com", slug="salcomp")
    svc.create_invitation(s.session, s.super_id, "pending@example.com", "lity", "visitor")
    members = svc.list_members(s.session, s.super_id, "lity")
    assert [(x["user_id"], x["email"], x["role"], x["is_active"]) for x in members] == [(m, "m@example.com", "sales_member", True)]
    assert {x["email"] for x in svc.list_members(s.session, s.super_id, "salcomp")} == {"other@example.com"}
    pending = svc.list_pending_invitations(s.session, s.super_id, "lity")
    assert [(p["email"], p["role"]) for p in pending] == [("pending@example.com", "visitor")]


def test_listings_expose_no_secrets(s):
    add(s, "m@example.com")
    issued = svc.create_invitation(s.session, s.super_id, "p@example.com", "lity", "visitor")
    h = hdr(s, "super@example.com")
    body = s.client.get("/api/central/companies/lity/members", headers=h).text
    body += s.client.get("/api/central/companies/lity/invitations", headers=h).text
    token_hash = svc.hash_token(issued.token)
    for secret in (issued.token, token_hash, "password", "hash"):
        assert secret not in body.lower() or secret == "password" and False
    assert "pbkdf" not in body and "$2b$" not in body


def test_company_admin_lists_only_its_own_company(s):
    admin = add(s, "a@example.com", role="admin")
    add(s, "m@example.com")
    add(s, "x@example.com", slug="salcomp")
    svc.create_invitation(s.session, s.super_id, "p@example.com", "lity", "visitor")
    svc.create_invitation(s.session, s.super_id, "q@example.com", "salcomp", "visitor")
    h = hdr(s, "a@example.com")
    assert {x["email"] for x in s.client.get("/api/central/companies/lity/members", headers=h).json()} == {"a@example.com", "m@example.com"}
    assert [p["email"] for p in s.client.get("/api/central/companies/lity/invitations", headers=h).json()] == ["p@example.com"]
    for path in ("members", "invitations"):
        assert s.client.get(f"/api/central/companies/salcomp/{path}", headers=h).status_code == 403
    assert admin


@pytest.mark.parametrize("role", ["sales_member", "visitor"])
def test_members_and_visitors_cannot_manage_anything(s, role):
    add(s, "u@example.com", role=role)
    target = add(s, "t@example.com")
    h = hdr(s, "u@example.com")
    assert s.client.get("/api/central/companies/lity/members", headers=h).status_code == 403
    assert s.client.get("/api/central/companies/lity/invitations", headers=h).status_code == 403
    assert s.client.post(f"/api/central/companies/lity/members/{target}/deactivate", headers=h).status_code == 403
    assert s.client.put(f"/api/central/companies/lity/members/{target}/role", headers=h, json={"role": "visitor"}).status_code == 403
    assert membership(s, target).is_active and membership(s, target).role == "sales_member"


def test_unauthenticated_and_unknown_company_are_refused(s):
    assert s.client.get("/api/central/companies/lity/members").status_code == 401
    h = hdr(s, "super@example.com")
    assert s.client.get("/api/central/companies/acme/members", headers=h).status_code == 403


# --- deactivation ----------------------------------------------------------

def test_super_admin_deactivates_member_and_admin_keeping_everything_else(s):
    m = add(s, "m@example.com")
    a = add(s, "a@example.com", role="admin")
    other = svc.create_invitation(s.session, s.super_id, "m@example.com", "salcomp", "visitor").token
    ca.join_invitation(s.session, other, "m@example.com", GOOD)
    for uid in (m, a):
        svc.deactivate_membership(s.session, s.super_id, "lity", uid)
        assert membership(s, uid).is_active is False  # soft: the row is still there
    assert svc.company_access(s.session, m, "lity") is None
    assert svc.company_access(s.session, m, "salcomp").role == "visitor"  # other company retained
    assert s.session.get(IdentityUser, m).is_active
    assert s.login("m@example.com").status_code == 200  # the account still works
    with pytest.raises(svc.Conflict):
        svc.deactivate_membership(s.session, s.super_id, "lity", m)


def test_company_admin_cannot_deactivate_anyone(s):
    add(s, "a@example.com", role="admin")
    m, v = add(s, "m@example.com"), add(s, "v@example.com", role="visitor")
    outsider = add(s, "o@example.com", slug="salcomp")
    h = hdr(s, "a@example.com")
    for uid in (m, v):
        assert s.client.post(f"/api/central/companies/lity/members/{uid}/deactivate", headers=h).status_code == 403
        assert membership(s, uid).is_active
    assert s.client.post(f"/api/central/companies/salcomp/members/{outsider}/deactivate", headers=h).status_code == 403
    assert membership(s, outsider, "salcomp").is_active


def test_company_admin_cannot_touch_admins_itself_or_the_super_admin(s):
    a1, a2 = add(s, "a1@example.com", role="admin"), add(s, "a2@example.com", role="admin")
    h = hdr(s, "a1@example.com")
    for uid in (a2, a1, s.super_id):
        assert s.client.post(f"/api/central/companies/lity/members/{uid}/deactivate", headers=h).status_code == 403
        assert s.client.put(f"/api/central/companies/lity/members/{uid}/role", headers=h, json={"role": "visitor"}).status_code == 403
    assert membership(s, a1).is_active and membership(s, a2).is_active
    assert membership(s, a2).role == "admin" and s.session.get(IdentityUser, s.super_id).is_active


def test_the_global_admin_and_self_are_protected_even_from_the_super_admin(s):
    with pytest.raises(svc.NotAuthorized):
        svc.deactivate_membership(s.session, s.super_id, "lity", s.super_id)
    with pytest.raises(svc.NotAuthorized):
        svc.change_membership_role(s.session, s.super_id, "lity", s.super_id, "visitor")
    other_super = IdentityUser(email="s2@example.com", is_global_admin=True)
    s.session.add(other_super)
    s.session.commit()
    with pytest.raises(svc.NotAuthorized):
        svc.deactivate_membership(s.session, s.super_id, "lity", other_super.id)
    assert s.session.get(IdentityUser, s.super_id).is_active and s.session.get(IdentityUser, other_super.id).is_active


def test_deactivation_does_not_touch_users_other_memberships_or_missing_targets(s):
    with pytest.raises(svc.NotFound):
        svc.deactivate_membership(s.session, s.super_id, "lity", 9999)
    stranger = svc.create_user(s.session, "stranger@example.com").id
    with pytest.raises(svc.NotFound):  # a user without a membership here
        svc.deactivate_membership(s.session, s.super_id, "lity", stranger)


# --- role changes ----------------------------------------------------------

def test_super_admin_changes_roles_across_all_supported_roles(s):
    m = add(s, "m@example.com")
    for new in ("visitor", "admin", "sales_member", "admin", "visitor"):
        svc.change_membership_role(s.session, s.super_id, "lity", m, new)
        assert membership(s, m).role == new
    events = audit(s, "membership_role_changed")
    assert len(events) == 5 and json.loads(events[0].details)["old_role"] == "sales_member"


@pytest.mark.parametrize("bad", ["Admin", " admin", "admin ", "SALES_MEMBER", "owner", "super_admin", "", None, 1])
def test_invalid_roles_are_rejected(s, bad):
    m = add(s, "m@example.com")
    with pytest.raises(svc.InvalidInput):
        svc.change_membership_role(s.session, s.super_id, "lity", m, bad)
    assert membership(s, m).role == "sales_member"
    r = s.client.put(f"/api/central/companies/lity/members/{m}/role", headers=hdr(s, "super@example.com"), json={"role": bad if isinstance(bad, str) else "x"})
    assert r.status_code == 422


def test_role_change_is_per_company_and_needs_an_active_membership(s):
    m = add(s, "m@example.com")
    ca.join_invitation(s.session, svc.create_invitation(s.session, s.super_id, "m@example.com", "salcomp", "visitor").token, "m@example.com", GOOD)
    svc.change_membership_role(s.session, s.super_id, "lity", m, "admin")
    assert membership(s, m, "salcomp").role == "visitor"
    with pytest.raises(svc.Conflict):
        svc.change_membership_role(s.session, s.super_id, "lity", m, "admin")  # no-op change
    svc.deactivate_membership(s.session, s.super_id, "lity", m)
    with pytest.raises(svc.Conflict):
        svc.change_membership_role(s.session, s.super_id, "lity", m, "visitor")
    with pytest.raises(svc.NotFound):
        svc.change_membership_role(s.session, s.super_id, "valor-log", m, "visitor")


def test_company_admin_cannot_change_roles_and_cannot_promote(s):
    add(s, "a@example.com", role="admin")
    m = add(s, "m@example.com")
    with pytest.raises(svc.NotAuthorized):
        svc.change_membership_role(s.session, svc.get_user_by_email(s.session, "a@example.com").id, "lity", m, "admin")
    assert membership(s, m).role == "sales_member"


# --- admin invitations, privileges stay disabled ---------------------------

def test_admin_cannot_invite_and_admin_invitations_stay_impossible(s):
    a = add(s, "a@example.com", role="admin")
    with pytest.raises(svc.NotAuthorized):
        svc.create_invitation(s.session, a, "n@example.com", "lity", "visitor")
    r = s.client.post("/api/central/companies/lity/invitations", headers=hdr(s, "a@example.com"),
                      json={"email": "n@example.com", "role": "visitor"})
    assert r.status_code == 403
    with pytest.raises(svc.InvalidInput):
        svc.create_invitation(s.session, s.super_id, "n@example.com", "lity", "admin")


def test_admin_membership_grants_no_general_company_privilege(s):
    a = add(s, "a@example.com", role="admin")
    access = svc.company_access(s.session, a, "lity")
    context = CompanyContext(a, False, "lity", access.role, svc.read_only(access.role))
    assert access.role == "admin"
    assert not (context.is_super or context.is_admin_level or context.can_review
                or context.can_manage_members or context.can_grant_admin)
    assert not context.read_only  # unchanged Phase 2B behavior: at most Sales Member-like, never admin-level
    assert context.role not in ("super_admin",) and not cd_is_admin(context)  # get_admin_context refuses it
    assert a not in svc.assignable_user_ids(s.session, "lity")


# --- invitations closed on demotion / deactivation -------------------------

def _stray_admin_invitation(s, admin_id):
    """An open invitation issued by an Admin (the service cannot create one; inserted directly as a legacy/forged row)."""
    now = svc._now()
    s.session.add(IdentityInvitation(email="stray@example.com", company_id=svc.get_company(s.session, "lity").id,
                                     role="visitor", invited_by_user_id=admin_id, created_at=now,
                                     expires_at=now + svc.INVITATION_TTL))
    s.session.flush()
    inv = s.session.scalar(select(IdentityInvitation).where(IdentityInvitation.email == "stray@example.com"))
    raw = "stray-token-for-test"
    s.session.add(IdentityAuthToken(token_hash=svc.hash_token(raw), purpose="invitation", invitation_id=inv.id,
                                    created_at=now, expires_at=now + svc.INVITATION_TTL))
    s.session.commit()
    return inv.id, raw


@pytest.mark.parametrize("how", ["demote", "deactivate"])
def test_losing_admin_authority_closes_its_pending_invitations(s, how):
    a = add(s, "a@example.com", role="admin")
    inv_id, raw = _stray_admin_invitation(s, a)
    if how == "demote":
        svc.change_membership_role(s.session, s.super_id, "lity", a, "visitor")
    else:
        svc.deactivate_membership(s.session, s.super_id, "lity", a)
    s.session.expire_all()
    assert s.session.get(IdentityInvitation, inv_id).revoked_at is not None
    assert s.session.scalar(select(IdentityAuthToken.invalidated_at).where(IdentityAuthToken.invitation_id == inv_id)) is not None
    with pytest.raises(svc.InvitationInvalid):
        svc.accept_invitation(s.session, raw)
    assert svc.list_pending_invitations(s.session, s.super_id, "lity") == []


def test_acceptance_still_rejects_an_invitation_whose_inviter_lost_authority(s):
    a = add(s, "a@example.com", role="admin")
    inv_id, raw = _stray_admin_invitation(s, a)  # simulate an invitation that was somehow left open
    assert not svc._may_invite(s.session, a, "lity")  # backstop: an Admin never has invite authority
    with pytest.raises(svc.InvitationInvalid):
        svc.accept_invitation(s.session, raw)
    assert s.session.scalar(select(func.count()).select_from(IdentityMembership).where(
        IdentityMembership.user_id == svc.get_user_by_email(s.session, "stray@example.com").id
        if svc.get_user_by_email(s.session, "stray@example.com") else text("0"))) == 0
    assert inv_id


def test_existing_invitation_flow_is_intact(s):
    token = svc.create_invitation(s.session, s.super_id, "new@example.com", "lity", "visitor").token
    acc = ca.accept_invitation_with_password(s.session, token, GOOD)
    assert svc.company_access(s.session, acc.user_id, "lity").role == "visitor"
    # a deactivated member is reactivated only by a fresh valid invitation, with a non-admin role
    svc.deactivate_membership(s.session, s.super_id, "lity", acc.user_id)
    again = svc.create_invitation(s.session, s.super_id, "new@example.com", "lity", "sales_member").token
    svc.accept_invitation(s.session, again)
    assert svc.company_access(s.session, acc.user_id, "lity").role == "sales_member"


# --- audit and concurrency -------------------------------------------------

def test_audit_events_identify_actor_company_and_target_without_secrets(s):
    m = add(s, "m@example.com")
    a = add(s, "a@example.com", role="admin")
    company_id = svc.get_company(s.session, "lity").id
    svc.deactivate_membership(s.session, s.super_id, "lity", m)
    svc.change_membership_role(s.session, s.super_id, "lity", a, "visitor")
    dea, chg = audit(s, "membership_deactivated")[0], audit(s, "membership_role_changed")[-1]
    assert (dea.actor_user_id, dea.target_user_id, dea.company_id) == (s.super_id, m, company_id)
    assert (chg.actor_user_id, chg.target_user_id, chg.company_id) == (s.super_id, a, company_id)
    assert json.loads(chg.details) == {"invitations_closed": 0, "new_role": "visitor", "old_role": "admin"}
    assert all(len(e.action) <= 40 for e in s.session.scalars(select(IdentityAuditEvent)))
    blob = " ".join(e.details for e in s.session.scalars(select(IdentityAuditEvent)))
    assert "example.com" not in blob and "token" not in blob.replace("invitations_closed", "")


def test_refused_operations_write_no_audit_and_change_nothing(s):
    a = add(s, "a@example.com", role="admin")
    m = add(s, "m@example.com")
    before = len(audit(s, "membership_deactivated")) + len(audit(s, "membership_role_changed"))
    for call in (lambda: svc.deactivate_membership(s.session, a, "lity", a),
                 lambda: svc.change_membership_role(s.session, a, "lity", m, "visitor"),
                 lambda: svc.change_membership_role(s.session, s.super_id, "lity", m, "Admin")):
        with pytest.raises(svc.IdentityServiceError):
            call()
    assert len(audit(s, "membership_deactivated")) + len(audit(s, "membership_role_changed")) == before


def test_concurrent_role_changes_and_deactivation_fail_safely(s):
    m = add(s, "m@example.com")
    outcomes, barrier = [], threading.Barrier(4)
    ops = [lambda x: svc.change_membership_role(x, s.super_id, "lity", m, "visitor"),
           lambda x: svc.change_membership_role(x, s.super_id, "lity", m, "visitor"),
           lambda x: svc.deactivate_membership(x, s.super_id, "lity", m),
           lambda x: svc.deactivate_membership(x, s.super_id, "lity", m)]

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
    assert outcomes.count("ok") in (1, 2) and set(outcomes) <= {"ok", "Conflict"}
    final = membership(s, m)
    assert final.is_active is False and final.role in ("visitor", "sales_member")
    assert len(audit(s, "membership_deactivated")) == 1
    assert len(audit(s, "membership_role_changed")) == outcomes.count("ok") - 1


def test_concurrent_deactivation_and_admin_demotion_cannot_bypass_restrictions(s):
    """A Sales Member deactivation by an Admin racing the Admin's demotion: no state in between is exploitable."""
    a = add(s, "a@example.com", role="admin")
    m = add(s, "m@example.com")
    barrier, outcomes = threading.Barrier(2), []

    def deactivate():
        session = s.factory()
        try:
            barrier.wait()
            svc.deactivate_membership(session, a, "lity", m)
            outcomes.append("deactivated")
        except svc.IdentityServiceError as exc:
            outcomes.append(type(exc).__name__)
        finally:
            session.close()

    def demote():
        session = s.factory()
        try:
            barrier.wait()
            svc.change_membership_role(session, s.super_id, "lity", a, "visitor")
            outcomes.append("demoted")
        finally:
            session.close()

    threads = [threading.Thread(target=deactivate), threading.Thread(target=demote)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert "demoted" in outcomes and membership(s, a).role == "visitor"
    # either order is legal, but a demoted Admin must have had no effect after the demotion
    assert outcomes.count("deactivated") + outcomes.count("NotAuthorized") == 1
    assert membership(s, m).is_active == ("deactivated" not in outcomes)
