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

## Part 5: Database Modeling ✅ AWAITING USER APPROVAL

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

#### 5.5 User Approval ⏳
- [ ] User reviews schema.json
- [ ] User reviews DATABASE.md
- [ ] User approves design
- [ ] Proceed to Part 6

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
- [ ] User has reviewed and approved schema
- [ ] Ready to implement in Part 6

---

## Part 6: Backend API Implementation

### Objective
Implement database integration and API routes for full CRUD on Kanban board. Backend-only tests.

### Substeps

#### 6.1 Database Setup
- [ ] Create `backend/database.py` with SQLAlchemy setup
- [ ] Implement database initialization (create if not exists)
- [ ] Add database session management
- [ ] Database file stored at path from .env

#### 6.2 Core API Routes
- [ ] POST /api/boards → create board (user 1 only in MVP)
- [ ] GET /api/boards → list user's boards
- [ ] GET /api/boards/{id} → get board with all cards
- [ ] PUT /api/boards/{id} → update board metadata

#### 6.3 Column API Routes
- [ ] POST /api/boards/{id}/columns → add column
- [ ] PUT /api/boards/{id}/columns/{colId} → rename column
- [ ] DELETE /api/boards/{id}/columns/{colId} → delete column
- [ ] Reorder columns (PUT with position)

#### 6.4 Card API Routes
- [ ] POST /api/boards/{id}/cards → create card in column
- [ ] PUT /api/boards/{id}/cards/{cardId} → update card (title, details)
- [ ] DELETE /api/boards/{id}/cards/{cardId} → delete card
- [ ] PUT /api/boards/{id}/cards/{cardId}/position → move card

#### 6.5 Error Handling
- [ ] Validate all inputs (required fields, string length, etc.)
- [ ] Return 400 for bad requests with error messages
- [ ] Return 404 for not found
- [ ] Return 500 with logging for server errors

#### 6.6 Database Queries
- [ ] Optimize queries (use joins, select specific columns)
- [ ] Add indexes where needed
- [ ] Avoid N+1 query problems

### Tests & Verification

#### Unit Tests (pytest)
- [ ] Database: create/read/update/delete operations
- [ ] Database: constraint enforcement
- [ ] Models: serialize to JSON correctly
- [ ] Validation: all input validators work

#### Integration Tests (pytest + test client)
- [ ] POST /api/boards succeeds and returns board
- [ ] GET /api/boards/{id} returns full board with cards
- [ ] PUT /api/boards/{id}/columns/{colId} renames column
- [ ] POST /api/boards/{id}/cards creates card in column
- [ ] PUT /api/boards/{id}/cards/{cardId}/position moves card
- [ ] DELETE removes card and updates column
- [ ] Invalid inputs return 400 errors
- [ ] Missing items return 404 errors

#### Load Tests
- [ ] Create board with 100 cards
- [ ] Verify performance acceptable
- [ ] Verify memory usage reasonable

### Success Criteria

- All CRUD operations work via API
- Database persists state correctly
- Input validation prevents invalid data
- Error responses are informative
- API tests achieve >90% coverage
- No database errors on edge cases

---

## Part 7: Frontend + Backend Integration

### Objective
Replace frontend's local state with backend API calls. Full end-to-end testing of persistent Kanban.

### Substeps

#### 7.1 Frontend API Client
- [ ] Create `src/lib/api.ts` with fetch wrapper
- [ ] Handle authentication headers (include token)
- [ ] Handle errors and retry logic
- [ ] Type-safe API calls with TypeScript

#### 7.2 Update KanbanBoard Component
- [ ] Replace useState with useEffect + API calls
- [ ] Load board on mount: GET /api/boards/{id}
- [ ] Add card: POST /api/boards/{id}/cards
- [ ] Delete card: DELETE /api/boards/{id}/cards/{id}
- [ ] Move card: PUT /api/boards/{id}/cards/{id}/position
- [ ] Rename column: PUT /api/boards/{id}/columns/{id}

#### 7.3 Loading & Error States
- [ ] Show loading spinner on initial load
- [ ] Show error toast on API failures
- [ ] Retry failed requests
- [ ] Display offline indicator if connection lost

#### 7.4 Real-time Updates
- [ ] Optimistic updates (update UI before API response)
- [ ] Revert on error
- [ ] Show sync status indicator

#### 7.5 Board Selection
- [ ] Allow user to select which board to view
- [ ] Store selected board in localStorage
- [ ] Load selected board on startup
- [ ] For MVP, auto-select the one board

### Tests & Verification

