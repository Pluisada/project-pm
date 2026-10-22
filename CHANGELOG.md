# Changelog

User-facing changes, newest first. For design decisions, test results and known gaps behind each entry, see the matching part in `docs/PLAN.md`.

## Unreleased

- **Central mode: card versioning and optimistic concurrency (Phase 7B; committed locally, not pushed, not released or deployed to a server).** Every tracked card change, column move and Bid-details change raises the card's version inside the same transaction; position-only changes and renumbering do not. A card without a version row is version 0 only while the version schema is valid. The company update, move and delete routes accept an optional `expected_version`; a stale one answers 409 `card_version_conflict` and changes nothing. It is **required**: a request without it is refused with HTTP 428 `card_version_required` (after the 404/403 checks; nothing is changed). A client written for the earlier optional contract would now get 428; the central UI is the only client in the repository and always sends it. The legacy module is untouched.
- **Central mode: a missing or malformed version/review schema fails closed.** Card reads that carry versions, writes, deletes, Cards-workbook and Bid import confirmations, Bid import revert, AI and Undo answer 503 `review_not_initialized` instead of committing a change without its version; the diagnostic reports it. A database-busy error while inspecting the schema keeps the usual `database_busy` / `import_busy` answer.
- **Central mode: error handling of imports, AI and Undo.** The imports (Bid confirm, Bid revert, Cards workbook) check the schema before taking any lock and answer 503 `review_not_initialized`; if the schema breaks after that check, an import that changes or deletes existing cards is rolled back and answers the same 503 instead of `import_failed` / `revert_failed` (a Bid confirm still records its failed attempt as a failed batch). Creating a card now also validates the version schema in the flush (the new card still gets no version row and is version 0), so an import that only creates cards is refused the same way. The AI assistant aborts and rolls back the whole batch on a schema failure (it was reported as a rejected action); genuine per-action rejections are unchanged. Undo rolls back and keeps its entry after such a failure; terminal Undo refusals still consume it.
- **Central mode: the UI sends and handles card versions.** Edit, drag and Delete send the version the card had when the person started (a board refresh in between never replaces it) and use the version returned by the server afterwards. A 409 `card_version_conflict` is explained, never retried automatically and the board is re-read: the edit form keeps what was typed and turns Save off, a refused drag puts the card back, a refused delete keeps the card.
- **Central mode: AI assistant and Undo respect card versions.** The assistant is shown a version snapshot taken before the model call and refuses, per action, any update, move or delete of a card that changed since (structured `card_version_conflict` in the action result; the other actions still apply; nothing newer is overwritten). Undo entries record the version of each card; a card changed since, also back to the same visible values (A to B to A), makes Undo answer 409 `card_version_conflict`, change nothing and keep the entry.
- **Central mode: company writes are bound to their company and re-check the caller at commit (F8 mitigation, not elimination).** A session must be the authorized company's own, and the caller's membership is read again from the identity database right before every commit of a write (403 and rollback if the person was deactivated, the company deactivated or the role changed; the AI keeps working with the reduced authority of a person demoted while the model answered). A change in the identity database during the commit itself is still not seen.
- **Cards import: restoring the cards of an already-imported workbook.** The at-most-once ledger refuses a workbook whose content was applied before on a board, even when its cards were deleted since (it is keyed by workbook content and board, permanently). That rule is unchanged for ordinary imports. When it is what blocks a preview and some of the workbook's cards are missing from the board, the preview explains this and offers a separate action, **"Restore missing cards from this workbook"**; choosing it previews again in restore mode, which creates (with fresh ids from the database, never the old or deleted ones) only the rows whose filled card_id is not a card of the board, and skips without a write rows whose id matches a card here, rows without a card_id and rows already restored by an earlier import or recovery (the ledger now records source id -> new id). The preview states how many cards are created and how many rows are skipped and why; nothing is updated. Confirmation is bound to the mode, the exact plan and the highest card id ever allocated, so a stale preview, a replay or a concurrent second confirmation is refused without writing; the recovery has its own ledger row and guard, existing ledger rows are never altered, and a ledger entry written before source ids were recorded blocks recovery while any card it created still exists. Authorization, company isolation, the transaction and rollback are as for any import.
- **Cards import: restoring deleted cards is now a guided choice.** The "Create new cards for rows whose card_id is not on this board" checkbox is gone. When a preview finds card_ids that are not on the board (cards deleted since the export, or a workbook from another board), it shows a prominent warning that explains why and offers **"Create new cards for unmatched IDs"**; choosing it previews again with the existing `new_for_unmatched_ids` behavior and shows the new counts, and "Do not create them" takes the choice back. Nothing is imported by the choice itself: Confirm stays disabled while any error remains, is bound to the exact preview and choice shown, and the choice is forgotten whenever another workbook is selected. Frontend only: the backend rules are unchanged (fresh ids, matching ids are still updates, ledger, transaction and authorization as before).
- **Cards import: workbooks exported from another board (central mode; committed locally, not pushed).** A filled `card_id` that is not a card of the destination board is still an error by default (a mistyped or stale id must not silently create a duplicate), but the preview offers an explicit choice, "Create new cards for unmatched IDs" (first built as a checkbox, since replaced by the contextual prompt described in the entry above): with it, those rows are previewed as new cards (warning `source_id_new_card`, "N of the new cards come from a card_id that is not on this board") and created with fresh ids from the destination database; the file's id is never kept. A card_id that is on the board is still an update, empty ids still create, every other validation, the preview-then-confirm flow, the all-or-nothing transaction and the "workbook applied at most once per board" ledger are unchanged, and the choice is part of the plan digest (a confirmation made with a different choice than its preview is refused). Known limitation: the ids of another company's export can coincide numerically with cards of the destination board; those rows are updates, as before, and the preview lists each update's current and proposed values.
- **Central UI: top-navigation controls, review markers and the company picker (committed locally, not pushed).** Undo, "Manage boards and columns" (its panel now opens above the board) and a restored **AI Assistant** button (the existing assistant panel, now sending to the company endpoint and listing any action the server refused, including version conflicts) are in the top navigation; "Import Cards from Excel" moved to the Database screen. The large "Pending reviews" section is gone: each pending review obligation of the signed-in reviewer is a small "Review" marker on its card; the marker opens a dialog with the category, status, every recorded change (who, when, before and after) and a Clear that clears only that obligation, only after the backend confirms (a stale version or a refusal keeps the marker and says so). The company picker shows the Sales Tracker horses artwork again (`sales-tracker-login.jpg`, the login artwork; no earlier version of the picker used it, so its placement is a decision to confirm).
- **Central UI: sections and board layout (committed locally, not pushed).** The company page now has a top navigation: **Board**, **Users** (Super Admin only: the invitation and member management that used to sit under the board) and **Database** ("Export Current Database" and "Download Import Template", Super Admin and company Admin; the existing downloads, unchanged). A role without a section never sees it, and a company always opens on its Board; "Switch company" is unchanged. The board scroll area now spans the whole window instead of the page's 80rem content column (which clipped columns at both edges while the window had room), so the first and last columns are reachable at any width.
- **Central UI browser tests.** `cd frontend && npm run test:e2e:central` runs a Playwright spec against a real central-mode backend with freshly seeded temporary databases (see `docs/DEPLOYMENT.md`); it covers a successful edit, stale edit, move, delete and Undo conflicts and the 428 for versionless writes. The legacy Undo E2E spec's stale selector (Undo entry text is the button's `title`) was fixed.
- **Test evidence (2026-10-22, local commits `638c9c5`..`8c30ac2` plus this documentation; automated and manual results are separate, groups overlap, no combined total):**
  - Backend, full suite (once, after mandatory `expected_version`): 3292 passed, 1 skipped, 0 failed (20 min 39 s). It was **not rerun** for the later Cards import work, which has targeted runs only (new recovery and unmatched-id tests, the existing import and route suites, the schema-upgrade and static-file tests: 152 passed together). No backend code changed for the UI work.
  - Frontend unit 723 passed (50 files); `tsc` clean; lint 0 errors (1 existing warning); build ok.
  - Browser E2E: central 25 passed against disposable databases (there is no central-UI E2E against real data); legacy 97 passed, 2 skipped.
  - Manual, on the developer's Mac against the real central databases (reported by the developer, not automated): the UI changes and the Cards import restore of deleted cards on the real Valor Log board worked. The real databases were backed up and upgraded with the two additive init scripts on 2026-10-22 (record in `docs/DEPLOYMENT.md`). Nothing has been deployed to a server.
