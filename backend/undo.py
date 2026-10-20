"""Single-level, per-user undo of board changes.

Every card change (REST route or AI action) records the steps that reverse
it, replacing that user's previous entry. Undo applies them and deletes the
entry. A user can only undo their own last change - which is why undo may
move a member's card backward: it restores where *they* had it, it isn't a
new backward move (see card_rules.move_denial). Undo still requires the
user to own the card at undo time (admins: any card).

Step shapes (JSON):
  {"op": "move",   "card_id", "column_id", "position", "expect"}   put a card back
  {"op": "update", "card_id", "fields": {...}, "expect"}           restore old values
  {"op": "delete", "card_id", "expect"}                            remove a created card
  {"op": "create", "column_id", "position", "fields"}              recreate a deleted card

"expect" is the card as the change left it ({"column_id": ...} and/or card
fields, JSON values). Undo first checks that every card still matches it:
if anyone - another user, the AI, an import - changed those values since,
restoring the old ones would silently overwrite that later change, so undo
refuses instead. Steps recorded before "expect" existed can't be checked and
are refused too.
"""
import json
from datetime import date

from pydantic import ValidationError
from sqlalchemy.orm import object_session

from card_rules import CardPermissionDenied, apply_card_updates, audit_deletion, can_edit_card
from stage_history import SOURCE_UNDO, set_stage_context
from models import BoardColumn as Column, Card, UndoEntry, User
from schemas import CardUpdate

CARD_FIELDS = list(CardUpdate.model_fields)


class UndoError(Exception):
    """Undo can't be applied; the message is shown to the user."""


def _json_value(value):
    return value.isoformat() if isinstance(value, date) else value


def card_fields(card: Card, fields=CARD_FIELDS) -> dict:
    return {field: _json_value(getattr(card, field)) for field in fields}


def undo_move(card: Card, to_column_id: int) -> dict:
    """Step putting `card` back where it is now (call before moving it to `to_column_id`)."""
    return {
        "op": "move", "card_id": card.id, "column_id": card.column_id, "position": card.position,
        "expect": {"column_id": to_column_id},
    }


def undo_update(card: Card, updates: dict) -> dict | None:
    """Step restoring the fields `updates` would change (call before applying)."""
    changed = [field for field, value in updates.items() if getattr(card, field) != value]
    if not changed:
        return None
    return {
        "op": "update", "card_id": card.id, "fields": card_fields(card, changed),
        "expect": {field: _json_value(updates[field]) for field in changed},
    }


def undo_create(card: Card) -> dict:
    """Step removing a just-created card (call after creating it)."""
    return {"op": "delete", "card_id": card.id, "expect": {"column_id": card.column_id, **card_fields(card)}}


def _restore_position(card: Card, column_id: int, index: int) -> None:
    """Put `card` back at `index` in `column_id`, renumbering both columns 0..n-1 (no ties, no gaps)."""
    db = object_session(card)
    source_id = card.column_id
    card.column_id = column_id
    others = sorted(db.query(Card).filter(Card.column_id == column_id, Card.id != card.id).all(),
                    key=lambda c: (c.position, c.id))
    others.insert(min(max(index, 0), len(others)), card)
    columns = [others]
    if source_id != column_id:
        columns.append(sorted(db.query(Card).filter(Card.column_id == source_id, Card.id != card.id).all(),
                              key=lambda c: (c.position, c.id)))
    for members in columns:
        for i, member in enumerate(members):
            if member.position != i:
                member.position = i


UNRESTORABLE = {"op": "unrestorable"}


def undo_delete(card: Card) -> dict:
    """Step recreating `card` (call before deleting it). Its edit history,
    which cascades with the card, isn't restored. A card carrying Bid details, a Bid source reference or import
    history cannot be recreated faithfully (those cascade away with it), so its deletion is not undoable:
    record_undo then records nothing and drops the caller's previous entry."""
    if card.bid_details is not None or card.source_ref is not None or card.import_changes:
        return UNRESTORABLE
    return {"op": "create", "column_id": card.column_id, "position": card.position, "fields": card_fields(card)}


