"""AI-powered Kanban board updates with structured outputs."""
import json
from typing import Optional
from pydantic import ValidationError
from sqlalchemy.orm import Session

from card_rules import InvalidCardUpdate, apply_card_updates, can_move_card, is_admin
from lead_options import CURRENCIES, LEAD_SOURCES, PRODUCT_LINES
from models import ConversationMessage, Card, BoardColumn as Column, User
from schemas import BoardDetail, CardUpdate
from stage_history import SOURCE_AI, set_stage_context
from undo import record_undo, undo_create, undo_delete, undo_move, undo_update
from database import SessionLocal
from ai import call_ai, AIError


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
    {{"type": "update", "card_id": 1, "title": "New title", "deal_value": 50000, "currency": "EUR"}},
    {{"type": "move", "card_id": 1, "column_id": 2, "position": 0}},
    {{"type": "delete", "card_id": 1}}
  ],
  "confidence": 0.95
}}

Lead fields (all optional, on create and update; send null to clear one):
- company, contact: text
- deal_value: number >= 0, with currency one of: {", ".join(CURRENCIES)}
- expected_close_date: "YYYY-MM-DD"
- lead_source: one of: {", ".join(LEAD_SOURCES)}
- product_line: one of: {", ".join(PRODUCT_LINES)}
- salesperson_id: a user id from the "salespeople" list in the board state

Rules:
- Only suggest actions that make sense for the user's request
- If no changes needed, return empty actions array
- Include helpful explanations in the response
- Be proactive but not overwhelming
- Validate column IDs exist before suggesting moves"""


async def get_board_context(db: Session, board_id: int) -> dict:
    """Get board state for AI context."""
    from models import Board

    board = db.query(Board).filter(Board.id == board_id).first()
    if not board:
        return None

    return {
        "board_id": board.id,
        "title": board.title,
        "salespeople": [
            {"id": u.id, "username": u.username}
            for u in db.query(User).order_by(User.username).all()
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
                        "currency": c.currency,
                        "expected_close_date": (
                            c.expected_close_date.isoformat() if c.expected_close_date else None
                        ),
                        "lead_source": c.lead_source,
                        "product_line": c.product_line,
                        "salesperson_id": c.salesperson_id,
                        "salesperson_name": c.salesperson_name,
                    }
                    for c in (col.cards or [])
                ],
            }
            for col in board.columns
        ],
    }


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


def _validated_card_fields(db: Session, action: dict) -> dict:
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
    if salesperson_id is not None and db.get(User, salesperson_id) is None:
        raise InvalidCardUpdate("Salesperson not found")
    return fields


async def apply_board_actions(
    db: Session, board_id: int, actions: list[dict], current_user: User
) -> dict:
    """Apply AI-suggested actions to the board.

    Goes through the same member/admin rules as the REST card routes (see
    card_rules.py) - the AI chat sidebar is another way to move/edit/delete
    cards, not a bypass of those rules.
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
                            db, {"title": "Untitled", "details": "", **action}
                        )
                    except ValueError as e:
                        results["failed"].append({"action": action, "reason": str(e)})
                        continue

                    # Set directly rather than via apply_card_updates: filling
                    # in a brand-new card isn't an edit to log in its history
                    # (same as the REST create route).
                    card = Card(column_id=column_id, position=action.get("position", 0), **fields)
                    db.add(card)
                    db.flush()
                    undo_steps.append(undo_create(card))
                    results["successful"].append(
                        {"type": "create", "card_id": card.id}
                    )

                elif action_type == "update":
                    card_id = action.get("card_id")
                    card = db.query(Card).filter(Card.id == card_id).first()
                    if not card:
                        results["failed"].append(
                            {"action": action, "reason": "Card not found"}
                        )
                        continue

                    try:
                        fields = _validated_card_fields(db, action)
                        undo_step = undo_update(card, fields)
                        apply_card_updates(db, card, fields, current_user)
                        undo_steps.append(undo_step)
                    except ValueError as e:
                        results["failed"].append({"action": action, "reason": str(e)})
                        continue
                    results["successful"].append({"type": "update", "card_id": card_id})

                elif action_type == "move":
                    card_id = action.get("card_id")
                    column_id = action.get("column_id")

                    card = db.query(Card).filter(Card.id == card_id).first()
                    column = db.query(Column).filter(Column.id == column_id).first()

                    if not card or not column or column.board_id != board_id:
                        results["failed"].append(
                            {"action": action, "reason": "Invalid card or column"}
                        )
                        continue

                    if not can_move_card(current_user, card.column.position, column.position):
                        results["failed"].append(
                            {"action": action, "reason": "Members can only move cards forward, not back to an earlier column"}
                        )
                        continue

                    undo_steps.append(undo_move(card, column.id))
                    card.column_id = column_id
                    card.position = action.get("position", 0)
                    results["successful"].append({"type": "move", "card_id": card_id})

                elif action_type == "delete":
                    card_id = action.get("card_id")
                    card = db.query(Card).filter(Card.id == card_id).first()
                    if not card:
                        results["failed"].append(
                            {"action": action, "reason": "Card not found"}
                        )
                        continue

                    if not is_admin(current_user):
                        results["failed"].append(
                            {"action": action, "reason": "Only admins can delete cards"}
                        )
                        continue

                    undo_steps.append(undo_delete(card))
                    db.delete(card)
                    results["successful"].append({"type": "delete", "card_id": card_id})

                else:
                    # Only card actions exist. Anything structural (columns,
                    # the board) is admin-only through the REST routes and
                    # deliberately has no AI path.
                    results["failed"].append(
                        {"action": action, "reason": "Unsupported action type"}
                    )

            except Exception as e:
                results["failed"].append(
                    {"action": action, "reason": str(e)}
                )

        count = len(results["successful"])
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
        db.rollback()
        return {"success": False, "error": str(e)}
