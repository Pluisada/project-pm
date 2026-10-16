# Changelog

User-facing changes, newest first. For design decisions, test results and known gaps behind each entry, see the matching part in `docs/PLAN.md`.

## 2026-10-16

- **Login no longer reveals which usernames exist by timing.** Unknown usernames and accounts without a usable password now cost one bcrypt check against a dummy hash, like a wrong password (`auth.verify_login_password`).
- **`PM_ENV=production`** (opt-in): the app refuses to start unless the public URL is a non-local `https` origin, `PM_PUBLIC_URL` and `APP_BASE_URL` agree, CORS origins are `https`, no catch-all trusted proxy is set and `PM_E2E` is off; `/docs`, `/redoc` and `/openapi.json` are not served. docker-compose passes `PM_ENV` through.
- `docs/SECURITY.md` corrected (invited accounts are unverified until the emailed link; container hardening was verified on Docker Desktop). `docs/DEPLOYMENT.md` gains the reverse-proxy prerequisites, the administrator activation/recovery options and the backup, migration and restore procedure (documentation only; nothing was run against the commercial database).

### Deal Value for the current and the next year
- **A lead now has two Deal Values**, one for its current year and one for the next, in the same currency. The edit form shows both; the card shows their sum. Existing amounts become the current-year value and the next-year value starts at 0.
- **Action needed before going live:** back up, then run `migrate_deal_years.py --year <year>` (dry run first); the app refuses to start on a database without it. Moving to a new year is an explicit step too (`migrate_deal_year_rollover.py`); nothing changes by itself on 1 January.
- **Deal Value is always in US dollars.** The currency selector is gone from the lead form, the backend only accepts USD, and every Deal Value is shown and totaled as USD. Existing amounts are kept exactly as stored and read as USD, with no conversion, whatever currency code they had before (no migration is needed; the old codes are simply ignored). Bid revenue keeps its own currency.
- **Reports and the Dashboard show both years.** Pipeline, won, per-stage, per-salesperson/product line/source, closing-soon, aging and "value today" figures give the two-year total with the current-year and next-year amounts beneath, in USD, labelled with the actual years. Closing values recorded before the split stay a single amount, since they can't be divided by year.
- **Details are shown on the compact card** again (read-only, scrolls when long); they are edited in the lead edit form.

## 2026-10-15

### Bid import: safer repeats and guarded database targets
- **Repeating an import that changes nothing is now a no-op.** No batch, ledger or history row is written, so it can no longer block undoing the earlier import. The Import Bids screen says "Nothing to import".
- **Database writes for the commercial import are guarded.** `migrate_bid_cpm.py --apply` needs the approved file's SHA-256 and the new `commercial_import.py` (check, backup, restore, import, verify) refuses the development databases, symlinks, missing files and any unexpected state; the batch record names the technical operator alongside the administrator.

### Security: sessions, emailed links, passwords and card ownership
- **Sign out now really signs you out, on every device.** Logout (and a password change or reset) ends all of that account's sessions. Sessions last 8 hours instead of 24. **Action needed:** tokens issued before this deployment are rejected, so everyone signs in again once.
- **Sign-up and reset links are single-use and stored hashed.** Sign-up creates the account only when the emailed link is used (that verifies the email); "Forgot my password?" shows the same message whether or not the account exists and no longer shows the address.
- **No links in logs.** Without SMTP configured nothing is sent and nothing sensitive is logged, so sign-up and password reset need working email settings.
- **Passwords need 8-128 characters with an upper-case letter, a lower-case letter, a number and a symbol** (setup, sign-up, reset, Manage Users and the admin tool).
- **Card editing follows ownership.** Members edit only leads assigned to them (leads they create are assigned to them); unassigned leads are admin-only; only admins assign, reassign or delete, and those actions are audited.
- **Moving a card follows ownership too.** Members move only leads assigned to them (and only forward); administrators move any lead. The same applies to the AI assistant and to Undo. A lead you create is logged as created and assigned to you.
- **Manage Users now invites by email.** Administrators enter an email address; the person gets a single-use link that verifies the address and lets them choose a password. Nobody can sign in before that.
- **First-run setup is closed by default.** The setup screen needs a setup key (`PM_SETUP_TOKEN`, set by whoever runs the server, then removed). See `docs/SECURITY.md`.
- **Operators:** run `migrate_auth_security.py --database <path>` (dry run, then `--apply`) on existing databases; the app refuses to start on one that has not been migrated. `create_commercial_db.py` now takes `--email` and creates the administrator without a password; the owner sets it with "Forgot my password?". Details in `docs/SECURITY.md` section 10.

