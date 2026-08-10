# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.


## Project

A Project Management MVP: a Kanban board with drag-and-drop, hardcoded single-user auth, and an AI chat sidebar that can create/edit/move cards. Next.js frontend, FastAPI backend serving the static Next.js export at `/`, SQLite database, packaged as a single Docker container. AI calls go through OpenRouter (`openai/gpt-oss-120b`).

MVP limits: one hardcoded user (`user`/`password`), one board per user, local-only via Docker. The DB schema supports multiple users/boards for the future even though the API only exposes the current user's data.

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
- `main.py` — FastAPI app setup, CORS, startup hook (`init_db` + `create_sample_data`), auth endpoints (`/api/login`, `/api/logout`, `/api/user`), static file mount.
- `auth.py` — JWT issuing/verification (`python-jose`), hardcoded credential check (`user`/`password`). No `get_current_user` DB lookup happens here; `routes.py` has its own simplified `get_current_user` that just looks up the single hardcoded `User` row (real bearer-token validation is only enforced on the endpoints in `main.py`).
- `database.py` — SQLAlchemy engine/session (SQLite by default, swappable via `DATABASE_URL`), `init_db()` creates tables, `create_sample_data()` seeds the one hardcoded user + a default board with 5 columns and sample cards, idempotently (skips if the user already exists).
- `models.py` — ORM models: `User` → `Board` → `BoardColumn` (aliased as `Column`) → `Card`, plus `ConversationMessage` (AI chat history) and `CardAction` (audit trail of AI-driven board changes). All children cascade-delete from their parent.
- `schemas.py` — Pydantic request/response models mirroring the ORM models (`*Create`, `*Update`, `*Response`, `BoardDetail`/`ColumnWithCards` for nested board fetches).
- `routes.py` — REST CRUD for boards/columns/cards under `/api`, plus the AI chat endpoint `POST /api/boards/{id}/ai`. Every route re-verifies the board belongs to the current user before touching nested resources.
- `ai.py` — low-level OpenRouter client wrapper (`call_ai`, `test_ai_connectivity`, `AIError`).
- `ai_kanban.py` — builds board-state context and conversation history for the AI, defines the system prompt requiring structured JSON output (`{response, actions[], confidence}`), and applies returned `actions` (create/update/move/delete) back onto the board via `apply_board_actions`.
- Tests are colocated (`test_*.py`), run with `pytest` + `TestClient`.

### Frontend (`frontend/src`)
- `app/page.tsx` → renders the top-level component tree; `ProtectedRoute` gates on auth, `LoginPage` handles hardcoded login, `KanbanWithSidebar` composes the board with `AIChatSidebar`.
- `components/KanbanBoardAPI.tsx` — the persisted, API-backed board (replaces the older local-state-only `KanbanBoard.tsx`/`lib/kanban.ts` demo, which is still used in its own tests). Talks to the backend via `lib/api.ts`.
- `lib/auth.ts` — stores the JWT (e.g. localStorage), exposes `API_BASE_URL` and `getAuthHeader()` used by `lib/api.ts`.
- `lib/api.ts` — typed fetch wrapper (`apiCall`) for all board/column/card/AI endpoints; normalizes HTTP and network errors into `ApiError`.
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

## Coding standards (from AGENTS.md)

- Use latest stable versions of libraries and idiomatic patterns.
- Keep it simple: no over-engineering, no unnecessary defensive programming, no speculative features.
- Be concise; no emojis, ever.
- When debugging, find the root cause before attempting a fix — don't guess.
