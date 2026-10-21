"""Company-scoped commercial routes under /api/companies/{company_slug}.

Authentication and authorization come from the central identity database only
(central_deps.company_context_dependency); the company database is opened after
that succeeds, by the session dependency that depends on the resulting context.
These routes never use deps.get_current_user, get_db, DATABASE_URL or a local
CompanyUser row for access. Reads (board detail, report, the five KPI views, lead-options, card history): every role with an active
membership may read, including Visitors. Writes (create, edit, move, delete a card): Visitors are refused and delete is
admin-only, both before the company database is opened; card_rules decides ownership.

Undo (single level, per person and board, same rules as the legacy undo.py) and the admin-only Bid import with its
revert run here too. Both reuse the legacy engines over the company session: the actor is the canonical central id,
Undo steps carry `expect` (a conflict answers 409), a Visitor is refused before the company database is opened, and
import previews are bound to the company they were made in.
"""
import hmac
import logging
from collections.abc import Callable, Iterator
from datetime import date
from typing import List, Optional

import rate_limit
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from card_rules import (
    CardPermissionDenied,
    InvalidCardUpdate,
    apply_card_updates,
    audit_deletion,
    audit_new_card_owner,
    move_denial,
    new_card_deal_fields,
    new_card_fields,
)
from central_deps import (
    CompanyContext,
    IdentityServiceError,
    assignable_user_ids,
    can_manage_cards,
    can_manage_imports,
    company_access,
    company_capabilities,
    company_context_dependency,
    read_only,
    sync_assignable_users,
    sync_company_user,
)
from bid_import_revert import is_lock_error
from central_runtime import central_runtime
from company_database import begin_write
from company_runtime import CompanyRuntime, company_runtime
from database import seed_stages
from models import Board, Card, Column, CompanyUser, UndoEntry
from lead_options import ConfidenceFilter
from routes import (
    board_ai_chat_payload,
    _check_stage_kind,
    board_report_payload,
    card_history_payload,
    kpi_aging_payload,
    kpi_conversion_payload,
    kpi_pipeline_payload,
    kpi_stage_time_payload,
    kpi_summary_payload,
    lead_options_payload,
    load_board_detail,
)
from product_catalog import ProductError, card_count, catalog_names, create_product, delete_product, has_catalog, list_products, rename_product
from cards_excel_import import ImportRefused, apply_plan, build_plan, issue_dict, plan_body
from commercial_excel_reader import read_commercial_excel
from excel_cards_writer import XLSX_MEDIA_TYPE, build_workbook, export_rows
from excel_import_ledger import has_ledger, ledger_compatible
from identity_models import IdentityUser
from import_routes import (
    REVERT_RETRY_AFTER_SECONDS,
    _file_too_large,
    _read_form,
    confirm_import_impl,
    get_import_impl,
    list_imports_impl,
    preview_import_impl,
    revert_import_impl,
    revert_preview_impl,
)
from stage_history import SOURCE_API, set_stage_context
from undo import UndoError, apply_undo, get_undo, record_undo, undo_create, undo_delete, undo_move, undo_update
from schemas import (
    AIChatRequest,
    BoardCreate,
    BoardDetail,
    BoardResponse,
    DefaultBoardResponse,
    ColumnCreate,
    ColumnResponse,
    ColumnUpdate,
    CardCreate,
    CardMove,
    CardResponse,
    CardUpdate,
    BoardReport,
    CardEditHistoryEntry,
    KpiAgingResponse,
    KpiConversionResponse,
    KpiPipelineResponse,
    KpiStageTimeResponse,
    KpiSummaryResponse,
    ImportBatchDetail,
    ImportBatchList,
    ImportResult,
    LeadOptionsResponse,
    ProductBody,
    ProductCatalogResponse,
    ProductResponse,
    RevertPlanResponse,
    RevertRequest,
    RevertResult,
    UndoStatus,
)

logger = logging.getLogger(__name__)

DEFAULT_BOARD_TITLE = "Sales Pipeline"
DEFAULT_BOARD_DESCRIPTION = "Company sales pipeline"


