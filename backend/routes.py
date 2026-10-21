"""API routes for boards, columns, and cards."""
import json
import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from auth import APP_BASE_URL, SIGNUP_TOKEN_EXPIRE_HOURS, UNUSABLE_PASSWORD_HASH
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
from database import get_db
from deps import get_current_user, require_admin
from lead_options import CURRENCIES, ConfidenceFilter, LEAD_SOURCES, PRODUCT_LINES, STAGE_OPEN
from models import Board, Column, Card, CardEditHistory, User, UserRole, ConversationMessage, UndoEntry
import ai_limits
import kpis
import token_store
from mailer import send_in_background
from rate_limit import limit_ai_chat
from reports import build_board_report
from undo import (
    UndoError,
    apply_undo,
    get_undo,
    record_undo,
    undo_create,
    undo_delete,
    undo_move,
    undo_update,
)
from schemas import (
    AIChatRequest,
    BoardCreate,
    BoardUpdate,
    BoardResponse,
    BoardDetail,
    ColumnCreate,
    ColumnUpdate,
    ColumnResponse,
    CardCreate,
    CardUpdate,
    CardMove,
    CardResponse,
    CardEditHistoryEntry,
    ColumnWithCards,
    BoardReport,
    LeadOptionsResponse,
    KpiAgingResponse,
    KpiConversionResponse,
    KpiPipelineResponse,
    KpiStageTimeResponse,
    KpiSummaryResponse,
    UndoStatus,
    UserCreate,
    UserResponse,
)
from ai import test_ai_connectivity, AIError
from ai_kanban import (
    get_board_context,
    get_conversation_history,
    call_ai_with_board,
    apply_board_actions,
)

router = APIRouter(prefix="/api", tags=["boards"])


# ============================================================================
# BOARD ROUTES
# ============================================================================
# Authorization policy: reading boards/columns is open to any authenticated
# user (boards are shared), but creating, editing or deleting a board or a
# column - the pipeline's structure - is admin-only (require_admin). Card
# operations keep their own member/admin rules in card_rules.py.


@router.get("/boards", response_model=list[BoardResponse])
async def list_boards(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all boards. Boards are shared across all authenticated users."""
    boards = db.query(Board).all()
    return boards


@router.post("/boards", response_model=BoardResponse, status_code=201)
async def create_board(
    board: BoardCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new board."""
    db_board = Board(
        user_id=current_user.id,
        title=board.title,
        description=board.description,
    )
    db.add(db_board)
    db.commit()
    db.refresh(db_board)
    return db_board


def load_board_detail(db: Session, board_id: int) -> BoardDetail:
    """Board with all columns and cards (read-only; shared by the legacy and company routes)."""
    board = db.query(Board).filter(Board.id == board_id).first()

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )

    # Load columns with cards, ordered by position
    columns = db.query(Column).filter(Column.board_id == board_id).order_by(Column.position).all()

    # One query for every card on the board (not one per column), grouped
    # below. has_member_edits and salesperson_name read edit_history and
    # salesperson (and the read-only bid_details / source_ref) - eager-load them so serializing doesn't cost a query per card.
    cards_by_column = defaultdict(list)
    for card in (
        db.query(Card)
        .join(Column)
        .options(
            selectinload(Card.edit_history),
            selectinload(Card.salesperson),
            selectinload(Card.bid_details),
            selectinload(Card.source_ref),
        )
        .filter(Column.board_id == board_id)
        .order_by(Card.column_id, Card.position)
        .all()
    ):
        cards_by_column[card.column_id].append(card)

    columns_data = [
        ColumnWithCards(**{**column.__dict__, "cards": cards_by_column[column.id]})
        for column in columns
    ]

    return BoardDetail(
        **board.__dict__,
        columns=columns_data,
    )


