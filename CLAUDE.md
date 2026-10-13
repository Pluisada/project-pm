# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.


## Project

A sales lead tracker for Salcomp, built on a Kanban board: each card is a lead and each column a sales stage (1st Contact, F2F Meeting, RFI, Proposal, Award, Lost). Each column's commercial meaning is its `stage_kind` (`open` / `won` / `lost`), never its title: titles are display text an admin can rename without changing any report. Cards carry structured lead fields (company, contact, deal value + currency, expected close date, lead source, product line, assigned salesperson), and a Reports screen aggregates them. Also: drag-and-drop, multi-user auth (admin/member roles), and an AI chat sidebar that can create/edit/move cards and fill in lead fields. Next.js frontend, FastAPI backend serving the static Next.js export at `/`, SQLite database, packaged as a single Docker container. AI calls go through OpenRouter (`openai/gpt-oss-120b`).

Auth: the first user ever created (via a dedicated setup screen) becomes admin; further admins are only ever created or promoted with the server-side `backend/admin_cli.py` (no HTTP path grants admin); an admin can create further users (always role "member") from a "Manage Users" screen, and anyone can self-sign-up by email (also always "member"): the sign-in screen's "Sign up" asks for an email, the backend emails a link (`/?signup_token=<jwt>`), and the page it opens sets the password and then returns to the login screen. Self-signed-up users' email is their username. "Forgot my password?" on the login screen takes a username or email, says whether that account exists (deliberately - unlike signup, which never reveals it), and emails a 1-hour reset link (`/?reset_token=<jwt>`) to accounts that have an email; admin-created and setup users have none and get an "ask an admin" message. Boards are shared across all authenticated users — not isolated per owner — but the board's structure (creating/editing/deleting boards and columns) is admin-only. `SECRET_KEY` must be set (no default; the app refuses to start otherwise) — see `docs/SECURITY.md`. Beyond user creation, role also gates card operations: members can create cards and move them only to a column further right (or reorder within a column), never back to an earlier column; only admins can delete cards or move them backward. A member's edit to a card's title/details is logged to `card_edit_history` and flips the card's `has_member_edits` flag (shown as an "M" badge with a history modal in the UI); admin edits are not logged. These rules apply identically whether the change comes from the REST routes or the AI chat sidebar — both funnel through `backend/card_rules.py` so the two can't drift. Local-only via Docker.

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
npm run test:e2e     # Playwright: starts its OWN backend (:8001, PM_E2E=1, temp SQLite, random SECRET_KEY) and frontend (:3001); never touches pm.db
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
The Dockerfile builds the frontend (`next build` → static export), copies the output into `backend/static/`, and runs the FastAPI app with uvicorn on port 8000. `OPENROUTER_API_KEY` and `SECRET_KEY` must be set in `.env` at the repo root (see `.env.example`); docker-compose passes them through (`SECRET_KEY` via `${SECRET_KEY:?...}`, so compose fails without it) and mounts `./pm.db` for persistence.

## Architecture

### Request flow
In production (Docker), FastAPI (`backend/main.py`) mounts the static Next.js export at `/` and exposes JSON routes under `/api/*`. In local dev, the two run separately (Next dev server on :3000, FastAPI on :8000) and the frontend's `lib/api.ts`/`lib/auth.ts` talk to `API_BASE_URL`.

