"""Central identity and company-membership services (not wired to routes or login).

Pure functions over SQLAlchemy sessions that the caller supplies; nothing here
opens, creates or migrates a database, and importing it touches none. Canonical
users, invitations, memberships and tokens live only in the central identity
database (identity_models). A company database only ever receives the minimal
CompanyUser reference, and only through sync_company_user.

Transaction boundaries. Each public function that writes is one transaction on
the identity session it is given: it commits on success and rolls back on any
error (the session is left clean). Acceptance consumes the token, closes the
invitation and creates the membership in that one transaction, so either all
happen or none. The first statement of acceptance is the conditional UPDATE
that claims the token, so two simultaneous acceptances serialize on SQLite's
write lock and only one can claim it.

Cross-database consistency. The identity and company databases are separate
files; there is no atomic transaction across them and none is claimed.
Central membership is authoritative. sync_company_user first validates the
central membership, then writes the company-local reference in the company
session's own transaction. If that fails, LocalSyncError is raised and the
central membership stays valid; calling sync_company_user again is the retry
(it is idempotent). Accepting an invitation never syncs implicitly, and one
call syncs exactly one company. No function here ever deletes a local
reference, so history survives deactivation or loss of membership.

Authorization model. is_global_admin is the only global role; it is checked in
the central database on every call, never taken from the caller. Company roles
are `sales_member` and `visitor`, from active memberships. A global admin is
treated as having access to every active company (an assumption recorded here;
the legacy admin could see every board). A CompanyUser row never grants access.

Invitation tokens are 256-bit random values returned once to the caller (for the
email, a later step); only their SHA-256 hex is stored. One invitation covers one
company and one role (the identity schema's token points at one invitation), so
inviting someone to several companies yields one invitation and one token per
company. Errors carry fixed messages: no token, hash, password, path or SQL.
"""
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from database_targets import COMPANY_SLUGS
from identity_models import (
    COMPANY_ROLES,
    IdentityAuditEvent,
    IdentityAuthToken,
    IdentityCompany,
    IdentityInvitation,
    IdentityMembership,
    IdentityUser,
)
from models import CompanyUser

INVITATION_TTL = timedelta(days=7)
ADMIN_ROLE = "admin"  # what company_access reports for a global admin; never a membership role


class IdentityServiceError(Exception):
    """Base class; messages are fixed and safe to show."""


class InvalidInput(IdentityServiceError):
    pass


class NotAuthorized(IdentityServiceError):
    pass


class NotFound(IdentityServiceError):
    pass


class Conflict(IdentityServiceError):
    pass


class InvitationInvalid(IdentityServiceError):
    """Unknown, expired, revoked, used or mismatched: deliberately indistinguishable to the caller."""

    def __init__(self):
        super().__init__("the invitation is not valid")


class LocalSyncError(IdentityServiceError):
    """The company-local reference could not be written; the central membership is unaffected. Retry."""


@dataclass(frozen=True)
class CompanyAccess:
    user_id: int
    company_slug: str
    role: str  # "admin" | "sales_member" | "visitor"


@dataclass(frozen=True)
class IssuedInvitation:
    invitation_id: int
    company_slug: str
    role: str
    expires_at: datetime
    token: str  # raw; shown once, never stored


@dataclass(frozen=True)
class Acceptance:
    user_id: int
    company_slug: str
    role: str
    local_sync_required: bool = True  # call sync_company_user for this company next


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, like the schema's DateTime columns


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    local, _, domain = value.partition("@")
    if not local or "." not in domain or "@" in domain or len(value) > 320 or any(c.isspace() for c in value):
        raise InvalidInput("the email address is not valid")
    return value


def _check_role(role: str) -> str:
    if role not in COMPANY_ROLES:  # "admin" is global, never a company role
        raise InvalidInput("the company role is not valid")
    return role