- **Pending:** a whole-suite backend rerun on the commit to be deployed, server-side validation (VPS, TLS/proxy, real SMTP) with its own backups and diagnostics, and explicit authorization before any commit, push or deployment.

## 2026-10-21

- **Central mode: database upgrade readiness (Phase 4; development only, no real database opened).** New read-only `backend/diagnose_company_schema.py --company <slug>` reports every missing required or additive element of a company database (versions, tables, columns, unique guards) and the script that creates each missing table; exit 0 complete, 1 incomplete, 2 not runnable. The four existing `init_company_*.py` scripts are unchanged and are documented as independent, idempotent and dry-run-first; there is deliberately no unified upgrade command. New `backend/test_company_upgrade.py` exercises them on temporary legacy-shaped databases. Existing company databases still need the review, loss-reason, import-ledger and product tables created by the operator after a backup (`docs/DEPLOYMENT.md`, "Existing company databases"); not run anywhere. Documentation of the Phase 1-3 roles, card review and mandatory loss reasons added to `docs/CENTRAL_AUTH.md`; two stale comments in `identity_service.py` corrected.

## 2026-10-20

- **Central mode: explicit "Create default board" (Stage 2B.3; development only, no real database touched).** A company with no board shows a "Create default board" button to callers whose capabilities allow board management (today the Super Admin; the company Admin role stays reserved). `POST /api/companies/{slug}/boards/default` returns the company's first board (lowest id) with 200 and `created: false` if one exists, unchanged; otherwise it creates "Sales Pipeline" ("Company sales pipeline") with the six standard stages and no cards and answers 201 with `created: true`. The check, the board and the stages are one transaction under the write lock, so concurrent requests create at most one board and a failure leaves nothing behind; lock contention answers 503 `database_busy`. Nothing creates a board on its own: startup, reads and page loads never do. Boards cannot be deleted through the API, so there is no "default board marker" and no schema change.
- **Central mode: users, invitations and deployment configuration (Part 2A; development only, nothing deployed, no commercial or production database created or opened; disposable development databases were).** The Super Admin can invite people to a company from a new "Users of <company>" panel (email and role Sales Member or Visitor), see members and pending invitations, change a member between those two roles and deactivate a membership. Invitation and password-reset links (`?central_invite_token=`, `?central_reset_token=`) now open screens to activate an account, join another company with an existing account, or choose a new password; the sign-in page has "Forgot my password?". Tokens are removed from the address bar and never stored. The company Admin role stays reserved: the interface never offers it and no invitation can carry it (the Super Admin's role-change API accepts it, but nothing in the interface calls it that way). The interface committed so far is an intermediate central shell; reusing the existing workspace in central mode is the planned target and is not implemented yet.
- **A salesperson who accepted an invitation can be assigned by the Cards import before ever writing in the company:** the import preview and confirmation create the missing company-local reference of each eligible person first (idempotent). Visitors, deactivated members and other companies' users still get "salesperson_unavailable".
- **`docker-compose.central.yml`** (standalone, replaces the two legacy files for a central deployment) and startup validation of the four central databases in production (identity plus Salcomp, Lity, Valor Log: set, existing, distinct, not a development or commercial database); `DATABASE_URL` is not required in central mode. Legacy files and behaviour are unchanged. See docs/DEPLOYMENT.md, "Central mode".
- **Cards workbook import (central mode, development only; nothing deployed, no real database touched).** A Super Admin or a company Admin can import the Cards workbook (the same 15 columns as the export) into a board: `POST /api/companies/{slug}/boards/{id}/excel/import/preview` validates every row and writes nothing; `.../excel/import/confirm` applies the whole workbook in one transaction or none. An empty `card_id` creates a card; a `card_id` of a card of that board updates it (any other id is the same "card not found" row error: a missing id, another board's and another company's cannot be told apart). A repeated `card_id` in the file, an unknown stage, product or salesperson, and any invalid value reject their row, and one rejected row blocks the whole confirmation. For an update, a blank cell clears the field (stage: keeps it) and a column missing from the file leaves its field alone; `deal_year` and `deal_total_usd` are never written. This is separate from the Bid importer.
- **A logical workbook is applied at most once per board (Part 2D-G9).** The server fingerprints the normalized content per company and board (not the filename, row order, `deal_year`, `deal_total_usd` or whether those two columns exist; which other columns exist does count, because an absent column leaves a field alone while a blank one clears it) and records every applied import, create or update, in the `card_excel_imports` table in the same transaction; a unique index backs the check under the write lock. Importing a workbook again answers 409 `already_imported` before any card is touched, even if the board changed since: A, then B, then A again is refused. A workbook with a changed business value is a new workbook. A retry after a lost response, or a concurrent duplicate, changes nothing; "Check result" re-runs the read-only preview and trusts only the ledger record. A confirmation is also bound to its preview (409 `preview_stale`).
- **The ledger is permanent and there is no undo.** Deleting cards does not release a workbook: re-importing the same workbook after its cards were deleted is refused with 409 `already_imported` (the message gives the import number and date); change a business value in the workbook to import similar data. No purge or reset exists; any such operation needs explicit authorization. The importer does not restore earlier values by itself.
- **`card_id` is a required column.** A workbook without it is rejected (`missing_column`) and never read as new cards; blank cells in the column still create cards. The preview now lists, for every updated card, the current and proposed value of each changed field, including changes to empty, to zero and to unassigned. The fingerprint version moved to 2: ledger rows written under version 1 (or with a NULL `repeat_guard`) do not match new fingerprints, so such a row may fail to stop a previously imported workbook from being applied once more. Whether any real database holds such rows has not been established. Before a pilot or production import into an environment that may contain legacy ledger records, an authorized operator must run the read-only check in docs/DEPLOYMENT.md on a backed-up copy. No automatic migration, purge, rewrite or reset exists.
- **Part 2D-G11 (importer only).** The update-only concurrent confirmation of one workbook is now covered by a deterministic test (one 201, one 409 `already_imported`, one ledger row). A ledger table with missing columns or without the unique `(board_id, repeat_guard)` guard now fails closed with 503 `import_ledger_incompatible` (`applied: false`) on preview and confirm; nothing is altered. Version-1 fingerprints do not match version 2: a version-1 row may not prevent reapplication of an earlier workbook, and their existence in real databases is unestablished (see docs/DEPLOYMENT.md for the consequence and the required read-only check).
- **Excel export and template are open to the company Admin of that company (Part 2D-G12)**, besides the Super Admin; same predicate as the import (`can_manage_imports`), checked before the company database opens. Sales, Visitor, anonymous users and Admins of other companies stay refused. Import permissions are unchanged. The Cards screen shows the preview-time message for `import_ledger_incompatible` (nothing imported, an administrator must investigate, no retry).
- **Action needed per existing company database (not run anywhere):** the import answers 503 `import_not_initialized` until the ledger table exists. After a backup, run `init_company_excel_imports.py --company <slug>` (dry run by default, then `--apply`). New databases from `create_company_db.py` already have it. The company schema version does not change.
- **Reader contract change:** a filled `card_id` is no longer a row error (it is the update reference); its syntax (a positive whole number) is still checked.

## 2026-10-19

### C6: probability level, probability filter and Bid Revenue (prepared for a legacy-only first publication; not published)
- **Scope of the first publication: legacy single-database mode only.** The central identity / multi-company code is in the same chain of commits but is not validated and not part of this publication (`/api/mode` answers `{"auth":"legacy"}` in the smoke test below).
- **Probability level on every lead.** Shown in the app as "Probability level" (stored in `cards.confidence_level`): LOW, MEDIUM, HIGH or "Not assigned". It is the sales team's own classification, set by hand in the lead edit form and by the AI assistant, and never inferred from anything. It appears as a coloured badge on the Kanban card, in the card details and in the card history.
- **Reports filter by probability level.** The Reports screen has a "Probability level" filter (All, LOW, MEDIUM, HIGH, Not assigned; the API value for the last is `UNASSIGNED`) and a "By probability level" table.
- **Bid Revenue is reported next to Deal Value, never mixed with it.** The Bid report's annual revenue (BRL, integer cents) appears beside the USD Deal Value in the reports and KPIs; it is not converted, copied into Deal Value or summed with it, and a revenue that was not informed is left out of the totals instead of counting as 0 (`docs/KPIS.md`). **Withdrawn later the same day (committed locally in `086a4a5`, not pushed):** reports, KPIs and the dashboard no longer carry Bid Revenue; Deal Value (USD) is the only financial measure. The Bid revenue columns and the Bid importer are unchanged.
- **The old Bid confidence columns are dropped.** `card_bid_details.confidence` and `card_source_refs.source_confidence` leave the schema and their values are discarded on purpose; nothing is converted into the new probability level.
- **Action needed on an existing legacy database (not done on any commercial database):** the app does not migrate by itself. Per `docs/MIGRATION_PROTOCOL_BID_CONFIDENCE.md`: stop the app, take a verified backup and an external copy, run `migrate_legacy_bid_confidence.py --database <abs path>` as a dry run, then `--apply --expect-sha256 <hash of the approved file>`. The tool writes an exclusive backup, verifies it against the approved content before changing anything, and rolls back on any failure. Check afterwards: obsolete columns gone, `confidence_level` empty on every card, integrity and foreign keys ok, row counts unchanged. To go back, restore the verified backup with the app stopped (`docs/DEPLOYMENT.md`).
- **Validation recorded for commit `175a549` (reported by the owner; not re-run while writing this entry):**
  - Backend: 2527 passed, 1 skipped, 0 failed (Python 3.11.15, `python -m pytest -p no:cacheprovider -q` in `backend/`, 670.82 s, 15 warnings, disposable virtual environment, `DATABASE_URL` and `SECRET_KEY` unset). The skipped test was not identified. Two earlier full runs on Python 3.12 gave 2524 passed, 3 failed, 1 skipped, the three failures caused by the project not being installed in editable mode (needed by the `python -I` subprocess tests); after fixing the environment the three files were re-run alone (52 passed). There is no clean full run on Python 3.12.
  - Frontend: `tsc --noEmit` and `npm run lint` pass; 137 unit tests pass in 19 files; `npm run build` passes and produced `frontend/out`; `test_static_files.py` 10 passed.
  - E2E: the five relevant specs (card-details, leads, dashboard, navigation, annual-deal-value) 21/21. Full suite: 94 passed, 2 skipped (the first-run setup tests in `auth.spec.ts`), 1 failed.
  - **Known failure, pre-existing:** `frontend/tests/bid-import.undo.spec.ts:120` looks for the text "Remove new card ..." with `getByText`, but that description exists only in the `title` attribute of the Undo button. It also fails on the base commit `69e3419` without the C6 commits. Not fixed here (neither the UI nor the test was changed).
  - Docker, 2026-10-19: `docker build -t pm-c6-local:175a549 .` exit 0 (image id `832a396442f8`, about 307 MB, Linux ARM64, Python 3.11.17; some layers came from cache). A disposable container answered `/api/health` 200, `/api/mode` `{"auth":"legacy"}`, `/` 200 and `/api/boards` without credentials 401 (expected); startup and static files ok. The container was removed, the local image kept.
  - Not validated: real SMTP and the real reset email, TLS and the production reverse proxy, any real deployment, and central mode. No migration was run on any commercial database. This is not a statement that the publication is done.

## 2026-10-17

- **Company membership management (Feature Phase 2D, development only, backend).** The Super Admin can list a company's members and pending invitations, deactivate memberships and change roles between Sales Member, Visitor and Admin; a company Admin can list its own company and deactivate its Sales Members and Visitors. No screen yet. An Admin membership still grants no extra privilege (no administrator operations): company Admin privileges stay disabled.

- **Identity schema v2 (Feature Phase 2A, development only).** Memberships and invitations can store the reserved `admin` role; nobody can be assigned it and it grants nothing. New identity databases are created at v2 and a stricter validator checks the role constraints, indexes and foreign keys on open. Existing v1 identity databases are refused until upgraded with the explicit `migrate_identity_v2.py` (dry run by default; rehearsed only on synthetic databases so far).

- **Legacy database: Bid confidence columns removal (development only).** `migrate_legacy_bid_confidence.py` upgrades a legacy single-database file to the probability-level schema (adds the empty `cards.confidence_level`, drops the old Bid confidence columns, whose values are discarded on purpose). Dry run by default; `--apply` needs the approved file's SHA-256, writes an exclusive backup that is verified against the approved content before anything changes (a failed one is kept as `*.UNVERIFIED.db.bak`), and rolls back on any failure. Never applied to a real database; see `docs/MIGRATION_PROTOCOL_BID_CONFIDENCE.md`.

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
