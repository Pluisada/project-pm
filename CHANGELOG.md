# Changelog

User-facing changes, newest first. For design decisions, test results and known gaps behind each entry, see the matching part in `docs/PLAN.md`.

## 2026-10-13

### Stable stage identity and isolated E2E tests (PLAN Part 17)
- Each stage now has a fixed type: open, won or lost. Reports count won and lost deals by that type, so renaming a column (e.g. "Award") no longer changes any number. Won and Lost columns show a small badge.
- Only admins can rename columns. The new title is saved when you leave the field, not on every keystroke. Members see the title as plain text.
- **Action needed for existing databases:** run `backend/migrate_stage_kind.py` once. It does a dry run first, and `--apply` backs up the database before changing it. The app refuses to start on an unmigrated database. See `docs/DATABASE.md`.
- `npm run test:e2e` now starts its own backend and frontend on a temporary database with a random key. It never touches your `pm.db`, and stops with an error if it detects it is connected to anything else.

### Security hardening (PLAN Part 16)
- Only admins can create, rename, reorder or delete boards and columns; members now get "403 Forbidden". Before this, any logged-in user, including anyone who self-signed-up, could delete the whole board.
- `SECRET_KEY` is required: the app refuses to start if it's missing, too short, or a known default. **Action needed:** add one to `.env` (see `docs/SECURITY.md`). Setting it logs everyone out and invalidates pending signup/reset links.
- New `backend/admin_cli.py` to create, promote or demote admins from the server, with passwords entered at a hidden prompt.
- `POST /api/ai/test` (a paid OpenRouter call) now requires an admin login.
- The AI assistant explicitly refuses board/column actions.
- Backend tests always use a temporary database and a random key, never `backend/pm.db`.

### Undo (PLAN Part 15)
- Undo button in a bar pinned to the top of the board, labeled with what it will reverse (e.g. `Move "X" back to RFI`).
- Reverses your last card move, card or lead edit, card creation, or card deletion (admin). One level only.
- An AI assistant request counts as one change, so one click reverses all of it.
- Per user: you can only undo your own last change. Members can undo their own forward move, though they still can't drag cards backward.
- Refused, with no changes made, if the card was deleted since.

### Sales lead tracking and reports (PLAN Part 14)
- The board is now the Salcomp sales pipeline. The stages are 1st Contact, F2F Meeting, RFI, Proposal, Award (won) and Lost.
- Every card is a lead with these fields: company, contact, deal value + currency (EUR, USD, CNY, INR, BRL, VND), expected close date, lead source, product line (Chargers, Power supplies) and assigned salesperson (a registered user).
- Cards show a compact lead summary. Open close dates in the past are shown as overdue. "Edit lead" opens a form with all fields.
- Member edits to lead fields are logged in the card's "M" history, like title/details edits.
- The AI assistant knows the stages and lead fields, and can fill them in.
- New **Reports** screen (all users):
  - KPI tiles: open leads, open pipeline, won, lost, win rate.
  - Leads-by-stage chart.
  - Leads closing in the next 30 days or overdue.
  - Breakdowns by salesperson, product line and lead source.
  - Filters for salesperson and product line.
  - Totals are per currency, never converted.
- Existing databases get the new card columns automatically on startup. `backend/convert_to_sales_board.py` converts an existing board to the stages + sample leads.
- Fixed: dragging a card into the empty lower part of a short column snapped it back to another column.

### Forgot / reset password (PLAN Part 13)
- "Forgot my password?" on the login screen takes a username or email.
- It says whether the account exists. If it does, it emails a reset link that expires after 1 hour and works only once.
- Accounts without an email (the setup admin, admin-created members) are told to ask an admin.

### Email self sign-up (PLAN Part 12)
- "Sign up" on the login screen asks for an email and sends a link to set a password. New accounts are members.
- The email is the username. Login accepts a username or an email.
- Emails go out over SMTP, configured with `SMTP_*` in `.env` (Gmail with an app password works). Without SMTP, links are printed in the backend log.
- Security: signup and reset link tokens can never be used as login tokens.

## Earlier

- Card permissions by role and member edit history ("M" badge).
- Multi-user authentication: first-run admin setup, admin-only user management, shared board (PLAN Part 11).
- Kanban board, FastAPI backend, SQLite, AI chat sidebar, Docker packaging (PLAN Parts 1-10).
