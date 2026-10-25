"""Phase 1: session revocation, persistent tokens, mail logging, password policy,
provisioning safeguards and opportunity authorization. Every database here is
an in-memory or tmp_path SQLite file; backend/pm.db is never opened."""
import json
import logging
import ssl
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import admin_cli
import auth
import mailer
import main
import token_store
from ai_kanban import apply_board_actions
from auth_test_utils import SETUP_HEADERS, add_member, VERIFIED, create_reset_token, create_signup_token
from database import get_db
from main import app
from models import AuditEvent, AuthToken, Base, Card, CardEditHistory, User

ADMIN_PW = "Adminpass123!"
MEMBER_PW = "Memberpass123!"
OTHER_PW = "Otherpass123!"


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: (yield db)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(main, "send_email", lambda to, subject, body: messages.append((to, body)))
    return messages


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def login(client, username, password):
    return client.post("/api/login", json={"username": username, "password": password})


@pytest.fixture
def users(client, db):
    """Admin (via setup, which seeds the board) plus members `member` and `other`."""
    admin_token = client.post("/api/setup", json={"username": "admin", "password": ADMIN_PW}, headers=SETUP_HEADERS).json()["access_token"]
    for name, pw in (("member", MEMBER_PW), ("other", OTHER_PW)):
        add_member(client, name, pw)
    return {
        "admin": admin_token,
        "member": login(client, "member", MEMBER_PW).json()["access_token"],
        "other": login(client, "other", OTHER_PW).json()["access_token"],
    }


# --- session version -------------------------------------------------------------