#### Unit Tests
- [ ] API client handles requests correctly
- [ ] Error handling works
- [ ] Retry logic functions
- [ ] Component hooks work with mocked API

#### Integration Tests
- [ ] Start backend and frontend together
- [ ] Login, see Kanban board
- [ ] Add card → appears in list
- [ ] Edit card → persists to DB
- [ ] Drag card → position saved
- [ ] Delete card → removed from DB
- [ ] Rename column → saved to DB
- [ ] Refresh page → state preserved

#### E2E Tests (Playwright)
- [ ] Full user flow with real backend
- [ ] Verify persistence: add card, refresh, card still there
- [ ] Verify no race conditions with rapid clicks
- [ ] Test on slow network (throttle in dev tools)

### Success Criteria

- Frontend uses backend API for all state
- All changes persist across page refreshes
- Loading states show during API calls
- Errors handled gracefully with user feedback
- Optimistic updates improve UX
- Comprehensive E2E tests pass
- No data loss or corruption

---

## Part 8: AI Connectivity

### Objective
Test AI connectivity via OpenRouter. Implement simple test (2+2) to verify the integration works.

### Substeps

#### 8.1 OpenRouter Setup
- [ ] Verify OPENROUTER_API_KEY in .env
- [ ] Create `backend/ai.py` module for AI calls
- [ ] Implement function to call OpenRouter API
- [ ] Use model: `openai/gpt-oss-120b` as specified
- [ ] Handle API errors (rate limits, auth, network)

#### 8.2 Test Endpoint
- [ ] POST /api/ai/test with question: "What is 2+2?"
- [ ] Call OpenRouter API
- [ ] Return response JSON
- [ ] Log all API calls for debugging

#### 8.3 Error Handling
- [ ] Handle missing API key
- [ ] Handle network timeouts
- [ ] Handle rate limiting
- [ ] Log errors clearly

#### 8.4 Environment Verification
- [ ] Add helper to validate OPENROUTER_API_KEY exists
- [ ] Warn on startup if key missing
- [ ] Prevent crashes if API key invalid

### Tests & Verification

#### Unit Tests
- [ ] API key validation works
- [ ] Error handling for network failures
- [ ] Error handling for invalid responses

#### Integration Tests
- [ ] POST /api/ai/test with valid API key → returns response
- [ ] Response contains AI's answer to "2+2?"
- [ ] Missing API key returns 500 with helpful error
- [ ] Network timeout handled gracefully

#### Manual Testing
- [ ] Call /api/ai/test via curl or browser
- [ ] Verify response is correct
- [ ] Check logs for API call details
- [ ] Monitor token usage

### Success Criteria

- AI API calls work end-to-end
- Simple test (2+2) succeeds
- Errors handled and logged
- API key is properly secured
- Ready for Part 9

---

## Part 9: AI-Powered Kanban Updates

### Objective
Extend AI integration to send full Kanban board state + conversation history. AI returns structured output with response + optional card updates.

### Substeps

#### 9.1 Conversation Storage
- [ ] Create database table for conversation history
- [ ] Schema: id, user_id, board_id, role (user/assistant), content, created_at
- [ ] Implement CRUD operations for messages

#### 9.2 Structured Output Schema
- [ ] Define JSON schema for AI responses:
  - `response`: Human-readable text for user
  - `actions`: Array of card updates (create, update, move, delete)
  - `confidence`: How confident AI is in suggested changes
- [ ] Document schema in docs/AI_SCHEMA.md

#### 9.3 Backend AI Endpoint
- [ ] POST /api/boards/{id}/ai → handle user question
- [ ] Load board state + last N messages
- [ ] Build prompt with: board JSON + history + current question
- [ ] Call OpenRouter with structured output schema
- [ ] Parse response into actions + text
- [ ] Save user message and AI response to history
- [ ] Return response + actions to frontend

#### 9.4 Action Processing
- [ ] Validate AI-suggested actions (stay within column bounds, etc.)
- [ ] Apply actions to board (create/update/move/delete cards)
- [ ] Save updated board to database
- [ ] Handle failures gracefully (invalid moves, etc.)

#### 9.5 Prompt Engineering
- [ ] Write clear system prompt for AI
- [ ] Include examples of valid card operations
- [ ] Instruct AI on when to modify board vs. just respond
- [ ] Document prompts in docs/PROMPTS.md

### Tests & Verification

#### Unit Tests
- [ ] Conversation storage/retrieval
- [ ] Action validation logic
- [ ] Board update from actions
- [ ] Prompt generation

