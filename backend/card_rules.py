"""Shared card permission/audit rules.

Used by both the REST routes (routes.py) and the AI chat action-applier
(ai_kanban.py) so member/admin restrictions and the member-edit-history audit
trail can't drift between the two entry points - a member must be bound by
the same rules whether they act through the UI or ask the AI to do it.
"""
import json
from datetime import date, datetime, timezone

from lead_options import DEAL_CURRENCY
from product_catalog import ProductError, check_product_line
from models import AuditEvent, Card, CardEditHistory, CompanyUser, User, UserRole


def is_admin(user: User) -> bool:
    # A central CompanyContext carries is_global_admin; its role string is never judged here.
    if hasattr(user, "is_global_admin"):
        return user.is_global_admin
    return user.role == UserRole.ADMIN.value


def _admin(user: User, admin: bool | None) -> bool:
    """The caller's card-administration authority: `admin` when the caller decided it (the company routes pass a
    company-scoped decision, Phase 2E part 3), otherwise is_admin(user), which AI, undo and legacy use unchanged."""
    return is_admin(user) if admin is None else admin


def move_denial(user: User, card: Card, to_position: int, *, admin: bool | None = None) -> str | None:
    """Why `user` may not move `card` to a column at `to_position`, or None if they may.

    Admins move any card anywhere. Members move only cards assigned to them
    (never unassigned ones), and only forward or within the same column.
    Judged on the card's current database state."""
    if _admin(user, admin):
        return None
    if not can_edit_card(user, card, admin=admin):
        return "You can only move opportunities assigned to you"
    if to_position < card.column.position:
        return "Members can only move cards forward, not back to an earlier column"
    return None


class CardPermissionDenied(PermissionError):
    """The user may not make this change to this card (routes answer 403)."""


def can_edit_card(user: User, card: Card, *, admin: bool | None = None) -> bool:
    """Admins edit every card; members only cards assigned to them. An unassigned
    card is editable by admins alone. A read-only role (a company Visitor) edits nothing,
    even a card assigned to it. Judged on the card's current database state."""
    if _admin(user, admin):
        return True
    if getattr(user, "role", None) == "visitor":
        return False
    return card.salesperson_id is not None and card.salesperson_id == user.id


def record_audit(db, board_id: int, card_id: int | None, actor: User, action: str, details: dict) -> None:
    """Add an audit row (assignment change or deletion) to the open transaction."""
    db.add(AuditEvent(
        board_id=board_id,
        card_id=card_id,
        actor_user_id=actor.id,
        action=action,
        details=json.dumps(details),
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    ))


def _assignment_details(db, card: Card, old_id, new_id) -> dict:
    def name(user_id):
        user = db.get(CompanyUser, user_id) if user_id is not None else None
        return user.username if user else None

    return {"title": card.title, "from_id": old_id, "from": name(old_id), "to_id": new_id, "to": name(new_id)}


def new_card_fields(user: User, fields: dict) -> dict:
    """Owner of a card being created. A member cannot assign: a card they create
    is assigned to themselves (otherwise they could never edit it); an admin's
    choice, if any, stands."""
    return fields if is_admin(user) else {**fields, "salesperson_id": user.id}


def new_card_deal_fields(card) -> dict:
    """Deal Value columns for a card being created (USD; deal_year keeps its column default)."""
    if card.deal_value is None and not card.deal_value_next_year:
        return {}
    return {"deal_value": card.deal_value, "deal_value_next_year": card.deal_value_next_year, "currency": DEAL_CURRENCY}


def audit_new_card_owner(db, board_id: int, card: Card, user: User) -> None:
    """Audit the creation of a freshly flushed card and its initial assignment, if it has one."""
    record_audit(db, board_id, card.id, user, "create", {
        "title": card.title,
        "column": card.column.title if card.column else None,
    })
    if card.salesperson_id is not None:
        record_audit(db, board_id, card.id, user, "assign", _assignment_details(db, card, None, card.salesperson_id))


def audit_deletion(db, board_id: int, card: Card, user: User) -> None:
    record_audit(db, board_id, card.id, user, "delete", {
        "title": card.title,
        "column": card.column.title if card.column else None,
        "salesperson_id": card.salesperson_id,
    })


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
            new_user = db.get(CompanyUser, new) if new is not None else None
            changes["salesperson"] = {
                "old": card.salesperson_name,
                "new": new_user.username if new_user else None,
            }
        else:
            changes[field] = {"old": _history_value(old), "new": _history_value(new)}
    return changes


def apply_card_updates(db, card: Card, updates: dict, user: User, *, admin: bool | None = None) -> dict:
    """Apply a validated partial update to a card, logging member edits.

    The single update path for both the REST route and the AI sidebar.
    Raises CardPermissionDenied unless `user` may edit this card (and, for a
    change of salesperson, is an admin), and InvalidCardUpdate for a
    salesperson_id that isn't a user. A change of salesperson is audited.
    """
    if not can_edit_card(user, card, admin=admin):
        raise CardPermissionDenied("You can only edit opportunities assigned to you")
    reassigns = "salesperson_id" in updates and updates["salesperson_id"] != card.salesperson_id
    if reassigns and not _admin(user, admin):
        raise CardPermissionDenied("Only an administrator can assign or reassign opportunities")
    salesperson_id = updates.get("salesperson_id")
    if salesperson_id is not None and db.get(CompanyUser, salesperson_id) is None:
        raise InvalidCardUpdate("Salesperson not found")
    try:
        check_product_line(db, updates, card.product_line)
    except ProductError as exc:
        raise InvalidCardUpdate(str(exc))

    previous_owner = card.salesperson_id
    changes = diff_card_changes(db, card, updates)
    for field, value in updates.items():
        setattr(card, field, value)
    if updates.keys() & {"deal_value", "deal_value_next_year", "currency"}:
        card.currency = DEAL_CURRENCY  # Deal Value is always USD; not a logged change
    record_member_edit(db, card, user, changes, admin=admin)
    audit_admin_edit(db, card, user, changes, admin=admin, owner=previous_owner)
    if reassigns:
        record_audit(
            db, card.column.board_id, card.id, user, "assign",
            _assignment_details(db, card, previous_owner, card.salesperson_id),
        )
    return changes


def audit_admin_edit(db, card: Card, user: User, changes: dict, *, admin: bool | None, owner: int | None) -> None:
    """Audit a company Admin's content edit of a card owned by someone else (Phase 2E remediation M2).

    Only the company routes pass an explicit admin decision, and only a company Admin has it without being a global
    administrator, so the Super Admin, members and the legacy path are untouched. A reassignment is already
    audited as "assign" and is left out here. Field names only, never values: the audit row is in the same
    transaction as the edit (the caller commits both or neither) and is not a CardEditHistory row, so the "M" badge
    keeps meaning "a member changed this".
    """
    if admin is not True or getattr(user, "is_global_admin", True) or owner is None or owner == user.id:
        return
    fields = sorted(field for field in changes if field != "salesperson")
    if fields:
        record_audit(db, card.column.board_id, card.id, user, "edit", {"title": card.title, "fields": fields})


def record_member_edit(db, card: Card, user: User, changes: dict, *, admin: bool | None = None) -> None:
    """Log a CardEditHistory row for a member's tracked content change.

    Admin edits are intentionally not logged here - this is what backs the
    "M" badge, which only ever means "a member changed this."
    """
    if changes and not _admin(user, admin):
        db.add(CardEditHistory(
            card_id=card.id,
            user_id=user.id,
            changes=json.dumps(changes),
        ))
