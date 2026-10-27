# Testing and validation

Every implementation batch passes three separate levels of validation. Passing a level does not imply the next one. The permanent rule is in `CLAUDE.md` ("Real-application validation"); the place of this loop in the project is in `docs/PLAN.md` ("Delivery loop"). Safety, authorization and database rules (`docs/SECURITY.md`, `docs/DEPLOYMENT.md`, `docs/DATABASE.md`) are unchanged by this document.

## Level A - Automated validation

Mocks, simulated services and disposable databases with synthetic data are appropriate here, including for writes and destructive cases.

- Backend: `cd backend && uv sync --locked --extra dev` once (pytest, pytest-asyncio and httpx are in the `dev` extra; a plain `uv sync` does not install them), then `uv run pytest <targeted files>`; the full suite once at the end of a body of work.
- Frontend: `npm run test:unit`, `npx tsc --noEmit`, `npm run lint`, `npm run build`.
- End to end: `npm run test:e2e` (legacy) and `npm run test:e2e:central` (central mode). Both start their own backend and frontend on temporary databases and never touch real data.

Report exactly what ran and its result. Level A says nothing about the running application or about acceptance.

Production data: the production databases were created fresh with the repository's creation scripts (`docs/PRODUCTION.md`). No disposable test database, manual-validation database, backup or `.env` of any local environment is ever copied into production, and testing never needs production data.

## Level B - Running-application verification

The developer does this; the user is not asked to restart services or do technical setup.

1. Inspect first: running processes, ports, configuration and the database each process uses (`ps`, `lsof -iTCP -sTCP:LISTEN`). Do not disrupt unrelated services or user work.
2. Restart only the processes needed to load the new code. The backend (`uvicorn` without `--reload`) always needs a restart after backend changes; `next dev` reloads frontend changes itself. A static export needs a rebuild.
3. Never start an incompatible application version against an original database. The code must match the schema of the database it is pointed at; stop and report rather than migrate or modify an original.
4. Confirm health: `/api/health` returns 200, the frontend answers, and the startup log has no relevant errors.
5. Confirm the databases: the real backend uses the local central databases of the real runtime (never production data, never a copy standing in for it). Record which, without exposing secrets or business data.
6. Check each integration the batch depends on (SMTP, AI provider, ...) and note whether it is configured. If it is not, say so; do not substitute a simulation and call it end-to-end.

### The manual-validation environment: the real local runtime (permanent policy, 2026-10-24)

Manual validation uses the established real local runtime, not a copied-database stack, simulated server or mocked frontend:

| | |
|---|---|
| Runtime directory | `~/Library/Application Support/SalcompSalesPipeline` (`central-run/` scripts, `central/` databases, `central-backups/`) |
| Backend / frontend | `8010` / `3010` |
| Manual test URL | `http://127.0.0.1:3010` |
| Databases | `central/identity.db`, `salcomp.db`, `lity.db`, `valor-log.db`: fictional test data kept for local validation only; the production databases were created fresh and no contents of these files were copied into production (`docs/PRODUCTION.md`) |
| Runtime `APP_BASE_URL` | `http://127.0.0.1:3010` (set by the runtime script) |

The development workflow is separate and unchanged: frontend `http://localhost:3000`, backend `8000`, `APP_BASE_URL=http://localhost:3000` in `.env`. Never edit that `.env` value to configure the runtime.

Rules: verify what the process serves (code version, health, databases) and restart the real backend when needed and safe; a `next dev` frontend reloads itself. The database must match the code: diagnose with `diagnose_company_schema.py` first; back up before any migration and apply it only through the documented procedure (`docs/DEPLOYMENT.md`) after inspecting the exact change. During testing and manual validation, never touch production: it is never a test target, and no test, rehearsal or migration for local work runs against it. Do not stop another active stack unless it is needed and verified safe. This rule does not replace or relax the production policy: changes to production need the owner's explicit authorization for that specific change and follow `docs/PRODUCTION.md` and the applicable deployment procedure (`CLAUDE.md`, `docs/VPS_DEPLOYMENT_PLAN.md`, `docs/DEPLOYMENT.md`). If the real application cannot start, report the precise blocker; the manual-testing requirement is not met. Automated tests keep their disposable databases and do not replace manual acceptance. The 3011/8011 copy stack of earlier batches (see the record below) is no longer an acceptance environment.

