"""Central identity/access schema, kept apart from the commercial databases.

This module has its own declarative base and imports nothing from models.py or
database.py. Nothing here points into a commercial database: commercial data
will later refer to a person by `identity_users.id` as a plain integer, never
through a foreign key.

Lifecycle: users, companies and memberships are deactivated, never deleted
(foreign keys are RESTRICT, and ids use AUTOINCREMENT so a number is never
reused even if a row is removed by hand). Company slugs are meant to be stable
once created; the schema keeps them unique and well-formed, the service layer
(a later phase) must not change them.

Tokens: only the SHA-256 hex digest is stored. A token is valid while
`consumed_at`, `invalidated_at` are NULL and `expires_at` is in the future;
consuming it is one conditional UPDATE (a later phase).

Validation split: the schema only guards what SQLite can express cheaply (case,
length ceilings, 0/1 booleans, timestamp order). Canonical email normalization
(Unicode, whitespace, domain handling) and full address validation belong to
the future service layer; the lower-case and 320-character checks here are a
backstop, not a validator.
"""
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import declarative_base

IdentityBase = declarative_base()

# The ordinary company roles.
COMPANY_ROLES = ("sales_member", "visitor")
# Roles the v2 schema stores in memberships and invitations. "admin" is the company-scoped Admin: the Super Admin
# can set it on a membership (identity_service.ASSIGNABLE_ROLES) or invite with it (INVITABLE_ROLES). "super_admin" is
# never stored here: it is the is_global_admin flag of the user.
SCHEMA_ROLES = (*COMPANY_ROLES, "admin")
TOKEN_PURPOSES = ("invitation", "password_reset")

_AUTOINCREMENT = {"sqlite_autoincrement": True}

# Reserved (RFC 2606 `.invalid`, never deliverable) domain of the placeholder that replaces a cancelled identity's email.
# No invitation or account may use it, so a placeholder can never collide with a live identity.
CANCELLED_EMAIL_DOMAIN = "cancelled.invalid"


def is_cancelled_email(email: str | None) -> bool:
    """Whether `email` is in the reserved cancellation domain (any case, trailing dot or subdomain included)."""
    domain = (email or "").strip().lower().rpartition("@")[2].rstrip(".")
    return domain == CANCELLED_EMAIL_DOMAIN or domain.endswith("." + CANCELLED_EMAIL_DOMAIN)


def _utcnow() -> datetime:
    """Naive UTC, matching the rest of the application's DateTime columns."""
    return datetime.now(UTC).replace(tzinfo=None)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class IdentityUser(IdentityBase):
    """A person who can sign in. `is_global_admin` is the only global role;
    company roles live in `identity_memberships`."""

    __tablename__ = "identity_users"
    __table_args__ = (
        CheckConstraint(
            "email = lower(email) AND length(email) > 0 AND length(email) <= 320", name="ck_identity_users_email_lower"
        ),
        CheckConstraint("session_version >= 0", name="ck_identity_users_session_version"),
        CheckConstraint("is_global_admin IN (0, 1)", name="ck_identity_users_is_global_admin_bool"),
        CheckConstraint("is_active IN (0, 1)", name="ck_identity_users_is_active_bool"),
        _AUTOINCREMENT,
    )

    id = Column(Integer, primary_key=True)
    email = Column(String(320), nullable=False, unique=True)
    # NULL = no usable password yet (invited, not activated). The password
    # fingerprint embedded in access tokens is derived from this hash.
    password_hash = Column(String, nullable=True)
    is_global_admin = Column(Boolean, nullable=False, server_default=text("0"))
    is_active = Column(Boolean, nullable=False, server_default=text("1"))
    session_version = Column(Integer, nullable=False, server_default=text("0"))
    email_verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    deactivated_at = Column(DateTime, nullable=True)


class IdentityCompany(IdentityBase):
    """One of the commercial databases' owners (salcomp, lity, valor-log)."""

    __tablename__ = "identity_companies"
    __table_args__ = (
        CheckConstraint(
            "length(slug) > 0 AND length(slug) <= 64 AND slug = lower(slug) AND slug NOT GLOB '*[^a-z0-9-]*' "
            "AND slug NOT GLOB '-*' AND slug NOT GLOB '*-'",
            name="ck_identity_companies_slug",
        ),
        CheckConstraint("length(name) > 0", name="ck_identity_companies_name"),
        CheckConstraint("is_active IN (0, 1)", name="ck_identity_companies_is_active_bool"),
        _AUTOINCREMENT,
    )

    id = Column(Integer, primary_key=True)
    slug = Column(String(64), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=text("1"))
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class IdentityMembership(IdentityBase):
    """A user's role in one company (at most one membership per pair)."""

    __tablename__ = "identity_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "company_id", name="uq_identity_memberships_user_company"),
        CheckConstraint(_in("role", SCHEMA_ROLES), name="ck_identity_memberships_role"),
        CheckConstraint("is_active IN (0, 1)", name="ck_identity_memberships_is_active_bool"),
        Index("ix_identity_memberships_company_id", "company_id"),
        _AUTOINCREMENT,
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False)
    company_id = Column(Integer, ForeignKey("identity_companies.id", ondelete="RESTRICT"), nullable=False)
    role = Column(String(20), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=text("1"))
    created_at = Column(DateTime, nullable=False, default=_utcnow)


