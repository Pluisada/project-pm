# Database Design - Project Management MVP

## Overview

This document describes the database schema for the Project Management MVP application. The schema is designed to support:

- **Multi-user architecture** (MVP uses 1 hardcoded user, future supports multiple)
- **Multiple Kanban boards per user**
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
│  • password_hash                                         │
│  • email, full_name                                      │
│  • created_at, updated_at                                │
└─────────────────────────────────────────────────────────┘
              │
              │ (1:N)
              ▼
┌─────────────────────────────────────────────────────────┐
│                       BOARDS                             │
│  • id (PK)                                               │
│  • user_id (FK) → users                                  │
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
| password_hash | TEXT | NOT NULL | Hashed password (bcrypt in prod) |
| email | TEXT | UNIQUE | Contact email (optional for MVP) |
| full_name | TEXT | - | Display name (optional) |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Account creation |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Last modified |

**Indexes:**
- `username (UNIQUE)` - Fast lookup by username
- `email (UNIQUE)` - Fast lookup by email
- `created_at` - Query recent users

**Current MVP:** Only 1 hardcoded user (id=1, username="user")  
**Future:** Multiple users with password hashing

### boards
**Purpose:** Kanban boards, one per user (MVP), multiple per user (future)

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| id | INTEGER | PK, AUTOINCREMENT | Unique identifier |
| user_id | INTEGER | FK → users, NOT NULL | Board owner |
| title | TEXT | NOT NULL | Board name |
| description | TEXT | - | Board description |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Creation time |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Last modified |

**Indexes:**
- `user_id` - Find all boards for a user
- `created_at` - List boards by date

**Current MVP:** 1 board per user  
**Future:** Multiple boards per user (project, team, etc.)

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

### User Login (Part 4)
```sql
SELECT * FROM users WHERE username = 'user'
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

### Current (MVP)
- Credentials hardcoded (user/password)
- Password stored as plain text

### Production
- Hash passwords with bcrypt
- Add password reset flow
- Add role-based access control (RBAC)
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
- [ ] User roles (admin, viewer, editor)
- [ ] Board sharing and permissions
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
