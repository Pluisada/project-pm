"""central_auth: credentials, tokens and sessions against the central identity database.

Disposable tmp_path databases only, created by the explicit initializers.
"""
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
import jwt
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError

import auth
import central_auth as ca
import identity_service as svc
from auth import create_access_token, hash_password, verify_password
from create_identity_db import create_identity_database
from database_targets import COMPANY_SLUGS, company_env_name
from identity_database import create_identity_session_factory, open_identity_engine
from identity_models import (
    IdentityAuditEvent, IdentityAuthToken, IdentityCompany, IdentityInvitation, IdentityMembership, IdentityUser,
)


def _unverified(token: str) -> dict:
    """The claims of a token without checking its signature or expiry (what python-jose called get_unverified_claims)."""
    return jwt.decode(token, options={"verify_signature": False})
from models import CompanyBase, CompanyUser

GOOD = "Str0ng!Passw0rd"
OTHER = "An0ther#Passw0rd"
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
            s.add(IdentityUser(email="admin@example.com", is_global_admin=True, password_hash=hash_password(GOOD),
                               email_verified_at=svc._now()))
            s.commit()
        self.session = self.factory()
        self.admin = svc.get_user_by_email(self.session, "admin@example.com").id

    def invite(self, email="new@example.com", slug="lity", role="sales_member"):
        return svc.create_invitation(self.session, self.admin, email, slug, role).token

    def member(self, email="m@example.com", password=GOOD):
        acceptance = ca.accept_invitation_with_password(self.session, self.invite(email), password)
        return acceptance.user_id

    def user(self, user_id):
        self.session.expire_all()
        return self.session.get(IdentityUser, user_id)


@pytest.fixture
def e(tmp_path):
    env = Env(tmp_path)
    yield env
    env.session.close()
    env.engine.dispose()


# --- authentication -------------------------------------------------------

def test_authenticate_success_and_token_validates(e):
    result = ca.authenticate(e.session, " ADMIN@example.com ", GOOD)
    assert result.user_id == e.admin
    assert ca.user_for_token(e.session, result.access_token).id == e.admin


def test_failures_are_indistinguishable(e):
    uid = e.member()
    with e.factory() as s:  # inactive
        s.add(IdentityUser(email="off@example.com", password_hash=hash_password(GOOD), is_active=False,
                           email_verified_at=svc._now()))
        s.add(IdentityUser(email="nopw@example.com", email_verified_at=svc._now()))
        s.add(IdentityUser(email="unverified@example.com", password_hash=hash_password(GOOD)))
        s.commit()
    cases = [("m@example.com", "wrong"), ("nobody@example.com", GOOD), ("off@example.com", GOOD),
             ("nopw@example.com", GOOD), ("unverified@example.com", GOOD), ("not-an-email", GOOD), ("", ""),
             ("m@example.com", None)]
    messages = set()
    for email, password in cases:
        with pytest.raises(ca.AuthenticationFailed) as exc:
            ca.authenticate(e.session, email, password)
        messages.add(str(exc.value))
    assert messages == {"authentication failed"}
    assert e.user(uid).is_active


def test_cost_is_equalized_for_unknown_accounts(e, monkeypatch):
    calls = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda p, h: calls.append(h) or real(p, h))
    for email in ("admin@example.com", "nobody@example.com"):
        with pytest.raises(ca.AuthenticationFailed):
            ca.authenticate(e.session, email, "wrong")
    assert len(calls) == 2


def test_legacy_username_is_not_an_identity(e):
    with pytest.raises(ca.AuthenticationFailed):
        ca.authenticate(e.session, "admin", GOOD)


# --- tokens: central and legacy never cross ------------------------------

def test_central_token_is_not_a_legacy_access_token(e):
    token = ca.authenticate(e.session, "admin@example.com", GOOD).access_token
    assert auth.decode_access_token(token) is None
    assert auth.verify_token(token) is None


def test_legacy_style_token_is_not_a_central_session(e):
    user = e.user(e.admin)
    legacy = create_access_token({"sub": str(user.id), "pwh": auth.password_fingerprint(user.password_hash),
                                  "sv": user.session_version})
    with pytest.raises(ca.AuthenticationFailed):
        ca.user_for_token(e.session, legacy)


