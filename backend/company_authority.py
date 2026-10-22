"""Commit-time binding and re-validation of a company write (mitigation of the cross-database authorization race, F8).

Identity data (users, memberships, roles) and company data live in separate SQLite databases, so no transaction covers
both. A request is authorized from the identity database at its start, but under contention it can wait seconds for
the company write lock, and the person's membership can change meanwhile. For every writer session the runtime binds
the authorized company and a check here; just before the company transaction commits (so under the company write lock,
after every change was made) the listener

- verifies that the session is still bound to the company the request was authorized for (the label AND the engine's
  registered company), and
- reads the caller's membership again from the identity database: the user, the membership and the company must still
  be active and the role must be the one the request was authorized with.

Anything else raises AuthorityRevoked (403) and the commit does not happen: the session is rolled back by its owner.
Not eliminated: a change made in the identity database after this read and before the SQLite commit completes is
not seen, because the two databases still have no common transaction.
"""
from fastapi import HTTPException, status
from sqlalchemy import event
from sqlalchemy.orm import Session

from company_database import COMPANY_SLUG_INFO_KEY, session_company_slug

AUTHORITY_KEY = "company_authority"


class AuthorityRevoked(HTTPException):
    """The caller's authority for this company no longer holds (or the session is not the authorized company's)."""

    def __init__(self):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


def bind_authority(session: Session, company_slug: str, still_valid) -> None:
    """Bind `session` to the company the request was authorized for. `still_valid()` must raise HTTPException (or return
    False) when the caller's authority has gone; it is called, under the write lock, at every commit of the session."""
    session.info[AUTHORITY_KEY] = (company_slug, still_valid)


@event.listens_for(Session, "before_commit")
def _revalidate_authority(session: Session) -> None:
    bound = session.info.get(AUTHORITY_KEY)
    if bound is None:
        return
    company_slug, still_valid = bound
    if session.info.get(COMPANY_SLUG_INFO_KEY) != company_slug or session_company_slug(session) != company_slug:
        raise AuthorityRevoked()
    try:
        if still_valid() is False:
            raise AuthorityRevoked()
    except HTTPException:
        raise AuthorityRevoked() from None
