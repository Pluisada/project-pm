"""Credential and session lifecycle against the central identity database.

Not wired to any route, to deps.py or to the login endpoint: the legacy flow
(auth.py + deps.py + the commercial `users` table) is untouched. See
docs/CENTRAL_AUTH.md for the integration dependency.

Pure functions over an identity session the caller supplies; importing this
opens no database. Credentials exist only in `identity_users`; there is no
fallback to any commercial database, and CompanyUser rows are never consulted.

Password hashing, the cost-equalizing login check and the password policy are the
existing ones (auth.py, password_policy.py). Emailed tokens are the schema's
`identity_auth_tokens` (random, SHA-256 only, single-use via one conditional
UPDATE); the invitation token is consumed by identity_service.claim_invitation.

Sessions. An access token is a signed purpose token ("identity_access") with
sub = the central user id, `pwh` (password fingerprint) and `sv` (session
version). The purpose claim keeps it out of the legacy deps.get_current_user
(decode_access_token rejects it), so a central token can never be read as a
legacy session and vice versa. It is checked against the database on every use:
the user must be active and verified, and `pwh`/`sv` must match. Password
reset/change, logout and deactivation bump `sv`, which ends all sessions.

Failures carry fixed messages: no password, token, hash, email or SQL.
"""
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    RESET_TOKEN_EXPIRE_MINUTES,
    create_purpose_token,
    decode_purpose_token,
    hash_password,
    password_fingerprint,
    verify_login_password,
    verify_password,
)
from database_targets import COMPANY_SLUGS
from identity_models import IdentityAuthToken, IdentityUser
from identity_service import (
    Acceptance,
    Conflict,
    IdentityServiceError,
    InvalidInput,
    InvitationInvalid,
    NotAuthorized,
    NotFound,
    _active_admin,
    _audit,
    _now,
    _Transaction,
    accept_invitation,
    claim_invitation,
    company_access,
    change_membership_role,
    create_invitation,
    begin_invitation_resend,
    finish_invitation_resend,
    deactivate_membership,
    list_members,
    list_pending_invitations,
    get_company,
    hash_token,
    normalize_email,
)
from password_policy import POLICY_DESCRIPTION, password_problems

ACCESS_TOKEN_PURPOSE = "identity_access"
RESET_TOKEN_TTL = timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)


class AuthenticationFailed(IdentityServiceError):
    """Unknown account, wrong password, inactive, not activated or invalid session: indistinguishable."""

    def __init__(self):
        super().__init__("authentication failed")


class ResetTokenInvalid(IdentityServiceError):
    """Unknown, expired, used, revoked or for an unusable account: indistinguishable."""

    def __init__(self):
        super().__init__("the link is not valid")


class WeakPassword(InvalidInput):
    def __init__(self):
        super().__init__(POLICY_DESCRIPTION)


@dataclass(frozen=True)
class AuthSession:
    """A successful authentication. `access_token` is shown once to the caller."""

    user_id: int
    access_token: str


def _check_password(password: str) -> str:
    if not isinstance(password, str) or password_problems(password):
        raise WeakPassword()
    return password


def _new_password(session: Session, user: IdentityUser, password: str) -> None:
    """Set the password and end every existing session (one SQL expression, no read-modify-write)."""
    session.flush()  # autoflush is off; expire() below would otherwise drop pending changes to `user`
    session.execute(update(IdentityUser).where(IdentityUser.id == user.id).values(
        password_hash=hash_password(password), session_version=IdentityUser.session_version + 1))
    session.expire(user)


def _invalidate_tokens(session: Session, user_id: int, now: datetime) -> None:
    session.execute(update(IdentityAuthToken).where(
        IdentityAuthToken.user_id == user_id,
        IdentityAuthToken.consumed_at.is_(None), IdentityAuthToken.invalidated_at.is_(None),
    ).values(invalidated_at=now))


def _usable(user: IdentityUser | None) -> bool:
    return user is not None and bool(user.is_active) and user.email_verified_at is not None and bool(user.password_hash)


def _issue_access_token(user: IdentityUser) -> str:
    return create_purpose_token(
        str(user.id), ACCESS_TOKEN_PURPOSE, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        pwh=password_fingerprint(user.password_hash), sv=user.session_version,
    )


# --- login and session validation ---------------------------------------