class TestSessions:
    def test_logout_revokes_the_token_and_repeated_use_stays_401(self, client, users):
        h = bearer(users["member"])
        assert client.get("/api/user", headers=h).status_code == 200
        assert client.post("/api/logout", headers=h).status_code == 200
        for _ in range(3):  # repeated requests with the dead token all fail alike
            assert client.get("/api/user", headers=h).status_code == 401
            assert client.post("/api/logout", headers=h).status_code == 401

    def test_logout_ends_every_session_of_that_user_only(self, client, users):
        second = login(client, "member", MEMBER_PW).json()["access_token"]
        client.post("/api/logout", headers=bearer(users["member"]))
        assert client.get("/api/user", headers=bearer(second)).status_code == 401
        assert client.get("/api/user", headers=bearer(users["other"])).status_code == 200
        assert client.get("/api/user", headers=bearer(users["admin"])).status_code == 200

    def test_login_after_logout_works_and_version_only_increases(self, client, users, db):
        before = db.query(User).filter_by(username="member").one().session_version
        client.post("/api/logout", headers=bearer(users["member"]))
        db.expire_all()
        assert db.query(User).filter_by(username="member").one().session_version == before + 1
        fresh = login(client, "member", MEMBER_PW).json()["access_token"]
        assert client.get("/api/user", headers=bearer(fresh)).status_code == 200

    def test_concurrent_logouts_leave_one_consistent_state(self, tmp_path):
        """Two overlapping logouts must not lose an increment or resurrect a token."""
        engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        with Session() as s:
            s.add(User(username="u", password_hash="x", email_verified_at=VERIFIED))
            s.commit()

        barrier = threading.Barrier(2)

        def bump():
            with Session() as s:
                barrier.wait()
                main._bump_session_version(s, 1)
                s.commit()

        threads = [threading.Thread(target=bump) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        with Session() as s:
            assert s.get(User, 1).session_version == 2

    def test_password_reset_ends_sessions_and_new_login_works(self, client, users, db):
        member = db.query(User).filter_by(username="member").one()
        member.email = "member@example.com"
        db.commit()
        old = bearer(users["member"])
        assert client.post(
            "/api/password/reset", json={"token": create_reset_token(db, member), "password": "Brandnew123!"}
        ).status_code == 200
        assert client.get("/api/user", headers=old).status_code == 401
        assert login(client, "member", MEMBER_PW).status_code == 401
        assert client.get("/api/user", headers=bearer(login(client, "member", "Brandnew123!").json()["access_token"])).status_code == 200

    def test_password_change_alone_still_invalidates_without_a_version_bump(self, client, users, db):
        member = db.query(User).filter_by(username="member").one()
        member.password_hash = auth.hash_password("Changed123!x")
        db.commit()
        assert client.get("/api/user", headers=bearer(users["member"])).status_code == 401

    def test_token_without_or_with_wrong_session_version_is_rejected(self, client, users, db):
        member = db.query(User).filter_by(username="member").one()
        base = {"sub": "member", "pwh": auth.password_fingerprint(member.password_hash)}
        assert client.get("/api/user", headers=bearer(auth.create_access_token({**base, "sv": member.session_version}))).status_code == 200
        for claims in (base, {**base, "sv": member.session_version + 1}, {**base, "sv": str(member.session_version)}, {**base, "sv": True}):
            assert client.get("/api/user", headers=bearer(auth.create_access_token(claims))).status_code == 401

    def test_expired_token_rejected_and_lifetime_is_eight_hours(self, client, users, db):
        member = db.query(User).filter_by(username="member").one()
        expired = auth.create_user_access_token(member, timedelta(seconds=-1))
        assert client.get("/api/user", headers=bearer(expired)).status_code == 401
        payload = jwt.decode(users["member"], auth.SECRET_KEY, algorithms=[auth.ALGORITHM])
        assert payload["exp"] - datetime.now(timezone.utc).timestamp() <= 8 * 3600 + 5

    def test_purpose_scoped_tokens_are_not_access_tokens(self, client, users, db):
        member = db.query(User).filter_by(username="member").one()
        purpose = auth.create_purpose_token("member", "x", timedelta(minutes=5), pwh=auth.password_fingerprint(member.password_hash), sv=member.session_version)
        assert client.get("/api/user", headers=bearer(purpose)).status_code == 401
        for raw in (create_reset_token(db, member), create_signup_token(db, "new@example.com")):
            assert client.get("/api/user", headers=bearer(raw)).status_code == 401

    def test_unverified_account_cannot_log_in_or_use_a_token(self, client, users, db):
        member = db.query(User).filter_by(username="member").one()
        member.email_verified_at = None
        db.commit()
        assert login(client, "member", MEMBER_PW).status_code == 401
        assert client.get("/api/user", headers=bearer(users["member"])).status_code == 401


# --- persistent tokens -----------------------------------------------------------


class TestTokenStore:
    def test_only_a_hash_is_stored(self, db):
        raw = token_store.issue(db, "reset", timedelta(hours=1), user_id=1)
        db.commit()
        row = db.query(AuthToken).one()
        assert row.token_hash == token_store.hash_token(raw) and len(raw) >= 43
        assert raw not in {str(v) for v in (row.token_hash, row.purpose, row.email, row.user_id)}

    def test_single_use_expiry_purpose_and_invalidation(self, db):
        raw = token_store.issue(db, "reset", timedelta(hours=1), user_id=1)
        assert token_store.consume(db, raw, "signup") is None  # wrong purpose
        assert token_store.consume(db, raw, "reset") is not None
        assert token_store.consume(db, raw, "reset") is None  # used
        old = token_store.issue(db, "reset", timedelta(hours=1), user_id=1)
        token_store.issue(db, "reset", timedelta(hours=1), user_id=1)  # supersedes
        assert token_store.consume(db, old, "reset") is None
        expired = token_store.issue(db, "reset", timedelta(seconds=-1), user_id=2)
        assert token_store.consume(db, expired, "reset") is None
        revoked = token_store.issue(db, "reset", timedelta(hours=1), user_id=3)
        token_store.invalidate_for_user(db, 3)
        assert token_store.consume(db, revoked, "reset") is None
        assert token_store.consume(db, "garbage", "reset") is None

    def test_concurrent_consumption_has_exactly_one_winner(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        with Session() as s:
            raw = token_store.issue(s, "reset", timedelta(hours=1), user_id=1)
            s.commit()

        barrier, results = threading.Barrier(8), []

        def attempt():
            with Session() as s:
                barrier.wait()
                won = token_store.consume(s, raw, "reset") is not None
                s.commit()
                results.append(won)

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(results) == [False] * 7 + [True]

    def test_concurrent_reset_requests_change_the_password_once(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        with Session() as s:
            s.add(User(username="u@example.com", email="u@example.com", password_hash="x", email_verified_at=VERIFIED))
            s.commit()
            raw = token_store.issue(s, "reset", timedelta(hours=1), user_id=1)
            s.commit()

        def override():
            with Session() as s:
                yield s

        app.dependency_overrides[get_db] = override
        statuses, barrier = [], threading.Barrier(4)

        def attempt(i):
            c = TestClient(app)
            barrier.wait()
            statuses.append(c.post("/api/password/reset", json={"token": raw, "password": f"Winner{i}pass!"}).status_code)

        try:
            threads = [threading.Thread(target=attempt, args=(i,)) for i in range(4)]
            [t.start() for t in threads]
            [t.join() for t in threads]
        finally:
            app.dependency_overrides.clear()
        assert sorted(statuses) == [200, 400, 400, 400]


# --- mail logging ----------------------------------------------------------------


class TestMailerLogging:
    BODY = "Open https://app.example/?reset_token=SECRETTOKEN123 now"

    def test_no_smtp_logs_metadata_only(self, monkeypatch, caplog):
        monkeypatch.delenv("SMTP_HOST", raising=False)
        with caplog.at_level(logging.DEBUG):
            mailer.send_email("alice@example.com", "Reset your PM password", self.BODY)
        text = caplog.text
        assert "SECRETTOKEN123" not in text and "reset_token" not in text and "https://" not in text
        assert "alice@example.com" not in text and "a***@example.com" in text
        assert "Reset your PM password" in text

    def test_smtp_failure_logs_the_error_type_only(self, monkeypatch, caplog):
        monkeypatch.setenv("SMTP_HOST", "smtp.invalid")

        class Boom:
            def __init__(self, *a, **k):
                raise OSError("could not reach alice@example.com with SECRETTOKEN123")

        monkeypatch.setattr(mailer.smtplib, "SMTP", Boom)
        with caplog.at_level(logging.DEBUG), pytest.raises(mailer.EmailDeliveryError) as info:
            mailer.send_email("alice@example.com", "Subject", self.BODY)
        assert "SECRETTOKEN123" not in caplog.text and "alice@example.com" not in caplog.text
        assert "OSError" in caplog.text
        assert "SECRETTOKEN123" not in str(info.value) and info.value.__cause__ is None

    def test_successful_send_logs_status_without_body(self, monkeypatch, caplog):
        monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
        sent = []

        class Fake:
            def __init__(self, *a, **k): ...
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self, *, context=None): ...
            def login(self, *a): ...
            def send_message(self, message): sent.append(message)

        monkeypatch.setattr(mailer.smtplib, "SMTP", Fake)
        with caplog.at_level(logging.DEBUG):
            mailer.send_email("alice@example.com", "Subject", self.BODY)
        assert len(sent) == 1 and "SECRETTOKEN123" in sent[0].get_content()
        assert "SECRETTOKEN123" not in caplog.text and "Email sent" in caplog.text

    def _fake_smtp(self, monkeypatch, calls, starttls_error=None):
        class Fake:
            def __init__(self, host, port, timeout=None):
                calls["init"] = (host, port, timeout)
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self, *args, **kwargs):
                calls["starttls"] = (args, kwargs)
                if starttls_error:
                    raise starttls_error
            def login(self, user, password): calls["login"] = (user, password)
            def send_message(self, message): calls["sent"] = message

        monkeypatch.setattr(mailer.smtplib, "SMTP", Fake)

    def test_starttls_uses_a_verifying_default_context(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "bot@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "pw")
        calls = {}
        self._fake_smtp(monkeypatch, calls)
        mailer.send_email("alice@example.com", "Subject", "body")
        args, kwargs = calls["starttls"]
        context = kwargs["context"]
        assert args == () and set(kwargs) == {"context"}
        assert isinstance(context, ssl.SSLContext)
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
        assert calls["init"] == ("smtp.example.com", 587, 10)
        assert calls["login"] == ("bot@example.com", "pw") and "sent" in calls

    def test_tls_failure_sends_nothing_and_logs_the_type_only(self, monkeypatch, caplog):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_USER", "bot@example.com")
        calls = {}
        error = ssl.SSLCertVerificationError("hostname mismatch for alice@example.com SECRETTOKEN123")
        self._fake_smtp(monkeypatch, calls, starttls_error=error)
        with caplog.at_level(logging.DEBUG), pytest.raises(mailer.EmailDeliveryError) as info:
            mailer.send_email("alice@example.com", "Subject", self.BODY)
        assert "login" not in calls and "sent" not in calls  # credentials never sent over an unverified channel
        assert "SSLCertVerificationError" in caplog.text
        assert "SECRETTOKEN123" not in caplog.text and "alice@example.com" not in caplog.text
        assert info.value.__cause__ is None and "SECRETTOKEN123" not in str(info.value)

    def test_real_signup_and_reset_requests_leave_no_link_in_the_logs(self, client, users, db, caplog, monkeypatch):
        monkeypatch.delenv("SMTP_HOST", raising=False)
        db.query(User).filter_by(username="member").one().email = "member@example.com"
        db.commit()
        with caplog.at_level(logging.DEBUG):
            assert client.post("/api/signup", json={"email": "new@example.com"}).status_code == 202
            assert client.post("/api/password/forgot", json={"identifier": "member@example.com"}).status_code == 200
        raw_tokens = [r.token_hash for r in db.query(AuthToken)]  # hashes must not be logged either
        assert len(raw_tokens) == 2
        for needle in ("signup_token", "reset_token", auth.APP_BASE_URL, "Open this link", "member@example.com", "new@example.com", *raw_tokens):
            assert needle not in caplog.text, needle
        assert caplog.text.count("Email not sent") == 2

    def test_access_log_filter_redacts_link_tokens(self):
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                   ("1.2.3.4:1", "GET", "/?reset_token=abc123&x=1", "1.1", 200), None)
        assert main._RedactLinkTokens().filter(record)
        assert "abc123" not in record.getMessage() and "reset_token=[redacted]&x=1" in record.getMessage()

    def test_access_log_filter_redacts_central_invitation_tokens(self):
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                   ("1.2.3.4:1", "GET", "/?central_invite_token=abc123&central_reset_token=def456", "1.1", 200), None)
        assert main._RedactLinkTokens().filter(record)
        message = record.getMessage()
        assert "abc123" not in message and "def456" not in message
        assert "central_invite_token=[redacted]&central_reset_token=[redacted]" in message


