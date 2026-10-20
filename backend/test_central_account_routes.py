"""/api/central: me, logout, password forgot/reset, invitation accept and admin invite. Disposable databases only."""
import pytest
from sqlalchemy import text

import central_routes
import mailer
import rate_limit
from test_central_login import GOOD, Setup
from identity_models import IdentityCompany, IdentityInvitation, IdentityMembership, IdentityUser
import identity_service as svc
import central_auth as ca


@pytest.fixture
def s(tmp_path, monkeypatch):
    setup = Setup(tmp_path)
    with setup.factory() as session:
        for slug, name in (("salcomp", "Salcomp"), ("valor-log", "Valor Log")):
            session.add(IdentityCompany(slug=slug, name=name))
        admin = IdentityUser(email="admin@example.com", is_global_admin=True, password_hash=ca.hash_password(GOOD),
                             email_verified_at=svc._now())
        session.add(admin)
        session.commit()
    sent = []
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: sent.append((to, subject, body)))
    setup.sent = sent
    rate_limit.reset() if hasattr(rate_limit, "reset") else None
    yield setup
    setup.runtime.dispose()
    setup.engine.dispose()


def bearer(setup, email, password=GOOD):
    return {"Authorization": "Bearer " + setup.login(email, password).json()["access_token"]}


