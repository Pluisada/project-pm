# Production environment: Sales Tracker (Sales Pipeline), central mode

State as documented on 2026-10-26, the day of the first production deployment. This is the operator's reference for what runs in production, how it is operated, which risks were accepted and what is still open. The procedures it builds on are in `docs/VPS_DEPLOYMENT_PLAN.md` (plan, rehearsal and production runbook) and `docs/DEPLOYMENT.md` (repository deployment notes).

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
| Release | Commit `cb216f722d33820d84537885ac18e2d93e70030b` on `origin/main` (pull request #3, fast-forward of `fdfe3b5`) |
| Mode | Central (`PM_AUTH_MODE=central`), `PM_ENV=production`; four SQLite databases: identity, Salcomp, Lity, Valor Log |
| Users | One active global Super Admin, `pluisada@gmail.com` (observed: identity database counts users 1, active global admins 1, with password 1, email verified 1, memberships 0). No other user, no company Admin, no data |
| Container | `pm-central-app-1` (Compose project `pm-central`), **no published host port** (observed: `docker port` empty, nothing listening on host port 8000, port 8000 times out from the internet) |
| Edge | Existing Nginx Proxy Manager (NPM 2.15.1), proxy host `pipeline.pluisada.com.br` to `http://sales-pipeline:8000` over the Docker network `nginx-proxy_default` |
| AI assistant | **Disabled** (no `OPENROUTER_API_KEY`); the panel stays visible and answers that the key is not configured |
| Backups | Daily local backup at 03:30 Brasilia time (06:30 UTC) with a manifest. **Local to the VPS only** |
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

**Environment variable names in `pm-central.env`**: `PM_CENTRAL_DATA_HOST_PATH`, `PM_CENTRAL_DATA_CONTAINER_PATH`, `PM_IDENTITY_DATABASE_URL`, `PM_COMPANY_DB_SALCOMP`, `PM_COMPANY_DB_LITY`, `PM_COMPANY_DB_VALOR_LOG`, `PM_PUBLIC_URL`, `PM_BEHIND_PROXY`, `RATE_LIMIT_TRUSTED_PROXIES` (the NPM address `172.16.1.2/32`, to be re-read if NPM is ever recreated), `HSTS_ENABLED`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USER`, `SMTP_PASSWORD`, `SECRET_KEY`. `OPENROUTER_API_KEY` is intentionally absent. `DATABASE_URL`, `PM_ENV`, `PM_E2E`, `PM_SETUP_TOKEN`, `PM_ALLOW_CREATE_DB` and `PM_REQUIRE_EXISTING_DB` must not be set (the Compose file sets what it needs).

**Email**: Gmail SMTP on port 587 with STARTTLS (certificate and host verification are enforced by the application); the sender is the Super Admin's own Gmail account, authenticated with an application password. Emails are plain text in this release.

**NPM proxy host** (verified in NPM's database): domain `pipeline.pluisada.com.br`, scheme `http`, forward host `sales-pipeline`, port 8000, certificate Let's Encrypt, Force SSL on, HTTP/2 on, Websockets off, HSTS off in NPM (the application sends one `Strict-Transport-Security` header), Block Common Exploits on, no access list, Advanced block exactly `access_log off;` and `client_max_body_size 11m;`.

**Hardening** (observed): container user uid 10001, no Linux capabilities, `no-new-privileges`, no published port, no `/docs`, `/redoc` or `/openapi.json` (HTTP 404), no CORS header for a foreign origin, exactly one HSTS header, CSP and the other security headers present.

## 6. Operations

**Backups.** `sales-pipeline-backup.sh` runs from cron daily at 06:30 UTC (03:30 Brasilia). Each run takes a consistent SQLite online backup of the four databases with the repository helper, verifies integrity, foreign keys and logical equality, and writes a manifest: UTC timestamp, release SHA, image ID, and for each file its size, SHA-256, integrity result, foreign-key violations, `user_version` and the row count of every table (counts only). `LAST_SUCCESS` is written only after the set verifies; a failed set is renamed `<timestamp>.FAILED`. Retention: the script **lists** sets older than 14 days (keeping the newest three) and **deletes nothing**. Restore procedures (verify a set against its manifest, restore into an isolated scratch directory, full production restore) are in `docs/VPS_DEPLOYMENT_PLAN.md` section 8. They were proven on the rehearsal (observed). **No restore has been performed on production data**, and the monthly restore test required by the plan is not yet scheduled.

**Monitoring.** `sales-pipeline-monitor.sh` runs every five minutes and emails the Super Admin through the application's own SMTP when it finds: the public `/api/health` check failing; the container not healthy; `/docker` disk at or above 80%; the last successful backup older than 26 hours (or none recorded); a `.FAILED` backup set; the certificate expiring in under 14 days or unreadable. It emails once per distinct problem set, repeats after six hours while the problem persists, and sends a recovery message. It also supports `--test-email`. A test alert and a normal run were observed; receipt of the test email is owner-reported. **It runs on the same VPS, so it cannot alert if the whole VPS or its network is down.** NPM renews the Let's Encrypt certificate automatically (provider behaviour; renewal not yet observed).

**Routine tasks.** Upgrade (new verified commit, tests and audits, backup before upgrade, rehearse, then deploy): plan section 7, Phase F3. Adding company Admins and users (Super Admin only; at most two Admins per company counting pending invitations): Phase F6. Sending a new activation link to the Super Admin: `identity_admin_cli.py send-activation --email <address>` inside the container. Command pattern: `docker compose --env-file /docker/sales-pipeline/pm-central.env -p pm-central -f /docker/sales-pipeline/src/docker-compose.central.yml -f /docker/sales-pipeline/docker-compose.vps.yml <command>`. Never use `down -v`, any `prune` command, or `docker compose run` without `</dev/null` inside scripts read from standard input (see the plan's lessons, section "Execution status").

## 7. Accepted risks and known gaps

| # | Item | Status |
|---|---|---|
| R1 | **NPM error log can contain a token.** NPM's default access log records the full URI with the query string (confirmed on 2.15.1 in the rehearsal with a synthetic marker); `access_log off;` in the proxy host removes it from the access log (verified clean for NPM log files, NPM container log and application log). When the upstream is unreachable, NPM's **error log** records the request line including the query string (confirmed in the rehearsal; for production the result is inherited from the rehearsal because the NPM container, its image and the Advanced block are the same, and was not provoked on production). A reset or invitation link opened while the application is down could therefore appear in `proxy-host-<id>_error.log`. Mitigations: tokens are single-use, stored hashed and expire (reset 1 hour, invitation 7 days); `/docker/nginx-proxy/data/logs` was restricted to `700 root:root` (observed; a rotation dry run showed no errors; other sites kept answering 200). | **Accepted by the owner** |
| R2 | **No off-host copy of application backups.** Backups, manifests and databases live only on the VPS. Loss, deletion or compromise of the VPS loses the data. The only other copy is the provider's weekly VPS backup, which was **not checked for this VPS** (provider claim: weekly backups and one manual snapshot are included in Hostinger VPS plans) and has no tested restore path for these files. | **Accepted by the owner**. The system is **not** disaster-recovery ready |
| R3 | **No automatic cleanup of old local backups.** Sets accumulate (about 1 MB each at the time of writing); the disk monitor alerts at 80%. | Open gap; deletion not enabled until it has been exercised on aged test sets |
| R4 | **No independent monitoring.** A total VPS or network failure raises no alert. | **Accepted by the owner** |
| R5 | Shared host: no swap and about 3.9 GB of free memory at the time; OpenClaw alone uses about 1.9 GB. The production container is small but memory pressure could affect all services. | Open, not caused by this deployment |
| R6 | The 1 skipped backend test is not individually identified; the frontend lint has 1 warning; the assistant's error message names the internal variable `OPENROUTER_API_KEY`. | Open, cosmetic |

## 8. Deferred tests

These need more than one account, so they were **not** run (the automated suite covers them; they are not proven in production):

1. Maximum two company Admins, including pending Admin invitations (a third is refused; an accepted invitation does not count twice).
2. Refusal of a second global administrator (`identity_admin_cli.py bootstrap` with another address, expected refusal, no data change).
3. Independent CLEAR per required reviewer; one reviewer's CLEAR does not clear another's; voiding of a removed or deactivated reviewer's pending acknowledgment.
4. Permanent user cancellation (the cancelled user cannot sign in).
5. Stale card-version rejection on production data (it was observed in the rehearsal, not in production).

Plan: run them at the first additional Admin onboarding (plan Phase F6, step 4), with clearly labelled test cards, and record the results in a new production record.

## 9. Open items

- The owner is to delete the rehearsal proxy host in NPM (it would answer 502 now that the rehearsal container is gone). The rehearsal DNS record no longer resolved at the last check (observed 2026-10-26); the NPM entry was not re-checked.
- Health bot (`@paulo_saude_bot`, OpenClaw): after the NPM changes the bot did not answer at first; the investigation (read-only) showed Telegram flood control (HTTP 429) on the bot's replies, no request reaching `/webhook/saude/paulo`, and other webhooks through NPM answering 200. The owner later reported that the original messages were answered. **Not independently verified** (no log check after the recovery). Debug notes for OpenClaw were delivered separately to the owner and are not part of this repository.
- Documentation that still describes the pre-deployment state and needs a follow-up edit by whoever holds the local, uncommitted changes to them: `CLAUDE.md` (Status section), `docs/DEPLOYMENT.md` (statements that nothing was run on a server, "none exists yet" for the reverse proxy, the central-mode section), `docs/SECURITY.md` and `docs/TESTING.md`. They were **not edited here** because they carry uncommitted local modifications.
- The Hostinger manual snapshot before the NPM edit was skipped by the owner; a copy of the NPM database (integrity checked) was taken instead and is kept in root's home on the VPS.

## 10. Recommendations (not done)

1. Add an **encrypted off-host copy** of each backup set (for example to a separate bucket or another machine), verify its hash at the destination, and test a restore from it. Until then, do not describe the system as disaster-recovery ready.
2. Enable local backup pruning after exercising it on aged test sets; keep at least the newest three sets and the pre-upgrade set.
3. Schedule the monthly restore test (plan section 8.4) and record it under `records/`.
4. Add an **independent external check** of the HTTPS health endpoint (outside the VPS) when a free option without extra accounts is acceptable.
5. Review the memory budget (swap or container limits) with the OpenClaw owner before adding load.
6. Complete the deferred multi-user tests at the first Admin onboarding.
7. Enabling the AI assistant later: provide a dedicated `OPENROUTER_API_KEY` in `pm-central.env` and restart the container; the key, its cost and the rate limits are a separate decision.
8. Update the stale documents listed in section 9 together with the owner's pending local edits, and pin base-image digests in the Dockerfile when convenient.

## 11. Recovery and redeployment pointers

- **Application or configuration failure, data intact**: stop or recreate only `pm-central-app-1`; never touch `data/`. Check `docker logs`, the production-config message (the application refuses to start and lists every problem) and `docker compose ... config`. Rollback for code is the previous image tag in `docker-compose.vps.yml`.
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
| The monitor's behaviour under a real failure; certificate renewal; the first scheduled (cron) backup run | **Not verified** at the time of writing |
| Restore on production data | **Not performed** |
| Provider weekly backups for this VPS | Provider claim, not checked |
| Health bot working again | Owner-reported |
