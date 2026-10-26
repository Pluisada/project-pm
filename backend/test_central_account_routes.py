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
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")  # the send itself is mocked; this only marks mail as configured
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body, html=None, inline_images=(): sent.append((to, subject, body, html)))
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


def test_invitation_email_is_branded_for_the_invitation_company(s):
    h = bearer(s, "admin@example.com")
    for slug, color in (("salcomp", "#be3124"), ("valor-log", "#b8450b")):
        r = s.client.post(f"/api/central/companies/{slug}/invitations", headers=h,
                          json={"email": f"{slug}@example.com", "role": "sales_member"})
        assert r.status_code == 202 and "token" not in r.text
        to, subject, text, html = s.sent[-1]
        assert subject == "You're invited to Sales Tracker" and "central_invite_token=" in text
        assert color in html and "cid:company-logo" in html
        assert "central_invite_token=" in html


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
    # Admin can be invited, by the global administrator only; Super Admin never, whatever the spelling.
    assert s.client.post("/api/central/companies/lity/invitations", headers=bearer(s, "m@example.com"),
                         json={"email": "x@example.com", "role": "admin"}).status_code == 403
    for bad in ("super_admin", "Admin", "owner", ""):
        assert s.client.post("/api/central/companies/lity/invitations", headers=bearer(s, "admin@example.com"),
                             json={"email": "x@example.com", "role": bad}).status_code == 422
    assert s.client.post("/api/central/companies/lity/invitations", headers=bearer(s, "admin@example.com"),
                         json={"email": "x@example.com", "role": "admin"}).status_code == 202
    with s.factory() as session:
        assert session.query(IdentityMembership).count() == 1  # an invitation creates no membership


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


# --- X-Company-Slug on the company administration routes: a consistency assertion, never a credential ---

ADMIN_ROUTES = [
    ("get", "/api/central/companies/{slug}/members", None),
    ("get", "/api/central/companies/{slug}/invitations", None),
    ("post", "/api/central/companies/{slug}/invitations", {"email": "x@example.com", "role": "visitor"}),
    ("post", "/api/central/companies/{slug}/members/1/deactivate", None),
    ("put", "/api/central/companies/{slug}/members/1/role", {"role": "visitor"}),
]


def call(s, method, path, headers, body):
    return getattr(s.client, method)(path, headers=headers, **({"json": body} if body is not None else {}))


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
@pytest.mark.parametrize("sent", ["salcomp", "valor-log", "LITY", "lity ", ""])
def test_admin_routes_refuse_a_mismatched_company_header_before_authentication(s, method, path, body, sent):
    # An invalid token is never examined: the mismatch is answered first, and nothing is created or changed.
    r = call(s, method, path.format(slug="lity"), {"Authorization": "Bearer junk", "X-Company-Slug": sent}, body)
    assert r.status_code == 400 and r.json()["detail"] == "Company mismatch"
    with s.factory() as session:
        assert session.query(IdentityInvitation).count() == 0 and session.query(IdentityMembership).count() == 0


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_admin_routes_still_authenticate_with_a_matching_or_absent_header(s, method, path, body):
    for extra in ({}, {"X-Company-Slug": "lity"}):
        r = call(s, method, path.format(slug="lity"), dict(extra), body)
        assert r.status_code == 401  # no token: the header neither replaces nor bypasses authentication


def test_matching_header_changes_nothing_for_the_global_administrator(s):
    base = bearer(s, "admin@example.com")
    plain = s.client.get("/api/central/companies/salcomp/members", headers=base)
    with_header = s.client.get("/api/central/companies/salcomp/members", headers={**base, "X-Company-Slug": "salcomp"})
    assert plain.status_code == with_header.status_code == 200
    assert plain.json() == with_header.json()
    invited = s.client.post("/api/central/companies/salcomp/invitations", json={"email": "n@example.com", "role": "visitor"},
                            headers={**base, "X-Company-Slug": "salcomp"})
    assert invited.status_code == 202


