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
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Account creation |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Last modified |

**Indexes:**
- `username (UNIQUE)` - Fast lookup by username
- `email (UNIQUE)` - Fast lookup by email
- `created_at` - Query recent users

**Current:** The first user ever created (via `POST /api/setup`) becomes `role="admin"`. Only an admin can create further users (`POST /api/users`), who are always created with `role="member"`. Role is re-read from this table on every request (not embedded in the JWT), so it can't go stale.
**Future:** More granular profiles beyond admin/member (viewer, editor, etc.)

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
| currency | VARCHAR(3) | - | EUR, USD, CNY, INR, BRL or VND |
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
- The lead columns were added after the first release: `database._add_missing_card_columns()` adds them with `ALTER TABLE` on startup to older databases (`create_all` never alters existing tables).
- Report totals are per currency - values are never converted or summed across currencies.
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
| source | VARCHAR | NOT NULL | `api` (REST: UI or API client), `ai`, `undo`, `seed`, `migration`, `system` |
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
- `baseline`: written only by the migration (see below).

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
| steps | TEXT | NOT NULL | JSON list of inverse operations (`move`/`update`/`delete`/`create`, see `backend/undo.py`), applied in reverse |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | When the change was made |

**Notes:**
- Replaced by the user's next change; deleted once undone (or once found impossible to undo).

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

---

## Data Relationships

### Cascade Rules

- **DELETE user** → Cascade delete boards, messages, actions; SET NULL on card_edit_history.user_id (preserve history)
- **DELETE board** → Cascade delete columns, cards, messages, actions
- **DELETE column** → Cascade delete cards
- **DELETE card** → Cascade delete card_edit_history; SET NULL on card_actions.card_id (preserve history)
- **DELETE message** → SET NULL on card_actions.message_id (preserve history)

This ensures referential integrity while preserving audit history.

---

## Example Data Flows

### User Login
```sql
SELECT * FROM users WHERE username = 'alice'
```

### First-run Admin Setup (`POST /api/setup`)
```sql
-- Only allowed while the users table is empty
INSERT INTO users (username, password_hash, role) VALUES ('alice', '<bcrypt hash>', 'admin')
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
- No token revocation/blacklist - a logged-out token remains valid until it expires (up to 24h)

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

For tests, use in-memory SQLite:
```python
DATABASE_URL = "sqlite:///:memory:"
```

This provides isolation between tests with no cleanup needed.

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
