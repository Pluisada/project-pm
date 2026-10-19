"""Rate limiting on login, signup, forgot-password and the Bid import."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

import main
import rate_limit
from auth import create_user_access_token, hash_password
from database import get_db
from main import app
from models import Base, User
from auth_test_utils import VERIFIED


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(rate_limit.limiter, "_clock", fake)
    return fake


class FromAddress:
    """ASGI wrapper that sets the TCP peer address, which TestClient can't."""

    def __init__(self, asgi_app, ip):
        self.app, self.ip = asgi_app, ip

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": (self.ip, 5000)}
        await self.app(scope, receive, send)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all([
        User(email_verified_at=VERIFIED, username="admin", password_hash=hash_password("Adminpass123!"), role="admin"),
        User(email_verified_at=VERIFIED, username="member@example.com", email="member@example.com",
             password_hash=hash_password("Memberpass123!"), role="member"),
    ])
    db.commit()
    yield db
    db.close()


@pytest.fixture
def make_client(session):
    app.dependency_overrides[get_db] = lambda: session

    def make(ip="203.0.113.7"):
        return TestClient(FromAddress(app, ip))

    yield make
    app.dependency_overrides.clear()


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(main, "send_email", lambda to, subject, body: messages.append(to))
    return messages


def login(client, username="admin", password="Adminpass123!"):
    return client.post("/api/login", json={"username": username, "password": password})


# --- login -------------------------------------------------------------------------

def backoff(clock, monkeypatch, value="2/10/40/100"):  # free 2, then 10s, 20s, 40s (cap)
    monkeypatch.setenv("RATE_LIMIT_LOGIN_BACKOFF", value)


