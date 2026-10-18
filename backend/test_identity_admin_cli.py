"""identity_admin_cli on disposable identity databases only."""
import pytest

import central_auth as ca
import identity_admin_cli as cli
import identity_service as svc
from create_identity_db import create_identity_database
from identity_database import create_identity_session_factory, open_identity_engine
from identity_models import IdentityCompany, IdentityMembership, IdentityUser


@pytest.fixture
def session(tmp_path):
    path = tmp_path / "identity.db"
    create_identity_database(str(path))
    engine = open_identity_engine({"IDENTITY_DATABASE_URL": f"sqlite:///{path}"})
    with create_identity_session_factory(engine)() as s:
        yield s
    engine.dispose()


def test_bootstrap_creates_companies_and_passwordless_admin_idempotently(session):
    first = cli.bootstrap(session, "Owner@Example.com")
    assert first["admin_created"] and sorted(first["companies_created"]) == ["lity", "salcomp", "valor-log"]
    admin = session.query(IdentityUser).one()
    assert (admin.email, admin.is_global_admin, admin.password_hash) == ("owner@example.com", True, None)
    assert admin.email_verified_at is not None
    again = cli.bootstrap(session, "owner@example.com")
    assert again == {"companies_created": [], "admin_created": False}
    assert session.query(IdentityUser).count() == 1 and session.query(IdentityCompany).count() == 3


def test_bootstrap_refuses_to_promote_an_existing_member(session):
    cli.bootstrap(session, "owner@example.com")
    token = svc.create_invitation(session, session.query(IdentityUser).one().id, "m@example.com", "lity", "sales_member").token
    ca.accept_invitation_with_password(session, token, "Str0ng!Passw0rd")
    with pytest.raises(svc.Conflict):
        cli.bootstrap(session, "m@example.com")
    assert session.query(IdentityUser).filter_by(is_global_admin=True).count() == 1
    assert session.query(IdentityMembership).count() == 1


def test_activation_link_sets_password_and_is_never_returned(session):
    cli.bootstrap(session, "owner@example.com")
    sent = []
    assert cli.send_activation(session, "owner@example.com", send=lambda *a: sent.append(a)) is True
    to, subject, body = sent[0]
    token = body.split("central_reset_token=")[1].split()[0]
    ca.reset_password(session, token, "Str0ng!Passw0rd")
    assert ca.authenticate(session, "owner@example.com", "Str0ng!Passw0rd").access_token
    with pytest.raises(ca.ResetTokenInvalid):
        ca.reset_password(session, token, "An0ther!Passw0rd")  # single use


def test_activation_for_unknown_email_sends_nothing(session):
    assert cli.send_activation(session, "nobody@example.com", send=lambda *a: pytest.fail("sent")) is False
