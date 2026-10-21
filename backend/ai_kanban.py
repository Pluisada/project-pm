"""AI-powered Kanban board updates with structured outputs."""
import json
import logging
from typing import Optional
from pydantic import ValidationError
from sqlalchemy.orm import Session

from card_rules import (
    CardPermissionDenied,
    InvalidCardUpdate,
    apply_card_updates,
    audit_deletion,
    audit_new_card_owner,
    move_denial,
    is_admin,
    new_card_fields,
)
from lead_options import DEAL_CURRENCY, LEAD_SOURCES, STAGE_LOST
from product_catalog import ProductError, catalog_names, check_product_line
from models import ConversationMessage, Card, BoardColumn as Column, CompanyUser
from schemas import BoardDetail, CardUpdate
from stage_history import SOURCE_AI, set_stage_context
import card_loss
import card_reviews
from undo import UNRESTORABLE, place_card, record_undo, undo_create, undo_delete, undo_move, undo_update
from database import SessionLocal
from ai import call_ai, AIError

logger = logging.getLogger(__name__)

ACTION_FAILED_REASON = "This action could not be applied"
ACTIONS_FAILED_ERROR = "Failed to apply actions"
LOGGED_ACTION_TYPES = ("create", "update", "move", "delete")  # the type is model output: only known values are logged

# Exceptions whose messages are written for the user; anything else may quote SQL, parameters or paths.
SAFE_ACTION_ERRORS = (
    ValueError, CardPermissionDenied, ProductError, card_loss.LossError, card_reviews.ReviewError,
)


# System prompt for AI
SYSTEM_PROMPT = f"""You are a helpful AI assistant for Salcomp's sales lead tracking board.
Each card is a sales lead; columns are the sales stages, in position order.
Each column has a "stage_kind": "won" means deals in it were won, "lost" that they
were lost, "open" that they're still in progress. Go by stage_kind, not by title.

The user will provide:
1. Current board state (columns and cards)
2. Conversation history
3. A new question or request

Your job is to:
1. Understand the user's intent
2. Suggest changes to the board if needed (create, update, move, or delete cards)
3. Provide a helpful response

You MUST respond with valid JSON in this exact format:
{{
  "response": "Your helpful message to the user",
  "actions": [
    {{"type": "create", "column_id": 1, "title": "Card title", "details": "Card details", "company": "Acme"}},
    {{"type": "update", "card_id": 1, "title": "New title", "deal_value": 50000}},
    {{"type": "move", "card_id": 1, "column_id": 2, "position": 0}},
    {{"type": "delete", "card_id": 1}}
  ],
  "confidence": 0.95
}}

Lead fields (all optional, on create and update; send null to clear one):
- company, contact: text
- deal_value (current year) and deal_value_next_year (the following year): numbers >= 0, always in USD; never send another currency
- expected_close_date: "YYYY-MM-DD" (when the sale is expected to close)
- next_step_due: "YYYY-MM-DD" (Next Action Date); next_step: the Action, a short text of at most 100 characters
- lead_source: one of: {", ".join(LEAD_SOURCES)}
- product_line: one of the "product_lines" listed in the board state
- confidence_level: exactly "LOW", "MEDIUM" or "HIGH" (upper case), or null for not assigned. Set it only when the user states the level explicitly; never infer it from other fields, notes or imported data
- salesperson_id: a user id from the "salespeople" list in the board state

Rules:
- Only suggest actions that make sense for the user's request
- If no changes needed, return empty actions array
- Include helpful explanations in the response
- Be proactive but not overwhelming
- Validate column IDs exist before suggesting moves
- If the board state has "loss_reason_required": true, moving a card into a "lost" column, or creating one there,
  needs a loss reason that the USER stated explicitly. The application attaches it to the request; you never write
  one in an action. NEVER invent a loss reason or infer one from card titles, details, notes or other fields. If the
  user asks for such a move without giving a reason, do not guess one: ask them for it in your response (the
  application refuses the change until they provide it)"""