# --- password policy -------------------------------------------------------------

BAD_PASSWORDS = {
    "Sh0rt!a": "too short (7)",
    "alllowercase1!": "no uppercase",
    "ALLUPPERCASE1!": "no lowercase",
    "NoDigitsHere!!": "no number",
    "NoSymbol12345": "no symbol",
    "Aa1!" + "x" * 130: "too long",
}


class TestPasswordPolicy:
    @pytest.mark.parametrize("password", ["Aa1!aaaa", "Correct-Horse-9", "Pässwörd1!"])
    def test_valid_passwords_accepted_everywhere(self, client, db, users, password):
        from password_policy import password_problems
        assert password_problems(password) == []

    @pytest.mark.parametrize("password", list(BAD_PASSWORDS), ids=list(BAD_PASSWORDS.values()))
    def test_every_password_setting_endpoint_rejects_it(self, client, db, users, password):
        member = db.query(User).filter_by(username="member").one()
        responses = [
            client.post("/api/signup/complete", json={"token": create_signup_token(db, "n@example.com"), "password": password}),
            client.post("/api/password/reset", json={"token": create_reset_token(db, member), "password": password}),
        ]
        assert [r.status_code for r in responses] == [422, 422]
        assert all(password not in r.text for r in responses)  # never echoed back

    @pytest.mark.parametrize("password", list(BAD_PASSWORDS), ids=list(BAD_PASSWORDS.values()))
    def test_setup_rejects_it(self, client, password):
        response = client.post("/api/setup", json={"username": "admin", "password": password}, headers=SETUP_HEADERS)
        assert response.status_code == 422 and password not in response.text

    @pytest.mark.parametrize("password", list(BAD_PASSWORDS))
    def test_admin_cli_enforces_the_same_policy(self, db, password):
        with pytest.raises(admin_cli.ProvisioningError):
            admin_cli.create_admin(db, "boss", password)
        assert db.query(User).count() == 0


# --- provisioning and registration -----------------------------------------------


