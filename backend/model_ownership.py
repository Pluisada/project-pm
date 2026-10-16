"""Declared ownership of every table, per declarative base (Phase 1.5 inventory).

Data only; nothing imports this at runtime. Three metadata sets exist:

- IdentityBase (identity_models.py): the central identity database, tables
  prefixed `identity_`. The canonical user id originates there.
- CompanyBase (models.py): one company database. COMMERCIAL_TABLES plus the
  minimal local user-reference table `users` (models.CompanyUser).
- Base (models.py), LEGACY: the pre-split `users` and `auth_tokens`, plus copies
  of the CompanyBase tables so the current single-database runtime and tests,
  which create the whole schema from Base.metadata, keep working. It disappears
  when the runtime moves to the central/company split.

Local user reference (`users` in a company database): primary key is the exact
canonical identity_users.id (supplied, never generated); only username (display
name) and full_name; no password, token, role or membership; never removed when
the central user is deactivated; its existence grants nothing. The link to the
central row is the shared number, not a cross-database foreign key. Commercial
foreign keys keep pointing at `users`.

audit_events stays company-owned: it audits card assignment/deletion and holds
no foreign keys. The global identity audit trail is identity_audit_events.
"""
from typing import NamedTuple

LOCAL_USER_TABLE = "users"

# Replaced in the central database by identity_models.py (auth_tokens -> identity_auth_tokens).
LEGACY_ONLY_TABLES = frozenset({"auth_tokens"})

COMMERCIAL_TABLES = frozenset({
    "boards",
    "columns",
    "cards",
    "card_actions",
    "card_edit_history",
    "card_bid_details",
    "card_import_changes",
    "card_source_refs",
    "card_stage_events",
    "conversation_messages",
    "import_batches",
    "import_batch_ledgers",
    "import_batch_items",
    "undo_entries",
    "audit_events",
})

# Everything CompanyBase.metadata must hold.
COMPANY_TABLES = COMMERCIAL_TABLES | {LOCAL_USER_TABLE}


class UserReference(NamedTuple):
    nullable: bool
    ondelete: str


# (table, column) -> the foreign key to the company-local users.id, with the
# nullability and ON DELETE behavior it has always had. A nullable SET NULL
# column is history that must survive its person; a CASCADE column is
# ownership (rows go with the local reference row, which is never deleted when
# the central user is only deactivated).
USER_REFERENCE_FOREIGN_KEYS = {
    ("boards", "user_id"): UserReference(False, "CASCADE"),
    ("cards", "salesperson_id"): UserReference(True, "SET NULL"),
    ("card_edit_history", "user_id"): UserReference(True, "SET NULL"),
    ("card_import_changes", "user_id"): UserReference(True, "SET NULL"),
    ("card_stage_events", "user_id"): UserReference(True, "SET NULL"),
    ("conversation_messages", "user_id"): UserReference(False, "CASCADE"),
    ("import_batches", "user_id"): UserReference(True, "SET NULL"),
    ("import_batch_ledgers", "reverted_by_user_id"): UserReference(True, "SET NULL"),
    ("undo_entries", "user_id"): UserReference(False, "CASCADE"),
}