## 2026-10-14

### Security: access tokens are bound to the current password
- Changing or resetting a password now signs that account out everywhere: access tokens carry a fingerprint of the password (`pwh`) and are rejected when it no longer matches.
- **Action needed:** tokens issued before this deployment have no `pwh` and are rejected, so every user must sign in again once after deploying.

### Security: container hardening, explicit CORS and security headers
- The container runs as an unprivileged user with all capabilities dropped, `no-new-privileges` and a healthcheck; port 8000 is published on `127.0.0.1` by default (`PM_BIND_ADDRESS=0.0.0.0` restores direct exposure). Not verified in a running container (no Docker daemon).
- CORS no longer allows every origin: `CORS_ALLOWED_ORIGINS` lists explicit origins (default: local dev frontend; empty in Docker = same-origin only).
- Every response carries CSP, `nosniff`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`; HSTS optional via `HSTS_ENABLED`. See `docs/SECURITY.md` section 9.

### Docker: a wrong commercial database path no longer creates an empty database
- With `PM_DB_HOST_PATH` set (or `PM_REQUIRE_EXISTING_DB=1`) the app refuses to start unless the database file exists with a complete schema, and says why. Development setup unchanged. Error table in `docs/DEPLOYMENT.md`.

### Docker: configurable database and proxy settings
- **Docker can now run on a separate commercial database** through `PM_DB_HOST_PATH`, `PM_DB_CONTAINER_PATH` and `PM_DATABASE_URL`, and receives `RATE_LIMIT_TRUSTED_PROXIES` and `PM_PUBLIC_URL` (emailed link base). With none set, behaviour is unchanged (`./pm.db`). Steps and the proxy/client-IP guidance are in `docs/DEPLOYMENT.md`; not yet verified on a real server.

### Rate limiting
- **Login, sign-up, forgot-password and Bid import are now rate limited.** Too many attempts get a "Too many requests" error with a retry time; repeated failed logins from one address earn that address a growing delay (5 s doubling to 5 min) for that account only, so nobody can lock others out. Responses never say whether an account exists. Limits are configurable; production notes in `docs/SECURITY.md` (section 7).

### Clean commercial database procedure
- **A script now creates a new, empty commercial database** separate from the development one: full schema, the Salcomp board with its stages, and the first admin (password prompted, never stored in code). It refuses to overwrite an existing file and creates no cards. Nothing changes for users; the commercial database itself has not been created yet. See `docs/DATABASE.md`.

### Salcomp Bid import: end-to-end tests (PLAN Part 22G)
- **Part 22 is complete:** the Import Bids screen now has browser tests covering preview (writes nothing), decisions, confirmation, re-import, updates, refused files and rows, stale or forged confirmations and the history tab. Nothing changes for users.
- **Not yet used on real data:** the import has only been tried with fictitious workbooks.

### Salcomp Bid import: screen (PLAN Part 22F)
- **Admins now have an "Import Bids" screen** (members don't see it). Choose the `.xlsx`, preview it, and nothing is saved until you confirm.
- **Review before confirming:** a summary (new cards, updates, rows needing a decision, revenue per currency), then every row with its errors, warnings and changed fields, filterable.
- **Decisions on screen:** pick a stage, accept the proposed one, confirm an update or a stage move, or skip a row. Changing a decision means updating the preview before you can confirm.
- **History tab:** every attempt on the board, completed or failed, with its counts and, for failures, what went wrong.

### Salcomp Bid import: API endpoints (PLAN Part 22E)
- **Admins can now import the Bids report over the API** (no screen yet). Members can't upload, preview, confirm or see import records.
- **Preview first:** uploading the `.xlsx` shows what would happen to each row (create, update, ignore or review), the errors and warnings, and which decisions are still needed. Nothing is saved.
- **Then confirm:**
  - re-send the same file and decisions with the preview's token;
  - the import is refused if the file, the decisions or the board changed since the preview, or if it was already confirmed.
- **Cancelled or oversized uploads clean up after themselves:** the temporary files of an upload are closed immediately when it is cut off for size, the connection drops or the request is refused, not whenever Python gets to it.
- **Import history:** each board lists its import attempts (completed and failed) with their counts; each attempt shows its details.
- Only `.xlsx` files up to 10 MB are accepted, and the file itself is never stored.

## 2026-10-13

### Import history (PLAN Part 22D follow-up 2)
- **Recorded:** when a Bid import updates an existing opportunity, the fields it actually changed (old and new values), the import batch and the user are recorded in a dedicated import history.
- **Not a member edit:** an import never appears as a member edit, and the "M" badge keeps meaning "a member changed this".
- **What gets no entry:** new opportunities, unchanged rows and updates that weren't confirmed.
- **Undo** keeps refusing any reversal that would overwrite a later change, whoever made it.

### Undo safety and import traceability (PLAN Part 22D follow-up)
- **Undo no longer overwrites later changes.** If someone else, the AI or a Bid import changed the card after your change, undo is refused with a clear message and their change is kept. Undo still works normally when nothing changed since.
  - An undo saved before this update can't be checked and is refused once.
- **Failed import attempts are now recorded**, with no imported data kept. Each record holds:
  - who and which board;
  - the file's hash;
  - the type of failure (validation, conflict or unexpected) and a short reason without the file's content.
- **A Bid already imported into another board:** the conflict message now names the codes and the board.

### Salcomp Bid import: transactional import service (PLAN Part 22D)
- The backend can now apply a confirmed import preview. Everything happens in one transaction, so a failure leaves nothing behind. The transaction:
  - records the import batch (file name and hash, user, board, mapping and counts);
  - creates the authorized cards with their Bid details and source references.
- **Imported cards get a "baseline" history event, dated at the import.** They don't count as new, won or lost opportunities in the KPIs.
- **Updates to already-imported cards:**
  - they need explicit confirmation;
  - they only change imported fields; deal value, contact, salesperson and the stage set in the app are kept;
  - a stage move needs its own confirmation.
- **Safe to retry:** confirming the same preview twice, or confirming after the data changed, is refused and creates nothing.
- **Confidence label:** the original label (e.g. "LOW (10%)") is now kept with each imported card.
- **No screen or endpoint yet.**

### Salcomp Bid import: mapping and preview engine (PLAN Part 22C)
- **Proposed action per row:** the backend can now work out, for each row of the Bids report:
  - the proposed Kanban stage;
  - whether the row would be created, updated, ignored or needs review;
  - which explicit decisions are still missing.
- **Stage mapping** follows the approved rules:
  - WON -> Award, LOST -> Lost;
  - OPEN/Open CE by stage -> F2F Meeting, RFI or Proposal.
- **Rows that are never imported automatically:**
  - CLOSED + NOT AWARDED BY CUSTOMER, which needs confirmation;
  - CLOSED (BY SALCOMP);
  - contradictory or unknown combinations;
  - duplicate Código + Cliente pairs;
  - invalid rows;
  - stage moves of already-imported cards.
- **Re-imports** are compared by content and source dates. Older files never overwrite newer data.
- **Annual revenue** stays in its own fields and is never used as the card's deal value. No users are created from CPM or project lead.
- **Still read-only:** nothing is written, and there is no screen or endpoint yet.

### Salcomp Bid import: Excel reader (PLAN Part 22B)
- The backend can now read and check the Salcomp Bids report (`.xlsx`):
  - it finds the sheet and checks the 31 columns;
  - it validates dates, volumes, revenue and confidence;
  - for each row, it reports errors and warnings by row and column, without repeating the cell's content.
- **Files it refuses:**
  - macro-enabled, legacy or renamed files;
  - corrupted or oversized files;
  - files over 5,000 rows;
  - files with unsafe XML.
- **Revenue is read exactly**, with no rounding through floating point.
- **Nothing is imported or changed yet.** There is no screen or endpoint, and no card, user or history is created.
- **New backend dependencies:** `openpyxl` and `defusedxml`.

### Salcomp Bid import: database foundation (PLAN Part 22)
- **New database tables** for importing the Salcomp Bids report:
  - **import batches:** a record of each import;
  - **Bid details per card:** plant, product, volumes, annual revenue, next step, close reason;
  - **source references:** the card's Bid code/customer, unique per source.
- **The import itself isn't available yet:** there is no Excel upload, screen or endpoint, and nothing fills these tables.
- **Exact revenue:** annual revenue is stored exactly in cents. It is never rounded silently and never mixed with a card's deal value.
- **Action needed for existing databases:** run `backend/migrate_import_tables.py --database <file>` once, as a dry run first. Then add `--apply`, which backs up and then migrates. The app refuses to start until this is done, and also refuses a database whose import tables are incomplete or different from the expected ones. See `docs/DATABASE.md`.

### Executive Dashboard (PLAN Part 21)
- New **Dashboard** screen for all users, with the following sections:
  - **Filters:** last 30 days, last 90 days, year to date or a custom range, with optional comparison to the previous period.
  - **Headline numbers:** won, lost, win rate, sales cycle, new opportunities and open deals.
  - **Closing values by outcome:** the value recorded at closing, deals whose value is unavailable, and today's value for comparison, shown separately.
  - **Conversion.**
  - **Time in stage.**
  - **Open pipeline by stage.**
  - **Aging.**
  - **Data quality.**
- Partial or missing history is labelled as such ("partial history", "not tracked", "at least"), never shown as zero. Values stay per currency.

### Closing values in stage history (PLAN Part 20)
- Every stage change now records the deal value and currency at that moment. Won/lost value KPIs use the value at closing, so later edits (or deleting the card) no longer change past results. Today's value is shown separately.
- Older events have no recorded value. They are reported as "value unavailable", never filled in with today's value.
- **Action needed for existing databases:** run `backend/migrate_stage_event_values.py` once (dry run first, `--apply` backs up then migrates). The app refuses to start until this is done. See `docs/DATABASE.md`.

### KPI engine and API (PLAN Part 19)
- New read-only endpoints at `/api/boards/{id}/kpis/`:
  - `summary`: won/lost deals and values, win rates, the creation cohort and the sales cycle, optionally compared with the previous period;
  - `conversion`;
  - `stage-time`;
  - `aging`;
  - `pipeline`.

  There is no screen for them yet; the dashboard comes next.
- Built only on recorded stage history. Cards that were already in a stage when tracking started are never counted as wins or losses, and their time in that stage is shown as "at least", never estimated. Values stay per currency.
- Every formula is defined in `docs/KPIS.md`.

### Card stage history (PLAN Part 18)
- Every stage change of a card is now recorded with who did it and how (UI/API, AI assistant, undo): creation, moves, wins, losses, reopenings and deletion. Groundwork for future pipeline reports; nothing new is shown on screen yet.
- Undo adds a new, compensating entry instead of rewriting history; deleting a card keeps its history.
- Card ids are no longer reused after a deletion.
- **Action needed for existing databases:** run `backend/migrate_stage_events.py` once (dry run first, `--apply` backs up then migrates). Existing cards get a "baseline" entry for their current stage - no past moves are invented. The app refuses to start until this is done. See `docs/DATABASE.md`.

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
