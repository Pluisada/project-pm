"""Permanent cancellation of a central identity (central_auth.cancel_user) and the re-invitation of the same address.

Synthetic data and disposable databases only; no mail is sent. Cancelling keeps the identity row (so history keeps
resolving) but removes everything that grants access, and frees the email for a brand-new identity.
"""
import threading

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import central_auth as ca
import identity_service as svc
from identity_models import IdentityAuditEvent, IdentityAuthToken, IdentityInvitation, IdentityMembership, IdentityUser
from models import Board, Card, CompanyBase, CompanyUser, Column
from test_central_auth import GOOD, OTHER, Env


@pytest.fixture
def e(tmp_path):
    env = Env(tmp_path)
    yield env
    env.session.close()
    env.engine.dispose()


def _membership(e, uid, slug):
    return e.session.scalar(select(IdentityMembership).join(svc.IdentityCompany).where(
        IdentityMembership.user_id == uid, svc.IdentityCompany.slug == slug))


def test_cancelled_user_cannot_authenticate_or_use_an_old_session_and_has_no_access(e):
    uid = e.member("gone@example.com")
    token = ca.authenticate(e.session, "gone@example.com", GOOD).access_token
    ca.cancel_user(e.session, e.admin, uid)
    with pytest.raises(ca.AuthenticationFailed):
        ca.authenticate(e.session, "gone@example.com", GOOD)  # the email no longer names anyone
    with pytest.raises(ca.AuthenticationFailed):
        ca.user_for_token(e.session, token)
    user = e.user(uid)
    assert user.password_hash is None and user.is_active is False and user.deactivated_at is not None
    assert svc.company_access(e.session, uid, "lity") is None
    assert not _membership(e, uid, "lity").is_active


def test_cancellation_invalidates_reset_and_invitation_tokens(e):
    uid = e.member("gone@example.com")
    reset = ca.issue_password_reset(e.session, "gone@example.com")
    pending = e.invite("gone@example.com", slug="valor-log")  # an open invitation addressed to the same address
    ca.cancel_user(e.session, e.admin, uid)
    with pytest.raises(ca.ResetTokenInvalid):
        ca.reset_password(e.session, reset, OTHER)
    with pytest.raises(svc.InvitationInvalid):
        ca.accept_invitation_with_password(e.session, pending, OTHER)
    assert e.session.scalar(select(func.count()).select_from(IdentityAuthToken).where(
        IdentityAuthToken.consumed_at.is_(None), IdentityAuthToken.invalidated_at.is_(None))) == 0


def test_same_email_can_be_invited_again_and_gets_a_new_identity_that_inherits_nothing(e):
    old = e.member("back@example.com", GOOD)
    ca.cancel_user(e.session, e.admin, old)
    assert e.user(old).email == f"cancelled-{old}@{ca.CANCELLED_EMAIL_DOMAIN}"
    issued = svc.create_invitation(e.session, e.admin, " BACK@example.com ", "valor-log", "visitor")
    fresh = ca.accept_invitation_with_password(e.session, issued.token, OTHER).user_id
    assert fresh != old
    assert svc.get_user_by_email(e.session, "back@example.com").id == fresh
    with pytest.raises(ca.AuthenticationFailed):
        ca.authenticate(e.session, "back@example.com", GOOD)  # the old password is gone
    assert ca.authenticate(e.session, "back@example.com", OTHER).user_id == fresh
    assert svc.company_access(e.session, fresh, "valor-log").role == "visitor"
    assert svc.company_access(e.session, fresh, "lity") is None  # the old Lity membership did not come back
    assert e.session.scalars(select(IdentityMembership.company_id).where(IdentityMembership.user_id == fresh)).all() \
        == [svc.get_company(e.session, "valor-log").id]
    assert svc.company_access(e.session, old, "lity") is None  # the old identity stays powerless
    assert e.user(old).password_hash is None


def test_first_password_form_works_for_the_re_invited_address(e):
    old = e.member("back@example.com")
    ca.cancel_user(e.session, e.admin, old)
    token = e.invite("back@example.com", slug="lity")
    assert ca.accept_invitation_with_password(e.session, token, OTHER).user_id != old