def test_tampered_expired_and_malformed_tokens_rejected(e):
    user = e.user(e.admin)
    good = ca.authenticate(e.session, "admin@example.com", GOOD).access_token
    claims = {"sub": str(user.id), "purpose": ca.ACCESS_TOKEN_PURPOSE,
              "pwh": auth.password_fingerprint(user.password_hash), "sv": user.session_version}
    forged = jwt.encode(claims, "x" * 40, algorithm="HS256")
    expired = auth.create_purpose_token(str(user.id), ca.ACCESS_TOKEN_PURPOSE, timedelta(seconds=-5),
                                        pwh=claims["pwh"], sv=claims["sv"])
    no_pwh = auth.create_purpose_token(str(user.id), ca.ACCESS_TOKEN_PURPOSE, timedelta(hours=1), sv=claims["sv"])
    no_sv = auth.create_purpose_token(str(user.id), ca.ACCESS_TOKEN_PURPOSE, timedelta(hours=1), pwh=claims["pwh"])
    bool_sv = auth.create_purpose_token(str(user.id), ca.ACCESS_TOKEN_PURPOSE, timedelta(hours=1),
                                        pwh=claims["pwh"], sv=True)
    bad_sub = auth.create_purpose_token("abc", ca.ACCESS_TOKEN_PURPOSE, timedelta(hours=1), pwh="a", sv=0)
    for bad in (forged, expired, no_pwh, no_sv, bool_sv, bad_sub, "garbage", "", None, good + "x"):
        with pytest.raises(ca.AuthenticationFailed):
            ca.user_for_token(e.session, bad)
    assert ca.user_for_token(e.session, good).id == e.admin


def test_token_lifetime_is_the_existing_session_length(e):
    token = ca.authenticate(e.session, "admin@example.com", GOOD).access_token
    claims = _unverified(token)
    assert claims["exp"] - _unverified(auth.create_access_token({"sub": "x"}))["exp"] in range(-5, 5)
    assert set(claims) == {"sub", "purpose", "exp", "pwh", "sv"}  # nothing identifying beyond the id


# --- initial password for invited users ----------------------------------

def test_invited_user_sets_first_password(e):
    raw = e.invite()
    acceptance = ca.accept_invitation_with_password(e.session, raw, GOOD)
    user = e.user(acceptance.user_id)
    assert user.email == "new@example.com" and user.email_verified_at is not None
    assert verify_password(GOOD, user.password_hash) and GOOD not in user.password_hash
    assert svc.company_access(e.session, user.id, "lity").role == "sales_member"
    assert ca.authenticate(e.session, "new@example.com", GOOD).user_id == user.id


def test_invitation_token_is_single_use_and_second_try_changes_nothing(e):
    raw = e.invite()
    uid = ca.accept_invitation_with_password(e.session, raw, GOOD).user_id
    before = e.user(uid).password_hash
    with pytest.raises(svc.InvitationInvalid):
        ca.accept_invitation_with_password(e.session, raw, OTHER)
    assert e.user(uid).password_hash == before


def test_weak_password_does_not_consume_the_invitation(e):
    raw = e.invite()
    for weak in ("Sh0rt!A", "alllowercase1!", "ALLUPPERCASE1!", "NoDigits!!", "NoSymbol123a", "x" * 129, None):
        with pytest.raises(ca.WeakPassword):
            ca.accept_invitation_with_password(e.session, raw, weak)
    assert e.session.scalar(select(IdentityAuthToken.consumed_at)) is None
    assert ca.accept_invitation_with_password(e.session, raw, GOOD).company_slug == "lity"


def test_invalid_expired_revoked_invitation_tokens(e):
    now = svc._now()
    expired = svc.create_invitation(e.session, e.admin, "late@example.com", "lity", "visitor",
                                    now=now - timedelta(days=30)).token
    revoked = svc.create_invitation(e.session, e.admin, "rev@example.com", "lity", "visitor")
    svc.revoke_invitation(e.session, e.admin, revoked.invitation_id)
    for token in ("nonsense", "", expired, revoked.token):
        with pytest.raises(svc.InvitationInvalid):
            ca.accept_invitation_with_password(e.session, token, GOOD)
    assert e.session.scalar(select(IdentityUser).where(IdentityUser.email == "late@example.com")) is None


