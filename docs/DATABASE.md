# Database Design - Project Management MVP

## Overview

This document describes the database schema for the Project Management MVP application. The schema is designed to support:

- **Multi-user architecture** with admin/member roles; the first user created becomes admin, and only admins can create further users
- **Shared Kanban boards**: boards are not owned/isolated per user - any authenticated user (admin or member) can see and edit them
- **Persistent card data** with full CRUD operations
- **AI conversation history** for chatbot context
- **Audit trail** of all changes
- **Scalability** to production environment (PostgreSQL)

---

## Database Choice

**MVP:** SQLite (simple, no setup required)  
**Production:** PostgreSQL (recommended for scaling)

### Why SQLite for MVP?

- ✅ No database server to manage
- ✅ Single file (pm.db)
- ✅ Perfect for development
- ✅ Can easily migrate to PostgreSQL later

### Why PostgreSQL for Production?

- ✅ Better concurrency
- ✅ Full ACID compliance
- ✅ Advanced indexing options
- ✅ JSON support (JSONB)
- ✅ Can handle millions of records

---

## Schema Overview

```
┌─────────────────────────────────────────────────────────┐
│                        USERS                             │
│  • id (PK)                                               │
│  • username (UNIQUE)                                     │
│  • password_hash (bcrypt)                                │
│  • role ("admin" or "member")                            │
│  • email, full_name                                      │
│  • created_at, updated_at                                │
└─────────────────────────────────────────────────────────┘
              │
              │ (1:N, "created by" - not an access filter)
              ▼
┌─────────────────────────────────────────────────────────┐
│                       BOARDS                             │
│  • id (PK)                                               │
│  • user_id (FK) → users (creator; boards are shared)     │
│  • title, description                                    │
│  • created_at, updated_at                                │
└─────────────────────────────────────────────────────────┘
              │
              │ (1:N)
              ▼
┌─────────────────────────────────────────────────────────┐
│                      COLUMNS                             │
│  • id (PK)                                               │
│  • board_id (FK) → boards                                │
│  • title (e.g., "Backlog", "In Progress")               │
│  • position (for ordering)                               │
│  • created_at, updated_at                                │
└─────────────────────────────────────────────────────────┘
              │
              │ (1:N)
              ▼
┌─────────────────────────────────────────────────────────┐
│                       CARDS                              │
│  • id (PK)                                               │
│  • column_id (FK) → columns                              │
│  • title, details                                        │
│  • position (for ordering within column)                 │
│  • created_at, updated_at                                │
└─────────────────────────────────────────────────────────┘

                    SEPARATE TREES

┌─────────────────────────────────────────────────────────┐
│              CONVERSATION_MESSAGES                       │
│  • id (PK)                                               │
│  • board_id (FK) → boards                                │
│  • user_id (FK) → users                                  │
│  • role (user or assistant)                              │
│  • content                                               │
│  • metadata (JSON)                                       │
│  • created_at                                            │
└─────────────────────────────────────────────────────────┘
              │
              │ (1:N)
              ▼
┌─────────────────────────────────────────────────────────┐
│                   CARD_ACTIONS                           │
│  • id (PK)                                               │
│  • board_id (FK) → boards                                │
│  • message_id (FK) → conversation_messages               │
│  • action_type (create, update, move, delete)            │
│  • card_id (FK) → cards                                  │
│  • changes (JSON)                                        │
│  • created_at                                            │
└─────────────────────────────────────────────────────────┘
```

---

## Detailed Table Specifications

### users
**Purpose:** Store user accounts and authentication info

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK, AUTOINCREMENT | Unique identifier |
| username | TEXT | UNIQUE, NOT NULL | Login credential |
| password_hash | TEXT | NOT NULL | Bcrypt password hash |
| role | TEXT | NOT NULL, DEFAULT "member" | "admin" or "member" |
| email | TEXT | UNIQUE | Contact email (optional for MVP) |
| full_name | TEXT | - | Display name (optional) |
| session_version | INTEGER | NOT NULL, DEFAULT 0 | Bumped on logout and on password change/reset; access tokens carry it (`sv`) and are rejected on mismatch. Ends all of the user's sessions |
| email_verified_at | TIMESTAMP | NULL | Set when the user proves control of the email (signup/reset link). NULL = no application access |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Account creation |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Last modified |

**Indexes:**
- `username (UNIQUE)` - Fast lookup by username
- `email (UNIQUE)` - Fast lookup by email
- `created_at` - Query recent users

**Current:** The first user ever created (via `POST /api/setup`) becomes `role="admin"`. Only an admin can create further users (`POST /api/users`), who are always created with `role="member"`. Role is re-read from this table on every request (not embedded in the JWT), so it can't go stale.
**Future:** More granular profiles beyond admin/member (viewer, editor, etc.)

`password_hash = "!"` marks an account with no usable password (never a bcrypt hash, so no password matches): this is how the commercial administrator is provisioned. Added by `migrate_auth_security.py` (existing users are marked verified at their `created_at`).

### auth_tokens
**Purpose:** Persistent, revocable, single-use signup and password-reset tokens.

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK | |
| token_hash | VARCHAR(64) | UNIQUE, NOT NULL | SHA-256 of the random token; the raw token is never stored |
| purpose | VARCHAR(20) | NOT NULL | `signup` or `reset` |
| user_id | INTEGER | FK users, ON DELETE CASCADE, NULL | The user (reset); NULL for a pending signup |
| email | TEXT | NULL | The pending registration's address (signup) |
| created_at / expires_at | TIMESTAMP | NOT NULL | UTC. Signup 24 h, reset 1 h |
| consumed_at / invalidated_at | TIMESTAMP | NULL | Set once on use / when superseded or revoked |

A token is valid only while both are NULL and `expires_at` is in the future. Consuming is one conditional UPDATE inside the caller's transaction, so concurrent requests cannot both succeed.

### audit_events
**Purpose:** Audit trail of opportunity assignment changes (`action = "assign"`) and deletions (`"delete"`).

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK | |
| board_id, card_id, actor_user_id | INTEGER | no foreign keys | Deliberately unconstrained so rows outlive the card and the actor |
| action | VARCHAR(20) | NOT NULL | `assign` or `delete` |
| details | TEXT | NOT NULL | JSON: assignment `{title, from_id, from, to_id, to}`; deletion `{title, column, salesperson_id}` |
| created_at | TIMESTAMP | NOT NULL | UTC |

No rows exist for events before the migration; none are reconstructed.

### boards
**Purpose:** Kanban boards, shared across all authenticated users

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK, AUTOINCREMENT | Unique identifier |
| user_id | INTEGER | FK → users, NOT NULL | Creator (metadata only) |
| title | TEXT | NOT NULL | Board name |
| description | TEXT | - | Board description |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Creation time |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Last modified |

**Indexes:**
- `user_id` - Attribute a board to its creator
- `created_at` - List boards by date

**Current:** Boards are shared - `user_id` records who created a board but is never used to restrict who can view/edit it; every authenticated user (admin or member) has full access to every board.
**Future:** Per-board membership/permissions if boards stop being globally shared.

### columns
**Purpose:** Sales stages of a board (1st Contact, F2F Meeting, RFI, Proposal, Award, Lost)

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK, AUTOINCREMENT | Unique identifier |
| board_id | INTEGER | FK → boards, NOT NULL | Parent board |
| title | TEXT | NOT NULL | Column name |
| position | INTEGER | NOT NULL, DEFAULT 0 | Order in board (0, 1, 2, ...) |
| stage_kind | VARCHAR | NOT NULL, DEFAULT 'open' | Commercial meaning: `open`, `won` or `lost` |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Creation time |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Last modified |