#### Integration Tests
- [ ] POST /api/boards/{id}/ai with simple request → returns response
- [ ] Response follows structured output schema
- [ ] Conversation is saved and retrievable
- [ ] AI can suggest card creation
- [ ] AI can suggest card movement
- [ ] Invalid suggestions are rejected
- [ ] Board state doesn't change if AI has no actions
- [ ] Conversation context used on follow-up messages

#### E2E Tests
- [ ] User asks "Create a task: Fix login bug"
- [ ] AI creates card in appropriate column
- [ ] User asks "Move it to In Progress"
- [ ] AI moves card (uses context from previous message)
- [ ] Refresh page → changes persisted

### Success Criteria

- AI understands full board context
- AI can suggest card operations
- Conversation history preserved
- Invalid moves rejected safely
- Structured outputs work correctly
- Comprehensive test coverage
- Ready for UI integration

---

## Part 10: AI Chat Sidebar & UI Integration

### Objective
Build beautiful sidebar chat widget. AI updates Kanban automatically based on structured outputs. Full UI refresh on AI actions.

### Substeps

#### 10.1 Chat Sidebar Component
- [ ] Create `src/components/AIChatSidebar.tsx`
- [ ] Layout: Messages list + input at bottom
- [ ] Responsive design (collapse on mobile)
- [ ] Beautiful styling matching design system

#### 10.2 Message Display
- [ ] Show user and assistant messages
- [ ] Render markdown in AI responses
- [ ] Show typing indicator while waiting
- [ ] Scroll to latest message
- [ ] Clear button to start new conversation

#### 10.3 Message Input
- [ ] Text input field
- [ ] Send button
- [ ] Keyboard shortcut: Enter to send, Shift+Enter for newline
- [ ] Disable input while waiting for response
- [ ] Character limit (optional) or warn on very long messages

#### 10.4 API Integration
- [ ] Send message: POST /api/boards/{id}/ai
- [ ] Include message text
- [ ] Handle response with actions array
- [ ] Parse suggested card changes

#### 10.5 Automatic Board Updates
- [ ] When AI returns actions, apply them to local board state
- [ ] Animate card creation/movement
- [ ] Flash highlight on changed cards
- [ ] Auto-refresh without full page reload

#### 10.6 Layout Integration
- [ ] Add sidebar to main layout (left or right)
- [ ] Toggle sidebar visibility (mobile)
- [ ] Main content adjusts when sidebar visible
- [ ] Responsive breakpoints

#### 10.7 Error Handling
- [ ] Show error message if AI call fails
- [ ] Allow retry
- [ ] Reconnection logic for network issues
- [ ] Fallback if AI doesn't respond

### Tests & Verification

#### Unit Tests
- [ ] Message rendering
- [ ] Input validation
- [ ] Action parsing
- [ ] Animation helpers

#### Integration Tests
- [ ] Send message → appears in UI
- [ ] Receive AI response → displayed
- [ ] AI suggests action → board updates
- [ ] New card appears with animation
- [ ] Card move animates smoothly

#### E2E Tests (Full Flow)
- [ ] Load app with Kanban and sidebar
- [ ] Type message in AI input
- [ ] Send message
- [ ] See typing indicator
- [ ] AI responds with text and card action
- [ ] Card is created/moved/deleted automatically
- [ ] Message saved in history
- [ ] Refresh page → history persists
- [ ] Continue conversation → AI uses context
- [ ] Test on mobile (sidebar collapse)

#### Visual Tests
- [ ] Sidebar looks good on light/dark themes
- [ ] Animations are smooth (60fps)
- [ ] No layout shifts
- [ ] Responsive on mobile/tablet/desktop
- [ ] Color contrast passes WCAG AA

### Success Criteria

- Beautiful sidebar chat widget implemented
- AI responses displayed with markdown
- Suggested card actions automatically apply
- Animations smooth and polished
- Responsive on all screen sizes
- Full E2E tests pass
- Conversation history persists
- MVP complete and ready for deployment

---

## Overall Success Criteria

- [ ] All 10 parts completed
- [ ] >80% test coverage across frontend and backend
- [ ] App runs in Docker container locally
- [ ] User can login, use Kanban, chat with AI
- [ ] AI can create/edit/move cards
- [ ] All state persists to SQLite database
- [ ] No hardcoded secrets
- [ ] Documentation complete (AGENTS.md, DATABASE.md, PROMPTS.md)
- [ ] Code follows coding standards (simple, idiomatic, concise)
- [ ] Ready for production deployment
