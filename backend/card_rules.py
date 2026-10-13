"""Shared card permission/audit rules.

Used by both the REST routes (routes.py) and the AI chat action-applier
(ai_kanban.py) so member/admin restrictions and the member-edit-history audit
trail can't drift between the two entry points - a member must be bound by
the same rules whether they act through the UI or ask the AI to do it.
"""
import json
from datetime import date

from models import Card, CardEditHistory, User, UserRole


def is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN.value


def can_move_card(user: User, from_position: int, to_position: int) -> bool:
    """Members may only advance cards (equal or higher column position) or
    reorder within the same column; admins are unrestricted."""
    return is_admin(user) or to_position >= from_position


class InvalidCardUpdate(ValueError):
    """An update that passed schema validation but references bad data."""


def _history_value(value):
    return value.isoformat() if isinstance(value, date) else value


def diff_card_changes(db, card: Card, updates: dict) -> dict:
    """Build a {field: {old, new}} diff of only the fields that actually change.

    `updates` is a CardUpdate dumped with exclude_unset=True. The salesperson
    is recorded by username (under "salesperson") so history reads well.
    """
    changes = {}
    for field, new in updates.items():
        old = getattr(card, field)
        if new == old:
            continue
        if field == "salesperson_id":
            new_user = db.get(User, new) if new is not None else None
            changes["salesperson"] = {
                "old": card.salesperson_name,
                "new": new_user.username if new_user else None,
            }
        else:
            changes[field] = {"old": _history_value(old), "new": _history_value(new)}
    return changes


def apply_card_updates(db, card: Card, updates: dict, user: User) -> dict:
    """Apply a validated partial update to a card, logging member edits.

    The single update path for both the REST route and the AI sidebar.
    Raises InvalidCardUpdate for a salesperson_id that isn't a user.
    """
    salesperson_id = updates.get("salesperson_id")
    if salesperson_id is not None and db.get(User, salesperson_id) is None:
        raise InvalidCardUpdate("Salesperson not found")

    changes = diff_card_changes(db, card, updates)
    for field, value in updates.items():
        setattr(card, field, value)
    record_member_edit(db, card, user, changes)
    return changes


def record_member_edit(db, card: Card, user: User, changes: dict) -> None:
    """Log a CardEditHistory row for a member's tracked content change.

    Admin edits are intentionally not logged here - this is what backs the
    "M" badge, which only ever means "a member changed this."
    """
    if changes and not is_admin(user):
        db.add(CardEditHistory(
            card_id=card.id,
            user_id=user.id,
            changes=json.dumps(changes),
        ))