class IdentityInvitation(IdentityBase):
    """An admin's invitation of an email address to one company with one role.

    The emailed link is an `identity_auth_tokens` row (purpose 'invitation')
    pointing here. An invitation is open while accepted_at and revoked_at are
    both NULL; only one open invitation may exist per (email, company).
    """

    __tablename__ = "identity_invitations"
    __table_args__ = (
        CheckConstraint(
            "email = lower(email) AND length(email) > 0 AND length(email) <= 320",
            name="ck_identity_invitations_email_lower",
        ),
        CheckConstraint(_in("role", SCHEMA_ROLES), name="ck_identity_invitations_role"),
        CheckConstraint("expires_at > created_at", name="ck_identity_invitations_expiry"),
        CheckConstraint(
            "NOT (accepted_at IS NOT NULL AND revoked_at IS NOT NULL)", name="ck_identity_invitations_one_outcome"
        ),
        CheckConstraint(
            "(accepted_at IS NULL) = (accepted_user_id IS NULL)", name="ck_identity_invitations_accepted_pair"
        ),
        Index(
            "uq_identity_invitations_open",
            "email",
            "company_id",
            unique=True,
            sqlite_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
        Index("ix_identity_invitations_company_id", "company_id"),
        _AUTOINCREMENT,
    )

    id = Column(Integer, primary_key=True)
    email = Column(String(320), nullable=False)
    company_id = Column(Integer, ForeignKey("identity_companies.id", ondelete="RESTRICT"), nullable=False)
    role = Column(String(20), nullable=False)
    invited_by_user_id = Column(Integer, ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)
    accepted_user_id = Column(Integer, ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=True)
    revoked_at = Column(DateTime, nullable=True)


class IdentityAuthToken(IdentityBase):
    """A single-use emailed token. Only the SHA-256 hex of the token is stored.

    'invitation' tokens belong to an invitation, 'password_reset' tokens to a
    user; the other reference must be NULL. There is no signup purpose: the
    target architecture has no public signup.

    Timestamps: a token is closed by being consumed (used) or invalidated
    (superseded or revoked), never both, so `consumed_at` and `invalidated_at`
    are mutually exclusive. Neither may precede `created_at`, and `consumed_at`
    may not be later than `expires_at`. These checks only keep the rows
    coherent; they do not make a token single-use. That needs the atomic
    conditional UPDATE in the future token service.
    """

    __tablename__ = "identity_auth_tokens"
    __table_args__ = (
        CheckConstraint(
            "length(token_hash) = 64 AND token_hash NOT GLOB '*[^0-9a-f]*'", name="ck_identity_auth_tokens_hash"
        ),
        CheckConstraint(_in("purpose", TOKEN_PURPOSES), name="ck_identity_auth_tokens_purpose"),
        CheckConstraint(
            "(purpose = 'invitation' AND invitation_id IS NOT NULL AND user_id IS NULL) "
            "OR (purpose = 'password_reset' AND user_id IS NOT NULL AND invitation_id IS NULL)",
            name="ck_identity_auth_tokens_subject",
        ),
        CheckConstraint("expires_at > created_at", name="ck_identity_auth_tokens_expiry"),
        CheckConstraint("consumed_at IS NULL OR consumed_at >= created_at", name="ck_identity_auth_tokens_consumed"),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at <= expires_at", name="ck_identity_auth_tokens_consumed_before_expiry"
        ),
        CheckConstraint(
            "invalidated_at IS NULL OR invalidated_at >= created_at", name="ck_identity_auth_tokens_invalidated"
        ),
        CheckConstraint(
            "NOT (consumed_at IS NOT NULL AND invalidated_at IS NOT NULL)",
            name="ck_identity_auth_tokens_one_outcome",
        ),
        Index("ix_identity_auth_tokens_user_id", "user_id"),
        Index("ix_identity_auth_tokens_invitation_id", "invitation_id"),
        _AUTOINCREMENT,
    )

    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    purpose = Column(String(20), nullable=False)
    user_id = Column(Integer, ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=True)
    invitation_id = Column(Integer, ForeignKey("identity_invitations.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)
    invalidated_at = Column(DateTime, nullable=True)


class IdentityAuditEvent(IdentityBase):
    """Identity/security audit trail.

    No foreign keys: a row must outlive any user or company it mentions.
    `details` is JSON and never holds a token, password or link.
    """

    __tablename__ = "identity_audit_events"
    __table_args__ = (
        CheckConstraint("length(action) > 0", name="ck_identity_audit_events_action"),
        Index("ix_identity_audit_events_created_at", "created_at"),
        Index("ix_identity_audit_events_actor_user_id", "actor_user_id"),
        Index("ix_identity_audit_events_target_user_id", "target_user_id"),
        _AUTOINCREMENT,
    )

    id = Column(Integer, primary_key=True)
    actor_user_id = Column(Integer, nullable=True)
    target_user_id = Column(Integer, nullable=True)
    company_id = Column(Integer, nullable=True)
    action = Column(String(40), nullable=False)
    details = Column(Text, nullable=False, server_default=text("'{}'"))
    created_at = Column(DateTime, nullable=False, default=_utcnow)