def test_failures_within_the_free_allowance_are_not_delayed(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_LOGIN_BACKOFF", "3/10/40/100")
    assert [login(client, password="wrong").status_code for _ in range(2)] == [401, 401]
    assert login(client).status_code == 200


def test_delay_progression_exact(client, clock, monkeypatch):
    backoff(clock, monkeypatch)
    assert login(client, password="wrong").status_code == 401  # failure 1: free
    assert login(client, password="wrong").status_code == 401  # failure 2: 10s delay
    for expected in (10, 20, 40, 40, 40):
        blocked = login(client, password="wrong")
        assert blocked.status_code == 429
        assert 1 <= int(blocked.headers["Retry-After"]) <= expected + 1
        clock.now += expected - 1
        assert login(client, password="wrong").status_code == 429  # 1s still to go
        clock.now += 2
        assert login(client, password="wrong").status_code == 401  # delay elapsed: next failure


def test_delayed_client_is_refused_even_with_the_correct_password(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_LOGIN_BACKOFF", "2/10/40/100")
    for _ in range(2):
        login(client, password="wrong")
    assert login(client).status_code == 429


def test_login_works_once_the_delay_has_elapsed(client, clock, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_LOGIN_BACKOFF", "2/10/40/100")
    for _ in range(2):
        login(client, password="wrong")
    clock.now += 9
    assert login(client).status_code == 429
    clock.now += 2
    assert login(client).status_code == 200


def test_blocked_attempts_do_not_extend_the_delay(client, clock, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_LOGIN_BACKOFF", "2/10/40/100")
    monkeypatch.setenv("RATE_LIMIT_LOGIN_IP", "1000/300")
    for _ in range(2):
        login(client, password="wrong")
    for _ in range(8):
        clock.now += 1
        assert login(client, password="wrong").status_code == 429
    clock.now += 3  # 11s after the failures, though blocked tries kept coming
    assert login(client).status_code == 200


def test_failure_count_resets_after_the_idle_period(client, clock, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_LOGIN_BACKOFF", "2/10/40/100")
    for _ in range(2):
        login(client, password="wrong")
    clock.now += 101
    # Back at zero: one failure is inside the free allowance again.
    assert login(client, password="wrong").status_code == 401
    assert login(client, password="wrong").status_code == 401
    assert login(client, password="wrong").status_code == 429


def test_successful_login_clears_the_failure_count(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_LOGIN_BACKOFF", "3/10/40/100")
    for _ in range(2):
        login(client, password="wrong")
    assert login(client).status_code == 200
    for _ in range(2):
        assert login(client, password="wrong").status_code == 401
    assert login(client).status_code == 200


def test_strangers_cannot_lock_the_admin_out(make_client, clock, monkeypatch):
    """The old policy locked the account for 15 minutes after 5 failures from anyone."""
    monkeypatch.setenv("RATE_LIMIT_LOGIN_IP", "1000/300")
    for i in range(1, 41):
        attacker = make_client(f"198.51.100.{i}")
        for _ in range(8):
            login(attacker, "admin", "wrong")
    assert login(make_client("203.0.113.50"), "admin").status_code == 200
    # Same time instant, a normal client at another address, no waiting needed.
    assert login(make_client("203.0.113.51"), "admin").status_code == 200


def test_delay_is_per_client_not_per_account(make_client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_LOGIN_BACKOFF", "2/10/40/100")
    attacker = make_client("198.51.100.1")
    for _ in range(2):
        login(attacker, "admin", "wrong")
    assert login(attacker, "admin", "wrong").status_code == 429
    assert login(make_client("198.51.100.2"), "admin").status_code == 200
    # The attacker is delayed for that account only.
    assert login(attacker, "other", "wrong").status_code == 401


def test_delay_follows_the_spelling_of_the_account(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_LOGIN_BACKOFF", "2/10/40/100")
    for name in ("admin", " ADMIN "):
        login(client, name, "wrong")
    assert login(client, "Admin", "wrong").status_code == 429


def test_unknown_and_real_accounts_are_delayed_identically(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_LOGIN_BACKOFF", "2/10/40/100")
    outcomes = {name: [login(client, name, "wrong") for _ in range(3)] for name in ("admin", "ghost")}
    for name, responses in outcomes.items():
        assert [r.status_code for r in responses] == [401, 401, 429], name
    assert outcomes["admin"][2].json() == outcomes["ghost"][2].json()
    assert outcomes["admin"][0].json() == outcomes["ghost"][0].json()


def test_login_ip_limit_counts_every_attempt(client, make_client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_LOGIN_IP", "3/60")
    for i in range(3):
        assert login(client, f"user{i}", "x").status_code == 401
    assert login(client, "another", "x").status_code == 429
    # A different address is unaffected.
    assert login(make_client("198.51.100.9"), "another", "x").status_code == 401


# --- signup ------------------------------------------------------------------------

def test_signup_email_limit_same_for_new_and_registered_addresses(client, sent, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_SIGNUP_EMAIL", "2/3600")
    for email in ("new@example.com", "member@example.com"):
        responses = [client.post("/api/signup", json={"email": email}) for _ in range(3)]
        assert [r.status_code for r in responses] == [202, 202, 429], email
    assert sent.count("new@example.com") == 2
    assert "member@example.com" not in sent


def test_signup_ip_limit_and_recovery(client, clock, sent, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_SIGNUP_IP", "2/3600")
    assert client.post("/api/signup", json={"email": "a@example.com"}).status_code == 202
    assert client.post("/api/signup", json={"email": "b@example.com"}).status_code == 202
    assert client.post("/api/signup", json={"email": "c@example.com"}).status_code == 429
    clock.now += 3601
    assert client.post("/api/signup", json={"email": "c@example.com"}).status_code == 202


# --- forgot-password ---------------------------------------------------------------

def forgot(client, identifier):
    return client.post("/api/password/forgot", json={"identifier": identifier})


def test_forgot_account_quota_counts_only_issued_resets(client, sent, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_FORGOT_ACCOUNT", "2/3600")
    monkeypatch.setenv("RATE_LIMIT_FORGOT_IP", "100/3600")
    known = [forgot(client, "member@example.com") for _ in range(3)]
    unknown = [forgot(client, "ghost@example.com") for _ in range(3)]
    # Over the quota the answer is still the generic one, with no mail.
    assert [r.status_code for r in known + unknown] == [200] * 6
    assert len({r.text for r in known + unknown}) == 1
    assert sent == ["member@example.com"] * 2


def test_unauthenticated_requests_cannot_exhaust_the_quota_without_a_reset(client, sent, monkeypatch, session):
    monkeypatch.setenv("RATE_LIMIT_FORGOT_ACCOUNT", "2/3600")
    monkeypatch.setenv("RATE_LIMIT_FORGOT_IP", "100/3600")
    session.query(User).filter_by(username="admin").update({"email": "admin@example.com"})
    session.commit()
    # Unknown names, near-misses and an email-less account are not eligible resets.
    for name in ["ghost@example.com", "admin@example.org", "", " "] * 3:
        forgot(client, name)
    session.query(User).filter_by(username="member@example.com").update({"email": None})
    session.commit()
    for _ in range(5):
        forgot(client, "member@example.com")
    assert sent == []
    # The administrator's quota is untouched: a legitimate request still sends.
    assert forgot(client, "admin").status_code == 200
    assert sent == ["admin@example.com"]


def test_forgot_ip_limit(client, sent, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_FORGOT_IP", "2/3600")
    codes = [client.post("/api/password/forgot", json={"identifier": f"u{i}@example.com"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]


def test_forgot_ip_limit_still_429_for_unknown_identifiers(client, sent, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_FORGOT_IP", "2/3600")
    codes = [forgot(client, "ghost@example.com").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_existing_auth_responses_unchanged_under_limits(client, sent):
    assert login(client, password="wrong").json() == {"detail": "Invalid credentials"}
    ok = client.post("/api/password/forgot", json={"identifier": "member@example.com"})
    assert ok.json() == {"message": main.RESET_SENT_MESSAGE}
    assert client.post("/api/signup", json={"email": "x@example.com"}).json() == {"message": main.SIGNUP_SENT_MESSAGE}


# --- Bid import --------------------------------------------------------------------

def auth(name):
    db = app.dependency_overrides[get_db]()
    user = db.query(User).filter(User.username == name).one()
    return {"Authorization": f"Bearer {create_user_access_token(user)}"}


def preview(client, headers):
    # Board 999 does not exist: past the limiter the call ends in a plain 404.
    return client.post("/api/boards/999/imports/preview", headers=headers)


def test_import_limited_per_admin_and_recovers(client, clock, session, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_IMPORT_USER", "2/60")
    session.add(User(email_verified_at=VERIFIED, username="admin2", password_hash="x", role="admin"))
    session.commit()
    assert [preview(client, auth("admin")).status_code for _ in range(3)] == [404, 404, 429]
    assert preview(client, auth("admin2")).status_code == 404  # separate bucket
    clock.now += 61
    assert preview(client, auth("admin")).status_code == 404


def test_import_confirm_shares_the_admin_bucket(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_IMPORT_USER", "2/60")
    headers = auth("admin")
    preview(client, headers)
    preview(client, headers)
    assert client.post("/api/boards/999/imports", headers=headers).status_code == 429


def test_import_rejects_anonymous_and_members_before_counting(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_IMPORT_USER", "1/60")
    for _ in range(3):
        assert preview(client, {}).status_code == 401
        assert preview(client, auth("member@example.com")).status_code == 403
    assert preview(client, auth("admin")).status_code == 404  # admin's bucket untouched


# --- client address and proxies ----------------------------------------------------

def request(peer, forwarded=()):
    headers = [(b"x-forwarded-for", value.encode()) for value in forwarded]
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


def test_forwarded_header_ignored_without_trusted_proxies():
    assert rate_limit.client_ip(request("203.0.113.7", ["1.2.3.4"])) == "203.0.113.7"


def test_forwarded_header_ignored_from_untrusted_peer(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_TRUSTED_PROXIES", "10.0.0.0/8")
    assert rate_limit.client_ip(request("203.0.113.7", ["1.2.3.4"])) == "203.0.113.7"


def test_forwarded_header_read_from_the_right_behind_trusted_proxies(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_TRUSTED_PROXIES", "10.0.0.0/8")
    # The client forged "9.9.9.9"; the proxy appended the real address.
    req = request("10.0.0.2", ["9.9.9.9, 198.51.100.4", "10.0.0.3"])
    assert rate_limit.client_ip(req) == "198.51.100.4"


def test_malformed_forwarded_entry_falls_back_to_the_proxy(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_TRUSTED_PROXIES", "10.0.0.0/8")
    assert rate_limit.client_ip(request("10.0.0.2", ["not-an-ip"])) == "10.0.0.2"


def test_multiple_trusted_proxies_are_skipped_right_to_left(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_TRUSTED_PROXIES", "192.0.2.1, 198.51.100.0/24")
    req = request("192.0.2.1", ["203.0.113.50, 198.51.100.9, 198.51.100.8"])
    assert rate_limit.client_ip(req) == "203.0.113.50"


def test_only_listed_peers_are_trusted_among_several(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_TRUSTED_PROXIES", "192.0.2.1,192.0.2.2")
    assert rate_limit.client_ip(request("192.0.2.2", ["203.0.113.5"])) == "203.0.113.5"
    assert rate_limit.client_ip(request("192.0.2.3", ["203.0.113.5"])) == "192.0.2.3"


def test_untrusted_ipv6_peer_cannot_spoof_forwarded_header(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_TRUSTED_PROXIES", "192.0.2.1")
    assert rate_limit.client_ip(request("2001:db8::5", ["203.0.113.5"])) == "2001:db8::/64"


@pytest.mark.parametrize("value", ["not-an-ip", "10.0.0.0/99", "10.0.0.1,bogus", "10.0.0.256"])
def test_malformed_trusted_proxies_fail_closed(monkeypatch, value):
    monkeypatch.setenv("RATE_LIMIT_TRUSTED_PROXIES", value)
    with pytest.raises(ValueError, match="RATE_LIMIT_TRUSTED_PROXIES"):
        rate_limit.validate_config()


@pytest.mark.parametrize("value", ["0.0.0.0/0", "::/0", "192.0.2.1,0.0.0.0/0", "::/0, 192.0.2.1"])
def test_catch_all_trusted_proxies_fail_closed_in_any_environment(monkeypatch, value):
    monkeypatch.setenv("RATE_LIMIT_TRUSTED_PROXIES", value)
    with pytest.raises(ValueError, match="0.0.0.0/0"):
        rate_limit.validate_config()


def test_ipv6_addresses_share_a_bucket_per_64(monkeypatch):
    assert rate_limit.client_ip(request("2001:db8::1")) == rate_limit.client_ip(request("2001:db8::ffff"))
    assert rate_limit.client_ip(request("2001:db8::1")) != rate_limit.client_ip(request("2001:db9::1"))


def test_spoofed_forwarded_header_cannot_dodge_the_ip_limit(make_client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_LOGIN_IP", "2/60")
    client = make_client("203.0.113.7")
    codes = [login_with_header(client, f"1.1.1.{i}") for i in range(3)]
    assert codes == [401, 401, 429]


def test_trusted_proxy_gives_each_real_client_its_own_limit(make_client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_LOGIN_IP", "2/60")
    monkeypatch.setenv("RATE_LIMIT_TRUSTED_PROXIES", "10.0.0.0/8")
    proxy = make_client("10.0.0.2")
    assert [login_with_header(proxy, "198.51.100.1") for _ in range(3)] == [401, 401, 429]
    assert login_with_header(proxy, "198.51.100.2") == 401


def login_with_header(client, forwarded):
    return client.post(
        "/api/login", json={"username": "ghost", "password": "x"}, headers={"X-Forwarded-For": forwarded}
    ).status_code


# --- configuration -----------------------------------------------------------------

def test_disabled_switch_turns_limits_off(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_LOGIN_BACKOFF", "1/60/60/60")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    assert [login(client, password="wrong").status_code for _ in range(4)] == [401] * 4


@pytest.mark.parametrize("var,value", [
    ("RATE_LIMIT_LOGIN_IP", "abc"),
    ("RATE_LIMIT_LOGIN_IP", "0/60"),
    ("RATE_LIMIT_IMPORT_USER", "5"),
    ("RATE_LIMIT_LOGIN_BACKOFF", "3/5"),
    ("RATE_LIMIT_LOGIN_BACKOFF", "3/50/10/900"),
    ("RATE_LIMIT_TRUSTED_PROXIES", "10.0.0.0/99"),
])
def test_malformed_settings_fail_validation(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ValueError, match=var):
        rate_limit.validate_config()


def test_default_config_is_valid():
    rate_limit.validate_config()


def test_backoff_store_stays_bounded(monkeypatch):
    monkeypatch.setattr(rate_limit, "MAX_KEYS", 5)
    store = rate_limit.LoginBackoff(lambda: 0.0)
    for i in range(20):
        store.failure(f"k{i}")
    assert len(store._state) <= 5


def test_key_store_stays_bounded(monkeypatch):
    monkeypatch.setattr(rate_limit, "MAX_KEYS", 5)
    limiter = rate_limit.RateLimiter()
    for i in range(20):
        limiter.record(f"k{i}", 60)
    assert len(limiter._hits) <= 5
