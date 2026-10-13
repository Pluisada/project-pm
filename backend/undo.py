"""Single-level, per-user undo of board changes.

Every card change (REST route or AI action) records the steps that reverse
it, replacing that user's previous entry. Undo applies them and deletes the
entry. A user can only undo their own last change - which is why undo may
move a member's card backward: it restores where *they* had it, it isn't a
new backward move (see card_rules.can_move_card).

Step shapes (JSON):
  {"op": "move",   "card_id", "column_id", "position"}   put a card back
  {"op": "update", "card_id", "fields": {...}}           restore old values
  {"op": "delete", "card_id"}                            remove a created card
  {"op": "create", "column_id", "position", "fields"}    recreate a deleted card
"""
import json
from datetime import date

from card_rules import apply_card_updates
from models import BoardColumn as Column, Card, UndoEntry, User
from schemas import CardUpdate

CARD_FIELDS = list(CardUpdate.model_fields)


class UndoError(Exception):
    """Undo can't be applied; the message is shown to the user."""


def _json_value(value):
    return value.isoformat() if isinstance(value, date) else value


def card_fields(card: Card, fields=CARD_FIELDS) -> dict:
    return {field: _json_value(getattr(card, field)) for field in fields}


def undo_move(card: Card) -> dict:
    """Step putting `card` back where it is now (call before moving it)."""
    return {"op": "move", "card_id": card.id, "column_id": card.column_id, "position": card.position}


def undo_update(card: Card, updates: dict) -> dict | None:
    """Step restoring the fields `updates` would change (call before applying)."""
    changed = [field for field, value in updates.items() if getattr(card, field) != value]
    if not changed:
        return None
    return {"op": "update", "card_id": card.id, "fields": card_fields(card, changed)}


def undo_create(card: Card) -> dict:
    return {"op": "delete", "card_id": card.id}


def undo_delete(card: Card) -> dict:
    """Step recreating `card` (call before deleting it). Its edit history,
    which cascades with the card, isn't restored."""
    return {"op": "create", "column_id": card.column_id, "position": card.position, "fields": card_fields(card)}


def record_undo(db, user: User, board_id: int, description: str, steps: list) -> None:
    """Replace `user`'s undo entry. Caller commits."""
    steps = [step for step in steps if step]
    if not steps:
        return
    db.query(UndoEntry).filter(UndoEntry.user_id == user.id).delete()
    db.add(UndoEntry(user_id=user.id, board_id=board_id, description=description, steps=json.dumps(steps)))


def get_undo(db, user: User, board_id: int) -> UndoEntry | None:
    return (
        db.query(UndoEntry)
        .filter(UndoEntry.user_id == user.id, UndoEntry.board_id == board_id)
        .first()
    )


def _board_card(db, board_id: int, card_id: int) -> Card | None:
    return (
        db.query(Card)
        .join(Column)
        .filter(Card.id == card_id, Column.board_id == board_id)
        .first()
    )


def _board_column(db, board_id: int, column_id: int) -> Column | None:
    return db.query(Column).filter(Column.id == column_id, Column.board_id == board_id).first()


def apply_undo(db, user: User, board_id: int) -> str:
    """Reverse `user`'s last change on the board; returns its description.

    All steps are checked before any is applied, so a change that can no
    longer be undone (e.g. someone deleted the card since) leaves the board
    untouched. Either way the entry is used up. Caller commits.
    """
    entry = get_undo(db, user, board_id)
    if entry is None:
        raise UndoError("Nothing to undo")
    steps = list(reversed(json.loads(entry.steps)))
    db.delete(entry)

    for step in steps:
        if "card_id" in step and step["op"] != "delete" and _board_card(db, board_id, step["card_id"]) is None:
            raise UndoError("Can't undo: the card was deleted since")
        if "column_id" in step and _board_column(db, board_id, step["column_id"]) is None:
            raise UndoError("Can't undo: the column no longer exists")

    for step in steps:
        op = step["op"]
        if op == "move":
            card = _board_card(db, board_id, step["card_id"])
            card.column_id = step["column_id"]
            card.position = step["position"]
        elif op == "update":
            card = _board_card(db, board_id, step["card_id"])
            # Goes through the normal update path, so a member's undo of an
            # edit is logged like any other edit by them.
            fields = CardUpdate(**step["fields"]).model_dump(exclude_unset=True)
            apply_card_updates(db, card, fields, user)
        elif op == "delete":
            card = _board_card(db, board_id, step["card_id"])
            if card is not None:  # already gone: nothing to remove
                db.delete(card)
        elif op == "create":
            fields = CardUpdate(**step["fields"]).model_dump(exclude_unset=True)
            db.add(Card(column_id=step["column_id"], position=step["position"], **fields))
        db.flush()

    return entry.description
