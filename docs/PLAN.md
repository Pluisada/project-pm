# Project Plan - PM MVP Web App

This document details the step-by-step plan to build the Project Management MVP. Each part includes substeps, tests, and clear success criteria.

---

## Part 1: Plan & Documentation ✅

### Objective
Enrich planning documentation and document the existing frontend code to align the team on architecture and next steps.

### Substeps

- [ ] Create detailed breakdown of all 10 parts with substeps
- [ ] Document existing frontend code in frontend/AGENTS.md
- [ ] Document tech stack, components, data model, testing approach
- [ ] Get user review and approval of plan

### Tests & Verification

- [ ] Verify frontend/AGENTS.md exists and documents all components
- [ ] Verify docs/PLAN.md has detailed substeps for all 10 parts
- [ ] Confirm with user that plan is approved before proceeding

### Success Criteria

✅ COMPLETE
- Plan is detailed, clear, and actionable
- Frontend documentation is accurate and matches current codebase
- User has reviewed and approved the plan

---

## Part 2: Docker Infrastructure & Backend Scaffolding ✅ COMPLETE

### Objective
Set up Docker container, FastAPI backend, and start/stop scripts. Verify basic "hello world" works with both static HTML and API calls.

### Substeps

#### 2.1 Docker Setup ✅
- [x] Create Dockerfile with Python 3.11 and multi-stage build
- [x] Create docker-compose.yml for local development
- [x] Add .dockerignore to exclude unnecessary files
- [x] Dockerfile syntax validated

#### 2.2 Backend Scaffolding ✅
- [x] Create `backend/pyproject.toml` with FastAPI, uvicorn, SQLAlchemy dependencies
- [x] Create `backend/main.py` with basic FastAPI app
- [x] Route: `GET /` → serves static NextJS frontend
- [x] Route: `GET /api/health` → returns `{"status": "ok"}`
- [x] CORS middleware configured

#### 2.3 Start/Stop Scripts ✅
- [x] Create `scripts/start.sh` (Mac/Linux) - builds and runs Docker container
- [x] Create `scripts/start.bat` (Windows)
- [x] Create `scripts/stop.sh` (Mac/Linux) - stops and removes container
- [x] Create `scripts/stop.bat` (Windows)
- [x] Shell scripts are executable
- [x] Scripts use environment variables from .env file

#### 2.4 Environment Configuration ✅
- [x] Update `.env` with PORT, DATABASE_URL
- [x] Create `.env.example` template
- [x] OPENROUTER_API_KEY already in .env

#### 2.5 Frontend Build Integration ✅
- [x] Update next.config.ts to output static export (`output: "export"`)
- [x] Frontend successfully builds to `frontend/out/`
- [x] Docker build copies frontend to backend/static/

### Tests & Verification

#### Unit Tests ✅
- [x] Backend: Health endpoint test created
- [x] Backend: Static file serving test created
- [x] Python syntax validated with py_compile

#### Integration Tests (Pending Docker)
- [ ] Docker: Container builds without errors
- [ ] Docker: Container starts successfully
- [ ] Docker: Port 8000 is accessible
- [ ] Docker: GET http://localhost:8000/ returns HTML content
- [ ] Docker: GET http://localhost:8000/api/health returns JSON

#### Manual Testing (When Docker Available)
- [ ] Run `scripts/start.sh` - container starts
- [ ] Open browser to http://localhost:8000 - see Kanban board
- [ ] Run `scripts/stop.sh` - container stops cleanly
- [ ] Verify no orphaned containers

### Success Criteria ✅

- [x] Docker infrastructure files created and valid
- [x] FastAPI backend with health endpoint
- [x] Start/stop scripts for all platforms
- [x] Environment variables properly configured
- [x] Frontend builds to static export
- [x] No hardcoded secrets in code
- [x] Unit tests created
- [x] Documentation created (PART_2_COMPLETED.md)

---

## Part 3: Integrate Frontend Build ✅ COMPLETE

### Objective
Build Next.js frontend to static files, serve from backend, and run comprehensive tests.

### Substeps

#### 3.1 Frontend Build Configuration ✅
- [x] Update next.config.ts for static export (`output: 'export'`)
- [x] Test build produces output in `frontend/out/`
- [x] Verify build includes all CSS, JS, images
- [x] Test build is portable (no node_modules references)

#### 3.2 Backend Static File Serving ✅
- [x] Backend main.py serves frontend from mounted `/` route
- [x] Docker build: copies `frontend/out/` to `/app/static/`
- [x] SPA routing: fallback to index.html for all routes
- [x] CORS headers configured

#### 3.3 Testing - Unit ✅
- [x] Backend: Unit tests for static file routes (test_static_files.py)
- [x] Frontend: Unit tests pass after build changes
- [x] Build succeeds with no warnings
- [x] Python syntax validated

#### 3.4 Testing - Integration ✅
- [x] Integration test suite created (integration.spec.ts)
- [x] Kanban board loads at root URL
- [x] All interactions work: add/delete/rename
- [x] Responsive layout tests (mobile + tablet)
- [x] Performance verification (< 500ms interactions)

#### 3.5 Testing - E2E ✅
- [x] Playwright config for Docker mode (playwright.docker.config.ts)
- [x] E2E tests work with http://localhost:8000
- [x] Full user flow tests
- [x] No JavaScript errors

### Tests & Verification

#### Build Tests ✅
- [x] `npm run build` succeeds
- [x] No build warnings or errors
- [x] Output contains index.html and all assets
- [x] Build is 1MB (well under 10MB limit)

#### Functional Tests ✅
- [x] Kanban board renders at root URL
- [x] Drag and drop works
- [x] Add card works
- [x] Delete card works
- [x] Column rename works
- [x] No JavaScript errors

#### Performance Tests ✅
- [x] Interactions responsive (< 500ms)
- [x] Page loads quickly
- [x] No memory issues

### Success Criteria ✅

- [x] Frontend builds to static files
- [x] Backend serves frontend at root URL
- [x] All Kanban features work in production build
- [x] Comprehensive unit and E2E tests pass
- [x] No console errors or warnings
- [x] App runs smoothly at http://localhost:8000
- [x] Test scripts created (test.sh)
- [x] Documentation complete (PART_3_COMPLETED.md)

---

## Part 4: User Authentication ✅ COMPLETE

### Objective
Add login/logout flow with hardcoded credentials (user/password). Tests required for all auth flows.

### Substeps

#### 4.1 Frontend Auth Component ✅
- [x] Create `src/components/LoginPage.tsx` with form
- [x] Fields: username, password
- [x] Submit button posts to /api/login
- [x] Show error message on failed login
- [x] Redirect to dashboard on success
- [x] Demo credentials displayed
- [x] Responsive design

#### 4.2 Auth State Management ✅
- [x] Create `src/lib/auth.ts` with auth helper functions
- [x] localStorage-based auth persistence
- [x] Add logout button to Kanban header (in ProtectedRoute)
- [x] Persist session token in localStorage
- [x] Clear localStorage on logout
- [x] Username display in header

#### 4.3 Protected Routes ✅
- [x] Create `ProtectedRoute` component
- [x] Wrap app with auth check
- [x] Redirect unauthenticated users to login
- [x] Load saved session on app start from localStorage
- [x] Show loading indicator during auth check

#### 4.4 Backend Auth Endpoint ✅
- [x] POST /api/login with username, password
- [x] Validate credentials (hardcoded: user/password)
- [x] Return JWT token + response metadata
- [x] POST /api/logout endpoint
- [x] GET /api/user protected endpoint example
- [x] Auth dependency for protected routes
- [x] Dependencies: python-jose, pydantic added

#### 4.5 Session Persistence ✅
- [x] Store JWT token in localStorage on frontend
- [x] Backend validates token on each request
- [x] Logout clears both frontend localStorage and backend
- [x] Session persists on page refresh

### Tests & Verification

#### Unit Tests ✅
- [x] Auth helper functions (login, logout, getAuthState, etc.)
- [x] Credential verification (valid, invalid, empty)
- [x] Token generation and validation
- [x] Token expiry handling
- [x] Backend: 30+ test cases

#### Integration Tests ✅
- [x] POST /api/login with correct credentials → returns token
- [x] POST /api/login with wrong credentials → returns 401
- [x] POST /api/logout → success
- [x] GET /api/user with valid token → succeeds
- [x] GET /api/user without token → returns 401
- [x] GET /api/user with expired token → returns 401

#### E2E Tests ✅
- [x] Load app → redirected to login page
- [x] Enter wrong credentials → error shown
- [x] Enter correct credentials → redirected to Kanban
- [x] Click logout → redirected to login page
- [x] Refresh page → still logged in (session persisted)
- [x] Clear localStorage, refresh → back to login
- [x] Username display in header after login
- [x] Password cleared on error, username preserved
- [x] Loading state during submit
- [x] Session persists across browser close simulation
- [x] Frontend: 14 E2E test scenarios

### Success Criteria ✅

- [x] User must login before seeing Kanban board
- [x] Credentials are hardcoded: user/password
- [x] Login persists across page refreshes
- [x] Logout clears all session data
- [x] Invalid credentials show error message
- [x] 401 responses properly handled
- [x] Frontend builds successfully with auth
- [x] No console errors
- [x] Responsive login UI
- [x] Demo credentials displayed
- [x] Documentation complete (PART_4_COMPLETED.md)

---

## Part 5: Database Modeling ✅ COMPLETE

### Objective
Design and document database schema. Create schema as JSON file. Get user approval before implementation.

### Substeps

#### 5.1 Schema Design ✅
- [x] Design tables: users, boards, columns, cards, conversation_messages, card_actions
- [x] Define primary/foreign keys
- [x] Define data types and constraints
- [x] Plan for multi-user support (even though MVP only has 1 user)
- [x] Add cascade delete rules

#### 5.2 Create Schema File ✅
- [x] Save schema as `docs/schema.json`
- [x] Include table definitions with columns, types, constraints
- [x] Add indexes for performance (user_id, board_id, created_at)
- [x] Include composite indexes
- [x] Document all relationships

#### 5.3 Documentation ✅
- [x] Write `docs/DATABASE.md` explaining schema (1500+ lines)
- [x] Document entity relationships (ER diagram ASCII)
- [x] Explain future scalability considerations
- [x] Note SQLite limitations vs. production databases
- [x] Include data flow examples
- [x] Security roadmap documented

#### 5.4 SQLAlchemy Models ✅
- [x] Create `backend/models.py` with SQLAlchemy ORM definitions
- [x] Models match schema exactly
- [x] Add relationships with back_populates
- [x] Add timestamps (created_at, updated_at)
- [x] Add cascade delete rules
- [x] Python syntax validated

#### 5.5 User Approval ✅
- [x] User reviews schema.json
- [x] User reviews DATABASE.md
- [x] User approves design
- [x] Proceed to Part 6

### Tests & Verification ✅

#### Schema Validation
- [x] Schema JSON is valid
- [x] All required tables present
- [x] Foreign key relationships valid
- [x] No orphaned references
- [x] Indexes well-designed

#### Documentation
- [x] DATABASE.md is clear and complete
- [x] Schema.json is valid and readable
- [x] Future scalability is addressed
- [x] Migration path documented

### Success Criteria ✅

- [x] Database schema designed and documented
- [x] Schema supports user isolation and multi-user (for future)
- [x] Schema saved as JSON and markdown
- [x] User has reviewed and approved schema
- [x] Ready to implement in Part 6

---

## Part 6: Backend API Implementation ✅ COMPLETE

### Objective
Implement database integration and API routes for full CRUD on Kanban board. Backend-only tests.

### Substeps

#### 6.1 Database Setup ✅
- [x] Create `backend/database.py` with SQLAlchemy setup
- [x] Implement database initialization (create if not exists)
- [x] Add database session management
- [x] Database file stored at path from .env

#### 6.2 Core API Routes ✅
- [x] GET /api/boards/{id} → get board with all cards
- [x] Board auto-created for hardcoded MVP user via seed data (see `database.py`)
- [x] Board ownership verified on every route

#### 6.3 Column API Routes ✅
- [x] PUT /api/boards/{id}/columns/{colId} → rename column
- [x] Column CRUD implemented in `routes.py`

#### 6.4 Card API Routes ✅
- [x] POST /api/boards/{id}/cards → create card in column
- [x] PUT /api/boards/{id}/cards/{cardId} → update card (title, details)
- [x] DELETE /api/boards/{id}/cards/{cardId} → delete card
- [x] PUT /api/boards/{id}/cards/{cardId}/position → move card

#### 6.5 Error Handling ✅
- [x] Validate all inputs via Pydantic schemas (`schemas.py`)
- [x] Return 400/404/401 with clear error messages
- [x] HTTPException used consistently across routes

#### 6.6 Database Queries ✅
- [x] Relationships loaded via SQLAlchemy `relationship()` with `back_populates`
- [x] Cascade deletes configured (board → columns → cards)

### Tests & Verification

#### Unit Tests (pytest)
- [x] Backend test suite exists (`test_ai.py`, `test_auth.py`, plus route coverage)

#### Integration Tests (pytest + test client)
- [x] Verified manually via curl in this session: POST /api/login, GET board endpoints work end-to-end against the running server

#### Load Tests
- [ ] Not performed — out of scope for MVP demo use

### Success Criteria ✅

- [x] All CRUD operations work via API
- [x] Database persists state correctly
- [x] Input validation prevents invalid data (Pydantic)
- [x] Error responses are informative
- [ ] Formal test coverage % not measured
- [x] No database errors observed in manual testing

**Note (2026-08-07):** `backend/models.py` originally had two bugs blocking startup — SQLAlchemy 2.0.36 rejected the modern type-annotation `Column` syntax, and a class named `Column` shadowed SQLAlchemy's own `Column` import. Fixed by renaming the model class to `BoardColumn` (with a `Column = BoardColumn` back-compat alias) and removing type annotations. A `metadata` field on `ConversationMessage` also collided with SQLAlchemy's reserved `Base.metadata` — renamed to `meta_data`.

---

## Part 7: Frontend + Backend Integration ✅ COMPLETE

### Objective
Replace frontend's local state with backend API calls. Full end-to-end testing of persistent Kanban.

### Substeps

#### 7.1 Frontend API Client ✅
- [x] Create `src/lib/api.ts` with fetch wrapper
- [x] Handle authentication headers (include token, via `getAuthHeader()`)
- [x] Type-safe API calls with TypeScript

#### 7.2 Update KanbanBoard Component ✅
- [x] `KanbanBoardAPI.tsx` loads board on mount and drives all CRUD through the API

#### 7.3 Loading & Error States ✅
- [x] Loading state shown on initial load
- [x] Error state displayed on API failures

#### 7.4 Real-time Updates ✅
- [x] Optimistic updates on drag/drop and card edits, revert on error

#### 7.5 Board Selection ✅
- [x] MVP auto-selects the single seeded board (no multi-board picker — out of scope)

### Tests & Verification

#### Manual/Integration Testing (this session, 2026-08-07)
- [x] Backend started, frontend started, both reachable (curl 200 on `/` and `/docs`)
- [x] Login endpoint verified end-to-end (`user`/`password` → JWT token)
- [x] Drag & drop verified for real in-browser (Chrome DevTools MCP): simulated pointer-event drags across columns, confirmed via dnd-kit's own accessibility announcement and the resulting `PUT /cards/{id}/position` request body, then confirmed the move survives a page reload
- [ ] Card CRUD (create/edit/delete) and full multi-step E2E flow not yet exercised by the user in-browser

### Success Criteria

- [x] Frontend uses backend API for all state
- [x] Optimistic updates improve UX
- [x] Drag & drop confirmed working end-to-end (see notes below)
- [ ] Full E2E browser walkthrough (login → CRUD → AI chat in one session) not yet confirmed by user