class TestProvisioning:
    def test_admin_without_password_has_no_usable_password_and_cannot_log_in(self, client, db, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")  # four failed logins here would hit the login delay
        client.post("/api/setup", json={"username": "first", "password": ADMIN_PW}, headers=SETUP_HEADERS)
        user = admin_cli.create_admin(db, "owner@example.com", None)
        assert user.password_hash == auth.UNUSABLE_PASSWORD_HASH and user.email_verified_at is None
        for attempt in ("", "!", "anything", ADMIN_PW):
            assert login(client, "owner@example.com", attempt).status_code == 401

    def test_no_password_admin_gets_in_only_through_the_reset_flow(self, client, db, sent):
        client.post("/api/setup", json={"username": "first", "password": ADMIN_PW}, headers=SETUP_HEADERS)
        admin_cli.create_admin(db, "owner@example.com", None)
        assert client.post("/api/password/forgot", json={"identifier": "owner@example.com"}).status_code == 200
        raw = sent[0][1].split("reset_token=")[1].split()[0]
        assert client.post("/api/password/reset", json={"token": raw, "password": "Ownerpass123!"}).status_code == 200
        response = login(client, "owner@example.com", "Ownerpass123!")
        assert response.status_code == 200 and response.json()["role"] == "admin"

    def test_no_password_requires_an_email_username(self, db):
        for name in ("owner", "Owner@Example.com"):
            with pytest.raises(admin_cli.ProvisioningError):
                admin_cli.create_admin(db, name, None)

    def test_cli_flag_creates_without_prompting_and_conflicts_with_stdin(self, tmp_path, capsys):
        engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        def no_prompt(_):
            raise AssertionError("must not prompt")

        code = admin_cli.main(["create", "--username", "boss@example.com", "--no-password"], session_factory=factory, init=lambda: None, prompt=no_prompt)
        assert code == 0
        assert admin_cli.main(["create", "--username", "x@example.com", "--no-password", "--password-stdin"], session_factory=factory, init=lambda: None) == 1

    @pytest.mark.parametrize("name", ["e2e_admin", "e2e_member"])
    def test_public_test_accounts_cannot_provision_outside_e2e_mode(self, client, db, users, name):
        # Not an email, so the admin interface cannot create it at all.
        assert client.post("/api/users", json={"email": name}, headers=bearer(users["admin"])).status_code == 422
        with pytest.raises(admin_cli.ProvisioningError):
            admin_cli.create_admin(db, name, "Aa1!aaaaaa")

    def test_setup_refuses_the_public_test_admin_outside_e2e_mode(self, client):
        assert client.post("/api/setup", json={"username": "e2e_admin", "password": "E2e_admin_password123!"}, headers=SETUP_HEADERS).status_code == 400
        assert client.get("/api/setup/status").json() == {"needs_setup": True, "setup_enabled": True}

    def test_an_existing_e2e_admin_row_cannot_authenticate_outside_e2e_mode(self, client, db):
        db.add(User(username="e2e_admin", password_hash=auth.hash_password("E2e_admin_password123!"), role="admin", email_verified_at=VERIFIED))
        db.commit()
        assert login(client, "e2e_admin", "E2e_admin_password123!").status_code == 401
        user = db.query(User).one()
        token = auth.create_user_access_token(user)
        assert client.get("/api/user", headers=bearer(token)).status_code == 401

    def test_signup_exception_never_grants_admin(self, client, db, users, monkeypatch):
        monkeypatch.setenv("SIGNUP_ALLOWED_EMAILS", "pluisada@gmail.com")
        response = client.post("/api/signup/complete", json={"token": create_signup_token(db, "pluisada@gmail.com"), "password": "Gmailpass123!"})
        assert response.status_code == 201
        user = db.query(User).filter_by(email="pluisada@gmail.com").one()
        assert user.role == "member" and user.email_verified_at is not None

    def test_other_gmail_addresses_are_not_allowed(self, client, users, monkeypatch):
        monkeypatch.setenv("SIGNUP_ALLOWED_DOMAINS", "salcomp.com")
        monkeypatch.setenv("SIGNUP_ALLOWED_EMAILS", "pluisada@gmail.com")
        assert client.post("/api/signup", json={"email": "someone@gmail.com"}).status_code == 403
        assert client.post("/api/signup", json={"email": "x@salcomp.com"}).status_code == 202


# --- opportunity authorization ---------------------------------------------------


@pytest.fixture
def lead(client, users, db):
    """First seeded card, plus ids. Starts unassigned (admin clears the seed owner)."""
    board = client.get("/api/boards", headers=bearer(users["admin"])).json()[0]
    detail = client.get(f"/api/boards/{board['id']}", headers=bearer(users["admin"])).json()
    card = detail["columns"][0]["cards"][0]
    ids = {name: db.query(User).filter_by(username=name).one().id for name in ("admin", "member", "other")}
    db.get(Card, card["id"]).salesperson_id = None
    db.commit()
    return {"board": board["id"], "card": card["id"], "columns": [c["id"] for c in detail["columns"]], "ids": ids}


def put(client, lead, token, payload):
    return client.put(f"/api/boards/{lead['board']}/cards/{lead['card']}", json=payload, headers=bearer(token))


def assign(db, lead, name):
    db.get(Card, lead["card"]).salesperson_id = lead["ids"][name]
    db.commit()


class TestOpportunityAuthorization:
    def test_everyone_can_view_everything(self, client, users, lead):
        for who in users:
            detail = client.get(f"/api/boards/{lead['board']}", headers=bearer(users[who]))
            assert detail.status_code == 200 and detail.json()["columns"][0]["cards"]

    def test_member_edits_only_their_own(self, client, users, lead, db):
        assign(db, lead, "member")
        assert put(client, lead, users["member"], {"company": "Mine"}).status_code == 200
        assert put(client, lead, users["other"], {"company": "Theirs"}).status_code == 403
        assert db.get(Card, lead["card"]).company == "Mine"

    def test_unassigned_is_admin_only(self, client, users, lead, db):
        assert put(client, lead, users["member"], {"company": "x"}).status_code == 403
        assert put(client, lead, users["admin"], {"company": "x"}).status_code == 200

    def test_admin_edits_everything(self, client, users, lead, db):
        assign(db, lead, "member")
        assert put(client, lead, users["admin"], {"company": "Admin"}).status_code == 200

    def test_member_cannot_assign_reassign_or_take_a_card(self, client, users, lead, db):
        ids = lead["ids"]
        assert put(client, lead, users["member"], {"salesperson_id": ids["member"]}).status_code == 403  # take unassigned
        assign(db, lead, "member")
        assert put(client, lead, users["member"], {"salesperson_id": ids["other"]}).status_code == 403  # give away
        assert put(client, lead, users["member"], {"salesperson_id": None}).status_code == 403  # unassign
        assert put(client, lead, users["other"], {"salesperson_id": ids["other"]}).status_code == 403  # steal
        assert db.get(Card, lead["card"]).salesperson_id == ids["member"]
        assert db.query(AuditEvent).count() == 0

    def test_member_resending_the_current_owner_is_not_a_reassignment(self, client, users, lead, db):
        assign(db, lead, "member")
        assert put(client, lead, users["member"], {"salesperson_id": lead["ids"]["member"], "company": "ok"}).status_code == 200
        assert db.query(AuditEvent).count() == 0

    def test_authorization_follows_the_database_not_the_token(self, client, users, lead, db):
        assign(db, lead, "member")
        assert put(client, lead, users["member"], {"company": "a"}).status_code == 200
        assign(db, lead, "other")  # reassigned after the member's token was issued
        assert put(client, lead, users["member"], {"company": "b"}).status_code == 403
        # A role claim in the token is ignored: demotion takes effect immediately.
        db.query(User).filter_by(username="admin").one().role = "member"
        db.commit()
        assert put(client, lead, users["admin"], {"company": "c"}).status_code == 403

    def test_admin_assignment_is_audited_once_per_change(self, client, users, lead, db):
        ids = lead["ids"]
        assert put(client, lead, users["admin"], {"salesperson_id": ids["member"]}).status_code == 200
        assert put(client, lead, users["admin"], {"salesperson_id": ids["member"]}).status_code == 200  # no change
        assert put(client, lead, users["admin"], {"salesperson_id": ids["other"], "company": "c"}).status_code == 200
        events = db.query(AuditEvent).order_by(AuditEvent.id).all()
        assert [e.action for e in events] == ["assign", "assign"]
        first, second = (json.loads(e.details) for e in events)
        assert (first["from"], first["to"]) == (None, "member") and (second["from"], second["to"]) == ("member", "other")
        assert all(e.actor_user_id == ids["admin"] and e.card_id == lead["card"] for e in events)

    def test_only_admin_deletes_and_deletion_is_audited(self, client, users, lead, db):
        assign(db, lead, "member")
        url = f"/api/boards/{lead['board']}/cards/{lead['card']}"
        assert client.delete(url, headers=bearer(users["member"])).status_code == 403
        assert db.get(Card, lead["card"]) is not None and db.query(AuditEvent).count() == 0
        assert client.delete(url, headers=bearer(users["admin"])).status_code == 204
        event = db.query(AuditEvent).one()
        assert (event.action, event.card_id, event.actor_user_id) == ("delete", lead["card"], lead["ids"]["admin"])
        assert json.loads(event.details)["salesperson_id"] == lead["ids"]["member"]

    def test_member_created_card_is_assigned_to_its_creator_and_audited(self, client, users, lead, db):
        response = client.post(
            f"/api/boards/{lead['board']}/cards",
            json={"column_id": lead["columns"][0], "title": "New", "salesperson_id": lead["ids"]["other"]},
            headers=bearer(users["member"]),
        )
        assert response.status_code == 201
        card = db.get(Card, response.json()["id"])
        assert card.salesperson_id == lead["ids"]["member"]  # the extra field is ignored
        created, assigned = db.query(AuditEvent).order_by(AuditEvent.id).all()
        assert (created.action, created.card_id, created.actor_user_id) == ("create", card.id, lead["ids"]["member"])
        assert json.loads(created.details)["title"] == "New"
        assert (assigned.action, assigned.card_id, assigned.actor_user_id) == ("assign", card.id, lead["ids"]["member"])
        assert json.loads(assigned.details)["to"] == "member" and json.loads(assigned.details)["from"] is None
        url = f"/api/boards/{lead['board']}/cards/{card.id}"
        assert client.put(url, json={"title": "Renamed"}, headers=bearer(users["member"])).status_code == 200
        assert client.put(url, json={"title": "Hijack"}, headers=bearer(users["other"])).status_code == 403

    def test_admin_created_card_stays_unassigned_with_only_a_create_audit(self, client, users, lead, db):
        response = client.post(f"/api/boards/{lead['board']}/cards", json={"column_id": lead["columns"][0], "title": "New"}, headers=bearer(users["admin"]))
        assert db.get(Card, response.json()["id"]).salesperson_id is None
        assert [e.action for e in db.query(AuditEvent)] == ["create"]

    def test_member_badge_and_history_unchanged_for_permitted_edits(self, client, users, lead, db):
        assign(db, lead, "member")
        assert put(client, lead, users["member"], {"title": "Member title"}).json()["has_member_edits"] is True
        assert db.query(CardEditHistory).count() == 1
        put(client, lead, users["other"], {"title": "Refused"})
        assert db.query(CardEditHistory).count() == 1  # the refused edit left no history

    def test_stage_transition_rules_are_unchanged(self, client, users, lead, db):
        move = lambda token, idx: client.put(
            f"/api/boards/{lead['board']}/cards/{lead['card']}/position",
            json={"column_id": lead["columns"][idx], "position": 0}, headers=bearer(token))
        assign(db, lead, "member")
        assert move(users["member"], 2).status_code == 200  # forward
        assert move(users["member"], 0).status_code == 403  # never back
        assert move(users["admin"], 0).status_code == 200

    def test_undo_cannot_bypass_ownership(self, client, users, lead, db):
        assign(db, lead, "member")
        put(client, lead, users["member"], {"company": "Edited"})
        assign(db, lead, "other")
        undo = client.post(f"/api/boards/{lead['board']}/undo", headers=bearer(users["member"]))
        assert undo.status_code == 409 and db.get(Card, lead["card"]).company == "Edited"

    def test_undoing_an_admin_assignment_is_audited(self, client, users, lead, db):
        put(client, lead, users["admin"], {"salesperson_id": lead["ids"]["member"]})
        assert client.post(f"/api/boards/{lead['board']}/undo", headers=bearer(users["admin"])).status_code == 200
        assert db.get(Card, lead["card"]).salesperson_id is None
        assert [json.loads(e.details)["to"] for e in db.query(AuditEvent).order_by(AuditEvent.id)] == ["member", None]


class TestAiAuthorization:
    @pytest.mark.asyncio
    async def test_ai_update_respects_ownership(self, users, lead, db):
        member, other = (db.query(User).filter_by(username=n).one() for n in ("member", "other"))
        assign(db, lead, "member")
        act = lambda user, **f: apply_board_actions(db, lead["board"], [{"type": "update", "card_id": lead["card"], **f}], user)
        assert (await act(member, company="Mine"))["successful"]
        refused = await act(other, company="Theirs")
        assert refused["successful"] == [] and "assigned to you" in refused["failed"][0]["reason"]
        assert db.get(Card, lead["card"]).company == "Mine"

    @pytest.mark.asyncio
    async def test_ai_cannot_assign_for_a_member(self, users, lead, db):
        member = db.query(User).filter_by(username="member").one()
        assign(db, lead, "member")
        result = await apply_board_actions(db, lead["board"], [{"type": "update", "card_id": lead["card"], "salesperson_id": lead["ids"]["other"]}], member)
        assert result["successful"] == [] and db.get(Card, lead["card"]).salesperson_id == lead["ids"]["member"]

    @pytest.mark.asyncio
    async def test_ai_create_by_member_is_self_assigned_and_audited(self, users, lead, db):
        member = db.query(User).filter_by(username="member").one()
        result = await apply_board_actions(db, lead["board"], [{"type": "create", "column_id": lead["columns"][0], "title": "AI", "salesperson_id": lead["ids"]["other"]}], member)
        card = db.get(Card, result["successful"][0]["card_id"])
        assert card.salesperson_id == lead["ids"]["member"]
        assert [e.action for e in db.query(AuditEvent).order_by(AuditEvent.id)] == ["create", "assign"]

    @pytest.mark.asyncio
    async def test_ai_delete_is_admin_only_and_audited(self, users, lead, db):
        member, admin = (db.query(User).filter_by(username=n).one() for n in ("member", "admin"))
        act = lambda user: apply_board_actions(db, lead["board"], [{"type": "delete", "card_id": lead["card"]}], user)
        assert (await act(member))["successful"] == [] and db.query(AuditEvent).count() == 0
        assert (await act(admin))["successful"]
        assert [e.action for e in db.query(AuditEvent)] == ["delete"]


# --- Phase 1 closeout: movement ownership -----------------------------------------


def move(client, lead, token, idx):
    return client.put(
        f"/api/boards/{lead['board']}/cards/{lead['card']}/position",
        json={"column_id": lead["columns"][idx], "position": 0},
        headers=bearer(token),
    )


def column_of(db, lead):
    db.expire_all()
    return db.get(Card, lead["card"]).column_id


class TestMovementOwnership:
    def test_owner_moves_forward_and_within_a_column(self, client, users, lead, db):
        assign(db, lead, "member")
        assert move(client, lead, users["member"], 0).status_code == 200  # same column
        assert move(client, lead, users["member"], 2).status_code == 200
        assert column_of(db, lead) == lead["columns"][2]

    @pytest.mark.parametrize("owner", [None, "other"], ids=["unassigned", "someone-else"])
    def test_member_cannot_move_a_card_they_do_not_own(self, client, users, lead, db, owner):
        if owner:
            assign(db, lead, owner)
        response = move(client, lead, users["member"], 2)
        assert response.status_code == 403 and "assigned to you" in response.json()["detail"]
        assert column_of(db, lead) == lead["columns"][0]

    def test_owner_cannot_move_backward(self, client, users, lead, db):
        assign(db, lead, "member")
        assert move(client, lead, users["member"], 3).status_code == 200
        assert move(client, lead, users["member"], 1).status_code == 403
        assert column_of(db, lead) == lead["columns"][3]

    def test_admin_moves_any_card_in_either_direction(self, client, users, lead, db):
        for owner in (None, "member", "other"):
            if owner:
                assign(db, lead, owner)
            assert move(client, lead, users["admin"], 3).status_code == 200
            assert move(client, lead, users["admin"], 0).status_code == 200

    def test_terminal_stages_stay_terminal_for_members(self, client, users, lead, db):
        assign(db, lead, "member")
        last = len(lead["columns"]) - 1
        assert move(client, lead, users["member"], last).status_code == 200
        for idx in range(last):
            assert move(client, lead, users["member"], idx).status_code == 403
        assert column_of(db, lead) == lead["columns"][last]
        assert move(client, lead, users["admin"], 0).status_code == 200  # only an admin reopens it

    def test_ownership_is_read_from_the_database_on_every_request(self, client, users, lead, db):
        """A token issued while the member owned the card gives no standing claim to it."""
        assign(db, lead, "member")
        assert move(client, lead, users["member"], 1).status_code == 200
        assert put(client, lead, users["admin"], {"salesperson_id": lead["ids"]["other"]}).status_code == 200
        assert move(client, lead, users["member"], 2).status_code == 403
        assert move(client, lead, users["other"], 2).status_code == 200

    def test_a_refused_move_leaves_no_trace(self, client, users, lead, db):
        before = (db.query(AuditEvent).count(), db.query(CardEditHistory).count())
        assert move(client, lead, users["member"], 2).status_code == 403
        assert (db.query(AuditEvent).count(), db.query(CardEditHistory).count()) == before
        assert client.get(f"/api/boards/{lead['board']}/undo", headers=bearer(users["member"])).json()["available"] is False

    def test_a_permitted_move_keeps_history_badge_and_undo(self, client, users, lead, db):
        assign(db, lead, "member")
        response = move(client, lead, users["member"], 2)
        assert response.json()["has_member_edits"] is False and db.query(CardEditHistory).count() == 0
        assert client.get(f"/api/boards/{lead['board']}/undo", headers=bearer(users["member"])).json()["available"] is True

    def test_undo_of_a_move_requires_still_owning_the_card(self, client, users, lead, db):
        assign(db, lead, "member")
        assert move(client, lead, users["member"], 2).status_code == 200
        assert put(client, lead, users["admin"], {"salesperson_id": lead["ids"]["other"]}).status_code == 200
        undo = client.post(f"/api/boards/{lead['board']}/undo", headers=bearer(users["member"]))
        assert undo.status_code == 409 and "assigned to you" in undo.json()["detail"]
        assert column_of(db, lead) == lead["columns"][2]

    def test_undo_of_a_move_still_works_for_the_owner(self, client, users, lead, db):
        assign(db, lead, "member")
        assert move(client, lead, users["member"], 2).status_code == 200
        assert client.post(f"/api/boards/{lead['board']}/undo", headers=bearer(users["member"])).status_code == 200
        assert column_of(db, lead) == lead["columns"][0]

    def test_concurrent_reassignment_and_move_never_let_a_non_owner_win(self, tmp_path):
        """Racing an admin reassignment against the old owner's move: the move is
        judged on the row as committed, so it either completes while they still
        own the card or is refused - never applied on a stale ownership."""
        import card_rules
        from models import Board, Column

        engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        with Session() as s:
            s.add_all([User(username="admin", password_hash="x", role="admin", email_verified_at=VERIFIED),
                       User(username="old", password_hash="x", email_verified_at=VERIFIED),
                       User(username="new", password_hash="x", email_verified_at=VERIFIED)])
            s.flush()
            board = Board(user_id=1, title="b")
            s.add(board)
            s.flush()
            s.add_all([Column(board_id=board.id, title=t, position=i) for i, t in enumerate("ab")])
            s.flush()
            s.add(Card(column_id=1, title="c", position=0, salesperson_id=2))
            s.commit()

        barrier = threading.Barrier(2)
        outcome = {}

        def reassign():
            with Session() as s:
                barrier.wait()
                s.get(Card, 1).salesperson_id = 3
                s.commit()

        def old_owner_moves():
            with Session() as s:
                barrier.wait()
                card, user, target = s.get(Card, 1), s.get(User, 2), s.get(Column, 2)
                outcome["denial"] = card_rules.move_denial(user, card, target.position)
                outcome["owner_seen"] = card.salesperson_id

        threads = [threading.Thread(target=reassign), threading.Thread(target=old_owner_moves)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        # Whichever committed first, the verdict matches the owner that was read.
        assert (outcome["denial"] is None) == (outcome["owner_seen"] == 2)
        with Session() as s:
            assert s.get(Card, 1).salesperson_id == 3
            assert card_rules.move_denial(s.get(User, 2), s.get(Card, 1), 1) is not None


class TestMovementOwnershipViaAi:
    @pytest.mark.asyncio
    async def test_ai_move_respects_ownership_and_direction(self, users, lead, db):
        member, admin = (db.query(User).filter_by(username=n).one() for n in ("member", "admin"))
        act = lambda user, idx: apply_board_actions(
            db, lead["board"], [{"type": "move", "card_id": lead["card"], "column_id": lead["columns"][idx]}], user)

        refused = await act(member, 2)  # unassigned
        assert refused["successful"] == [] and "assigned to you" in refused["failed"][0]["reason"]
        assign(db, lead, "other")
        assert (await act(member, 2))["successful"] == []  # someone else's
        assign(db, lead, "member")
        assert (await act(member, 2))["successful"]
        assert "forward" in (await act(member, 0))["failed"][0]["reason"]
        assert (await act(admin, 0))["successful"]
        assert column_of(db, lead) == lead["columns"][0]


# --- Phase 1 closeout: creation and initial assignment -----------------------------


class TestCreationOwnership:
    def post_card(self, client, lead, token, **extra):
        return client.post(
            f"/api/boards/{lead['board']}/cards",
            json={"column_id": lead["columns"][0], "title": "Fresh", **extra},
            headers=bearer(token),
        )

    def test_member_creation_makes_them_the_owner_and_they_can_edit_and_move(self, client, users, lead, db):
        response = self.post_card(client, lead, users["member"])
        card_id = response.json()["id"]
        assert response.status_code == 201 and db.get(Card, card_id).salesperson_id == lead["ids"]["member"]
        url = f"/api/boards/{lead['board']}/cards/{card_id}"
        assert client.put(url, json={"company": "Mine Oy"}, headers=bearer(users["member"])).status_code == 200
        assert client.put(f"{url}/position", json={"column_id": lead["columns"][1], "position": 0}, headers=bearer(users["member"])).status_code == 200

    @pytest.mark.parametrize("field", ["salesperson_id", "salesperson_name", "owner_id", "user_id"])
    def test_a_client_supplied_owner_is_ignored(self, client, users, lead, db, field):
        response = self.post_card(client, lead, users["member"], **{field: lead["ids"]["other"]})
        assert response.status_code == 201
        assert db.get(Card, response.json()["id"]).salesperson_id == lead["ids"]["member"]

    def test_creation_and_initial_assignment_are_both_audited(self, client, users, lead, db):
        card_id = self.post_card(client, lead, users["member"]).json()["id"]
        events = db.query(AuditEvent).order_by(AuditEvent.id).all()
        assert [(e.action, e.card_id, e.actor_user_id) for e in events] == [
            ("create", card_id, lead["ids"]["member"]), ("assign", card_id, lead["ids"]["member"])]
        assert json.loads(events[1].details)["from"] is None

    def test_a_member_still_cannot_reassign_an_existing_card(self, client, users, lead, db):
        card_id = self.post_card(client, lead, users["member"]).json()["id"]
        url = f"/api/boards/{lead['board']}/cards/{card_id}"
        assert client.put(url, json={"salesperson_id": lead["ids"]["other"]}, headers=bearer(users["member"])).status_code == 403
        assert db.get(Card, card_id).salesperson_id == lead["ids"]["member"]

    def test_admin_creation_and_assignment_are_preserved(self, client, users, lead, db):
        card_id = self.post_card(client, lead, users["admin"]).json()["id"]
        assert db.get(Card, card_id).salesperson_id is None
        url = f"/api/boards/{lead['board']}/cards/{card_id}"
        assert client.put(url, json={"salesperson_id": lead["ids"]["other"]}, headers=bearer(users["admin"])).status_code == 200
        assert [e.action for e in db.query(AuditEvent).order_by(AuditEvent.id)] == ["create", "assign"]


# --- Phase 1 closeout: administrator-created users ---------------------------------


@pytest.fixture
def invites(monkeypatch):
    """Mail sent by the admin interface: (recipient, body) pairs."""
    messages = []
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: messages.append((to, body)))
    return messages


def invite(client, admin_token, email="newhire@example.com", **extra):
    return client.post("/api/users", json={"email": email, **extra}, headers=bearer(admin_token))


class TestAdminCreatedUsers:
    def test_invited_account_is_unverified_and_cannot_log_in(self, client, users, db, invites):
        response = invite(client, users["admin"], password="Smuggled123!")
        assert response.status_code == 201 and response.json()["role"] == "member"
        user = db.query(User).filter_by(username="newhire@example.com").one()
        assert user.email == "newhire@example.com" and user.email_verified_at is None
        assert user.password_hash == auth.UNUSABLE_PASSWORD_HASH
        for attempt in ("", "!", "Smuggled123!"):
            assert login(client, "newhire@example.com", attempt).status_code == 401

    def test_response_carries_no_link_or_token(self, client, users, db, invites):
        response = invite(client, users["admin"])
        raw = invites[0][1].split("reset_token=")[1].split()[0]
        assert raw not in response.text and "token" not in response.text

    def test_verification_link_is_emailed_and_grants_access_once(self, client, users, db, invites):
        invite(client, users["admin"])
        (to, body), = invites
        assert to == "newhire@example.com"
        raw = body.split("reset_token=")[1].split()[0]
        done = client.post("/api/password/reset", json={"token": raw, "password": "Newhire123!"})
        assert done.status_code == 200
        assert db.query(User).filter_by(username="newhire@example.com").one().email_verified_at is not None
        logged_in = login(client, "newhire@example.com", "Newhire123!")
        assert logged_in.status_code == 200 and logged_in.json()["role"] == "member"
        assert client.post("/api/password/reset", json={"token": raw, "password": "Another123!"}).status_code == 400

    def test_expired_link_does_not_verify(self, client, users, db, invites):
        invite(client, users["admin"])
        db.query(AuthToken).update({"expires_at": datetime(2020, 1, 1)})
        db.commit()
        raw = invites[0][1].split("reset_token=")[1].split()[0]
        assert client.post("/api/password/reset", json={"token": raw, "password": "Newhire123!"}).status_code == 400
        assert db.query(User).filter_by(username="newhire@example.com").one().email_verified_at is None
        assert login(client, "newhire@example.com", "Newhire123!").status_code == 401

    def test_only_a_hash_of_the_token_is_stored(self, client, users, db, invites):
        invite(client, users["admin"])
        raw = invites[0][1].split("reset_token=")[1].split()[0]
        stored = db.query(AuthToken).one()
        assert stored.token_hash == token_store.hash_token(raw) and raw not in (stored.token_hash or "")

    def test_nothing_is_logged_with_the_link(self, client, users, invites, caplog):
        with caplog.at_level(logging.DEBUG):
            invite(client, users["admin"])
        raw = invites[0][1].split("reset_token=")[1].split()[0]
        assert raw not in caplog.text and "reset_token" not in caplog.text

    def test_duplicates_in_any_case_are_rejected_and_send_nothing(self, client, users, invites):
        assert invite(client, users["admin"]).status_code == 201
        assert invite(client, users["admin"], email="NewHire@Example.com").status_code == 409
        assert len(invites) == 1

    def test_non_admins_cannot_invite_and_input_must_be_an_email(self, client, users, db, invites):
        assert invite(client, users["member"]).status_code == 403
        assert client.post("/api/users", json={"email": "not-an-email"}, headers=bearer(users["admin"])).status_code == 422
        assert db.query(User).filter_by(username="newhire@example.com").count() == 0 and invites == []

    def test_invited_users_are_always_members(self, client, users, db, invites):
        assert invite(client, users["admin"], role="admin").json()["role"] == "member"


# --- Phase 1 closeout: /api/setup -----------------------------------------------


SETUP_BODY = {"username": "first", "password": "Adminpass123!"}


class TestSetupEndpoint:
    def test_disabled_by_default_even_on_an_empty_database(self, client, db, monkeypatch):
        monkeypatch.delenv("PM_SETUP_TOKEN", raising=False)
        assert client.get("/api/setup/status").json() == {"needs_setup": True, "setup_enabled": False}
        for headers in ({}, SETUP_HEADERS):
            response = client.post("/api/setup", json=SETUP_BODY, headers=headers)
            assert response.status_code == 403 and "disabled" in response.json()["detail"]
        assert db.query(User).count() == 0

    def test_a_short_configured_key_leaves_setup_disabled(self, client, db, monkeypatch):
        monkeypatch.setenv("PM_SETUP_TOKEN", "short-key")
        assert client.post("/api/setup", json=SETUP_BODY, headers={"X-Setup-Token": "short-key"}).status_code == 403
        assert client.get("/api/setup/status").json()["setup_enabled"] is False
        assert db.query(User).count() == 0

    @pytest.mark.parametrize("headers", [{}, {"X-Setup-Token": "x" * 40}, {"X-Setup-Token": ""}], ids=["missing", "wrong", "empty"])
    def test_enabled_setup_still_refuses_a_caller_without_the_key(self, client, db, headers):
        response = client.post("/api/setup", json=SETUP_BODY, headers=headers)
        assert response.status_code == 403 and "access_token" not in response.text
        assert db.query(User).count() == 0

    def test_the_key_is_not_accepted_in_the_body_or_query(self, client, db):
        from auth_test_utils import SETUP_TOKEN
        assert client.post(f"/api/setup?token={SETUP_TOKEN}", json={**SETUP_BODY, "token": SETUP_TOKEN}).status_code == 403
        assert db.query(User).count() == 0

    def test_valid_key_provisions_exactly_one_admin_and_then_closes(self, client, db):
        response = client.post("/api/setup", json=SETUP_BODY, headers=SETUP_HEADERS)
        assert response.status_code == 201 and response.json()["role"] == "admin"
        assert db.query(User).filter_by(role="admin").count() == 1
        again = client.post("/api/setup", json={"username": "second", "password": "Adminpass123!"}, headers=SETUP_HEADERS)
        assert again.status_code == 409
        assert [u.username for u in db.query(User)] == ["first"]
        assert client.get("/api/setup/status").json()["needs_setup"] is False

    def test_setup_stays_closed_once_users_exist_even_if_the_key_is_removed_and_restored(self, client, db, monkeypatch):
        client.post("/api/setup", json=SETUP_BODY, headers=SETUP_HEADERS)
        monkeypatch.delenv("PM_SETUP_TOKEN")
        assert client.post("/api/setup", json=SETUP_BODY, headers=SETUP_HEADERS).status_code == 403
        monkeypatch.setenv("PM_SETUP_TOKEN", SETUP_HEADERS["X-Setup-Token"])
        assert client.post("/api/setup", json=SETUP_BODY, headers=SETUP_HEADERS).status_code == 409
        assert db.query(User).count() == 1

    def test_the_commercial_workflow_without_a_usable_password_is_unaffected(self, client, db):
        owner = admin_cli.create_admin(db, "owner@example.com", None)
        assert owner.password_hash == auth.UNUSABLE_PASSWORD_HASH and owner.role == "admin"
        assert client.post("/api/setup", json=SETUP_BODY, headers=SETUP_HEADERS).status_code == 409