def test_removing_one_company_membership_does_not_cancel_the_identity(e):
    uid = e.member("two@example.com")
    svc.accept_invitation(e.session, e.invite("two@example.com", slug="valor-log"), user_id=uid)
    svc.deactivate_membership(e.session, e.admin, "lity", uid)
    user = e.user(uid)
    assert user.is_active and user.password_hash is not None and user.email == "two@example.com"
    assert svc.company_access(e.session, uid, "lity") is None
    assert svc.company_access(e.session, uid, "valor-log").role == "sales_member"
    assert ca.authenticate(e.session, "two@example.com", GOOD).user_id == uid


def test_only_a_global_admin_may_cancel_and_not_themselves_an_admin_or_twice(e):
    uid = e.member("a@example.com")
    other = e.member("b@example.com")
    with pytest.raises(svc.NotAuthorized):
        ca.cancel_user(e.session, uid, other)
    with pytest.raises(svc.Conflict):
        ca.cancel_user(e.session, e.admin, e.admin)
    with pytest.raises(svc.NotFound):
        ca.cancel_user(e.session, e.admin, 9999)
    second_admin = svc.IdentityUser(email="root2@example.com", is_global_admin=True, password_hash=ca.hash_password(GOOD),
                                    email_verified_at=svc._now())
    e.session.add(second_admin)
    e.session.commit()
    with pytest.raises(svc.NotAuthorized):
        ca.cancel_user(e.session, e.admin, second_admin.id)
    ca.cancel_user(e.session, e.admin, uid)
    with pytest.raises(svc.Conflict):
        ca.cancel_user(e.session, e.admin, uid)
    assert e.user(other).is_active  # nobody else was touched
    assert e.user(other).email == "b@example.com"


def test_email_uniqueness_is_still_enforced_between_live_identities(e):
    e.member("live@example.com")
    e.session.add(IdentityUser(email="live@example.com"))
    with pytest.raises(IntegrityError):
        e.session.commit()
    e.session.rollback()


def test_cancellation_is_audited_without_personal_data_and_keeps_foreign_keys_consistent(e):
    uid = e.member("gone@example.com")
    ca.cancel_user(e.session, e.admin, uid)
    event = e.session.scalar(select(IdentityAuditEvent).where(IdentityAuditEvent.action == "user_cancelled"))
    assert event.target_user_id == uid and event.actor_user_id == e.admin
    assert "gone@example.com" not in str(event.details)
    assert e.session.execute(text("PRAGMA foreign_key_check")).all() == []
    assert e.session.scalar(select(func.count()).select_from(IdentityInvitation).where(
        IdentityInvitation.email == "gone@example.com")) == 0  # the address no longer appears on closed invitations


def test_cards_creators_and_totals_survive_cancellation_and_the_new_identity_has_its_own_history(e):
    uid = e.member("gone@example.com")
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    CompanyBase.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as company:
        company.add(CompanyUser(id=uid, username="gone@example.com"))
        company.add(Board(user_id=uid, title="B"))
        company.flush()
        column = Column(board_id=1, title="Proposal")
        company.add(column)
        company.flush()
        company.add_all([Card(column_id=column.id, title="Deal", salesperson_id=uid, deal_value=1000.0),
                         Card(column_id=column.id, title="Deal 2", salesperson_id=uid, deal_value=250.0)])
        company.commit()
        before = company.execute(text("SELECT id, title, salesperson_id, deal_value FROM cards ORDER BY id")).all()

        ca.cancel_user(e.session, e.admin, uid)
        new_id = ca.accept_invitation_with_password(e.session, e.invite("gone@example.com"), OTHER).user_id

        assert company.execute(text("SELECT id, title, salesperson_id, deal_value FROM cards ORDER BY id")).all() == before
        totals = dict(company.execute(text("SELECT salesperson_id, SUM(deal_value) FROM cards GROUP BY salesperson_id")).all())
        assert totals == {uid: 1250.0}  # still attributed to the old identity
        assert new_id not in totals and new_id != uid
        assert company.get(CompanyUser, uid).username == "gone@example.com"  # the display name of the history is kept
    engine.dispose()