**Indexes:**
- `board_id` - Find all columns for a board
- `(board_id, position)` - Get columns in order

**Notes:**
- Position field allows reordering columns without full reconstruction
- **`stage_kind`, not `title`, decides whether a lead is won, lost or open** in reports and in the card "overdue" badge. Titles are display text; renaming never changes the kind.
- Rules enforced by the API (admin-only routes): values are limited to `open`/`won`/`lost`; at most one `won` and one `lost` stage per board; a column's kind can't change while it holds cards (move them out first), so opportunities are never reclassified silently.
- New boards are seeded with Award = `won`, Lost = `lost`, the rest `open` (`lead_options.PIPELINE_STAGES`).

#### Migrating an existing database to `stage_kind`

Databases created before Part 17 have no `columns.stage_kind`. The column is **not** added automatically, because it decides which opportunities count as won or lost. Until it's migrated, the app refuses to start with `StageKindMigrationRequired`.

From `backend/`:
```bash
cp pm.db pm.db.bak-$(date +%Y%m%d)              # extra manual backup (optional; --apply also makes one)
uv run python migrate_stage_kind.py              # dry run: read-only, prints the classification
uv run python migrate_stage_kind.py --apply      # backup + migrate in one transaction
```

**Classification.** The rule is exactly the one the reports used before Part 17, so no KPI changes at migration time:
- A column titled exactly `Award` becomes `won`.
- A column titled exactly `Lost` becomes `lost`.
- Every other column becomes `open`.

**Ambiguity.** If a board has two or more columns titled exactly `Award` (or exactly `Lost`), none of them is classified automatically. The run stops, and the operator chooses with `--set COLUMN_ID=KIND`, for example `--set 12=won --set 14=open`.

**Look-alikes.** Titles such as `award`, ` Lost `, `Won` or `Perdido` were counted as open by the old reports, so they stay `open`. They are listed as warnings so they can be reclassified on purpose with `--set`.

**What changes.**
- One column is added: `ALTER TABLE columns ADD COLUMN stage_kind ... DEFAULT 'open'`.
- `UPDATE`s set the won and lost columns.
- No card, column, user or association is created, deleted or moved.

**Safety.** The run checks the column count and every card's `column_id` before committing, and rolls back if anything differs. It refuses to run twice.

**Recovery.** Stop the app and copy the automatic backup (`<name>-pre-stage-kind-<time>.db.bak`, ignored by git) back over `pm.db`. The previous code version runs on it unchanged.

**Development database dry run (2026-10-13).** Board 1 has one exact `Award` (column 5) and one exact `Lost` (column 6), with no duplicates and no look-alikes. The migration would classify them `won` and `lost`, and the 4 other stages `open`. Applied by the operator on 2026-10-13.

### cards
**Purpose:** Sales leads - one card per lead; the card's column is its sales stage

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK, AUTOINCREMENT | Unique identifier |
| column_id | INTEGER | FK → columns, NOT NULL | Parent column |
| title | TEXT | NOT NULL | Card title/task name |
| details | TEXT | - | Description/notes |
| position | INTEGER | NOT NULL, DEFAULT 0 | Order within column |
| company | TEXT | - | Lead's company |
| contact | TEXT | - | Contact person |
| deal_value | REAL | - | Deal value (>= 0), in `currency` |
| currency | VARCHAR(3) | - | Legacy. Deal Value is always USD: only `USD` is accepted on write (and set whenever an amount is saved), reads always report `USD`, and older codes left in existing rows are ignored. No migration rewrites them |
| expected_close_date | DATE | - | Expected close date |
| lead_source | TEXT | - | Website, Referral, Trade show, Cold outreach, Existing customer, Other |
| product_line | TEXT | - | Chargers or Power supplies |
| salesperson_id | INTEGER | FK → users, ON DELETE SET NULL | Assigned salesperson |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Creation time |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Last modified |

**Indexes:**
- `column_id` - Find all cards in a column
- `(column_id, position)` - Get cards in order
- `created_at` - Query recent cards

**Notes:**
- Position allows drag-and-drop reordering
- Deleting a column cascades delete to all its cards
- Allowed lead values and the stages (1st Contact, F2F Meeting, RFI, Proposal, Award, Lost) live in `backend/lead_options.py`; they're validated by the API, not the database. "Award" = won, "Lost" = lost, everything else open.
- The lead columns were added after the first release. `init_db()` does **not** add them to an older `cards` table: it validates the schema before any write (`require_complete_schema`) and refuses a database that lacks tables or columns the models expect, raising `DatabaseSchemaIncomplete` and listing what is missing (`create_all` never alters existing tables). Such a database needs an explicit migration before it can be initialized. No migration script for the lead columns exists yet, so there is no documented procedure for it; until one is written and validated, restore a complete database or a backup instead of pointing the app at the old file.
- Report totals are USD (the only Deal Value currency); legacy currency codes are ignored and amounts are never converted.
- `has_member_edits` in the API response is not a stored column - it's derived from whether `card_edit_history` has any rows for this card

### conversation_messages
**Purpose:** Store AI chatbot conversation history

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK, AUTOINCREMENT | Unique identifier |
| board_id | INTEGER | FK → boards, NOT NULL | Associated board |
| user_id | INTEGER | FK → users, NOT NULL | Message author |
| role | TEXT | CHECK(...IN ('user', 'assistant')) | user or assistant |
| content | TEXT | NOT NULL | Message text |
| metadata | TEXT (JSON) | - | tokens, model, etc. |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Message time |

**Indexes:**
- `board_id` - Get all messages for a board
- `user_id` - Get all messages from a user
- `(board_id, created_at)` - Get messages in order
- `created_at` - Query recent messages

**Notes:**
- Stores full conversation history for AI context
- Part 9 will query this to provide context to LLM
- Metadata stores OpenRouter usage info (tokens, cost, etc.)

### card_edit_history
**Purpose:** Audit trail of card content edits made by member users (backs the "M" badge and its history modal in the UI). Distinct from `card_actions` below, which is AI-only.

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK, AUTOINCREMENT | Unique identifier |
| card_id | INTEGER | FK → cards, NOT NULL, ON DELETE CASCADE | Edited card |
| user_id | INTEGER | FK → users (nullable), ON DELETE SET NULL | Member who made the edit |
| changes | TEXT (JSON) | NOT NULL | `{"title": {"old", "new"}, "details": {"old", "new"}}` - only changed fields |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Edit time |

**Indexes:**
- `card_id` - Get a card's edit history (newest first)
- `user_id` - Get all edits by a user
- `created_at` - Timeline of changes

**Notes:**
- Only written for edits made by `member` users - admin edits are not tracked here (`routes.update_card`)
- A card's `has_member_edits` flag is `true` whenever it has at least one row here; it stays `true` permanently, even if an admin edits the card afterwards
- `user_id` is `SET NULL` rather than cascading, so history survives if the editing user is later removed
- Changes made by a Salcomp Bid import are **never** written here (they would turn on the "M" badge, i.e. pass for a member edit): they go to `card_import_changes` (Part 22D), and this table's structure and meaning are unchanged

