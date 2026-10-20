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
    company_access, is_admin_level, is_super, read_only, sync_assignable_users, sync_company_user,
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


def company_capabilities(context: CompanyContext) -> dict[str, bool]:
    """What the caller may do in the company, derived only from the predicates the write routes already enforce.

    A report for the UI, never an authority: every route still checks for itself. "canEditOwnCards" is a
    ceiling (a Sales Member edits and moves forward only cards assigned to them; unassigned cards are admin-only).
    "canManageBoards" covers board creation and column create/edit; deleting a column stays Super Admin only.
    """
    return {
        "readOnly": context.read_only,
        "canEditOwnCards": not context.read_only,
        "canEditAnyCard": context.can_manage_cards,
        "canMoveBack": context.can_manage_cards,
        "canDeleteCards": context.can_manage_cards,
        "canAssignSalesperson": context.can_manage_cards,
        "canManageBoards": context.can_manage_boards,
        "canImport": context.can_manage_imports,
    }


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


def reject_company_mismatch(company_slug: str, x_company_slug: str | None) -> None:
    """400 when the client's `X-Company-Slug` assertion is present and differs from the URL's company.

    A consistency check only: it runs before any token, membership or database is looked at, never selects a
    company and never grants anything. An absent header is accepted.
    """
    if x_company_slug is not None and x_company_slug != company_slug:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Company mismatch")


def require_matching_company_header(company_slug: str, x_company_slug: str | None = Header(None)) -> None:
    """Route dependency for company-scoped routes that do not build a CompanyContext (the /api/central/companies/
    administration routes). Declared on the route, so it runs before the route's own token and role checks."""
    reject_company_mismatch(company_slug, x_company_slug)


def company_context_dependency(
    identity_db: Callable[[], Iterator[Session]],
) -> Callable[..., CompanyContext]:
    """A FastAPI dependency that resolves a CompanyContext.

    `identity_db` is the identity session dependency (it owns opening and closing the
    session). A route using the result must declare a `company_slug` path parameter
    (e.g. `/api/companies/{company_slug}/...`): the slug is explicit, never inferred
    from the user, a header or a token.

    An optional `X-Company-Slug` header is a consistency assertion by the client, nothing more: when present
    it must equal the path slug, or the request is refused (400) before any token, membership or database is
    looked at. It never selects a company and never grants anything; an absent header changes nothing.
    """

    def get_company_context(
        company_slug: str,
        authorization: str | None = Header(None),
        x_company_slug: str | None = Header(None),
        session: Session = Depends(identity_db),
    ) -> CompanyContext:
        reject_company_mismatch(company_slug, x_company_slug)
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
    "reject_company_mismatch", "require_matching_company_header",
    "CompanyContext", "CompanyAccessDenied", "company_capabilities", "resolve_company_context", "can_modify_opportunity",
    "bearer_token", "company_context_dependency", "assignable_user_ids", "sync_assignable_users", "sync_company_user", "IdentityServiceError",
    "ROLE_SUPER_ADMIN", "ROLE_SALES_MEMBER", "ROLE_VISITOR",
    # re-exported so the company routes never import identity_service directly (test_identity_service guard)
    "can_manage_cards", "can_manage_imports", "company_access", "read_only",
]