def test_concurrent_cancellations_succeed_once_and_a_racing_acceptance_cannot_revive_access(e):
    uid = e.member("race@example.com")
    outcomes = []
    barrier = threading.Barrier(3)

    def cancel():
        session = e.factory()
        try:
            barrier.wait()
            ca.cancel_user(session, e.admin, uid)
            outcomes.append("cancelled")
        except svc.IdentityServiceError as exc:
            outcomes.append(type(exc).__name__)
        finally:
            session.close()

    token = e.invite("race@example.com", slug="valor-log")

    def accept():
        session = e.factory()
        try:
            barrier.wait()
            ca.accept_invitation_with_password(session, token, OTHER)
            outcomes.append("accepted")
        except svc.IdentityServiceError as exc:
            outcomes.append(type(exc).__name__)
        finally:
            session.close()

    threads = [threading.Thread(target=cancel), threading.Thread(target=cancel), threading.Thread(target=accept)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes.count("cancelled") == 1
    e.session.expire_all()
    # whichever order won, the cancelled identity has no access and no usable password, and no address is shared
    assert svc.company_access(e.session, uid, "lity") is None and svc.company_access(e.session, uid, "valor-log") is None
    assert e.user(uid).password_hash is None
    live = e.session.scalars(select(IdentityUser.email).where(IdentityUser.is_active.is_(True))).all()
    assert len(live) == len(set(live))


@pytest.mark.parametrize("address", [
    "cancelled-1@cancelled.invalid", "cancelled-1@CANCELLED.INVALID", "  Cancelled-1@Cancelled.Invalid ",
    "x@cancelled.invalid.", "x@sub.cancelled.invalid",
])
def test_reserved_cancellation_domain_cannot_be_invited_or_normalized(e, address):
    with pytest.raises(svc.InvalidInput):
        svc.normalize_email(address)
    with pytest.raises(svc.InvalidInput):
        svc.create_invitation(e.session, e.admin, address, "lity", "visitor")
    assert e.session.scalar(select(func.count()).select_from(IdentityInvitation)) == 0


def test_ordinary_addresses_still_normalize_including_lookalike_domains(e):
    assert svc.normalize_email(" Ana@Example.COM ") == "ana@example.com"
    for ok in ("a@cancelled.com", "a@notcancelled.invalid", "a@cancelled-invalid.org", "cancelled-1@example.invalid"):
        assert svc.normalize_email(ok) == ok


def test_cancellation_succeeds_even_after_an_attempt_to_invite_its_placeholder(e):
    uid = e.member("target@example.com")
    with pytest.raises(svc.InvalidInput):
        svc.create_invitation(e.session, e.admin, f"cancelled-{uid}@{ca.CANCELLED_EMAIL_DOMAIN}", "lity", "visitor")
    ca.cancel_user(e.session, e.admin, uid)
    assert e.user(uid).email == f"cancelled-{uid}@{ca.CANCELLED_EMAIL_DOMAIN}" and not e.user(uid).is_active
    # the placeholder cannot be used to sign in or to request a reset either
    with pytest.raises(ca.AuthenticationFailed):
        ca.authenticate(e.session, e.user(uid).email, GOOD)
    assert ca.issue_password_reset(e.session, e.user(uid).email) is None


def test_invitation_authorization_and_tokens_are_unchanged_by_the_reserved_domain_rule(e):
    uid = e.member("plain@example.com")
    with pytest.raises(svc.NotAuthorized):
        svc.create_invitation(e.session, uid, "someone@example.com", "lity", "visitor")  # a member still cannot invite
    token = e.invite("new@example.com", slug="valor-log")
    assert ca.accept_invitation_with_password(e.session, token, OTHER).user_id
    with pytest.raises(svc.InvitationInvalid):
        ca.accept_invitation_with_password(e.session, token, OTHER)  # single use
    with pytest.raises(svc.InvitationInvalid):
        ca.accept_invitation_with_password(e.session, "not-a-real-token", OTHER)