def authenticate(session: Session, email: str, password: str) -> AuthSession:
    """Check central credentials and issue an access token.

    Costs one bcrypt verification whatever the reason for failure, and raises the
    same AuthenticationFailed for an unknown email, a wrong password, an inactive
    account and an account with no password yet.
    """
    try:
        user = session.scalar(select(IdentityUser).where(IdentityUser.email == normalize_email(email)))
    except InvalidInput:
        user = None
    password_ok = verify_login_password(
        password if isinstance(password, str) else "", user.password_hash if user is not None else None)
    if not password_ok or not _usable(user):
        raise AuthenticationFailed()
    return AuthSession(user.id, _issue_access_token(user))


def join_invitation(session: Session, raw_token: str, email: str, password: str) -> int:
    """An existing account accepts an invitation addressed to its own email. Returns the user id.

    Authenticates first (AuthenticationFailed, same single failure as login), then claims the single-use token
    for exactly that user (InvitationInvalid for every invitation-side problem, including someone else's token).
    """
    user_id = authenticate(session, email, password).user_id
    accept_invitation(session, raw_token, user_id=user_id)
    return user_id


def user_for_token(session: Session, token: str) -> IdentityUser:
    """The active, verified user an access token belongs to, or AuthenticationFailed.

    Re-reads the user every time, so deactivation, a password change or a logout
    takes effect immediately. The global-admin flag is read here, never from the token.
    """
    payload = decode_purpose_token(token, ACCESS_TOKEN_PURPOSE) if isinstance(token, str) else None
    try:
        user_id = int(payload["sub"]) if payload else None
    except ValueError:
        user_id = None
    user = session.get(IdentityUser, user_id) if user_id is not None else None
    if not _usable(user):
        raise AuthenticationFailed()
    pwh, sv = payload.get("pwh"), payload.get("sv")
    if (
        not isinstance(pwh, str) or not hmac.compare_digest(pwh, password_fingerprint(user.password_hash))
        or type(sv) is not int or sv != user.session_version
    ):
        raise AuthenticationFailed()
    return user


def logout(session: Session, user_id: int) -> None:
    """End every session of the user on every device."""
    with _Transaction(session):
        result = session.execute(update(IdentityUser).where(IdentityUser.id == user_id).values(
            session_version=IdentityUser.session_version + 1))
        if result.rowcount != 1:
            raise NotFound("the user does not exist")
        _audit(session, "logout", actor=user_id, target=user_id)


# --- initial password (invited users) -----------------------------------

def accept_invitation_with_password(
    session: Session, raw_token: str, password: str, *, now: datetime | None = None
) -> Acceptance:
    """Accept an invitation and set the invited person's first password, atomically.

    Claims the single-use invitation token, creates the membership and the user
    (email verified by the token) and stores the password hash in one transaction:
    any failure, including a token that was already used, leaves everything,
    token included, unchanged. Only for a person with no password yet; someone who
    already has one gets the same InvitationInvalid and uses identity_service.accept_invitation
    after signing in. The password policy is checked before the token is touched.
    """
    now = now or _now()
    _check_password(password)
    try:
        with _Transaction(session):
            acceptance = claim_invitation(session, raw_token, user_id=None, now=now)
            user = session.get(IdentityUser, acceptance.user_id)
            if user.password_hash is not None:
                raise InvitationInvalid()
            _new_password(session, user, password)
            _audit(session, "password_set", actor=user.id, target=user.id, via="invitation")
    except IntegrityError:
        raise InvitationInvalid() from None
    return acceptance


# --- password reset and change ------------------------------------------

def issue_password_reset(session: Session, email: str, *, now: datetime | None = None) -> str | None:
    """A one-hour reset token (raw, for the email) for an active, verified account, else None.

    The caller answers identically either way. Earlier unused reset tokens of the
    user are invalidated, so only the newest link works. An invited user who has
    not set a password yet is eligible (this is the recovery path if the first
    password was never set). Rate limiting belongs to the route that will call this.
    """
    now = now or _now()
    try:
        email = normalize_email(email)
    except InvalidInput:
        return None
    with _Transaction(session):
        user = session.scalar(select(IdentityUser).where(IdentityUser.email == email))
        if user is None or not user.is_active or user.email_verified_at is None:
            return None
        _invalidate_tokens(session, user.id, now)
        raw = secrets.token_urlsafe(32)
        session.add(IdentityAuthToken(
            token_hash=hash_token(raw), purpose="password_reset", user_id=user.id,
            created_at=now, expires_at=now + RESET_TOKEN_TTL,
        ))
        _audit(session, "reset_requested", target=user.id)
    return raw