@router.get("/boards/{board_id}", response_model=BoardDetail)
async def get_board(
    board_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get board with all columns and cards."""
    return load_board_detail(db, board_id)


@router.put("/boards/{board_id}", response_model=BoardResponse)
async def update_board(
    board_id: int,
    board_update: BoardUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update board metadata."""
    board = db.query(Board).filter(Board.id == board_id).first()

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )

    if board_update.title is not None:
        board.title = board_update.title
    if board_update.description is not None:
        board.description = board_update.description

    db.commit()
    db.refresh(board)
    return board


@router.delete("/boards/{board_id}", status_code=204)
async def delete_board(
    board_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a board and all its data."""
    board = db.query(Board).filter(Board.id == board_id).first()

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )

    db.delete(board)
    db.commit()


# ============================================================================
# COLUMN ROUTES
# ============================================================================


def _check_stage_kind(db: Session, board_id: int, kind: str, column: Optional[Column] = None) -> None:
    """Enforce the allowed stage_kind states and transitions (admin routes).

    - A board has at most one won and one lost stage, so "won"/"lost" can
      never be ambiguous for reports.
    - A column's kind only changes while it holds no cards: changing it would
      silently reclassify every opportunity in it (e.g. turn open deals into
      wins). Move the cards out first, then change the kind.
    """
    if column is not None and kind != column.stage_kind and column.cards:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Can't change the stage kind of a column that has cards; move them out first",
        )
    if kind != STAGE_OPEN:
        clash = db.query(Column).filter(Column.board_id == board_id, Column.stage_kind == kind)
        if column is not None:
            clash = clash.filter(Column.id != column.id)
        if clash.first() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'This board already has a "{kind}" stage',
            )


@router.post("/boards/{board_id}/columns", response_model=ColumnResponse, status_code=201)
async def create_column(
    board_id: int,
    column: ColumnCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a column in a board."""
    board = db.query(Board).filter(Board.id == board_id).first()

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )

    _check_stage_kind(db, board_id, column.stage_kind)
    db_column = Column(
        board_id=board_id,
        title=column.title,
        position=column.position,
        stage_kind=column.stage_kind,
    )
    db.add(db_column)
    db.commit()
    db.refresh(db_column)
    return db_column


@router.put("/boards/{board_id}/columns/{column_id}", response_model=ColumnResponse)
async def update_column(
    board_id: int,
    column_id: int,
    column_update: ColumnUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update column."""
    # Verify board exists
    board = db.query(Board).filter(Board.id == board_id).first()

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )

    column = db.query(Column).filter(
        Column.id == column_id,
        Column.board_id == board_id,
    ).first()

    if not column:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Column not found",
        )

    # Renaming is display-only: it never touches stage_kind.
    if column_update.title is not None:
        column.title = column_update.title
    if column_update.position is not None:
        column.position = column_update.position
    if column_update.stage_kind is not None:
        _check_stage_kind(db, board_id, column_update.stage_kind, column)
        column.stage_kind = column_update.stage_kind

    db.commit()
    db.refresh(column)
    return column


@router.delete("/boards/{board_id}/columns/{column_id}", status_code=204)
async def delete_column(
    board_id: int,
    column_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete column and all its cards."""
    board = db.query(Board).filter(Board.id == board_id).first()

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )

    column = db.query(Column).filter(
        Column.id == column_id,
        Column.board_id == board_id,
    ).first()

    if not column:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Column not found",
        )

    db.delete(column)
    db.commit()


# ============================================================================
# CARD ROUTES
# ============================================================================