def test_company_header_never_grants_authority_over_a_company(s):
    with s.factory() as session:
        admin_id = svc.get_user_by_email(session, "admin@example.com").id
        ca.accept_invitation_with_password(session, svc.create_invitation(session, admin_id, "m@example.com", "lity", "sales_member").token, GOOD)
    member = bearer(s, "m@example.com")
    # Naming the company in the header does not make a Sales Member a manager of it.
    r = s.client.get("/api/central/companies/lity/members", headers={**member, "X-Company-Slug": "lity"})
    assert r.status_code == 403
    r = s.client.post("/api/central/companies/lity/invitations", headers={**member, "X-Company-Slug": "lity"},
                      json={"email": "x@example.com", "role": "visitor"})
    assert r.status_code == 403
    # And a header for another company cannot be used to reach it from this URL.
    assert s.client.get("/api/central/companies/lity/members", headers={**member, "X-Company-Slug": "salcomp"}).status_code == 400


def test_global_central_endpoints_ignore_the_company_header(s):
    h = bearer(s, "admin@example.com")
    assert s.client.get("/api/central/me", headers={**h, "X-Company-Slug": "anything"}).status_code == 200
    assert s.client.post("/api/central/password/forgot", json={"email": "nobody@example.com"},
                         headers={"X-Company-Slug": "anything"}).status_code == 200
    assert s.client.post("/api/central/logout", headers={**h, "X-Company-Slug": "anything"}).status_code == 204


def test_an_admin_invitation_end_to_end_gives_the_company_admin_role_and_never_the_super_admin(s):
    r = s.client.post("/api/central/companies/lity/invitations", headers=bearer(s, "admin@example.com"),
                      json={"email": "newadmin@example.com", "role": "admin"})
    assert r.status_code == 202
    token = s.sent[-1][2].split("central_invite_token=")[1].split()[0]
    assert s.client.post("/api/central/invitations/accept", json={"token": token, "password": GOOD}).status_code == 200
    me = s.client.get("/api/central/me", headers=bearer(s, "newadmin@example.com")).json()
    assert me["is_global_admin"] is False
    assert [(c["slug"], c["role"]) for c in me["companies"]] == [("lity", "admin")]
    # what that Admin may not do: invite or promote (company isolation of company routes is covered by the company route tests)
    assert s.client.post("/api/central/companies/lity/invitations", headers=bearer(s, "newadmin@example.com"),
                         json={"email": "y@example.com", "role": "admin"}).status_code == 403
    assert s.client.get("/api/central/companies/lity/members", headers=bearer(s, "newadmin@example.com")).status_code == 200  # read-only list
    assert s.client.put("/api/central/companies/lity/members/1/role", headers=bearer(s, "newadmin@example.com"),
                        json={"role": "admin"}).status_code == 403


# --- resending an invitation whose email was not delivered ---------------

def _open_invitation(s, email="r@example.com", role="sales_member"):
    with s.factory() as session:
        issued = svc.create_invitation(session, session.query(IdentityUser).filter_by(email="admin@example.com").one().id,
                                       email, "lity", role)
    return issued.invitation_id, issued.token


def _resend(s, invitation_id, headers=None, slug="lity"):
    return s.client.post(f"/api/central/companies/{slug}/invitations/{invitation_id}/resend",
                         headers=headers or bearer(s, "admin@example.com"))


def _accept(s, token):
    return s.client.post("/api/central/invitations/accept", json={"token": token, "password": GOOD}).status_code