def test_user_who_already_has_a_password_cannot_overwrite_it(e):
    uid = e.member("m@example.com")
    before = e.user(uid).password_hash
    raw = e.invite("m@example.com", slug="valor-log")
    with pytest.raises(svc.InvitationInvalid):
        ca.accept_invitation_with_password(e.session, raw, OTHER)
    user = e.user(uid)
    assert user.password_hash == before
    token = e.session.scalar(select(IdentityAuthToken).where(IdentityAuthToken.token_hash == svc.hash_token(raw)))
    assert token.consumed_at is None  # rolled back: the person can still accept after signing in
    assert svc.accept_invitation(e.session, raw, user_id=uid).company_slug == "valor-log"


def test_failure_after_token_claim_rolls_everything_back(e, monkeypatch):
    raw = e.invite()

    def boom(*_a, **_k):
        raise SQLAlchemyError("boom")

    monkeypatch.setattr(ca, "_new_password", boom)
    with pytest.raises(SQLAlchemyError):
        ca.accept_invitation_with_password(e.session, raw, GOOD)
    assert e.session.scalar(select(IdentityUser).where(IdentityUser.email == "new@example.com")) is None
    assert e.session.scalar(select(IdentityMembership)) is None
    token = e.session.scalar(select(IdentityAuthToken).where(IdentityAuthToken.token_hash == svc.hash_token(raw)))
    assert token.consumed_at is None
    assert e.session.scalar(select(IdentityInvitation.accepted_at)) is None
    monkeypatch.undo()
    assert ca.accept_invitation_with_password(e.session, raw, GOOD).user_id


def test_plain_accept_invitation_still_leaves_no_password(e):
    uid = svc.accept_invitation(e.session, e.invite()).user_id
    assert e.user(uid).password_hash is None
    with pytest.raises(ca.AuthenticationFailed):
        ca.authenticate(e.session, "new@example.com", GOOD)


# --- reset ----------------------------------------------------------------

def test_reset_happy_path_ends_sessions(e):
    uid = e.member()
    old = ca.authenticate(e.session, "m@example.com", GOOD).access_token
    raw = ca.issue_password_reset(e.session, "M@example.com")
    assert ca.reset_password(e.session, raw, OTHER) == uid
    with pytest.raises(ca.AuthenticationFailed):
        ca.user_for_token(e.session, old)
    with pytest.raises(ca.AuthenticationFailed):
        ca.authenticate(e.session, "m@example.com", GOOD)
    new = ca.authenticate(e.session, "m@example.com", OTHER)
    assert ca.user_for_token(e.session, new.access_token).id == uid


def test_reset_token_single_use_expiry_invalid_and_superseded(e):
    e.member()
    first = ca.issue_password_reset(e.session, "m@example.com")
    second = ca.issue_password_reset(e.session, "m@example.com")
    with pytest.raises(ca.ResetTokenInvalid):  # superseded
        ca.reset_password(e.session, first, OTHER)
    ca.reset_password(e.session, second, OTHER)
    with pytest.raises(ca.ResetTokenInvalid):  # reused
        ca.reset_password(e.session, second, GOOD)
    stale = ca.issue_password_reset(e.session, "m@example.com", now=svc._now() - timedelta(hours=2))
    for bad in (stale, "nonsense", "", None):
        with pytest.raises(ca.ResetTokenInvalid):
            ca.reset_password(e.session, bad, GOOD)
    assert ca.authenticate(e.session, "m@example.com", OTHER)


def test_invitation_token_cannot_reset_and_reset_token_cannot_accept(e):
    e.member()
    reset = ca.issue_password_reset(e.session, "m@example.com")
    with pytest.raises(ca.ResetTokenInvalid):
        ca.reset_password(e.session, e.invite("x@example.com"), OTHER)
    with pytest.raises(svc.InvitationInvalid):
        ca.accept_invitation_with_password(e.session, reset, GOOD)


