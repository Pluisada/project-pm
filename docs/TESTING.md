# Testing and validation

Every implementation batch passes three separate levels of validation. Passing a level does not imply the next one. The permanent rule is in `CLAUDE.md` ("Real-application validation"); the place of this loop in the project is in `docs/PLAN.md` ("Delivery loop"). Safety, authorization and database rules (`docs/SECURITY.md`, `docs/DEPLOYMENT.md`, `docs/DATABASE.md`) are unchanged by this document.

## Level A - Automated validation

Mocks, simulated services and disposable databases with synthetic data are appropriate here, including for writes and destructive cases.

- Backend: `cd backend && uv run pytest <targeted files>`; the full suite once at the end of a body of work.
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
5. Confirm isolation: the process uses the manual-validation databases, not production data or the originals. Record which database, without exposing secrets or business data.
6. Check each integration the batch depends on (SMTP, AI provider, ...) and note whether it is configured. If it is not, say so; do not substitute a simulation and call it end-to-end.

### The manual-validation environment

Keep one persistent, documented environment between batches so the user can test repeatedly: the real application code, isolated or staging databases (copies or synthetic data), fixed ports (the project has used frontend 3011 and backend 8011), a log file outside the repository, and a recorded way to restart it. Do not create parallel clones or worktrees for it. It is not the same thing as the disposable databases of level A. If it must be stopped or recreated, document why and how it is restored.

External integrations: when a feature depends on SMTP or similar, configure a safe test integration when authorized and available (a dedicated test account or an existing authorized test configuration) and verify the real delivery when that is part of the acceptance criteria. Do not ask for production secrets unnecessarily, do not invent credentials and do not print secret values. For the local manual-validation backend the authorized test SMTP settings are the `SMTP_*` entries of the repository `.env` (git-ignored); every restart of that backend loads them, and only them, from there (the application URL and the databases stay those of the validation environment, so emailed links point to it). Never state that real email delivery was tested unless SMTP was configured and the delivery was observed.

## Level C - User acceptance

Hand over, for each batch:

- The exact URL (for example `http://127.0.0.1:3011`).
- The features ready to test, with short practical steps (where to click, what to expect).
- Known limitations and unavailable integrations, stated plainly.
- A clear split between what automated tests verified and what was exercised in the running application.

The batch stays open until the user confirms acceptance. On a reported defect: fix it, rerun the relevant tests, update the running application, and offer another validation cycle. Do not mark a phase accepted without that confirmation, and do not claim manual validation that was not actually carried out in the running application.

## Production

Production preparation and release (backups, migrations on real databases, deployment) come only after acceptance and need their own explicit authorization (`docs/DEPLOYMENT.md`). Testing never requires production databases.

## Record of accepted batches

- 2026-10-23, correction batch 1: level A automated results, level B checks on the manual-validation environment (3011/8011 on copies of the databases) and level C acceptance by the user are recorded in `docs/PLAN.md` ("Correction batch 1: acceptance"). Acceptance was given in that environment, not in production.