@router.post("/boards/{board_id}/cards", response_model=CardResponse, status_code=201)
async def create_card(
    board_id: int,
    card: CardCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a card in a column."""
    # Verify board exists
    board = db.query(Board).filter(Board.id == board_id).first()

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )

    # Verify column exists in this board
    column = db.query(Column).filter(
        Column.id == card.column_id,
        Column.board_id == board_id,
    ).first()

    if not column:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Column not found",
        )

    db_card = Card(
        column_id=card.column_id,
        title=card.title,
        details=card.details,
        position=card.position,
        **new_card_fields(current_user, new_card_deal_fields(card)),
    )
    db.add(db_card)
    db.flush()
    audit_new_card_owner(db, board_id, db_card, current_user)
    record_undo(db, current_user, board_id, f'Remove new card "{db_card.title}"', [undo_create(db_card)])
    db.commit()
    db.refresh(db_card)
    return db_card


@router.put("/boards/{board_id}/cards/{card_id}", response_model=CardResponse)
async def update_card(
    board_id: int,
    card_id: int,
    card_update: CardUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update card."""
    # Verify board exists
    board = db.query(Board).filter(Board.id == board_id).first()

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )

    # Verify card exists in this board
    card = db.query(Card).join(Column).filter(
        Card.id == card_id,
        Column.board_id == board_id,
    ).first()

    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Card not found",
        )

    # Members can edit content, but their edits must be tracked - capture a
    # diff of only the fields that actually changed before mutating. Admin
    # edits are intentionally not tracked (see CLAUDE.md / CardEditHistory).
    updates = card_update.model_dump(exclude_unset=True)
    undo_step = undo_update(card, updates)
    undo_description = f'Undo edit to "{card.title}"'
    try:
        apply_card_updates(db, card, updates, current_user)
    except CardPermissionDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except InvalidCardUpdate as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    record_undo(db, current_user, board_id, undo_description, [undo_step])

    db.commit()
    db.refresh(card)
    return card


@router.put("/boards/{board_id}/cards/{card_id}/position", response_model=CardResponse)
async def move_card(
    board_id: int,
    card_id: int,
    move: CardMove,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Move card to another column."""
    # Verify board exists
    board = db.query(Board).filter(Board.id == board_id).first()

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )

    # Verify target column exists in this board
    target_column = db.query(Column).filter(
        Column.id == move.column_id,
        Column.board_id == board_id,
    ).first()

    if not target_column:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target column not found",
        )

    # Verify card exists in this board
    card = db.query(Card).join(Column).filter(
        Card.id == card_id,
        Column.board_id == board_id,
    ).first()

    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Card not found",
        )

    # Members move only their own cards, and only forward (or within a
    # column). Admins are unrestricted.
    denial = move_denial(current_user, card, target_column.position)
    if denial:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=denial)

    if (card.column_id, card.position) != (move.column_id, move.position):
        record_undo(
            db,
            current_user,
            board_id,
            f'Move "{card.title}" back to {card.column.title}',
            [undo_move(card, move.column_id)],
        )
    card.column_id = move.column_id
    card.position = move.position

    db.commit()
    db.refresh(card)
    return card


@router.delete("/boards/{board_id}/cards/{card_id}", status_code=204)
async def delete_card(
    board_id: int,
    card_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a card. Admin only."""
    # Verify board exists
    board = db.query(Board).filter(Board.id == board_id).first()

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )

    # Verify card exists in this board
    card = db.query(Card).join(Column).filter(
        Card.id == card_id,
        Column.board_id == board_id,
    ).first()

    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Card not found",
        )

    record_undo(db, current_user, board_id, f'Restore deleted card "{card.title}"', [undo_delete(card)])
    audit_deletion(db, board_id, card, current_user)
    db.delete(card)
    db.commit()


