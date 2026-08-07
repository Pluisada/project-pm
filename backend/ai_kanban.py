"""AI-powered Kanban board updates with structured outputs."""
import json
from typing import Optional
from sqlalchemy.orm import Session

from models import ConversationMessage, Card, BoardColumn as Column
from schemas import BoardDetail
from database import SessionLocal
from ai import call_ai, AIError


# System prompt for AI
SYSTEM_PROMPT = """You are a helpful AI assistant for a Kanban board project management tool.

The user will provide:
1. Current board state (columns and cards)
2. Conversation history
3. A new question or request

Your job is to:
1. Understand the user's intent
2. Suggest changes to the board if needed (create, update, move, or delete cards)
3. Provide a helpful response

You MUST respond with valid JSON in this exact format:
{
  "response": "Your helpful message to the user",
  "actions": [
    {"type": "create", "column_id": 1, "title": "Card title", "details": "Card details"},
    {"type": "update", "card_id": 1, "title": "New title", "details": "New details"},
    {"type": "move", "card_id": 1, "column_id": 2, "position": 0},
    {"type": "delete", "card_id": 1}
  ],
  "confidence": 0.95
}

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
        "columns": [
            {
                "id": col.id,
                "title": col.title,
                "position": col.position,
                "cards": [
                    {
                        "id": c.id,
                        "title": c.title,
                        "details": c.details,
                        "position": c.position,
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


async def apply_board_actions(
    db: Session, board_id: int, actions: list[dict]
) -> dict:
    """Apply AI-suggested actions to the board."""
    from models import Board

    results = {
        "successful": [],
        "failed": [],
    }

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

                    card = Card(
                        column_id=column_id,
                        title=action.get("title", "Untitled"),
                        details=action.get("details", ""),
                        position=action.get("position", 0),
                    )
                    db.add(card)
                    db.flush()
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

                    if "title" in action:
                        card.title = action["title"]
                    if "details" in action:
                        card.details = action["details"]

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

                    db.delete(card)
                    results["successful"].append({"type": "delete", "card_id": card_id})

            except Exception as e:
                results["failed"].append(
                    {"action": action, "reason": str(e)}
                )

        db.commit()
        return {"success": True, **results}

    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e)}
