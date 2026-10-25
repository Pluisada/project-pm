"""The company Admin limit through the real /api/central endpoints (disposable databases, mail mocked).

Contract, unchanged by the limit: POST .../invitations answers EVERY refused service call with 422 and the service's
message (that route has always done so, e.g. for an already-open invitation or an existing member), while the member
routes (.../members/{id}/role, .../deactivate) answer a Conflict with 409 through `_managed`. The limit therefore
returns 422 on invite and 409 on promotion; both carry the same actionable text and the interface shows either. An
acceptance that finds the company over capacity gets the uniform invitation-side 400, like every other acceptance
refusal, and consumes nothing."""
import pytest
from sqlalchemy import text

import central_auth as ca
import identity_service as svc
from test_central_account_routes import bearer, s  # noqa: F401  (fixture/helper)
from test_central_login import GOOD

LITY = "/api/central/companies/lity"
FULL = "maximum of 2 Admins"


def snapshot(s):
    with s.factory() as session:
        return [session.execute(text(q)).all() for q in (
            "SELECT * FROM identity_users ORDER BY id", "SELECT * FROM identity_memberships ORDER BY id",
            "SELECT * FROM identity_invitations ORDER BY id", "SELECT id, consumed_at, invalidated_at FROM identity_auth_tokens ORDER BY id")]


def invite(s, email, role="admin"):
    return s.client.post(f"{LITY}/invitations", headers=bearer(s, "admin@example.com"), json={"email": email, "role": role})


def accept(s, email, password=GOOD):
    token = s.sent[-1][2].split("central_invite_token=")[1].split()[0]
    return token, s.client.post("/api/central/invitations/accept", json={"token": token, "password": password})


def member(s, email, role="sales_member"):
    with s.factory() as session:
        admin_id = svc.get_user_by_email(session, "admin@example.com").id
        uid = ca.accept_invitation_with_password(
            session, svc.create_invitation(session, admin_id, email, "lity", "sales_member").token, GOOD).user_id
        if role != "sales_member":
            svc.change_membership_role(session, admin_id, "lity", uid, role)
    return uid


def test_inviting_an_admin_when_both_seats_are_reserved_is_refused_with_422_and_changes_nothing(s):
    member(s, "a1@example.com", "admin")  # one seat occupied
    assert invite(s, "a2@example.com").status_code == 202  # one seat reserved by a pending invitation
    before, mails = snapshot(s), len(s.sent)
    r = invite(s, "a3@example.com")
    assert r.status_code == 422 and FULL in r.json()["detail"] and "deactivate an Admin or revoke" in r.json()["detail"]
    assert snapshot(s) == before and len(s.sent) == mails  # no invitation, token, user, membership or email
    assert invite(s, "v@example.com", "visitor").status_code == 202  # other roles are unaffected


def test_promoting_a_member_when_two_seats_are_taken_is_refused_with_409_and_changes_nothing(s):
    member(s, "a1@example.com", "admin")
    member(s, "a2@example.com", "admin")
    m = member(s, "m@example.com")
    before = snapshot(s)
    r = s.client.put(f"{LITY}/members/{m}/role", headers=bearer(s, "admin@example.com"), json={"role": "admin"})
    assert r.status_code == 409 and FULL in r.json()["detail"]
    assert snapshot(s) == before
    ok = s.client.put(f"{LITY}/members/{m}/role", headers=bearer(s, "admin@example.com"), json={"role": "visitor"})
    assert ok.status_code == 204


def test_a_pending_invitation_blocks_a_promotion_and_revoking_it_frees_the_seat(s):
    member(s, "a1@example.com", "admin")
    m = member(s, "m@example.com")
    assert invite(s, "a2@example.com").status_code == 202
    promote = lambda: s.client.put(f"{LITY}/members/{m}/role", headers=bearer(s, "admin@example.com"), json={"role": "admin"})
    assert promote().status_code == 409
    with s.factory() as session:
        admin_id = svc.get_user_by_email(session, "admin@example.com").id
        pending = svc.list_pending_invitations(session, admin_id, "lity")
        svc.revoke_invitation(session, admin_id, pending[0]["invitation_id"])
    assert promote().status_code == 204


def test_accepting_an_admin_invitation_converts_its_seat_over_http(s):
    member(s, "a1@example.com", "admin")
    assert invite(s, "a2@example.com").status_code == 202
    token, r = accept(s, "a2@example.com")
    assert r.status_code == 200
    me = s.client.get("/api/central/me", headers=bearer(s, "a2@example.com")).json()
    assert me["companies"] == [{"slug": "lity", "name": "Lity", "role": "admin"}]
    assert invite(s, "a3@example.com").status_code == 422  # still exactly two
    assert s.client.post("/api/central/invitations/accept", json={"token": token, "password": GOOD}).status_code == 400


def test_acceptance_when_capacity_was_consumed_since_is_refused_without_consuming_anything(s):
    member(s, "a1@example.com", "admin")
    assert invite(s, "a2@example.com").status_code == 202  # company now full: 1 member + this reservation
    m = member(s, "m@example.com")
    with s.factory() as session:  # consumed since: a third Admin appears by a path the service forbids (data fix)
        session.execute(text("UPDATE identity_memberships SET role = 'admin' WHERE user_id = :u"), {"u": m})
        session.commit()
    before = snapshot(s)
    token, r = accept(s, "a2@example.com")
    assert r.status_code == 400 and FULL not in r.text  # the uniform invitation-side refusal
    assert snapshot(s) == before  # no user, membership, accepted invitation or consumed token
    # nothing was demoted, and once a seat is freed the very same link works
    with s.factory() as session:
        session.execute(text("UPDATE identity_memberships SET role = 'visitor' WHERE user_id = :u"), {"u": m})
        session.commit()
    assert s.client.post("/api/central/invitations/accept", json={"token": token, "password": GOOD}).status_code == 200


def test_the_limit_is_enforced_by_the_backend_for_every_caller(s):
    member(s, "a1@example.com", "admin")
    member(s, "a2@example.com", "admin")
    # a company Admin or member cannot invite at all (403), so the cap is not their concern; the Super Admin is refused
    assert s.client.post(f"{LITY}/invitations", headers=bearer(s, "a1@example.com"),
                         json={"email": "x@example.com", "role": "admin"}).status_code == 403
    assert invite(s, "x@example.com").status_code == 422