def test_me_lists_only_companies_with_access(s):
    with s.factory() as session:
        admin_id = svc.get_user_by_email(session, "admin@example.com").id
        token = svc.create_invitation(session, admin_id, "v@example.com", "lity", "visitor").token
        ca.accept_invitation_with_password(session, token, GOOD)
    visitor = s.client.get("/api/central/me", headers=bearer(s, "v@example.com")).json()
    assert visitor == {"email": "v@example.com", "is_global_admin": False,
                       "companies": [{"slug": "lity", "name": "Lity", "role": "visitor"}]}
    admin = s.client.get("/api/central/me", headers=bearer(s, "admin@example.com")).json()
    assert admin["is_global_admin"] and {c["slug"] for c in admin["companies"]} == {"lity", "salcomp", "valor-log"}
    assert {c["role"] for c in admin["companies"]} == {"super_admin"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer junk"}])
def test_me_and_logout_need_a_valid_token(s, headers):
    assert s.client.get("/api/central/me", headers=headers).status_code == 401
    assert s.client.post("/api/central/logout", headers=headers).status_code == 401


def test_logout_ends_every_session(s):
    h = bearer(s, "ok@example.com")
    assert s.client.get("/api/central/me", headers=h).status_code == 200
    assert s.client.post("/api/central/logout", headers=h).status_code == 204
    assert s.client.get("/api/central/me", headers=h).status_code == 401


def test_forgot_answers_identically_and_mails_only_eligible_accounts(s):
    known = s.client.post("/api/central/password/forgot", json={"email": "ok@example.com"})
    unknown = s.client.post("/api/central/password/forgot", json={"email": "nobody@example.com"})
    off = s.client.post("/api/central/password/forgot", json={"email": "off@example.com"})
    assert known.status_code == unknown.status_code == off.status_code == 200
    assert known.json() == unknown.json() == off.json()
    assert [m[0] for m in s.sent] == ["ok@example.com"]


def test_reset_flow_single_use_and_policy(s):
    s.client.post("/api/central/password/forgot", json={"email": "ok@example.com"})
    token = s.sent[0][2].split("central_reset_token=")[1].split()[0]
    assert s.client.post("/api/central/password/reset", json={"token": token, "new_password": "weak"}).status_code == 422
    assert s.client.post("/api/central/password/reset", json={"token": token, "new_password": "N3w!Passw0rdxx"}).status_code == 200
    assert s.client.post("/api/central/password/reset", json={"token": token, "new_password": "N3w!Passw0rdyy"}).status_code == 400
    assert s.login("ok@example.com", GOOD).status_code == 401
    assert s.login("ok@example.com", "N3w!Passw0rdxx").status_code == 200


def test_admin_invite_then_accept_creates_membership(s):
    h = bearer(s, "admin@example.com")
    r = s.client.post("/api/central/companies/lity/invitations", headers=h, json={"email": "n@example.com", "role": "sales_member"})
    assert r.status_code == 202 and "token" not in r.text
    token = s.sent[-1][2].split("central_invite_token=")[1].split()[0]
    assert s.client.post("/api/central/invitations/accept", json={"token": token, "password": "weak"}).status_code == 422
    assert s.client.post("/api/central/invitations/accept", json={"token": token, "password": GOOD}).status_code == 200
    assert s.client.post("/api/central/invitations/accept", json={"token": token, "password": GOOD}).status_code == 400
    me = s.client.get("/api/central/me", headers=bearer(s, "n@example.com")).json()
    assert me["companies"] == [{"slug": "lity", "name": "Lity", "role": "sales_member"}]


def test_only_global_admin_may_invite_and_slug_must_be_known(s):
    with s.factory() as session:
        admin_id = svc.get_user_by_email(session, "admin@example.com").id
        ca.accept_invitation_with_password(session, svc.create_invitation(session, admin_id, "m@example.com", "lity", "sales_member").token, GOOD)
    body = {"email": "x@example.com", "role": "visitor"}
    assert s.client.post("/api/central/companies/lity/invitations", headers=bearer(s, "m@example.com"), json=body).status_code == 403
    assert s.client.post("/api/central/companies/lity/invitations", json=body).status_code == 401
    assert s.client.post("/api/central/companies/nope/invitations", headers=bearer(s, "admin@example.com"), json=body).status_code == 403
    assert s.client.post("/api/central/companies/lity/invitations", headers=bearer(s, "admin@example.com"),
                         json={"email": "x@example.com", "role": "admin"}).status_code == 422
    with s.factory() as session:
        assert session.query(IdentityMembership).count() == 1


def test_expired_reset_token_is_rejected(s):
    from datetime import timedelta
    from identity_models import IdentityAuthToken
    s.client.post("/api/central/password/forgot", json={"email": "ok@example.com"})
    token = s.sent[0][2].split("central_reset_token=")[1].split()[0]
    with s.factory() as session:
        for row in session.query(IdentityAuthToken).all():
            row.created_at = row.created_at - timedelta(hours=3)
            row.expires_at = row.expires_at - timedelta(hours=3)
        session.commit()
    assert s.client.post("/api/central/password/reset", json={"token": token, "new_password": "N3w!Passw0rdxx"}).status_code == 400
    assert s.login("ok@example.com", GOOD).status_code == 200  # the old password still works


def test_forgot_answers_the_same_when_delivery_fails(s, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise OSError("smtp down")
    monkeypatch.undo()  # the fixture's send_email stub must not hide the real delivery path
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
    monkeypatch.setattr(mailer.smtplib, "SMTP", broken)
    ok = s.client.post("/api/central/password/forgot", json={"email": "ok@example.com"})
    unknown = s.client.post("/api/central/password/forgot", json={"email": "nobody@example.com"})
    assert ok.status_code == unknown.status_code == 200 and ok.json() == unknown.json()
    assert "ok@example.com" not in caplog.text and "central_reset_token" not in caplog.text


# --- an existing account joins a second company ---------------------------------------------------------------

def make_member(s, email, slug="lity", role="sales_member"):
    with s.factory() as session:
        admin_id = svc.get_user_by_email(session, "admin@example.com").id
        ca.accept_invitation_with_password(session, svc.create_invitation(session, admin_id, email, slug, role).token, GOOD)


def invite(s, email, slug, role):
    with s.factory() as session:
        admin_id = svc.get_user_by_email(session, "admin@example.com").id
        return svc.create_invitation(session, admin_id, email, slug, role).token


def join(s, token, email, password=GOOD):
    return s.client.post("/api/central/invitations/join", json={"token": token, "email": email, "password": password})


def test_existing_account_joins_a_second_company_with_its_own_password(s):
    make_member(s, "ana@example.com")
    token = invite(s, "ana@example.com", "valor-log", "visitor")
    assert join(s, token, "ana@example.com", "Wr0ng!pass-word").status_code == 401
    assert join(s, token, "ana@example.com").status_code == 200
    me = s.client.get("/api/central/me", headers=bearer(s, "ana@example.com")).json()
    assert {c["slug"]: c["role"] for c in me["companies"]} == {"lity": "sales_member", "valor-log": "visitor"}
    assert join(s, token, "ana@example.com").status_code == 400  # single use


def test_join_refuses_someone_elses_invitation_and_nothing_changes(s):
    make_member(s, "ana@example.com")
    make_member(s, "bob@example.com")
    token = invite(s, "ana@example.com", "valor-log", "visitor")
    assert join(s, token, "bob@example.com").status_code == 400
    bob = s.client.get("/api/central/me", headers=bearer(s, "bob@example.com")).json()
    assert {c["slug"] for c in bob["companies"]} == {"lity"}
    assert join(s, token, "ana@example.com").status_code == 200  # the rightful invitee can still use it


def test_invite_authority_is_read_from_the_database_not_the_token(s, monkeypatch):
    h = bearer(s, "admin@example.com")  # a valid session issued while the user was a global admin
    body = {"email": "x@example.com", "role": "visitor"}
    with s.factory() as session:
        session.execute(text("UPDATE identity_users SET is_global_admin = 0 WHERE email = 'admin@example.com'"))
        session.commit()
    import company_database

    def boom(*_a, **_k):
        raise AssertionError("a company database was opened before authorization")

    monkeypatch.setattr(company_database, "open_company_engine", boom)
    assert s.client.post("/api/central/companies/lity/invitations", headers=h, json=body).status_code == 403
    with s.factory() as session:
        assert session.query(IdentityInvitation).count() == 0


def test_expired_invitation_link_is_refused_and_creates_nothing(s):
    from datetime import timedelta

    with s.factory() as session:
        admin_id = svc.get_user_by_email(session, "admin@example.com").id
        issued = svc.create_invitation(session, admin_id, "late@example.com", "lity", "sales_member",
                                       now=svc._now() - timedelta(days=30))
    r = s.client.post("/api/central/invitations/accept", json={"token": issued.token, "password": GOOD})
    assert r.status_code == 400 and issued.token not in r.text
    assert s.client.post("/api/central/invitations/accept", json={"token": "not-a-token", "password": GOOD}).status_code == 400
    with s.factory() as session:
        assert svc.get_user_by_email(session, "late@example.com") is None
        assert session.query(IdentityMembership).count() == 0


def test_invitation_to_one_company_grants_nothing_in_another(s):
    h = bearer(s, "admin@example.com")
    s.client.post("/api/central/companies/lity/invitations", headers=h, json={"email": "n@example.com", "role": "sales_member"})
    token = s.sent[-1][2].split("central_invite_token=")[1].split()[0]
    assert s.client.post("/api/central/invitations/accept", json={"token": token, "password": GOOD}).status_code == 200
    n = bearer(s, "n@example.com")
    assert [c["slug"] for c in s.client.get("/api/central/me", headers=n).json()["companies"]] == ["lity"]
    assert s.client.get("/api/central/companies/salcomp/members", headers=n).status_code == 403
    assert s.client.get("/api/central/companies/lity/members", headers=n).status_code == 403  # a sales member is no manager
    assert s.client.put("/api/central/companies/lity/members/1/role", headers=n, json={"role": "visitor"}).status_code == 403
