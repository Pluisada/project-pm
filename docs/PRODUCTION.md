# Production environment: Sales Tracker (Sales Pipeline), central mode

State as documented on 2026-10-26, the day of the first production deployment, updated the same day for the upgrade to `e1a79ae` (section 3A). This is the operator's reference for what runs in production, how it is operated, which risks were accepted and what is still open. The procedures it builds on are in `docs/VPS_DEPLOYMENT_PLAN.md` (plan, rehearsal and production runbook) and `docs/DEPLOYMENT.md` (repository deployment notes).

**Evidence levels used below.**
- **Observed**: seen by the operator in command output during the deployment (2026-10-25 and 2026-10-26). The deployment records (`records/rehearsal-20261026.md`, `records/production-20261026.md` on the VPS) hold the same facts. **None of this has been re-verified when this document was written.**
- **Owner-reported**: stated by the owner in chat; the operator could not observe it (for example, that an email arrived in a mailbox).
- **Provider claim**: taken from a public vendor page; not checked for this VPS.
- **Not verified**: stated plainly as such.

No credentials, secret values, tokens, activation links or configuration file contents are recorded here, only variable names and paths.

## 1. Summary

| Item | State |
|---|---|
| Production URL | `https://pipeline.pluisada.com.br` (observed: HTTPS 200, valid Let's Encrypt certificate issued 2026-10-26, expires 2027-01-24) |
| Release | Commit `762c01f8f7802415b2f5313bce7939b074729360` on `origin/main` (pull request #9 merged; deployed 2026-10-26 20:10 UTC, section 3B). Earlier releases kept as rollback images on the VPS: `e1a79ae4e524649dc66aaa3cc229d3a122e3b092` (section 3A) and the first production release `cb216f722d33820d84537885ac18e2d93e70030b` (pull request #3) |
| Mode | Central (`PM_AUTH_MODE=central`), `PM_ENV=production`; four SQLite databases: identity, Salcomp, Lity, Valor Log |
| Users | At the first deployment: one active global Super Admin, `pluisada@gmail.com` (observed: identity database counts users 1, active global admins 1, with password 1, email verified 1, memberships 0). Since then the owner has invited users (owner-reported: at least one company Admin, on Valor Log, who accepted); the owner also imported one Cards workbook per company (owner-reported); the user counts and the card counts were **not re-read** after the upgrades |
| Container | `pm-central-app-1` (Compose project `pm-central`), **no published host port** (observed: `docker port` empty, nothing listening on host port 8000, port 8000 times out from the internet) |
| Edge | Existing Nginx Proxy Manager (NPM 2.15.1), proxy host `pipeline.pluisada.com.br` to `http://sales-pipeline:8000` over the Docker network `nginx-proxy_default` |
| AI assistant | **Enabled** on 2026-10-26 18:32 UTC by the owner's decision (a dedicated `OPENROUTER_API_KEY` typed by the owner on the VPS into `pm-central.env`; observed: set in the container, 73 characters, value never shown; the owner reported that the assistant answered). Data of the board is sent to OpenRouter (model `openai/gpt-oss-120b`); cost and the spending limit are managed in the provider account (the owner reported on 2026-10-27 that the limit was checked; R7) |
| Backups | Daily local backup at 03:30 Brasilia time (06:30 UTC) with a manifest, kept 7 days and pruned automatically (section 6). **Local to the VPS only** |
| Monitoring | Five-minute e-mail monitor on the VPS (HTTPS, container health, disk, backup age, certificate) |
| Disaster recovery | **Not disaster-recovery ready**: application backups exist only on the same VPS (section 7) |
| Rehearsal | The disposable rehearsal environment was run and removed (section 4) |

## 2. Architecture

```
Internet --443--> NPM (nginx-proxy-npm-1; Let's Encrypt; Force SSL; HTTP/2)
                   | proxy host pipeline.pluisada.com.br -> http://sales-pipeline:8000
                   v  network nginx-proxy_default (nothing published on the host)
        container pm-central-app-1   (uid 10001, no capabilities, no-new-privileges, healthcheck)
                   | bind mount rw  /docker/sales-pipeline/data -> /data
                   v
        identity.db   salcomp.db   lity.db   valor-log.db
```

- One process (FastAPI, one uvicorn worker) serves `/api/*` and the static frontend on one origin. No CORS is configured (same origin).
- The VPS (Hostinger KVM 2, Ubuntu 24.04.4, x86_64, 2 vCPU, 7.8 GB RAM, **no swap**, Docker 29.6.2, Compose v5.3.1; observed 2026-10-25) is shared with n8n, Evolution, Portainer, Redis, Postgres, OpenClaw, a static site and NPM. This deployment added one container, one network entry and one NPM proxy host and changed nothing else (observed: inventory comparison before and after the rehearsal; production added `pm-central-app-1`).
- The image was **built on the owner's Mac** for `linux/amd64` and loaded on the VPS with `docker save | docker load`, because the VPS has little free memory and no swap. Layer digests on the VPS equal the Mac image (observed). Base images at build time: `python:3.11-slim` `sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377`, `node:24-alpine` `sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53`.

## 3. Release and its evidence

- Image tag `sales-pipeline:cb216f722d33820d84537885ac18e2d93e70030b`, image ID `sha256:0f5d250fd14913c622bfc1b7b2d5b29e369b14ac404525fa65ce5344551ed23f`; Python 3.11.17, FastAPI 0.162.0, Starlette 1.10.0, PyJWT 2.15.1; runs as uid 10001; no `.env` or database file inside the image (observed).
- **Dependency remediation** (pull request #3): the audit of the original release `fdfe3b5` found 20 distinct advisories in 6 Python runtime packages (3 critical, 7 high, some without a fix: `python-jose`, `ecdsa`) and 16 findings in JavaScript development tooling (3 critical, 9 high). The commit replaces `python-jose` with PyJWT (used only in `backend/auth.py`), upgrades FastAPI/Starlette/Pydantic/`python-multipart`/`python-dotenv`/`certifi`, and upgrades the frontend test tooling (`vitest` 5, `@types/node` 24).
- **Results on the exact commit**, in a clean clone (Python 3.11.15, Node 24.19.0, macOS arm64): backend full suite 3475 passed, 1 skipped (not individually identified), 0 failed; central end-to-end 39 passed; frontend unit tests 799 passed; lint 0 errors and 1 warning; type check clean; production build ok; `pip-audit` 2.10.1 on the runtime lock (34 of 34 packages, platform markers removed): no known vulnerabilities; `npm audit` (production and all dependencies, 598 packages): 0 findings.
- **Limits of that evidence:** advisory databases only, as of the audit day; operating-system packages of the base images are not scanned; the test run was on macOS, not on the Linux image; the production application had never run on FastAPI 0.162 / Starlette 1.10 before the rehearsal.

## 3A. Upgrade to `e1a79ae` (2026-10-26)

Code-only release: no change to schema, models, migrations, Dockerfile, Compose files or dependency lockfiles since `cb216f7` (verified with `git diff --name-only cb216f7 e1a79ae`). The merge commit `e1a79ae` (pull request #7, parents `7a2a39c` and `7e6c014`) has the same tree (`2bfa1d02bf39e5c89c1f65b19a3535bc5fd1f78d`) as the commit that was tested.

- **What it adds:** branded HTML invitation and password-reset emails (company logo embedded, neutral layout for a reset); the invitation endpoint reports `email_sent` and a pending or deactivated member can be invited again; the Lead Generation Pareto on the KPIs screen (leads created this calendar year ranked by creator); a company Admin may assign a card to themself and to no other Admin (owner decision, 2026-10-26); clearer AI provider errors; the label "Confidence level"; `test_no_automatic_email.py`.
- **Evidence on the exact commit** (clean clone, Node 24.19.0, Python 3.12.13 on macOS arm64, `uv sync --locked --extra dev`): frontend `tsc` clean, lint 0 errors and 1 warning, 815 unit tests passed (vitest 5.0.5), production build ok; central end-to-end 39 passed; backend focused run 202 passed; the backend full suite (3533 passed, 1 skipped, 0 failed) was run on the identical tree earlier the same day and reused. `npm audit` (production and all dependencies): 0 vulnerabilities; `pip-audit` 2.10.1 on the runtime lock: 34 of 34 packages, no known vulnerabilities, none skipped. Evidence files: the owner's `~/pm-archive-20261026/release-e1a79ae-evidence/` (not in the repository).
- **Image:** `sales-pipeline:e1a79ae4e524649dc66aaa3cc229d3a122e3b092`, ID `sha256:51ea72b1df1d154706e1b569c92ff547c1699e27ea7b2332566d2eecb21b5fc6`, `linux/amd64`, Python 3.11.17, FastAPI 0.162.0, Starlette 1.10.0, PyJWT 2.15.1, uid 10001, no `.env` or database, three company logos in `static/brand` (observed). Built once on the Mac; loaded on the VPS with `docker save | docker load`; image ID and layer digests equal the Mac's (observed).
- **Protection:** the existing backup script was run before the change: set `20261026T180406Z`; `sha256sum -c` passed for the four databases and the manifest; each file `integrity_check` ok, 0 foreign-key violations; the manifest records release `cb216f7` and its image ID (observed). The previous image stayed on the VPS.
- **Deployment:** the VPS source tree was moved to `e1a79ae` (clean); only the `image:` line of `docker-compose.vps.yml` changed (the previous file was kept as `docker-compose.vps.yml.pre-e1a79ae` until the owner deleted it on 2026-10-27 (owner-reported)); only `pm-central-app-1` was recreated at 18:05:15 UTC and was healthy after about 9 seconds (observed). NPM, DNS, SMTP settings, OpenClaw, n8n and the databases were not touched.
- **Checks (observed):** public `/api/health` 200 and `/api/mode` central; no published port; `/docs`, `/redoc` and `/openapi.json` 404; one HSTS header; CSP, nosniff, frame and referrer headers; no CORS header for a foreign origin; `diagnose_company_schema.py` for the three companies "Schema complete" (version 4) and `diagnose_identity_limits.py` "Within limits"; the new KPI route answers 401 without a token; the container's `APP_BASE_URL` is `https://pipeline.pluisada.com.br`; one password-reset email, sent only to the Super Admin's own address, accepted by the SMTP server (`Email sent`, no error). **Owner-reported:** Super Admin sign-in, the Lead Generation Pareto section on the KPIs screen, and receipt of the reset email (neutral layout, link host the production URL; the link was not used).
- **AI enablement** at 18:32 UTC (separate step, same day): see the summary table and R7. A copy of the previous `pm-central.env` was kept in root's home (mode 600); the owner reported on 2026-10-27 that it was deleted (owner-reported).
- **Not done in production:** no invitation was sent by the operator, no test account or record was created; the multi-user tests of section 8 remain deferred. A backup run after the upgrade, and the first cron run on the new release, were not yet observed. **Known limits of the Admin self-assignment rule (accepted by the owner):** it applies to card creation, card edit and the lead-options list; AI chat, Excel valid-values and Undo still use the unchanged central eligibility, and Undo back to the Admin's own id is untested. The owner first chose to keep the original rule that an Admin is not an assignable salesperson in the Cards workbook import (a Valor Log Admin's address was refused on import, as designed), then reversed that decision the same day. **Superseded by section 3B (`762c01f`): Admins are eligible through the single rule, including the import, the AI chat and Undo, and these known limits no longer apply.**

## 3B. Upgrade to `762c01f` (2026-10-26)

Second upgrade of the day, code-only again: no change to schema, models, migrations, Dockerfile, Compose files or dependency lockfiles relative to `e1a79ae` (verified with `git diff`). The merge commit `762c01f` (pull request #9, parents `ababbe0` and `8636304`) has exactly the tree (`e9ed1210948be4ef9a4de0b91ff54ecc4319f6d2`) that was tested.

- **What it changes (owner decisions, 2026-10-26):** a company Admin is an eligible salesperson: one rule, `identity_service.assignable_user_ids`, now includes active company Admins, and it governs card create/edit, the lead options, the Cards workbook template ("Valid values") and import, the AI chat and Undo (Visitors and deactivated members stay refused; the interim self-assignment shim of `e1a79ae` is removed, which also removes R8). In the Lead Generation Pareto a card created by a workbook import is credited to the salesperson of the row, not to the importer (the importer stays in the event details and as the actor of the `assign` event); a row without a salesperson has no recorded creator and stays out of the ranking. Cards already in production keep the credit they have (no data migration).
- **Evidence on the exact tree** (clean isolated clone of `8636304`, whose tree equals `762c01f`; Node 24.19.0, Python 3.12.13 on macOS arm64): backend full suite 3536 passed, 1 skipped, 0 failed; frontend `tsc` clean, lint 0 errors and 1 warning, 815 unit tests, build ok; central end-to-end 39 passed; `npm audit` (production and all dependencies) 0 vulnerabilities; `pip-audit` 2.10.1 on the runtime lock 34 of 34 packages, no known vulnerabilities, none skipped. The suite and the audits were **not repeated** on `762c01f` because the tree and the lockfiles are identical. Evidence files are in the owner's `~/pm-archive-20261026/release-8636304-evidence/` and `release-762c01f-evidence/` (outside the repository).
- **Image:** `sales-pipeline:762c01f8f7802415b2f5313bce7939b074729360`, ID `sha256:f2ee39d1caae7293a5c25e6137060117a136408a6a5f382a79a5ea4dc93a48db`, `linux/amd64`, Python 3.11.17, FastAPI 0.162.0, Starlette 1.10.0, PyJWT 2.15.1, uid 10001, no `.env` or database, the three company logos embedded by the invitation emails; the new Admin rule and the imported-card credit are present (observed inside the image). Built once from a clean clone of the exact SHA, loaded on the VPS with `docker save | docker load`; image ID and layer digests equal the Mac's (observed).
- **Protection:** the existing backup script was run before the change: set `20261026T200936Z`; `sha256sum -c` passed for the four databases and the manifest; each file `integrity_check` ok and 0 foreign-key violations; the manifest records release `e1a79ae` and its image ID (observed). The images of `e1a79ae` and `cb216f7` stayed on the VPS.
- **Deployment:** the VPS source tree was moved to `762c01f` (clean) after the image was loaded; only the `image:` line of `docker-compose.vps.yml` changed (the previous file was kept as `docker-compose.vps.yml.pre-762c01f` until the owner deleted it on 2026-10-27 (owner-reported)); only `pm-central-app-1` was recreated at 20:10:14 UTC and was healthy after about 9 seconds (observed). NPM, DNS, SMTP settings, the AI key, OpenClaw, n8n and the databases were not touched.
- **Checks (observed):** public `/api/health` 200 and `/api/mode` central; no published port; `/docs`, `/redoc` and `/openapi.json` 404; one HSTS header and the CSP, nosniff, frame and referrer headers; no CORS header for a foreign origin; `diagnose_company_schema.py` for the three companies "Schema complete" and `diagnose_identity_limits.py` "Within limits"; the KPI route answers 401 without a token; the AI key is still set in the container; no error in the startup logs; the monitor raised no alert. No email was sent in this upgrade (the email code did not change since `e1a79ae`).
- **Owner validation:** the owner chose to validate the Cards workbook import with a company Admin directly in production; the result is recorded by the owner (not observed by the operator).
- **Not done:** no invitation, no test account or record, no restore; the multi-user tests of section 8 remain deferred.

## 4. How it was deployed

| Phase | What was done | Evidence |
|---|---|---|
| A. Preflight | Remote SHA verified; clean clone; read-only VPS inventory (OS, resources, Docker, Compose, networks, ports, NPM address); firewall (owner screenshot: only 22, 80, 443 accepted, then Drop); tests and audits above | Observed |
| Image | Built on the Mac, loaded on the VPS, identity verified | Observed |
| D. Disposable rehearsal | Separate project `pm-rehearsal`, hostname `pipeline-rehearsal.pluisada.com.br`, same image ID, disposable databases, no published port. Verified: configuration assertions (including no published ports), fresh database creation and diagnostics inside the container, HTTPS and headers, rate-limit client address logic, token-logging tests, administrator activation by email, application workflows (owner, in the browser), restart and down/up with unchanged data, backup with manifest, isolated restore test, full restore drill, failure drills | Observed, except the browser workflows and email receipt (owner-reported) |
| E. Production | Verification of SHA and image ID; copy of the NPM database; production configuration with a fresh `SECRET_KEY` generated on the VPS; four fresh databases created in the image; single Super Admin bootstrapped; baseline backup before the application started; start; NPM proxy host; HTTPS and header checks; synthetic-marker token test; SMTP test email; activation email; smoke test (restart, persistence, second backup) | Observed, except email receipt and the owner's login (owner-reported) |
| F. Operations | Daily backup cron and five-minute monitor installed; test alert email sent | Installed and ran (observed); receipt of the alert email owner-reported |
| Cleanup | Rehearsal container, network and directory removed after the owner confirmed; the rehearsal record was kept | Observed |

Not copied from development: no database, backup, `.env` or token. The production databases were created empty in the release image. The SMTP application password was typed by the owner on the VPS and never shown to the operator.

## 5. Configuration facts (names and paths only)

**On the VPS (`/docker/sales-pipeline/`)**

| Path | Purpose | Owner, mode |
|---|---|---|
| `src/` | Fresh clone, detached at `cb216f7`, clean | root |
| `pm-central.env` | Environment file (secrets inside; never committed or copied) | root, 600 |
| `docker-compose.vps.yml` | Override on top of `src/docker-compose.central.yml`: image tag, `pull_policy: never`, `ports: !reset []`, the `nginx-proxy_default` network with alias `sales-pipeline`, log rotation 5 x 10 MB | root, 640 |
| `data/` | The four databases | 10001:10001, 700 |
| `backups/` | Backup sets `<UTC timestamp>/` (four databases, `MANIFEST.json`, `SHA256SUMS`), `LAST_SUCCESS` | 10001:10001, 700 |
| `restore-test/` | Scratch area for restore tests | 10001:10001, 700 |
| `records/` | Non-secret deployment records | root, 750 |
| `sales-pipeline-backup.sh` | Backup script (root, 700) | |
| `/usr/local/sbin/sales-pipeline-monitor.sh` | Monitor script | root, 700 |
| `/etc/cron.d/sales-pipeline-backup`, `/etc/cron.d/sales-pipeline-monitor` | Schedules | root |
| `/var/log/sales-pipeline-backup.log`, `/var/log/sales-pipeline-monitor.log`, `/var/lib/sales-pipeline-monitor/` | Logs and monitor state | root |

**Environment variable names in `pm-central.env`**: `PM_CENTRAL_DATA_HOST_PATH`, `PM_CENTRAL_DATA_CONTAINER_PATH`, `PM_IDENTITY_DATABASE_URL`, `PM_COMPANY_DB_SALCOMP`, `PM_COMPANY_DB_LITY`, `PM_COMPANY_DB_VALOR_LOG`, `PM_PUBLIC_URL`, `PM_BEHIND_PROXY`, `RATE_LIMIT_TRUSTED_PROXIES` (the NPM address `172.16.1.2/32`, to be re-read if NPM is ever recreated), `HSTS_ENABLED`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USER`, `SMTP_PASSWORD`, `SECRET_KEY`. `OPENROUTER_API_KEY` was absent at the first deployment and was added on 2026-10-26 (section 1). `DATABASE_URL`, `PM_ENV`, `PM_E2E`, `PM_SETUP_TOKEN`, `PM_ALLOW_CREATE_DB` and `PM_REQUIRE_EXISTING_DB` must not be set (the Compose file sets what it needs).

**Email**: Gmail SMTP on port 587 with STARTTLS (certificate and host verification are enforced by the application); the sender is the Super Admin's own Gmail account, authenticated with an application password. Invitation and reset emails are HTML with a plain-text alternative since `e1a79ae` (plain text in `cb216f7`).

**NPM proxy host** (verified in NPM's database): domain `pipeline.pluisada.com.br`, scheme `http`, forward host `sales-pipeline`, port 8000, certificate Let's Encrypt, Force SSL on, HTTP/2 on, Websockets off, HSTS off in NPM (the application sends one `Strict-Transport-Security` header), Block Common Exploits on, no access list, Advanced block exactly `access_log off;` and `client_max_body_size 11m;`.

**Hardening** (observed): container user uid 10001, no Linux capabilities, `no-new-privileges`, no published port, no `/docs`, `/redoc` or `/openapi.json` (HTTP 404), no CORS header for a foreign origin, exactly one HSTS header, CSP and the other security headers present.

## 6. Operations

**Backups.** `sales-pipeline-backup.sh` runs from cron daily at 06:30 UTC (03:30 Brasilia). Each run takes a consistent SQLite online backup of the four databases with the repository helper, verifies integrity, foreign keys and logical equality, and writes a manifest: UTC timestamp, release SHA, image ID, and for each file its size, SHA-256, integrity result, foreign-key violations, `user_version` and the row count of every table (counts only). `LAST_SUCCESS` is written only after the set verifies; a failed set is renamed `<timestamp>.FAILED`. Retention: the backup script itself only **lists** candidates and deletes nothing; since 2026-10-27 a separate script, `/usr/local/sbin/sales-pipeline-backup-prune.py` (cron `/etc/cron.d/sales-pipeline-backup-prune`, daily 06:50 UTC), deletes sets older than 7 days (owner's choice) only if they verify against their `SHA256SUMS`, never below the 7 newest verified sets, and never when the last successful backup is older than 26 hours or missing (plan section 8.6; failed `.FAILED` sets, unverifiable sets and any other name are never touched). Restore procedures (verify a set against its manifest, restore into an isolated scratch directory, full production restore) are in `docs/VPS_DEPLOYMENT_PLAN.md` section 8. They were proven on the rehearsal (observed). **No restore has been performed on production data**, and the monthly restore test required by the plan is not yet scheduled.

**Monitoring.** `sales-pipeline-monitor.sh` runs every five minutes and emails the Super Admin through the application's own SMTP when it finds: the public `/api/health` check failing; the container not healthy; `/docker` disk at or above 80%; the last successful backup older than 26 hours (or none recorded); a `.FAILED` backup set; the certificate expiring in under 14 days or unreadable. It emails once per distinct problem set, repeats after six hours while the problem persists, and sends a recovery message. It also supports `--test-email`. A test alert and a normal run were observed; receipt of the test email is owner-reported. **It runs on the same VPS, so it cannot alert if the whole VPS or its network is down.** NPM renews the Let's Encrypt certificate automatically (provider behaviour; renewal not yet observed).

**Routine tasks.** Upgrade (new verified commit, tests and audits, backup before upgrade, rehearse, then deploy): plan section 7, Phase F3. Adding company Admins and users (Super Admin only; at most two Admins per company counting pending invitations): Phase F6. Sending a new activation link to the Super Admin: `identity_admin_cli.py send-activation --email <address>` inside the container. Command pattern: `docker compose --env-file /docker/sales-pipeline/pm-central.env -p pm-central -f /docker/sales-pipeline/src/docker-compose.central.yml -f /docker/sales-pipeline/docker-compose.vps.yml <command>`. Never use `down -v`, any `prune` command, or `docker compose run` without `</dev/null` inside scripts read from standard input (see the plan's lessons, section "Execution status").

## 7. Accepted risks and known gaps

| # | Item | Status |
|---|---|---|
| R1 | **NPM error log can contain a token.** NPM's default access log records the full URI with the query string (confirmed on 2.15.1 in the rehearsal with a synthetic marker); `access_log off;` in the proxy host removes it from the access log (verified clean for NPM log files, NPM container log and application log). When the upstream is unreachable, NPM's **error log** records the request line including the query string (confirmed in the rehearsal; for production the result is inherited from the rehearsal because the NPM container, its image and the Advanced block are the same, and was not provoked on production). A reset or invitation link opened while the application is down could therefore appear in `proxy-host-<id>_error.log`. Mitigations: tokens are single-use, stored hashed and expire (reset 1 hour, invitation 7 days); `/docker/nginx-proxy/data/logs` was restricted to `700 root:root` (observed; a rotation dry run showed no errors; other sites kept answering 200). | **Accepted by the owner** |
| R2 | **No off-host copy of application backups.** Backups, manifests and databases live only on the VPS. Loss, deletion or compromise of the VPS loses the data. The only other copy is the provider's weekly VPS backup, which was **not checked for this VPS** (provider claim: weekly backups and one manual snapshot are included in Hostinger VPS plans) and has no tested restore path for these files. | **Accepted by the owner**. The system is **not** disaster-recovery ready |
| R3 | Old local backups accumulate (about 1.3 MB per set; the disk monitor alerts at 80%). | **Closed 2026-10-27**: automatic pruning installed (section 6, plan 8.6), tested on fake sets before installation. The 7-day window is the owner's choice (the plan had proposed 14 daily sets); because backups are local only (R2), a logical error noticed after a week has no backup to restore from. First real deletions are expected from about 2026-11-03, when more than 7 verified sets exist |
| R4 | **No independent monitoring.** A total VPS or network failure raises no alert. | **Accepted by the owner** |
| R5 | Shared host: no swap and about 3.9 GB of free memory at the time; OpenClaw alone uses about 1.9 GB. The production container is small but memory pressure could affect all services. | Open, not caused by this deployment |
| R6 | The 1 skipped backend test is not individually identified; the frontend lint has 1 warning (an unused parameter in `AuthModeGate.test.tsx`). | Open, cosmetic |
| R7 | **AI assistant enabled.** Card, company and user data in the assistant's context is sent to OpenRouter (model `openai/gpt-oss-120b`); the key is dedicated to this environment and sits in `pm-central.env` (mode 600). The owner reported on 2026-10-27 that the spending limit in the provider account was checked and is OK (owner-reported; the value was not shown to the operator). The application rate-limits the assistant. | Accepted by the owner (enabled by the owner's decision) |
| R8 | Company Admin self-assignment edges (Undo back to the Admin's own id, AI-chat path) were untested in `e1a79ae`. | **Closed by `762c01f`**: Admins are eligible through the same single rule as everyone else, so those paths no longer differ (no dedicated Undo test was added) |

## 8. Deferred tests

These need more than one account, so they were **not** run (the automated suite covers them; they are not proven in production):

1. Maximum two company Admins, including pending Admin invitations (a third is refused; an accepted invitation does not count twice).
2. Refusal of a second global administrator (`identity_admin_cli.py bootstrap` with another address, expected refusal, no data change).
3. Independent CLEAR per required reviewer; one reviewer's CLEAR does not clear another's; voiding of a removed or deactivated reviewer's pending acknowledgment.
4. Permanent user cancellation (the cancelled user cannot sign in).
5. Stale card-version rejection on production data (it was observed in the rehearsal, not in production).

Undo of a reassignment to a company Admin follows the same eligibility rule since `762c01f` (R8); it has no dedicated test.

Plan: run them at the first additional Admin onboarding (plan Phase F6, step 4), with clearly labelled test cards, and record the results in a new production record.

## 9. Open items

- The rehearsal proxy host in NPM: the owner reported on 2026-10-27 that it was deleted (owner-reported; not re-checked by the operator).
- Health bot (`@paulo_saude_bot`, OpenClaw): after the NPM changes the bot did not answer at first; the investigation (read-only) showed Telegram flood control (HTTP 429) on the bot's replies, no request reaching `/webhook/saude/paulo`, and other webhooks through NPM answering 200. The owner later reported that the original messages were answered. **Not independently verified** (no log check after the recovery). Debug notes for OpenClaw were delivered separately to the owner and are not part of this repository.
- (Resolved by pull requests #5 and #6 on 2026-10-26; kept for history.) Documentation that still described the pre-deployment state and needed a follow-up edit by whoever held the local, uncommitted changes to them: `CLAUDE.md` (Status section), `docs/DEPLOYMENT.md` (statements that nothing was run on a server, "none exists yet" for the reverse proxy, the central-mode section), `docs/SECURITY.md` and `docs/TESTING.md`. They were **not edited here** because they carry uncommitted local modifications.
- The Hostinger manual snapshot before the NPM edit was skipped by the owner; a copy of the NPM database (integrity checked) was taken instead and is kept in root's home on the VPS.

## 10. Recommendations (not done)

1. Add an **encrypted off-host copy** of each backup set (for example to a separate bucket or another machine), verify its hash at the destination, and test a restore from it. Until then, do not describe the system as disaster-recovery ready.
2. Local backup pruning: done on 2026-10-27 (R3), with a 7-day window; consider a longer window while there is no off-host copy (R2).
3. Schedule the monthly restore test (plan section 8.4) and record it under `records/`.
4. Add an **independent external check** of the HTTPS health endpoint (outside the VPS) when a free option without extra accounts is acceptable.
5. Review the memory budget (swap or container limits) with the OpenClaw owner before adding load.
6. Complete the deferred multi-user tests at the first Admin onboarding.
7. The AI assistant is enabled (R7): confirm that a spending limit is set in the provider account, and rotate the key if it is ever exposed (edit `pm-central.env`, recreate the container).
8. Update the stale documents listed in section 9 together with the owner's pending local edits, and pin base-image digests in the Dockerfile when convenient.
9. Done on 2026-10-27 (owner-reported): `/root/pm-central.env.before-ai`, `docker-compose.vps.yml.pre-e1a79ae` and `docker-compose.vps.yml.pre-762c01f` were deleted from the VPS; the first scheduled backup on `762c01f` was observed (section 12). The saved override files no longer exist, so a rollback edits the one `image:` line (next point).

## 11. Recovery and redeployment pointers

- **Application or configuration failure, data intact**: stop or recreate only `pm-central-app-1`; never touch `data/`. Check `docker logs`, the production-config message (the application refuses to start and lists every problem) and `docker compose ... config`. Rollback for code is the previous image tag in `docker-compose.vps.yml` (`sales-pipeline:e1a79ae4e524649dc66aaa3cc229d3a122e3b092` and `sales-pipeline:cb216f722d33820d84537885ac18e2d93e70030b` are still on the VPS; the saved override files were deleted on 2026-10-27, so set the `image:` line of `docker-compose.vps.yml` back to the tag of the release to restore, which is the only line the upgrades changed); also return the source tree with `git -C /docker/sales-pipeline/src checkout --detach <that release's SHA>`, because the backup script takes the release SHA and image tag from it. The upgrade changed no schema, so a code rollback needs no database action.
- **Data damaged**: restore from the latest set that verifies against its manifest, using the procedure in plan section 8.5 (stop, move the damaged files aside, restore with the repository helper, verify against the manifest, run the schema and limit diagnostics, start). Data written after the set's timestamp is lost.
- **VPS lost**: the databases and local backups are lost with it (R2). Rebuild from this repository and the plan (Phases A, B and E: image, configuration with a new `SECRET_KEY`, fresh databases, bootstrap, NPM proxy host, activation by email), and recover data only if the provider's weekly backup or a manual copy exists.
- **Super Admin cannot sign in**: send a new activation or reset link; no command sets a password by design.
- **NPM administrator password lost**: the NPM database holds one user; a consistent copy of the database must be taken first, and only the `auth` row of that user is changed (a bcrypt hash computed without exposing the new password). This was done once on 2026-10-26 (observed); the proxy hosts and certificates were unchanged afterwards (observed).

## 12. What was and was not independently verified

| Claim | Basis |
|---|---|
| Release SHA on `origin/main`, tests and audits | Observed commands on the exact commit (section 3) |
| Production configuration, hardening, no published port, HTTPS, headers, proxy-host settings | Observed commands on 2026-10-26 |
| One active Super Admin with a password, verified email, no memberships | Observed database counts (no addresses read) |
| The owner received the test and activation emails, set the password and signed in | Owner-reported |
| Backup sets, manifests and their verification; cron and monitor installed | Observed |
| The monitor's behaviour under a real failure; certificate renewal | **Not verified** at the time of writing |
| First scheduled (cron) backup on `762c01f`: set `20261027T063002Z`, `MANIFEST.json: OK`, `backup set ... ok`, manifest `release_sha` `762c01f8f7802415b2f5313bce7939b074729360` and `image_id` `sha256:f2ee39d1caae7293a5c25e6137060117a136408a6a5f382a79a5ea4dc93a48db` | Observed in the owner's pasted terminal output, 2026-10-27 |
| Pruning script: 12 scenarios on fake backup sets passed on the operator's Mac (Python 3.9; the script and tests are in the owner's `~/pm-archive-20261026/backup-prune/`, outside the repository); on the VPS a dry run printed `only 5 verified sets (minimum 7); nothing deleted` and the cron entry was installed | Observed (test run); the VPS output is from the owner's pasted terminal, 2026-10-27 |
| Upgrade to `e1a79ae`: image identity, backup set, health, headers, diagnostics, reset email accepted by SMTP | Observed on 2026-10-26 (section 3A) |
| Upgrade to `762c01f`: tree equal to the tested tree, image identity, backup set, health, headers, diagnostics | Observed on 2026-10-26 (section 3B) |
| Sign-in, Pareto screen, reset email received, AI answering after enablement | Owner-reported |
| Restore on production data | **Not performed** |
| Provider weekly backups for this VPS | Provider claim, not checked |
| Health bot working again | Owner-reported |