### card_stage_events
**Purpose:** Append-only history of each card's stage (Part 18), read by the KPI engine (`backend/kpis.py`, Part 19).

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK | Unique, also the tie-break for events in the same instant |
| card_id | INTEGER | NOT NULL, indexed, **no FK** | The card; survives the card's deletion |
| board_id | INTEGER | NOT NULL, indexed, no FK | Board the card was on |
| event_type | VARCHAR | NOT NULL | `created`, `moved`, `won`, `lost`, `reopened`, `deleted` or `baseline` |
| from_column_id / from_stage_kind | INTEGER / VARCHAR | nullable | Stage before (empty for `created`/`baseline`) |
| to_column_id / to_stage_kind | INTEGER / VARCHAR | nullable | Stage after (empty for `deleted`) |
| user_id | INTEGER | FK → users, ON DELETE SET NULL, nullable | Who acted (empty for `system`/`migration`) |
| source | VARCHAR | NOT NULL | `api` (REST: UI or API client), `ai`, `undo`, `seed`, `migration`, `import` (Salcomp Bid import, Part 22D), `system` |
| occurred_at | DATETIME | NOT NULL, indexed | When (UTC) |
| deal_value_snapshot | FLOAT | nullable | Card's `deal_value` at the event (Part 20); an exact copy of `cards.deal_value`, which is also FLOAT |
| deal_currency_snapshot | VARCHAR(3) | nullable | Card's `currency` at the event |
| value_snapshot_taken | BOOLEAN | NOT NULL, DEFAULT 0 | 1 = recorded at event time (a null value means the card had none); 0 = event predates snapshots, value unknown |

**Event semantics.** The type is decided by `stage_kind`, never by titles:
- `won`: the card enters the won stage from any other stage.
- `lost`: the card enters the lost stage from any other stage. This includes won → lost.
- `reopened`: the card goes from won or lost back to an open stage.
- `moved`: open → open.
- `created`: a card is inserted.
- `deleted`: a card is removed.
- `baseline`: written by the migration (see below) for cards that existed when tracking started, and by the Salcomp Bid import for each card it creates (source `import`, dated at the import, never at a source date). The listener writes it instead of `created` while the session's source is `import`: importing a Bid is not when the opportunity was created, so it never counts in a creation cohort, and a card imported into Award/Lost is not a win/loss.

A reorder within the same column, or an edit of title, details or lead fields, records **nothing**.

**How it's written.** Only `backend/stage_history.py` writes this table, through a SQLAlchemy `after_flush` listener. It inserts the events on the same connection, in the same transaction as the card change: either both commit or both roll back. Every path goes through it with no per-route code, so a change can't be missed or recorded twice:
- REST create, move and delete;
- AI actions;
- undo;
- column and board deletion cascading to their cards;
- seeding.

Entry points only set who acted and the source (`set_stage_context`). REST routes get it from `deps.get_current_user`.

**Card ids are never reused.** `cards` has no `AUTOINCREMENT`, so SQLite would hand a deleted top card's id to the next new card. A `before_flush` listener therefore allocates new ids above both `max(cards.id)` and `max(card_stage_events.card_id)`.

**Deletion.** Card deletion is unchanged: it is still a hard delete. History stays readable through `card_id` and ends with a `deleted` event, which is why `card_id`, `board_id` and the column ids are not foreign keys, and why `Card` has no relationship to this table. Stage kinds are copied at event time, so the history keeps its meaning after a column is deleted.

**Undo.** Undo never edits or removes events; it adds compensating ones:
- an undone move is a new event with `source` = `undo`;
- undoing a create records `deleted`;
- undoing a delete records `created` for the re-created card, which has a new id;
- undoing an edit records nothing.

**Value snapshots (Part 20).** Every event, of every type, copies the card's value and currency in the same transaction, with `value_snapshot_taken` = 1. Historical money (won/lost KPIs) comes only from these snapshots, never from the card's current value. Events written before Part 20 keep `value_snapshot_taken` = 0 and no value: their historical value is unknown and is never back-filled. The Part 18 migration, when run on a new database, writes its baselines with a snapshot, because their timestamp is the migration instant.

**Query.** `stage_history.card_stage_history(db, card_id)` returns a card's events ordered by `occurred_at`, then `id`. There is no API endpoint or UI for it yet.

#### Migrating an existing database to `card_stage_events`

Databases created before Part 18 have no `card_stage_events` table. It is **not** created implicitly: `init_db()` checks for it before `create_all`, and refuses to start with `StageEventsMigrationRequired`. A brand-new database gets every table normally. The `stage_kind` migration (Part 17) must be applied first.

From `backend/`, with the app stopped:
```bash
cp pm.db "pm-manual-$(date +%Y%m%d%H%M%S).db.bak"                    # optional extra backup
uv run python migrate_stage_events.py --database "$PWD/pm.db"           # dry run, read-only
uv run python migrate_stage_events.py --database "$PWD/pm.db" --apply   # backup + migrate
```

**What `--apply` does, in one transaction:**
1. Creates the table and indexes. The DDL is generated from `models.CardStageEvent`, so it can't drift from the app.
2. Writes one `baseline` event per existing card, with:
   - the card's current column and `stage_kind`;
   - `source` = `migration`;
   - no user;
   - `occurred_at` = the migration time.
3. Checks that users, boards, columns, cards and card→column links are unchanged, and that every card got exactly one baseline. Otherwise it rolls everything back.

**No invented history.** A `baseline` is a reference point, not an observed move. Nothing is back-filled: no `created`, `won` or `lost` events for the past, and no historical dates. Cards whose column is missing get no baseline and are reported.

**Safety and recovery.**
- Refuses to run twice, and refuses before the `stage_kind` migration.
- Takes a backup first: `<name>-pre-stage-events-<time>.db.bak`, ignored by git.
- To recover, stop the app and copy the backup over `pm.db`. The current code will then refuse the database again until it's re-migrated; the code from before Part 18 runs on it unchanged.

**Development database dry run (2026-10-13).** 7 cards would get 7 baselines (5 open, 1 won, 1 lost). Applied by the operator on 2026-10-13.

#### Adding value snapshots to an existing `card_stage_events` (Part 20)

Databases migrated with Part 18 before Part 20 have the table without the three value columns. The app refuses to start (`StageEventValuesMigrationRequired`) until they are added explicitly. From `backend/`, with the app stopped:
```bash
cp pm.db "pm-manual-$(date +%Y%m%d%H%M%S).db.bak"                             # optional extra backup
uv run python migrate_stage_event_values.py --database "$PWD/pm.db"            # dry run, read-only
uv run python migrate_stage_event_values.py --database "$PWD/pm.db" --apply    # backup + migrate
```

**What `--apply` does, in one transaction:**
1. Adds the 3 columns. The types are generated from the model: `FLOAT`, `VARCHAR(3)`, and `BOOLEAN NOT NULL DEFAULT 0`.
2. Leaves every existing event at `value_snapshot_taken` = 0 with null values. What those events were worth is unknown, so nothing is back-filled, and the current value is not used.
3. Checks that row counts of users, boards, columns, cards and events are unchanged, that every existing event row is identical, and that no event claims a snapshot. Otherwise it rolls back.

**Safety and recovery.**
- Refuses to run twice, and refuses before the Part 18 table exists.
- Takes a backup first: `<name>-pre-stage-event-values-<time>.db.bak`, ignored by git.
- To recover, stop the app and copy the backup over `pm.db`.