@router.get("/boards/{board_id}/undo", response_model=UndoStatus)
async def get_undo_status(
    board_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The current user's undoable last change on this board, if any."""
    entry = get_undo(db, current_user, board_id)
    return UndoStatus(available=entry is not None, description=entry.description if entry else None)


@router.post("/boards/{board_id}/undo", response_model=UndoStatus)
async def undo_last_change(
    board_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reverse the current user's last change on this board (single level).

    Returns the new status - always unavailable, since the entry is used up.
    """
    try:
        description = apply_undo(db, current_user, board_id)
        db.commit()
    except (UndoError, ValueError) as exc:
        # Drop anything half-applied, but still use the entry up: a change
        # that can't be undone now won't become undoable later.
        db.rollback()
        db.query(UndoEntry).filter(UndoEntry.user_id == current_user.id).delete()
        db.commit()
        code = status.HTTP_404_NOT_FOUND if str(exc) == "Nothing to undo" else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=str(exc))
    return UndoStatus(available=False, description=None, undone=description)


def card_history_payload(db: Session, board_id: int, card_id: int) -> List[CardEditHistoryEntry]:
    """Member-edit history of a card, newest first. Read-only.

    `changes` is returned exactly as stored. An entry whose editor has no local user row
    (deleted, or never synchronised into a company database) keeps its record and shows
    edited_by "unknown".
    """
    card = db.query(Card).join(Column).filter(
        Card.id == card_id,
        Column.board_id == board_id,
    ).first()

    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Card not found",
        )

    entries = (
        db.query(CardEditHistory)
        .options(selectinload(CardEditHistory.user))
        .filter(CardEditHistory.card_id == card.id)
        .order_by(CardEditHistory.created_at.desc(), CardEditHistory.id.desc())
        .all()
    )
    return [
        CardEditHistoryEntry(
            id=entry.id,
            edited_by=entry.user.username if entry.user else "unknown",
            changes=json.loads(entry.changes),
            created_at=entry.created_at,
        )
        for entry in entries
    ]


@router.get("/boards/{board_id}/cards/{card_id}/history", response_model=List[CardEditHistoryEntry])
async def get_card_history(
    board_id: int,
    card_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List a card's member-edit history, newest first."""
    return card_history_payload(db, board_id, card_id)


# ============================================================================
# AI ROUTES
# ============================================================================


@router.post("/ai/test")
async def test_ai(current_user: User = Depends(require_admin)):
    """Test AI connectivity with a simple question. Admin only.

    A diagnostic that makes a paid OpenRouter call, so it's gated before the
    call is made: unauthenticated requests get 401 and members 403 from the
    dependency, never reaching test_ai_connectivity.
    """
    result = await test_ai_connectivity("What is 2+2?")

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=result.get("error", "AI service error"),
        )

    return result


async def board_ai_chat_payload(
    db: Session, board_id: int, request: AIChatRequest, current_user,
    assignable_ids: Optional[set] = None, record_undo_entry: bool = True, admin: Optional[bool] = None,
    reauthorize=None, can_delete: Optional[bool] = None, reauthorize_delete=None,
):
    """The AI chat exchange for one board, shared by the legacy and the company-scoped routes."""
    # Verify board exists and belongs to user
    board = db.query(Board).filter(Board.id == board_id).first()

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )

    user_message = request.message
    if not user_message.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Message required",
        )
    if len(user_message) > ai_limits.max_message_chars():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Message too long (maximum {ai_limits.max_message_chars()} characters)",
        )

    try:
        # Get board context and conversation history
        board_state = await get_board_context(db, board_id)
        history = await get_conversation_history(db, board_id)

        # Call AI with board context
        ai_result = await call_ai_with_board(board_state, history, user_message)

        if not ai_result["success"]:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=ai_result.get("error", "AI service error"),
            )

        # The model call can take seconds: authority is read again from the identity database before anything is
        # written, and its fresh card-administration decision replaces the one taken at request start. A callable
        # that raises HTTPException refuses the request with nothing written (company routes only).
        if reauthorize is not None:
            admin = reauthorize()
        if reauthorize_delete is not None:
            can_delete = reauthorize_delete()

        # Save user message
        user_msg = ConversationMessage(
            board_id=board_id,
            user_id=current_user.id,
            role="user",
            content=user_message,
        )
        db.add(user_msg)
        db.flush()

        # Apply actions if any
        actions_result = None
        if ai_result.get("actions"):
            actions_result = await apply_board_actions(
                db, board_id, ai_result["actions"], current_user,
                assignable_ids=assignable_ids, record_undo_entry=record_undo_entry, admin=admin,
                can_delete=can_delete,
            )
            if not actions_result.get("success"):
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=actions_result.get("error", "Failed to apply actions"),
                )

        # Save assistant message
        assistant_msg = ConversationMessage(
            board_id=board_id,
            user_id=current_user.id,
            role="assistant",
            content=ai_result["response"],
        )
        db.add(assistant_msg)
        db.commit()

        return {
            "response": ai_result["response"],
            "actions": ai_result.get("actions", []),
            "actions_applied": actions_result or {},
            "confidence": ai_result.get("confidence", 0.5),
            "tokens_used": ai_result.get("tokens_used", 0),
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        # str(e) can quote SQL, parameters or paths; log only the type.
        logging.getLogger(__name__).error("AI chat failed (%s)", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The AI request failed",
        )


@router.post("/boards/{board_id}/ai")
async def board_ai_chat(
    board_id: int,
    request: AIChatRequest,
    current_user: User = Depends(get_current_user),
    _: None = Depends(limit_ai_chat),
    db: Session = Depends(get_db),
):
    """AI chat endpoint for board management."""
    return await board_ai_chat_payload(db, board_id, request, current_user)


# ============================================================================
# SALES LEADS
# ============================================================================


def lead_options_payload(salespeople: list[dict], product_lines: list[str] | None = None) -> LeadOptionsResponse:
    return LeadOptionsResponse(
        currencies=CURRENCIES,
        lead_sources=LEAD_SOURCES,
        product_lines=PRODUCT_LINES if product_lines is None else product_lines,
        salespeople=salespeople,
    )


@router.get("/lead-options", response_model=LeadOptionsResponse)
async def get_lead_options(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Allowed lead field values. Open to members, who also assign salespeople
    (unlike GET /users, which is admin-only)."""
    users = db.query(User).order_by(User.username).all()
    return lead_options_payload([{"id": u.id, "username": u.username} for u in users])


def board_report_payload(
    db: Session, board_id: int, salesperson_id: Optional[int], product_line: Optional[str],
    confidence_level: Optional[str] = None,
):
    if not db.query(Board).filter(Board.id == board_id).first():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )
    return build_board_report(db, board_id, salesperson_id, product_line, confidence_level=confidence_level)


@router.get("/boards/{board_id}/report", response_model=BoardReport)
async def get_board_report(
    board_id: int,
    salesperson_id: Optional[int] = None,
    product_line: Optional[str] = None,
    confidence_level: Optional[ConfidenceFilter] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sales report over the board's leads, optionally filtered.

    confidence_level: LOW, MEDIUM or HIGH (exact, upper case), or UNASSIGNED for cards with no level; anything else is a
    422. Omitted = no filter. Combines (AND) with the other filters.
    """
    return board_report_payload(db, board_id, salesperson_id, product_line, confidence_level)


# ============================================================================
# KPIs (Part 19) - read-only views over card_stage_events. Definitions and
# limitations: docs/KPIS.md. Same access as the existing report: any
# authenticated user. These routes never write.
# ============================================================================


def _kpi_history(db: Session, board_id: int):
    if not db.query(Board.id).filter(Board.id == board_id).first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Board not found")
    return kpis.load_board_history(db, board_id, kpis.utcnow())


def _kpi_period(history, period: str, start: Optional[date], end: Optional[date]):
    try:
        return kpis.resolve_period(period, start, end, history.as_of.date())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def kpi_summary_payload(db: Session, board_id: int, period: str, start: Optional[date], end: Optional[date], compare: bool):
    history = _kpi_history(db, board_id)
    current = _kpi_period(history, period, start, end)
    return {
        "as_of": history.as_of,
        "current": kpis.period_kpis(history, current),
        "previous": kpis.period_kpis(history, current.previous()) if compare else None,
    }


def kpi_conversion_payload(db: Session, board_id: int, period: str, start: Optional[date], end: Optional[date]):
    history = _kpi_history(db, board_id)
    current = _kpi_period(history, period, start, end)
    return {
        "as_of": history.as_of,
        "period": current.as_dict(),
        "coverage": kpis.coverage(history, current),
        "win_rate_closed": kpis.win_rate_closed(history, current),
        "cohort": kpis.cohort(history, current),
        "advancement": kpis.advancement(history, current),
    }


def kpi_stage_time_payload(db: Session, board_id: int, period: str, start: Optional[date], end: Optional[date]):
    history = _kpi_history(db, board_id)
    current = _kpi_period(history, period, start, end)
    return {
        "as_of": history.as_of,
        "period": current.as_dict(),
        "coverage": kpis.coverage(history, current),
        "stages": kpis.stage_time(history, current),
    }


def kpi_aging_payload(db: Session, board_id: int):
    history = _kpi_history(db, board_id)
    return {
        "as_of": history.as_of,
        "tracking_started_at": history.tracking_started_at,
        "opportunities": kpis.aging(history),
    }


def kpi_pipeline_payload(db: Session, board_id: int):
    history = _kpi_history(db, board_id)
    stages = kpis.pipeline(history)
    return {"as_of": history.as_of, "stages": stages, "deal_years": kpis.pipeline_deal_years(history)}


@router.get("/boards/{board_id}/kpis/summary", response_model=KpiSummaryResponse)
async def get_kpi_summary(
    board_id: int,
    period: str = "last_30_days",
    start: Optional[date] = None,
    end: Optional[date] = None,
    compare: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Won/lost flow, win rates, creation cohort and sales cycle for a period;
    with compare=true also for the same-length period just before it."""
    return kpi_summary_payload(db, board_id, period, start, end, compare)


@router.get("/boards/{board_id}/kpis/conversion", response_model=KpiConversionResponse)
async def get_kpi_conversion(
    board_id: int,
    period: str = "last_30_days",
    start: Optional[date] = None,
    end: Optional[date] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stage advancement, cohort reach and win rates for a period."""
    return kpi_conversion_payload(db, board_id, period, start, end)


@router.get("/boards/{board_id}/kpis/stage-time", response_model=KpiStageTimeResponse)
async def get_kpi_stage_time(
    board_id: int,
    period: str = "last_30_days",
    start: Optional[date] = None,
    end: Optional[date] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Observed time per stage: complete and partial (baseline) durations of
    stays exited in the period, plus stays still open."""
    return kpi_stage_time_payload(db, board_id, period, start, end)


@router.get("/boards/{board_id}/kpis/aging", response_model=KpiAgingResponse)
async def get_kpi_aging(
    board_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Open opportunities and how long they've been in their current stage."""
    return kpi_aging_payload(db, board_id)


@router.get("/boards/{board_id}/kpis/pipeline", response_model=KpiPipelineResponse)
async def get_kpi_pipeline(
    board_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Current cards per stage and per currency."""
    return kpi_pipeline_payload(db, board_id)


# ============================================================================
# USER MANAGEMENT ROUTES (admin-only)
# ============================================================================


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all users. Admin-only."""
    return db.query(User).order_by(User.created_at).all()


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    payload: UserCreate,
    background: BackgroundTasks,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Invite a new user with the "member" role by email. Admin-only.

    The account is created unverified and without a usable password, so it
    cannot log in. The invitee follows a single-use emailed link, which proves
    control of the mailbox, verifies the email and sets the first password
    (the password-reset flow). The link is never returned or logged."""
    email = payload.email.lower()
    if db.query(User).filter(or_(User.username == email, User.email == email)).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already exists",
        )

    user = User(
        username=email,
        email=email,
        password_hash=UNUSABLE_PASSWORD_HASH,
        role=UserRole.MEMBER.value,
    )
    db.add(user)
    db.flush()
    raw = token_store.issue(db, token_store.PURPOSE_RESET, timedelta(hours=SIGNUP_TOKEN_EXPIRE_HOURS), user_id=user.id)
    db.commit()
    db.refresh(user)
    send_in_background(
        background,
        email,
        "You have been invited to PM",
        f"An administrator created an account for you. Open this link to verify your email and choose a password:"
        f"\n\n{APP_BASE_URL}/?reset_token={raw}\n\nThe link expires in {SIGNUP_TOKEN_EXPIRE_HOURS} hours and works once.",
    )
    return user