def build_company_router(
    identity_db: Callable[[], Iterator[Session]], runtime: CompanyRuntime
) -> APIRouter:
    router = APIRouter(prefix="/api/companies/{company_slug}", tags=["companies"])
    get_context = company_context_dependency(identity_db)

    def get_company_db(context: CompanyContext = Depends(get_context)) -> Iterator[Session]:
        yield from runtime.session_for(context)

    def get_writer_context(context: CompanyContext = Depends(get_context)) -> CompanyContext:
        """The caller's context, only if the role may write. Runs before any company database is opened."""
        if context.read_only:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return context

    def get_admin_context(context: CompanyContext = Depends(get_writer_context)) -> CompanyContext:
        if not context.is_admin_level:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return context

    def get_product_admin_context(context: CompanyContext = Depends(get_writer_context)) -> CompanyContext:
        """Product catalog writes: Super Admin or this company's Admin. Runs before any company database opens."""
        if not context.can_manage_products:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return context

    def get_board_admin_context(context: CompanyContext = Depends(get_writer_context)) -> CompanyContext:
        """Board creation and column create/edit: Super Admin or this company's Admin, before any company DB opens."""
        if not context.can_manage_boards:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return context

    def get_card_admin_context(context: CompanyContext = Depends(get_writer_context)) -> CompanyContext:
        """Card deletion: Super Admin or this company's Admin, before any company database opens."""
        if not context.can_manage_cards:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return context

    def get_import_admin_context(context: CompanyContext = Depends(get_writer_context)) -> CompanyContext:
        """Bid import, history and revert: Super Admin or this company's Admin, before any company DB opens."""
        if not context.can_manage_imports:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return context

    def get_excel_context(context: CompanyContext = Depends(get_writer_context)) -> CompanyContext:
        """Excel export and template: Super Admin or this company's Admin (the company comes from the token's context
        and the path, never from the request body), before any company database opens."""
        if not context.can_manage_imports:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return context

    def fresh_role(identity: Session, context: CompanyContext) -> str:
        """The caller's role in this company read again from the identity database now (not the request-start
        snapshot): 403 when the user, the membership or the company is no longer active, or is read-only."""
        identity.expire_all()  # drop cached rows so the read below reaches the database
        try:
            access = company_access(identity, context.user_id, context.company_slug)
        except IdentityServiceError:
            access = None
        if access is None or read_only(access.role):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return access.role

    def sync_local_references(sync) -> None:
        """Run a company-local reference sync; contention answers 503 database_busy, any other failure a fixed 503."""
        try:
            sync()
        except IdentityServiceError as exc:
            if is_lock_error(exc.__cause__):  # temporary contention: same answer as main.database_error_handler
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "database_busy", "message": "The database is busy. Try again shortly."},
                    headers={"Retry-After": REVERT_RETRY_AFTER_SECONDS},
                ) from None
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Company data is unavailable"
            ) from None

    def writer_session(identity: Session, context: CompanyContext) -> Iterator[Session]:
        """A company session for a writer, with the actor's local reference row ensured and stage events attributed.

        The row is created only here (a write the central membership already authorized), never on a GET.
        """
        for db in runtime.session_for(context):
            sync_local_references(lambda: sync_company_user(identity, db, context.user_id, context.company_slug))
            set_stage_context(db, context, SOURCE_API)
            yield db

    def get_writer_db(
        identity: Session = Depends(identity_db), context: CompanyContext = Depends(get_writer_context)
    ) -> Iterator[Session]:
        yield from writer_session(identity, context)

    def get_admin_db(
        identity: Session = Depends(identity_db), context: CompanyContext = Depends(get_admin_context)
    ) -> Iterator[Session]:
        yield from writer_session(identity, context)

    def get_product_admin_db(
        identity: Session = Depends(identity_db), context: CompanyContext = Depends(get_product_admin_context)
    ) -> Iterator[Session]:
        yield from writer_session(identity, context)

    def get_board_admin_db(
        identity: Session = Depends(identity_db), context: CompanyContext = Depends(get_board_admin_context)
    ) -> Iterator[Session]:
        yield from writer_session(identity, context)

    def get_card_admin_db(
        identity: Session = Depends(identity_db), context: CompanyContext = Depends(get_card_admin_context)
    ) -> Iterator[Session]:
        yield from writer_session(identity, context)

    def get_import_admin_db(
        identity: Session = Depends(identity_db), context: CompanyContext = Depends(get_import_admin_context)
    ) -> Iterator[Session]:
        yield from writer_session(identity, context)

    def find_board(db: Session, board_id: int) -> Board:
        board = db.query(Board).filter(Board.id == board_id).first()
        if board is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Board not found")
        return board

    def find_card(db: Session, board_id: int, card_id: int) -> Card:
        find_board(db, board_id)
        card = db.query(Card).join(Column).filter(Card.id == card_id, Column.board_id == board_id).first()
        if card is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found")
        return card

    @router.post("/boards/{board_id}/cards", response_model=CardResponse, status_code=201)
    def create_company_card(
        board_id: int,
        card: CardCreate,
        context: CompanyContext = Depends(get_writer_context),
        db: Session = Depends(get_writer_db),
    ):
        """Create a card. A Sales Member's card is assigned to them; admins may assign later."""
        begin_write(db)  # the target column must still exist when the card is inserted (a column delete may race)
        find_board(db, board_id)
        column = db.query(Column).filter(Column.id == card.column_id, Column.board_id == board_id).first()
        if column is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Column not found")
        db_card = Card(
            column_id=card.column_id, title=card.title, details=card.details, position=card.position,
            **new_card_fields(context, new_card_deal_fields(card)),
        )
        db.add(db_card)
        db.flush()
        audit_new_card_owner(db, board_id, db_card, context)
        record_undo(db, context, board_id, f'Remove new card "{db_card.title}"', [undo_create(db_card)])
        db.commit()
        db.refresh(db_card)
        return db_card

    @router.put("/boards/{board_id}/cards/{card_id}", response_model=CardResponse)
    def update_company_card(
        board_id: int,
        card_id: int,
        card_update: CardUpdate,
        identity: Session = Depends(identity_db),
        context: CompanyContext = Depends(get_writer_context),
        db: Session = Depends(get_writer_db),
    ):
        card = find_card(db, board_id, card_id)
        updates = card_update.model_dump(exclude_unset=True)
        new_owner = updates.get("salesperson_id")
        if context.can_manage_cards and new_owner is not None and new_owner != card.salesperson_id:
            # Only a person the central database lets this company assign to (the local row is checked by card_rules).
            if new_owner not in assignable_user_ids(identity, context.company_slug):
                raise HTTPException(status_code=422, detail="Salesperson not found")
        undo_step = undo_update(card, updates)
        undo_description = f'Undo edit to "{card.title}"'
        try:
            apply_card_updates(db, card, updates, context, admin=context.can_manage_cards)
        except CardPermissionDenied as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
        except InvalidCardUpdate as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        record_undo(db, context, board_id, undo_description, [undo_step])
        db.commit()
        db.refresh(card)
        return card

    @router.put("/boards/{board_id}/cards/{card_id}/position", response_model=CardResponse)
    def move_company_card(
        board_id: int,
        card_id: int,
        move: CardMove,
        context: CompanyContext = Depends(get_writer_context),
        db: Session = Depends(get_writer_db),
    ):
        begin_write(db)  # origin, target and both columns' positions are read under the write lock
        find_board(db, board_id)
        target = db.query(Column).filter(Column.id == move.column_id, Column.board_id == board_id).first()
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target column not found")
        card = find_card(db, board_id, card_id)
        denial = move_denial(context, card, target.position, admin=context.can_manage_cards)
        if denial:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=denial)
        if (card.column_id, card.position) != (move.column_id, move.position):
            record_undo(db, context, board_id, f'Move "{card.title}" back to {card.column.title}',
                        [undo_move(card, move.column_id)])
        source_id = card.column_id
        card.column_id = move.column_id
        # `position` is the index the card takes among the target column's cards: the column is renumbered
        # 0..n-1 so the order is exact (no ties). Only positions change here, never card content.
        others = sorted(
            (c for c in db.query(Card).filter(Card.column_id == move.column_id, Card.id != card.id).all()),
            key=lambda c: (c.position, c.id),
        )
        others.insert(min(move.position, len(others)), card)
        for index, member in enumerate(others):
            if member.position != index:
                member.position = index
        if source_id != move.column_id:  # close the gap the card left behind, keeping the others' order
            rest = sorted(db.query(Card).filter(Card.column_id == source_id, Card.id != card.id).all(),
                          key=lambda c: (c.position, c.id))
            for index, member in enumerate(rest):
                if member.position != index:
                    member.position = index
        db.commit()
        db.refresh(card)
        return card

    @router.delete("/boards/{board_id}/cards/{card_id}", status_code=204)
    def delete_company_card(
        board_id: int,
        card_id: int,
        context: CompanyContext = Depends(get_card_admin_context),
        db: Session = Depends(get_card_admin_db),
    ):
        """Delete a card. Super Admin or the company's Admin (checked before the company database is opened)."""
        card = find_card(db, board_id, card_id)
        record_undo(db, context, board_id, f'Restore deleted card "{card.title}"', [undo_delete(card)])
        audit_deletion(db, board_id, card, context)
        db.delete(card)
        db.commit()

    def limit_company_ai(context: CompanyContext = Depends(get_writer_context)) -> None:
        """AI quota per person and company; runs after authorization, so refused callers never spend it."""
        key = f"{context.company_slug}:{context.user_id}"
        rate_limit.check("ai_user", key)
        rate_limit.check("ai_user_hour", key)
        rate_limit.record("ai_user", key)
        rate_limit.record("ai_user_hour", key)

    @router.post("/boards/{board_id}/ai")
    async def company_board_ai_chat(
        board_id: int,
        request: AIChatRequest,
        identity: Session = Depends(identity_db),
        context: CompanyContext = Depends(get_writer_context),
        _: None = Depends(limit_company_ai),
        db: Session = Depends(get_writer_db),
    ):
        """AI board assistant. Writers only; assignments limited to people the central database allows; recorded for Undo like the REST edits."""
        return await board_ai_chat_payload(
            db, board_id, request, context,
            assignable_ids=set(assignable_user_ids(identity, context.company_slug)), record_undo_entry=True,
            admin=context.can_manage_cards,
            reauthorize=lambda: can_manage_cards(fresh_role(identity, context)),
        )

    @router.get("/boards", response_model=List[BoardResponse])
    def list_company_boards(db: Session = Depends(get_company_db)):
        """Every board of the company (readable by every role)."""
        return db.query(Board).order_by(Board.id).all()

    @router.post("/boards", response_model=BoardResponse, status_code=201)
    def create_company_board(
        board: BoardCreate,
        context: CompanyContext = Depends(get_board_admin_context),
        db: Session = Depends(get_board_admin_db),
    ):
        db_board = Board(user_id=context.user_id, title=board.title, description=board.description)
        db.add(db_board)
        db.commit()
        db.refresh(db_board)
        return db_board

    @router.post("/boards/default", response_model=DefaultBoardResponse)
    def ensure_default_company_board(
        response: Response,
        context: CompanyContext = Depends(get_board_admin_context),
        db: Session = Depends(get_board_admin_db),
    ):
        """The company's first board, created with the six standard stages (no cards) only when it has none.

        201 + created=true when this request created it, 200 + created=false when a board already existed (returned
        unchanged). The check, the board and its stages are one transaction under the write lock, so concurrent calls
        create at most one board and an error leaves nothing behind. Boards cannot be deleted through the API.
        """
        begin_write(db)  # another request's board must be visible to the check below, and block this insert
        try:
            board = db.query(Board).order_by(Board.id).first()
            created = board is None
            if created:
                board = Board(user_id=context.user_id, title=DEFAULT_BOARD_TITLE, description=DEFAULT_BOARD_DESCRIPTION)
                db.add(board)
                db.flush()
                seed_stages(db, board)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        db.refresh(board)
        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return DefaultBoardResponse(**BoardResponse.model_validate(board).model_dump(), created=created)

    @router.post("/boards/{board_id}/columns", response_model=ColumnResponse, status_code=201)
    def create_company_column(
        board_id: int,
        column: ColumnCreate,
        context: CompanyContext = Depends(get_board_admin_context),
        db: Session = Depends(get_board_admin_db),
    ):
        begin_write(db)  # the stage-kind check and the renumbering must see the committed state
        find_board(db, board_id)
        _check_stage_kind(db, board_id, column.stage_kind)
        db_column = Column(board_id=board_id, title=column.title, position=column.position, stage_kind=column.stage_kind)
        db.add(db_column)
        db.flush()  # the new id is the tie-break when legacy data already has duplicate positions
        place_column(db, board_id, db_column, column.position)
        db.commit()  # one commit: the insert and the renumbering are applied together or not at all
        db.refresh(db_column)
        return db_column

    def place_column(db: Session, board_id: int, column: Column, index: int) -> None:
        """Put `column` at 0-based `index` among the board's columns and renumber the board 0..n-1 (unique, no gaps).
        An index past the end means last. Only `position` changes; the caller commits."""
        others = sorted(
            db.query(Column).filter(Column.board_id == board_id, Column.id != column.id).all(),
            key=lambda c: (c.position, c.id),
        )
        others.insert(min(index, len(others)), column)
        for number, member in enumerate(others):
            if member.position != number:
                member.position = number

    def find_column(db: Session, board_id: int, column_id: int) -> Column:
        find_board(db, board_id)
        column = db.query(Column).filter(Column.id == column_id, Column.board_id == board_id).first()
        if column is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Column not found")
        return column

    @router.put("/boards/{board_id}/columns/{column_id}", response_model=ColumnResponse)
    def update_company_column(
        board_id: int,
        column_id: int,
        column_update: ColumnUpdate,
        context: CompanyContext = Depends(get_board_admin_context),
        db: Session = Depends(get_board_admin_db),
    ):
        begin_write(db)
        column = find_column(db, board_id, column_id)
        if column_update.title is not None:
            column.title = column_update.title
        if column_update.stage_kind is not None:
            _check_stage_kind(db, board_id, column_update.stage_kind, column)
            column.stage_kind = column_update.stage_kind
        if column_update.position is not None:
            place_column(db, board_id, column, column_update.position)
        db.commit()
        db.refresh(column)
        return column

    @router.delete("/boards/{board_id}/columns/{column_id}", status_code=204)
    def delete_company_column(
        board_id: int,
        column_id: int,
        context: CompanyContext = Depends(get_admin_context),
        db: Session = Depends(get_admin_db),
    ):
        """Delete an empty column. Administrators only; a column that still holds cards is refused (409)."""
        begin_write(db)  # the 'column is empty' check and the delete (which cascades to cards) are one unit
        column = find_column(db, board_id, column_id)
        if db.query(Card.id).filter(Card.column_id == column.id).first() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Column cannot be deleted while it contains cards. Move the cards to another column first.",
            )
        db.delete(column)
        db.commit()

    @router.get("/boards/{board_id}", response_model=BoardDetail)
    def get_company_board(board_id: int, db: Session = Depends(get_company_db)) -> BoardDetail:
        """Board with all columns and cards, from the company's database."""
        return load_board_detail(db, board_id)

    def excel_response(content: bytes, filename: str) -> Response:
        # The filename is built by the server from validated values only, never from client input.
        return Response(
            content=content,
            media_type=XLSX_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
        )

    @router.get("/boards/{board_id}/excel/export")
    def export_company_cards(
        board_id: int,
        context: CompanyContext = Depends(get_excel_context),
        db: Session = Depends(get_company_db),
        identity: Session = Depends(identity_db),
    ):
        """The board's cards as the commercial Excel workbook. Super Admin or this company's Admin (the file carries salesperson emails):
        the role check runs before the company database opens; the company comes from the token's context."""
        find_board(db, board_id)
        ids = {i for (i,) in db.query(Card.salesperson_id).join(Column).filter(
            Column.board_id == board_id, Card.salesperson_id.isnot(None)).distinct()}
        emails = {}
        if ids:
            emails = {u.id: u.email for u in identity.query(IdentityUser).filter(IdentityUser.id.in_(ids)).all()}
        filename = f"cards-{context.company_slug}-board-{board_id}-{date.today():%Y%m%d}.xlsx"
        return excel_response(build_workbook(export_rows(db, board_id, emails)), filename)

    @router.get("/boards/{board_id}/excel/template")
    def download_cards_template(board_id: int, context: CompanyContext = Depends(get_excel_context)):
        """The empty import template (same workbook, zero cards). Needs no company data, so no database is opened."""
        return excel_response(build_workbook([]), "cards-import-template.xlsx")

    @router.get("/boards/{board_id}/report", response_model=BoardReport)
    def get_company_board_report(
        board_id: int,
        salesperson_id: Optional[int] = None,
        product_line: Optional[str] = None,
        confidence_level: Optional[ConfidenceFilter] = None,
        db: Session = Depends(get_company_db),
    ):
        return board_report_payload(db, board_id, salesperson_id, product_line, confidence_level)

    @router.get("/boards/{board_id}/cards/{card_id}/history", response_model=List[CardEditHistoryEntry])
    def get_company_card_history(board_id: int, card_id: int, db: Session = Depends(get_company_db)):
        """Member-edit history of a card, newest first; never writes or creates local user rows."""
        return card_history_payload(db, board_id, card_id)

    @router.get("/boards/{board_id}/kpis/summary", response_model=KpiSummaryResponse)
    def get_company_kpi_summary(
        board_id: int,
        period: str = "last_30_days",
        start: Optional[date] = None,
        end: Optional[date] = None,
        compare: bool = False,
        db: Session = Depends(get_company_db),
    ):
        return kpi_summary_payload(db, board_id, period, start, end, compare)

    @router.get("/boards/{board_id}/kpis/conversion", response_model=KpiConversionResponse)
    def get_company_kpi_conversion(
        board_id: int,
        period: str = "last_30_days",
        start: Optional[date] = None,
        end: Optional[date] = None,
        db: Session = Depends(get_company_db),
    ):
        return kpi_conversion_payload(db, board_id, period, start, end)

    @router.get("/boards/{board_id}/kpis/stage-time", response_model=KpiStageTimeResponse)
    def get_company_kpi_stage_time(
        board_id: int,
        period: str = "last_30_days",
        start: Optional[date] = None,
        end: Optional[date] = None,
        db: Session = Depends(get_company_db),
    ):
        return kpi_stage_time_payload(db, board_id, period, start, end)

    @router.get("/boards/{board_id}/kpis/aging", response_model=KpiAgingResponse)
    def get_company_kpi_aging(board_id: int, db: Session = Depends(get_company_db)):
        return kpi_aging_payload(db, board_id)

    @router.get("/boards/{board_id}/kpis/pipeline", response_model=KpiPipelineResponse)
    def get_company_kpi_pipeline(board_id: int, db: Session = Depends(get_company_db)):
        return kpi_pipeline_payload(db, board_id)

    # --- Undo -------------------------------------------------------------------------------------

    @router.get("/boards/{board_id}/undo", response_model=UndoStatus)
    def get_company_undo_status(
        board_id: int,
        context: CompanyContext = Depends(get_writer_context),
        db: Session = Depends(get_writer_db),
    ):
        """The caller's undoable last change on this board. Writers only (a Visitor never has one)."""
        entry = get_undo(db, context, board_id)
        return UndoStatus(available=entry is not None, description=entry.description if entry else None)

    @router.post("/boards/{board_id}/undo", response_model=UndoStatus)
    def undo_company_change(
        board_id: int,
        context: CompanyContext = Depends(get_writer_context),
        db: Session = Depends(get_writer_db),
    ):
        """Reverse the caller's last change on this board in one transaction (same rules as the legacy undo)."""
        begin_write(db)  # the entry, the cards it expects and the positions it restores are read under the lock
        try:
            description = apply_undo(db, context, board_id, admin=context.can_manage_cards)
            db.commit()
        except (UndoError, ValueError) as exc:
            # Nothing half-applied survives; the entry is used up either way (as in the legacy route).
            db.rollback()
            db.query(UndoEntry).filter(UndoEntry.user_id == context.user_id).delete()
            db.commit()
            code = status.HTTP_404_NOT_FOUND if str(exc) == "Nothing to undo" else status.HTTP_409_CONFLICT
            raise HTTPException(status_code=code, detail=str(exc))
        return UndoStatus(available=False, description=None, undone=description)

    # --- Bid import and revert: Super Admin and the company's Admin, checked before the company database opens ------

    def limit_company_imports(context: CompanyContext = Depends(get_import_admin_context)) -> None:
        rate_limit.hit("import_user", f"{context.company_slug}:{context.user_id}")

    @router.post("/boards/{board_id}/imports/preview")
    async def preview_company_import(
        board_id: int,
        request: Request,
        context: CompanyContext = Depends(get_import_admin_context),
        _: None = Depends(limit_company_imports),
        db: Session = Depends(get_import_admin_db),
    ):
        return await preview_import_impl(db, board_id, request, context.user_id, scope=context.company_slug)

    @router.post("/boards/{board_id}/imports", response_model=ImportResult, status_code=201)
    async def confirm_company_import(
        board_id: int,
        request: Request,
        context: CompanyContext = Depends(get_import_admin_context),
        _: None = Depends(limit_company_imports),
        db: Session = Depends(get_import_admin_db),
        identity: Session = Depends(identity_db),
    ):
        def recheck():
            if not can_manage_imports(fresh_role(identity, context)):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

        return await confirm_import_impl(db, board_id, request, context, scope=context.company_slug, recheck=recheck)

    @router.get("/boards/{board_id}/imports", response_model=ImportBatchList)
    def list_company_imports(
        board_id: int,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        context: CompanyContext = Depends(get_import_admin_context),
        db: Session = Depends(get_import_admin_db),
    ):
        return list_imports_impl(db, board_id, limit, offset)

    @router.get("/imports/{batch_id}", response_model=ImportBatchDetail)
    def get_company_import(
        batch_id: int,
        context: CompanyContext = Depends(get_import_admin_context),
        db: Session = Depends(get_import_admin_db),
    ):
        return get_import_impl(db, batch_id)

    @router.get("/imports/{batch_id}/revert-preview", response_model=RevertPlanResponse)
    def get_company_revert_preview(
        batch_id: int,
        context: CompanyContext = Depends(get_import_admin_context),
        _: None = Depends(limit_company_imports),
        db: Session = Depends(get_import_admin_db),
    ):
        return revert_preview_impl(db, batch_id)

    @router.post("/imports/{batch_id}/revert", response_model=RevertResult)
    def revert_company_import(
        batch_id: int,
        body: RevertRequest,
        context: CompanyContext = Depends(get_import_admin_context),
        _: None = Depends(limit_company_imports),
        db: Session = Depends(get_import_admin_db),
        identity: Session = Depends(identity_db),
    ):
        def recheck():
            if not can_manage_imports(fresh_role(identity, context)):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

        return revert_import_impl(db, batch_id, context, body.plan_fingerprint, recheck=recheck)

    # --- Cards workbook import (not the Bid import): same roles as the Bid import, checked before the company DB opens ---

    def refusal_http(exc: ImportRefused) -> HTTPException:
        return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message, **exc.extra})

    def busy_http() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "import_busy", "message": "The database is busy; nothing was changed. Try again shortly.",
                    "applied": False},
            headers={"Retry-After": REVERT_RETRY_AFTER_SECONDS},
        )

    def require_import_ledger(db: Session) -> None:
        """Without its ledger a repeated request could not be made safe, so the import is off (see excel_import_ledger)."""
        if not has_ledger(db):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "import_not_initialized", "message": "The Excel import is not set up for this company yet",
                        "applied": False},  # raised before anything is read or written
            )
        if not ledger_compatible(db):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "import_ledger_incompatible",
                        "message": "The Excel import ledger of this company has an unexpected structure; the import is off until an operator reviews it",
                        "applied": False},  # nothing was read from or written to the ledger
            )

    def assignable_by_email(identity: Session, slug: str, db: Session) -> dict[str, int]:
        """Lower-cased e-mail -> canonical user id of everyone the central database lets this company assign to.

        Their company-local reference rows are ensured first (idempotent, central eligibility only), so an invited
        salesperson who never wrote in the company is not rejected by the all-or-nothing import. Runs before the
        import's write lock is taken; the rows are references, not import data."""
        sync_local_references(lambda: sync_assignable_users(identity, db, slug))
        ids = assignable_user_ids(identity, slug)
        if not ids:
            return {}
        return {u.email.lower(): u.id for u in identity.query(IdentityUser).filter(IdentityUser.id.in_(ids)).all()}

    async def read_cards_workbook(request: Request):
        data, filename, _, fields = await _read_form(request)
        read = read_commercial_excel(data, filename)
        if not read.ok:
            if any(issue.code == "file_too_large" for issue in read.file_errors):
                raise _file_too_large()
            raise HTTPException(status_code=422, detail={
                "code": "file_rejected", "message": "The file can't be imported",
                "file_errors": [issue_dict(issue) for issue in read.file_errors],
            })
        return read, fields

    @router.post("/boards/{board_id}/excel/import/preview")
    async def preview_cards_import(
        board_id: int,
        request: Request,
        context: CompanyContext = Depends(get_import_admin_context),
        _: None = Depends(limit_company_imports),
        db: Session = Depends(get_import_admin_db),
        identity: Session = Depends(identity_db),
    ):
        """Validate the Cards workbook against this board and report what a confirmation would create and update.
        Writes nothing."""
        find_board(db, board_id)
        require_import_ledger(db)
        read, _fields = await read_cards_workbook(request)
        plan = build_plan(db, board_id, context.company_slug, read, assignable_by_email(identity, context.company_slug, db))
        return plan_body(plan, read)

    @router.post("/boards/{board_id}/excel/import/confirm", status_code=201)
    async def confirm_cards_import(
        board_id: int,
        request: Request,
        context: CompanyContext = Depends(get_import_admin_context),
        _: None = Depends(limit_company_imports),
        db: Session = Depends(get_import_admin_db),
        identity: Session = Depends(identity_db),
    ):
        """Apply the workbook (fields: file, confirm=true, plan_digest from the preview) all-or-nothing. The server
        validates everything again under the write lock; a repeated or concurrent confirmation of a workbook of new
        cards, or of any workbook applied before on this board, is refused with 409 already_imported and changes nothing."""
        find_board(db, board_id)
        require_import_ledger(db)
        read, fields = await read_cards_workbook(request)
        if fields.get("confirm") != "true":
            raise HTTPException(status_code=422, detail={"code": "confirm_required", "message": "Set confirm=true to apply the import"})

        def recheck():
            if not can_manage_imports(fresh_role(identity, context)):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

        recheck()
        people = assignable_by_email(identity, context.company_slug, db)  # syncs references (commits): before the write lock
        try:
            begin_write(db)  # everything below is decided on the state this request will write to
            plan = build_plan(db, board_id, context.company_slug, read, people)
            refusal = plan.refusal()
            if refusal:
                raise refusal
            if not hmac.compare_digest(plan.digest, fields.get("plan_digest") or ""):
                raise ImportRefused(409, "preview_stale", "The workbook or the board changed since the preview; build the preview again")
            result = apply_plan(db, board_id, plan, context, filename=read.file.filename,
                                file_sha256=read.file.sha256, recheck=recheck)
            db.commit()
        except ImportRefused as exc:
            db.rollback()
            raise refusal_http(exc) from None
        except CardPermissionDenied:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied") from None
        except InvalidCardUpdate:
            db.rollback()
            raise HTTPException(status_code=422, detail={"code": "invalid_card_data", "message": "A row could not be applied; nothing was imported"}) from None
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            if isinstance(exc, OperationalError) and is_lock_error(exc):
                raise busy_http() from None
            logger.error("Cards import failed (%s)", type(exc).__name__)
            raise HTTPException(status_code=500, detail={
                "code": "import_failed", "message": "The import failed and nothing was changed", "applied": False}) from None
        return {**result, "applied": True}

    @router.get("/me")
    def get_company_me(context: CompanyContext = Depends(get_context)):
        """Who the caller is in this company and what they may do. Central database only: no company database is
        opened and no personal data is returned. `user_id` is identity_users.id, the value of CompanyUser.id and
        of Card.salesperson_id for cards assigned to the caller. Everything is resolved from the token and the
        slug in the path; a user with no active access to the company gets the same 403 as any other route."""
        return {"user_id": context.user_id, "company_slug": context.company_slug, "role": context.role,
                "capabilities": company_capabilities(context)}

    @router.get("/lead-options", response_model=LeadOptionsResponse)
    def get_company_lead_options(
        identity: Session = Depends(identity_db),
        context: CompanyContext = Depends(get_context),
        db: Session = Depends(get_company_db),
    ):
        """Lead field values and the assignable salespeople of one company.

        Eligibility comes from the central database (assignable_user_ids); the id is the canonical
        identity_users.id, which is CompanyUser.id. The label is the local CompanyUser.username. An
        eligible user with no local row is omitted: assigning needs that row, and this GET never creates it.
        """
        ids = assignable_user_ids(identity, context.company_slug)
        labels = dict(db.query(CompanyUser.id, CompanyUser.username).filter(CompanyUser.id.in_(ids)).all()) if ids else {}
        people = sorted(((name, user_id) for user_id, name in labels.items()), key=lambda p: (p[0], p[1]))
        return lead_options_payload(
            [{"id": user_id, "username": name} for name, user_id in people], catalog_names(db)
        )

    # --- Product catalog: any member reads, Super Admin and the company's Admin write (checked before the company database opens) ---

    def product_error(exc: ProductError) -> HTTPException:
        return HTTPException(status_code=exc.status, detail=str(exc))

    def catalog_response(db: Session) -> ProductCatalogResponse:
        return ProductCatalogResponse(initialized=has_catalog(db), products=list_products(db))

    @router.get("/products", response_model=ProductCatalogResponse)
    def get_company_products(db: Session = Depends(get_company_db)):
        """This company's catalog; its built-in defaults (initialized false) until the catalog is initialized."""
        return catalog_response(db)

    @router.post("/products", response_model=ProductResponse, status_code=201)
    def create_company_product(
        body: ProductBody,
        context: CompanyContext = Depends(get_product_admin_context),
        db: Session = Depends(get_product_admin_db),
    ):
        try:
            product = create_product(db, body.name)
            db.commit()
        except ProductError as exc:
            db.rollback()
            raise product_error(exc)
        return ProductResponse(id=product.id, name=product.name, card_count=card_count(db, product.name))

    @router.put("/products/{product_id}", response_model=ProductResponse)
    def update_company_product(
        product_id: int,
        body: ProductBody,
        context: CompanyContext = Depends(get_product_admin_context),
        db: Session = Depends(get_product_admin_db),
    ):
        try:
            product = rename_product(db, product_id, body.name)
            db.commit()
        except ProductError as exc:
            db.rollback()
            raise product_error(exc)
        return ProductResponse(id=product.id, name=product.name, card_count=card_count(db, product.name))

    @router.delete("/products/{product_id}", status_code=204)
    def delete_company_product(
        product_id: int,
        context: CompanyContext = Depends(get_product_admin_context),
        db: Session = Depends(get_product_admin_db),
    ):
        try:
            delete_product(db, product_id)
            db.commit()
        except ProductError as exc:
            db.rollback()
            raise product_error(exc)

    return router


company_router = build_company_router(central_runtime.get_session, company_runtime)