async def get_board_context(db: Session, board_id: int) -> dict:
    """Get board state for AI context."""
    from models import Board

    board = db.query(Board).filter(Board.id == board_id).first()
    if not board:
        return None

    state = {
        "board_id": board.id,
        "title": board.title,
        "product_lines": catalog_names(db),
        "salespeople": [
            {"id": u.id, "username": u.username}
            for u in db.query(CompanyUser).order_by(CompanyUser.username).all()
        ],
        "columns": [
            {
                "id": col.id,
                "title": col.title,
                "position": col.position,
                "stage_kind": col.stage_kind,
                "cards": [
                    {
                        "id": c.id,
                        "title": c.title,
                        "details": c.details,
                        "position": c.position,
                        "company": c.company,
                        "contact": c.contact,
                        "deal_value": c.deal_value,
                        "currency": DEAL_CURRENCY,
                        "expected_close_date": (
                            c.expected_close_date.isoformat() if c.expected_close_date else None
                        ),
                        "lead_source": c.lead_source,
                        "product_line": c.product_line,
                        "confidence_level": c.confidence_level,
                        "salesperson_id": c.salesperson_id,
                        "salesperson_name": c.salesperson_name,
                    }
                    for c in (col.cards or [])
                ],
            }
            for col in board.columns
        ],
    }
    if card_loss.is_company_session(db):
        state["loss_reason_required"] = True  # company boards only; the legacy state is unchanged
    return state


async def get_conversation_history(
    db: Session, board_id: int, limit: int = 10
) -> list[dict]:
    """Get recent conversation history."""
    messages = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.board_id == board_id)
        .order_by(ConversationMessage.created_at.desc())
        .limit(limit)
        .all()
    )

    # Reverse to get chronological order
    return [
        {"role": msg.role, "content": msg.content}
        for msg in reversed(messages)
    ]


async def call_ai_with_board(
    board_state: dict,
    conversation_history: list[dict],
    user_message: str,
) -> dict:
    """Call AI with board context and get structured response."""
    # Build messages for API
    context = f"""Current Board State:
{json.dumps(board_state, indent=2)}

Recent Conversation:
{json.dumps(conversation_history, indent=2)}"""

    messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + conversation_history
        + [
            {
                "role": "user",
                "content": f"{context}\n\nUser's Request: {user_message}",
            }
        ]
    )

    try:
        response = await call_ai(
            messages=messages,
            temperature=0.7,
            max_tokens=2000,
            response_format="json_object",
        )

        # Parse AI response as JSON
        content = response["content"]
        try:
            ai_response = json.loads(content)
        except json.JSONDecodeError:
            # If not JSON, wrap in response format
            ai_response = {
                "response": content,
                "actions": [],
                "confidence": 0.5,
            }

        return {
            "success": True,
            "response": ai_response.get("response", ""),
            "actions": ai_response.get("actions", []),
            "confidence": ai_response.get("confidence", 0.5),
            "tokens_used": response["tokens_used"],
        }

    except AIError as e:
        return {
            "success": False,
            "error": str(e),
        }