def test_resend_delivers_new_link_and_retires_the_old_one(s, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
    inv, old = _open_invitation(s)
    r = _resend(s, inv)
    assert r.status_code == 200 and "token" not in r.text and "central_invite_token" not in r.text
    assert len(s.sent) == 1 and s.sent[0][0] == "r@example.com"
    new = s.sent[0][2].split("central_invite_token=")[1].split()[0]
    assert new != old
    assert _accept(s, old) == 400  # old link dead after a successful resend
    assert _accept(s, new) == 200
    with s.factory() as session:
        assert session.query(IdentityInvitation).count() == 1  # no duplicate invitation
        actions = [a.action for a in session.query(svc.IdentityAuditEvent)]
        assert "invitation_resent" in actions


def test_resend_without_smtp_is_503_and_changes_nothing(s, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    inv, old = _open_invitation(s)
    r = _resend(s, inv)
    assert r.status_code == 503 and "not sent" in r.json()["detail"] and not s.sent
    with s.factory() as session:
        assert session.query(svc.IdentityAuthToken).count() == 1
    assert _accept(s, old) == 200


def test_resend_delivery_failure_is_502_keeps_old_link_and_can_be_retried(s, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
    inv, old = _open_invitation(s)

    def boom(to, subject, body, html=None, inline_images=()):
        raise mailer.EmailDeliveryError("Could not send email")
    monkeypatch.setattr(mailer, "send_email", boom)
    r = _resend(s, inv)
    assert r.status_code == 502 and "could not be sent" in r.json()["detail"]
    with s.factory() as session:
        tokens = session.query(svc.IdentityAuthToken).all()
        assert len(tokens) == 2 and sum(t.invalidated_at is None for t in tokens) == 1  # only the original is live
        assert "invitation_resend_failed" in [a.action for a in session.query(svc.IdentityAuditEvent)]
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body, html=None, inline_images=(): s.sent.append((to, subject, body, html)))
    assert _resend(s, inv).status_code == 200  # retry after the failure
    new = s.sent[-1][2].split("central_invite_token=")[1].split()[0]
    assert _accept(s, old) == 400 and _accept(s, new) == 200


def test_resend_authorization_and_state_checks(s, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
    inv, token = _open_invitation(s)
    with s.factory() as session:
        ca.accept_invitation_with_password(session, svc.create_invitation(
            session, session.query(IdentityUser).filter_by(email="admin@example.com").one().id,
            "m@example.com", "lity", "sales_member").token, GOOD)
    assert s.client.post(f"/api/central/companies/lity/invitations/{inv}/resend").status_code == 401
    assert _resend(s, inv, headers=bearer(s, "m@example.com")).status_code == 403
    assert _resend(s, inv, slug="nope").status_code == 403
    assert _resend(s, 9999).status_code == 404
    assert not s.sent
    assert _accept(s, token) == 200
    assert _resend(s, inv).status_code == 409  # accepted: no longer open
    assert not s.sent


def test_resend_refuses_an_expired_invitation(s, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
    inv, _ = _open_invitation(s)
    with s.factory() as session:
        session.execute(text("UPDATE identity_invitations SET expires_at = datetime('now', '-1 hour'), created_at = datetime('now', '-2 hour')"))
        session.commit()
    assert _resend(s, inv).status_code == 409 and not s.sent


# --- invitation delivery is reported honestly, and members can be invited again ---------------------------------

def _invite(s, email, role="sales_member", slug="salcomp", headers=None):
    return s.client.post(f"/api/central/companies/{slug}/invitations",
                         headers=headers or bearer(s, "admin@example.com"), json={"email": email, "role": role})


def _pending(s, email):
    with s.factory() as session:
        return session.query(svc.IdentityInvitation).filter_by(email=email, accepted_at=None, revoked_at=None).count()


def test_invite_reports_email_sent_only_when_delivered(s):
    r = _invite(s, "ok@example.com")
    assert r.status_code == 202 and r.json() == {"email_sent": True, "message": "Invitation sent."}
    assert len(s.sent) == 1 and _pending(s, "ok@example.com") == 1


def test_invite_without_smtp_creates_invitation_but_says_nothing_was_sent(s, monkeypatch):
    monkeypatch.delenv("SMTP_HOST")
    r = _invite(s, "nomail@example.com")
    assert r.status_code == 202 and r.json()["email_sent"] is False and "not configured" in r.json()["message"]
    assert s.sent == [] and _pending(s, "nomail@example.com") == 1 and "token" not in r.text.lower().replace("email", "")


def test_invite_with_provider_failure_keeps_invitation_and_reports_failure(s, monkeypatch):
    def boom(*a, **k):
        raise mailer.EmailDeliveryError("Could not send email")
    monkeypatch.setattr(mailer, "send_email", boom)
    r = _invite(s, "fail@example.com")
    assert r.status_code == 202 and r.json()["email_sent"] is False and "could not be sent" in r.json()["message"]
    assert _pending(s, "fail@example.com") == 1


def test_undelivered_invitation_can_be_resent_once_mail_works(s, monkeypatch):
    monkeypatch.delenv("SMTP_HOST")
    _invite(s, "later@example.com")
    with s.factory() as session:
        inv_id = session.query(svc.IdentityInvitation).filter_by(email="later@example.com").one().id
    h = bearer(s, "admin@example.com")
    assert s.client.post(f"/api/central/companies/salcomp/invitations/{inv_id}/resend", headers=h).status_code == 503
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
    assert s.client.post(f"/api/central/companies/salcomp/invitations/{inv_id}/resend", headers=h).status_code == 200
    assert len(s.sent) == 1 and _pending(s, "later@example.com") == 1


def _accept_latest(s, password=GOOD):
    token = s.sent[-1][2].split("central_invite_token=")[1].split()[0]
    return s.client.post("/api/central/invitations/accept", json={"token": token, "password": password})


def _member_of(s, email, slug="salcomp"):
    members = s.client.get(f"/api/central/companies/{slug}/members", headers=bearer(s, "admin@example.com")).json()
    return next((m for m in members if m["email"] == email), None)


def test_deactivated_member_can_be_invited_again_and_is_reactivated_without_duplicates(s):
    assert _invite(s, "again@example.com", "visitor").status_code == 202
    assert _accept_latest(s).status_code == 200
    member = _member_of(s, "again@example.com")
    admin_h = bearer(s, "admin@example.com")
    assert s.client.post(f"/api/central/companies/salcomp/members/{member['user_id']}/deactivate", headers=admin_h).status_code == 204
    assert _member_of(s, "again@example.com")["is_active"] is False
    r = _invite(s, "again@example.com", "sales_member")
    assert r.status_code == 202 and r.json()["email_sent"] is True
    assert _invite(s, "again@example.com").status_code == 422  # a second open invitation is refused
    token = s.sent[-1][2].split("central_invite_token=")[1].split()[0]
    joined = s.client.post("/api/central/invitations/join", json={"token": token, "email": "again@example.com", "password": GOOD})
    assert joined.status_code == 200
    assert _member_of(s, "again@example.com")["is_active"] is True and _member_of(s, "again@example.com")["role"] == "sales_member"
    with s.factory() as session:
        rows = session.query(svc.IdentityMembership).filter_by(user_id=member["user_id"]).all()
        assert len(rows) == 1


def test_existing_identity_without_membership_can_be_invited(s):
    with s.factory() as session:
        session.add(svc.IdentityUser(email="stale@example.com", password_hash=ca.hash_password(GOOD), email_verified_at=svc._now()))
        session.commit()
    assert _invite(s, "stale@example.com").status_code == 202


def test_active_member_and_open_invitation_conflicts_remain(s):
    assert _invite(s, "dup@example.com").status_code == 202
    assert _invite(s, "dup@example.com").status_code == 422
    assert _accept_latest(s).status_code == 200
    assert _invite(s, "dup@example.com").status_code == 422  # already an active member
    assert _pending(s, "dup@example.com") == 0


def test_only_the_global_admin_may_invite(s):
    assert _invite(s, "x@example.com").status_code == 202
    assert _accept_latest(s).status_code == 200
    member_h = bearer(s, "x@example.com")
    assert _invite(s, "y@example.com", headers=member_h).status_code == 403
    assert s.client.post("/api/central/companies/salcomp/invitations", json={"email": "y@example.com", "role": "visitor"}).status_code == 401


def test_password_reset_email_is_branded_and_uses_the_effective_base_url(s, monkeypatch, caplog):
    import logging
    import central_routes
    monkeypatch.setattr(central_routes, "APP_BASE_URL", "http://127.0.0.1:3010")
    with caplog.at_level(logging.DEBUG):
        r = s.client.post("/api/central/password/forgot", json={"email": "ok@example.com"})
    assert r.status_code == 200
    to, subject, text, html = s.sent[0]
    assert subject == "Reset your Sales Tracker password"
    token = text.split("central_reset_token=")[1].split()[0]
    assert f"http://127.0.0.1:3010/?central_reset_token={token}" in text
    assert f'href="http://127.0.0.1:3010/?central_reset_token={token}"' in html and "Reset Password" in html
    assert token not in caplog.text and "central_reset_token" not in caplog.text
    assert s.client.post("/api/central/password/reset", json={"token": token, "new_password": "N3w!Passw0rdxx"}).status_code == 200


def test_first_password_form_refuses_an_account_that_already_has_a_password_and_the_link_stays_usable(s):
    """A deactivated member keeps their account and password, so a new invitation must be accepted with
    /invitations/join. The first-password endpoint refuses (same 400 as any bad link), changes nothing and does
    not consume the token, so the very same link still works through join."""
    assert _invite(s, "back@example.com", "visitor").status_code == 202
    assert _accept_latest(s).status_code == 200
    member = _member_of(s, "back@example.com")
    admin_h = bearer(s, "admin@example.com")
    assert s.client.post(f"/api/central/companies/salcomp/members/{member['user_id']}/deactivate", headers=admin_h).status_code == 204
    assert _invite(s, "back@example.com", "sales_member").status_code == 202
    token = s.sent[-1][2].split("central_invite_token=")[1].split()[0]
    with s.factory() as session:
        before = session.get(IdentityUser, member["user_id"]).password_hash

    refused = s.client.post("/api/central/invitations/accept", json={"token": token, "password": "Another#Pass9"})
    assert refused.status_code == 400
    assert refused.json()["detail"] == "This invitation is invalid or has expired"
    with s.factory() as session:
        assert session.get(IdentityUser, member["user_id"]).password_hash == before  # password untouched
        assert session.query(IdentityInvitation).filter_by(email="back@example.com", accepted_at=None).count() == 1
    assert _member_of(s, "back@example.com")["is_active"] is False

    joined = s.client.post("/api/central/invitations/join", json={"token": token, "email": "back@example.com", "password": GOOD})
    assert joined.status_code == 200
    assert _member_of(s, "back@example.com")["is_active"] is True
    assert s.client.post("/api/central/invitations/join", json={"token": token, "email": "back@example.com", "password": GOOD}).status_code == 400


def test_cancel_route_is_global_admin_only_revokes_access_and_frees_the_address(s):
    assert _invite(s, "gone@example.com", "sales_member").status_code == 202
    assert _accept_latest(s).status_code == 200
    member = _member_of(s, "gone@example.com")
    member_h = bearer(s, "gone@example.com")
    uid = member["user_id"]
    assert s.client.post(f"/api/central/users/{uid}/cancel").status_code == 401
    assert s.client.post(f"/api/central/users/{uid}/cancel", headers=member_h).status_code == 403
    assert s.client.post(f"/api/central/users/{uid}/cancel", headers=bearer(s, "admin@example.com")).status_code == 204
    assert s.client.get("/api/central/me", headers=member_h).status_code == 401  # the old session is gone
    assert s.login("gone@example.com", GOOD).status_code == 401
    assert s.client.post(f"/api/central/users/{uid}/cancel", headers=bearer(s, "admin@example.com")).status_code == 409
    again = _invite(s, "gone@example.com", "visitor")
    assert again.status_code == 202
    token = s.sent[-1][2].split("central_invite_token=")[1].split()[0]
    assert s.client.post("/api/central/invitations/accept", json={"token": token, "password": GOOD}).status_code == 200
    assert s.login("gone@example.com", GOOD).status_code == 200


def test_inviting_the_reserved_cancellation_domain_is_refused_over_http(s):
    response = _invite(s, "cancelled-1@cancelled.invalid", "visitor")
    assert response.status_code == 422 and "cancelled" not in response.text.lower().replace("the email address is not valid", "")
    assert s.sent == []
