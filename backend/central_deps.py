"""Company-scoped request authorization against the central identity database.

Not wired to any route, to deps.py or to the login endpoint: the legacy flow is
untouched. Importing this opens no database and creates no engine; the caller
supplies the identity session dependency (identity_database.identity_db_dependency
for a factory it built, or a test fixture). There is no fallback to DATABASE_URL
or to any commercial database, and no company database is opened here.

Everything is re-read from the central database on each request:
central_auth.user_for_token validates the signed "identity_access" token (legacy
tokens are rejected by their purpose claim) and loads the active, verified user;
identity_service.company_access then decides the effective role for one company.
The global-admin flag and the company role are never taken from token claims,
and a company-local CompanyUser row is never consulted.

Failures are fixed-message HTTP errors: 401 for any authentication problem, 403
for every authorization problem (unknown company, inactive company, inactive
user, no active membership), so a caller cannot probe which slugs exist.
"""
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from central_auth import AuthenticationFailed, user_for_token
from identity_service import (
    SUPER_ADMIN_ROLE, IdentityServiceError, assignable_user_ids, can_grant_admin, can_manage_boards, can_manage_cards, can_manage_imports, can_manage_members, can_manage_products, can_review,
    company_access, is_admin_level, is_super, read_only, sync_company_user,
)

ROLE_SUPER_ADMIN = SUPER_ADMIN_ROLE
ROLE_SALES_MEMBER = "sales_member"
ROLE_VISITOR = "visitor"


@dataclass(frozen=True)
class CompanyContext:
    """Who is calling and what they may do in one company. Holds no secrets."""

    user_id: int  # canonical identity_users.id
    is_global_admin: bool
    company_slug: str
    role: str  # "super_admin" | "sales_member" | "visitor"
    read_only: bool

    @property
    def is_super(self) -> bool:
        return is_super(self.role)

    @property
    def is_admin_level(self) -> bool:
        return is_admin_level(self.role)

    @property
    def can_review(self) -> bool:
        return can_review(self.role)

    @property
    def can_manage_members(self) -> bool:
        return can_manage_members(self.role)

    @property
    def can_grant_admin(self) -> bool:
        return can_grant_admin(self.role)

    @property
    def can_manage_imports(self) -> bool:
        return can_manage_imports(self.role)

    @property
    def can_manage_cards(self) -> bool:
        return can_manage_cards(self.role)

    @property
    def can_manage_boards(self) -> bool:
        return can_manage_boards(self.role)

    @property
    def can_manage_products(self) -> bool:
        return can_manage_products(self.role)

    @property
    def id(self) -> int:
        """The actor id card_rules and stage_history read (`user.id`): the canonical central id."""
        return self.user_id


class CompanyAccessDenied(IdentityServiceError):
    """Unknown or inactive company, inactive user or no active membership: indistinguishable."""

    def __init__(self):
        super().__init__("access to this company is not permitted")


def resolve_company_context(session: Session, token: str, company_slug: str) -> CompanyContext:
    """The caller's context for `company_slug`, or AuthenticationFailed / CompanyAccessDenied.

    Reads the identity database only.
    """
    user = user_for_token(session, token)
    try:
        access = company_access(session, user.id, company_slug)
    except IdentityServiceError:  # unsupported slug, or one with no central company row
        access = None
    if access is None:
        raise CompanyAccessDenied()
    return CompanyContext(
        user_id=access.user_id,
        is_global_admin=is_super(access.role),
        company_slug=access.company_slug,
        role=access.role,
        read_only=read_only(access.role),
    )


def can_modify_opportunity(context: CompanyContext, assigned_user_id: int | None) -> bool:
    """Coarse write gate only: the business rules (stage transitions, assignment) stay in card_rules.

    Administrators may modify any opportunity, a Sales Member only one assigned to them
    (company membership alone grants nothing over others' opportunities, and
    unassigned ones are admin-only), a Visitor none.
    """
    if context.is_admin_level:
        return True
    return context.role == ROLE_SALES_MEMBER and assigned_user_id is not None and assigned_user_id == context.user_id


def bearer_token(authorization: str | None) -> str:
    """The token from an `Authorization: Bearer <token>` header, or AuthenticationFailed."""
    parts = authorization.split() if authorization else []
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthenticationFailed()
    return parts[1]


def company_context_dependency(
    identity_db: Callable[[], Iterator[Session]],
) -> Callable[..., CompanyContext]:
    """A FastAPI dependency that resolves a CompanyContext.

    `identity_db` is the identity session dependency (it owns opening and closing the
    session). A route using the result must declare a `company_slug` path parameter
    (e.g. `/api/companies/{company_slug}/...`): the slug is explicit, never inferred
    from the user, a header or a token.
    """

    def get_company_context(
        company_slug: str,
        authorization: str | None = Header(None),
        session: Session = Depends(identity_db),
    ) -> CompanyContext:
        try:
            return resolve_company_context(session, bearer_token(authorization), company_slug)
        except AuthenticationFailed:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from None
        except CompanyAccessDenied:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied") from None

    return get_company_context


__all__ = [
    "CompanyContext", "CompanyAccessDenied", "resolve_company_context", "can_modify_opportunity",
    "bearer_token", "company_context_dependency", "assignable_user_ids", "sync_company_user", "IdentityServiceError",
    "ROLE_SUPER_ADMIN", "ROLE_SALES_MEMBER", "ROLE_VISITOR",
    # re-exported so the company routes never import identity_service directly (test_identity_service guard)
    "can_manage_cards", "can_manage_imports", "company_access", "read_only",
]