class _Transaction:
    """Commit on success, roll back on any error (the session is left usable)."""

    def __init__(self, session: Session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            try:
                self.session.commit()
            except BaseException:
                self.session.rollback()
                raise
        else:
            self.session.rollback()
        return False


def _audit(session: Session, action: str, *, actor=None, target=None, company_id=None, **details) -> None:
    session.add(IdentityAuditEvent(
        actor_user_id=actor, target_user_id=target, company_id=company_id, action=action,
        details=json.dumps(details, sort_keys=True), created_at=_now(),
    ))


# --- users and companies -------------------------------------------------

def get_user(session: Session, user_id: int) -> IdentityUser | None:
    return session.get(IdentityUser, user_id)


def get_user_by_email(session: Session, email: str) -> IdentityUser | None:
    return session.scalar(select(IdentityUser).where(IdentityUser.email == normalize_email(email)))


def create_user(session: Session, email: str) -> IdentityUser:
    """A non-admin user with no password and no verified email. Global admins are never created here."""
    email = normalize_email(email)
    with _Transaction(session):
        if session.scalar(select(IdentityUser.id).where(IdentityUser.email == email)) is not None:
            raise Conflict("a user with this email already exists")
        user = IdentityUser(email=email)
        session.add(user)
        session.flush()
        _audit(session, "user_created", target=user.id)
    return user


def get_company(session: Session, slug: str) -> IdentityCompany:
    """The central company row for a supported slug (unsupported slugs never reach the query)."""
    if slug not in COMPANY_SLUGS:
        raise InvalidInput("unknown company")
    company = session.scalar(select(IdentityCompany).where(IdentityCompany.slug == slug))
    if company is None:
        raise NotFound("the company is not registered")
    return company


def _active_admin(session: Session, user_id: int) -> IdentityUser:
    user = session.get(IdentityUser, user_id)
    if user is None or not user.is_active or not user.is_global_admin:
        raise NotAuthorized("administrator access is required")
    return user


# --- memberships ---------------------------------------------------------

def company_access(session: Session, user_id: int, slug: str) -> CompanyAccess | None:
    """The user's effective access to a company, from the central database only, or None."""
    company = get_company(session, slug)
    user = session.get(IdentityUser, user_id)
    if user is None or not user.is_active or not company.is_active:
        return None
    if user.is_global_admin:
        return CompanyAccess(user.id, slug, ADMIN_ROLE)
    membership = session.scalar(select(IdentityMembership).where(
        IdentityMembership.user_id == user_id, IdentityMembership.company_id == company.id,
        IdentityMembership.is_active.is_(True),
    ))
    return CompanyAccess(user.id, slug, membership.role) if membership else None


def require_company_role(session: Session, user_id: int, slug: str, allowed: tuple[str, ...]) -> CompanyAccess:
    """Access to `slug` with one of the `allowed` roles (ADMIN_ROLE counts when listed), or NotAuthorized."""
    access = company_access(session, user_id, slug)
    if access is None or access.role not in allowed:
        raise NotAuthorized("the user has no permitted role in this company")
    return access


# --- invitations ---------------------------------------------------------

def create_invitation(
    session: Session, admin_user_id: int, email: str, company_slug: str, role: str, *, now: datetime | None = None
) -> IssuedInvitation:
    """Invite `email` to one company with one company role. Global admins only.

    An open invitation for the same email and company that has expired is closed
    (revoked); one that is still valid is a Conflict. Refused if the email already
    belongs to a user with an active membership there.
    """
    email, role = normalize_email(email), _check_role(role)
    now = now or _now()
    with _Transaction(session):
        admin = _active_admin(session, admin_user_id)
        company = get_company(session, company_slug)
        if not company.is_active:
            raise Conflict("the company is not active")
        existing_user = session.scalar(select(IdentityUser).where(IdentityUser.email == email))
        if existing_user is not None:
            if existing_user.is_global_admin:
                raise Conflict("a global administrator needs no company invitation")
            if session.scalar(select(IdentityMembership.id).where(
                IdentityMembership.user_id == existing_user.id, IdentityMembership.company_id == company.id,
                IdentityMembership.is_active.is_(True),
            )) is not None:
                raise Conflict("the user already belongs to this company")
        open_invitation = session.scalar(select(IdentityInvitation).where(
            IdentityInvitation.email == email, IdentityInvitation.company_id == company.id,
            IdentityInvitation.accepted_at.is_(None), IdentityInvitation.revoked_at.is_(None),
        ))
        if open_invitation is not None:
            if open_invitation.expires_at > now:
                raise Conflict("an invitation for this email and company is already open")
            _close_invitation(session, open_invitation, now)
        expires_at = now + INVITATION_TTL
        invitation = IdentityInvitation(
            email=email, company_id=company.id, role=role, invited_by_user_id=admin.id,
            created_at=now, expires_at=expires_at,
        )
        session.add(invitation)
        session.flush()
        raw = secrets.token_urlsafe(32)
        session.add(IdentityAuthToken(
            token_hash=hash_token(raw), purpose="invitation", invitation_id=invitation.id,
            created_at=now, expires_at=expires_at,
        ))
        _audit(session, "invitation_created", actor=admin.id, company_id=company.id, role=role,
               invitation_id=invitation.id)
    return IssuedInvitation(invitation.id, company_slug, role, expires_at, raw)


def create_invitations(
    session: Session, admin_user_id: int, email: str, grants: list[tuple[str, str]], *, now: datetime | None = None
) -> list[IssuedInvitation]:
    """One invitation (and token) per (company_slug, role), all or none."""
    if not grants or len({slug for slug, _ in grants}) != len(grants):
        raise InvalidInput("list each company once")
    now = now or _now()
    issued: list[IssuedInvitation] = []
    # Inputs are validated up front. create_invitation commits per grant, so a
    # failure part-way withdraws (revokes) the grants already issued.
    for slug, role in grants:
        _check_role(role)
        if slug not in COMPANY_SLUGS:
            raise InvalidInput("unknown company")
    try:
        for slug, role in grants:
            issued.append(create_invitation(session, admin_user_id, email, slug, role, now=now))
    except IdentityServiceError:
        for item in issued:  # earlier grants were committed; withdraw them so the call is all-or-none
            _revoke(session, item.invitation_id, now)
        raise
    return issued


def _close_invitation(session: Session, invitation: IdentityInvitation, now: datetime) -> None:
    invitation.revoked_at = now
    session.execute(update(IdentityAuthToken).where(
        IdentityAuthToken.invitation_id == invitation.id,
        IdentityAuthToken.consumed_at.is_(None), IdentityAuthToken.invalidated_at.is_(None),
    ).values(invalidated_at=now))


def _revoke(session: Session, invitation_id: int, now: datetime) -> bool:
    with _Transaction(session):
        result = session.execute(update(IdentityInvitation).where(
            IdentityInvitation.id == invitation_id,
            IdentityInvitation.accepted_at.is_(None), IdentityInvitation.revoked_at.is_(None),
        ).values(revoked_at=now))
        if result.rowcount != 1:
            return False
        session.execute(update(IdentityAuthToken).where(
            IdentityAuthToken.invitation_id == invitation_id,
            IdentityAuthToken.consumed_at.is_(None), IdentityAuthToken.invalidated_at.is_(None),
        ).values(invalidated_at=now))
        return True


def revoke_invitation(session: Session, admin_user_id: int, invitation_id: int, *, now: datetime | None = None) -> None:
    """Close an open invitation and its tokens. Global admins only; an accepted or closed one is a Conflict."""
    now = now or _now()
    with _Transaction(session):
        admin = _active_admin(session, admin_user_id)
    if not _revoke(session, invitation_id, now):
        if session.get(IdentityInvitation, invitation_id) is None:
            raise NotFound("the invitation does not exist")
        raise Conflict("the invitation is no longer open")
    with _Transaction(session):
        _audit(session, "invitation_revoked", actor=admin.id, invitation_id=invitation_id)


def revoke_expired_invitations(session: Session, *, now: datetime | None = None) -> int:
    """Close every open invitation past its expiry (housekeeping; acceptance rejects them regardless)."""
    now = now or _now()
    with _Transaction(session):
        ids = session.scalars(select(IdentityInvitation.id).where(
            IdentityInvitation.accepted_at.is_(None), IdentityInvitation.revoked_at.is_(None),
            IdentityInvitation.expires_at <= now,
        )).all()
        for invitation_id in ids:
            invitation = session.get(IdentityInvitation, invitation_id)
            _close_invitation(session, invitation, now)
        return len(ids)


def accept_invitation(
    session: Session, raw_token: str, *, user_id: int | None = None, now: datetime | None = None
) -> Acceptance:
    """Atomically accept an invitation: claim its token, close it, create the membership.

    The token is single-use and expires; presenting it proves control of the
    invited email, which is then marked verified. With `user_id` the acceptor must
    be the user whose email was invited; without it the user for that email is
    found or created (no password). The inviter must still be an active global
    admin and the company active. Any failure leaves everything unchanged,
    including the token. Every invalid case raises the same InvitationInvalid.
    """
    now = now or _now()
    try:
        with _Transaction(session):
            return claim_invitation(session, raw_token, user_id=user_id, now=now)
    except IntegrityError:  # e.g. a concurrent duplicate membership: same answer, nothing changed
        raise InvitationInvalid() from None


def claim_invitation(session: Session, raw_token: str, *, user_id: int | None, now: datetime) -> Acceptance:
    """accept_invitation's body, inside a transaction the caller owns (no commit, no rollback).

    For callers that must change more in the same transaction (central_auth sets
    the initial password). The caller must map IntegrityError to InvitationInvalid.
    """
    if not isinstance(raw_token, str) or not raw_token:
        raise InvitationInvalid()
    # First statement: the conditional claim, which takes SQLite's write lock.
    claimed = session.execute(update(IdentityAuthToken).where(
        IdentityAuthToken.token_hash == hash_token(raw_token),
        IdentityAuthToken.purpose == "invitation",
        IdentityAuthToken.consumed_at.is_(None), IdentityAuthToken.invalidated_at.is_(None),
        IdentityAuthToken.expires_at > now,
    ).values(consumed_at=now).returning(IdentityAuthToken.invitation_id)).first()
    if claimed is None:
        raise InvitationInvalid()
    invitation = session.get(IdentityInvitation, claimed[0])
    company = session.get(IdentityCompany, invitation.company_id)
    inviter = session.get(IdentityUser, invitation.invited_by_user_id)
    if (
        invitation.accepted_at is not None or invitation.revoked_at is not None
        or invitation.expires_at <= now or not company.is_active
        or not inviter.is_active or not inviter.is_global_admin
    ):
        raise InvitationInvalid()
    if user_id is not None:
        user = session.get(IdentityUser, user_id)
        if user is None or user.email != invitation.email:
            raise InvitationInvalid()
    else:
        user = session.scalar(select(IdentityUser).where(IdentityUser.email == invitation.email))
        if user is None:
            user = IdentityUser(email=invitation.email)
            session.add(user)
            session.flush()
    if not user.is_active or user.is_global_admin:
        raise InvitationInvalid()
    membership = session.scalar(select(IdentityMembership).where(
        IdentityMembership.user_id == user.id, IdentityMembership.company_id == company.id))
    if membership is not None and membership.is_active:
        raise InvitationInvalid()  # a second membership is never created; the invitation stays unaccepted
    closed = session.execute(update(IdentityInvitation).where(
        IdentityInvitation.id == invitation.id,
        IdentityInvitation.accepted_at.is_(None), IdentityInvitation.revoked_at.is_(None),
    ).values(accepted_at=now, accepted_user_id=user.id))
    if closed.rowcount != 1:
        raise InvitationInvalid()
    if membership is None:
        session.add(IdentityMembership(user_id=user.id, company_id=company.id, role=invitation.role, created_at=now))
    else:  # a lapsed membership is reactivated with the invited role
        membership.is_active = True
        membership.role = invitation.role
    if user.email_verified_at is None:
        user.email_verified_at = now
    _audit(session, "invitation_accepted", actor=user.id, target=user.id, company_id=company.id,
           role=invitation.role, invitation_id=invitation.id)
    return Acceptance(user.id, company.slug, invitation.role)


# --- company-local reference --------------------------------------------

def sync_company_user(identity_session: Session, company_session: Session, user_id: int, slug: str) -> CompanyUser:
    """Create or refresh the user's company-local reference after validating central access.

    Not atomic with the central database (they are separate files). The central
    membership is checked first and stays authoritative; a failure here raises
    LocalSyncError and changes nothing central. Idempotent: call it again to
    retry. Only id (the canonical central id) and the display name are written,
    never a credential or role. `company_session` must be bound to company
    `slug` (company_session_factory tags its sessions).
    """
    access = company_access(identity_session, user_id, slug)
    if access is None:
        raise NotAuthorized("the user has no access to this company")
    if company_session.info.get("company_slug") != slug:
        raise InvalidInput("the company session does not belong to this company")
    email = identity_session.get(IdentityUser, user_id).email
    try:
        local = company_session.get(CompanyUser, user_id)
        if local is None:
            local = CompanyUser(id=user_id, username=email)
            company_session.add(local)
        elif local.username != email:
            local.username = email
        company_session.commit()
    except SQLAlchemyError:
        company_session.rollback()
        raise LocalSyncError("the company reference could not be written; retry") from None
    return local
