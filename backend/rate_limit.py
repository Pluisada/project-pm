"""In-memory rate limiting for the abuse-prone endpoints.

Sliding-window counters, per process (the app runs as one uvicorn worker on
SQLite; see docs/SECURITY.md for what that implies). Applied to login, signup,
forgot-password, the admin Bid import and the AI chat. Every limit answers with the same
generic 429, so a limit never reveals whether an account exists.

Configuration is read from the environment on each use (format "count/seconds"),
and validated at startup by `validate_config()`.
"""
import ipaddress
import logging
import os
import threading
import time
from collections import deque
from typing import Callable, Optional

from fastapi import Depends, HTTPException, Request, status

from deps import get_current_user, require_admin
from models import User

logger = logging.getLogger(__name__)

# name -> (environment variable, default "count/seconds")
DEFAULT_LIMITS = {
    # Every login attempt from one address.
    "login_ip": ("RATE_LIMIT_LOGIN_IP", "30/300"),
    "signup_ip": ("RATE_LIMIT_SIGNUP_IP", "5/3600"),
    "signup_email": ("RATE_LIMIT_SIGNUP_EMAIL", "3/3600"),
    "forgot_ip": ("RATE_LIMIT_FORGOT_IP", "10/3600"),
    # Reset emails actually issued for one account (keyed by user id, not by
    # the typed identifier, so unknown names and mailless accounts never use it).
    # At the limit the request still gets the generic answer, but no mail.
    "forgot_account": ("RATE_LIMIT_FORGOT_ACCOUNT", "3/3600"),
    # Bid import preview and confirm, per admin.
    "import_user": ("RATE_LIMIT_IMPORT_USER", "10/60"),
    # AI chat, per user: a burst limit and an hourly ceiling (each message is a
    # paid model call).
    "ai_user": ("RATE_LIMIT_AI_USER", "10/60"),
    "ai_user_hour": ("RATE_LIMIT_AI_USER_HOUR", "100/3600"),
}
# Progressive login delay, "free/first/cap/reset" in seconds (see LoginBackoff).
LOGIN_BACKOFF = ("RATE_LIMIT_LOGIN_BACKOFF", "3/5/300/900")
TOO_MANY = "Too many requests. Try again later."
MAX_KEYS = 50_000
MAX_KEY_LENGTH = 254


def enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _parse(value: str, var: str) -> tuple[int, int]:
    try:
        count, seconds = (int(part) for part in value.split("/"))
        if count < 1 or seconds < 1:
            raise ValueError
    except ValueError:
        raise ValueError(f'{var} must look like "count/seconds" with positive integers, got {value!r}') from None
    return count, seconds


def get_limit(name: str) -> tuple[int, int]:
    var, default = DEFAULT_LIMITS[name]
    return _parse(os.getenv(var, "").strip() or default, var)


def get_login_backoff() -> tuple[int, int, int, int]:
    var, default = LOGIN_BACKOFF
    value = os.getenv(var, "").strip() or default
    try:
        free, first, cap, reset = (int(part) for part in value.split("/"))
        if free < 1 or first < 1 or cap < first or reset < 1:
            raise ValueError
    except ValueError:
        raise ValueError(f'{var} must look like "free/first/cap/reset" (positive integers, cap >= first), got {value!r}') from None
    return free, first, cap, reset


