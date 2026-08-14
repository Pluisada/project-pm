"""API routes for boards, columns, and cards."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from auth import hash_password
from database import get_db
from deps import get_current_user, require_admin
from models import Board, Column, Card, User, UserRole, ConversationMessage
from schemas import (
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
    ColumnWithCards,
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
    current_user: User = Depends(get_current_user),
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


@router.get("/boards/{board_id}", response_model=BoardDetail)
async def get_board(
    board_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get board with all columns and cards."""
    board = db.query(Board).filter(Board.id == board_id).first()

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )

    # Load columns with cards, ordered by position
    columns = db.query(Column).filter(Column.board_id == board_id).order_by(Column.position).all()

    columns_data = []
    for column in columns:
        cards = db.query(Card).filter(Card.column_id == column.id).order_by(Card.position).all()
        columns_data.append(ColumnWithCards(
            **{**column.__dict__, "cards": cards}
        ))

    return BoardDetail(
        **board.__dict__,
        columns=columns_data,
    )


@router.put("/boards/{board_id}", response_model=BoardResponse)
async def update_board(
    board_id: int,
    board_update: BoardUpdate,
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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


@router.post("/boards/{board_id}/columns", response_model=ColumnResponse, status_code=201)
async def create_column(
    board_id: int,
    column: ColumnCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a column in a board."""
    board = db.query(Board).filter(Board.id == board_id).first()

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )

    db_column = Column(
        board_id=board_id,
        title=column.title,
        position=column.position,
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
    current_user: User = Depends(get_current_user),
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

    if column_update.title is not None:
        column.title = column_update.title
    if column_update.position is not None:
        column.position = column_update.position

    db.commit()
    db.refresh(column)
    return column


@router.delete("/boards/{board_id}/columns/{column_id}", status_code=204)
async def delete_column(
    board_id: int,
    column_id: int,
    current_user: User = Depends(get_current_user),
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
    )
    db.add(db_card)
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

    if card_update.title is not None:
        card.title = card_update.title
    if card_update.details is not None:
        card.details = card_update.details

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

    card.column_id = move.column_id
    card.position = move.position

    db.commit()
    db.refresh(card)
    return card


@router.delete("/boards/{board_id}/cards/{card_id}", status_code=204)
async def delete_card(
    board_id: int,
    card_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a card."""
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

    db.delete(card)
    db.commit()


# ============================================================================
# AI ROUTES
# ============================================================================


@router.post("/ai/test")
async def test_ai():
    """Test AI connectivity with a simple question."""
    result = await test_ai_connectivity("What is 2+2?")

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=result.get("error", "AI service error"),
        )

    return result


@router.post("/boards/{board_id}/ai")
async def board_ai_chat(
    board_id: int,
    request: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """AI chat endpoint for board management."""
    # Verify board exists and belongs to user
    board = db.query(Board).filter(Board.id == board_id).first()

    if not board:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Board not found",
        )

    user_message = request.get("message", "")
    if not user_message:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Message required",
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
            actions_result = await apply_board_actions(db, board_id, ai_result["actions"])
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


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
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new user with the "member" role. Admin-only."""
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=UserRole.MEMBER.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
