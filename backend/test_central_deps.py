"""central_deps: company-scoped authorization context from the central identity database.

Disposable tmp_path identity databases only. Company databases are never created or opened.
"""
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import jwt
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker

import auth
import central_auth as ca
import central_deps as cd
import identity_service as svc
from auth import create_access_token, hash_password
from create_identity_db import create_identity_database
from database_targets import COMPANY_SLUGS, company_env_name
from identity_database import create_identity_session_factory, identity_db_dependency, open_identity_engine
from identity_models import IdentityCompany, IdentityMembership, IdentityUser

GOOD = "Str0ng!Passw0rd"
BACKEND = Path(__file__).parent


class Env:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
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

    def member(self, email, slug="lity", role="sales_member"):
        token = svc.create_invitation(self.session, self.admin, email, slug, role).token
        return ca.accept_invitation_with_password(self.session, token, GOOD).user_id

    def token(self, email):
        return ca.authenticate(self.session, email, GOOD).access_token

    def update(self, model, where, **values):
        with self.factory() as s:
            s.execute(update(model).where(where).values(**values))
            s.commit()
        self.session.expire_all()


@pytest.fixture
def e(tmp_path):
    env = Env(tmp_path)
    yield env
    env.session.close()
    env.engine.dispose()


def ctx(e, email, slug="lity"):
    return cd.resolve_company_context(e.session, e.token(email), slug)


# --- context --------------------------------------------------------------

def test_admin_context_for_every_active_company(e):
    for slug in COMPANY_SLUGS:
        c = ctx(e, "admin@example.com", slug)
        assert (c.user_id, c.is_global_admin, c.company_slug, c.role, c.read_only) == (
            e.admin, True, slug, "super_admin", False)


def test_member_and_visitor_contexts(e):
    m = e.member("m@example.com")
    v = e.member("v@example.com", role="visitor")
    cm, cv = ctx(e, "m@example.com"), ctx(e, "v@example.com")
    assert (cm.user_id, cm.is_global_admin, cm.role, cm.read_only) == (m, False, "sales_member", False)
    assert (cv.user_id, cv.is_global_admin, cv.role, cv.read_only) == (v, False, "visitor", True)


def test_context_has_no_secret_fields(e):
    assert set(cd.CompanyContext.__dataclass_fields__) == {
        "user_id", "is_global_admin", "company_slug", "role", "read_only"}
    with pytest.raises(AttributeError):
        ctx(e, "admin@example.com").role = "visitor"


# --- tokens ---------------------------------------------------------------

def test_invalid_tokens_rejected(e):
    user = e.session.get(IdentityUser, e.admin)
    legacy = create_access_token({"sub": str(user.id), "pwh": auth.password_fingerprint(user.password_hash),
                                  "sv": user.session_version})
    good = e.token("admin@example.com")
    forged = jwt.encode({"sub": str(e.admin), "purpose": ca.ACCESS_TOKEN_PURPOSE}, "x" * 40, algorithm="HS256")
    for bad in (legacy, good + "x", forged, "", "garbage", None):
        with pytest.raises(ca.AuthenticationFailed):
            cd.resolve_company_context(e.session, bad, "lity")


def test_revoked_session_rejected(e):
    token = e.token("admin@example.com")
    ca.logout(e.session, e.admin)
    with pytest.raises(ca.AuthenticationFailed):
        cd.resolve_company_context(e.session, token, "lity")


def test_inactive_user_rejected(e):
    e.member("m@example.com")
    token = e.token("m@example.com")
    e.update(IdentityUser, IdentityUser.email == "m@example.com", is_active=False)
    with pytest.raises(ca.AuthenticationFailed):
        cd.resolve_company_context(e.session, token, "lity")