def test_weak_reset_password_keeps_the_link_usable(e):
    e.member()
    raw = ca.issue_password_reset(e.session, "m@example.com")
    with pytest.raises(ca.WeakPassword):
        ca.reset_password(e.session, raw, "weak")
    ca.reset_password(e.session, raw, OTHER)


def test_reset_request_gives_nothing_for_ineligible_accounts(e):
    uid = e.member()
    svc_user = e.user(uid)
    ca.deactivate_user(e.session, e.admin, uid)
    with e.factory() as s:
        s.add(IdentityUser(email="unverified@example.com"))
        s.commit()
    for email in ("m@example.com", "unverified@example.com", "nobody@example.com", "bad", ""):
        assert ca.issue_password_reset(e.session, email) is None
    assert svc_user is not None
    assert e.session.scalar(select(IdentityAuthToken).where(IdentityAuthToken.purpose == "password_reset")) is None


def test_reset_for_user_deactivated_after_issue_is_refused_and_rolled_back(e):
    uid = e.member()
    raw = ca.issue_password_reset(e.session, "m@example.com")
    before = e.user(uid).password_hash
    ca.deactivate_user(e.session, e.admin, uid)
    with pytest.raises(ca.ResetTokenInvalid):
        ca.reset_password(e.session, raw, OTHER)
    assert e.user(uid).password_hash == before


def test_invited_user_without_password_can_activate_through_reset(e):
    uid = svc.accept_invitation(e.session, e.invite()).user_id
    raw = ca.issue_password_reset(e.session, "new@example.com")
    ca.reset_password(e.session, raw, GOOD)
    assert ca.authenticate(e.session, "new@example.com", GOOD).user_id == uid


def test_reset_rolls_back_when_the_write_fails(e, monkeypatch):
    uid = e.member()
    raw = ca.issue_password_reset(e.session, "m@example.com")
    before = e.user(uid).password_hash

    def boom(*_a, **_k):
        raise SQLAlchemyError("boom")

    monkeypatch.setattr(ca, "_new_password", boom)
    with pytest.raises(SQLAlchemyError):
        ca.reset_password(e.session, raw, OTHER)
    monkeypatch.undo()
    assert e.user(uid).password_hash == before
    ca.reset_password(e.session, raw, OTHER)  # token was not burned


# --- change, logout, deactivation ----------------------------------------

def test_change_password(e):
    uid = e.member()
    old = ca.authenticate(e.session, "m@example.com", GOOD).access_token
    with pytest.raises(ca.AuthenticationFailed):
        ca.change_password(e.session, uid, "wrong", OTHER)
    with pytest.raises(ca.WeakPassword):
        ca.change_password(e.session, uid, GOOD, "weak")
    pending = ca.issue_password_reset(e.session, "m@example.com")
    fresh = ca.change_password(e.session, uid, GOOD, OTHER)
    with pytest.raises(ca.AuthenticationFailed):
        ca.user_for_token(e.session, old)
    assert ca.user_for_token(e.session, fresh.access_token).id == uid
    with pytest.raises(ca.ResetTokenInvalid):  # outstanding reset links die with a password change
        ca.reset_password(e.session, pending, GOOD)


def test_logout_ends_all_sessions(e):
    a = ca.authenticate(e.session, "admin@example.com", GOOD).access_token
    b = ca.authenticate(e.session, "admin@example.com", GOOD).access_token
    ca.logout(e.session, e.admin)
    for token in (a, b):
        with pytest.raises(ca.AuthenticationFailed):
            ca.user_for_token(e.session, token)
    assert ca.authenticate(e.session, "admin@example.com", GOOD)
    with pytest.raises(svc.NotFound):
        ca.logout(e.session, 9999)