Startup scripts and launchers: the scripts in `central-run/` unset `SMTP_*` and `OPENROUTER_API_KEY` on purpose. To give the real backend those settings, use a temporary launcher outside the application directory (derived from `central-run/start-backend.sh`); do not edit the established scripts or `.env`. Details and the recipe: `docs/DEPLOYMENT.md`, "Real local runtime".

### Real email testing: two levels

**Level A (automated).** Mocked providers or test doubles cover: successful submission, SMTP connection failure, authentication failure, missing configuration, provider rejection, duplicate invitations and retries, and the resulting error handling and UI feedback. They never send mail and never need real credentials.

**Level B (real application and SMTP).** Whenever a change affects invitations, password resets, email templates, links, delivery status or SMTP configuration, also prepare the real runtime with the configured SMTP provider before asking the user to test:

1. Inspect the runtime's mail configuration without exposing secrets (names and set/empty only).
2. Verify the `SMTP_*` settings are loaded into the running backend process (`ps eww -p <pid>`, names only; never values).
3. Verify `APP_BASE_URL` is `http://127.0.0.1:3010` in that process, so invitation, reset and logo URLs point to the real local application.
4. Verify `SMTP_FROM` is the authenticated account or a verified sender alias of the provider.
5. Restart the real backend if the configuration changed; verify health and read the logs for SMTP connection or authentication errors.
6. Ask the user to specify or confirm the recipient before any real send. Never send to an address the user has not authorized for that test.

Report real email results in separate levels and claim no more than the evidence shows: (1) the invitation or reset request was persisted; (2) the backend attempted SMTP submission; (3) the SMTP server accepted the message; (4) provider delivery status, if available; (5) receipt in the intended mailbox, only if the user confirmed it. Accepting the message at SMTP is not delivery, and a saved invitation is not a sent email. If SMTP is not configured or cannot authenticate, say that real delivery is blocked. Never print SMTP passwords, API keys, invitation tokens or reset tokens in logs, documents or reports.

### Final checklist before declaring a change ready for manual testing

Report each item as done, failed or not applicable; do not claim checks that were not performed.

- Relevant automated tests passed.
- Frontend checks and build passed (when frontend code changed).
- The real application is running the intended current code.
- Backend and frontend health checks passed.
- The correct local databases are in use.
- Real SMTP configuration is loaded (email changes).
- `APP_BASE_URL` is `http://127.0.0.1:3010` in the real runtime (email changes).
- The SMTP sender configuration is valid (email changes).
- The user confirmed the test recipient before any real send (email changes).
- The exact test URL and short manual steps were given.
- Limitations are disclosed.

## Level C - User acceptance

Hand over, for each batch:

- The exact URL (`http://127.0.0.1:3010`, the real runtime).
- The features ready to test, with short practical steps (where to click, what to expect).
- Known limitations and unavailable integrations, stated plainly.
- A clear split between what automated tests verified and what was exercised in the running application.

The batch stays open until the user confirms acceptance. On a reported defect: fix it, rerun the relevant tests, update the running application, and offer another validation cycle. Do not mark a phase accepted without that confirmation, and do not claim manual validation that was not actually carried out in the running application.

## Production

Production preparation and release (backups, migrations on real databases, deployment) come only after acceptance and need their own explicit authorization (`docs/DEPLOYMENT.md`). Testing never requires production databases.

## Record of accepted batches

- 2026-10-23, correction batch 1 (historical; acceptance environments are now the real runtime, see above): level A automated results, level B checks on the manual-validation environment (3011/8011 on copies of the databases) and level C acceptance by the user are recorded in `docs/PLAN.md` ("Correction batch 1: acceptance"). Acceptance was given in that environment, not in production.