def test_admin_flag_is_read_from_database_not_token(e):
    m = e.member("m@example.com")
    token = e.token("m@example.com")
    # a forged admin claim in a validly signed token changes nothing
    user = e.session.get(IdentityUser, m)
    forged = auth.create_purpose_token(
        str(m), ca.ACCESS_TOKEN_PURPOSE, auth.timedelta(minutes=5),
        pwh=auth.password_fingerprint(user.password_hash), sv=user.session_version,
        is_global_admin=True, role="admin")
    for t in (token, forged):
        c = cd.resolve_company_context(e.session, t, "lity")
        assert (c.is_global_admin, c.role) == (False, "sales_member")
        with pytest.raises(cd.CompanyAccessDenied):
            cd.resolve_company_context(e.session, t, "salcomp")
    # promotion in the database takes effect on the same token
    e.update(IdentityUser, IdentityUser.id == m, is_global_admin=True)
    assert cd.resolve_company_context(e.session, token, "salcomp").is_global_admin
    # demotion of the admin takes effect too
    admin_token = e.token("admin@example.com")
    e.update(IdentityUser, IdentityUser.id == e.admin, is_global_admin=False)
    with pytest.raises(cd.CompanyAccessDenied):
        cd.resolve_company_context(e.session, admin_token, "lity")


# --- company scoping ------------------------------------------------------

def test_membership_does_not_cross_companies(e):
    e.member("m@example.com", slug="lity")
    for slug in ("salcomp", "valor-log"):
        with pytest.raises(cd.CompanyAccessDenied):
            ctx(e, "m@example.com", slug)


def test_inactive_membership_and_company_rejected(e):
    e.member("m@example.com")
    token = e.token("m@example.com")
    e.update(IdentityMembership, IdentityMembership.id > 0, is_active=False)
    with pytest.raises(cd.CompanyAccessDenied):
        cd.resolve_company_context(e.session, token, "lity")
    e.update(IdentityMembership, IdentityMembership.id > 0, is_active=True)
    assert cd.resolve_company_context(e.session, token, "lity").role == "sales_member"
    e.update(IdentityCompany, IdentityCompany.slug == "lity", is_active=False)
    with pytest.raises(cd.CompanyAccessDenied):
        cd.resolve_company_context(e.session, token, "lity")
    with pytest.raises(cd.CompanyAccessDenied):  # an active global admin is not exempt from an inactive company
        cd.resolve_company_context(e.session, e.token("admin@example.com"), "lity")


@pytest.mark.parametrize("slug", ["nope", "", "LITY", "lity ", "../lity", "valor_log"])
def test_unknown_slug_fails_closed(e, slug):
    with pytest.raises(cd.CompanyAccessDenied):
        cd.resolve_company_context(e.session, e.token("admin@example.com"), slug)


def test_missing_company_row_fails_closed(e):
    with e.factory() as s:
        s.execute(IdentityCompany.__table__.delete().where(IdentityCompany.slug == "valor-log"))
        s.commit()
    with pytest.raises(cd.CompanyAccessDenied):
        ctx(e, "admin@example.com", "valor-log")


def test_user_without_membership_denied(e):
    with e.factory() as s:
        s.add(IdentityUser(email="loner@example.com", password_hash=hash_password(GOOD),
                           email_verified_at=svc._now()))
        s.commit()
    for slug in COMPANY_SLUGS:
        with pytest.raises(cd.CompanyAccessDenied):
            ctx(e, "loner@example.com", slug)


def test_company_local_user_never_grants_access(e):
    from models import CompanyBase, CompanyUser
    with e.factory() as s:
        s.add(IdentityUser(email="loner@example.com", password_hash=hash_password(GOOD),
                           email_verified_at=svc._now()))
        s.commit()
    uid = svc.get_user_by_email(e.session, "loner@example.com").id
    # an in-memory company-style database holding a CompanyUser for the same id
    engine = create_engine("sqlite://")
    CompanyBase.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as cs:
        cs.add(CompanyUser(id=uid, username="loner@example.com"))
        cs.commit()
    with pytest.raises(cd.CompanyAccessDenied):
        ctx(e, "loner@example.com", "lity")


def test_role_never_inferred_from_legacy_role(e):
    e.member("v@example.com", role="visitor")
    assert ctx(e, "v@example.com").role == "visitor"
    import inspect
    assert "UserRole" not in inspect.getsource(cd) and "models" not in inspect.getsource(cd)


# --- opportunity gate -----------------------------------------------------

