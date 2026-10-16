"""Company-scoped commercial routes under /api/companies/{company_slug}.

Authentication and authorization come from the central identity database only
(central_deps.company_context_dependency); the company database is opened after
that succeeds, by the session dependency that depends on the resulting context.
These routes never use deps.get_current_user, get_db, DATABASE_URL or a local
CompanyUser row for access. Read-only so far: every role with an active
membership may read, including Visitors.
"""
from collections.abc import Callable, Iterator

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from central_deps import CompanyContext, company_context_dependency
from central_runtime import central_runtime
from company_runtime import CompanyRuntime, company_runtime
from routes import load_board_detail
from schemas import BoardDetail


def build_company_router(
    identity_db: Callable[[], Iterator[Session]], runtime: CompanyRuntime
) -> APIRouter:
    router = APIRouter(prefix="/api/companies/{company_slug}", tags=["companies"])
    get_context = company_context_dependency(identity_db)

    def get_company_db(context: CompanyContext = Depends(get_context)) -> Iterator[Session]:
        yield from runtime.session_for(context)

    @router.get("/boards/{board_id}", response_model=BoardDetail)
    def get_company_board(board_id: int, db: Session = Depends(get_company_db)) -> BoardDetail:
        """Board with all columns and cards, from the company's database."""
        return load_board_detail(db, board_id)

    return router


company_router = build_company_router(central_runtime.get_session, company_runtime)
