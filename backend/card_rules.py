"""Shared card permission/audit rules.

Used by both the REST routes (routes.py) and the AI chat action-applier
(ai_kanban.py) so member/admin restrictions and the member-edit-history audit
trail can't drift between the two entry points - a member must be bound by
the same rules whether they act through the UI or ask the AI to do it.
"""
import json

from models import Card, CardEditHistory, User, UserRole


def is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN.value


def can_move_card(user: User, from_position: int, to_position: int) -> bool:
    """Members may only advance cards (equal or higher column position) or
    reorder within the same column; admins are unrestricted."""
    return is_admin(user) or to_position >= from_position


def diff_card_changes(card: Card, title: str = None, details: str = None) -> dict:
    """Build a {field: {old, new}} diff of only the fields that actually change."""
    changes = {}
    if title is not None and title != card.title:
        changes["title"] = {"old": card.title, "new": title}
    if details is not None and details != card.details:
        changes["details"] = {"old": card.details, "new": details}
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