def _validated_card_fields(db: Session, action: dict, assignable_ids: set | None = None) -> dict:
    """Pick an action's card fields and validate them like the REST route does.

    Raises ValueError (ValidationError and InvalidCardUpdate both are) with a
    readable reason for the action's "failed" entry.
    """
    raw = {k: v for k, v in action.items() if k in CardUpdate.model_fields}
    try:
        fields = CardUpdate(**raw).model_dump(exclude_unset=True)
    except ValidationError as e:
        raise ValueError(
            "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        )
    salesperson_id = fields.get("salesperson_id")
    if salesperson_id is not None and (
        db.get(CompanyUser, salesperson_id) is None
        or (assignable_ids is not None and salesperson_id not in assignable_ids)
    ):
        raise InvalidCardUpdate("Salesperson not found")
    try:
        check_product_line(db, fields)
    except ProductError as exc:
        raise InvalidCardUpdate(str(exc))
    return fields


def _board_card(db: Session, board_id: int, card_id) -> Card | None:
    """The card, only if its column belongs to this board (card -> column -> board); any other board is "not found"."""
    return db.query(Card).join(Column).filter(Card.id == card_id, Column.board_id == board_id).first()


async def apply_board_actions(
    db: Session, board_id: int, actions: list[dict], current_user,
    assignable_ids: set | None = None, record_undo_entry: bool = True, admin: bool | None = None,
    can_delete: bool | None = None, loss_reason: str | None = None,
) -> dict:
    """Apply AI-suggested actions to the board.

    Goes through the same member/admin rules as the REST card routes (see
    card_rules.py) - the AI chat sidebar is another way to move/edit/delete
    cards, not a bypass of those rules. `admin` is an explicit card-administration decision from the company
    routes (Phase 2E part 4); None keeps is_admin(current_user), as the legacy route and every other caller do.
    `can_delete` is the separate, narrower decision to delete (company routes: Super Admin only); None falls
    back to `admin`, as the legacy route does. When `admin` is True (company routes) a created card needs a
    salesperson and keeps it; otherwise the creator owns the card.
    """
    from models import Board

    set_stage_context(db, current_user, SOURCE_AI)
    results = {
        "successful": [],
        "failed": [],
    }
    # Inverse steps of every applied action, recorded as one undo entry so a
    # single Undo click reverses the whole AI request.
    undo_steps = []

    try:
        board = db.query(Board).filter(Board.id == board_id).first()
        if not board:
            return {"error": "Board not found"}

        for action in actions:
            action_type = action.get("type")

            try:
                if action_type == "create":
                    column_id = action.get("column_id")
                    column = db.query(Column).filter(Column.id == column_id).first()
                    if not column or column.board_id != board_id:
                        results["failed"].append(
                            {"action": action, "reason": "Invalid column"}
                        )
                        continue

                    try:
                        fields = _validated_card_fields(
                            db, {"title": "Untitled", "details": "", **action}, assignable_ids
                        )
                    except ValueError as e:
                        results["failed"].append({"action": action, "reason": str(e)})
                        continue

                    # Set directly rather than via apply_card_updates: filling
                    # in a brand-new card isn't an edit to log in its history
                    # (same as the REST create route).
                    if admin is True:
                        # Same rule as the company REST create: an administrator names an eligible salesperson
                        # (checked by _validated_card_fields); nothing falls back to the creator.
                        if fields.get("salesperson_id") is None:
                            results["failed"].append({"action": action, "reason": "A salesperson is required"})
                            continue
                        owner_fields = fields
                    else:
                        owner_fields = new_card_fields(current_user, fields)
                    create_reason = None
                    if card_loss.is_company_session(db) and column.stage_kind == STAGE_LOST:
                        # Same rule as a move into LOST: the reason is the person's, never the model's.
                        try:
                            card_loss.require_loss(db)
                            create_reason = card_loss.clean_reason(loss_reason)
                        except card_loss.LossError as exc:
                            results["failed"].append({"action": action, "reason": (
                                f"{exc.message}. Ask the user for the reason and send it in the loss_reason field of the request"
                                if isinstance(exc, card_loss.LossReasonRequired) else exc.message)})
                            continue
                    card = Card(
                        column_id=column_id,
                        position=action.get("position", 0),
                        **owner_fields,
                    )
                    db.add(card)
                    db.flush()
                    if create_reason is not None:
                        card_loss.note_loss(db, card, current_user, create_reason, None, column, "ai")
                    audit_new_card_owner(db, board_id, card, current_user)
                    # Undoing a creation deletes the card: only offered where the caller may delete (None: legacy rule).
                    undo_steps.append(undo_create(card) if can_delete is not False else UNRESTORABLE)
                    results["successful"].append(
                        {"type": "create", "card_id": card.id}
                    )

                elif action_type == "update":
                    card_id = action.get("card_id")
                    card = _board_card(db, board_id, card_id)
                    if not card:
                        results["failed"].append(
                            {"action": action, "reason": "Card not found"}
                        )
                        continue

                    try:
                        fields = _validated_card_fields(db, action, assignable_ids)
                        undo_step = undo_update(card, fields)
                        apply_card_updates(db, card, fields, current_user, admin=admin)
                        undo_steps.append(undo_step)
                    except (ValueError, CardPermissionDenied) as e:
                        results["failed"].append({"action": action, "reason": str(e)})
                        continue
                    results["successful"].append({"type": "update", "card_id": card_id})

                elif action_type == "move":
                    card_id = action.get("card_id")
                    column_id = action.get("column_id")

                    card = _board_card(db, board_id, card_id)
                    column = db.query(Column).filter(Column.id == column_id).first()

                    if not card or not column or column.board_id != board_id:
                        results["failed"].append(
                            {"action": action, "reason": "Invalid card or column"}
                        )
                        continue

                    new_position = action.get("position", 0)
                    if not isinstance(new_position, int) or isinstance(new_position, bool):
                        results["failed"].append({"action": action, "reason": "Invalid position"})
                        continue

                    denial = move_denial(current_user, card, column.position, admin=admin)
                    if denial:
                        results["failed"].append({"action": action, "reason": denial})
                        continue

                    entering_lost = card_loss.is_company_session(db) and card_loss.enters_lost(card, column)
                    if entering_lost:
                        # Company boards: a move into LOST needs the reason the PERSON gave in this request. The
                        # action's own text (written by the model) is never used, so a reason cannot be invented.
                        try:
                            card_loss.require_loss(db)
                            reason = card_loss.clean_reason(loss_reason)
                        except card_loss.LossError as exc:
                            results["failed"].append({"action": action, "reason": (
                                f"{exc.message}. Ask the user for the reason and send it in the loss_reason field of the request"
                                if isinstance(exc, card_loss.LossReasonRequired) else exc.message)})
                            continue
                        card_loss.note_loss(db, card, current_user, reason, card.column, column, "ai")
                    if column.position < card.column.position:
                        # Only an Admin-level caller passes move_denial for a backward move: the Super Admin reviews it.
                        card_reviews.note_backward_move(db, card, current_user, card.column, column)
                    undo_steps.append(undo_move(card, column.id))
                    place_card(card, column_id, new_position)
                    results["successful"].append({"type": "move", "card_id": card_id})

                elif action_type == "delete":
                    card_id = action.get("card_id")
                    card = _board_card(db, board_id, card_id)
                    if not card:
                        results["failed"].append(
                            {"action": action, "reason": "Card not found"}
                        )
                        continue

                    may_delete = can_delete if can_delete is not None else (is_admin(current_user) if admin is None else admin)
                    if not may_delete:
                        results["failed"].append(
                            {"action": action, "reason": "Only admins can delete cards"}
                        )
                        continue

                    undo_steps.append(undo_delete(card))
                    audit_deletion(db, board_id, card, current_user)
                    db.delete(card)
                    results["successful"].append({"type": "delete", "card_id": card_id})

                else:
                    # Only card actions exist. Anything structural (columns,
                    # the board) is admin-only through the REST routes and
                    # deliberately has no AI path.
                    results["failed"].append(
                        {"action": action, "reason": "Unsupported action type"}
                    )

            except SAFE_ACTION_ERRORS as e:
                results["failed"].append({"action": action, "reason": str(e)})
            except Exception as e:
                # Class and category only: the message and traceback can quote SQL, bound parameters and card data.
                logger.error("AI action failed: category=action_error exception=%s action_type=%s",
                             type(e).__name__, action_type if action_type in LOGGED_ACTION_TYPES else "other")
                results["failed"].append({"action": action, "reason": ACTION_FAILED_REASON})

        count = len(results["successful"])
        if record_undo_entry:
            record_undo(
                db,
                current_user,
                board_id,
                f"Undo AI assistant changes ({count} change{'s' if count != 1 else ''})",
                undo_steps,
            )
        db.commit()
        return {"success": True, **results}

    except Exception as e:
        logger.error("AI action batch failed: category=batch_error exception=%s", type(e).__name__)
        db.rollback()
        return {"success": False, "error": ACTIONS_FAILED_ERROR}