**Development database dry run (2026-10-13).** 7 existing events (7 baselines) would be marked "value unknown". Applied by the operator on 2026-10-13. The same apply on a temporary copy preserved all 7 events, 7 cards and 15 users, with no value invented.

### undo_entries
**Purpose:** Each user's single level of undo - the inverse of their last board change

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK, AUTOINCREMENT | Unique identifier |
| user_id | INTEGER | FK → users, NOT NULL, UNIQUE, ON DELETE CASCADE | Owner (one row per user) |
| board_id | INTEGER | FK → boards, NOT NULL, ON DELETE CASCADE | Board the change was made on |
| description | TEXT | NOT NULL | Shown next to the Undo button, e.g. `Move "X" back to RFI` |
| steps | TEXT | NOT NULL | JSON list of inverse operations (`move`/`update`/`delete`/`create`, see `backend/undo.py`), applied in reverse. Card steps carry `expect`: the card's state as the change left it |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | When the change was made |

**Notes:**
- Replaced by the user's next change; deleted once undone (or once found impossible to undo).
- **Undo only applies if nothing changed since.** Before applying anything, undo compares every card with its steps' `expect` (merged per card, so several edits in one AI request compare with the final state). If another user, the AI or a Bid import changed those values (or the card's column) since, undo is refused with HTTP 409 and the later change is kept. This is a later change, not a structural one: no schema change was needed, because the steps are JSON. Entries recorded before `expect` existed can't be checked, so they are refused too (at most one per user, once).

### card_actions
**Purpose:** Audit trail of AI-suggested card changes

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK, AUTOINCREMENT | Unique identifier |
| board_id | INTEGER | FK → boards, NOT NULL | Associated board |
| message_id | INTEGER | FK → messages | AI message that created this |
| action_type | TEXT | CHECK(...IN ('create', ...)) | create/update/move/delete |
| card_id | INTEGER | FK → cards (nullable) | Affected card |
| changes | TEXT (JSON) | - | Before/after values |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Action time |

**Indexes:**
- `board_id` - Get all actions for a board
- `message_id` - Get actions from a message
- `card_id` - Get all actions affecting a card
- `created_at` - Timeline of changes

**Notes:**
- Enables full audit trail of AI suggestions
- Future: Could implement undo/redo using this table
- Helps debug AI decision-making

### Salcomp Bid import tables (Part 22)

Four tables for importing the Salcomp Bids report (`relatorio-leads.xlsx`). The Excel reader (22B) and the preview (22C) only read. `bid_import_service.py` (22D, below) is the only code that writes these tables. The admin-only endpoints (`backend/import_routes.py`, Part 22E) call the service; the UI is planned (see `docs/PLAN.md`, Part 22). The models live in `backend/models.py`; `backend/import_schema.py` derives the exact expected schema from them, and both the migration and `init_db()` check databases against it.

#### import_batches
**Purpose:** One attempted import (metadata only).

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK | Unique identifier |
| board_id | INTEGER | FK → boards, NOT NULL, ON DELETE CASCADE | Board imported into |
| user_id | INTEGER | FK → users, ON DELETE SET NULL | Who ran the import |
| filename | VARCHAR | NOT NULL | Uploaded file name |
| file_sha256 | VARCHAR(64) | NOT NULL | SHA-256 of the file |
| mapping_json | TEXT | - | Mapping used (JSON) |
| created_count, updated_count, skipped_count, review_count | INTEGER | NOT NULL, DEFAULT 0 | Row outcomes |
| status | VARCHAR | NOT NULL, DEFAULT 'pending' | `pending` / `completed` / `failed` |
| created_at, updated_at | DATETIME | NOT NULL | Timestamps |
| completed_at | DATETIME | - | When it finished |

**Indexes:** `id`, `board_id`, `user_id`, `file_sha256`, `created_at`.

#### card_bid_details
**Purpose:** The Bid report's commercial fields, one row per card.

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK | Unique identifier |
| card_id | INTEGER | FK → cards, NOT NULL, UNIQUE, ON DELETE CASCADE | The card (1:1) |
| plant, customer_type, business_type, product_category, product, solution_type | VARCHAR | - | Classification fields from the report |
| confidence | INTEGER | - | Confidence |
| volume_current_year, volume_next_year | INTEGER | - | Volumes |
| production_start_date | DATE | - | Start of production |
| revenue_current_year_cents, revenue_next_year_cents | INTEGER | - | Annual revenue **in cents** of `revenue_currency` |
| revenue_currency | VARCHAR(3) | NOT NULL, DEFAULT 'BRL' | Currency of both revenue columns |
| status_note, next_step, close_reason_note | TEXT | - | Free-text notes |
| project_lead, cpm, close_reason | VARCHAR | - | Project lead and CPM (source text as written, never users), close reason |
| next_step_due | DATE | - | Next step due date |
| created_at, updated_at | DATETIME | NOT NULL | Timestamps |

**Indexes:** `card_id` (UNIQUE), `id`, `created_at`.

**Revenue in cents.** SQLite has no exact decimal type: a `NUMERIC(18, 2)` column (the first draft) silently rounds past about 15 significant digits (`12345678901234567.89` came back as `12345678901234568`). Revenue is therefore an exact 64-bit INTEGER number of cents:
- Convert only with `backend/bid_money.py`. `reais_to_cents()` takes a `Decimal`, an `int` or a plain decimal string, never a float. It rejects (never rounds) more than two decimal places, negative values, NaN/Infinity, text that is not a plain decimal number (`"1.234,56"`, `"R$ 10"`) and anything above `MAX_BID_REVENUE_CENTS` (R$ 9,999,999,999,999,999.99).
- `cents_to_reais()` turns cents back into a `Decimal` with two places. Divide by 100 only for display.
- `NULL` means "not informed", in both directions, and is never stored as 0; a real zero is `0`.
- This is annual revenue from the report. It is not `cards.deal_value`, it is never copied into it or summed with it, and reports/KPIs don't read it.

#### card_source_refs
**Purpose:** Where an imported card came from, so a later import can find it again.

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK | Unique identifier |
| card_id | INTEGER | FK → cards, NOT NULL, UNIQUE, ON DELETE CASCADE | The card (at most one source ref) |
| source_system | VARCHAR | NOT NULL, DEFAULT 'salcomp-bids' | Source system |
| source_key | VARCHAR | NOT NULL; UNIQUE with source_system | Identity within the source |
| source_code | VARCHAR | NOT NULL | Codigo, as text (keeps leading zeros) |
| source_status, source_stage | VARCHAR | - | Status/stage as written in the source |
| source_confidence | VARCHAR | - | Confidence label as written, e.g. `LOW (10%)` (the percentage is `card_bid_details.confidence`) |
| source_created_at, source_updated_at, source_last_edited_at | DATETIME | - | Source timestamps |
| last_import_batch_id | INTEGER | FK → import_batches, ON DELETE SET NULL | Last batch that touched it |
| source_row_hash | VARCHAR(64) | - | Hash of the source row |
| created_at, updated_at | DATETIME | NOT NULL | Timestamps |

**Indexes:** `uq_card_source_refs_system_key` UNIQUE (`source_system`, `source_key`), `card_id` (UNIQUE), `id`, `source_system`, `last_import_batch_id`, `created_at`.