### Backend (`backend/`)
- `main.py` — FastAPI app setup, CORS, startup hook (`init_db` only — users/boards are created via `/api/setup`, not seeded at boot), auth endpoints (`/api/login` — matches username or email, `/api/logout`, `/api/user`, `/api/setup/status`, `/api/setup`, `/api/signup`, `/api/signup/complete`, `/api/password/forgot`, `/api/password/reset`; `_find_user` is the shared username-or-email lookup), static file mount.
- `auth.py` — `SECRET_KEY = load_secret_key()` at import: raises `InsecureSecretKeyError` if the key is missing/empty, in `INSECURE_SECRET_KEYS` (old default, `.env.example` placeholder) or shorter than 32 chars — no fallback, never echoed. JWT issuing/verification (`python-jose`), including purpose-scoped email-link tokens (`create_purpose_token`/`decode_purpose_token`; signup links are 24h and single-use because completing one creates the user; reset links are 1h and carry `pwh = password_fingerprint(password_hash)`, so changing the password invalidates them), password hashing (`hash_password`/`verify_password`, via `bcrypt` directly — not `passlib`, which has a known incompatibility with `bcrypt>=4.1`). No DB access here.
- `mailer.py` — `send_email()` over SMTP (STARTTLS) configured by `SMTP_*` env vars; with `SMTP_HOST` unset it logs the message (including the signup link) instead, which is how local dev works. `APP_BASE_URL` (in `auth.py`) sets the host in emailed links: `http://localhost:3000` for `next dev`, `http://localhost:8000` in Docker.
- `admin_cli.py` — operator CLI (`create` / `promote` / `demote` admins). Passwords only via no-echo prompt or `--password-stdin`, never argv; `create` refuses existing usernames; `demote` refuses the last admin; confirmation by retyping the username. Runs `init_db()` on `DATABASE_URL`.
- `conftest.py` — sets a random `SECRET_KEY` and a temp-file `DATABASE_URL` for every pytest run, so backend tests can't reach `backend/pm.db` (the default URL resolves there with a cwd of `backend/`).
- `deps.py` — the single `get_current_user` (parses `Authorization`, verifies the JWT, loads the `User` row) and `require_admin` (403s unless `role == "admin"`), used by both `main.py` and `routes.py`. Role is re-read from the DB on every request, not embedded in the JWT.
- `database.py` — SQLAlchemy engine/session (SQLite by default, swappable via `DATABASE_URL`), `init_db()` creates tables, runs `_add_missing_card_columns` (ALTER TABLE for the nullable lead columns an older `cards` table lacks - there's no migration tool) and `require_stage_kind_column` (raises `StageKindMigrationRequired` instead of adding `columns.stage_kind` itself - that one decides won/lost, so it's migrated explicitly with `migrate_stage_kind.py`). With `PM_E2E=1` it also runs `check_e2e_database` at import: `DATABASE_URL` must be an explicit sqlite file under the system temp dir (never either `pm.db`), else `E2EDatabaseError`; `seed_default_board(db, creator)` seeds the shared default board (the 6 sales stages + sample leads, via `seed_pipeline`) once, called from `POST /api/setup` — not at startup.
- `models.py` — ORM models: `User` (with `role`: `UserRole.ADMIN`/`MEMBER`) → `Board` → `BoardColumn` (aliased as `Column`) → `Card`, plus `ConversationMessage` (AI chat history), `CardAction` (audit trail of AI-driven board changes — defined but never written), and `CardEditHistory` (member-edit audit trail; `Card.has_member_edits` is a Python property derived from `bool(self.edit_history)`, not a stored column). All children cascade-delete from their parent.
- `lead_options.py` — allowed lead values (`CURRENCIES`, `LEAD_SOURCES`, `PRODUCT_LINES`), stage kinds (`STAGE_OPEN`/`STAGE_WON`/`STAGE_LOST`, `STAGE_KINDS`, `StageKind`) and the seeded pipeline `PIPELINE_STAGES` (title, kind); imported by schemas (as `Literal` types), reports, seeding and the AI prompt.
- `reports.py` — `build_board_report()`, Python-side aggregation for `GET /api/boards/{id}/report`. Money totals are always per currency (no conversion).
- `migrate_stage_kind.py` — explicit migration adding `columns.stage_kind` to a pre-Part-17 database. Dry run by default (read-only connection); `--apply` backs up to `<name>-pre-stage-kind-<time>.db.bak`, then classifies in one transaction with exactly the old report rule (title exactly "Award" → won, exactly "Lost" → lost, else open), refusing ambiguity (duplicate titles on a board) until `--set COLUMN_ID=KIND`, and verifying cards/columns are unchanged.
- `convert_to_sales_board.py` — one-off script that replaces an existing board's columns/cards with the sales stages and sample leads (users untouched). Back up `pm.db` first.
- `undo.py` — single-level, per-user undo. Every card create/update/move/delete (REST route or AI action) records its inverse steps in that user's one `UndoEntry` row, replacing the previous one; an AI request is one entry. `apply_undo` validates all steps before applying any (409 if a card was deleted since) and always uses the entry up. Undo deliberately bypasses `can_move_card`: it restores a user's *own* last change, so a member can put back a card they advanced. Undoing a delete recreates the card with a new id; its edit history is not restored. Endpoints: `GET`/`POST /api/boards/{id}/undo`.
- `card_rules.py` — the single source of truth for member/admin card permissions (`can_move_card`, `diff_card_changes`, `apply_card_updates`, `record_member_edit`). `apply_card_updates` is the only path that writes card content/lead fields, for both REST and AI, imported by both `routes.py` and `ai_kanban.py` so the REST routes and the AI chat sidebar enforce identical rules.
- `schemas.py` — Pydantic request/response models mirroring the ORM models (`*Create`, `*Update`, `*Response`, `BoardDetail`/`ColumnWithCards` for nested board fetches, `SetupRequest`/`UserCreate`/`UserResponse` for auth, `CardEditHistoryEntry` for the history endpoint).
- `routes.py` — REST CRUD for boards/columns/cards under `/api` (board and column create/update/delete use `Depends(require_admin)`; `POST /api/ai/test` too, since it makes a paid call), the AI chat endpoint `POST /api/boards/{id}/ai`, and admin-only user management (`GET`/`POST /api/users`, via `Depends(require_admin)`). Boards are shared, not owned — routes look up `Board.id` only, never filter by who created it. `delete_card` and the backward-move branch of `move_card` are admin-only (via `card_rules`); `GET .../cards/{id}/history` returns a card's `CardEditHistory` entries.
- `ai.py` — low-level OpenRouter client wrapper (`call_ai`, `test_ai_connectivity`, `AIError`).
- `ai_kanban.py` — builds board-state context and conversation history for the AI, defines the system prompt requiring structured JSON output (`{response, actions[], confidence}`), and applies returned `actions` (create/update/move/delete) back onto the board via `apply_board_actions`, which takes the requesting `current_user` and applies the same `card_rules` checks as the REST routes (a member can't get the AI to delete a card or move it backward on their behalf).
- Tests are colocated (`test_*.py`), run with `pytest` + `TestClient`.

### Frontend (`frontend/src`)
- `app/page.tsx` → renders the top-level component tree; `ProtectedRoute` gates on setup-status/auth (`SetupPage` when no users exist yet, else `LoginPage`), `KanbanWithSidebar` composes the board with `AIChatSidebar`, `ManageUsersPage` is admin-only (toggled from the header). `SignupPage` (email request) and `ForgotPasswordPage` are reached from `LoginPage`; `SetPasswordPage` (password + confirm, parameterized by title and submit function) is shown when the URL has `?signup_token=` or `?reset_token=` (read from `window.location.search` in the bootstrap effect, not `useSearchParams`, which would need a Suspense boundary under static export).
- `components/KanbanBoardAPI.tsx` — the persisted, API-backed board (replaces the older local-state-only `KanbanBoard.tsx`/`lib/kanban.ts` demo, which is still used in its own tests). Talks to the backend via `lib/api.ts`; receives `role` as a prop (threaded down from `ProtectedRoute`) to gate card deletion and to block a member's backward drag client-side before the backend's 403 (defense in depth, not the real gate).
- `KanbanBoardAPI.tsx` has a sticky Undo bar at the top of the board: it re-reads `GET .../undo` after every successful change and reloads the whole board after an undo. Its root uses `overflow-clip`, not `overflow-hidden` - the latter makes it the scroll container and the bar stops sticking.
- `components/LeadEditModal.tsx` — the lead fields form (opened by a card's "Edit lead" button); sends only changed fields, with `null` to clear. `components/ReportsPage.tsx` — KPI tiles, leads-by-stage bars, closing-soon table and breakdowns, with salesperson/product filters; opened from the header "Reports" button (all roles). Selects are fed by `GET /api/lead-options`, which members can read (unlike admin-only `GET /api/users`).
- `components/KanbanCard.tsx` — title/details are always-editable `<input>`/`<textarea>` (committed on blur, not per-keystroke, to avoid an API call — and a member-edit-history row — per character typed); dragging is via a dedicated handle button, not the whole card, since the card's surface is otherwise all editable text. Shows an "M" badge when `card.hasMemberEdits`, opening `CardHistoryModal` (the project's first true overlay/modal — no prior modal pattern existed).
- `lib/auth.ts` — stores the JWT + role (e.g. localStorage), exposes `API_BASE_URL`, `getAuthHeader()`, `getSetupStatus()`/`setupAdmin()`, and `isAdmin()` (UI-only gating — the backend independently re-checks role on every admin request).
- `lib/api.ts` — typed fetch wrapper (`apiCall`) for all board/column/card/AI/user endpoints; normalizes HTTP and network errors into `ApiError` (`getErrorMessage(err, fallback)` narrows a caught `unknown` error down to its `.detail`).
- Drag-and-drop uses `@dnd-kit` (core + sortable); column/card components (`KanbanColumn`, `KanbanCard`, `KanbanCardPreview`, `NewCardForm`) are shared between the demo and API-backed boards. Card/column titles are Playwright-unfriendly for `getByText` (they're input values) — e2e tests match cards via a `data-title` attribute instead.
- Styling: Tailwind CSS 4, with the app color palette defined as CSS variables in `app/layout.tsx` (`--primary-blue #209dd7`, `--secondary-purple #753991`, `--accent-yellow #ecad0a`, `--navy-dark #032147`, `--gray-text #888888`).

### Docs
`CHANGELOG.md` (repo root) is the short, dated, user-facing list of what changed; `docs/SECURITY.md` is the operational security guide (SECRET_KEY, admin provisioning, test-vs-real credentials, permission matrix, validation); `docs/PLAN.md` is the working plan for how the project was built in parts, with each part's decisions, tests and known gaps; `docs/DATABASE.md` documents the schema; `docs/schema.json` is the machine-readable schema. Check `docs/PLAN.md` before making structural changes.

## Status and known gaps (see `docs/PLAN.md` for full history)

Parts 1-17 are implementation-complete (11: multi-user auth, 12: email self-signup, 13: forgot/reset password, 14: sales lead tracking + reports, 15: undo, 16: security hardening, 17: stable stage identity + isolated E2E). Verification has been piecemeal (pytest, Playwright, curl, browser checks feature by feature). Be aware of these gaps before assuming something works:

- **Docker path is unverified.** All testing so far has run the backend and frontend natively (`uvicorn` on :8000, `next dev` on :3000) as separate processes, not through `Dockerfile`/`docker-compose.yml`. Don't assume the Docker build works without checking.
- No markdown rendering, card-change animations, or mobile sidebar collapse in the AI chat sidebar.
- `docs/PROMPTS.md` / `docs/AI_SCHEMA.md`, referenced in the Part 9 plan, were never written — the system prompt and action schema live inline in `ai_kanban.py`.
- Test coverage is not formally measured; the existing suite did not catch any of the runtime bugs below.
- Backend: 3 tests fail independently of recent work (`test_ai.py` x2 - fully mocked, no network calls, but the mocks don't match `call_ai`'s current behavior; `test_static_files.py::test_root_path_serves_html` needs a built `backend/static`). Frontend `npx tsc --noEmit` reports 17 errors, all in `KanbanBoard.test.tsx`/`kanban.test.ts` (Vitest globals not typed in `tsconfig.json`); app code is type-clean.
- Existing databases need `migrate_stage_kind.py --apply` once (the app refuses to start until then) - see `docs/DATABASE.md`. The dev `backend/pm.db` has not been migrated yet.
- No rate limiting on login/signup/forgot-password; login tokens live in localStorage (24h) with no revocation, including after a password reset.
- E2E runs serially (`workers: 1`): all tests share one throwaway board. Parallel workers would need per-test boards first.
- `playwright.docker.config.ts` is guarded too and therefore refuses to run against the standard compose setup (which mounts the real `./pm.db`) until a Docker test profile with `PM_E2E=1` exists.
- Email delivery uses real SMTP when `SMTP_HOST` is set in `.env` (verified with Gmail + an app password); without it, signup/reset links are only logged by the backend.
- Card moves aren't recorded anywhere (`CardAction` is still never written), so time-in-stage and stage-conversion reports aren't possible yet.
- `pm.db.bak` (made before converting the board to the sales pipeline) is a local backup, ignored by git.

### Non-obvious bugs already fixed (don't reintroduce)
- `models.py`: a model class literally named `Column` shadowed SQLAlchemy's own `Column` import and broke under SQLAlchemy 2.0.36's type-annotation syntax — now aliased as `BoardColumn` (`Column = BoardColumn` for back-compat). `ConversationMessage.meta_data` is named that way (not `metadata`) because `metadata` collides with `Base.metadata`.
- `ai.py`: OpenRouter's base URL is `https://openrouter.ai/api/v1` — `.io` 404s.
- `ai_kanban.py`: `call_ai_with_board()` must actually prepend `{"role": "system", "content": SYSTEM_PROMPT}` to the message list (plus `response_format: {"type": "json_object"}` in `ai.py`'s `call_ai()`) or the model free-forms text instead of structured JSON, and `apply_board_actions()` silently never runs.
- `KanbanBoardAPI.tsx`: columns and cards are separate SQL tables with independent autoincrement ids, so a column and a card can share a numeric id. dnd-kit keys all draggables/droppables in one id-keyed map, so raw numeric ids collide. Ids are prefixed (`col-${id}` / `card-${id}`) before being handed to `KanbanColumn`/`KanbanCard`, then translated back to numeric ids in callbacks — don't strip that prefixing.
- `auth.ts` / `api.ts`: use the `API_BASE_URL` constant, not relative paths — relative `/api/...` calls only work when frontend and backend share an origin (the Docker/static-export setup), and 404 against Next.js itself when run as separate dev servers.
- `routes.py` used to have its own `get_current_user` that ignored the `Authorization` header and always loaded a hardcoded username — meaning no board/column/card/AI route ever validated a bearer token. Fixed by unifying on `deps.get_current_user`; don't reintroduce a second, route-file-local auth dependency.
- Backend tests using an in-memory SQLite engine (`create_engine("sqlite:///:memory:")`) need `poolclass=StaticPool` + `connect_args={"check_same_thread": False}`, or `TestClient` requests intermittently fail with `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread` — see `test_auth.py`/`test_routes.py`'s `test_db` fixture.
- `ai_kanban.py`'s `apply_board_actions()` must receive and check `current_user` for every action — it's a second entry point onto the same cards as the REST routes, so skipping the `card_rules` checks there (as an early version did) lets a member delete/backward-move/untracked-edit a card just by asking the AI to do it, silently bypassing the REST routes' 403s.
- `auth.py` used to fall back to a hardcoded `SECRET_KEY` (public in this repo) when the env var was unset - anyone could forge an admin token. Never reintroduce a default; `load_secret_key()` refuses missing/known/short keys. Board/column write routes and `/api/ai/test` used to accept any logged-in user (a self-signed-up member could delete the whole board) - they're `require_admin` now; keep structural changes admin-only.
- `auth.py`: `verify_token` must reject any JWT carrying a `purpose` claim. Signup-link tokens share `SECRET_KEY` and a `sub` (the email, which is also the username), so without that check a signup link would work as a bearer token once its account exists.
- Reports used to classify won/lost by comparing column titles with "Award"/"Lost", so renaming a column silently changed the KPIs. Always key on `Column.stage_kind`. Backend rules (`routes._check_stage_kind`): at most one won and one lost stage per board, and a column's kind can't change while it holds cards (that would silently reclassify its opportunities).
- Playwright used to start only a frontend and hit whatever backend was on :8000 - the dev one, with `backend/pm.db`. Now `playwright.config.ts` starts its own backend with `PM_E2E=1` on a temp DB, `tests/helpers.ts` has no URL fallback, and `tests/e2e-setup.ts` refuses a backend whose `/api/health` lacks `test_mode: true` and fails the run if a dev `pm.db` changed. Don't add a fallback URL. The E2E `next dev` uses `distDir` `.next-e2e` (`PM_E2E_DIST_DIR`), because `next dev` locks its dist dir and the developer's own server holds the default one.
- E2E drags: `dragCardToColumn` waits for the `drag-overlay` testid to detach before and after each drag. dnd-kit ignores a new drag while the previous drop animation runs, so dragging the same card twice in quick succession silently did nothing. Column title testids must not start with `column-` (tests select columns by that prefix) - the member read-only title is `stage-title`.
- `KanbanColumn.tsx`: the column title is committed on blur/Enter (a `draft` state that falls back to the saved title), not per keystroke - per-keystroke saves fired an API call per character. Members get a plain heading, not an input.
- `KanbanBoardAPI.tsx` collision detection is `pointerWithin` with a `closestCenter` fallback, not plain `closestCenter`: lead cards are tall, one long column stretches every column in the grid row, and `closestCenter` then measures a drop into a short column's empty area against that column's far-away center - the card silently snaps back into another column.
- `main.py` `reset_password`: the `pwh` fingerprint check is what makes reset links single-use - there's no token table. Drop it and a link stays usable for its full hour, even after it's been used.
- `KanbanCard.tsx`: title/details became `<input>`/`<textarea>`, not static text — (1) don't spread dnd-kit's drag `listeners`/`attributes` across the whole card, only a dedicated handle button, or clicking into a field to edit competes with picking the card up; (2) in a narrow column, an unconstrained `flex-1 min-w-0` content div can get squeezed to literally 0 width by fixed-size siblings (the drag handle, delete button) — the action row lives *above* the content, not beside it, so title/details always get the card's full width; (3) e2e/unit tests must match cards via `getByDisplayValue`/`[data-title=...]`, not `getByText` (an input's value isn't text content).

## Coding standards (from AGENTS.md)

- Use latest stable versions of libraries and idiomatic patterns.
- Keep it simple: no over-engineering, no unnecessary defensive programming, no speculative features.
- Be concise; no emojis, ever.
- When debugging, find the root cause before attempting a fix — don't guess.