def record_undo(db, user: User, board_id: int, description: str, steps: list) -> None:
    """Replace `user`'s undo entry. Caller commits."""
    steps = [step for step in steps if step]
    if not steps:
        return
    db.query(UndoEntry).filter(UndoEntry.user_id == user.id).delete()
    if UNRESTORABLE in steps:
        return  # part of this change cannot be reversed, so none of it is offered (no partial undo)
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


def _check_unchanged_since(db, board_id: int, steps: list) -> None:
    """Refuse unless every card is still as the change left it.

    Expectations are merged per card in the order the change made them, so a
    card edited twice in one AI request is compared with its final state.
    """
    expected = {}
    for step in steps:
        if "card_id" not in step:
            continue
        if "expect" not in step:
            raise UndoError("Can't undo: this change was recorded before undo could check for later edits")
        expected.setdefault(step["card_id"], {}).update(step["expect"])
    for card_id, fields in expected.items():
        card = _board_card(db, board_id, card_id)
        if card is None:
            continue  # a removal step whose card is already gone (checked above for the others)
        current = {field: card.column_id if field == "column_id" else _json_value(getattr(card, field)) for field in fields}
        if current != fields:
            raise UndoError(
                f'Can\'t undo: "{card.title}" was changed since (by another user, the AI or an import)'
            )


def apply_undo(db, user: User, board_id: int, *, admin: bool | None = None) -> str:
    """Reverse `user`'s last change on the board; returns its description.

    All steps are checked before any is applied, so a change that can no
    longer be undone (e.g. someone deleted the card since) leaves the board
    untouched. Either way the entry is used up. Caller commits. `admin` is the company routes' explicit
    card-administration decision (None: is_admin(user)); the entry is still the caller's own.
    """
    entry = get_undo(db, user, board_id)
    if entry is None:
        raise UndoError("Nothing to undo")
    # Claim the entry first: the DELETE takes SQLite's write lock, so of two simultaneous undos only one finds the
    # row and applies it; the other waits, then finds nothing (instead of re-applying the steps).
    claimed = db.query(UndoEntry).filter(UndoEntry.id == entry.id).delete(synchronize_session=False)
    if claimed != 1:
        raise UndoError("Nothing to undo")
    # Moves, deletions and re-creations made by the undo are new, compensating
    # stage events; the events of the change being undone are kept.
    set_stage_context(db, user, SOURCE_UNDO)
    steps = list(reversed(json.loads(entry.steps)))

    for step in steps:
        if "card_id" in step and step["op"] != "delete" and _board_card(db, board_id, step["card_id"]) is None:
            raise UndoError("Can't undo: the card was deleted since")
        if "column_id" in step and _board_column(db, board_id, step["column_id"]) is None:
            raise UndoError("Can't undo: the column no longer exists")
    _check_unchanged_since(db, board_id, json.loads(entry.steps))

    for step in steps:
        op = step["op"]
        if op == "move":
            card = _board_card(db, board_id, step["card_id"])
            if not can_edit_card(user, card, admin=admin):
                raise UndoError("Can't undo: you can only move opportunities assigned to you")
            _restore_position(card, step["column_id"], step["position"])
        elif op == "update":
            card = _board_card(db, board_id, step["card_id"])
            # Goes through the normal update path, so a member's undo of an
            # edit is logged like any other edit by them.
            try:
                fields = CardUpdate(**step["fields"]).model_dump(exclude_unset=True)
            except ValidationError:
                # e.g. an imported Action longer than the 100-character edit limit
                raise UndoError("Can't undo: the previous value is no longer valid")
            try:
                apply_card_updates(db, card, fields, user, admin=admin)
            except CardPermissionDenied as exc:
                raise UndoError(f"Can't undo: {exc}")
        elif op == "delete":
            card = _board_card(db, board_id, step["card_id"])
            if card is not None:  # already gone: nothing to remove
                if not can_edit_card(user, card, admin=admin):
                    raise UndoError("Can't undo: you can only change opportunities assigned to you")
                audit_deletion(db, board_id, card, user)
                db.delete(card)
        elif op == "create":
            fields = CardUpdate(**step["fields"]).model_dump(exclude_unset=True)
            db.add(Card(column_id=step["column_id"], position=step["position"], **fields))
        db.flush()

    return entry.description
