"""SIGNUP_ALLOWED_DOMAINS: allowlist for /api/signup and /api/signup/complete."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
import signup_policy
from auth_test_utils import create_signup_token
from database import get_db
from main import app
from models import Base, User


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(username="admin", email=None, password_hash="x", role="admin"))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(main, "send_email", lambda to, subject, body: messages.append(to))
    return messages


@pytest.fixture
def domains(monkeypatch):
    monkeypatch.setenv("SIGNUP_ALLOWED_DOMAINS", " Salcomp.com , partner.example ")


def _request(client, email):
    return client.post("/api/signup", json={"email": email})


def _complete(client, db, email):
    return client.post("/api/signup/complete", json={"token": create_signup_token(db, email), "password": "Password123!"})


@pytest.mark.parametrize("email", ["a@salcomp.com", "A@SALCOMP.COM", "b@Partner.Example"])
def test_allowed_domains_get_link_and_can_complete(client, sent, domains, db, email):
    assert _request(client, email).status_code == 202
    assert sent == [email.lower()]
    assert _complete(client, db, email.lower()).status_code == 201
    assert db.query(User).filter(User.email == email.lower()).one()


@pytest.mark.parametrize(
    "email",
    [
        "a@gmail.com",
        "a@sub.salcomp.com",          # subdomain not authorized
        "a@evilsalcomp.com",          # suffix lookalike
        "a@salcomp.com.evil.io",      # allowed domain as a prefix
        "salcomp.com@evil.io",        # allowed domain in the local part
    ],
)
def test_other_domains_refused_on_both_endpoints(client, sent, domains, db, email):
    assert _request(client, email).status_code == 403
    assert sent == []
    assert _complete(client, db, email).status_code == 403
    assert db.query(User).count() == 1  # only the admin


@pytest.mark.parametrize("value", [None, "", "  ,  "])
def test_unset_or_empty_config_closes_signup(client, sent, monkeypatch, db, value):
    if value is None:
        monkeypatch.delenv("SIGNUP_ALLOWED_DOMAINS", raising=False)
    else:
        monkeypatch.setenv("SIGNUP_ALLOWED_DOMAINS", value)
    assert _request(client, "a@example.com").status_code == 403
    assert _complete(client, db, "a@example.com").status_code == 403
    assert sent == [] and db.query(User).count() == 1


def test_link_issued_before_allowlist_change_is_refused(client, monkeypatch, db):
    monkeypatch.setenv("SIGNUP_ALLOWED_DOMAINS", "example.com")
    token = create_signup_token(db, "a@example.com")
    monkeypatch.setenv("SIGNUP_ALLOWED_DOMAINS", "salcomp.com")
    r = client.post("/api/signup/complete", json={"token": token, "password": "Password123!"})
    assert r.status_code == 403
    assert db.query(User).count() == 1


def test_request_creates_no_user(client, sent, domains, db):
    assert _request(client, "a@salcomp.com").status_code == 202
    assert db.query(User).count() == 1


def test_domains_are_normalized(monkeypatch):
    monkeypatch.setenv("SIGNUP_ALLOWED_DOMAINS", " Salcomp.COM,,partner.example , salcomp.com")
    assert signup_policy.allowed_domains() == {"salcomp.com", "partner.example"}


@pytest.mark.parametrize("bad", ["@salcomp.com", "*.salcomp.com", "com", "a b.com", "salcomp..com", "salcomp.com:25", "-x.com", "a@b.com"])
def test_malformed_config_fails_validation(monkeypatch, bad):
    monkeypatch.setenv("SIGNUP_ALLOWED_DOMAINS", f"salcomp.com,{bad}")
    with pytest.raises(signup_policy.InvalidSignupDomainsError):
        signup_policy.validate_config()


def test_startup_refuses_malformed_config(monkeypatch):
    monkeypatch.setenv("SIGNUP_ALLOWED_DOMAINS", "*.salcomp.com")
    with pytest.raises(signup_policy.InvalidSignupDomainsError):
        with TestClient(app):
            pass


@pytest.fixture
def exception_only(monkeypatch):
    monkeypatch.setenv("SIGNUP_ALLOWED_DOMAINS", "salcomp.com")
    monkeypatch.setenv("SIGNUP_ALLOWED_EMAILS", " Pluisada@Gmail.com ")


def test_listed_gmail_address_can_sign_up(client, sent, exception_only, db):
    assert _request(client, "pluisada@gmail.com").status_code == 202
    assert sent == ["pluisada@gmail.com"]
    assert _complete(client, db, "pluisada@gmail.com").status_code == 201
    assert _request(client, "PLuisada@GMAIL.com").status_code == 202


@pytest.mark.parametrize(
    "email",
    [
        "other@gmail.com",             # same provider, not listed
        "pluisada+x@gmail.com",        # tag variant
        "p.luisada@gmail.com",         # dotted variant
        "pluisada@googlemail.com",     # alias domain
        "pluisada@sub.gmail.com",
        "pluisada@gmail.com.evil.io",
        "pluisada@evil.io",
        "x@unauthorized.example",
    ],
)
def test_exception_does_not_open_gmail_or_other_domains(client, sent, exception_only, db, email):
    assert _request(client, email).status_code == 403
    assert _complete(client, db, email).status_code == 403
    assert sent == [] and db.query(User).count() == 1


def test_exception_works_without_any_allowed_domain(client, sent, monkeypatch, db):
    monkeypatch.delenv("SIGNUP_ALLOWED_DOMAINS", raising=False)
    monkeypatch.setenv("SIGNUP_ALLOWED_EMAILS", "pluisada@gmail.com")
    assert _request(client, "pluisada@gmail.com").status_code == 202
    assert _request(client, "a@salcomp.com").status_code == 403


def test_corporate_domain_still_allowed_with_exception(client, sent, exception_only):
    assert _request(client, "a@salcomp.com").status_code == 202


@pytest.mark.parametrize("bad", ["gmail.com", "@gmail.com", "a@", "a b@gmail.com", "*@gmail.com", "a@b"])
def test_malformed_email_config_fails_validation(monkeypatch, bad):
    monkeypatch.setenv("SIGNUP_ALLOWED_EMAILS", f"pluisada@gmail.com,{bad}")
    with pytest.raises(signup_policy.InvalidSignupDomainsError):
        signup_policy.validate_config()