def test_deactivation(e):
    uid = e.member()
    token = ca.authenticate(e.session, "m@example.com", GOOD).access_token
    reset = ca.issue_password_reset(e.session, "m@example.com")
    with pytest.raises(svc.NotAuthorized):  # a member cannot deactivate anyone
        ca.deactivate_user(e.session, uid, e.admin)
    with pytest.raises(svc.Conflict):
        ca.deactivate_user(e.session, e.admin, e.admin)
    with pytest.raises(svc.NotFound):
        ca.deactivate_user(e.session, e.admin, 9999)
    ca.deactivate_user(e.session, e.admin, uid)
    with pytest.raises(ca.AuthenticationFailed):
        ca.user_for_token(e.session, token)
    with pytest.raises(ca.AuthenticationFailed):
        ca.authenticate(e.session, "m@example.com", GOOD)
    with pytest.raises(ca.ResetTokenInvalid):
        ca.reset_password(e.session, reset, OTHER)
    assert svc.company_access(e.session, uid, "lity") is None
    with pytest.raises(svc.Conflict):
        ca.deactivate_user(e.session, e.admin, uid)
    assert e.user(uid).deactivated_at is not None


# --- isolation, hygiene, compatibility ------------------------------------

def test_foreign_keys_enforced_on_the_connection(e):
    assert e.session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_no_credentials_in_company_database_or_audit(e, tmp_path):
    uid = e.member()
    raw = ca.issue_password_reset(e.session, "m@example.com")
    ca.reset_password(e.session, raw, OTHER)
    ca.authenticate(e.session, "m@example.com", OTHER)
    assert "password_hash" not in {c.name for c in CompanyUser.__table__.columns}
    assert not any(c.name in {"password", "password_hash", "token", "token_hash"} for c in CompanyUser.__table__.columns)
    assert not any("identity" in t for t in CompanyBase.metadata.tables)
    dump = " ".join(str(r) for r in e.session.scalars(select(IdentityAuditEvent.details)))
    for secret in (GOOD, OTHER, raw, svc.hash_token(raw), e.user(uid).password_hash):
        assert secret not in dump
    actions = set(e.session.scalars(select(IdentityAuditEvent.action)))
    assert {"password_set", "reset_requested", "password_reset"} <= actions


def test_company_user_sync_still_works_and_grants_nothing(e):
    from company_database import company_session_factory, open_company_engine
    from create_company_db import create_company_database

    uid = e.member()
    create_company_database("lity", e.env)
    company = company_session_factory(open_company_engine("lity", e.env))()
    svc.sync_company_user(e.session, company, uid, "lity")
    ca.deactivate_user(e.session, e.admin, uid)
    assert company.get(CompanyUser, uid) is not None  # local row survives
    with pytest.raises(ca.AuthenticationFailed):  # and authorizes nothing
        ca.authenticate(e.session, "m@example.com", GOOD)
    company.close()


def test_no_fallback_to_a_commercial_database(e, monkeypatch):
    import database  # noqa: F401  (conftest points it at a throwaway file)

    monkeypatch.setattr("database.get_db", lambda: pytest.fail("commercial database consulted"))
    with pytest.raises(ca.AuthenticationFailed):
        ca.authenticate(e.session, "ghost@example.com", GOOD)


def test_statements_stay_on_identity_tables(e):
    seen = []
    event.listen(e.engine, "before_cursor_execute", lambda c, cur, stmt, *a: seen.append(stmt))
    e.member()
    ca.authenticate(e.session, "m@example.com", GOOD)
    assert seen and all("company_users" not in s and " users" not in s.replace("identity_users", "") for s in seen)


def test_import_has_no_side_effects_and_no_commercial_modules():
    code = (
        "import os, sys; os.environ['SECRET_KEY']='k'*40; sys.path.insert(0, sys.argv[1]);"
        "import central_auth;"
        "bad = {'database','deps','routes','main'} & set(sys.modules);"
        "sys.exit(f'imported {bad}' if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code, str(BACKEND)], capture_output=True, text=True,
                            cwd="/", env={"PATH": "/usr/bin:/bin"})
    assert result.returncode == 0, result.stderr


def test_only_the_central_login_route_imports_central_auth():
    callers = [
        p.name for p in BACKEND.glob("*.py")
        if not p.name.startswith("test_") and p.name not in ("central_auth.py", "central_deps.py", "central_routes.py", "identity_admin_cli.py", "e2e_central_seed.py", "seed_synthetic_homolog.py")
        and any(f"{kw} central_auth" in p.read_text() for kw in ("import", "from"))
    ]
    assert callers == []
