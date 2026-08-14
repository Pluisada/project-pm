# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.


## Project

A Project Management MVP: a Kanban board with drag-and-drop, multi-user auth (admin/member roles), and an AI chat sidebar that can create/edit/move cards. Next.js frontend, FastAPI backend serving the static Next.js export at `/`, SQLite database, packaged as a single Docker container. AI calls go through OpenRouter (`openai/gpt-oss-120b`).

Auth: the first user ever created (via a dedicated setup screen) becomes admin; only an admin can create further users (always role "member") from a "Manage Users" screen. Boards are shared across all authenticated users — not isolated per owner — so admin vs. member today only gates who can create users, nothing else. Local-only via Docker.

## Commands

### Backend (`backend/`, Python 3.11+, managed with `uv`)
```bash
cd backend
uv sync                          # install deps (or: pip install -e .[dev])
uv run uvicorn main:app --reload --port 8000   # run dev server
uv run pytest                    # run all tests
uv run pytest test_routes.py -k test_name       # run a single test
```

### Frontend (`frontend/`)
```bash
cd frontend
npm install
npm run dev          # dev server on :3000 (proxies API calls to backend separately)
npm run lint
npm run test:unit    # Vitest, single run
npm run test:unit:watch
npm run test:e2e     # Playwright (needs a running app; see playwright.config.ts)
npm run test:all     # unit + e2e
npm run build         # static export -> frontend/out (consumed by Dockerfile)
```
Run a single Vitest file: `npx vitest run src/lib/kanban.test.ts`.
Run a single Playwright test: `npx playwright test tests/kanban.spec.ts -g "test name"`.

### Full stack (Docker)
```bash
./scripts/start.sh   # docker-compose build && up -d, waits on /api/health
./scripts/stop.sh    # docker-compose down
```
The Dockerfile builds the frontend (`next build` → static export), copies the output into `backend/static/`, and runs the FastAPI app with uvicorn on port 8000. `OPENROUTER_API_KEY` must be set in `.env` at the repo root (see `.env.example`); docker-compose passes it through and mounts `./pm.db` for persistence.

## Architecture

### Request flow
In production (Docker), FastAPI (`backend/main.py`) mounts the static Next.js export at `/` and exposes JSON routes under `/api/*`. In local dev, the two run separately (Next dev server on :3000, FastAPI on :8000) and the frontend's `lib/api.ts`/`lib/auth.ts` talk to `API_BASE_URL`.

### Backend (`backend/`)
- `main.py` — FastAPI app setup, CORS, startup hook (`init_db` only — users/boards are created via `/api/setup`, not seeded at boot), auth endpoints (`/api/login`, `/api/logout`, `/api/user`, `/api/setup/status`, `/api/setup`), static file mount.
- `auth.py` — JWT issuing/verification (`python-jose`), password hashing (`hash_password`/`verify_password`, via `bcrypt` directly — not `passlib`, which has a known incompatibility with `bcrypt>=4.1`). No DB access here.
- `deps.py` — the single `get_current_user` (parses `Authorization`, verifies the JWT, loads the `User` row) and `require_admin` (403s unless `role == "admin"`), used by both `main.py` and `routes.py`. Role is re-read from the DB on every request, not embedded in the JWT.
- `database.py` — SQLAlchemy engine/session (SQLite by default, swappable via `DATABASE_URL`), `init_db()` creates tables, `seed_default_board(db, creator)` seeds the shared default board (5 columns + sample cards) once, called from `POST /api/setup` — not at startup.
- `models.py` — ORM models: `User` (with `role`: `UserRole.ADMIN`/`MEMBER`) → `Board` → `BoardColumn` (aliased as `Column`) → `Card`, plus `ConversationMessage` (AI chat history) and `CardAction` (audit trail of AI-driven board changes). All children cascade-delete from their parent.
- `schemas.py` — Pydantic request/response models mirroring the ORM models (`*Create`, `*Update`, `*Response`, `BoardDetail`/`ColumnWithCards` for nested board fetches, `SetupRequest`/`UserCreate`/`UserResponse` for auth).
- `routes.py` — REST CRUD for boards/columns/cards under `/api`, the AI chat endpoint `POST /api/boards/{id}/ai`, and admin-only user management (`GET`/`POST /api/users`, via `Depends(require_admin)`). Boards are shared, not owned — routes look up `Board.id` only, never filter by who created it.
- `ai.py` — low-level OpenRouter client wrapper (`call_ai`, `test_ai_connectivity`, `AIError`).
- `ai_kanban.py` — builds board-state context and conversation history for the AI, defines the system prompt requiring structured JSON output (`{response, actions[], confidence}`), and applies returned `actions` (create/update/move/delete) back onto the board via `apply_board_actions`.
- Tests are colocated (`test_*.py`), run with `pytest` + `TestClient`.

