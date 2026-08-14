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
**Purpose:** Kanban board columns (Backlog, In Progress, Review, Done, etc.)

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK, AUTOINCREMENT | Unique identifier |
| board_id | INTEGER | FK → boards, NOT NULL | Parent board |
| title | TEXT | NOT NULL | Column name |
| position | INTEGER | NOT NULL, DEFAULT 0 | Order in board (0, 1, 2, ...) |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Creation time |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Last modified |

**Indexes:**
- `board_id` - Find all columns for a board
- `(board_id, position)` - Get columns in order

**Notes:**
- Position field allows reordering columns without full reconstruction
- Typically 3-5 columns per board (Backlog, Discovery, In Progress, Review, Done)

### cards
**Purpose:** Individual Kanban cards (tasks, items)

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK, AUTOINCREMENT | Unique identifier |
| column_id | INTEGER | FK → columns, NOT NULL | Parent column |
| title | TEXT | NOT NULL | Card title/task name |
| details | TEXT | - | Description/notes |
| position | INTEGER | NOT NULL, DEFAULT 0 | Order within column |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Creation time |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Last modified |

**Indexes:**
- `column_id` - Find all cards in a column
- `(column_id, position)` - Get cards in order
- `created_at` - Query recent cards

**Notes:**
- Position allows drag-and-drop reordering
- Deleting a column cascades delete to all its cards

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

- **DELETE user** → Cascade delete boards, messages, actions
- **DELETE board** → Cascade delete columns, cards, messages, actions
- **DELETE column** → Cascade delete cards
- **DELETE card** → SET NULL on card_actions.card_id (preserve history)
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
- Binary role check (admin/member) gates user-management endpoints only; board/card routes just require authentication, not a specific role
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
- [ ] Activity feed (who did what)
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