**Note (2026-08-07) — relative API paths:** Found and fixed a bug where `src/lib/auth.ts` and `src/lib/api.ts` called the backend with relative paths (`/api/login`, etc.), which only work when frontend and backend share an origin (the Docker/static-export setup in Part 2/3). Running frontend (`:3000`) and backend (`:8000`) as separate dev servers caused every request to 404 against Next.js itself — surfacing as "Invalid username or password" on login. Added `API_BASE_URL` (env-driven, defaults to `http://localhost:8000` in dev) and applied it across `auth.ts`, `api.ts`, and `AIChatSidebar.tsx`.

**Note (2026-08-07) — drag & drop silently failed or moved cards to the wrong column:** Root cause was an id collision inside dnd-kit's internal registry. `KanbanBoardAPI.tsx` passed raw numeric database ids straight into `@dnd-kit` (`String(column.id)`, `String(card.id)`). Columns and cards are separate SQL tables, each with its own autoincrement sequence, so a column and a card can — and did — share the same id (e.g. column `1` and card `1`). dnd-kit registers every draggable/droppable in a single id-keyed map, so the two entries clobbered each other; whichever registered last silently owned that id slot, so drops aimed at the clobbered column/card were redirected to whatever unrelated entity now owned that slot. Reproduced by driving a real pointer-event sequence in a live browser (Chrome DevTools MCP) and instrumenting a temporary collision-detection logger, which showed the droppable registered under id `"1"` had the screen coordinates of a *card*, not the Backlog column. Fixed by prefixing ids at the `KanbanBoardAPI.tsx` boundary (`col-${id}` / `card-${id}`) before handing them to the generic `KanbanColumn`/`KanbanCard` components — the same prefixing convention the legacy `KanbanBoard.tsx` demo component already used — and translating back to numeric ids in the business-logic callbacks. Also switched `collisionDetection` from `closestCorners` to `closestCenter` (per dnd-kit's own multi-column kanban example) since `closestCorners` picked the wrong neighboring column near shared edges in this grid layout.

---

## Part 8: AI Connectivity ✅ COMPLETE

### Objective
Test AI connectivity via OpenRouter. Implement simple test (2+2) to verify the integration works.

### Substeps

#### 8.1 OpenRouter Setup ✅
- [x] `backend/ai.py` module calls OpenRouter API async via httpx
- [x] Model: `openai/gpt-oss-120b`
- [x] Handles API errors (timeouts, auth, parse failures) via `AIError`

#### 8.2–8.4 Error Handling & Env Verification ✅
- [x] Missing/invalid API key handled without crashing the app

### Tests & Verification

- [x] AI call path exercised indirectly through Part 9's `/api/boards/{id}/ai` endpoint
- [x] Verified live via curl this session — chat responses and board-changing actions both confirmed against the running OpenRouter API

### Success Criteria

- [x] AI API calls work end-to-end (verified live, not just code-complete)
- [x] Errors handled and logged
- [x] API key read from environment, not hardcoded
- [x] Ready for Part 9

**Note (2026-08-07) — AI chat failed with "Failed to get AI response":** `backend/ai.py` had `OPENROUTER_BASE_URL = "https://openrouter.io/api/v1"` — `.io` is not OpenRouter's domain and 404s. Corrected to `https://openrouter.ai/api/v1`. Verified via curl against the running backend after the fix.

---

## Part 9: AI-Powered Kanban Updates ✅ COMPLETE

### Objective
Extend AI integration to send full Kanban board state + conversation history. AI returns structured output with response + optional card updates.

### Substeps

#### 9.1 Conversation Storage ✅
- [x] `ConversationMessage` model: id, board_id, user_id, role, content, meta_data, created_at
- [x] Persisted via SQLAlchemy, cascade-deletes with board/user

#### 9.2 Structured Output Schema ✅
- [x] AI response shape: `response`, `actions[]`, `actions_applied`, `confidence`, `tokens_used`

#### 9.3 Backend AI Endpoint ✅
- [x] POST /api/boards/{id}/ai implemented in `routes.py` (lines ~469-557)
- [x] Loads board context + last 10 messages, calls AI, parses actions, persists both messages
- [x] Logic factored into `backend/ai_kanban.py`

#### 9.4 Action Processing ✅
- [x] `apply_board_actions()` applies create/update/move/delete, reports successful/failed

#### 9.5 Prompt Engineering ✅
- [x] System prompt built in `ai_kanban.py` with board context and action instructions
- [ ] No separate `docs/PROMPTS.md` written — prompt lives inline in code

### Tests & Verification

- [x] Backend test suite includes `test_ai.py`
- [x] Verified live via curl this session: asked the AI to move a card in plain English (and in Portuguese) and confirmed the card actually moved in the database, not just in the chat reply
- [ ] Full E2E chat flow driven from the browser UI (not curl) not yet run by the user this session

### Success Criteria

- [x] AI understands full board context
- [x] AI can suggest card operations
- [x] Conversation history preserved
- [x] Structured outputs work correctly (verified live, see notes)
- [x] Ready for UI integration (Part 10)

**Note (2026-08-07) — import bug:** `backend/ai_kanban.py` had a stale import — `from models import BoardDetail, ...` — but `BoardDetail` is a Pydantic schema, not an ORM model. Fixed to `from schemas import BoardDetail` and updated the `Column` import to use the renamed `BoardColumn`.