def test_can_modify_opportunity(e):
    m = e.member("m@example.com")
    e.member("v@example.com", role="visitor")
    admin, member, visitor = (ctx(e, x) for x in ("admin@example.com", "m@example.com", "v@example.com"))
    for assigned in (None, m, 999):
        assert cd.can_modify_opportunity(admin, assigned)
        assert not cd.can_modify_opportunity(visitor, assigned)
    assert cd.can_modify_opportunity(member, m)
    assert not cd.can_modify_opportunity(member, 999)
    assert not cd.can_modify_opportunity(member, None)


# --- FastAPI dependency ---------------------------------------------------

def client_for(e):
    app = FastAPI()
    closed = []

    def spy_dependency():
        gen = identity_db_dependency(e.factory)()
        session = next(gen)
        try:
            yield session
        finally:
            gen.close()
            closed.append(session)

    get_ctx = cd.company_context_dependency(spy_dependency)

    @app.get("/api/companies/{company_slug}/whoami")
    def whoami(context: cd.CompanyContext = Depends(get_ctx)):
        return {"user_id": context.user_id, "role": context.role, "read_only": context.read_only,
                "admin": context.is_global_admin}

    return TestClient(app), closed


def test_dependency_status_codes_and_session_release(e):
    e.member("m@example.com")
    client, closed = client_for(e)

    def get(slug, token=None, header=None):
        headers = {"Authorization": header or f"Bearer {token}"} if (token or header) else {}
        return client.get(f"/api/companies/{slug}/whoami", headers=headers)

    token = e.token("m@example.com")
    ok = get("lity", token)
    assert ok.status_code == 200 and ok.json()["role"] == "sales_member" and ok.json()["admin"] is False
    assert get("lity").status_code == 401
    assert get("lity", header="Basic abc").status_code == 401
    assert get("lity", header=f"Bearer {token} extra").status_code == 401
    assert get("lity", "garbage").status_code == 401
    assert get("salcomp", token).status_code == 403
    assert get("nope", token).status_code == 403
    assert get("salcomp", token).json() == get("nope", token).json()
    assert len(closed) == 9 and all(x.get_bind() is e.engine for x in closed)  # every request released its session


# --- boundaries -------------------------------------------------------------

def test_no_company_database_is_opened_or_created(e, monkeypatch):
    import company_database
    import identity_service

    def boom(*a, **k):
        raise AssertionError("a company database was opened")

    monkeypatch.setattr(company_database, "open_company_engine", boom)
    monkeypatch.setattr(identity_service, "sync_company_user", boom)
    e.member("m@example.com")
    ctx(e, "m@example.com")
    ctx(e, "admin@example.com", "salcomp")
    assert sorted(p.name for p in e.tmp_path.iterdir()) == ["identity.db"]


def test_import_has_no_side_effects_and_no_commercial_modules():
    code = (
        "import os, sys; os.environ['SECRET_KEY']='k'*40; sys.path.insert(0, sys.argv[1]);"
        "import central_deps;"
        "bad = {'database','deps','routes','main','company_database'} & set(sys.modules);"
        "sys.exit(f'imported {bad}' if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code, str(BACKEND)], capture_output=True, text=True,
                            cwd="/", env={"PATH": "/usr/bin:/bin"})
    assert result.returncode == 0, result.stderr


def test_only_company_modules_import_central_deps():
    callers = [
        p.name for p in BACKEND.glob("*.py")
        if not p.name.startswith("test_") and p.name != "central_deps.py"
        and any(f"{kw} central_deps" in p.read_text() for kw in ("import", "from"))
    ]
    assert sorted(callers) == ["central_routes.py", "company_routes.py", "company_runtime.py"]


def test_role_predicates_keep_only_the_global_admin_administrative():
    from identity_service import can_review, is_admin_level, is_super, read_only

    roles = ("super_admin", "sales_member", "visitor")
    assert [is_super(r) for r in roles] == [True, False, False]
    assert [is_admin_level(r) for r in roles] == [True, False, False]
    assert [can_review(r) for r in roles] == [True, False, False]
    assert [read_only(r) for r in roles] == [False, False, True]
    # the reserved company-Admin string grants nothing in Phase 1
    assert not (is_super("admin") or is_admin_level("admin") or can_review("admin") or read_only("admin"))