### Frontend (`frontend/src`)
- `app/page.tsx` → renders the top-level component tree; `ProtectedRoute` gates on setup-status/auth (`SetupPage` when no users exist yet, else `LoginPage`), `KanbanWithSidebar` composes the board with `AIChatSidebar`, `ManageUsersPage` is admin-only (toggled from the header).
- `components/KanbanBoardAPI.tsx` — the persisted, API-backed board (replaces the older local-state-only `KanbanBoard.tsx`/`lib/kanban.ts` demo, which is still used in its own tests). Talks to the backend via `lib/api.ts`.
- `lib/auth.ts` — stores the JWT + role (e.g. localStorage), exposes `API_BASE_URL`, `getAuthHeader()`, `getSetupStatus()`/`setupAdmin()`, and `isAdmin()` (UI-only gating — the backend independently re-checks role on every admin request).
- `lib/api.ts` — typed fetch wrapper (`apiCall`) for all board/column/card/AI/user endpoints; normalizes HTTP and network errors into `ApiError`.
- Drag-and-drop uses `@dnd-kit` (core + sortable); column/card components (`KanbanColumn`, `KanbanCard`, `KanbanCardPreview`, `NewCardForm`) are shared between the demo and API-backed boards.
- Styling: Tailwind CSS 4, with the app color palette defined as CSS variables in `app/layout.tsx` (`--primary-blue #209dd7`, `--secondary-purple #753991`, `--accent-yellow #ecad0a`, `--navy-dark #032147`, `--gray-text #888888`).

### Docs
`docs/PLAN.md` is the working plan for how the project was built in parts; `docs/DATABASE.md` documents the schema; `docs/schema.json` is the machine-readable schema. Check `docs/PLAN.md` before making structural changes.

## Status and known gaps (see `docs/PLAN.md` for full history)

All 10 planned parts are implementation-complete, but verification has been piecemeal (curl + Chrome DevTools MCP, feature by feature), not one continuous browser session. Be aware of these gaps before assuming something works:

- **Docker path is unverified.** All testing so far has run the backend and frontend natively (`uvicorn` on :8000, `next dev` on :3000) as separate processes, not through `Dockerfile`/`docker-compose.yml`. Don't assume the Docker build works without checking.
- No markdown rendering, card-change animations, or mobile sidebar collapse in the AI chat sidebar.
- `docs/PROMPTS.md` / `docs/AI_SCHEMA.md`, referenced in the Part 9 plan, were never written — the system prompt and action schema live inline in `ai_kanban.py`.
- Test coverage is not formally measured; the existing suite did not catch any of the runtime bugs below.

### Non-obvious bugs already fixed (don't reintroduce)
- `models.py`: a model class literally named `Column` shadowed SQLAlchemy's own `Column` import and broke under SQLAlchemy 2.0.36's type-annotation syntax — now aliased as `BoardColumn` (`Column = BoardColumn` for back-compat). `ConversationMessage.meta_data` is named that way (not `metadata`) because `metadata` collides with `Base.metadata`.
- `ai.py`: OpenRouter's base URL is `https://openrouter.ai/api/v1` — `.io` 404s.
- `ai_kanban.py`: `call_ai_with_board()` must actually prepend `{"role": "system", "content": SYSTEM_PROMPT}` to the message list (plus `response_format: {"type": "json_object"}` in `ai.py`'s `call_ai()`) or the model free-forms text instead of structured JSON, and `apply_board_actions()` silently never runs.
- `KanbanBoardAPI.tsx`: columns and cards are separate SQL tables with independent autoincrement ids, so a column and a card can share a numeric id. dnd-kit keys all draggables/droppables in one id-keyed map, so raw numeric ids collide. Ids are prefixed (`col-${id}` / `card-${id}`) before being handed to `KanbanColumn`/`KanbanCard`, then translated back to numeric ids in callbacks — don't strip that prefixing.
- `auth.ts` / `api.ts`: use the `API_BASE_URL` constant, not relative paths — relative `/api/...` calls only work when frontend and backend share an origin (the Docker/static-export setup), and 404 against Next.js itself when run as separate dev servers.
- `routes.py` used to have its own `get_current_user` that ignored the `Authorization` header and always loaded a hardcoded username — meaning no board/column/card/AI route ever validated a bearer token. Fixed by unifying on `deps.get_current_user`; don't reintroduce a second, route-file-local auth dependency.
- Backend tests using an in-memory SQLite engine (`create_engine("sqlite:///:memory:")`) need `poolclass=StaticPool` + `connect_args={"check_same_thread": False}`, or `TestClient` requests intermittently fail with `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread` — see `test_auth.py`/`test_routes.py`'s `test_db` fixture.

## Coding standards (from AGENTS.md)

- Use latest stable versions of libraries and idiomatic patterns.
- Keep it simple: no over-engineering, no unnecessary defensive programming, no speculative features.
- Be concise; no emojis, ever.
- When debugging, find the root cause before attempting a fix — don't guess.