def reset_password(session: Session, raw_token: str, new_password: str, *, now: datetime | None = None) -> int:
    """Set a new password from a valid reset token; returns the user id.

    The policy is checked first (a weak password does not burn the link). The
    token claim, the new hash, the session-version bump and the invalidation of
    the user's other tokens are one transaction. All sessions end.
    """
    now = now or _now()
    _check_password(new_password)
    if not isinstance(raw_token, str) or not raw_token:
        raise ResetTokenInvalid()
    with _Transaction(session):
        claimed = session.execute(update(IdentityAuthToken).where(
            IdentityAuthToken.token_hash == hash_token(raw_token),
            IdentityAuthToken.purpose == "password_reset",
            IdentityAuthToken.consumed_at.is_(None), IdentityAuthToken.invalidated_at.is_(None),
            IdentityAuthToken.expires_at > now,
        ).values(consumed_at=now).returning(IdentityAuthToken.user_id)).first()
        if claimed is None:
            raise ResetTokenInvalid()
        user = session.get(IdentityUser, claimed[0])
        if not user.is_active or user.email_verified_at is None:
            raise ResetTokenInvalid()
        _new_password(session, user, new_password)
        _invalidate_tokens(session, user.id, now)
        _audit(session, "password_reset", actor=user.id, target=user.id)
    return user.id


def change_password(session: Session, user_id: int, current_password: str, new_password: str) -> AuthSession:
    """Change a signed-in user's password; every other session ends, and a fresh one is returned."""
    _check_password(new_password)
    with _Transaction(session):
        user = session.get(IdentityUser, user_id)
        if not _usable(user) or not verify_password(current_password or "", user.password_hash):
            raise AuthenticationFailed()
        _new_password(session, user, new_password)
        _invalidate_tokens(session, user.id, _now())
        _audit(session, "password_changed", actor=user.id, target=user.id)
    return AuthSession(user.id, _issue_access_token(user))


# --- account status ------------------------------------------------------

def deactivate_user(session: Session, admin_user_id: int, target_user_id: int, *, now: datetime | None = None) -> None:
    """Deactivate an account (global admins only): sessions end and unused tokens are revoked.

    Memberships are left as they are; an inactive user has no access anyway
    (identity_service.company_access). Reactivation is not provided here.
    """
    now = now or _now()
    with _Transaction(session):
        admin = _active_admin(session, admin_user_id)
        if admin.id == target_user_id:
            raise Conflict("administrators cannot deactivate themselves")
        result = session.execute(update(IdentityUser).where(
            IdentityUser.id == target_user_id, IdentityUser.is_active.is_(True),
        ).values(is_active=False, deactivated_at=now, session_version=IdentityUser.session_version + 1))
        if result.rowcount != 1:
            if session.get(IdentityUser, target_user_id) is None:
                raise NotFound("the user does not exist")
            raise Conflict("the user is already inactive")
        _invalidate_tokens(session, target_user_id, now)
        _audit(session, "user_deactivated", actor=admin.id, target=target_user_id)


__all__ = [
    "AuthenticationFailed", "ResetTokenInvalid", "WeakPassword", "AuthSession",
    "authenticate", "user_for_token", "logout", "accept_invitation_with_password",
    "issue_password_reset", "reset_password", "change_password", "deactivate_user",
    "list_company_access", "create_invitation", "begin_invitation_resend", "finish_invitation_resend", "NotAuthorized", "NotFound", "Conflict", "IdentityServiceError",
    "list_members", "list_pending_invitations", "deactivate_membership", "change_membership_role",
]


# --- read models for the central routes ----------------------------------

def list_company_access(session: Session, user: IdentityUser) -> list[dict]:
    """Every active company the user may enter, with the effective role. Central database only."""
    found = []
    for slug in COMPANY_SLUGS:
        try:
            access = company_access(session, user.id, slug)
            company = get_company(session, slug)
        except IdentityServiceError:  # a company not registered centrally
            continue
        if access is not None:
            found.append({"slug": slug, "name": company.name, "role": access.role})
    return found