def trusted_proxies() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    raw = os.getenv("RATE_LIMIT_TRUSTED_PROXIES", "")
    try:
        networks = [ipaddress.ip_network(item.strip(), strict=False) for item in raw.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError(f"RATE_LIMIT_TRUSTED_PROXIES must be comma-separated IPs or CIDRs: {exc}") from None
    # A catch-all would trust every peer, so any client could choose its own address.
    if any(net.prefixlen == 0 for net in networks):
        raise ValueError("RATE_LIMIT_TRUSTED_PROXIES must not contain 0.0.0.0/0 or ::/0; list only the proxy address(es)")
    return networks


def validate_config() -> None:
    """Fail at startup on a malformed setting instead of on the first request."""
    for name in DEFAULT_LIMITS:
        get_limit(name)
    get_login_backoff()
    trusted_proxies()
    if not enabled():
        logger.warning("Rate limiting is DISABLED (RATE_LIMIT_ENABLED)")


class RateLimiter:
    """Thread-safe sliding-window counters keyed by string."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[deque[float], int]] = {}

    def _live(self, key: str, window: int, now: float) -> Optional[deque[float]]:
        entry = self._hits.get(key)
        if entry is None:
            return None
        hits = entry[0]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if not hits:
            del self._hits[key]
            return None
        return hits

    def retry_after(self, key: str, limit: int, window: int) -> int:
        """Seconds until `key` may act again; 0 when it is under the limit."""
        with self._lock:
            now = self._clock()
            hits = self._live(key, window, now)
            if hits is None or len(hits) < limit:
                return 0
            return max(1, int(hits[-limit] + window - now) + 1)

    def record(self, key: str, window: int) -> None:
        with self._lock:
            now = self._clock()
            hits = self._live(key, window, now)
            if hits is None:
                if len(self._hits) >= MAX_KEYS:
                    self._evict(now)
                hits = deque()
                self._hits[key] = (hits, window)
            hits.append(now)

    def _evict(self, now: float) -> None:
        for key in [k for k, (h, w) in self._hits.items() if not h or h[-1] <= now - w]:
            del self._hits[key]
        # Still full of live keys: drop the oldest-created ones. Reaching this
        # takes MAX_KEYS distinct keys inside their windows.
        while len(self._hits) >= MAX_KEYS:
            del self._hits[next(iter(self._hits))]

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


class LoginBackoff:
    """Progressive delay after repeated failed logins, per (account, client).

    The key includes the caller's address on purpose: a delay keyed on the
    account alone would let anyone who types the administrator's name lock the
    administrator out. Here a stranger only delays logins to that account from
    the stranger's own address. After `free` failures the next attempts are
    refused for `first` seconds, doubling with each further failure up to `cap`.
    Refused attempts are not counted and do not extend the delay. The count
    resets on a successful login or after `reset` seconds without a failure.
    """

    def __init__(self, clock: Callable[[], float]):
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, list[float]] = {}  # key -> [failures, blocked_until, last_failure]

    def retry_after(self, key: str) -> int:
        _, _, _, reset = get_login_backoff()
        with self._lock:
            now = self._clock()
            entry = self._state.get(key)
            if entry is None:
                return 0
            if now - entry[2] >= reset:
                del self._state[key]
                return 0
            return max(0, int(entry[1] - now) + 1) if entry[1] > now else 0

    def failure(self, key: str) -> None:
        free, first, cap, reset = get_login_backoff()
        with self._lock:
            now = self._clock()
            entry = self._state.get(key)
            if entry is None or now - entry[2] >= reset:
                if entry is None and len(self._state) >= MAX_KEYS:
                    self._evict(now, reset)
                entry = self._state[key] = [0, 0.0, now]
            entry[0] += 1
            entry[2] = now
            if entry[0] >= free:
                entry[1] = now + min(cap, first * 2 ** (entry[0] - free))

    def success(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)

    def _evict(self, now: float, reset: int) -> None:
        for k in [k for k, e in self._state.items() if now - e[2] >= reset]:
            del self._state[k]
        while len(self._state) >= MAX_KEYS:
            del self._state[next(iter(self._state))]

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


login_backoff = LoginBackoff(lambda: limiter._clock())


def _login_key(account: str, ip: str) -> str:
    return f"{account[:MAX_KEY_LENGTH]}|{ip}"


def check_login_delay(account: str, ip: str) -> None:
    """Raise 429 while this client is serving a delay for this account."""
    if not enabled():
        return
    wait = login_backoff.retry_after(_login_key(account, ip))
    if wait:
        raise _too_many(wait)


def record_login_failure(account: str, ip: str) -> None:
    if enabled():
        login_backoff.failure(_login_key(account, ip))


def clear_login_failures(account: str, ip: str) -> None:
    login_backoff.success(_login_key(account, ip))


def _too_many(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=TOO_MANY,
        headers={"Retry-After": str(retry_after)},
    )


def check(name: str, key: str) -> None:
    """Raise 429 if `key` is at the `name` limit. Does not count anything."""
    if not enabled():
        return
    limit, window = get_limit(name)
    wait = limiter.retry_after(f"{name}:{key[:MAX_KEY_LENGTH]}", limit, window)
    if wait:
        raise _too_many(wait)


def record(name: str, key: str) -> None:
    if not enabled():
        return
    _, window = get_limit(name)
    limiter.record(f"{name}:{key[:MAX_KEY_LENGTH]}", window)


def hit(name: str, key: str) -> None:
    """Count one request against `key` and raise 429 once it is over the limit.

    Blocked requests are not counted, so hammering a locked key doesn't extend
    the lock beyond one window.
    """
    check(name, key)
    record(name, key)


def try_hit(name: str, key: str) -> bool:
    """Like `hit`, but returns False at the limit instead of raising, for callers
    that must answer the same way whether or not the limit was reached."""
    try:
        hit(name, key)
    except HTTPException:
        return False
    return True


def clear(name: str, key: str) -> None:
    limiter.clear(f"{name}:{key[:MAX_KEY_LENGTH]}")


def identifier_key(value: str) -> str:
    """One bucket per account however it is typed (case, surrounding spaces)."""
    return value.strip().lower()


def _network_key(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    # An IPv6 user owns a whole /64; keying on the full address would let them
    # rotate through it to dodge the limit.
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def client_ip(request: Request) -> str:
    """The caller's address, honouring X-Forwarded-For only from trusted proxies.

    The header is client-controlled, so it is ignored unless the direct peer is
    in RATE_LIMIT_TRUSTED_PROXIES. Then it is read right to left, skipping
    trusted proxies; the first other address is the client. Entries a client
    prepends to the header sit to the left of it and are never reached.
    """
    peer = request.client.host if request.client else "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    proxies = trusted_proxies()
    if not any(peer_ip in net for net in proxies):
        return _network_key(peer_ip)
    forwarded = [p.strip() for h in request.headers.getlist("x-forwarded-for") for p in h.split(",") if p.strip()]
    for item in reversed(forwarded):
        try:
            addr = ipaddress.ip_address(item)
        except ValueError:
            break  # malformed: don't guess, fall back to the proxy
        if not any(addr in net for net in proxies):
            return _network_key(addr)
    return _network_key(peer_ip)


def limit_imports(request: Request, current_user: User = Depends(require_admin)) -> None:
    """Dependency for the Bid import POSTs. Runs after require_admin, so
    anonymous and non-admin callers get 401/403 and never touch the counters."""
    hit("import_user", str(current_user.id))


def limit_ai_chat(current_user: User = Depends(get_current_user)) -> None:
    """Dependency for the AI chat POST. Runs after authentication, so anonymous
    callers get 401 and never touch the counters. Both windows are checked
    before either is counted, so a blocked request uses no quota."""
    key = str(current_user.id)
    check("ai_user", key)
    check("ai_user_hour", key)
    record("ai_user", key)
    record("ai_user_hour", key)