Uniqueness is enforced by the database, not only by code. For Bids, `source_key` is `bid_source_key(Código, Cliente)` in `backend/bid_import_preview.py` (Part 22C), because Código alone repeats in the report:
- **Format:** `v1:` followed by the JSON array of the two parts, e.g. `v1:["000123-01","cliente sao paulo"]`.
- **Each part:** accents dropped (NFKD without combining marks), whitespace collapsed, casefolded. Leading zeros are kept.
- **No collisions:** the JSON encoding means no separator inside a value can make two pairs collide.

`source_row_hash` is `bid_row_hash(record)`: `v1:` + SHA-256 of every value read from the row except its position. The preview uses it to tell unchanged re-imports from changed ones.

#### Bid report columns (reader, Part 22B)

`backend/bid_excel_reader.py` reads the report and maps each column to a field of its `BidRecord`. **It writes nothing:** these tables stay empty until the import step exists. Field names reuse the `card_bid_details` / `card_source_refs` column names where the meaning matches. The import step will decide what is actually stored.

| # | Header | Reader field | Kind | Rules |
|---|--------|--------------|------|-------|
| 1 | Código | code | code | **Required value.** Text kept as written (leading zeros included). A numeric cell is zero-padded only with an all-zeros format; otherwise it is flagged `code_needs_review`. Not unique in the report. |
| 2 | Ano | year | year | Whole number, 1900-2100 |
| 3 | CAT | cat | text | **Required value.** Kept as written (not mapped) |
| 4 | Atualizado em | source_updated_on | date | `dd/mm/yyyy` or an Excel date |
| 5 | Estágio | stage | text | **Required value.** Kept as written (not mapped) |
| 6 | Planta | plant | text | |
| 7 | Cliente | customer | text | **Required value** |
| 8 | Tipo de cliente | customer_type | text | |
| 9 | Tipo de negócio | business_type | text | |
| 10 | Categoria de Produto | product_category | text | |
| 11 | Produto | product | text | Numbers become text |
| 12 | Solução | solution_type | text | |
| 13 | Nome do projeto | project_name | text | **Required column** |
| 14 | Descrição da prospecção | prospect_description | text | |
| 15 | Nível de Confiança | confidence_label + confidence_percent | confidence | `"LOW (10%)"` -> 10 |
| 16 | Volume (ano atual) | volume_current_year | volume | Whole number >= 0; 1 is kept with a warning |
| 17 | Volume (próximo ano) | volume_next_year | volume | Same |
| 18 | Data de MP | production_start_date | date | |
| 19 | Revenue BRL (ano atual) | revenue_current_year_cents | revenue | Decimal -> exact cents (BRL) via `bid_money.py` |
| 20 | Revenue BRL (próximo ano) | revenue_next_year_cents | revenue | Same |
| 21 | Descrição do status | status_note | text | |
| 22 | Budget habilitado | budget_enabled | text | Kept as written |
| 23 | Budget | budget | text | Kept as written (no currency stated) |
| 24 | CPM | cpm | text | Kept verbatim in `card_bid_details.cpm`; never turned into a user or salesperson |
| 25 | Líder do projeto | project_lead | text | A name; never turned into a user |
| 26 | Próximo passo | next_step | text | |
| 27 | Deadline | next_step_due | date | Not an expected close date |
| 28 | Razão | close_reason_note | text | |
| 29 | Status da conclusão | close_reason | text | |
| 30 | Data de criação | source_created_on | date | Not the card's creation date |
| 31 | Última edição | source_last_edited_on | date | |

**Rules for every column:**
- **Absent values:** `-` and blank mean "absent" (None), never 0.
- **Header matching:** case-insensitive, with whitespace collapsed.
- **Required columns:** missing one of the five (`Código`, `CAT`, `Estágio`, `Cliente`, `Nome do projeto`) is a file error. A missing optional column is a warning.
- **Severities:** see `docs/PLAN.md` Part 22B for file errors, row errors and warnings, and for the file safety checks.