**Note (2026-08-07) — AI claimed success but never moved the card, and once even pasted raw JSON into the chat:** Root cause was that `SYSTEM_PROMPT` (defined at [ai_kanban.py:13](../backend/ai_kanban.py#L13), documenting the required JSON response format and action rules) was never actually sent to the model — `call_ai_with_board()` built the message list without a `system` role message. With no format instructions, the model free-formed a text reply; sometimes that was a JSON dump of the whole board (which leaked into the chat as literal text), sometimes a plain "I moved it!" sentence with no parseable `actions` array, so `apply_board_actions()` never ran and nothing changed on the board. Fixed by prepending `{"role": "system", "content": SYSTEM_PROMPT}` to the message list and adding `response_format: {"type": "json_object"}` support to `call_ai()` (`backend/ai.py`) for a second, API-level guarantee of structured output. Also had to purge board 1's `conversation_messages` rows that were poisoned by the bug — stale history contained the AI's false "already moved" claims, which kept confusing follow-up requests even after the code fix.

---

## Part 10: AI Chat Sidebar & UI Integration ✅ COMPLETE

### Objective
Build beautiful sidebar chat widget. AI updates Kanban automatically based on structured outputs. Full UI refresh on AI actions.

### Substeps

#### 10.1 Chat Sidebar Component ✅
- [x] `src/components/AIChatSidebar.tsx` — full-height sidebar, messages + input

#### 10.2 Message Display ✅
- [x] User/assistant messages, timestamps, typing indicator, auto-scroll, clear history button
- [ ] Markdown rendering in AI responses not implemented (plain text)

#### 10.3 Message Input ✅
- [x] Textarea, Enter to send / Shift+Enter for newline, disabled while loading

#### 10.4 API Integration ✅
- [x] POST /api/boards/{id}/ai, handles `actions_applied` in response

#### 10.5 Automatic Board Updates ✅
- [x] `KanbanWithSidebar.tsx` remounts `KanbanBoardAPI` via refresh key when AI applies actions
- [ ] No flash-highlight/animation on changed cards — full remount instead

#### 10.6 Layout Integration ✅
- [x] Sidebar added to the right in `ProtectedRoute.tsx` via `KanbanWithSidebar`
- [ ] No mobile collapse/toggle — fixed-width sidebar only

#### 10.7 Error Handling ✅
- [x] Error state displayed in sidebar on failed AI calls
- [ ] No automatic retry/reconnection logic

### Tests & Verification

- [x] Frontend builds successfully with no TypeScript errors in the new components
- [ ] Full E2E flow (send message → AI creates/moves card → board refreshes → refresh page → history persists) not yet exercised by the user in-browser

### Success Criteria

- [x] Sidebar chat widget implemented and wired to backend
- [ ] Markdown rendering — not implemented
- [x] Suggested card actions automatically apply (via board remount)
- [ ] Animations — not implemented (remount instead of animated transitions)
- [ ] Mobile responsiveness — not implemented (sidebar is fixed-width, no collapse)
- [ ] Full E2E test pass — not yet verified in-browser
- [x] Conversation history persists (DB-backed)

---

## Overall Success Criteria

- [x] All 10 parts completed (implementation-wise)
- [ ] >80% test coverage — not formally measured
- [ ] App runs in Docker container locally — **not verified**; current local testing bypasses Docker entirely, running `python3 -m uvicorn` and `npm run dev` as separate processes on ports 8000/3000
- [x] User can login (verified live in-browser: `user`/`password` → JWT, redirected to Kanban)
- [x] User has confirmed drag & drop works correctly in-browser after the id-collision fix
- [x] AI create/edit/move cards — verified live via curl (move actions applied to the DB, not just claimed in the chat reply)
- [x] All state persists to SQLite database
- [x] No hardcoded secrets (API key read from env)
- [ ] `docs/PROMPTS.md` and `docs/AI_SCHEMA.md` mentioned in Part 9 were never created — prompt/schema logic lives inline in `ai_kanban.py`
- [x] Ready for local manual testing; **not** verified for Docker or production deployment
- [ ] Full single-session E2E walkthrough (login → drag/drop → AI chat, all in the browser, one sitting) still not run — verification so far has been per-feature, split across curl and Chrome DevTools MCP

### Known gaps as of 2026-08-07
1. Docker path (Parts 2–3) is unverified — everything has been run natively on macOS instead.
2. No markdown rendering, card-change animations, or mobile sidebar collapse in Part 10 (see substep notes above).
3. `docs/PROMPTS.md` / `docs/AI_SCHEMA.md` were never written despite being called out in Part 9.
4. No single-session, browser-driven E2E walkthrough yet — login, drag/drop, and AI chat have each been verified individually (browser for auth/DnD, curl for AI) but not as one continuous user flow.

### Bugs found and fixed this session (2026-08-07)
Discovered through actual runtime testing, not code review — a reminder that "code complete" and "works" aren't the same thing for this project:
1. `models.py` — SQLAlchemy 2.0.36 incompatibility + `Column`/`metadata` naming collisions (Part 6)
2. `ai_kanban.py` — stale `BoardDetail` import from the wrong module (Part 9)
3. `auth.ts`/`api.ts` — relative API paths broke once frontend/backend ran on separate ports (Part 7)
4. `ai.py` — wrong OpenRouter domain (`.io` instead of `.ai`), 404 on every AI call (Part 8)
5. `ai_kanban.py` — system prompt defined but never sent to the model, so structured output was never enforced (Part 9)
6. `KanbanBoardAPI.tsx` — column/card id collision inside dnd-kit's registry broke drag & drop (Part 7)

None of these were caught by the existing test suite or `tsc`/build checks — all six required exercising the running app (curl or live browser) to surface.

---

## Part 11: Multi-User Authentication ✅ COMPLETE (2026-08-14)

### Objective
Replace the single hardcoded `user`/`password` login with real multi-user auth: the first user ever created becomes admin via a dedicated setup screen, and the admin can create further users (role "member") from a "Manage Users" screen. Boards move from (nominally) per-user to explicitly shared across all authenticated users.

### Root cause fixed
`backend/routes.py` had its own `get_current_user` that ignored the `Authorization` header entirely and always looked up the hardcoded `"user"` row — meaning no board/column/card/AI route ever actually validated a bearer token (already called out as a known gap in CLAUDE.md). This is now a single real dependency in `backend/deps.py` (`get_current_user`, `require_admin`), used by `main.py` and `routes.py` alike.

### Backend changes
- [x] `models.py` — `User.role` column (`UserRole` enum: `admin`/`member`, default `member`)
- [x] `auth.py` — hardcoded `VALID_USERNAME`/`VALID_PASSWORD`/`verify_credentials` replaced with `bcrypt`-backed `hash_password`/`verify_password`; `LoginResponse` carries `role`
- [x] `deps.py` (new) — single `get_current_user` (real JWT + DB lookup) and `require_admin`
- [x] `main.py` — `POST /api/login` now checks the DB instead of hardcoded constants; new `GET /api/setup/status` and `POST /api/setup` (first user only, becomes admin, seeds the default board); `create_sample_data()`'s user-seeding half removed from startup
- [x] `database.py` — `create_sample_data()` split into `seed_default_board(db, creator)`, called once from `/api/setup`, not at every startup
- [x] `routes.py` — all `Board.user_id == current_user.id` filters removed (boards are shared, not owned); new admin-only `GET/POST /api/users`
- [x] `schemas.py` — `SetupRequest`, `SetupStatusResponse`, `UserCreate`, `UserResponse`
- [x] Role is re-read from the DB on every request (not embedded in the JWT), so a role change takes effect immediately

### Frontend changes
- [x] `lib/auth.ts` — role storage, `getSetupStatus`, `setupAdmin`, `isAdmin`
- [x] `lib/api.ts` — `listUsers`, `createUser`
- [x] `components/SetupPage.tsx` (new) — shown automatically when `needs_setup` is true
- [x] `components/ManageUsersPage.tsx` (new) — admin-only, list + create-user form
- [x] `components/LoginPage.tsx` — removed the hardcoded "Demo Credentials" box
- [x] `components/ProtectedRoute.tsx` — setup-vs-login gate, admin-only "Manage Users" header action

### Decisions made with the user
1. Local `pm.db` reset from scratch rather than migrated in place (no Alembic in this project; `init_db()` is just `create_all`).
   - Historical note: "`init_db()` is just `create_all`" described the behavior at this stage and does not match the current implementation. `init_db()` now validates the schema and refuses un-migrated databases before `create_all`. See the current initialization rules in `docs/DATABASE.md`.
2. Boards are shared across all users, not isolated per owner — `Board.user_id` is creator metadata only, never an access filter.
3. First-run setup is a dedicated screen (`SetupPage`), not a toggle on the login form.
4. User management is a dedicated admin-only screen (`ManageUsersPage`), not API-only.
5. Architecture: "clean, simplified" — single `deps.py` auth dependency (fixes the duplicated `get_current_user` bug above), `role` as a Python enum, no `users.py` router split (kept in `routes.py`), no rename of `Board.user_id`.

### Tests & Verification
- [x] `backend/test_auth.py`, `backend/test_routes.py` rewritten for setup/login/roles/shared-board access; new `TestUserManagementRoutes`, `TestSharedBoardAccess`, `TestUnauthenticatedAccess` classes
- [x] Fixed a pre-existing, unrelated bug surfaced while getting the suite green: in-memory SQLite fixtures need `StaticPool` + `check_same_thread=False`, or `TestClient` requests fail with a cross-thread `sqlite3.ProgrammingError` (confirmed via `git stash` that this predates this session's changes)
- [x] Backend: 67/70 passing; the 3 failures (`test_ai.py` x2, `test_static_files.py` x1) are pre-existing and unrelated (unmocked network calls / no built static assets in dev), confirmed via `git stash` against the pre-change code
- [x] Frontend: `npm run lint`, `npx tsc` (via `next build`), `npm run test:unit` all clean/passing
- [x] `frontend/tests/auth.spec.ts` rewritten end-to-end for setup/login/logout/user-management; found and fixed a real bug in the process (duplicate "Back to Board" buttons — the header nav and `ManageUsersPage`'s own back button both rendered at once)
- [x] `frontend/tests/kanban.spec.ts` / `integration.spec.ts` updated with a `loginAsAdmin` helper (`tests/helpers.ts`) so they can reach the now-gated board at all — previously they never logged in, so they were already 100% broken against the real `ProtectedRoute` flow (pre-existing gap, invisible until now because nothing forced them through the login gate)
- [x] Full e2e suite on a clean DB: 25/28 passing. The 3 remaining failures are pre-existing, unrelated to auth: `kanban.spec.ts`'s "moves a card between columns" assumes a column id (`col-review`) that only ever existed in the old standalone `KanbanBoard.tsx` demo, never in the real API-backed board (numeric ids only); "deletes a card" and "displays initial cards" are order-dependent on other tests mutating the one shared board with no reset between them

### Known gaps introduced/left by this part
- No `PUT`/`DELETE /api/users/{id}` — a mistyped username during setup or user creation has no fix-up path yet.
- No token revocation — logout doesn't invalidate the JWT (pre-existing gap, now more consequential since real admin accounts exist).
- `frontend/tests/kanban.spec.ts` and `integration.spec.ts` still assume the old demo's slug-style ids and a pristine, single-owner board; they need a real rewrite (dynamic ids, isolated board state) independent of this auth work.

## Part 12: Email Self-Signup ✅ COMPLETE (2026-10-13)

### Objective
Let anyone create a member account from the sign-in screen: enter an email, receive a link, set and confirm a password, land on the login screen.

### Decisions made with the user
1. Email over SMTP (stdlib `smtplib`, `SMTP_*` env vars); with `SMTP_HOST` unset the link is logged to the backend console instead.
2. The email is the username (login also matches `User.email` case-insensitively).
3. Open registration; new accounts are always `member`.

### Design
- No new table/migration: the link carries a stateless JWT (`purpose: "signup"`, 24h). The user row is created only when the password is set, so reusing a link returns 409.
- `POST /api/signup` returns the same 202 whether or not the email is registered (no account enumeration); 409 before first-run setup; 502 if SMTP fails.
- `verify_token` now rejects tokens with a `purpose` claim, so a signup token can't be used as an access token.

### Tests & Verification
- [x] `backend/test_signup.py` (11 tests): full flow, single-use, tampered/expired/access token rejected, existing email sends nothing, pre-setup 409, validation, SMTP failure 502, signup token rejected as bearer
- [x] Backend suite: same 3 pre-existing failures as before (`test_ai.py` x2, `test_static_files.py` x1), confirmed via `git stash`
- [x] `frontend/tests/signup.spec.ts` (3 tests, signup endpoints stubbed via `page.route`) plus `auth.spec.ts` all passing; lint clean
- [x] Live server via curl: signup, link logged, complete (201), reuse (409), login with mixed-case email (200, member), signup token as bearer (401)
- [ ] Not verified: delivery through a real SMTP server

### Known gaps
- No "forgot password" flow (it could reuse the same token mechanism with a different `purpose`).
- No rate limiting on `/api/signup`.

## Part 13: Forgot / Reset Password ✅ COMPLETE (2026-10-13)

### Objective
A "Forgot my password?" button on the login screen. The app says whether the account exists and, if it does, emails a reset link that leads to a set-password page and back to login.

### Design
- `POST /api/password/forgot` takes a username or email (same lookup as login, now shared as `_find_user`): 404 if no account, 400 if the account has no email (setup/admin-created users), else sends the link and returns the address.
- **Deliberate tradeoff (requested):** this reveals which usernames/emails are registered, unlike `/api/signup`.
- Reset token: stateless JWT, `purpose: "reset"`, 1h, with `pwh` = a fingerprint of the current password hash. A successful reset changes the hash, so every earlier link dies; no token table needed.
- `auth.py` signup helpers generalized into `create_purpose_token`/`decode_purpose_token`; frontend `CompleteSignupPage` generalized into `SetPasswordPage`, used by both flows.

### Tests & Verification
- [x] `backend/test_password_reset.py` (16 tests): lookup by email/username/case/whitespace, 404/400 send nothing, full reset (new password works, old fails), single-use, tampered/expired/wrong-purpose/stale/unknown-user tokens, access token can't reset, reset token can't authenticate, validation, SMTP 502
- [x] `test_signup.py` unchanged and passing after the helper refactor; full suite only the 3 known pre-existing failures
- [x] `frontend/tests/password-reset.spec.ts` (3 tests, stubbed) plus `signup.spec.ts`/`auth.spec.ts` passing; lint clean

### Known gaps
- Existing sessions aren't revoked after a reset (no token revocation anywhere yet).
- No rate limiting on `/api/password/forgot`.
- Setup/admin-created accounts have no email, so they can't use this; there's no UI to add an email to an existing account.

## Part 14: Sales Lead Tracking + First Report ✅ COMPLETE (2026-10-13)

### Objective
Turn the board into a Salcomp sales lead tracker: each card is a lead with structured fields, columns are sales stages, and a Reports screen summarizes the pipeline.

### Decisions made with the user
1. Stages: 1st Contact, F2F Meeting, RFI, Proposal, Award (won), Lost.
2. Lead fields: company, contact, deal value + currency, expected close date, lead source, product line (Chargers / Power supplies), assigned salesperson.
3. Cards show a compact lead summary; all fields are edited in an "Edit lead" modal.
4. Salesperson is picked from registered users (a `users` FK), not free text.
5. Report totals are per currency, never converted.
6. The existing `pm.db` board was replaced with the stages + sample leads (users kept; backup in `backend/pm.db.bak`).

### Design
- New nullable `cards` columns. They were first added to older databases automatically at startup; that was removed. `init_db()` now refuses a database whose `cards` table lacks them (`DatabaseSchemaIncomplete`, checked before any write) and an explicit migration is required. That migration script is not implemented yet.
- Allowed values in `backend/lead_options.py`, validated as `Literal` types in `CardUpdate`; partial updates via `model_dump(exclude_unset=True)` (omitted = unchanged, null = cleared).
- `card_rules.apply_card_updates()` is the single write path for REST and AI; member edits to any lead field are logged to `card_edit_history` (salesperson recorded by username).
- `GET /api/lead-options` (any user) and `GET /api/boards/{id}/report` (`backend/reports.py`, filters: salesperson, product line).
- Frontend: `LeadEditModal`, `ReportsPage` (plain CSS bars, no chart library), board columns scroll horizontally.

### Bug found and fixed
- Drag-and-drop silently failed into short columns once one column grew long: `closestCenter` compares centers, and grid-stretched columns put the target's center thousands of pixels away. Switched to `pointerWithin` with a `closestCenter` fallback.

### Tests & Verification
- [x] `backend/test_leads.py` (18 tests): round-trip, validation, clear vs omit, member history, lead options for members, report math (per-currency totals, win rate, filters, closing soon), AI lead actions, column migration
- [x] Backend suite: only the 3 known pre-existing failures
- [x] `frontend/tests/leads.spec.ts` (3 tests, real backend); full e2e suite 41/41 with `--workers=1`
- [x] Live: conversion script run on `pm.db`; report API returns the expected stages and KPIs

### Known gaps
- Time-in-stage and stage-to-stage conversion reports need card-move history, which isn't recorded yet (`CardAction` is still never written) - the natural next step.
- E2E tests share one live database: in parallel (default workers) drag tests can flake when another worker changes the board mid-drag; run with `--workers=1` for a reliable result. Every run also adds test cards to the real board.
- No report export (Excel/PDF) yet.

## Part 15: Undo ✅ COMPLETE (2026-10-13)

### Objective
An Undo button at the top of the board to reverse an unwanted change.

### Decisions made with the user
1. Undoable: card move, card/lead edit, card create, card delete (admin).
2. A member can undo their own change, including putting back a card they moved forward (normally a forbidden backward move). Nobody can undo someone else's change.
3. AI assistant changes are undoable; one AI request = one undo.
4. Single level: the last change only.

### Design
- Server-side `undo_entries` table, one row per user, holding the inverse steps of their last change (`backend/undo.py`). Recorded by every card route and by `ai_kanban.apply_board_actions`; no-op edits/moves don't replace the entry.
- `POST /api/boards/{id}/undo` checks every step first (409 if the card was deleted since, nothing applied) and uses the entry up either way. Edits are reverted through `apply_card_updates`, so a member's undo of an edit is logged like any edit.
- Frontend: sticky Undo bar at the top of `KanbanBoardAPI`, with the entry's description beside it and an "Undone: ..." notice; the board reloads after an undo.

### Tests & Verification
- [x] `backend/test_undo.py` (11 tests): nothing-to-undo, move, member undoing own forward move, lead edit (logged), no-op edit keeps entry, create, delete (fields restored), single level, per-user isolation, conflict after deletion, AI batch in one step
- [x] Backend suite: only the 3 known pre-existing failures
- [x] `frontend/tests/leads.spec.ts` undo test; full e2e 42/42 with `--workers=1`

### Known gaps
- Single level only; column renames aren't undoable.
- Undo restores the user's last state even if someone else changed the same card since (last-writer-wins), except when the card was deleted.
- Undoing a delete recreates the card with a new id; its member-edit history is lost.

## Part 16: Security Hardening (audit P0s) ✅ COMPLETE (2026-10-13)

### Objective
Fix the critical findings of the technical audit before any new feature work: structural authorization, the default JWT key, the public test admin, and the unauthenticated paid AI endpoint.

### Changes
- `routes.py`: `create/update/delete_board` and `create/update/delete_column` use `require_admin`; `POST /api/ai/test` uses `require_admin` (rejected before any OpenRouter call). Card permissions unchanged.
- `ai_kanban.py`: unknown/structural action types are reported as "Unsupported action type" (they were silently ignored; there was never a structural AI path).
- `auth.py`: `load_secret_key()` - no fallback; refuses missing/empty, known-insecure (old default, `.env.example` placeholder) and < 32-char keys, never echoing the value.
- `docker-compose.yml`: `SECRET_KEY=${SECRET_KEY:?...}`; `.env.example` documents generating one without printing it.
- `admin_cli.py` (new): create/promote/demote admins with no-echo or stdin passwords, takeover and last-admin protection, typed confirmation.
- `conftest.py` (new): random `SECRET_KEY` and temp `DATABASE_URL` per pytest run.
- `docs/SECURITY.md` (new): operations guide, including the manual procedure to replace `e2e_admin` in the dev database (not executed).

### Tests & Verification
- [x] `backend/test_security.py`: 42 tests (member 403 on all six structural writes, 401 unauthenticated, admin allowed, AI structural refusal, card rules unchanged, startup refused without/empty/default/placeholder/short key in a subprocess, valid key starts, old-default tokens rejected, no admin via signup/users/setup, CLI provisioning cases, `/api/ai/test` 401/403 without calling OpenRouter and 200 for admin)
- [x] `test_routes.py` board/column tests now run as admin (they assumed members could change structure)
- [x] Full backend suite: 179 passed, only the 3 known pre-existing failures
- [ ] Not run: Playwright e2e (it runs against the dev database), Docker build

### Known gaps
- Frontend still renders column titles as editable for members (backend refuses; UI shows an error per keystroke).
- `e2e_admin` is still the dev database's only admin until the manual replacement procedure in `docs/SECURITY.md` is followed.
- No rate limiting; no token revocation; e2e not isolated from the dev database.

## Part 17: Stable Stage Identity + Isolated E2E ✅ COMPLETE (2026-10-13)

### Objective
Remove two structural risks: (1) reports classified won/lost by column title, so a rename changed the KPIs; (2) Playwright ran against the dev backend and `backend/pm.db`.

### Phase A - stage_kind
- `columns.stage_kind` (`open`/`won`/`lost`, NOT NULL, default `open`) in `models.py`; constants and seed pipeline in `lead_options.py` (`STAGE_KINDS`, `PIPELINE_STAGES`; the title-based `STAGES`/`WON_STAGE`/`LOST_STAGE` are gone).
- `reports.py` and the frontend "overdue" badge use `stage_kind`; `ReportStage` and `ColumnResponse` expose it; the AI context and prompt use it.
- `routes._check_stage_kind`: one won and one lost stage per board; a column's kind can't change while it has cards; renames never touch it. Column writes stay admin-only (Part 16).
- `KanbanColumn.tsx`: title committed on blur/Enter (no request per keystroke), read-only heading for members, won/lost badge.
- Migration: `migrate_stage_kind.py` (dry run by default, read-only; `--apply` with automatic backup, single transaction, integrity check, `--set` for ambiguity). `init_db()` refuses an unmigrated database (`StageKindMigrationRequired`) instead of migrating it implicitly. **Not applied to the dev database**; its dry run classifies Award=won, Lost=lost, 4 others open, no ambiguity.

### Phase B - E2E isolation
- `playwright.config.ts`: own backend (:8001, `PM_E2E=1`, temp-dir SQLite, random `SECRET_KEY`, SMTP/OpenRouter cleared) and frontend (:3001, `distDir` `.next-e2e` via `PM_E2E_DIST_DIR` since `next dev` locks its dist dir); `reuseExistingServer: false`; `workers: 1`.
- Backend `check_e2e_database`: with `PM_E2E=1`, `DATABASE_URL` must be an explicit SQLite file inside the system temp dir (never `pm.db`), else the app won't import. `/api/health` reports `test_mode`.
- `tests/e2e-setup.ts` (global setup for both configs): requires the config's env, `test_mode: true` and the temp DB file; fingerprints both dev `pm.db` files and fails the run if they changed; removes the temp DB afterwards. `tests/helpers.ts` lost its `localhost:8000` fallback.
- Found while validating: (a) a `column-title` testid collided with the tests' `column-*` selector; renamed `stage-title`. (b) dnd-kit ignores a new drag while the previous drop animation runs; `dragCardToColumn` now waits for the `drag-overlay` testid to detach (this was the long-standing "flaky drag" cause, not shared-DB interference alone).

### Tests & Verification
- [x] `backend/test_stages.py` (33): renaming Award/Lost keeps won/lost counts and win rate; other columns renamed "Award"/"Lost" don't become won/lost; unknown titles stay open; closing-soon after rename; seeded kinds; kind validation (422, 409 duplicate, 409 non-empty, allowed change on empty column); member can't rename or reclassify; migration (refusal on unmigrated DB, dry run byte-identical, apply preserves cards/associations + backup, refuses twice, look-alikes stay open with warnings, ambiguity blocked until `--set`, override can't create two won stages, per-board); E2E guard (missing/non-file/in-memory URL, both dev DBs, outside temp, temp accepted; subprocess import refused with default and dev DB, accepted with temp DB)
- [x] Backend suite: 212 passed, only the 3 known pre-existing failures
- [x] Playwright (isolated): 47/47 twice in a row, 0 skipped (setup-flow tests now really run on a fresh DB); dev `backend/pm.db` sha256 identical before/after; temp dirs removed; `playwright.docker.config.ts` refuses with an explicit error
- [x] Lint, type-check, Vitest clean

### Known gaps
- Dev database not migrated yet; the app won't start on it until `migrate_stage_kind.py --apply` is run.
- No admin UI to change a column's `stage_kind` (API only).
- E2E is serial on one shared throwaway board; parallel workers need per-test boards.
- Docker E2E needs a test profile (`PM_E2E=1`, temp DB) before `playwright.docker.config.ts` can run.

## Part 18: Card Stage History ✅ COMPLETE (2026-10-13)

### Objective
Record every commercial stage transition reliably, as the base for future executive KPIs (no dashboard, reports or UI in this part).

### Design decisions
- **Single writer:** an SQLAlchemy `after_flush` listener (`stage_history.py`) records events on the same connection/transaction as the card change. Probed first: `after_flush` sees new cards with ids, `column_id` history and cards deleted by ORM cascades (column/board deletion), so one listener covers REST, AI, undo, cascades and seeding with no per-route code - no path can be missed or double-recorded.
- **Attribution:** `set_stage_context(db, user, source)` - `api` from `deps.get_current_user`, `ai` in `apply_board_actions`, `undo` in `apply_undo`, `seed` at setup, `system` otherwise. UI and direct API calls are indistinguishable server-side, hence `api`.
- **Types from stage_kind:** created, moved (open->open), won, lost, reopened (won/lost->open), deleted, baseline (migration only). Reorders and field edits record nothing.
- **Deletion policy:** hard delete unchanged; history kept via plain-integer `card_id`/`board_id`/column ids (no FKs, no Card relationship), ending in a `deleted` event; stage kinds copied at event time. Soft delete was rejected as an unrequested behavior change.
- **Card id reuse (found by the tests):** SQLite reused a just-deleted top card's id for the next card, which would splice histories (and could make a stale undo step hit the wrong card). A `before_flush` allocator assigns ids above `max(cards.id, card_stage_events.card_id)` - no table rebuild needed.
- **Migration:** `migrate_stage_events.py`, Part 17 pattern (read-only dry run; `--apply` with backup, one transaction, integrity check, refuses twice, requires stage_kind). Existing cards get one `baseline` event at migration time; nothing is back-filled. `init_db()` refuses an existing DB without the table *before* `create_all` (which would otherwise create it empty). Not applied to the dev database; its dry run: 7 cards -> 7 baselines (5 open, 1 won, 1 lost).

### Tests & Verification
- [x] `backend/test_stage_history.py` (35): seed/creation events; open->open move; won; lost; won->lost; admin reopen; lead edit and same-column reorder record nothing; invalid requests (404, member backward 403, 401, member delete 403, 422) record nothing and leave the card; event write failure rolls back move and create; exactly one event per move; AI create/move (source ai, user) and refused AI backward move; undo of move (original kept + compensating), of edit (nothing), of create (deleted), of delete (created, new id), after another user's later move (from actual column); card/column/board deletion keep history ending in `deleted`; no reused ids; chronological query with id tie-break; migration (app refusal, fresh DB accepted, dry run byte-identical, apply baselines + data preserved + backup identical, migrated DB records new events, refuses twice, requires stage_kind, orphan card reported not invented, mid-transaction failure rollback, backup restore)
- [x] Backend suite: 247 passed, only the 3 known pre-existing failures
- [x] Playwright (isolated): 47/47; dev `backend/pm.db` byte-identical throughout

### Known gaps
- Dev database not migrated; the dev backend refuses to start until `migrate_stage_events.py --apply`.
- No endpoint/UI for history; no KPIs built on it yet.
- Baselines carry no past information (by design); KPIs must treat them as "already in stage at tracking start".
- Several moves of the same card within one flush collapse into one net event (only possible inside a single AI request).

## Part 19: Commercial KPI Engine + Read-only API ✅ COMPLETE (2026-10-13)

### Objective
Reliable, tested commercial KPIs over `card_stage_events`, exposed by a read-only API. No dashboard yet.

### Design
- Definitions written first in `docs/KPIS.md` (the contract): flow vs stock, half-open UTC periods, previous-period rule, per-currency money (current card value; deleted cards' values unknown), baseline semantics, stays (complete / partial / current), and one denominator per rate.
- `backend/kpis.py`: service layer independent of HTTP; `load_board_history` = 3 queries, then pure functions (no N+1, deterministic via `(occurred_at, id)` ordering).
- KPIs: won/lost/reopened (events vs distinct opportunities vs `still_in_outcome`), `win_rate_closed` (last outcome in period), creation cohort (size, first outcome, `win_rate`, `closed_win_rate`, `reached` per stage), advancement per open stage, time in stage (complete/partial/current, deletions excluded), velocity (created -> first won/lost; baseline cards excluded and counted), aging (open stock; baseline ages are lower bounds), pipeline (stock per stage and currency). Classic value-weighted "pipeline velocity" deliberately not provided (would mix currencies).
- API: `GET /api/boards/{id}/kpis/{summary,conversion,stage-time,aging,pipeline}`, any authenticated user (same as `/report`), 422 on invalid periods, no user data returned. Existing `/report` unchanged.

### Tests & Verification
- [x] `backend/test_kpis.py` (48): created->won, created->lost, win date vs creation date, reopen after won and after lost, multi-cycle won/lost/reopen (events 2 vs opportunities 1, value once, last vs first outcome), baseline in won stage not a win, open card aging, baseline+move (partial, not in cohort, not an advancement entry), baseline aging lower bound, baseline win excluded from velocity, coverage partial/complete/no_history, multi-stage durations and advancement, advancement categories, deleted card (counted, value unknown, stay ended by deletion, not aging), deleted before outcome in cohort, period boundaries (start included, end excluded), custom end inclusive + previous period, presets, invalid periods, empty period / zero denominators, equal timestamps by id, out-of-order inserts, determinism, currencies (0.1+0.2=0.3 per currency, no mixing, without_value), pipeline distribution, API = service (summary with compare, conversion, stage-time, aging, pipeline), pipeline counts = existing report, invalid filters 422, 401 on all five, 404, reads never write, no user data exposed
- [x] Backend suite: 295 passed, only the 3 known pre-existing failures
- [x] Playwright (isolated): 47/47
- [x] Read-only run against the dev DB (`mode=ro`): coverage partial, no flows, rates null with reasons, 5 open opportunities aged "since tracking start"; file byte-identical

### Known gaps
- Dev history = 7 baselines from 2026-10-13: flow KPIs empty until real moves accumulate.
- Values are current values, not values at win/loss time; no salesperson/product filters on KPIs yet.
- No UI.

## Part 20: Value Snapshots on Stage Events ✅ COMPLETE (2026-10-13)

### Problem
Won/lost money KPIs (Part 19) used each card's *current* value, so editing a value after closing (or deleting the card) rewrote history.

### Decision
- Snapshot the card's value on **every** event type (not only won/lost): one uniform rule in the single writer (`stage_history.py` after_flush), same transaction as the stage change, negligible cost, and `deleted` events now keep the value of a card that is about to disappear.
- Three columns: `deal_value_snapshot` (FLOAT, an exact copy of `cards.deal_value`; money sums stay `Decimal` in `kpis.py`), `deal_currency_snapshot`, `value_snapshot_taken` (distinguishes "recorded, no value" from "unknown").
- KPIs: won/lost `values` = snapshot of each distinct card's **last** won/lost event in the period (reopen + reprice + re-win -> latest price, once); `without_value` (recorded, none) vs `value_unavailable` (no snapshot - never substituted); `current_values` reported separately. Pipeline/aging (stock) keep current values. Event semantics unchanged.
- Part 18 migration (future runs) snapshots baselines, since its baseline timestamp is the migration instant itself.
- Migration `migrate_stage_event_values.py`: Part 17/18 pattern; existing events stay `value_snapshot_taken = 0`; `init_db()` refuses an unmigrated table (`StageEventValuesMigrationRequired`) so the listener never fails mid-operation. Guard added before the model change.

### Tests & Verification
- [x] `backend/test_stage_values.py` (20): won/lost snapshots; later edit keeps closing snapshot (history vs current); each transition records the value of its moment; value edit alone adds no event; deleted event keeps value (won value survives deletion); AI and undo snapshots; one event per transition; currencies separate (0.1+0.2=0.3); no-snapshot events -> `value_unavailable`, not current value; recorded-without-value vs unavailable; reopen+reprice+re-win -> latest value once; API returns historical and current values; migration (app refusal, dry run byte-identical, apply preserves every row/count + invents nothing + backup identical, refuses twice, rollback + restore, requires events table); Part 18 baselines snapshot their own instant
- [x] `test_kpis.py` helper now records snapshots like the app (48/48 unchanged expectations)
- [x] Backend suite: 315 passed, only the 3 known pre-existing failures
- [x] Playwright (isolated): 47/47
- [x] Dev DB: read-only dry run (7 baselines -> value unknown); full apply on a temp copy (7 events, 7 cards, 15 users preserved, 0 values invented); dev file byte-identical

### Known gaps
- Dev database not migrated: the dev backend refuses to start until `migrate_stage_event_values.py --apply`.
- The 7 dev baselines (2026-10-13) will never have a historical value.

## Part 21: Executive Dashboard ✅ COMPLETE (2026-10-13)

### Scope
A dashboard page over the Part 19/20 KPI API. No KPI is computed on the client; no backend change.

### Implementation
- `lib/api.ts`: types mirroring `schemas.py` (`MoneyTotals`, `KpiRate`, `KpiDuration`, `KpiCoverage` with literal statuses, `OutcomeFlow`, `PeriodKpis`, `KpiSummary`, `KpiConversion`, `KpiStageTime`, `KpiAging`, `KpiPipeline`) plus `getKpi*` functions sharing one query builder.
- `lib/format.ts`: `moneyTotalsToNumbers` (reuses `formatTotals`), `formatRate`, `formatDays`, `formatPeriodDates`, `formatDateTime`.
- `components/ReportBlocks.tsx`: `StatTile` (now with an optional `note`), `Section`, `Totals`, `StageBars` and table classes, moved from `ReportsPage` (which now imports them) because the dashboard reuses them.
- `components/DashboardPage.tsx`: filters, coverage banner, headline tiles, closing values table, conversion, time in stage, open pipeline (open stages), aging, data quality. Flow data is fetched per filter change (summary/conversion/stage-time in parallel), stock data (pipeline/aging) once.
- `ProtectedRoute.tsx`: "Dashboard" header button and view, all roles (same access as the KPI API).

### Defects found while validating (fixed)
- Period bounds were printed in local time while labelled UTC; now UTC calendar dates, end inclusive.
- A previous period entirely before tracking start was shown as "0"; now "not tracked".
- After changing filters, the previous period's numbers stayed on screen until the new response arrived (caught by the E2E test); responses now carry the filter key they belong to and are shown only when it matches.

### Tests & Verification
- [x] `frontend/tests/dashboard.spec.ts` (3, isolated backend): a real win through the board shows at its closing value; editing the card's value afterwards leaves the closing value and shows today's value separately; coverage banner, data quality and aging render; previous period "not tracked"; custom range re-queries, hides numbers while incomplete, shows "-" (not 0) for an empty period and the API's unavailable reason; an invalid range shows the API's 422 message and no numbers
- [x] `frontend/src/lib/format.test.ts` (4): money adapter, rate "-" vs 0%, lower-bound days, UTC period dates
- [x] Backend 315 passed / 3 pre-existing failures (unchanged); tsc 17 pre-existing errors in the two legacy test files, 0 elsewhere; lint clean; Vitest 10/10 (6 before); Playwright 50/50 (47 before)
- [x] Screenshots reviewed at 1440px and 390px

### Known gaps
- The five KPI requests are independent (`as_of` may differ by milliseconds between sections).
- No salesperson/product filters (the KPI API has none yet); no export.

## Part 22: Salcomp Bid Import ✅ IMPLEMENTED (steps 1-7 complete, 2026-10-14; run on the real report only as a preview and on disposable copies, Phase 4A-4B.2; the CPM migration and the import of the 52 opportunities were applied to the actual commercial database on 2026-10-16, Phase 4C.8; see "Phase 4C: CPM migration and import" below)

### Scope
Import the Salcomp Bids report (`relatorio-leads.xlsx`) into the pipeline.
- **Step 1 (22A):** the tables, an explicit migration, the startup guard and exact money handling.
- **Step 2 (22B):** the Excel reader and structural validation.
- **Step 3 (22C):** the commercial mapping, classification, `source_key`, duplicate/re-import detection and the preview engine.
- **Step 4 (22D):** the transactional import service (batch, cards, Bid details, source refs, `baseline` events) and `card_source_refs.source_confidence`.
- **Step 5 (22E):** the admin-only HTTP endpoints: upload + preview, confirmation, batch history and batch details.
- **Step 6 (22F):** the admin-only React screen over those endpoints (2026-10-14).
- **Step 7 (22G):** Playwright E2E for the screen (below).
- **Not done as of 2026-10-14 (superseded: the CPM migration and the import were applied on 2026-10-16, Phase 4C.8; only activation remains not done):** the clean commercial database was prepared on 2026-10-15 (Phase 3) and the real report was previewed and rehearsed on disposable copies in Phase 4A-4B.2. Still not done: the CPM migration, the import and the activation on the actual database.

### Implementation (step 1)
- `models.py`: `ImportBatch` (`import_batches`, per-board import metadata), `CardBidDetails` (`card_bid_details`, 1:1 with a card) and `CardSourceRef` (`card_source_refs`, at most one per card). The database enforces UNIQUE (`source_system`, `source_key`).
  - Cascades: card -> details/source ref; board -> batches.
  - SET NULL: user -> `import_batches.user_id`; batch -> `card_source_refs.last_import_batch_id`.
- Started by Codex (interrupted by a usage limit, never committed), reviewed and completed here.
- `bid_money.py`: revenue is stored as INTEGER **cents**.
  - The first draft used `NUMERIC(18, 2)`. SQLite rounds that past about 15 significant digits (`12345678901234567.89` came back as `12345678901234568`).
  - `reais_to_cents` goes through `Decimal` only. It refuses floats, more than two decimal places, negatives, NaN/Infinity, non-plain text and anything above R$ 9,999,999,999,999,999.99. It never rounds.
  - `None` stays `None` (not informed), never 0.
  - `cents_to_reais` is for display. Revenue is never mixed with `cards.deal_value`.
- `import_schema.py`: the expected schema is read back via PRAGMA from an in-memory database built with the model-generated DDL, so nothing is hand-copied. It covers columns (type, NOT NULL, default, pk), indexes (columns, unique, origin) and foreign keys (target, ON DELETE/UPDATE).
  - `import_schema_state()` returns `absent`, `partial`, `incompatible` or `complete`, with every difference listed.
  - Shared by the migration and `init_db()`, so both accept exactly the same schema.
- `migrate_import_tables.py`:
  - **Target and dry run:** `--database` is required (no `DATABASE_URL`/`./pm.db` fallback). Dry run on a read-only connection by default.
  - **Prerequisites:** refuses unless the three earlier migrations are applied, naming the missing script.
  - **Refusals:** partial or incompatible import tables, a second `--apply`, and missing or non-app files.
  - **Backup and transaction:** writes `<name>-pre-import-tables-<time>.db.bak`, then works in one transaction. It re-checks absence inside the transaction, creates the tables, verifies them with the shared check, and verifies every row of every existing table is unchanged, rolling back otherwise.
- `database.py`: `init_db()` checks the import tables once, before `create_all`. The redundant second call is gone.
  - An empty database gets everything from `create_all`.
  - None of the tables: `ImportTablesMigrationRequired`.
  - Partial or incompatible tables: `ImportTablesSchemaError`, listing the differences.

### Implementation (step 2: Excel reader, 22B)
- **Dependencies:** `openpyxl==3.1.5` and `defusedxml==0.7.1`, declared in `backend/pyproject.toml` / `uv.lock`.
  - openpyxl's own docs say it doesn't guard against XML bombs without defusedxml.
  - With defusedxml installed, openpyxl parses through it, and the reader refuses to run if it's missing.
  - No upload dependency (`python-multipart`) was added.
- **`bid_excel_reader.py`:** `read_bid_excel(data, filename, limits)` (bytes, for the future upload) and `read_bid_excel_file(path)` return a `BidReadResult` and touch nothing else. It has no database imports, writes nothing and logs nothing. The result carries:
  - `FileMetadata`: name, size, SHA-256, uncompressed size;
  - `SheetInfo`: sheet, headers, mapping, missing optional and ignored columns;
  - one `BidRow` per non-blank row: the original Excel row number, `RawRow` (the cells as read, for the preview), `BidRecord` (normalized), errors, warnings and `empty_fields`;
  - `file_errors` / `file_warnings` and a `ReadSummary`.

  A later preview can use it without re-reading the file.
- **Three severities:**
  - **File errors:** wrong or corrupted format, a limit exceeded, no Bid sheet, missing/duplicate/blank headers, no data rows. They yield no rows at all.
  - **Row errors:** the row is kept but flagged invalid, the field is None, and reading continues.
  - **Warnings.**

  Empty optional fields are listed, neither errors nor 0. Messages name the row, the column and the reason, never the cell value. Money errors use `InvalidMoneyValue.reason`, added to `bid_money.py` for this.
- **File checks**, all before any XML is parsed:
  - **Name and type:** `.xlsx` only, case-insensitive. `.xlsm`, `.xls` and other extensions are refused.
  - **Real content:** OLE2/`.xls` signatures, non-zip and corrupted zips are refused. `[Content_Types].xml` must declare the plain spreadsheet main part; a macro-enabled content type renamed to `.xlsx` is refused, as is any `vbaProject.bin` and any encrypted member.
  - **Size limits** (configurable `ReaderLimits`): file 10 MB, declared uncompressed total 100 MB, compression ratio 200x per member above 1 MiB, 1,000 zip entries, 200 columns, 10,000 characters per text cell, 5,000 data rows.
  - **Loading:** `keep_vba=False`, `keep_links=False`, `read_only`, `data_only`.
  - **Unsafe XML:** DTD entities and external entities are refused (`unsafe_xml`). openpyxl wraps defusedxml's refusal in a plain `ValueError`, so the reader walks the exception chain to report it correctly.
- **Exact numbers:** `_ExactCellParser` extends openpyxl's `WorkSheetParser`, the class its read-only worksheets use.
  - Numeric cells keep their XML text as `Decimal`; openpyxl would cast `"9999999999999999.99"` to a float, `1e16`.
  - A formula without a cached value is marked and becomes a row error. Excel error values (`#N/A`) are row errors too.
  - This relies on openpyxl internals (`_reader`, `_get_source`, `_cell_styles`), hence the exact version pin.
- **Sheet and headers:**
  - **Finding the sheet:** the sheet is the one whose row 1 holds the five required headers (`Código`, `CAT`, `Estágio`, `Cliente`, `Nome do projeto`). Zero matches or more than one is a file error.
  - **Matching:** case-insensitive, with whitespace collapsed.
  - **Missing optional column:** a file warning, and the field stays empty.
  - **Unknown column:** a warning, and it is ignored.
- **Value rules:** see the column table in `docs/DATABASE.md`.
  - **Absent values:** `-` / blank mean absent, never 0. `Código`, `CAT`, `Estágio` and `Cliente` must have values.
  - **Kept as written:** `CAT`, `Estágio`, `Budget habilitado` and `Budget` (no currency is stated, so it isn't interpreted). People (`CPM`, `Líder do projeto`) stay text.
  - **Codes:**
    - text codes are kept, leading zeros included;
    - a numeric code is zero-padded only when its cell format is all zeros (`000000`, unambiguous), with a warning;
    - any other numeric code keeps its digits and is flagged `code_needs_review`, without inventing zeros;
    - an unusual format (not `999999-99`) is a warning.
  - **Dates:** text `dd/mm/yyyy` (validated calendar dates) or Excel date cells, between 1900 and 2100. Numbers without a date format, ISO text and impossible dates are row errors.
  - **Volumes:** whole numbers from the Decimal (`1.5E3` -> 1500, plain-digit text accepted; fractions, negatives and `1.500` are rejected). A volume of 1 is kept, with a warning.
  - **Revenue:** `Decimal` -> `reais_to_cents` (BRL). Negatives, more than two decimals, out-of-range values and `1.234,56` are row errors.
  - **Confidence:** `"LOW (10%)"` -> label + 10; also `"70%"` or a whole number from 0 to 100.
  - **Not converted:** `Data de criação` doesn't become the card's creation date, `Deadline` doesn't become the expected close date, and revenue doesn't become `deal_value`.
- **Real report** (`relatorio-leads.xlsx`, read from an isolated copy; only counts printed):
  - sheet "Leads", 31 columns, all mapped;
  - 52 rows, all valid, no file warnings;
  - 17 `volume_one` warnings (16 current year, 1 next year);
  - 15/22 revenues present (current/next year);
  - confidence 10/50/85 %;
  - no code needing review.

### Implementation (step 3: mapping and preview, 22C)
- **`bid_import_preview.py`** (`build_import_preview(read_result, stages, existing=(), decisions=())`) is a pure function: no database import, no I/O, nothing written (`ImportPreview.written` is always False). The future endpoints load the board stages and the existing imports, and pass them in.
- **Stage rules** (`STAGE_RULES`), compared after `normalize_label` (NFKC, whitespace collapsed, casefold). The original CAT/Estágio text is kept everywhere: in the row, the classification and `source_ref`.

  | CAT | Estágio | Result |
  |-----|---------|--------|
  | WON | AWARD GIVEN | Award (the board's `won` stage) |
  | LOST | NOT AWARDED BY CUSTOMER | Lost (the board's `lost` stage) |
  | OPEN / Open CE | SALES EVALUATION | F2F Meeting |
  | OPEN / Open CE | INTERNAL TECHNICAL EVALUATION | RFI |
  | OPEN / Open CE | RFQ | Proposal |
  | CLOSED | NOT AWARDED BY CUSTOMER | `needs_confirmation`: Lost proposed, applied only with `confirm_proposed_stage` |
  | CLOSED | CLOSED (BY SALCOMP) | `needs_review`: no stage until `target_stage` is chosen |
  | other combinations of known values (e.g. WON/LOST/CLOSED + RFQ) | | `inconsistent`: no stage |
  | an unknown CAT or Estágio | | `unknown`: no stage |

  - **Open CE** maps like OPEN but stays distinct and carries a warning: its meaning is not confirmed.
  - **Finding the target column:** Award/Lost by `stage_kind`, so renaming them changes nothing. The open stages by title; a missing one sends the row to review.
  - **Stages and kinds:** no stage is created and no `stage_kind` changes. The board must have exactly one won and one lost stage.
- **`bid_source_key(code, customer)`** returns `v1:` followed by the JSON array `[code, customer]`.
  - **Normalization of each part:** NFKD, combining marks (accents) dropped, then NFC, whitespace collapsed and casefold. "São Paulo" = "SAO  PAULO", and leading zeros are kept.
  - **Why JSON:** the encoding makes `"A|B"+"C"` and `"A"+"B|C"` (and quotes, commas, spaces) different keys.
  - **Why accents are dropped:** dropping them can only merge spellings, so two rows that might be the same customer go to review as duplicates instead of becoming two cards.
  - **Uniqueness:** the database still enforces it in `card_source_refs`.
  - **Real report:** 52 rows, 49 codes and 52 distinct keys, so no duplicate.
- **`bid_row_hash(record)`:** `v1:` + SHA-256 of every reader field except the row position. Unmapped fields count, so any change in the source is detected.
- **Field mapping** (constants; tests check every target is a real model column):
  - **Card:** `title` <- Nome do projeto, `details` <- Descrição da prospecção, `company` <- Cliente.
  - **`card_bid_details`:** plant, customer_type (never `lead_source`), business_type, product_category, product, solution_type, confidence (percent), volumes, production_start_date, revenue cents + BRL, status_note, project_lead (text), next_step, next_step_due (Deadline), close_reason (Status da conclusão), close_reason_note (Razão).
  - **`card_source_refs`:** system, key, Código, CAT, Estágio, Data de criação / Atualizado em / Última edição as source dates, and the row hash.
  - **Not mapped (`not_mapped`, shown but never imported):** Ano, Budget habilitado, Budget, CPM. The original confidence label was here too until 22D added `source_ref.source_confidence`.
  - **Never set:** `deal_value`, `currency`, `expected_close_date`, `lead_source`, `product_line`, `contact`, `salesperson_id`, the card's creation date. No user is created or matched, no currency is converted, and None stays None.
- **Action per row**, checked in this order:
  1. a `skip` decision -> **ignore**;
  2. reader errors, a code flagged for review or no project name -> **review** (`fix_source_row`);
  3. the same key on another non-skipped row -> **review** (`resolve_duplicate`; skipping all but one resolves it);
  4. a new key -> **create** if the stage is determined, else **review** (`confirm_stage` / `choose_stage`);
  5. an existing key:
     - same hash -> **ignore** (unchanged, even if the card was moved in the app);
     - stage undetermined -> **review**;
     - newer source (max of Atualizado em / Última edição) -> **update**, awaiting `confirm_update`, with `changed_fields`;
     - older source -> **ignore**, never overwritten without `confirm_update`;
     - undecidable dates (missing, or equal with different content) -> **review** until `confirm_update`;
     - a different target stage -> `stage_change` (current, proposed); the card only moves with `confirm_move`.
- **`RowDecision(row_number, target_stage, confirm_proposed_stage, skip, confirm_update, confirm_move)`:** explicit choices from a later step, never presumed. Unknown rows, unknown stage titles and two decisions for one row raise `ValueError`.
- **`PreviewSummary`:**
  - totals, valid rows, rows with errors and rows with warnings;
  - creates (resolved only), updates (and how many still need confirmation), unchanged, internal duplicate rows, pending review, ignored;
  - distribution by target stage, classification and action;
  - revenue of create/update rows per currency, current and next year apart, in cents.
- **Real report** (isolated copy, empty board, no existing imports; counts only):
  - 52 rows: 29 create, 23 review (18 `choose_stage`, 5 `confirm_stage`);
  - classification: 29 mapped, 12 needs_review (CLOSED BY SALCOMP), 5 needs_confirmation (CLOSED + NOT AWARDED), 6 inconsistent (WON+RFQ 3, CLOSED+RFQ 2, LOST+RFQ 1);
  - target stages: Proposal 15, F2F Meeting 5, RFI 3, Award 3, Lost 3;
  - no duplicates.

### Implementation (step 4: transactional import, 22D)
- **`card_source_refs.source_confidence`** (VARCHAR, nullable) keeps the original confidence label ("LOW (10%)"); `card_bid_details.confidence` keeps the percentage.
  - The 22A migration, `import_schema` and docs follow the model automatically.
  - No database had the tables yet, so no extra migration was needed.
  - Verified on a temp copy of the dev DB: the migration creates the column and `init_db()` accepts the result.
  - In the preview, the label moved from `not_mapped` to `source_ref["source_confidence"]`.
- **Preview hardening:** a decision that doesn't fit its row gets a `decision_not_applicable` reason (error) and the row goes to review. It is never silently ignored or turned into another action. The cases:
  - `skip` combined with anything else;
  - `target_stage` together with `confirm_proposed_stage`;
  - confirming a stage where nothing is proposed;
  - choosing a stage for a row that must be fixed;
  - `confirm_update` with nothing changed to update;
  - `confirm_move` with no stage change, or without the update confirmed.
- **`stage_history.py`:**
  - `SOURCE_IMPORT = "import"`;
  - while a session's source is `import`, an inserted card gets a `baseline` event instead of `created`. The listener stays the single writer, so no event is written by the service and none can be duplicated;
  - `stage_context()` sets the source for a block and restores the previous one.
- **`bid_import_service.py`:**
  - `preview_bid_import(db, read_result, board_id, decisions)` (read-only) loads the board's stages and the already-imported cards. A key imported into another board raises `ImportConflict`.
  - `apply_bid_import(db, read_result, preview, decisions, board_id, user)`:
    1. refuses a dirty session, a file with errors or a preview of another file;
    2. in one transaction on the caller's session, inserts the batch first (taking SQLite's write lock), rebuilds the preview from the database and refuses (`ImportConflict`) unless it equals the confirmed one;
    3. refuses unfit decisions (`ImportRejected`);
    4. writes: CREATE rows -> card (appended to the decided column) + details + source ref; confirmed UPDATE rows -> mapped fields only, plus a move only if `confirm_move` was given;
    5. counts the rows, marks the batch `completed` and commits.

    Any exception rolls everything back; a unique violation on `card_source_refs` becomes `ImportConflict`.
  - **Repeated confirmation:** the old preview no longer matches (the rows are now unchanged), so it's refused and nothing is duplicated.
  - **`mapping_json`:** rule versions, stage rules, board stages and decisions; no row content and no file.
  - **Users:** no user is created and no salesperson is assigned.
  - **Ownership of the transaction:** the service commits or rolls back the caller's session itself, like the routes do.

### Tests & Verification
- [x] `backend/test_bid_import_service.py` (36 cases), on temp SQLite files with the full schema and synthetic workbooks:
  - **Creating:** a full batch (counts, status, metadata, `mapping_json` without row content, cards in Proposal/Award/Lost); card fields (no unmapped card field set, appended after existing cards) and Bid details; no user created; source refs (key, zeros, status/stage, `source_confidence`, dates, hash, batch).
  - **Events:** exactly one `baseline` per created card (source `import`, user, import time) and no created/won/lost/moved, including a card created in Award; the stage context is restored afterwards.
  - **Rejections:** pending rows not created; 3 unfit decisions reject everything unchanged; invalid decisions; decisions differing from the confirmed preview; internal duplicates; a file with errors; another file's preview; unknown board or user; a dirty session; a key imported into another board.
  - **Updates:** confirmed update (mapped fields changed; deal value, currency, contact, expected close, salesperson, column and events untouched; counts); unconfirmed update not applied; a stage change not applied without `confirm_move`; a confirmed move records a real `moved` event; an unchanged re-import is skipped, not updated.
  - **Idempotency and conflicts:** a repeated confirmation is refused with the database unchanged, and a fresh preview finds only unchanged rows; a stale read that would duplicate a key is stopped by the unique constraint, leaving nothing.
  - **Rollback:** injected failures inserting details, the first or second source ref, the second card, the baseline event, and after all rows - each leaves the database row-identical. Nothing is visible to another connection before the commit.
  - **Integrity and the dev DB:** `foreign_key_check` and `integrity_check` pass; the manual card is untouched; deleting an imported card cascades its details/ref and keeps its history; `backend/pm.db` is unchanged.
- [x] `backend/test_bid_import_preview.py` (+9 for 22D): `source_confidence`, unfit decisions flagged, a move needing the update confirmed.
- [x] `backend/test_bid_import_preview.py` (85 cases), on synthetic records and workbooks (fictitious data):
  - **Classification:** every approved rule; Open CE distinct with its warning; normalization only for comparison (originals kept everywhere); CLOSED + NOT AWARDED only with explicit confirmation; CLOSED (BY SALCOMP) only with an explicit stage; 10 inconsistent and 3 unknown combinations; won/lost by `stage_kind` after renaming; missing target stage; board validation.
  - **Source key:** format, leading zeros, spacing/case/accents/NFD, 5 concatenation-collision pairs, same code with another customer, required parts, source system.
  - **Duplicates:** detection across case/spacing, resolution by skipping, same code with different customers.
  - **Re-import:** new; unchanged (also after a move in the app); newer -> update awaiting confirmation with changed fields; datetime vs date; older -> ignored unless confirmed; 3 undecidable-date cases; last-edited date; a stage change needs `confirm_move`; a status change on the same stage; undetermined stage; unique existing keys.
  - **Blocked rows:** invalid (stage decisions don't unblock it, `skip` does), invalid without a key, code needing review, missing title, file errors, invalid decisions.
  - **Fields:** card fields, every target a real column, never-mapped card fields, revenue in cents and never `deal_value`, full bid details, None kept, source ref preserved, people as text with no user field, Deadline not an expected close date, row hash.
  - **Summary:** coherent with the rows and per currency; determinism.
  - **End to end and side effects:** from a workbook; no database modules loaded and the test database unchanged.
- [x] `backend/test_bid_excel_reader.py` (106 cases), on synthetic workbooks built in memory (fictitious data, no database):
  - **Valid files:** the 31 columns mapped and typed; metadata; CAT/stage kept; header case and spaces; sheet found among others; read from a path.
  - **Dates:** text and Excel dates; impossible, ISO and unformatted-number dates.
  - **Money:** exact cents, including values read from the cell text that a float would lose; invalid amounts reported without the value.
  - **Absent and numeric values:** `-`/blank/empty -> None; volume 1 kept with a warning; whole-number volumes; confidence; year; numbers in text columns; text length; Excel errors; booleans.
  - **Codes:** leading zeros, a zero-padded numeric format, a numeric code flagged for review, an unusual code, required values.
  - **Rows:** one invalid row doesn't stop the others; formulas with and without cached values; blank rows counted; a row of dashes reported; values outside the headers; messages never contain cell values.
  - **Structure:** required/optional/unknown/duplicate/blank headers, no Bid sheet, two Bid sheets, headers not in row 1, no data rows, row and column limits.
  - **Format:** extensions, empty file, CSV and `.xls` renamed, garbage zip, truncated file, zip without a workbook, corrupted sheet, macro content type, `vbaProject.bin`, file/uncompressed/entry limits, a zip-bomb member.
  - **Unsafe XML:** entity expansion and external entities in four package parts.
  - **No side effects:** in a separate interpreter, the reader loads neither `sqlalchemy`, `database`, `models` nor `sqlite3`, and the test database is unchanged.
- [x] `backend/test_bid_money.py` (39 cases):
  - two-decimal, large and maximum values;
  - the value NUMERIC lost;
  - rejection of extra decimals, excess digits, negatives, NaN, localized text and floats/bools;
  - None/0 handling and round trips.
- [x] `backend/test_import_tables.py` (33), all on temp files under `tmp_path`:
  - **Model constraints:** 1:1 details, one source ref per card, DB-level key uniqueness per source system, FK required, cascades/SET NULL, batch defaults.
  - **Money in the model:** exact cents round trip at the maximum; revenue not touching `deal_value`.
  - **Schema states:** absent/partial/incompatible, covering a missing or extra index, an extra or missing column, a wrong type and a wrong FK rule.
  - **Migration:** `--database` required; dry run byte-identical with no backup; apply creates empty tables, keeps every row, passes the integrity check and backs up the original content; the migrated database starts.
  - **Migration refusals:** second run (apply refused, dry run "nothing to do"); each out-of-order prerequisite; non-app and missing files; partial and incompatible schemas, all unchanged and without backup; a failure inside the transaction rolls back.
  - **`init_db()`:** a new database gets the full schema; an absent, partial or incompatible schema is refused without writing (`create_all` never runs).
- [x] Backend suite: 387 passed after step 1; 493 after step 2; 578 after step 3; 623 passed, 1 skipped after step 4. Only the 3 known pre-existing failures remain. Only the 3 known pre-existing failures remain (`test_ai.py` x2, `test_static_files.py`).
- [x] Temp copy of the dev DB:
  - dry run: 15 users, 1 board, 7 cards;
  - apply: the 9 existing tables are row-identical, the 3 new ones are empty, the integrity check is ok and the backup was written;
  - second apply refused; `init_db()` accepts the migrated copy and refuses an unmigrated one.
- [x] `backend/pm.db` byte-identical (SHA-256) before and after; not migrated.

### Known gaps
- **The dev database is not migrated.** The backend refuses to start on `backend/pm.db` (`ImportTablesMigrationRequired`) until an operator runs `migrate_import_tables.py --database "$PWD/pm.db" --apply` (or the planned clean Salcomp database replaces it).
- **Planned, not built:**
  - the clean `pm-salcomp.db` bootstrap.
- **Open decisions:**
  - the meaning of "Open CE" (mapped like OPEN, with a warning);
  - whether the 6 inconsistent rows, the 12 CLOSED (BY SALCOMP) and the 5 CLOSED + NOT AWARDED rows get standing rules or per-row decisions;
  - CPM -> user mapping;
  - whether revenue is always BRL (`revenue_currency` defaults to BRL as an assumption).
- **Undo:**
  - undoing the deletion of a card recreates it without its `card_bid_details`/`card_source_refs`;
  - ~~a pending undo could overwrite fields an import updated since~~ - fixed in the 22D follow-up (undo checks `expect`).
- **Import service:**
  - an import updates card fields directly, not through `card_rules`: its changes go to `card_import_changes` (option B, approved), never to the member history;
  - ~~a failed import leaves no batch row~~ - recorded since the 22D follow-up;
  - a preview that names a key already imported into another board is refused as a whole, rather than per row (by design: the key is unique across boards).
- **Reader:**
  - it depends on openpyxl 3.1.5 internals; upgrading openpyxl means re-running `test_bid_excel_reader.py`;
  - confidence is stored as a percentage only when one can be read;
  - a percent-formatted number such as `0.5` is rejected rather than guessed;
  - the volume-1 warning also covers next year's volume.
- **Older migration scripts** (`migrate_stage_kind.py`, `migrate_stage_events.py`, `migrate_stage_event_values.py`) still fall back to `DATABASE_URL`/`./pm.db` when `--database` is omitted (dry run by default). Only the new script requires it.

### 22D follow-up: undo safety, failed attempts, cross-board keys (2026-10-14)
- **Undo vs later changes (fixed):**
  - **Root cause:** undo steps stored only the old values and restored them unconditionally. A pending undo overwrote any later change to the same card, from an import, another user or the AI. Only a deleted card or column was detected.
  - **Fix:** the existing mechanism, with no second undo and no schema change. Each card step records `expect`, the state the change left the card in (`undo_move(card, to_column_id)`, `undo_update`, `undo_create`), and `apply_undo` compares it with the card first, merged per card in step order.
  - **On a mismatch:** HTTP 409 with a clear message. Like any refused undo, the entry is used up; the later change is kept.
  - **Old entries:** entries recorded before `expect` existed are refused (they can't be checked).
  - **What doesn't block:** an edit to another field doesn't block undo, and neither does an unchanged re-import.
  - **Behavior change:** this applies to every later change, not only imports. `test_stage_history.py::test_undo_after_someone_else_moved_the_card` asserted the old behavior (a member's undo overwriting the admin's later move) and now asserts the refusal. Restricting the check to import changes only would be the alternative.
- **Failed attempts (recorded):**
  - `import_batches.failure_kind` (`validation` / `conflict` / `unexpected`) and `failure_reason` (at most 500 characters, no cell content; only the exception type for unexpected failures). These are new nullable columns in a table no database has yet, so the 22A migration and `import_schema` follow automatically.
  - `apply_bid_import` rolls back, then `record_failed_import` writes a `failed` batch in its own transaction on a separate session.
  - A recording failure is only logged and the original error is re-raised. A success is never recorded as failed, and an unknown board gets no record.
  - **Bug found by the tests:** reading the user's id after the failure loaded an expired `User`, which autoflushed the caller's pending changes and held SQLite's write lock, so the recording session timed out. The id is now read up front under `no_autoflush`, and the caller's pending work is left untouched.
- **Cross-board keys:** unchanged rule (the key is unique across all boards).
  - The `ImportConflict` message now names the Códigos and their board and says how to proceed.
  - At apply time, the import is rolled back entirely and the attempt is recorded as `conflict`.
- **Card edit history for import updates:** decided as option B and implemented in the second follow-up below.
  - **Why it can't go in as is:** `card_edit_history` backs the "M" badge (`Card.has_member_edits = bool(edit_history)`, "a member changed this"). Writing import updates there would show the badge, i.e. simulate a member edit, and the table has no column for the origin.
  - **Alternatives:**
    - **(A)** add `source` (default `member`) to `card_edit_history`, filter `has_member_edits` and the "M" badge to `member`, and log import diffs with source `import` and the importing user. It reuses the existing history endpoint/modal, but alters a table that already exists in `backend/pm.db`, so it needs an explicit migration (or folding it into `migrate_import_tables.py`, which no database has run yet) plus a startup guard.
    - **(B)** a new `card_import_changes` table (card, batch, changes JSON), part of the unapplied import tables. Existing tables and the badge are untouched, and the user comes through the batch, but it's a second history the UI would show separately.
    - **(C)** no per-card history: the batch already records who/when/which file, and `source_ref.last_import_batch_id` links the card; only the field diff is lost.
- **Tests:** `test_bid_import_undo.py` (10) covers:
  - normal undo of an imported card;
  - an unchanged re-import not blocking undo;
  - undo refused after an import updated the same field;
  - undo of another field still working after an import;
  - a move undo refused after an import move;
  - refused after another user's edit;
  - undo of a creation refused after someone else's edit;
  - an AI request editing one card twice still undoable;
  - old entries refused;
  - imports writing no undo entry.

  `test_bid_import_service.py` (+7) covers:
  - no failure record on success;
  - a validation failure recorded without row content;
  - an unexpected failure storing only its type;
  - a failed recording never hiding the error or writing data;
  - the caller's pending work kept;
  - no record for an unknown board;
  - the cross-board conflict explained with nothing written.

  The rollback tests now compare all commercial tables and check the `failed` record.
- **Verification:** backend 640 passed, 1 skipped; only the 3 known pre-existing failures remain. Playwright `leads.spec.ts` "undoes the last move" passed against the isolated backend. `backend/pm.db` byte-identical.

### 22D follow-up 2: import history and undo coverage (2026-10-14)
- **Decisions approved:**
  - **Import history:** option B, a separate `card_import_changes` table. `card_edit_history` and the "M" badge are unchanged.
  - **Undo protection:** stays broad and applies to any later change, whatever its origin.
- **`card_import_changes`:** card (CASCADE), batch (CASCADE), user (SET NULL), `changes` JSON and `created_at`, with UNIQUE (`card_id`, `import_batch_id`). `Card.import_changes` and `ImportBatch.card_changes` are relationships separate from `Card.edit_history`, so `has_member_edits` can't see import history.
- **Service:**
  - **Diff:** for a confirmed UPDATE, `_write_rows` snapshots the managed fields (card, Bid details, source ref except the row hash) from the objects before and after applying the row, and stores only the differences, with old/new as JSON values.
  - **No-op updates:** an update that changes no managed field (e.g. only CPM differed: new hash, nothing else) records nothing.
  - **Scope:** creations, unchanged/older/skipped/pending/unconfirmed rows and moves are never recorded. Moves are in `card_stage_events`.
  - **Transaction:** same transaction as the update; a failure rolls both back (and records the failed attempt as before).
- **Migration and schema:** the table is the fourth import table, so `import_schema`, `migrate_import_tables.py` and the `init_db()` guard cover it automatically (dry run, apply with backup, refusal of partial/incompatible schemas and second runs). No database has these tables yet.
  - **Temp copy of the dev DB:** the dry run lists 4 tables; apply creates them; a second apply is refused and a second dry run says "nothing to do"; the `card_edit_history` DDL and rows are identical; `init_db()` accepts the result.
- **Undo scenarios covered:**
  - normal undo;
  - another user's later edit (refused);
  - the AI's later edit (refused);
  - an import's later update (refused) and an unrelated field (allowed);
  - a stage move after an edit (allowed: the move doesn't touch the edited field);
  - a move after a move (refused);
  - an AI request editing one card twice (allowed);
  - old entries without `expect` (refused).

  Refusals keep the existing 409 behavior and nothing is mass-deleted.
- **Tests:**
  - `test_bid_import_service.py` (+9 import-history cases):
    - exact old/new values, batch and user;
    - values read from the database (an app edit since the last import shows as the old value);
    - no rows for new/unchanged/skipped/unconfirmed/older rows;
    - none for a no-op update;
    - one row per card per batch;
    - moves kept out;
    - rollback together with the update;
    - member history and the "M" badge untouched;
    - referential integrity.
  - `test_import_tables.py` (+5): links and relationships, uniqueness per card and batch, delete rules, a required batch, and the migration leaving `card_edit_history` DDL and rows identical.
  - `test_bid_import_undo.py` (+3): the AI's later change, an edit surviving a later move, a move after a move.
- **Verification:** backend 657 passed, 1 skipped; only the 3 known pre-existing failures remain. `backend/pm.db` byte-identical; no migration applied to it.
- **Limitations:**
  - import history goes with its card when the card is deleted, like `card_edit_history`; stage events are kept;
  - there is no endpoint or UI to show it yet.

### Implementation (step 5: HTTP endpoints, 22E, 2026-10-14)
- **Where:** `backend/import_routes.py` (its own `APIRouter`, prefix `/api`, included by `main.py`), request/response models in `schemas.py` (`ImportDecision`, `ImportBatchResponse`, `ImportBatchDetail`, `ImportBatchList`, `ImportResult`). The routes parse, check and translate errors only: reading is `bid_excel_reader`, the rules are `bid_import_preview`, every write and the transaction are `bid_import_service`.
- **Endpoints (all `Depends(require_admin)`; 401 without a valid token, 403 for members):**
  - `POST /api/boards/{id}/imports/preview` - multipart: `file` (.xlsx), `decisions` (JSON array). Returns the file metadata, sheet info, file warnings, reader summary, preview summary and every classified row (action, classification, reasons, errors, warnings, pending decisions, mapped fields, duplicates, existing card, changed fields, stage change), plus `confirmable`, `preview_token` and `expires_in_minutes`. Writes nothing - not even an `import_batches` row.
  - `POST /api/boards/{id}/imports` - multipart: the same `file` and `decisions`, `preview_token`, `confirm=true`. 201 with the batch and the created/updated/moved card ids and skipped/review counts.
  - `GET /api/boards/{id}/imports?limit=50&offset=0` - this board's batches (completed and failed), `created_at DESC, id DESC`, with `total`; `limit` 1-200.
  - `GET /api/imports/{batch_id}` - metadata, state, counts, `failure_kind`/`failure_reason`, the parsed `mapping_json` and `card_changes_count`.
- **Decisions** (`ImportDecision`, strict, `extra="forbid"`): `row_number` (>= 2), `target_stage` (a column title), `confirm_proposed_stage`, `skip`, `confirm_update`, `confirm_move`. No card id, column id or source key is accepted from the client: the engine resolves rows, stages and existing cards itself.
- **Confirmation binding.** The preview token is a `create_purpose_token` JWT (purpose `bid_import_confirm`, 30 minutes; `verify_token` refuses it as a login token) naming the admin, the board, the file's SHA-256, a hash of the decisions, the board's latest completed batch id and a hash of the whole preview shown (file name excluded). Confirming:
  1. requires `confirm=true` and a token for this admin and board;
  2. re-reads the uploaded file and checks its SHA-256 and the decisions against the token (422 otherwise);
  3. refuses (409) if another import completed on the board since - which is also what refuses a repeated confirmation, even of a file that would write nothing;
  4. rebuilds the preview from the current database and refuses (409) unless its hash matches;
  5. calls `apply_bid_import`, which rebuilds it once more under the write lock.

  Errors in steps 1-4 write nothing, not even a failed batch; from step 5 on the service records failures as before.
- **Upload safety:**
  - the form is parsed only after the admin check and a Content-Length check (411 without it, 413 above the reader's 10 MiB plus 1 MiB of form overhead), with at most 1 file and 3 fields; at most `max_file_bytes + 1` bytes of the file are read, so the reader refuses anything larger (413);
  - the format is decided by the reader from the bytes (extension, magic, zip structure, content types), never by the part's Content-Type;
  - the bytes stay in memory; Starlette spools a part over 1 MiB to an anonymous temporary file that disappears when the request ends; only the name and SHA-256 are persisted (in `import_batches`).
- **Errors:** an unreadable file is 422 with `detail.file_errors` (reader codes and messages, never cell values); `ImportRejected` is 422, `ImportConflict` 409; anything else is 500 with a fixed message (logged by exception type only, no stack trace or SQL in the response), after the service's rollback and failed-batch record.
- **What responses leave out:** the CPM name (read, never imported) is dropped from the preview's `not_mapped`; batches name the user by `user_id` and `username` only (no email or full name); no batch response contains row content.
- **Dependency:** `python-multipart==0.0.20` (Starlette's form parser), declared in `pyproject.toml` and `uv.lock` via `uv add`.
- **History, events and undo unchanged:** imported cards still get only a `baseline` event (source `import`); updates still go to `card_import_changes`, never `card_edit_history` (no "M" badge); undo still refuses to overwrite an import's change.

### Tests & Verification (22E)
- [x] `backend/test_import_routes.py` (43 tests, temp SQLite file with the full schema and FKs on, per-request sessions):
  - **Access:** 401 without or with a bad token on all four endpoints; 403 for a member, checked before the upload is parsed; a preview token refused as a bearer token; 404 for a missing board/batch.
  - **Preview:** classification and pending decisions; the database byte-identical afterwards; CPM absent; .csv/.xlsm/.xls/fake .xlsx/empty refused with the reader's code; 413 by Content-Length and by the reader's limit; missing file part; malformed decisions (not JSON, not a list, `card_id`/`source_key`, wrong types, header row); decisions the engine rejects; an unfit decision gives no token.
  - **Confirmation:** creates cards in the right stages with Bid details, source refs and one `baseline`/`import` event each; `confirm` must be exactly `true`; missing/garbage/tampered/other-purpose/other-board/other-admin tokens; another file; other decisions; same bytes under another name accepted; a repeated confirmation is 409 and writes nothing; a renamed stage since the preview is 409; internal duplicates left for review until one is skipped; a Bid on another board is 409; a concurrent import caught by the service's own re-check (409, failed `conflict` batch); an unexpected failure after rows were flushed (500, fixed message, commercial tables identical, failed `unexpected` batch with reason `RuntimeError` only).
  - **History and undo:** a confirmed re-import updates the card, records `card_import_changes`, leaves `has_member_edits` false and adds no stage event; undo of an earlier edit is refused after an import changed the card.
  - **Batches:** listing order, totals and pagination; other boards' batches never listed; details with mapping and failure diagnostics; no email, full name, password, CPM/project lead names or row content in batch responses.
- **Verification:** backend 700 passed, 1 skipped; only the 3 known pre-existing failures (`test_ai.py` x2, `test_static_files.py::test_root_path_serves_html`). `backend/pm.db` byte-identical (MD5 before and after); no migration run on it; nothing committed or pushed.

### Known gaps (22E)
- **Any admin can read any board's batch by id** (`GET /api/imports/{id}`): boards are shared and imports are admin-only, so there is no narrower rule to enforce; listings are per board.
- **The token is not single-use by itself:** a repeated confirmation is refused because the board's latest completed batch changed. Two confirmations of the same preview racing each other are serialized by the service; for a file that writes no card, both could complete as two empty batches.
- **Route-level refusals** (bad token, other file, stale preview) leave no failed batch; only attempts that reach the service are recorded.
- **Uploads over 1 MiB** touch an anonymous temp file (Starlette's spooling), deleted when the request ends.
- The dev database still needs `migrate_import_tables.py` before the backend starts on it (the UI is 22F, below).


### Implementation (step 6: Import Bids screen, 22F, 2026-10-14)
- **Where:** `frontend/src/components/BidImportPage.tsx` (upload, preview, confirm), `BidImportRows.tsx` (row table, filters, per-row decision controls), `BidImportHistory.tsx` (batch list and details); `lib/bidImport.ts` (pure helpers: choices to decisions, filters); typed client in `lib/api.ts` (`previewBidImport`, `confirmBidImport`, `listBidImports`, `getBidImport`). `ProtectedRoute` adds an "Import Bids" header button and view, shown to admins only (the endpoints stay `require_admin`; the UI gate is defense in depth).
- **Flow:** choose a `.xlsx` > Preview > summary (rows, new, updates, pending, per-stage counts, revenue per currency in cents-formatted amounts, file warnings) > rows with filters (needs decision, errors, warnings, new, updates, ignored, all; 100 per page) > decisions > confirm.
- **Decisions:** the controls shown per row follow the row's `required_decisions` from the backend (stage select from the board's columns, accept proposed stage, confirm update, confirm move, skip). The UI sends only `row_number` plus the explicit choices; skipping excludes the other choices and a chosen stage replaces accepting the proposal. It never decides which rows need what, and never sends card ids, column ids or source keys.
- **Confirmation:** decisions are local until "Update preview" re-sends the file and decisions (the token binds both). After any decision change the preview is stale and confirming is disabled; confirming also needs the backend's `confirmable` and `preview_token`, and an explicit "I reviewed this preview" checkbox. Rows still pending are shown as staying in review. A refused confirmation (stale token, changed data, 409/422) shows the backend's message and drops the preview, so the admin must preview again.
- **History:** the second tab lists the board's batches (completed and failed, paginated 50 at a time, refreshed after an import); "Details" shows counts, SHA-256, a failed batch's kind and reason, and the mapping used.
- **Client changes:** `apiCall` omits `Content-Type` for a `FormData` body (the browser sets the multipart boundary); `extractErrorDetail` reads the unreadable-file refusal (`detail.message` plus `file_errors`).

### Tests & Verification (22F)
- [x] `src/lib/bidImport.test.ts` (7): decisions built only from explicit choices, no false flags, skip/stage exclusivity, filters.
- [x] `src/components/BidImportPage.test.tsx` (5, API mocked): preview then stale after a decision change; confirm with the new token and decisions; non-confirmable preview blocks confirm; a refused confirmation shows the backend message; a failed batch's reason in the history details.
- [x] `tsc --noEmit` shows only the 17 known test-file errors; eslint clean; `npm run build` succeeds. Backend suite not run in this step (no backend changes).

### Known gaps (22F)
- ~~No Playwright E2E for the screen~~ - added in 22G.
- The screen is not responsive-tuned beyond horizontal table scroll.
- The preview has no row search; very large files (up to 5000 rows) are paged client-side.
- The dev database still needs `migrate_import_tables.py` before the backend, and so the screen, can work on it.

### Implementation (step 7: Import Bids E2E, 22G, 2026-10-14)
- `frontend/tests/bid-import.spec.ts` (10 tests, serial, on the usual temp-DB E2E backend) with fictitious workbooks from `frontend/tests/fixtures/make_bid_fixtures.py` (run from `backend/`; deterministic bytes; reuses `test_bid_excel_reader.build`). No real report is used.
- Covered: member has no entry and the API answers 403/401; upload + preview leave the database snapshot unchanged; decisions go stale until the preview is updated, then confirm; re-import creates only the row left in review; a newer report updates the card and stays out of `card_edit_history`; a non-workbook file and an invalid row are refused/flagged; a board change after the preview forces a new preview; a forged token and a second confirmation are refused; the history tab lists batches newest first.

### Tests & Verification (22G, final run)
- [x] `npx playwright test tests/bid-import.spec.ts`: 10 passed.
- [x] Full backend suite (`uv run pytest`, run once at the end of Part 22): 708 passed, 1 skipped, 3 failed - all three the known pre-existing ones (`test_ai.py::test_call_ai_missing_key`, `test_call_ai_request_error` with stale mocks; `test_static_files.py::test_root_path_serves_html` needs a built `backend/static`). No regressions.
- [x] `backend/pm.db` byte-identical before and after (md5 unchanged).

### Known gaps (Part 22, final)
- As of Part 22 (2026-10-14) never run on the real Salcomp report or a clean commercial database; the mapping rules rested on the approved spec and synthetic data. Since then (Phase 4A-4B.2, 2026-10-15) the real report has been previewed and imported on disposable copies of the clean database; the actual database was imported on 2026-10-16 (see the Phase 4C note below).
- Dev `backend/pm.db` still lacks the Part 22 tables (backend refuses to start on it until `migrate_import_tables.py --database <path> --apply`).
- The other E2E specs and the full frontend unit suite were not re-run in the final step; Docker path still unverified.
- Screen is not responsive-tuned, has no row search; no rate limiting on the import endpoints.

---

## Decision: local-first commercial database (2026-10-16)

- Remaining development and validation run on the user's Mac. VPS Hostinger migration is deferred until development is complete and the user explicitly approves deployment.
- Permanent local database: `/Users/pauloluisada/Library/Application Support/SalcompSalesPipeline/data/pm-commercial.db`. The Phase 3 scratch copy is not the permanent location and stays preserved until the user authorizes its retirement.
- Not activated. State on 2026-10-16 at decision time: CPM migration, import, activation and cleanup were each still pending; the CPM migration and the import were then completed the same day (see Phase 4C below). Remaining, each requiring its own explicit authorization: administrator activation (reset email, needs working SMTP), cleanup.
- Details and exact configuration: `docs/DEPLOYMENT.md`.

## Phase 4: real report preview and import rehearsal (2026-10-15, reconciled 2026-10-16)

- Evidence lives outside the repo in `pm-phase4-evidence/` (reports 4A, 4A follow-up v2, 4B.1, 4B.2, 4C.2) and `pm-phase3-scratch/evidence/`. No 4C.4 or 4C.5 report file was found on disk, so those phases are not documented here.
- The real report `relatorio-leads.xlsx` (SHA-256 `1b758fdf...059b00ea6`) was read for the preview (4A, v2 with CPM) and used for dress rehearsals on disposable copies of the prepared database (4B.1, 4B.2): CPM migration, 52-card import, repeat no-op, ledger revert, backup restore. `import_batches.file_sha256` of the rehearsal batch equals the real report hash. Synthetic workbooks are used only by the automated tests.
- Three different things, never to be conflated: rehearsing on a disposable copy (done in 4B.1/4B.2), importing into the actual commercial database (done on 2026-10-16, Phase 4C.8; before that it held 0 cards and 0 batches, as read-only checked in Phase 4C.6), activating it (not done: administrator unverified, no usable password).
- Open as of this writing: administrator activation, SMTP delivery verification, cleanup. Each real step needs separate authorization. (Update 2026-10-16: the 4 `test_bid_import_undo.py` failures, the 17 `tsc` test-file errors and the 2 `test_ai.py` stale-mock failures are resolved; `test_static_files.py` needs a generated `backend/static/`.)

## Phase 4C: CPM migration and import into the actual commercial database (2026-10-16)

Evidence (outside the repo): `pm-commercial-backups/20261016-cpm-migration/` and `pm-commercial-backups/20261016-import-4c8-run1/`. Only the text/JSON reports were consulted when writing this section.

- **CPM migration:** pre-migration backup `01-pre-cpm.db` (SHA-256 `896261371e6e...a973db02`, integrity ok, 0 foreign-key violations, 6 stages, 1 user, 0 cards, 0 batches) with a restore check; logical dumps of source, backup and restore are equal (raw bytes differ from the source's `de0ac1a2...` after the SQLite backup API; logical equality was the criterion). A further pre-migration backup `pm-commercial-pre-bid-cpm-20261016064523.db.bak` exists. No migration run report or post-migration hash is on disk; that the migration was applied is evidenced by the post-import verification below (`cpm_populated` 52).
- **Import (Phase 4C.8):** result SUCCESS, batch 1 (`completed`), 52 created, 0 skipped, 0 review; operator `cli:pauloluisada:phase4c-real-import`, importer administrator id 1. Workbook SHA-256 `1b758fdf...059b00ea6`, approved preview and decisions hashes matched. Dry run: 52 create, 0 update, 0 ignore, 0 review, matching the approved preview.
- **Database hashes:** pre-import `302d3440...5961` (backup `02-pre-import.db` / restore check `e09e09b3...a178bb5`, logical dumps equal); post-import `1aa5a32ed990850d557a2ce439866e8530d00ab70060816651d5260e10132509`.
- **Verification:** integrity ok, 0 foreign-key violations, 52 cards with matching refs/details, stage counts 0/5/3/15/6/23 (1st Contact/F2F/RFI/Proposal/Award/Lost) equal to the approved ones, BRL revenue totals equal to the approved ones, 52 CPM values equal to the approved ones, source keys equal, 0 record mismatches, independent field reconciliation 52 matched / 0 mismatch, exactly one `baseline`/`import` event per card, nothing fabricated (no extra users, assigned cards, audit events, `card_edit_history` or `card_actions` rows). A repeat dry run is `nothing_to_do` (52 ignored).
- **Protected files unchanged in that run:** `backend/pm.db` `3c4ce115...`, `backend/pm.db.bak` `96d9e503...`, scratch original `de0ac1a2...`.
- **Startup smoke test (07:15):** the app was started on 127.0.0.1:8765 and shut down; `/api/setup/status` 200 (`needs_setup` false), unauthenticated `/api/boards` and `/api/users` 401, one malformed login 422. No successful login. No database hash after that smoke test is recorded, so the current hash of the file is not verified here.
- **Not evidenced:** the reports contain no quoted authorization text; the authorization is recorded here on the user's statement. Duplicates, conflicts and rejects are not separate fields in the reports; zero is inferred from 0 review/0 skipped/0 mismatch and a `completed` batch.
- **Not done:** administrator activation (the administrator row still carries the placeholder password `!`), SMTP delivery, cleanup.

## Phase 7B: optimistic concurrency, central UI and Cards import recovery (implemented, committed and pushed to `origin/main`; not deployed to a server)

Status as of 2026-10-22: the work below is implemented, committed and pushed to `origin/main` (the commits after `1fc5623`: legacy Undo spec fix `638c9c5`, versions/AI/Undo/authority backend `74c4329`, Cards import unmatched-id and recovery backend `3b41d52`, central UI `d4c3a70`, guided import UX `8c30ac2`, then the documentation commit `66aef7c`; pushed on 2026-10-22) and tested as listed under "Test evidence". Nothing is deployed to a server, and nothing has had a deployment review. It has run on the developer's Mac against the real central databases (see "Real environment"). The legacy single-database module is untouched in behavior (a few shared files carry guarded additions, see "Shared files").

### Scope and design decisions
- Protects card operations in the central module, which uses separate databases for Salcomp, Lity and Valor Log. The legacy single-database module is permanently out of scope for development.
- Reuses the monotonic `card_versions` mechanism (written by the `card_reviews` flush listener). A card without a version row is version 0, but only when the version schema is valid.
- Changes to tracked card fields, `column_id` and Bid-details increment the version in the same transaction as the change. Position-only changes and position renumbering do not. Creating a card validates the schema in the flush but writes no row.
- Central REST card update (`PUT .../cards/{id}`), move (`PUT .../cards/{id}/position`) and delete (`DELETE .../cards/{id}?expected_version=`) **require** `expected_version`. Missing: HTTP 428 `card_version_required`. Stale: HTTP 409 `card_version_conflict` (body: `card_id`, `expected_version`, `current_version`). Invalid (negative or not an integer): 422. Order of checks: authentication/company access, record existence (404), permission (403), then the version (428/409), all under the write lock and before anything is changed. The legacy module is untouched. Decision (2026-10-22): the central UI is the only caller of these routes in the repository and central mode has never been deployed, so enforcement was chosen over a logged transition.
- A missing or malformed versioning/review schema fails closed (503 `review_not_initialized`); it is never read as "every card is at version 0".
- Card ids are not reused: `stage_history.py` allocates ids above every existing card and every card id in the stage-event history. That is a different guarantee from versioning: a new card has no `card_versions` row until its first tracked change, and the missing row means an effective version of 0.

### Central application: sections, navigation, reviews, layout (frontend; no backend change)
- **Sections.** After a company is chosen, the top navigation offers **Board** (always), **Users** (Super Admin only: the invitation and member management that used to sit under the board) and **Database** (Super Admin and company Admin: "Export Current Database", "Download Import Template" and "Import Cards from Excel"). A role without a section never sees it, a company always opens on its Board, and "Switch company" is unchanged. There are no URLs for the sections (one page, state-based); the backend still decides every request.
- **Controls in the top navigation.** Undo (hidden unless there is something to undo; its description is the tooltip; feedback stays under the board title), "Manage boards and columns" (its panel opens above the board) and the **AI Assistant** toggle (not offered to a Visitor). They are the same components with the same state, rendered into header slots (`NavSlot`). The assistant reuses `AIChatSidebar` (a `send` prop and a `themed` prop; the legacy panel is unchanged) and posts to `POST /api/companies/{slug}/boards/{id}/ai`; actions the server refused are listed under the answer (including `card_version_conflict`). It needs an `OPENROUTER_API_KEY` on the backend; without one it answers with an error.
- **Pending reviews.** The large "Pending reviews" section is gone. Each pending review obligation of the signed-in reviewer is a small **Review** marker on its card (accessible name "Admin|Super Admin review required (Sales Member edit | Move to an earlier stage): <card>"). The model has one obligation per card per reviewer role, and the API lists only the caller's own role's obligations, so a reviewer sees at most one marker per card; the two categories in the data (Sales Member edit, backward move) are trigger kinds inside one obligation and are cleared together, so no artificial second marker is shown (if the API ever returned two obligations for a card, two markers would show and clear independently). The marker opens a dialog with the category, status, stage, times, every recorded change (kind, "user #id" because names are not in the API, time, before and after values) and **Clear**, which calls the existing clear endpoint for that obligation only with the version on screen. The marker and dialog go away only after the backend confirms and the list is re-read; a stale version (409), a refusal (403) or a vanished obligation (404) keeps the dialog, says so and never claims success. Sales Members and Visitors get no markers and make no review request; the backend authorization is unchanged.
- **Layout and picker.** The board scroll area spans the whole window (it bleeds through the page gutter) instead of the 80rem content column, so the first and last columns are reachable at any width and the page itself does not scroll sideways. The company picker shows the Sales Tracker horses artwork (`frontend/public/sales-tracker-login.jpg`, the login artwork); no earlier version of the picker used it, so that placement is the developer's decision to confirm.

### Cards workbook import (central mode)
- **Flow.** Preview first, confirm second: nothing is written by a preview, a workbook with any error is not imported at all, and a confirmation is bound (plan digest, checked again under the write lock) to the exact workbook and the state of every card it would touch. One transaction applies the creates, the updates and the ledger row, or nothing. Authorization (Super Admin and company Admin, checked before the company database opens), company isolation, the version schema gate and card-version behavior are as before.
- **Rules.** An empty `card_id` creates a card; a `card_id` that is a card of THIS board updates it (the preview lists each field's current and proposed value); a repeated `card_id` in the file rejects its rows. A `card_id` that is not a card of this board is, by default, a row error (`card_not_found`): a mistyped or stale id must not silently create a duplicate, and the same answer is given whether the id is missing, on another board or in another company, so nothing is disclosed.
- **Unmatched ids as new cards (explicit choice).** When a preview finds such rows the UI shows a warning and the action **"Create new cards for unmatched IDs"**; choosing it previews again with `new_for_unmatched_ids=true`. Those rows become new cards with **fresh ids from the destination database** (the file's id is never kept and ids of deleted cards are never reused: `stage_history.py` allocates above every card ever created), with a `source_id_new_card` warning; rows whose id is on the board stay updates. Nothing is imported by the choice itself, Confirm stays disabled while any error remains, the choice is part of the plan digest, and it is forgotten when another workbook is selected.
- **At-most-once ledger.** Every applied import writes one `card_excel_imports` row whose `repeat_guard` is the fingerprint of the workbook's normalized content, scoped to company and board (unique per board). A logical workbook is applied at most once per board, permanently: deleting its cards releases nothing, and the ordinary rule is unchanged.
- **Restore missing cards (explicit recovery).** When the ledger is what blocks a preview and some of the workbook's cards are missing from the board, the preview explains this and offers **"Restore missing cards from this workbook"**; choosing it previews again with `recover_missing_cards=true`. Recovery writes ONLY the rows whose filled `card_id` is not a card of the board (new cards, fresh ids) and skips without writing: rows whose id matches a card here (never updated or duplicated), rows with an **empty `card_id`** and rows already restored by an earlier import or recovery whose card still exists (the ledger now records source id to new id in `changes_json.created_from`). The preview states how many cards are created and how many rows are skipped and why; nothing is updated. A fresh preview and an explicit confirmation are required; the confirmation is bound to the mode, the plan and the highest card id ever allocated, recomputed under the write lock, so a stale preview, a replay or a concurrent second confirmation is refused without writing. A recovery has its own ledger row and guard (existing ledger rows are never altered or deleted). A ledger entry written before source ids were recorded cannot say which rows its cards came from, so it blocks recovery while any card it created still exists on the board. Invalid rows still block; authorization, company isolation, the transaction and rollback are as for any import.
- **Limitations.** Rows with an empty `card_id` are not recoverable through the unmatched-id recovery (they would only duplicate what the earlier import created); a modified copy of an already-imported workbook is a different workbook and, with the unmatched-id choice, creates its new cards again (the preview says so); the ids of an export from another company can coincide numerically with cards of the destination board, and those rows are updates as before (the preview lists the proposed changes). The ledger and these flows add no schema: `card_excel_imports` already existed.

### Stage status
- **Stage 1, versioning foundation:** done (reported earlier).
- **Stage 2, REST optimistic concurrency:** done (reported earlier).
- **Stage 2A / 2B / 2C, fail-closed schema, error-handling review, error handling and import integrity:** done. The Stage 2C read-only review and its corrective fixes (import/revert/cards-workbook map a post-gate schema failure to 503; the flush listener also validates the schema when a card is created) are done.
- **Stage 3, frontend:** the central UI keeps the version a card had when the form opened, the drag started or Delete was chosen, sends it as `expected_version` (update body, move body, delete query) and never replaces it with a fresher one before submitting; a board re-read in between does not change it. Successful responses carry the new version into the board state. A 409 `card_version_conflict` is explained, never retried, the board is re-read, the edit form keeps what was typed and turns Save off, a refused drag puts the card back, a refused delete keeps the card, a refused Undo says nothing was undone. Files: `lib/api.ts`, `CentralBoardView.tsx`, `CentralCardForm.tsx`.
- **Stage 3, AI:** `board_ai_chat_payload` takes a snapshot `{card_id: version}` of every card of the board before the cards are shown to the model (`card_reviews.board_version_snapshot`); `apply_board_actions(version_snapshot=...)` takes the write lock, then checks update, move and delete actions after the permission checks (a refused person learns nothing). A stale action, or one on a card that was not in the snapshot, is refused individually as `{"action", "reason", "code": "card_version_conflict", "card_id", "expected_version", "current_version"}` in `actions_applied.failed`; the other actions still apply (the AI batch was never all-or-nothing; infrastructure failures still roll back the whole batch). Only the first action on a card is compared, because the write lock is held until the commit and the batch's own changes raise the version.
- **Stage 4, Undo:** company undo entries are stamped at commit (`undo._stamp_versions`) with the version of every card they name. `apply_undo` compares versions first; a mismatch raises `UndoVersionConflict`, the route rolls back (which also restores the claim of the entry) and answers 409 `card_version_conflict`; the entry is kept. A value restored to what the entry expects (A to B to A) is still refused, because versions only rise. Entries without versions keep the old value comparison, and the existing terminal refusals still consume the entry.
- **Stage 5, F8 mitigation:** every company session is checked against the authorized company (`company_runtime.session_for`: label and engine), and every commit of a writer session re-reads the caller's membership from the identity database under the write lock and refuses (403, rollback) if it is no longer active or the role changed (`company_authority.py`; Bid imports keep their own earlier recheck; the AI route rebinds to the role it was reauthorized with after the model answered, so a person demoted meanwhile still works with less). The race is narrowed to one commit, not eliminated (`docs/SECURITY.md`, F8).

### Test evidence
Kept apart on purpose: automated results (unit, integration, browser E2E) are from commands run in the working tree before the commits, and each code commit was re-checked on the exact staged tree (see below); the "Real environment" results are manual and reported by the developer. Groups overlap; there is no combined total.
- **Backend, full suite.** `cd backend && uv run pytest -q -p no:warnings -p no:cacheprovider`: **3292 passed, 1 skipped, 0 failed**, 20 min 39 s, run once after the mandatory `expected_version` work. The skip is the known `test_bid_import_service.py:505` ("no development database here"). **The full suite was not rerun afterwards.** The backend changes made since (the Cards import unmatched-id and recovery code in `cards_excel_import.py`, `excel_import_ledger.py` and the two form fields in `company_routes.py`) were covered by targeted runs only: `test_cards_import_recovery.py` (10), `test_cards_import_unmatched_ids.py` (10), `test_company_cards_excel_import.py`, `test_company_excel_routes.py`, `test_company_upgrade.py` and `test_static_files.py`, together **152 passed** in the last run. No backend code changed for the UI work.
- **Commit-time checks (2026-10-22).** Each code commit was exported from the index and tested on its own: versions/AI/Undo/authority backend 351 + 383 passed (two batches of central suites); import backend 138 passed; central UI commit: unit 713 passed, `tsc` clean, lint 0 errors, central E2E 20 passed, build ok; guided import commit: unit 723 passed, `tsc` clean, lint 0 errors, central E2E 25 passed. The full backend suite was not rerun for the commits.
- **Backend, targeted and integration (earlier in the phase).** Central route suites (1036 passed in one run after mandatory versions), the rewritten race tests (18 passed three times in a row), `test_schema_failure_handling.py`, `test_ai_version_conflicts.py`, `test_undo_version_conflicts.py`, `test_company_binding.py` (97 passed together), and the earlier stage reports (Stage 1 20 / 211 / 72; Stage 2 32 / 43; Stage 2A 15-file set 496; Stage 2C 38 / 140).
- **Mandatory version, what the tests assert.** Missing `expected_version` is 428 `card_version_required` for update, move and delete (also an explicit null) and changes nothing; an unknown card is 404 and a member without permission 403 before the version is looked at; the current version succeeds and the next stale one is 409 `card_version_conflict`; negative or non-integer versions are 422. Competing requests start from the same version, so the second to take the write lock is refused instead of overwriting; the winner stays and the refused one leaves no audit row, history, undo entry or version bump. Existing tests send versions through `Rig.call`/`Rig.versioned` unless they pass `versionless=True`.
- **Frontend unit.** `npm run test:unit`: **723 passed** (50 files), including the sections, review markers and dialog, version handling, picker artwork, AI panel and the Cards import prompts. `npx tsc --noEmit` clean; `npm run lint` 0 errors and 1 warning in an unrelated test file (`AuthModeGate.test.tsx`); `npm run build` ok. `backend/static` (generated, git-ignored) must be refreshed from `frontend/out` after every build or `test_static_files.py::test_static_matches_the_current_build` fails.
- **Browser E2E.** Central (`npm run test:e2e:central`, real central-mode backend on :8002 and frontend on :3002, a new temporary directory with freshly seeded databases, never a real or development database): **25 passed** (version conflicts and 428, sections and layout at four widths, review markers and clearing per role, controls in the navigation, picker artwork, import of an export into a new empty board, unmatched-id choice, ledger refusal, restore of deleted cards). Legacy (`npm run test:e2e`): **97 passed, 2 skipped, 0 failed**; the former `bid-import.undo.spec.ts` failure was a stale selector (the Undo entry text is the button's `title`).
- **Real environment (manual, reported by the developer; not automated).** The real central databases on the developer's Mac (identity, Salcomp, Lity, Valor Log) were backed up and upgraded with the two additive init scripts on 2026-10-22 (see `docs/DEPLOYMENT.md`, "Executed record"), and the application was run against them in central mode. The developer reported that the UI changes (sections, navigation, review markers, layout) worked, and that the Cards import recovery worked on the real Valor Log board: the exported workbook of two deleted cards was restored through the explicit choices. No automated test ran against real data, no business records were read by the assistant, and nothing was deployed to a server (VPS), so server, TLS/proxy and real SMTP behavior remain unvalidated.

### Shared files
`routes.py`, `ai_kanban.py`, `import_routes.py` and `undo.py` also serve the legacy module. Additions there are inert for legacy sessions: `ReviewsNotInitialized` and `AuthorityRevoked` are only raised for company sessions; `track_versions` / `version_snapshot` default to off; undo stamping and the version comparison run only when `is_company_session`.

### Known limitations and residual risks
- Mandatory `expected_version` applies to the three central REST card writes only. Other card writers use their own version-aware contracts: the AI assistant (version snapshot), Undo (stamped versions), the imports (preview digests/fingerprints) and review clearing (`card_version`). Card creation needs no version. An external client written against the earlier optional contract would now receive 428 (none is known).
- F8 is mitigated, not eliminated: a change in the identity database between the commit-time read and the SQLite commit is not seen.
- AI conflicts are reported per action inside a 200 response, not as an HTTP 409; the first action on a card is the one compared.
- A conflicted Undo keeps its entry, so the Undo button stays offered until another change replaces it; it keeps answering 409.
- Entries recorded before this change have no versions and keep the value comparison (A to B to A is undetectable for them).
- The AI Assistant button and panel now exist in the central UI (refused actions, including conflicts, are listed under the answer); no model is configured on the real backend yet (`OPENROUTER_API_KEY`), so the assistant itself has only been exercised with the endpoint and a stubbed model in tests.
- Lock-contention, "schema breaks mid-operation" and the F8 revocation cases are tested by deterministic simulation (a second connection or a patched call), not by real concurrent load.
- `card_reviews` imports `bid_import_revert.is_lock_error` lazily (coupling noted in the Stage 2C review).

### Remaining work
1. Server-side validation by an authorized operator: the upgrade, diagnostics and backups were done for the developer's local databases only; a server deployment (VPS) needs the same procedure on its own databases, plus TLS/proxy and real SMTP checks (`docs/DEPLOYMENT.md`).
2. A whole-suite backend rerun on the exact commit that will be deployed (the last full run predates the Cards import recovery code).
3. Optional follow-ups noted above: reviewer names in the review dialog (the API returns only ids), a decision on whether two review markers per card are wanted (it would need an API or authorization change), and configuring a model key for the AI Assistant.
4. Obtain explicit authorization and a deployment review before any deployment (the commits are already pushed).


## Batch 2: Reports and KPIs in the central shell, stage palette, ordering, contact fields, Admin promotion, Undo notice (local work; not pushed or deployed)

**Scope (six requirements).** (1) Reports and KPIs entries in the central navigation, reading the existing company endpoints (the central shell never had them; the legacy shell is unchanged). (2) Open stages blue, cyan, violet, amber (slate beyond the fourth), won green, lost red, in `frontend/src/lib/stageTone.ts`. (3) Cards listed by Deal Value, highest first, unvalued last, ties by stored position and id (`frontend/src/lib/cardOrder.ts`); display only, so a drop inside the same column sends nothing. (4) `cards.contact_email` and `cards.contact_phone` with backend validation, central form and details, and `backend/migrate_card_contact.py`. (5) The Super Admin can set a joined member to Admin from Users; invitations still cannot carry Admin. (6) The "Undone: ..." message is removed in both shells; problems are still shown.

**Decisions.** Contact fields are columns on `cards` (the model, the required schema and the legacy init guard agree), so migration is a prerequisite for each database; the schema version was not bumped because the change is additive and `migrate_confidence_level.py`-style version migrations are heavier than the change warrants. The Cards workbook contract (fixed columns, unknown columns rejected) and the AI assistant were left unchanged. Admin creation by invitation was not restored: that would change the security model.

**Automated evidence (local, temporary databases only; not production data).** Backend full suite 3353 passed, 1 skipped (unidentified), 0 failed. Frontend unit 780 passed (55 files); `tsc --noEmit` clean; lint 0 errors (1 existing warning in `AuthModeGate.test.tsx`); build ok. Legacy browser E2E 98 passed, 2 skipped. Central browser E2E (`npm run test:e2e:central`) 38 passed. `test_card_contact.py` 34 passed; `test_static_files.py` 14 passed after `backend/static` was synchronized with `frontend/out` (it is a generated, git-ignored copy; resync with `rsync -rc --delete frontend/out/ backend/static/` after every frontend build). A baseline run found four central specs failing on stale expectations from Batch 1 (Edit and Delete moved into the details dialog) and Batch 2 (new navigation entries); they were updated, not weakened. The Reports error box gained `role="alert"` because the E2E exposed that it had none.

**Not validated.** Any real or Salcomp database, the migration on a real file, the new screens on production data, a server, real SMTP, TLS or proxy. No manual validation of Batch 2 is recorded.

**Remaining.** Back up and migrate each affected database before running this code against it (`docs/DEPLOYMENT.md`); a decision on the Salcomp protected-path limitation; optional follow-ups: contact fields in the Cards workbook and the AI assistant, identification of the single skipped backend test.