def test_card_rules_is_admin_judges_context_by_identity_flag_and_legacy_by_role():
    from types import SimpleNamespace
    from card_rules import is_admin

    assert is_admin(cd.CompanyContext(1, True, "lity", "super_admin", False))
    assert not is_admin(cd.CompanyContext(2, False, "lity", "admin", False))  # reserved string, no privilege
    assert not is_admin(cd.CompanyContext(3, False, "lity", "sales_member", False))
    assert not is_admin(cd.CompanyContext(4, False, "lity", "visitor", True))
    assert is_admin(SimpleNamespace(role="admin")) and not is_admin(SimpleNamespace(role="member"))


def test_member_management_and_admin_grant_policies():
    from identity_service import can_grant_admin, can_manage_members

    roles = ("super_admin", "sales_member", "visitor", "admin")
    assert [can_manage_members(r) for r in roles] == [True, False, False, False]
    assert [can_grant_admin(r) for r in roles] == [True, False, False, False]  # only the Super Admin may issue an Admin invitation


@pytest.mark.parametrize("role", [None, "", "ADMIN", "Super_Admin", " super_admin", "owner", 1, ["super_admin"]])
def test_malformed_roles_fail_closed(role):
    from identity_service import can_grant_admin, can_manage_members, can_review, is_admin_level, is_super, read_only

    assert not any(f(role) for f in (is_super, is_admin_level, can_review, can_manage_members, can_grant_admin))
    assert not read_only(role) or role == "visitor"


def test_context_policy_properties_by_role():
    def props(role, flag=False):
        c = cd.CompanyContext(1, flag, "lity", role, role == "visitor")
        return (c.is_super, c.is_admin_level, c.can_review, c.can_manage_members, c.can_grant_admin, c.read_only)

    assert props("super_admin", True) == (True, True, True, True, True, False)
    assert props("sales_member") == (False, False, False, False, False, False)
    assert props("visitor") == (False, False, False, False, False, True)
    assert props("admin") == (False, False, False, False, False, False)
    # the role string, not the flag, drives the context properties; a stray flag on a member grants nothing here
    assert props("sales_member", True)[:5] == (False,) * 5


def test_company_admin_membership_is_fail_closed(e):
    uid = e.member("boss@example.com")
    e.update(IdentityMembership, IdentityMembership.user_id == uid, role="admin")  # directly, as no service can
    c = ctx(e, "boss@example.com")
    assert c.role == "admin" and not c.is_global_admin and not c.read_only
    assert not (c.is_super or c.is_admin_level or c.can_review or c.can_manage_members or c.can_grant_admin)
    assert not cd.can_modify_opportunity(c, None) and not cd.can_modify_opportunity(c, 999)
    assert cd.can_modify_opportunity(c, uid) is False  # the reserved role is not even a Sales Member


def test_admin_in_one_company_has_nothing_in_another(e):
    uid = e.member("boss@example.com", slug="lity")
    e.update(IdentityMembership, IdentityMembership.user_id == uid, role="admin")
    with pytest.raises(cd.CompanyAccessDenied):
        ctx(e, "boss@example.com", slug="salcomp")
    other = [s for s in COMPANY_SLUGS if s != "lity"][0]
    with pytest.raises(cd.CompanyAccessDenied):
        ctx(e, "boss@example.com", slug=other)


def test_inactive_admin_membership_and_invitation_paths_fail_closed(e):
    uid = e.member("boss@example.com")
    e.update(IdentityMembership, IdentityMembership.user_id == uid, role="admin", is_active=False)
    with pytest.raises(cd.CompanyAccessDenied):
        ctx(e, "boss@example.com")
    for role in ("super_admin", "ADMIN", ""):
        with pytest.raises(svc.IdentityServiceError):
            svc.create_invitation(e.session, e.admin, "new@example.com", "lity", role)


def test_policy_ignores_legacy_role_strings():
    from types import SimpleNamespace

    from card_rules import is_admin

    assert is_admin(SimpleNamespace(role="admin"))  # legacy mode unchanged
    ctx_admin_string = cd.CompanyContext(5, False, "lity", "admin", False)
    assert not is_admin(ctx_admin_string) and not ctx_admin_string.can_manage_members