#### card_import_changes
**Purpose:** What an import actually changed on an existing card (Part 22D follow-up). Import history, kept apart from `card_edit_history`.

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK | Unique identifier |
| card_id | INTEGER | FK → cards, NOT NULL, ON DELETE CASCADE | The updated card |
| import_batch_id | INTEGER | FK → import_batches, NOT NULL, ON DELETE CASCADE | The batch that changed it |
| user_id | INTEGER | FK → users, ON DELETE SET NULL | Who ran the import (the batch's user) |
| changes | TEXT (JSON) | NOT NULL | `{"card.title": {"old", "new"}, "bid_details.next_step": {...}, "source_ref.source_status": {...}}`. Only fields that really changed; dates as ISO text |
| created_at | DATETIME | NOT NULL | When the change was written |

**Indexes:** `uq_card_import_changes_card_batch` UNIQUE (`card_id`, `import_batch_id`), plus `card_id`, `import_batch_id`, `user_id`, `created_at`.

**What is written:**
- **Only a confirmed UPDATE** that changed at least one managed field gets a row: one row per card and batch, enforced by the database.
- **Never** for created cards (they get a `baseline` event and a source ref), unchanged re-imports, older sources, skipped rows, pending reviews or unconfirmed updates.
- **Old and new values** are read from the database objects themselves before and after the update. The history shows what really changed, including a value someone had edited in the app since the last import.
- **Not recorded here:** the row hash, unchanged fields, unmapped fields and the file. A confirmed move appears in `card_stage_events`, not here.
- **Same transaction:** it is written with the update and rolls back with it.

**Personal data:** values such as the customer or the project lead's name appear only when that field changed, as needed for the audit.

**Deletion:** like `card_edit_history`, the rows go with their card. They also go with their batch, which is only deleted with its board.

#### Import mapping (preview, Part 22C)

`backend/bid_import_preview.py` proposes, without writing, what each row would become. The stage rules, actions and decisions are in `docs/PLAN.md` Part 22C. Model column <- report column:
- **cards:** `title` <- Nome do projeto, `details` <- Descrição da prospecção, `company` <- Cliente. Nothing else is set: no `deal_value`, `currency`, `expected_close_date`, `lead_source`, `product_line`, `contact` or `salesperson_id`. The card's own `created_at` is never Data de criação.
- **card_bid_details:**

  | Column | Report column |
  |--------|---------------|
  | plant | Planta |
  | customer_type | Tipo de cliente |
  | business_type | Tipo de negócio |
  | product_category | Categoria de Produto |
  | product | Produto |
  | solution_type | Solução |
  | confidence | Nível de Confiança, as a percentage |
  | volume_current_year, volume_next_year | the two Volume columns |
  | production_start_date | Data de MP |
  | revenue_current_year_cents, revenue_next_year_cents | the two Revenue BRL columns, in cents |
  | revenue_currency | always BRL |
  | status_note | Descrição do status |
  | project_lead | Líder do projeto (text) |
  | cpm | CPM (text, verbatim; no business definition assumed) |
  | next_step | Próximo passo |
  | next_step_due | Deadline |
  | close_reason | Status da conclusão |
  | close_reason_note | Razão |
- **card_source_refs:**

  | Column | Value |
  |--------|-------|
  | source_system | `salcomp-bids` |
  | source_key | `bid_source_key(Código, Cliente)` |
  | source_code | Código |
  | source_status | CAT, as written |
  | source_stage | Estágio, as written |
  | source_confidence | Nível de Confiança, as written |
  | source_created_at | Data de criação |
  | source_updated_at | Atualizado em |
  | source_last_edited_at | Última edição |
  | source_row_hash | `bid_row_hash(record)` |
- **Not mapped** (shown in the preview, never imported): Ano, Budget habilitado, Budget. CPM and Líder do projeto are stored as text and never matched to users. API responses never include the CPM name.

#### Applying an import (Part 22D)

`backend/bid_import_service.py` writes a confirmed preview. The HTTP endpoints (Part 22E, below) call it; they never write these tables themselves.
- **`preview_bid_import(db, read_result, board_id, decisions)`** builds the preview from the board's columns and the already-imported cards (`load_board_stages`, `load_existing_imports`). It is read-only.
- **`apply_bid_import(db, read_result, preview, decisions, board_id, user)`** does everything in one transaction on the caller's session:
  1. inserts the `import_batches` row (`pending`), which takes SQLite's write lock;
  2. rebuilds the preview from the database with the same decisions and refuses (`ImportConflict`) unless it equals the confirmed one. This covers concurrent imports, data edited since the preview and a repeated confirmation;
  3. refuses (`ImportRejected`) decisions that don't fit their rows;
  4. writes the authorized rows, flushes, sets the counts and `completed`, and commits.

  **On failure:** any exception rolls back the whole batch. A unique-key violation on `card_source_refs` becomes `ImportConflict`.
- **Create:** a card in the decided column, appended at the end, with `title`/`details`/`company`, plus `card_bid_details` and `card_source_refs` (`last_import_batch_id` = the batch). The listener records a `baseline` event, source `import`.
- **Update** (only when `confirm_update` was given): overwrites only the mapped card / Bid / source-ref fields. `deal_value`, `currency`, `contact`, `expected_close_date`, `lead_source`, `product_line`, `salesperson_id`, the position and the column are untouched. The card moves only with `confirm_move`, which records a real `moved`/`won`/`lost` event, dated now, source `import`.
- **Counts:**
  - IGNORE rows -> `skipped_count`;
  - REVIEW rows and updates awaiting confirmation -> `review_count`;
  - unchanged rows are never counted as updated.
- **`mapping_json`** holds the rule versions, the stage rules, the board's stages and the decisions. It holds no row content, and the file itself is not stored.
- **Failed attempts:** any failure of `apply_bid_import` rolls back the whole import. Then, in a separate short transaction on its own session, the service records a batch with `status = failed`:
  - **`failure_kind`:** `validation` (`ImportRejected`), `conflict` (`ImportConflict`) or `unexpected` (anything else);
  - **`failure_reason`:** at most 500 characters. For validation/conflict it is the service's own message (row numbers and codes, never cell content); for unexpected failures, only the exception type, since its message may quote SQL parameters;
  - **other fields:** user, board, file name and SHA-256, and the mapping. Counts stay 0.

  **Guarantees:**
  - commercial data is never partial;
  - a failure to record the attempt is only logged (by exception type) and never replaces or hides the original error;
  - a completed import is never recorded as failed;
  - no record is possible for a board that doesn't exist.

  **Limitation:** recording reuses the caller's engine. On a single-connection test engine (in-memory `StaticPool`) it shares that connection.
- **A Bid already imported into another board:** `source_system` + `source_key` is unique across the whole database, so a Bid lives on one board only.
  - **Preview:** `preview_bid_import` raises `ImportConflict`, naming the Códigos and the board they're on.
  - **Apply:** at apply time (for example, if the other board imported it after the preview) the whole import is rolled back, nothing is written, and the attempt is recorded as `conflict`.
  - **To proceed:** import into that board, or remove those rows from the file.

#### Adding the import tables to an existing database (Part 22)

A brand-new database gets the three tables from `create_all`. An existing one gets them only from the explicit migration. `init_db()` checks before `create_all` and refuses to start in three cases:
- **None of the tables:** `ImportTablesMigrationRequired`, telling you to run the migration.
- **Only some of them** (an interrupted or manual change): `ImportTablesSchemaError` ("partly migrated").
- **All three, but different from the models** (a column, type, NOT NULL, default, index or foreign key rule): `ImportTablesSchemaError`, listing every difference.

Nothing is ever repaired automatically: restore the backup the migration took.

From `backend/`, with the app stopped. `--database` is required; there is no `DATABASE_URL` or `./pm.db` fallback:
```bash
uv run python migrate_import_tables.py --database "$PWD/pm.db"            # dry run, read-only
uv run python migrate_import_tables.py --database "$PWD/pm.db" --apply    # backup + migrate
```

**Refused, with exit 1, nothing written and no backup:**
- **Out of order:** the earlier migrations must already be applied: `columns.stage_kind` (`migrate_stage_kind.py`), `card_stage_events` (`migrate_stage_events.py`) and its value columns (`migrate_stage_event_values.py`). The error names the script to run.
- **Partial or incompatible** import tables.
- **A second `--apply`.** A dry run on a migrated database prints "nothing to do" and exits 0.
- **A file that doesn't exist** (it is not created) or isn't a Sales Pipeline database.

**What `--apply` does:**
1. Writes `<name>-pre-import-tables-<time>.db.bak` next to the database (ignored by git).
2. In one transaction:
   - re-checks that the tables are still absent;
   - creates the three tables and their indexes, with the DDL generated from the models (identical to `create_all`'s);
   - verifies the result with the same check `init_db()` uses;
   - verifies that every row of every pre-existing table is unchanged;
   - rolls back on any failure.
3. Leaves the new tables empty. No user, board, card or history row is created or changed.

#### Import revert tables (Part 23, schema only)

Two additive tables prepare the revert of a confirmed Bid import. This step only creates the schema; nothing writes to them yet.

- `import_batch_ledgers`: one row per batch (`import_batch_id` UNIQUE, FK to `import_batches` ON DELETE CASCADE). Its existence makes a batch revertible; batches imported before the migration have none and stay not revertible. Holds `ledger_version`, `created_at`, the revert claim (`reverted_at`, `reverted_by_user_id` FK to `users` ON DELETE SET NULL) and the final result (`revert_outcome`, `no_commercial_changes`, `cards_deleted_count`, `cards_restored_count`, `moves_reverted_count`, `already_deleted_count`, `result_summary_json`: counts only, no cell content).
- `import_batch_items`: one row per card a batch created, updated or observed (`ledger_id` FK ON DELETE CASCADE; `UNIQUE(ledger_id, card_id)`; index on `card_id`). `card_id` and `board_id` are plain integers with no foreign key, so an item outlives its card. Holds `source_key`, `action` (`created`/`updated`/`observed`), `observation`, `before_json`, `after_json`, `bid_details_created`, `stage_event_max_id_after`.

The Part 22 tables are unchanged. `import_schema.py` checks this group separately (`revert_schema_state`), so a Part 22 database is not reported as incompatible. A brand-new database (including `create_commercial_db.py`) gets both tables from `create_all`. For an existing one, `init_db()` raises `ImportRevertMigrationRequired` (none of the two) or `ImportRevertSchemaError` (partial or different from the models) before `create_all`; the Part 22 guard runs first.

From `backend/`, with the app stopped (same rules as `migrate_import_tables.py`: dry run by default, `--database` required, backup `<name>-pre-import-revert-<time>.db.bak`, one transaction with row-preservation check, refuses a second `--apply`, refuses a database without the earlier migrations or with Part 22 tables missing or different):
```bash
uv run python migrate_import_revert.py --database "$PWD/pm.db"            # dry run, read-only
uv run python migrate_import_revert.py --database "$PWD/pm.db" --apply    # backup + migrate
```

#### Annual Deal Value: `cards.deal_value_next_year`, `cards.deal_year` (migration `migrate_deal_years.py`)

`cards.deal_value` is unchanged and now means the amount for `deal_year` (the "current year"); `deal_value_next_year` (FLOAT NOT NULL, default 0) is the amount for `deal_year + 1`. Both are in USD (see `cards.currency`: legacy, ignored; no conversion), and they are separate from Bid revenue (`card_bid_details.revenue_*`). The minimized card shows `deal_value + deal_value_next_year`; the edit form shows both, labelled with the card's own `deal_year` (no year is hard-coded). `deal_year` is stamped when a card is created and cannot be set through the API.

A database without the two columns is refused at startup with `DealYearsMigrationRequired`. The year of the existing amounts can't be inferred, so it is passed explicitly. From `backend/`, app stopped, on a backup first (dry run by default, `--database` absolute, `--apply` needs `--expect-sha256`, refuses `pm.db`, takes `<name>-pre-deal-years-<time>.db.bak`, one transaction, refuses a second `--apply`):

```
uv run python migrate_deal_years.py --database /abs/path/db --year 2026                                   # dry run
uv run python migrate_deal_years.py --database /abs/path/db --year 2026 --expect-sha256 <hash> --apply
```

Every existing `deal_value` stays as is and becomes the amount for `--year`; `deal_value_next_year` is 0 on every card.

Year rollover is explicit, never automatic. Until it is run, stored amounts keep the year they were entered for (the UI labels follow the card's `deal_year`, not the calendar). Once a year has ended (the script refuses otherwise), with the app stopped and a hash approved:

```
uv run python migrate_deal_year_rollover.py --database /abs/path/db --from-year 2026                      # dry run
uv run python migrate_deal_year_rollover.py --database /abs/path/db --from-year 2026 --expect-sha256 <hash> --apply
```

For each card with `deal_year = 2026`: `deal_value` becomes the old `deal_value_next_year` (NULL if that was 0), `deal_value_next_year` becomes 0 and `deal_year` becomes 2027. The old current-year amount is only kept in the backup. Stage-history value snapshots are past events and are not rewritten. Reports and the Dashboard show the current-year, next-year and two-year total measures (docs/KPIS.md); the stage-history closing snapshots stay `deal_value` alone.

#### `card_bid_details.cpm` (migration `migrate_bid_cpm.py`)

A nullable VARCHAR added after the Part 22 tables, for the report's CPM column. A database that has the import tables but not this column is refused at startup with `BidCpmMigrationRequired`; new databases get it from `create_all`. From `backend/`, with the app stopped (dry run by default, `--database` required, backup `<name>-pre-bid-cpm-<time>.db.bak`, one transaction with foreign keys enforced, refuses a second `--apply`, refuses any other difference from the models):
```bash
uv run python migrate_bid_cpm.py --database "$PWD/pm.db"            # dry run, read-only
uv run python migrate_bid_cpm.py --database "$PWD/pm.db" --apply    # backup + migrate
```
It runs one `ALTER TABLE ... ADD COLUMN`, then verifies that every row of every table and every other schema object is unchanged, `foreign_key_check` is clean and the new column is all NULL.

#### HTTP endpoints (Part 22E)

All admin-only (401/403 otherwise), in `backend/import_routes.py`:

| Method and path | Writes | Returns |
|---|---|---|
| `POST /api/boards/{id}/imports/preview` (multipart `file`, `decisions`) | nothing | the classified preview, `confirmable`, `preview_token` |
| `POST /api/boards/{id}/imports` (multipart `file`, `decisions`, `preview_token`, `confirm=true`) | through `apply_bid_import` only | 201: the batch and created/updated/moved card ids |
| `GET /api/boards/{id}/imports?limit&offset` | nothing | the board's `import_batches`, newest first |
| `GET /api/imports/{batch_id}` | nothing | one batch, with its parsed `mapping_json` and `card_changes_count` |

- **The preview token** binds the confirmation to the admin, the board, the file's SHA-256, the decisions, the latest completed batch and the preview shown. The confirmation re-reads the file and rebuilds the preview before calling the service.
- **What is stored:** the uploaded file never is. Only its name and SHA-256 go in `import_batches`.
- **What responses leave out:** batch responses carry no row content, and users appear only as `user_id`/`username`.

**Recovery:** stop the app and copy the backup over the database file.

**Development database.** `backend/pm.db` has not been migrated. Until an operator runs the migration on it, the app refuses to start on it with `ImportTablesMigrationRequired`.

---

## Data Relationships

### Cascade Rules

- **DELETE user** → Cascade delete boards, messages, actions; SET NULL on card_edit_history.user_id (preserve history)
- **DELETE board** → Cascade delete columns, cards, messages, actions
- **DELETE column** → Cascade delete cards
- **DELETE card** → Cascade delete card_edit_history, card_import_changes, card_bid_details and card_source_refs; SET NULL on card_actions.card_id (preserve history); card_stage_events is kept (no FK)
- **DELETE board** also cascade-deletes its import_batches; **DELETE user** sets import_batches.user_id to NULL
- **DELETE import batch** → SET NULL on card_source_refs.last_import_batch_id
- **DELETE message** → SET NULL on card_actions.message_id (preserve history)

This ensures referential integrity while preserving audit history.

SQLite only enforces foreign keys (and these cascades) when `PRAGMA foreign_keys=ON` is set on each connection. `database.enable_sqlite_foreign_keys()` does this from a SQLAlchemy `connect` event, and is applied to the application engine (`database.py`) and to `create_commercial_db.py`. Any new code that builds a SQLite engine must call it; `test_foreign_keys.py` fails if a non-test module calls `create_engine` without it. The standalone `migrate_*.py` scripts use raw `sqlite3` connections and do not enable it.

---

## Example Data Flows

### User Login
```sql
SELECT * FROM users WHERE username = 'alice'
-- accepted only if the password matches AND email_verified_at IS NOT NULL;
-- the token then carries session_version (sv), re-checked on every request
```

### First-run Admin Setup (`POST /api/setup`)
```sql
-- Only allowed while the users table is empty
INSERT INTO users (username, password_hash, role, email_verified_at) VALUES ('alice', '<bcrypt hash>', 'admin', CURRENT_TIMESTAMP)
```

### Load Kanban Board (Part 6)
```sql
SELECT c.*, co.id, co.title as column_title
FROM columns co
LEFT JOIN cards c ON c.column_id = co.id
WHERE co.board_id = 1
ORDER BY co.position, c.position
```

### Get Conversation History (Part 9)
```sql
SELECT * FROM conversation_messages
WHERE board_id = 1
ORDER BY created_at ASC
LIMIT 20  -- Last 20 messages for context
```

### AI Creates Card (Part 9)
```sql
BEGIN TRANSACTION
  INSERT INTO cards (column_id, title, details, position) 
    VALUES (1, 'New Task', 'Details', 0)
  INSERT INTO card_actions (board_id, message_id, action_type, card_id, changes)
    VALUES (1, 42, 'create', LAST_INSERT_ID(), '{...}')
COMMIT
```

---

## Performance Considerations

### Indexing Strategy

1. **Foreign Keys** (board_id, user_id, column_id, message_id)
   - Essential for JOIN operations
   
2. **Timestamps** (created_at, updated_at)
   - For sorting and range queries
   
3. **Composite Indexes** ((board_id, position))
   - Optimize board loading with ordered data
   
4. **Future:** Add indexes based on query patterns
   - Monitor slow queries
   - Add indexes to frequently filtered columns

### Query Optimization

- Use indexes on foreign keys
- Avoid SELECT * (specify needed columns)
- Use LIMIT for pagination
- Archive old conversation messages (future)

### Scalability Notes

- SQLite: Fine for 1 user, ~10k cards
- PostgreSQL: Handles 1M+ users, 100M+ cards
- Consider sharding by user_id for very large scale

---

## Creating the clean commercial database

The commercial database is a separate file from the development `backend/pm.db`, built once by `create_commercial_db.py`. Existing migrations don't apply to it: it is created with the current schema.

```bash
cd backend
SECRET_KEY=<any value, >=32 chars> uv run python create_commercial_db.py \
  --database /absolute/path/pm-commercial.db --email <admin-email>
```

- `--database` is required and must not exist (a file, symlink or directory there is refused, never overwritten); its directory must exist. There is no `DATABASE_URL` or `./pm.db` fallback, and the development database paths are refused.
- The administrator is created **without a usable password** (`password_hash = "!"`, email unverified): there is no password argument, prompt, stdin option or constant. The `--email` (lowercase, also the username) owner requests "Forgot my password?" in the application, which emails a single-use link, sets the password (which must meet the password policy) and verifies the email. This needs working SMTP and the reset flow, so test those before the transition. `e2e_admin` / `e2e_member` are refused as names.
- It creates the full schema (Part 22 and auth tables included), the first admin, the "Salcomp Sales Pipeline" board and the stages from `lead_options.PIPELINE_STAGES` with their `stage_kind` (1st Contact, F2F Meeting, RFI, Proposal = open; Award = won; Lost = lost). No cards, sample leads, import batches or history rows.
- If any step fails the new file is removed. The schema is checked with the same guards the app runs at startup before the admin is created.
- Because the admin exists, `/api/setup` answers 409 and the setup screen is skipped. Further admins: `admin_cli.py`; members: Manage Users or signup links.

Run the app on it by setting `DATABASE_URL` for that process only, leaving `.env` and the development database untouched:

```bash
cd backend
DATABASE_URL=sqlite:////absolute/path/pm-commercial.db uv run uvicorn main:app --port 8000
```

(Four slashes: `sqlite:///` plus an absolute path.) For Docker, `docker-compose.yml` takes the URL from `PM_DATABASE_URL` and the mount from `PM_DB_HOST_PATH` / `PM_DB_CONTAINER_PATH` (defaults keep `./pm.db`); see `docs/DEPLOYMENT.md`. That path is configured but not yet verified on a real server.

Local commercial database (decided 2026-10-16): the permanent path on the user's Mac is `/Users/pauloluisada/Library/Application Support/SalcompSalesPipeline/data/pm-commercial.db`, a byte-identical copy of the prepared scratch file (`pm-phase3-scratch/data/pm-commercial.db`), which stays preserved until the user explicitly authorizes retiring it. It is not activated; CPM migration, workbook import, administrator activation and cleanup each need separate authorization. The prepared file predates `migrate_bid_cpm.py`, so the app refuses to start on it until that migration is applied. Exact startup command (quoting, `PM_REQUIRE_EXISTING_DB=1`) and the `scripts/start-mac.sh` warning are in `docs/DEPLOYMENT.md`.

Validate the result (read-only):

```bash
sqlite3 /absolute/path/pm-commercial.db "
SELECT username, role, password_hash, email_verified_at FROM users; -- one row: admin, '!', NULL
SELECT title FROM boards;                              -- Salcomp Sales Pipeline
SELECT title, stage_kind FROM columns ORDER BY position; -- 6 stages, kinds as above
SELECT (SELECT COUNT(*) FROM cards), (SELECT COUNT(*) FROM card_stage_events), (SELECT COUNT(*) FROM import_batches); -- 0|0|0
"
```

Then start the app as above; the admin cannot log in until the password reset is completed. Tests: `uv run pytest test_create_commercial_db.py` (temporary databases only).

---

## Migration Strategy

### SQLite → PostgreSQL

Simple migration path:

```bash
# Export SQLite to SQL
sqlite3 pm.db .dump > schema.sql

# Import to PostgreSQL
psql -U postgres -d pm_db < schema.sql

# Adjust for PostgreSQL-specific features:
# - Use SERIAL instead of INTEGER AUTOINCREMENT
# - Use JSONB for metadata/changes columns
# - Use UUID for distributed systems
```

---

## Security Considerations

### Current
- Passwords hashed with bcrypt (no plaintext storage)
- Binary role check (admin/member) gates user-management endpoints, and within card routes: deleting a card and moving a card to an earlier column are admin-only; all other board/card routes just require authentication, not a specific role
- Access tokens last 8 hours and are revocable: logout and password changes bump `users.session_version`, which invalidates every earlier token of that user (all devices)

### Production
- Add password reset flow
- Extend RBAC beyond the current binary admin/member split (e.g. viewer, editor)
- Encrypt sensitive fields (email, etc.)
- Implement row-level security (PostgreSQL)

### Data Privacy
- Implement data retention policies
- Add GDPR compliance (data export, deletion)
- Audit logging for compliance
- Implement soft deletes (archive instead of delete)

---

## Future Enhancements

### Phase 2
- [x] Admin/member roles, admin-only user creation (first user = admin)
- [ ] More granular user roles (viewer, editor)
- [ ] Per-board membership/permissions (today, all boards are shared)
- [x] Activity feed, scoped to card edits (`card_edit_history` tracks member edits; a general feed across all action types is still open)
- [ ] Notifications system

### Phase 3
- [ ] Analytics (velocity, burndown)
- [ ] Board templates
- [ ] Integrations (GitHub, Jira, Slack)
- [ ] File attachments

### Phase 4
- [ ] Collaborative real-time updates
- [ ] Webhooks and automation
- [ ] Custom fields
- [ ] Report generation

---

## SQL Schema (SQLite)

See `backend/models.py` for SQLAlchemy ORM definitions.

The ORM models are auto-generated from this design and provide type safety and validation.

---

## Testing Database

Backend tests never touch `backend/pm.db`: `backend/conftest.py` sets a random `SECRET_KEY` and a temp-file `DATABASE_URL` for every pytest run. Tests that need several databases (migrations, startup guards) build them as files under pytest's `tmp_path`. Some route tests use in-memory SQLite with `StaticPool`. Playwright uses its own temp database (`PM_E2E=1`).

```bash
cd backend
uv run pytest                                          # whole suite
uv run pytest test_import_tables.py test_bid_money.py  # Part 22
```

---

## Backup & Recovery

### SQLite Backup
```bash
# Simple file copy
cp pm.db pm.db.backup

# Or use sqlite3
sqlite3 pm.db ".backup pm.db.backup"
```

### PostgreSQL Backup
```bash
pg_dump pm_db > pm_db.sql
pg_dump -Fc pm_db > pm_db.dump  # Compressed
```

---

## Approval Checklist

Before proceeding to Part 6 (Backend API), please review:

- [ ] Schema supports current MVP requirements
- [ ] Schema allows future multi-user expansion
- [ ] Relationships and constraints are correct
- [ ] Performance indexes are in place
- [ ] Data integrity is preserved
- [ ] Migration path to PostgreSQL is clear
- [ ] Security considerations addressed

**Status:** Awaiting user approval to proceed with Part 6
