# VPS Deployment Plan: Sales Pipeline (PM Pilot), central mode

Version 3 (final review applied), 2026-10-25. Supersedes versions 1 and 2 of the same day. Version 3 applies the audit findings to version 2, and the final review corrections listed at the end of the table below; the architecture and the owner's confirmed decisions are unchanged.

**Status (updated 2026-10-26): this plan was executed.** Phase A, the disposable rehearsal (Phase D) and the production deployment (Phase E) were carried out on 2026-10-25/26 and the first operations (Phase F) were installed; production is live at `https://pipeline.pluisada.com.br`. The state, accepted risks and open items are in `docs/PRODUCTION.md`; what actually happened, the deviations and the template defects found are in the new section "Execution status and lessons" below. Sections written before execution (the runbook, the prerequisite tables, the risk table) are kept unchanged as the reference for redeployment and for future releases; their "Open" and "Pending" statuses describe the planning stage. **Command blocks are templates unless the execution section marks them proven.** **Update (2026-10-26, later the same day):** two code-only upgrades were then deployed with the shortened procedure recorded under F3 below (`e1a79ae`, then `762c01f`; `docs/PRODUCTION.md` sections 3A and 3B). Statements below about plain-text emails, the unreleased branded HTML emails and the disabled AI assistant describe the first release `cb216f7` and are historical: the HTML emails and the AI assistant are in production now.

Evidence tags: **[VPS-doc]** the VPS documentation folder (`Docs da Infra da VPS`: `CLAUDE.md`, `vps-context-primer.md` = "primer", `Setup_VPS_pluisada_Ago2026_atualizado.docx` = "Setup docx", `resumo-tecnico-infra_1.html`, `infra-explicada-para-leigos_1.html`, `arquitetura-agentes-saude-openclaw.md`); **[repo]** a file in this repository at `fdfe3b5` unless stated; **[v1]** a read-only observation made while preparing version 1 (`git ls-remote` against GitHub and a public `dig`; not repeated since); **[upstream]** public NPM source code; **[docs]** public tool documentation (Docker Compose, pip-audit, nginx) consulted while preparing this plan; **[inferred]** reasoning to be verified; **[unknown]** not established.

### What changed in version 3 (audit corrections)

| # | Correction | Where |
|---|---|---|
| 1 | NPM token-logging: marker test kept; new controlled upstream-failure test for the NPM error log; NPM container, application and Docker logs checked; verified results separated from residual risks | C10 (tests T1 to T4), 7 Phase C analysis, 9 record |
| 2 | Dependency audits: exact `npm audit` and `pip-audit` commands, prerequisites, a findings record, severity and exception rules, kept apart from functional results | A10, 5 (P9), 9 record |
| 3 | Compose: explicit minimum version and a behavioural check; the loopback-publish fallback is removed; stop if unavailable; rehearsal and production use the same rule | 4.2, A3, B1, C8, 11 risk 8 |
| 4 | Backups: sections 8.2 to 8.5 rewritten (directories, permissions, mounts, scratch validation), per-backup manifest, restores checked against the manifest, mandatory gates, templates labelled | 8 |
| 5 | Gates and DNS: old A12 moved to B1; A-numbering and gates reconciled; rehearsal DNS and TLS before Phase D; production DNS deferred to Phase E (E4) | 5, 7 Phase A, D, E, 12 |
| 6 | Mandatory deployment/rehearsal record | 9 |
| 7 | Final review: Compose floor corrected to v2.24.4 with the official documentation cited (4.2); the upstream-failure test restricted to the rehearsal container, with cross-environment guards (6, C10, D7, D9, cleanup) and production inheriting that result under stated equivalence checks (E11); backup manifest, verifier and restore scripts made consistent (8.2 to 8.5); a shell syntax error fixed in 8.4; cross-references repaired (A-notes, E0, 9, 10.1, 11, 12) | as listed |

---

## Owner amendments and Phase A results (2026-10-26) - these override the sections they name

| # | Amendment | Overrides |
|---|---|---|
| 1 | **Release SHA is now `cb216f722d33820d84537885ac18e2d93e70030b`** (PR #3, fast-forward of `fdfe3b5`, on `origin/main`). Verified on the exact SHA in clean checkouts: backend 3475 passed / 1 skipped / 0 failed; central E2E 39 passed; frontend unit 799 passed, lint 0 errors, `tsc` clean, build ok; `pip-audit` 2.10.1 no known vulnerabilities (34/34 packages); `npm audit` 0 findings. A fresh clone of `origin/main` has the same tree hash (`990751e1...`) as the tested clone. G-A.1 and G-A.2 are met for this SHA. | A1 expected SHA, 3.1, 3.3, 5.4, G-A |
| 2 | **Image is built on the owner's Mac** for `linux/amd64` (the VPS is x86_64 with 4.2 GB free RAM and no swap, so building there could starve running services) and loaded on the VPS with `docker save` / `docker load`. Both steps need Docker Desktop running on the Mac. | A11 |
| 3 | **Backups: local on the VPS only; the off-host copy and its encryption are waived by the owner** (risk accepted: loss or compromise of the VPS loses the data; only Hostinger's automatic weekly backup remains). Daily local sets, manifest, verification, retention and restore tests stay mandatory. | P3, 8.1 "off-host" gate, 8.2 off-host block, D8, E8, E0, 9.5 |
| 4 | **Alerts:** a script on the VPS checks health, disk and backup age and mails the owner through the same SMTP. No external monitor and no alert if the whole VPS is down (accepted). | P5, F |
| 5 | **SMTP:** Gmail, `smtp.gmail.com:587`, sender = the authenticated account, dedicated app password typed by the owner on the VPS (never shown to Claude). Reachability from the VPS verified (TCP only). | P2 |
| 6 | **Rehearsal hostname:** `pipeline-rehearsal.pluisada.com.br` (DNS resolves to `automacao.pluisada.com.br`, observed 2026-10-26). | P6, A8a |
| 7 | **VPS facts observed 2026-10-25 (read-only):** Ubuntu 24.04.4, x86_64, 2 vCPU, 7.8 GB RAM (4.2 GB available, no swap), 77 GB free disk, Docker 29.6.2, **Compose v5.3.1 (meets the floor; the B1 assertion is still mandatory)**, Swarm active, port 8000 free, no `/docker/sales-pipeline*`, network `nginx-proxy_default` 172.16.1.0/24 with NPM at 172.16.1.2 and `simone-site` at 172.16.1.3 (so `RATE_LIMIT_TRUSTED_PROXIES` = `172.16.1.2/32`, to be re-read in E1), `sqlite3` absent (NPM data copy in E3 needs the NPM-stop or Hostinger-snapshot route), `git`, `python3`, `flock`, `curl`, `gpg` present, firewall exactly 22/80/443 + Drop (owner screenshot), `smtp.gmail.com:587` reachable. | A3 to A7 |
| 8 | **Standing authorizations:** routine, non-destructive checks of the release and the rehearsal need no per-step approval. **Production deployment stays a separate approval**, as do production DNS, production databases, activation emails, NPM edits and any package install. | P10, E0 |

---

## Execution status and lessons (2026-10-25 and 2026-10-26)

Evidence levels: **observed** (command output seen by the operator), **owner-reported** (stated by the owner, not observable by the operator). Records: `records/rehearsal-20261026.md` and `records/production-20261026.md` on the VPS (non-secret).

### Phase and gate status

| Phase / gate | Status | Notes |
|---|---|---|
| A. Preflight | Done | Remote SHA, clean clone, VPS read-only inventory, tests and audits. The first audit of `fdfe3b5` failed G-A.2 (Python runtime advisories); fixed in pull request #3 (commit `cb216f722d33820d84537885ac18e2d93e70030b`); tests 3475 passed / 1 skipped / 0 failed, central E2E 39 passed, `pip-audit` and `npm audit` clean on that exact SHA |
| G-A.1 and G-A.2 | Met | For `cb216f7` |
| Image (A11) | Done on the Mac | `linux/amd64`, loaded on the VPS with `docker save \| docker load`, layer digests identical; the VPS did not build it (memory) |
| D. Rehearsal | Done, then removed | D2 to D9 passed; D10 deferred (below); C9 (bot test) first not verified, then owner-reported |
| G-D | Approved by the owner in writing | With the accepted risks listed in `docs/PRODUCTION.md` section 7 |
| E. Production | Done | E1 to E15 executed in order; E3's Hostinger snapshot skipped by the owner (NPM database copy taken instead) |
| F. Operations | Installed | Daily local backup (06:30 UTC), five-minute email monitor |

### Prerequisites as resolved

| ID | Resolution |
|---|---|
| P1 production DNS | Created by the owner at E4 (CNAME `pipeline` to `automacao`, DNS only) |
| P2 SMTP | Gmail, `smtp.gmail.com:587`, application password typed by the owner on the VPS |
| P3 backup destination | **Waived by the owner**: local backups only, no off-host copy (risk accepted) |
| P4 preflight | Done (Ubuntu 24.04.4, x86_64, 7.8 GB RAM and no swap, Docker 29.6.2, Compose v5.3.1, NPM at 172.16.1.2) |
| P5 alerts | Script on the VPS with email; no external monitor (accepted) |
| P6 rehearsal hostname | `pipeline-rehearsal.pluisada.com.br`, created and later removed |
| P7 source access | Public repository, anonymous clone |
| P8, P9 evidence | Recorded for `cb216f7` |
| P10 authorizations | Given per phase in writing |

### Deviations from the plan

- Off-host backup waived (P3); retention pruning left as listing only; the 8.1 "off-host" and "retention" gates are therefore **not met** and the system is not disaster-recovery ready.
- NPM proxy host created in the UI by the owner; at the first rehearsal save Force SSL and HTTP/2 were not stored and Websockets was on; corrected and verified in NPM's database. At production creation the settings were checked after saving and were right.
- T2 (upstream failure) was run only on the rehearsal; production inherited the result after the E11 equivalence check (same NPM container and image, identical Advanced block). The residual NPM error-log risk was accepted by the owner and the NPM logs directory was restricted to `700 root:root`.
- The owner reset the NPM administrator password (the old one was not remembered): consistent database copy first, only the `auth` row changed, new password typed by the owner on the VPS without the operator seeing it.
- The VPS has no `sqlite3` binary; Python's `sqlite3` module was used for read-only database checks and for the consistent copy (`Connection.backup`).
- The deferred multi-user checks (D10) were not run (next section).

### Template defects found while executing (corrected in the executed commands; the blocks above are left as written)

1. `docker compose run` (also with `-T`) reads standard input; inside a script piped to `ssh ... bash -s` it swallowed the rest of the script. Use `</dev/null` on every compose call in such scripts.
2. In the backup script the compose helper must **not** redirect standard input, because the Python program is passed through a heredoc to `compose run ... python -`.
3. `docker run ... python -` needs `-i` to read the heredoc (8.3 to 8.5 blocks).
4. A `grep -c` that finds nothing exits 1: do not treat it as a failure under `set -e`.
5. After saving an NPM proxy host with a new certificate, re-open it and confirm Force SSL, HTTP/2 and Websockets; check the stored values in NPM's database.
6. Verified working for the first time: database creation, bootstrap, diagnostics and the application inside the container on Linux; `ports: !reset []` with Compose v5.3.1 (rendered configuration shows no ports); backup, manifest, isolated restore and full restore drill against the rehearsal.

### Deferred checks (not passed)

Two-Admin limit incl. pending invitations, refusal of a second global administrator, independent CLEAR per reviewer, reviewer removal, permanent user cancellation, and the stale card version on production data. They need extra accounts and are scheduled for the first additional Admin onboarding (Phase F6 step 4). No restore has been performed on production data.

---

## 0. Confirmed decisions incorporated

| # | Decision (owner, 2026-10-25) | How the plan applies it |
|---|---|---|
| 1 | Launch with **exactly one global SUPERADMIN**; no company-level ADMIN required; others configured after launch | `identity_admin_cli.py bootstrap` creates the three companies and the single global administrator and nothing else (B3). Admin onboarding is post-launch (F6) |
| 2 | Initial SUPERADMIN email `pluisada@gmail.com` (the development address, explicitly authorized) | Used as `--admin-email` and as the `send-activation` target. Only the address is reused; no development password, token or database is |
| 3 | Release = what is published on `origin/main`, expected `fdfe3b5`; verify the real remote HEAD; no uncommitted local changes | A1 to A2 record the exact SHA from the remote; the VPS builds from a fresh clone pinned (detached) to that SHA; the Mac working tree is never a source |
| 4 | **Fresh** production databases; nothing copied from development | B2 creates them in the release image; copying `.db` files, backups or `.env` from the Mac is forbidden |
| 5 | Hostname `pipeline.pluisada.com.br`, subject to DNS verification | A8b checks it; P1 names who creates the record; the record is created at E4 |
| 6 | Existing **NPM**; no Traefik | Phase C |
| 7 | Directory `/docker/sales-pipeline`; Compose project `pm-central` | Used throughout (the rehearsal uses its own names, D1) |
| 8 | AI may stay disabled at launch | `OPENROUTER_API_KEY` stays unset; the application supports this (3.4); AI is not a launch gate |
| 9 | Working SMTP is a launch prerequisite; determine the minimum; invent nothing | Section 5.2 gives the minimum from the code; provider, sender and credentials are unresolved inputs (P2) |
| 10 | Daily backups, off-host copy, documented restore, periodic restore tests | Section 8 and Phase F; destination unresolved (P3); these are mandatory gates |
| 11 | Disposable rehearsal before production | Phase D is a gate for Phase E; Phase E cannot start without the signed rehearsal record |

---

## 1. Executive summary

- **Design:** one Compose project (`pm-central`) outside Swarm, one container from the repository's `docker-compose.central.yml` plus a small VPS-only override, **no published host port**, attached to the existing NPM network under an explicit alias. NPM terminates TLS for `pipeline.pluisada.com.br` (Let's Encrypt). This copies how `simone-site` already runs [VPS-doc: Setup docx 3.8, 4.2].
- **Access model:** the database chain creates one global administrator with no password. The only way to give it one is the emailed single-use link, so **working SMTP, a resolving hostname and a working HTTPS proxy must exist before activation**, in that order. No repository command sets a password, by design [repo: `docs/DEPLOYMENT.md`, `identity_admin_cli.py`].
- **Evidence gaps:** no test result is recorded for `fdfe3b5` and no dependency audit has ever been run for it. Both are separate gates (G-A.1 functional, G-A.2 audit), and neither is claimed as passing.
- **NPM logging:** NPM's default access log records `"$request_uri"` including the query string, so reset and invitation links would be written in clear [upstream]. `access_log off;` is required, and its effect is validated with synthetic markers in the access log, the **error log under a controlled upstream failure (run only against the disposable rehearsal container, never production)**, NPM's container log, the application log and the Docker log (C10). Results are recorded as verified or as residual risk.
- **No published port, no weakening:** the override removes the file's `127.0.0.1:8000` publish with `ports: !reset []`. If the host's Compose cannot do that, the deployment **stops**; there is no fallback that publishes a port (4.2).
- **Gates in order:** A (preflight; G-A.1 and G-A.2) > D (rehearsal; signed record, G-D) > E (production, with production DNS created at E4) > F (operations). No phase starts without the previous gate and the owner's explicit authorization of that phase.

**Readiness: ready after the specified prerequisites** (section 13).

---

## 2. What the VPS documentation establishes

| Topic | Established fact | Source |
|---|---|---|
| Host | Hostinger KVM 2, Ubuntu 24.04 LTS, `manager1` / `srv1769808.hstgr.cloud`, 2 vCPU, 8.3 GB RAM, public IP `187.77.236.218` | Setup docx 1.1 |
| Docker | Engine 29.6.2 (primer) / 29.6.0 (older docx); Swarm, single manager | primer 1; Setup docx 1.1 |
| Orchestration | Hybrid: Swarm for n8n, Redis, Postgres, Evolution, Portainer; **standalone Compose** for NPM, OpenClaw and `simone-site` | primer 1; Setup docx 3.8 |
| Edge | NPM (`jc21/nginx-proxy-manager:latest`, v2.15.1 observed), container `nginx-proxy-npm-1`, ports 80/443 public, admin :81 only through an SSH tunnel, Let's Encrypt per proxy host, Force SSL, HTTP/2 | Setup docx 3.1; primer 2, 5 |
| Precedent | `simone-site`: `/docker/simone-site/`, network `nginx-proxy_default`, no host port, proxy host `simone-site:80` | Setup docx 3.8, 4.2 |
| Networks | `nginx-proxy_default` (bridge), `network_public` (overlay), `docker_gwbridge` 172.20.0.0/16. NPM's bridge is `172.16.1.x` in the primer | Setup docx 2.3; primer 1 |
| Directories | `/docker/<project>/` with Compose file, `.env`, `data/` | Setup docx 7.1 |
| DNS | Cloudflare zone `pluisada.com.br`, all records **DNS-only**; `automacao` A record to the IP; services are CNAMEs to `automacao.pluisada.com.br` | Setup docx 4.1 |
| Firewall | Hostinger panel, default DROP, only 22/80/443 open | Setup docx 5; primer 5 |
| Admin access | SSH key only, as **root**; NPM :81 only via `ssh -L 8181:localhost:81 root@<IP>` | primer 5; `CLAUDE.md` |
| Ports in use | 22, 80, 443, 81, 9000, 5678, 8080, 40476. **8000 is not listed** | Setup docx 3, 4.2; primer 5 |
| Secrets | `.env` beside each Compose file; values never written into documents; keys rotated after exposure | `CLAUDE.md`; Setup docx 7.2, 8.1 |
| Cron precedent | `/etc/cron.hourly/openclaw-limpa-anexos` (host cron is already used) | primer 4.1 |
| Backups | **None documented** for the host; only a planned, not-yet-done dump of the `saude` database to Google Drive | arquitetura-agentes 6, 8 |
| Monitoring / log rotation | **None documented** | all files |
| Operating rules | Tell the owner the impact before restarting NPM, n8n or Postgres and test `@paulo_saude_bot` afterwards; test a risky change in a second session before closing the old access path | `CLAUDE.md`; primer 4.1 |

### Contradictions and gaps (still open)

| # | Finding | Resolution in this plan |
|---|---|---|
| V1 | The original brief assumed Traefik; the VPS uses NPM (Traefik is incompatible with Docker 29.x) | NPM, as decided |
| V2 | NPM bridge `172.16.1.x` (primer) vs proxy-host targets on `172.20.0.1` (Setup docx) | A5 reads the real network and NPM address |
| V3 | Docker 29.6.0 vs 29.6.2; disk 100 GB/10 GB used vs 96 GB/21% | A3 reads them |
| V4 | `arquitetura-vps-*.md`, `gerar-arquitetura-vps.sh` and old PDFs are cited by the primer but **absent from the folder**; no current network/volume/digest inventory | A3 to A6 take a fresh inventory |
| V5 | Setup docx contains a plaintext n8n encryption key | Out of scope; the owner should remove it from the document (not repeated here) |
| V6 | OpenClaw project naming/port inconsistencies; stale `agente.pluisada.com.br` proxy host | Unrelated; not touched |
| V7 | No backup, off-host location, monitoring or alert channel is documented | Section 8, Phase F, P3 and P5 |
| V8 | CPU architecture not documented | A3 |

---

## 3. The release and the application

### 3.1 Release facts

- Repository `https://github.com/Pluisada/project-pm.git`, branch `main`. At version 1 the remote HEAD and local `origin/main` were both `fdfe3b5b7f58d167a7cd25de17f1d12ec2105052` [v1]. **It must be re-read from the remote in A1; if it has moved, stop and have the owner decide which SHA is the release.**
- The Mac working tree (`/Users/pauloluisada/IA/pm-pilot-dev`) has uncommitted changes (42 modified and 7 untracked at version 1; 49 entries when this revision began, not counting this document). They include an unreleased HTML-email change (`mailer.py`, untracked `branded_email.py`), report changes and a lead-generation component. `git grep` showed HEAD references neither `branded_email` nor that component, so the commit is self-consistent. **Consequence: the invitation and reset emails of this release are plain text.**
- `CHANGELOG.md` still describes the access-limits feature as "working tree" although it is committed; stale wording only.

### 3.2 Architecture [repo]

- One FastAPI process (one uvicorn worker, port 8000, uid/gid 10001) serves `/api/*` and the Next.js static export at `/`, same origin. Image: node:24-alpine builds the frontend, python:3.11-slim runs the app with `uv sync --locked`. The Dockerfile has no `HEALTHCHECK`; the Compose file defines one on `/api/health` (30 s, 20 s start period).
- Four SQLite files in one directory: `identity.db` (users, companies, memberships, invitations, tokens, audit) and `salcomp.db`, `lity.db`, `valor-log.db`. The tooling treats WAL databases as unsupported, so they are rollback-journal databases.
- Startup never creates or migrates a database. With `PM_ENV=production` it refuses to start (listing every problem) unless: `APP_BASE_URL` is a bare non-local https origin; the four files exist, are distinct and are not protected development names; SMTP host and a sender are set; `PM_E2E` is unset; proxy variables are consistent (`production_config.py`). `/docs`, `/redoc`, `/openapi.json` are off.
- `GET /api/health` returns `{"status":"ok","test_mode":false}`; `GET /api/mode` returns `{"auth":"central"}`.
- Fresh-database tooling (all in the image): `create_identity_db.py`, `identity_admin_cli.py bootstrap | send-activation`, `create_company_db.py --company <slug>`, `diagnose_company_schema.py`, `diagnose_identity_limits.py`. `create_company_db.py` creates every table the release needs, including `card_versions`, `card_review_obligations` and `card_review_acknowledgments`, so **the `init_company_*` scripts are not part of a fresh install**.

### 3.3 Test evidence for the release: what is and is not known

| Evidence | Status |
|---|---|
| Full backend suite on `fdfe3b5` | **Not recorded.** Must be run (A9, gate G-A.1) |
| Focused tests for this commit (`test_admin_limits.py`, `test_admin_limit_http.py`, `test_independent_card_review_clear.py`, `test_review_membership_races.py`, edits to `test_card_versions.py`, `test_create_company_db.py`, `test_identity_service.py`, `CentralReviews.test.tsx`) | Files exist in the commit; **no results are recorded in the documents read**; the changelog says only "tests on temporary databases". Not claimed as passing. A focused run is **not** a full-suite run and is recorded separately |
| Dependency audits (`npm audit`, `pip-audit`) | **Never run for this release** (earlier `npm audit` figures, 6 findings on 2026-10-16, predate the current `next`; `pip-audit` was never run) [repo: `docs/DEPLOYMENT.md`]. Gate G-A.2 (A10), separate from functional results |
| Last recorded full backend run | 2527 passed, 1 skipped (unidentified), 0 failed on Python 3.11.15, 2026-10-19, for an earlier commit (`175a549`) [repo: `CLAUDE.md`]. **Does not apply to `fdfe3b5`** |
| Last recorded central end-to-end run | 38 tests, 2026-10-22, earlier commit [repo: `docs/DEPLOYMENT.md`]. Does not apply |
| Docker image built and started | Built for an earlier legacy commit on Docker Desktop (macOS/ARM64) only. **The central compose file, a Linux host, production mode in a container and the create-database chain through `docker compose run` have never been run** [repo: `docs/DEPLOYMENT.md`] |
| Backup helper | `make_backup` and the other helpers were used on the developer's Mac for the four central databases on 2026-10-22 and in disposable rehearsals [repo: `docs/DEPLOYMENT.md`]; never inside this image or on Linux |
| Manual acceptance of this feature | The changelog records none for access limits and independent CLEAR |

Repository quirks the test run must account for: `test_static_files.py` passes only while the git-ignored `backend/static` exists (copy `frontend/out` there after the frontend build), and a clean full run on Python 3.12 has not been recorded (the image uses 3.11, so run on 3.11).

### 3.4 AI disabled

`OPENROUTER_API_KEY` is read once at import; when unset, AI calls raise `AIError` and the assistant answers 503, while the rest of the application is unaffected [repo: `ai.py`; `docs/DEPLOYMENT.md`]. The central Compose file passes the variable as empty by default. The assistant panel stays visible and reports an error if used; this is checked in the rehearsal (D6). Enabling AI later is an env-file change and a restart.

---

## 4. Target architecture and configuration

```
Internet --443--> NPM (nginx-proxy-npm-1; Let's Encrypt; Force SSL; HTTP/2)
                   | proxy host <hostname> -> http://sales-pipeline:8000
                   v  network nginx-proxy_default (no host port is published)
        container pm-central-app-1  (uid 10001, cap_drop ALL, no-new-privileges, healthcheck)
                   | bind mount rw  /docker/sales-pipeline/data -> /data
                   v
        identity.db  salcomp.db  lity.db  valor-log.db   (+ SQLite journal files)
```

Why not Swarm or Traefik: SQLite with one worker gains nothing from Swarm and a Swarm service would need a host-published port; Traefik is incompatible with the host's Docker. The existing NPM + standalone Compose pattern needs no new technology.

### 4.1 Layout (decision 7; subdirectory names other than the two decided ones are proposals)

| Item | Path / name | Owner, mode |
|---|---|---|
| Project directory | `/docker/sales-pipeline` | root, 750 |
| Source (fresh clone, detached at the verified SHA; shared read-only by rehearsal and production) | `/docker/sales-pipeline/src` | root |
| Environment file | `/docker/sales-pipeline/pm-central.env` (never inside `src`, never committed) | root, 600 |
| VPS override | `/docker/sales-pipeline/docker-compose.vps.yml` | root, 640 |
| Data (4 databases) | `/docker/sales-pipeline/data` | `10001:10001`, 700 |
| Local backup sets | `/docker/sales-pipeline/backups` (**outside** the mounted data directory) | `10001:10001`, 700 |
| Restore-test scratch | `/docker/sales-pipeline/restore-test` | `10001:10001`, 700 |
| Records (non-secret) | `/docker/sales-pipeline/records` | root, 750 |
| Compose project / container | `pm-central` / `pm-central-app-1` | |
| Image tag | `sales-pipeline:<verified SHA>` (immutable per release; kept for rollback) | |
| NPM alias | `sales-pipeline` | |

### 4.2 VPS override and the Compose requirement

```yaml
# /docker/sales-pipeline/docker-compose.vps.yml  (proposal; the rehearsal copy uses its own alias and project)
services:
  app:
    image: sales-pipeline:<VERIFIED_SHA>      # built once by hand (A11); the same image ID for rehearsal and production
    pull_policy: never                        # never try to pull this local tag from a registry
    ports: !reset []                          # publish NOTHING on the host
    networks:
      default: {}
      proxy:
        aliases: [sales-pipeline]             # explicit alias; the generic service name "app" could collide on the shared NPM network
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "5" }   # no rotation policy is documented for this host
networks:
  proxy:
    external: true
    name: nginx-proxy_default                 # replace with the name found in A5 if it differs
```

The base file publishes `127.0.0.1:8000:8000`, and Compose **concatenates** `ports` across files [docs], so the override must remove the entry explicitly; hence `!reset`.

**Requirement (mandatory, no fallback):**
- **Minimum Compose: v2.24.4.** Source: Docker's official documentation, "Merge Compose files", <https://docs.docker.com/reference/compose-file/merge/>. It documents `ports: !reset []` (section "Reset value") and `!override` (section "Replace value"). Reading its sources, the version statement ("Docker Compose 2.24.4 and later", the `Compose replace file` summary-bar entry in Docker's documentation data) is attached to the "Replace value" section, and the "Reset value" section shows the `ports: !reset []` example without a separate version statement. This plan adopts **v2.24.4 as the floor for the whole override** (the conservative reading; both tags are documented together on that page). `docker compose version` must report v2.24.4 or higher. Docker Engine 29.x packages from Docker's own repository should ship a much newer Compose plugin [inferred; A3 reads the real value].
- **The rendered-configuration assertion is a mandatory runtime check and is not replaced by the version number:** a version at or above the floor is only a pre-screen; the rendered configuration must contain **no `ports` entry** for the service (assertion in B1, repeated at every start and upgrade), and `docker port <container>` must be empty after start (C8). A Compose that does not know the tag fails to load the file or leaves the port in place; either way the assertion decides. A host that fails the version pre-screen or either check **stops the deployment**; nothing is relaxed to get past it.
- **There is no loopback fallback.** Keeping `127.0.0.1:8000:8000` would contradict C8 (no host listener on 8000), would add a port the VPS documentation does not list, and would stop the rehearsal and production from running side by side. Changing the host's Compose is itself a host change (and a Docker package upgrade can restart the daemon, which would disturb Swarm services and the health agent): it needs its own plan and the owner's separate authorization, and is **not** part of this plan.
- The rehearsal and production use the same rule, the same override shape and the same assertions. Because nothing is published, they can run at the same time and cannot collide on a host port.
- One-off `docker compose run` containers (database creation, diagnostics, SMTP test) publish nothing by default (Compose's `run` clears the service ports unless `--service-ports` is passed [docs]), and do not take the service's network aliases unless `--use-aliases` is passed [docs; recollection, verified by the A5/C3 alias checks].

### 4.3 Environment file (names only; values are supplied on the VPS)

Variables marked **req** make Compose refuse to render when missing [repo: `docker-compose.central.yml`].

| Variable | Value / rule | Secret |
|---|---|---|
| `PM_CENTRAL_DATA_HOST_PATH` (req) | `/docker/sales-pipeline/data` | no |
| `PM_CENTRAL_DATA_CONTAINER_PATH` (req) | `/data` | no |
| `PM_IDENTITY_DATABASE_URL` (req) | `sqlite:////data/identity.db` | no |
| `PM_COMPANY_DB_SALCOMP` / `_LITY` / `_VALOR_LOG` (req) | `sqlite:////data/salcomp.db`, `lity.db`, `valor-log.db` | no |
| `SECRET_KEY` (req) | generated on the VPS, at least 32 characters, never the Mac's | **yes** |
| `PM_PUBLIC_URL` (req) | `https://<hostname>` (bare origin; becomes `APP_BASE_URL`) | no |
| `SMTP_HOST` (req), `SMTP_FROM` (req) | unresolved inputs (P2) | no |
| `SMTP_PORT` | `587` unless the provider says otherwise (STARTTLS only) | no |
| `SMTP_USER`, `SMTP_PASSWORD` | only if the provider requires authentication; a user without a password stops the app | **password: yes** |
| `PM_BEHIND_PROXY` | `true` | no |
| `RATE_LIMIT_TRUSTED_PROXIES` | NPM's address as the app sees it (A5); required when `PM_BEHIND_PROXY=true` | no |
| `CORS_ALLOWED_ORIGINS` | empty (same origin) | no |
| `HSTS_ENABLED` | `true`, with NPM's own HSTS toggle left **off** (exactly one `Strict-Transport-Security` header; the app's header has no `preload` and no `includeSubDomains`, a domain-wide decision) | no |
| `OPENROUTER_API_KEY` | **not set** at launch | yes (later) |
| Must not be set | `DATABASE_URL`, `PM_ENV` (the file sets it), `PM_E2E`, `PM_SETUP_TOKEN`, `PM_ALLOW_CREATE_DB`, `PM_REQUIRE_EXISTING_DB` | |

Configuration that must come from production-only files rather than the repository: the env file, the override file, the NPM proxy host, and the DNS record. The repository ships no production values.

---

## 5. Prerequisites and unresolved inputs

Treated as explicit gates, not assumptions. Owner = who must act. "Needed before" names the first step that cannot run without it.

### 5.1 Table

| ID | Prerequisite | Owner | Needed before | Status |
|---|---|---|---|---|
| P1 | **Production DNS:** CNAME `pipeline` > `automacao.pluisada.com.br`, **DNS-only (grey cloud)**, in the Cloudflare zone `pluisada.com.br`. Absent when checked [v1]. **Created at E4, not earlier** (no technical dependency requires it sooner: the rehearsal has its own hostname, and Let's Encrypt validation for production happens at E10, after E4). Allow for DNS propagation and negative caching of the earlier "no record" answers before E10 | Paulo (zone owner) | **E4** | Open |
| P2 | Production SMTP: provider host, port, authentication user/password if required, **sender address** and permission to send as it (5.2) | Paulo | A7 (reachability), then D (D2a(i), B4) and E | Open; nothing may be invented |
| P3 | **Backup destination** for the off-host copy, its transfer mechanism, **encryption tool and key custody**, and the off-host retention it can enforce | Paulo | **D8** (the rehearsal must exercise the whole chain) and E8 | Open |
| P4 | VPS preflight results (A3 to A7), including **Compose at or above the minimum (4.2)** | Operator | Everything after A | Open |
| P5 | Alert channel and external uptime monitor for Phase F (5.3) | Paulo | E15 | Open |
| P6 | **Rehearsal hostname** (e.g. `pipeline-rehearsal.pluisada.com.br`, a proposal) with its DNS record **created and resolving before Phase D starts**; its TLS certificate is issued in D4 and must succeed before any other rehearsal check | Paulo | **Phase D entry** (A8a, G-A) | Open |
| P7 | How the VPS gets the source: a public clone needs no credentials; a private repository needs a read-only deploy key. A1/A2 reveal which | Paulo | A11 | Open |
| P8 | Functional test evidence for the verified SHA (A9) | Operator | G-A.1 | Open |
| P9 | **Dependency-audit evidence** for the verified SHA, with every finding dispositioned (A10, 5.4) | Operator, Paulo | G-A.2 | Open |
| P10 | Written owner authorization for each phase (section 13) | Paulo | Every phase | Open |

### 5.2 Minimum SMTP configuration (derived from `mailer.py` and `production_config.smtp_problems`)

1. `SMTP_HOST` set (production refuses to start without it).
2. A sender: `SMTP_FROM`, or `SMTP_USER` as the sender; it must parse as a single valid address.
3. `SMTP_PORT` an integer 1 to 65535, default `587`. The mailer **only** speaks plain SMTP followed by **STARTTLS with full certificate and hostname verification** (`ssl.create_default_context()`); implicit TLS (port 465) and plaintext fallback are not supported, and the server certificate must be valid for the name in `SMTP_HOST`.
4. `SMTP_USER` and `SMTP_PASSWORD` if the server requires login; setting the user without the password stops the app. A relay that needs no login is allowed.
5. Outside the code but required: outbound connections from the VPS to that host and port are allowed (A7); the provider accepts mail from that sender (for Gmail-type providers `SMTP_FROM` must be the authenticated account or a verified alias [repo: `docs/DEPLOYMENT.md`]); if the sender is on a domain the owner controls, SPF/DKIM/DMARC for that domain must permit the provider (otherwise the activation mail may land in spam; owner to confirm).
6. Timeout is 10 seconds per message; failures are logged by exception class only (`Email delivery failed ... error=<type>`); success is logged as `Email sent: to=a***@domain` and means only that the SMTP server accepted the message.

Because no repository command sets a password, **the SUPERADMIN cannot be activated without this**. If SMTP cannot be made to work, the only options are the owner's decisions (fix or replace the provider), not a shortcut in this plan.

### 5.3 Monitoring inputs (P5)

An alert channel is not documented. Options consistent with what exists: an external uptime service for the HTTPS check (independent of the host); email through the same SMTP; or an n8n workflow messaging the existing Telegram bot (shares fate with the host). The owner chooses; Phase F lists what must be monitored regardless.

### 5.4 Dependency-audit policy (P9; used by A10)

Separate from functional tests: a green test run says nothing about known vulnerabilities, and a clean audit says nothing about behaviour. They have separate records and separate gates.

- **Recording.** Each finding gets a row in the audit-findings table of the record (section 9.4): tool, package, installed version, advisory ID, severity and its source, **where it lives** (runtime Python dependency in the image / JavaScript shipped to browsers / build or dev tool only), fix available (version), decision, approver, date, review-by date.
- **Severity.** `npm audit` reports a severity. `pip-audit` does not: look up each advisory ID (PYSEC/GHSA/CVE) in the advisory database, record the severity and where it came from, and treat an advisory whose severity cannot be established as **High** until it is.
- **Decisions allowed:** *fix before deploying* (any repository change creates a **new SHA**, which restarts the plan from A1; nothing already tested carries over); *accepted exception*; *not applicable* (with the reason). An accepted exception needs the owner's written approval, the reason it is acceptable, the compensating control, and a **review-by date no later than 30 days** after the deployment.
- **Thresholds.** A Critical or High finding in the runtime image or in the JavaScript shipped to browsers **blocks G-A.2** unless fixed or covered by an accepted exception. A Critical or High finding only in build/dev tooling not present in the image needs a recorded decision (it does not block by itself). Moderate and Low are recorded and scheduled for the next upgrade.
- **What is never a pass.** A nonzero exit code is not a pass and is not an automatic fail: it is a result to be read and recorded. The following are **"audit incomplete"** and block G-A.2 until a rerun succeeds: the tool could not run, a network or registry error, unparseable output, skipped or unresolved dependencies, or an exit code not explained by the findings table. An empty findings table is accepted only together with a zero exit code **and** the tool versions, commands and dependency counts in the record.
- **Not covered (stated, not hidden):** operating-system packages in the base images (no image scanner is introduced by this plan; the base-image digests are recorded so they can be checked later), and vulnerabilities newer than the advisory databases on the day of the audit. The audit is repeated at every upgrade (F3).

---

## 6. Conventions for every command in this plan

- **Where:** `[Mac]`, `[VPS]` (SSH key, as root) or `[UI]`. **R** = read-only, **C** = changes something. Every step is also a stop point: on an unexpected result, stop and report; do not improvise.
- **Placeholders** in angle brackets are not facts. `<SHA>` is the verified release commit; `<ts>` a UTC timestamp. Every command block is a **template** until the rehearsal proves it (section 9 records which).
- **Scope every Docker command** to the project (`-p "$PM_PROJECT"`) through the helper below. **Forbidden anywhere in this plan:** `docker system prune`, `docker volume prune`, `docker network prune`, `docker image prune`, `docker builder prune`, `docker compose down -v` / `--volumes`, `docker rm`/`docker rmi` of anything not created by this plan, `docker stop $(docker ps -q)`, any command touching the Swarm stacks, NPM's container, or other `/docker/<project>` directories except the NPM data **copy** in E3, and **anything that publishes a port on the host**: `--service-ports`, `-p`/`--publish`, `PM_BIND_ADDRESS`, a `ports:` entry in any file, `docker run -p`.
- **Shell helpers** (define in each SSH session; the rehearsal sets its own four values):

  ```bash
  set +o history                                    # keep this session out of shell history
  export PM_DIR=/docker/sales-pipeline              # rehearsal: /docker/sales-pipeline-rehearsal
  export PM_PROJECT=pm-central                      # rehearsal: pm-rehearsal
  export PM_HOST=pipeline.pluisada.com.br           # rehearsal: the P6 hostname
  export PM_SRC=/docker/sales-pipeline/src          # the one verified checkout, shared read-only by both environments
  export PM_IMAGE=sales-pipeline:<SHA>
  export PM_CTR="${PM_PROJECT}-app-1"
  pmc() { docker compose --env-file "$PM_DIR/pm-central.env" -p "$PM_PROJECT" \
            -f "$PM_SRC/docker-compose.central.yml" -f "$PM_DIR/docker-compose.vps.yml" "$@"; }
  ```
  Throughout the runbook `pmc` means this helper for whichever environment is being worked on (the earlier `pmr` for the rehearsal is just `pmc` with the rehearsal values).

  **Cross-environment guard.** A shell loaded with production values would make a rehearsal command act on production. Every disruptive command in Phase D (T2, D7, D9, cleanup) therefore starts with this guard and names the rehearsal container by its literal name, never through `$PM_CTR`:

  ```bash
  pm_expect() { [ "$PM_PROJECT" = "$1" ] && [ "$PM_DIR" = "$2" ] || { echo "REFUSED: this shell is set for $PM_PROJECT ($PM_DIR), not $1 ($2)" >&2; return 1; }; }
  # Phase D:  pm_expect pm-rehearsal /docker/sales-pipeline-rehearsal && ...
  # Phase E:  pm_expect pm-central   /docker/sales-pipeline            && ...   (first command of each production step that changes anything)
  ```
- **Secrets:** never typed on a command line, echoed, pasted into chat, written into this repository, the record or logs. Values enter the env file by `read -rs` or an editor (B1). Env-file values that contain `$`, `#` or spaces must be single-quoted. `docker compose config` **prints resolved values including secrets**: B1 pipes it into a checker that prints only PASS/FAIL names and removes the temporary file; never paste its output.
- **Never run** `docker inspect` without `--format` on the app container (it prints the environment).
- **Harmless markers only:** the token-logging tests use made-up marker strings. A real invitation or reset token must never be requested with `curl`, typed into a test or searched for in logs.

---

## 7. Runbook

### Phase A. Preflight

All steps are read-only except the local test and audit runs (scratch directory), the VPS clone and the image build (A11). The owner's authorization of Phase A covers exactly these.

| Step | Where | R/C | Action | Expected / stop |
|---|---|---|---|---|
| A1 | Mac | R | `git ls-remote https://github.com/Pluisada/project-pm.git refs/heads/main` | Prints the release SHA. Record it as `<SHA>`. Expected `fdfe3b5b7f58d167a7cd25de17f1d12ec2105052`. **If different: stop; the owner decides.** |
| A2 | Mac | R | Fresh clone into an **empty new directory** (not the dev folder): `git clone https://github.com/Pluisada/project-pm.git <scratch>/verify && git -C <scratch>/verify checkout --detach <SHA>`, then `git -C <scratch>/verify status --short`. Create `<scratch>/records/<SHA>/` for evidence files | Empty status. Reveals whether the repository is reachable without credentials (P7) |
| A3 | VPS | R | `cat /etc/os-release`, `uname -m`, `nproc`, `free -h`, `df -h /docker`, `docker version`, `docker compose version`, `command -v git sqlite3 python3 flock` | Ubuntu 24.04; architecture recorded; at least 4 GB RAM available for the build and 6 GB free disk; Docker 29.x; `git`, `python3`, `flock` present. **`docker compose version` reports v2.24.4 or higher (4.2); if not: STOP.** No loopback or other port-publishing fallback exists; the owner must authorize a separate Compose-upgrade plan. Record all values (P4) |
| A4 | VPS | R | Take the inventory: `docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}'`, `docker network ls`, `docker volume ls`, `ss -ltn`, `ls -la /docker`; save to `/root/preflight-<ts>.txt` (names only; no `inspect`) | This is the **before** snapshot. Stop if `/docker/sales-pipeline*` already exists: treat it as an existing installation and write a separate plan |
| A5 | VPS | R | `docker network inspect nginx-proxy_default --format '{{range .Containers}}{{.Name}} {{.IPv4Address}}{{"\n"}}{{end}}'` and `docker network inspect nginx-proxy_default --format '{{(index .IPAM.Config 0).Subnet}}'` | The network exists; `nginx-proxy-npm-1` and `simone-site` are listed. Record the **exact network name**, NPM's IPv4 address (the value for `RATE_LIMIT_TRUSTED_PROXIES`, as /32) and the subnet. Different name: use the real one in the override. NPM on another network: stop and re-plan |
| A6 | VPS | R | `ss -ltn` against the plan: no new listener will be created. Confirm the Hostinger panel firewall still lists only 22/80/443 | Matches the documented set |
| A7 | VPS | R | Once P2 is known: `timeout 10 bash -c '</dev/tcp/<SMTP_HOST>/<SMTP_PORT>' && echo open` | `open`. If blocked, SMTP is not possible from this host: stop (P2 fails) |
| A8a | Mac | R | **Rehearsal hostname:** `dig +short <rehearsal hostname>` and `dig +short CAA pluisada.com.br` | The rehearsal hostname resolves via `automacao.pluisada.com.br` to `187.77.236.218`. **If empty, P6 is open: the owner creates the record before Phase D.** A CAA record that does not permit `letsencrypt.org` must be changed first. This is the only DNS record Phase D needs |
| A8b | Mac | R | **Production hostname:** `dig +short pipeline.pluisada.com.br` | Informational. Expected empty (absent at version 1). **No production DNS change is made or required before E4**; record the state |
| A9 | Mac | C (scratch) | **Functional evidence (G-A.1).** In the clone, on **Python 3.11**: `cd backend && uv sync --frozen --extra dev`; then `cd ../frontend && npm ci && npm run build && cp -R out ../backend/static`; `cd ../backend && uv run pytest -q \| tee <scratch>/records/<SHA>/pytest-full.txt`; the focused files of 3.3 as their own run (`uv run pytest -q <files> \| tee .../pytest-focused.txt`); `cd ../frontend && npm run lint && npx tsc --noEmit && npm run test:unit && npm run test:e2e:central` (log each) | Record **separately**: full backend totals (passed/skipped/failed, Python and uv versions), focused totals, frontend checks, central E2E totals, all against `<SHA>`. A run counts as a **full-suite pass only if the whole backend run completed with 0 failures**; a focused run never counts as one. Any failure: stop and report |
| A10 | Mac | C (scratch) | **Dependency-audit evidence (G-A.2), separate from A9.** Commands and prerequisites below | Findings recorded and dispositioned per 5.4. **Not a pass on exit code alone** |
| A11 | VPS | C | Source and image: `install -d -m 750 "$PM_DIR"`; `git clone https://github.com/Pluisada/project-pm.git "$PM_SRC"`; `git -C "$PM_SRC" checkout --detach <SHA>`; `git -C "$PM_SRC" status --short` (empty); `git -C "$PM_SRC" rev-parse HEAD` (equals `<SHA>`); `test ! -e "$PM_SRC/backend/branded_email.py"`; `docker build -t sales-pipeline:<SHA> "$PM_SRC"`. Then image checks: `docker image inspect sales-pipeline:<SHA> --format '{{.Id}} {{.Architecture}}'`; `docker run --rm --entrypoint id sales-pipeline:<SHA>` (uid 10001); `docker run --rm --entrypoint sh sales-pipeline:<SHA> -c 'ls -A /app \| grep -E "^\.env\|\.db$" \|\| echo "no env or db files in the image"'` | Build succeeds. Watch `docker stats --no-stream` and `free -h`; if memory is exhausted or the architecture differs, the fallback is a cross-platform build on the Mac (`docker buildx build --platform linux/<arch>`) and `docker save \| ssh ... docker load`, after the owner approves. Record the image ID, architecture, build date and the base-image digests (`docker image inspect python:3.11-slim node:24-alpine --format '{{.RepoDigests}}'`) |
| A12 | VPS | R | Release contents: `git -C "$PM_SRC" ls-tree -r --name-only HEAD \| grep -E '^(Dockerfile\|docker-compose.central.yml\|backend/(create_identity_db\|identity_admin_cli\|create_company_db\|diagnose_company_schema\|diagnose_identity_limits)\.py)$'` and `git -C "$PM_SRC" log --oneline -6` | All six paths present; the top commit is `fdfe3b5 feat: enforce admin limits and independent card review` |

*(The step that compared the required environment variables with the Compose file, numbered A12 in version 2, is now part of **B1 step 4**: it needs the env file, which only exists from Phase B.)*

**A10 commands (templates, untested in this repository; run on the Mac in the clean clone from A2).** Prerequisites: network access to the npm registry and to PyPI/OSV; Node 24 and its npm (the Dockerfile builds on Node 24); `uv` at the version the Dockerfile pins (0.12.15) or newer; `python3`; a clean clone with `git status --short` empty before and after (so the audit proves nothing was changed). `REC=<scratch>/records/<SHA>`.

```bash
# --- JavaScript (frontend) ---
cd <scratch>/verify/frontend
node --version; npm --version                         # record both
npm ci                                                # installs exactly package-lock.json
npm audit --omit=dev --json > "$REC/npm-audit-prod.json"; echo "npm audit (production deps) exit=$?"
npm audit --json            > "$REC/npm-audit-all.json";  echo "npm audit (all deps) exit=$?"
npm ls --omit=dev --all --json > "$REC/npm-ls-prod.json" 2>/dev/null   # dependency inventory for the record

# --- Python (backend runtime dependencies, from the lock the image installs) ---
cd ../backend
uv --version                                          # record
uv export --frozen --no-dev --no-emit-project -o "$REC/requirements-runtime.txt"    # pinned, with hashes, from uv.lock
uvx pip-audit --version                               # record this exact version and pin it in reruns: uvx pip-audit==<version>
uvx pip-audit -r "$REC/requirements-runtime.txt" --require-hashes --disable-pip --strict \
      -f json -o "$REC/pip-audit.json"; echo "pip-audit exit=$?"

# --- summary for the record (counts and ids only) ---
python3 - "$REC" <<'PY'
import json, sys
rec = sys.argv[1]
for name in ("npm-audit-prod.json", "npm-audit-all.json"):
    d = json.load(open(f"{rec}/{name}"))
    print(name, d.get("metadata", {}).get("vulnerabilities"))
    for pkg, v in sorted(d.get("vulnerabilities", {}).items()):
        print("  ", pkg, v.get("severity"), "direct" if v.get("isDirect") else "transitive", "fix:", v.get("fixAvailable"))
d = json.load(open(f"{rec}/pip-audit.json"))
deps = d.get("dependencies", d) if isinstance(d, dict) else d
print("pip-audit packages:", len(deps))
for dep in deps:
    for v in dep.get("vulns", []):
        print("  ", dep["name"], dep["version"], v["id"], v.get("aliases"), "fix:", v.get("fix_versions"))
PY
git -C <scratch>/verify status --short                # must still be empty
```

How these relate to the policy (5.4): `npm audit` exits nonzero when it finds advisories (and also on registry errors), and `pip-audit` exits `1` when it finds vulnerabilities [docs]; both exit codes are recorded next to their JSON and read, never used as the verdict. `--strict` makes a dependency that cannot be collected an error rather than a silent skip [docs], and `--disable-pip` with hashes avoids resolving anything new [docs]. Known limits of this template: `uv export` writes environment markers, so the audit host may evaluate some Linux-only packages differently from the Linux image (compare the package count with `uv.lock`, and treat any shortfall as "incomplete"); `pip-audit` gives no severity (5.4); if either tool needs a changed flag on first use, the change is recorded as a deviation, not hidden.

**Gate G-A (owner authorizes Phase D).** Two independent parts plus the environment checks:
- **G-A.1 functional:** A9 recorded for `<SHA>`; full backend run with 0 failures; focused run, frontend checks and central E2E recorded separately.
- **G-A.2 audit:** A10 complete (no "audit incomplete"); every finding dispositioned under 5.4; Critical/High runtime findings fixed or covered by a signed exception.
- **Environment:** A1 to A8a and A11 to A12 recorded; **A3 passed including the Compose minimum**; P2, P3, P4, P6 (rehearsal DNS resolving), P7 resolved. A8b is informational. P1 and P5 are **not** required for Phase D.

### Phase B. Procedures for the env file, databases, SMTP and activation (used by Phase D and Phase E)

Parameters per environment:

| Parameter | Rehearsal (D) | Production (E) |
|---|---|---|
| `PM_DIR` / `PM_PROJECT` | `/docker/sales-pipeline-rehearsal` / `pm-rehearsal` | `/docker/sales-pipeline` / `pm-central` |
| `PM_HOST` / `PM_PUBLIC_URL` | the P6 hostname | `pipeline.pluisada.com.br` |
| NPM alias | `sales-pipeline-rehearsal` | `sales-pipeline` |
| `SECRET_KEY` | new random value, different from production | new random value |
| SMTP | `SMTP_HOST=smtp.invalid` by default (D2); real SMTP only under D2a(i) | the real, verified settings (P2) |
| Data | disposable | real, fresh |
| Image | `sales-pipeline:<SHA>` | the **same image ID** as the signed rehearsal record |

**B1. Directories, env file, override, configuration checks (C, VPS).**

*Step 1: directories* (permissions as in 4.1):

```bash
install -d -m 700 -o 10001 -g 10001 "$PM_DIR/data" "$PM_DIR/backups" "$PM_DIR/restore-test"
install -d -m 750 "$PM_DIR/records"
```

*Step 2: env file* without secrets in history or on command lines (the heredoc is unquoted on purpose so the four variables expand; it contains no secret):

```bash
install -m 600 /dev/null "$PM_DIR/pm-central.env"
cat >> "$PM_DIR/pm-central.env" <<EOF
PM_CENTRAL_DATA_HOST_PATH=$PM_DIR/data
PM_CENTRAL_DATA_CONTAINER_PATH=/data
PM_IDENTITY_DATABASE_URL=sqlite:////data/identity.db
PM_COMPANY_DB_SALCOMP=sqlite:////data/salcomp.db
PM_COMPANY_DB_LITY=sqlite:////data/lity.db
PM_COMPANY_DB_VALOR_LOG=sqlite:////data/valor-log.db
PM_PUBLIC_URL=https://$PM_HOST
PM_BEHIND_PROXY=true
RATE_LIMIT_TRUSTED_PROXIES=<NPM address from A5>/32
HSTS_ENABLED=true
SMTP_PORT=<from P2>
EOF
printf 'SECRET_KEY=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" >> "$PM_DIR/pm-central.env"
```

Then the SMTP values, entered without echo (they never appear in the command text), one line per value:

```bash
read -rp 'SMTP host: ' V && printf "SMTP_HOST='%s'\n" "$V" >> "$PM_DIR/pm-central.env"
read -rp 'SMTP sender (From): ' V && printf "SMTP_FROM='%s'\n" "$V" >> "$PM_DIR/pm-central.env"
read -rp 'SMTP user (empty if none): ' V && [ -n "$V" ] && printf "SMTP_USER='%s'\n" "$V" >> "$PM_DIR/pm-central.env"
read -rsp 'SMTP password (empty if none): ' V && [ -n "$V" ] && printf "SMTP_PASSWORD='%s'\n" "$V" >> "$PM_DIR/pm-central.env"; unset V
```

(A value containing a single quote, or credentials delivered another way, goes in with `vi`/`nano` instead.)

*Step 3: the override file* from 4.2, with this environment's alias and `<SHA>`; `chmod 640`.

*Step 4: required variables, names only* (this is the check that was A12 in version 2):

```bash
for v in PM_CENTRAL_DATA_HOST_PATH PM_CENTRAL_DATA_CONTAINER_PATH PM_IDENTITY_DATABASE_URL PM_COMPANY_DB_SALCOMP \
         PM_COMPANY_DB_LITY PM_COMPANY_DB_VALOR_LOG SECRET_KEY PM_PUBLIC_URL SMTP_HOST SMTP_FROM PM_BEHIND_PROXY RATE_LIMIT_TRUSTED_PROXIES; do
  grep -q "^$v=" "$PM_DIR/pm-central.env" && echo "$v: set" || echo "$v: MISSING"; done
grep -E '^(DATABASE_URL|PM_ENV|PM_E2E|PM_SETUP_TOKEN|PM_ALLOW_CREATE_DB|PM_REQUIRE_EXISTING_DB|OPENROUTER_API_KEY)=' "$PM_DIR/pm-central.env" && echo "FORBIDDEN VARIABLE PRESENT" || echo "no forbidden variables"
stat -c '%a %U:%G %n' "$PM_DIR/pm-central.env"        # expect: 600 root:root
```

*Step 5: rendered-configuration assertions, printing only PASS/FAIL names* (the JSON contains secrets, so it goes to a root-only temporary file that is removed immediately):

```bash
CFG=$(mktemp /root/pm-config.XXXXXX)
pmc config --format json > "$CFG"
python3 - "$CFG" "$PM_IMAGE" <<'PY'
import json, sys
svc = json.load(open(sys.argv[1]))["services"]["app"]
env = svc.get("environment", {})
nets = svc.get("networks") or {}
checks = {
  "PM_ENV is production":            env.get("PM_ENV") == "production",
  "PM_AUTH_MODE is central":         env.get("PM_AUTH_MODE") == "central",
  "no DATABASE_URL":                 "DATABASE_URL" not in env,
  "NO PUBLISHED PORTS":              not svc.get("ports"),
  "exactly one bind mount":          len(svc.get("volumes", [])) == 1 and svc["volumes"][0].get("type") == "bind",
  "proxy network with an alias":     "proxy" in nets and bool((nets["proxy"] or {}).get("aliases")),
  "AI key is empty":                 not env.get("OPENROUTER_API_KEY"),
  "image is the verified tag":       svc.get("image") == sys.argv[2],
}
for name, ok in checks.items():
    print(("PASS " if ok else "FAIL ") + name)
sys.exit(0 if all(checks.values()) else 1)
PY
rc=$?; shred -u "$CFG" 2>/dev/null || rm -f "$CFG"; echo "config assertions exit=$rc"
```

Expected: all PASS and exit 0. **`NO PUBLISHED PORTS` failing means the Compose version cannot do the reset: stop (4.2).** Compose also names any missing required variable and stops. The JSON field names are the standard `config --format json` layout [inferred]; if the first run shows a different layout, adjust the checker and record the change.

**B2. Create the fresh databases (C, VPS, app not yet started).** From `docs/DEPLOYMENT.md` ("Initial production installation"); running them through `compose run` is **untried** [repo], so Phase D is its first run. One-off containers publish nothing.

```bash
pmc run --rm --no-deps -T app python create_identity_db.py --database /data/identity.db
pmc run --rm --no-deps -T app python create_company_db.py --company salcomp
pmc run --rm --no-deps -T app python create_company_db.py --company lity
pmc run --rm --no-deps -T app python create_company_db.py --company valor-log
```

Expected: each succeeds; four files under `$PM_DIR/data`, owner 10001. Each command refuses to overwrite an existing file and removes its own file on failure. If one fails: read the message, fix the cause (ownership of `data/` is the likely one on Linux), delete only the partially created file the message names, rerun. Do not proceed with a partial set.

**B3. Bootstrap exactly one SUPERADMIN (C, VPS).**

```bash
pmc run --rm --no-deps -T app python identity_admin_cli.py bootstrap --admin-email pluisada@gmail.com
```

Expected: a dictionary listing the three companies created and `admin_created: True`. This registers `salcomp`, `lity`, `valor-log` and creates the single global administrator with **no password**, marked email-verified by the operator's attestation, as the code states. It creates no memberships and no Admins. It is idempotent for the same address; a different address is refused (`grant_global_admin` enforces one global administrator). Then the read-only checks:

```bash
pmc run --rm --no-deps -T app python diagnose_company_schema.py --company salcomp    # and lity, valor-log: exit 0, "Schema complete."
pmc run --rm --no-deps -T app python diagnose_identity_limits.py                      # exit 0, "Within limits."
pmc run --rm --no-deps -T app python -c "import sqlite3; c=sqlite3.connect('file:/data/identity.db?mode=ro',uri=True); print('integrity',c.execute('pragma integrity_check').fetchone()[0]); print('users',c.execute('select count(*) from identity_users').fetchone()[0]); print('active global admins',c.execute('select count(*) from identity_users where is_global_admin=1 and is_active=1').fetchone()[0]); print('with password',c.execute('select count(*) from identity_users where password_hash is not null').fetchone()[0]); print('memberships',c.execute('select count(*) from identity_memberships').fetchone()[0])"
```

Expected: `integrity ok`, `users 1`, `active global admins 1`, `with password 0`, `memberships 0`. (`diagnose_identity_limits.py` alone cannot prove "exactly one": it only reports when there is more than one.) Counts only; no addresses are printed.

**B4. Verify SMTP before any activation (C: sends one email; the owner names the recipient).** A plain test with no link and no token, the method the repository used on 2026-10-16:

```bash
pmc run --rm --no-deps -T app python -c "import mailer; mailer.send_email('pluisada@gmail.com', 'Sales Pipeline SMTP test', 'SMTP check. No link. Safe to delete.')"
```

Expected: no traceback. Report five separate levels: (1) the request was made; (2) SMTP submission attempted; (3) the server accepted it (the process exited without `Email delivery failed`; check the logs); (4) provider delivery status: normally unavailable; (5) the **owner confirms the message arrived** (check spam). Do not claim delivery without (5). A traceback means `EmailDeliveryError`; the log carries only the exception class (`gaierror` DNS, `SMTPAuthenticationError` credentials, `SSLCertVerificationError` certificate/hostname, `TimeoutError`/`ConnectionRefusedError` network). Fix the settings; never weaken verification. **Do not run B5 until (5) is confirmed.**

**B5. Activation (C, VPS and browser).** Preconditions, all verified: DNS resolves, NPM serves HTTPS with a valid certificate, the app is up and healthy, the token-logging results are recorded and accepted (T1 in this environment; T2 and T3 from the rehearsal under the E11 equivalence conditions), B4 level (5) confirmed. Then:

```bash
pmc exec -T app python identity_admin_cli.py send-activation --email pluisada@gmail.com
```

The CLI prints `sent` (meaning only that the mailer returned) or `not eligible (nothing sent)`; the link is never printed. The owner opens the emailed link **from the real mailbox** (`https://<hostname>/?central_reset_token=...`, valid one hour, single use; the page removes the token from the address bar), chooses a password (8 to 128 characters with upper, lower, number and symbol; unique, not the development password) and signs in. If the link expired, run the command again (each run invalidates earlier links; the "Forgot my password?" route is limited to 3 emails per account per hour). If the mail does not arrive: check `pmc logs --tail 100 app` for `Email delivery failed` / `Email sent`, spam, and the link host. If SMTP cannot be fixed, stop: the owner decides.

**B6. Confirm the SUPERADMIN role (R).** (a) In the UI the signed-in account shows the Super Admin role and the company selector lists Salcomp, Lity and Valor Log; (b) rerun the count query from B3: `active global admins 1`, `with password 1`, `users 1`, `memberships 0`; (c) `diagnose_identity_limits.py` exits 0. `GET /api/central/me` returns `is_global_admin: true` but needs the bearer token: use the browser, not `curl`.

### Phase C. Network, HTTPS and logging

| Step | Where | R/C | Action | Expected / stop |
|---|---|---|---|---|
| C1 | VPS | R | After `pmc up -d`: `docker inspect -f '{{.State.Health.Status}}' "$PM_CTR"` and `pmc exec -T app python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read())"` | `healthy`; body `{"status":"ok","test_mode":false}`. Inside-the-container check, so no host port is needed |
| C2 | Mac | R | `dig +short <hostname>` (rehearsal hostname in Phase D, production hostname in Phase E) | CNAME then `187.77.236.218`; for production this follows E4 |
| C3 | VPS | R | From NPM's side: `docker exec nginx-proxy-npm-1 sh -c 'wget -qO- http://<alias>:8000/api/health \|\| curl -s http://<alias>:8000/api/health'` | Same JSON (this also proves the alias resolves). If neither tool exists in the NPM image, rely on C6 |
| C4 | UI | C (NPM) | Open the tunnel `ssh -L 8181:localhost:81 root@<IP>`, then `http://localhost:8181`. **First test the access in a second session before closing anything.** Add a Proxy Host: domain = this environment's hostname; scheme `http`; forward host = the alias; port `8000`; Websockets **off**; Block Common Exploits on; SSL tab: request a new Let's Encrypt certificate (agreeing to the terms is an account-level consent for the owner to give), **Force SSL on, HTTP/2 on, HSTS off**. Advanced tab: the block in C5. No Access List | The host saves; certificate issued (needs the DNS record and port 80 reachable). Stop on a certificate error and read NPM's message; do not retry in a loop (Let's Encrypt rate limits). Record issuer and expiry |
| C5 | UI | C (NPM) | Advanced tab content, exactly: <br>`access_log off;` <br>`client_max_body_size 11m;` | See the analysis below; effect proven only by C10 |
| C6 | Mac | R | `curl -sI http://<hostname>/` (redirect to https); `curl -sI https://<hostname>/` (200, valid certificate, CSP, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and **exactly one** `Strict-Transport-Security`: `curl -sI ... \| grep -ci strict-transport` prints 1); `curl -sI -H 'Origin: https://other.example' https://<hostname>/api/health` (no `access-control-allow-origin`); `curl -s https://<hostname>/api/mode` returns `{"auth":"central"}`; `curl -s https://<hostname>/api/health` shows `"test_mode":false`; `curl -s -o /dev/null -w '%{http_code}\n' https://<hostname>/openapi.json` and `.../docs` must not return the API documentation (production disables them) | All as stated. Zero or two HSTS headers: fix `HSTS_ENABLED` / NPM toggle |
| C7 | VPS / Mac | R | Peer address and rate-limit trust: make one request through HTTPS and read the peer in `pmc logs --tail 20 app`; it must equal `RATE_LIMIT_TRUSTED_PROXIES`. From the Mac send `curl -s -H 'X-Forwarded-For: 1.2.3.4' https://<hostname>/api/health` several times and confirm the application records the Mac's address, not `1.2.3.4` (NPM appends via `$proxy_add_x_forwarded_for` [upstream]) | Matches. If the peer differs, correct the env value and `pmc up -d` |
| C8 | Mac / VPS | R | **No direct exposure:** (a) from the Mac `curl -m 8 -sS http://187.77.236.218:8000/api/health` must fail (timeout or refused); (b) on the VPS `ss -ltn \| grep -E ':8000\b'` prints nothing; (c) `docker port "$PM_CTR"` prints nothing and `docker inspect -f '{{json .NetworkSettings.Ports}}' "$PM_CTR"` shows no host binding; (d) `ss -ltn` equals the A4 snapshot | All four hold. The health endpoint is reachable **only** through HTTPS at the hostname (status only; intended, and what an external monitor calls) and inside Docker |
| C9 | VPS | R | Regression of shared services: `docker ps` compared with the A4 snapshot; a message to `@paulo_saude_bot` (NPM is a dependency of the health agent); loading `workflow.`, `webhookworkflow.`, `evolution.`, `painel.` and `fono-simonetavolaro.com` | Unchanged and working |
| C10 | Mac / VPS | R + harmless requests (T2 stops the rehearsal container only) | **Token-logging tests T1 to T4** (below) | Results classified as in the table below; record every one |

**Do the NPM access logs capture tokens? Yes by default, according to the NPM source** [upstream: `docker/rootfs/etc/nginx/conf.d/include/log-proxy.conf`, `backend/templates/proxy_host.conf`, `develop` branch]:
- Each proxy host writes `access_log /data/logs/proxy-host-<id>_access.log proxy;` and `error_log /data/logs/proxy-host-<id>_error.log warn;` at server level.
- Both access formats (`proxy`, `standard`) contain `"$request_uri"`, the original URI **including the query string**. A link `/?central_reset_token=<token>` therefore lands in the access log in clear.
- The `Advanced` field is inserted at server level after those directives, so `access_log off;` there cancels every `access_log` directive at that level (nginx: the `off` value cancels all `access_log` directives on the current level [docs]).
- **Error log (not fixed by `access_log off;`):** when nginx fails to reach the upstream it logs the client request line and the upstream URL at `error` level, which the `warn` threshold includes [nginx behaviour, inferred]. That line would carry the query string. `error_log` directives are additive, so the Advanced tab cannot switch it off. T2 tests whether this happens on this NPM version.
- The installed NPM version is unverified (v2.15.1 observed in August; the `develop` source may differ). Only the tests count.
- Other places a query string could appear: the application's own uvicorn access log (redacted by `_RedactLinkTokens`, whose pattern also matches the `central_` token names), Docker's `json-file` log of the app and of NPM, and browser history (the page strips the token from the address bar immediately; `Referrer-Policy: no-referrer`).

**C10 token-logging tests.** **Synthetic markers only:** each marker is a fixed prefix (`MARKERBASE`, `MARKERHEALTHY`, `MARKERDOWN`) plus the epoch seconds, produced by the operator's shell. A marker is never read from a mailbox, a log, a database or a token table, and no real invitation, reset or activation link is ever requested, typed or searched for. Requesting the page with a fake `central_reset_token` creates, consumes and validates nothing on the server: the token is checked only when the reset form is submitted (`POST /api/password/reset` [repo]), which no test does, and `curl` does not run the page's JavaScript.

| Test | When | Procedure | Pass |
|---|---|---|---|
| **T3 baseline (rehearsal only, before C5)** | D5 only, with the Advanced block **absent**; never run against production | `M0=MARKERBASE$(date +%s)`; `curl -s -o /dev/null "https://<host>/?central_reset_token=$M0"`; then on the VPS `docker exec nginx-proxy-npm-1 sh -c "grep -rl '$M0' /data/logs /var/log 2>/dev/null"` | **Expected: found in the access log.** This is the positive control: it proves the leak exists on this NPM version and that the search can see it. If it is **not** found, the later "clean" results are not trustworthy: investigate before continuing |
| **T1 healthy-upstream request** | D5 (rehearsal) and E11 (production), with C5 in place | `M1=MARKERHEALTHY$(date +%s)`; `curl -s -o /dev/null -w '%{http_code}\n' "https://<host>/?central_reset_token=$M1"` (expect 200). VPS: (a) `docker exec nginx-proxy-npm-1 sh -c "grep -rl '$M1' /data/logs /var/log 2>/dev/null \|\| echo 'not in NPM log files'"`; (b) `docker logs --since 10m nginx-proxy-npm-1 2>&1 \| grep -c "$M1"`; (c) `docker logs --since 10m "$PM_CTR" 2>&1 \| grep -c "$M1"`; (d) positive control for the app log: `docker logs --since 10m "$PM_CTR" 2>&1 \| grep -c 'central_reset_token=\[redacted\]'` | (a) `not in NPM log files`; (b) `0`; (c) `0`; (d) `1` or more (the request reached the app and the redaction ran) |
| **T2 controlled upstream failure** | **D5 only, against the disposable rehearsal container. Never run in production and never against any other container or service** | Procedure in the block below the table | The failure response was produced (otherwise the test did not run); (a) and (b) classified below; the rehearsal container is healthy again |
| **T4 production check (E11)** | E11, before B5 | **T1 only**, against the production hostname, plus the **equivalence check** below. T2 is not repeated in production | T1 passes; the equivalence check holds; otherwise stop |

**T2 procedure (rehearsal only).** Nothing is stopped except the rehearsal application container. NPM itself is not stopped, reloaded or edited, no other proxy host is touched, and the request goes to the rehearsal hostname, so only the rehearsal proxy host sees an unreachable upstream.

```bash
t2_rehearsal() {
  pm_expect pm-rehearsal /docker/sales-pipeline-rehearsal || return 1        # refuse if this shell is set for production
  [ "$PM_HOST" != "pipeline.pluisada.com.br" ] || { echo "REFUSED: production hostname"; return 1; }
  [ "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' pm-rehearsal-app-1)" = "pm-rehearsal" ] \
    || { echo "REFUSED: not the rehearsal container"; return 1; }
  docker stop pm-rehearsal-app-1                                              # literal name, never $PM_CTR
  M2=MARKERDOWN$(date +%s)
  curl -s -o /dev/null -w '%{http_code}\n' "https://$PM_HOST/?central_reset_token=$M2"      # expect 502 or 503 from NPM
  docker exec nginx-proxy-npm-1 sh -c "grep -rl '$M2' /data/logs /var/log 2>/dev/null || echo 'not in NPM log files'"   # (a)
  docker logs --since 10m nginx-proxy-npm-1 2>&1 | grep -c "$M2"                                                       # (b)
  docker start pm-rehearsal-app-1                                             # restart the same container
  docker inspect -f '{{.State.Health.Status}}' pm-rehearsal-app-1             # repeat until healthy
  docker logs --since 10m pm-rehearsal-app-1 2>&1 | grep -c "$M2"            # (c) the app was down: 0 expected
}
t2_rehearsal
```

**Equivalence check (E11), because T2 is not run in production.** The production result for the NPM error log is **inherited from the rehearsal, not observed**, and is recorded that way. It is valid only if: (1) NPM's image ID is the same as in the rehearsal record (`docker inspect -f '{{.Image}}' nginx-proxy-npm-1`); (2) the production proxy host's Advanced block is byte-identical to the rehearsal's (compare the texts and record both hashes); (3) the rehearsal T2 and T3 are recorded and classified. If any of the three differs (for example NPM was upgraded or recreated in between), **stop**: T2 must be repeated, again only against a throwaway rehearsal container and proxy host, never on production, and the owner decides.

**Result classification (record one per log source):**

| Result | Classification | Consequence |
|---|---|---|
| Marker absent from a source **and** the positive control for that path succeeded | **Verified clean for that source on that date and NPM version** | Recorded as verified, scoped to the tested path only |
| T2 marker **present** in an NPM error log or container log | **Residual risk confirmed on this NPM version.** `access_log off;` cannot remove it. Record it as a residual risk and **do not** edit or delete the log. Mitigations: the file is root-only; keep NPM log rotation short; tokens are single-use, stored hashed and expire (reset one hour, invitation seven days); the activation link is requested only while the upstream is healthy and not during maintenance windows. The owner explicitly **accepts or rejects** the residual risk in the record before B5; rejection blocks activation until a different mitigation (for example a custom NPM nginx include, a separate decision) is planned | Not a verified-clean result and never reported as one |
| Marker absent but the positive control failed or was not run | **Inconclusive** | Not a pass; rerun |
| T1 marker present in the access log | **Failure of the mitigation** (the Advanced block is not in effect) | Stop; fix C5; rerun T1 |
| Marker present in the application or Docker log of the app | **Failure of the application's redaction** | Stop; report as a defect; do not proceed |
| NPM stores logs somewhere the search does not reach | **Unverified** | Stated as unverified; widen the search before accepting |
| Production NPM error-log behaviour | **Inherited from the rehearsal** (E11 equivalence check), never observed in production | Recorded as inherited, with the owner's written decision on any residual risk |

What these tests do **not** verify (always listed in the record): tokens passing through any system outside the host (browser history, mail providers, link scanners), NPM versions other than the one tested, log destinations not searched, upstream-failure behaviour in production (inherited, not observed), and real tokens (never used).

### Phase D. Disposable rehearsal (gate for Phase E)

**Purpose:** prove the whole chain on this host with the same image ID and a materially equivalent network setup, using throwaway data, before production is touched.

**Entry conditions (all must hold; G-A signed by the owner):** G-A.1 and G-A.2 recorded; **the rehearsal hostname resolves (A8a)**; A3 passed (Compose minimum); P2, P3, P4, P6, P7 resolved. The rehearsal's **TLS certificate is issued in D4 and is a pass criterion before D5 to D9 run**. No production DNS record is needed or created.

| Item | Rehearsal setup |
|---|---|
| D1 Isolation | Directory `/docker/sales-pipeline-rehearsal`, project `pm-rehearsal`, alias `sales-pipeline-rehearsal`, its own env file, data dir, records dir and `SECRET_KEY`; hostname per P6 with its own NPM proxy host and certificate. Image `sales-pipeline:<SHA>`: record its ID, the production run must use the same one. Same compose file, same override shape, same assertions as production (including **no published ports**). Nothing is shared with production except the read-only source checkout and the image |
| D2 **Email safety** | The rehearsal env sets `SMTP_HOST=smtp.invalid` (a name that cannot resolve, RFC 6761), a syntactically valid non-deliverable placeholder as `SMTP_FROM` (the validator needs a valid address), and no SMTP user/password. Production-mode validation passes and **no email can leave the host**: any invitation or reset attempt fails with `Email delivery failed error=gaierror` and the API reports `email_sent: false`. The rehearsal database contains only the SUPERADMIN `pluisada@gmail.com`, so there is no other address to mail by accident |
| D2a Activating the rehearsal admin | **(i) Required for the gate:** once P2 is resolved, temporarily switch the rehearsal env to the real SMTP settings, run B4 and B5 to the owner's own mailbox only, then switch back to `smtp.invalid` immediately. During that window the only account is the owner's and any invitation goes only to mailboxes the owner controls and names. This also rehearses the real SMTP and the link host. **(ii)** If SMTP is not ready the rehearsal cannot close: there is no script that prints a reset link, and production activation (E) is not allowed until (i) has passed |
| D3 Start | B1 (all five steps, rehearsal values), B2, B3, `pmc up -d`. Check: `healthy`, `pmc exec -T app id` shows uid 10001, `CapEff` is `0000000000000000`, `no-new-privileges` present (`docker inspect -f '{{.HostConfig.SecurityOpt}}' "$PM_CTR"`), logs free of startup errors |
| D4 Network and TLS | Add the rehearsal proxy host in NPM as C4 **without the Advanced block** (C5 is applied in D5, after the T3 baseline) and obtain the certificate; run C1 to C3 and C6 to C8 against the rehearsal hostname. **The certificate must be issued and C6 must pass before D5** |
| D5 Logging | T3 baseline (Advanced block still absent), then apply C5, then T1, then T2 (rehearsal container only, with the guards), all as specified in C10; classify each result, synthetic markers only. Also confirm the app and Docker logs hold no token (T1c, T2c). Record the NPM image ID and the hash of the Advanced block for the E11 equivalence check |
| D6 Application workflows (signed in as the SUPERADMIN) | Select a company; "Create default board" (six stages); create a labelled card; edit it; move it forward through the stages and into Lost with a reason; open it in a second tab, change it in one tab and save in the other to see the **stale-version conflict** (HTTP 409 message) and the retry; Undo; open Reports and KPI/Dashboard (counts and Deal Value match what was entered); download the Cards template; **session revocation:** with two tabs signed in, log out in one and confirm the other tab's next action is rejected (401); confirm the AI assistant fails gracefully without a key and nothing else breaks; try inviting an address (fails with the SMTP-failure message while `smtp.invalid`) |
| D7 Restart behaviour | Guarded with `pm_expect pm-rehearsal /docker/sales-pipeline-rehearsal`: `pmc restart`, then `pmc down` and `pmc up -d` (never `-v`): same account, same card, same password, container healthy again; a still-valid login after restart proves `SECRET_KEY` and data persistence |
| D8 Backup, off-host and restore (mandatory gates, section 8) | Run the backup script once (8.2) including **encryption, off-host transfer and hash verification** to the P3 destination; verify the set against its manifest (8.3); restore the **off-host copy** into a validated scratch directory and compare to the **manifest** (8.4); exercise the retention listing on aged synthetic sets; then the full restore drill (8.5): stop, quarantine, restore, verify against the manifest, start, sign in. Record the timings and every deviation; this is what turns section 8 from a template into a proven procedure |
| D9 Failure drills | Guarded as T2 (`pm_expect`, literal name `pm-rehearsal-app-1`, rehearsal hostname only): stop the rehearsal container and request the rehearsal hostname (expect 502 from NPM, recover with `docker start`); set a wrong `PM_COMPANY_DB_LITY` file name in a copy of the env file and confirm startup is refused with the production-config message and that no file is created; `pmc up -d` again recovers |
| D10 Not testable with one user | Maximum two company Admins (including invitation capacity), second global administrator refusal (try `bootstrap --admin-email <other>`: expected refusal and no data change), and independent CLEAR across reviewers need extra accounts. Automated tests cover them; they are exercised live at the first Admin onboarding (F6) or in the rehearsal only if the owner provides additional mailboxes under D2a(i). **This is a documented, accepted deferral, not a pass** |
| D11 Record and sign-off | Complete the rehearsal record (section 9) and copy it, with the Mac-side evidence files, to `/docker/sales-pipeline/records/` and off-host. The owner reviews it and signs off in writing (G-D) |

**Acceptance criteria (G-D):** every D3 to D9 item passes (D10 recorded as deferred); `diagnose_company_schema.py` x3 exit 0 and `diagnose_identity_limits.py` exits 0; the B1 assertions all PASS; C6 to C8 pass; T3 shows the baseline leak, T1 and T2 are classified per C10 with any residual risk explicitly accepted or rejected by the owner; D8 completed in full with restore checks matching the manifest; the A4 inventory is unchanged except for the rehearsal's own containers; no real email left the host other than the D2a(i) messages to the owner, which the owner confirmed; the record is complete (section 9.5). Any failure: fix, rerun the affected part, report. **The owner confirms in writing that Phase E may start.**

**Cleanup (scoped; look before deleting; only after the record is copied out):**

```bash
pm_expect pm-rehearsal /docker/sales-pipeline-rehearsal && echo "guard ok" || echo "STOP: do not continue"   # a production-configured shell must never reach the rm below
cp -a "$PM_DIR/records/." /docker/sales-pipeline/records/       # records first (they must outlive the rehearsal)
ls -la /docker/sales-pipeline/records                           # confirm the copy before anything is removed
pmc down                                                        # no -v; the rehearsal uses bind mounts only
docker ps -a --filter "name=$PM_PROJECT"                        # must list nothing of ours
ls -la "$PM_DIR" "$PM_DIR/data"                                 # read, then (owner confirms):
[ "$PM_DIR" = /docker/sales-pipeline-rehearsal ] && rm -rf "$PM_DIR"    # only the rehearsal directory; refuses anything else
```

Then remove the rehearsal proxy host and certificate in NPM, ask the owner to delete the rehearsal DNS record, compare `docker ps -a`, `docker network ls`, `docker volume ls`, `ss -ltn` with the A4 snapshot, and confirm the bot still works. The image `sales-pipeline:<SHA>` is kept for production. The cleanup evidence goes into the record.

### Phase E. Production deployment

**E0 Preconditions (stop if any is false):** G-A.1 and G-A.2 recorded and G-A signed; Phase D accepted with the **rehearsal record (section 9) complete, including the T3/T1/T2 classifications, the owner's written decision on any residual risk, the backup and restore results of D8, and the owner's written G-D approval**; P3, P5, P10 resolved and the owner available to create the production DNS record at E4 (P1); the image on the VPS has **the same ID recorded in the rehearsal record**; a quiet window; a second SSH session ready; the owner's explicit written authorization of Phase E. Nothing below may start without all of them.

| Step | Where | R/C | Action | Verification | Rollback checkpoint |
|---|---|---|---|---|---|
| E1 | VPS | R | Repeat A1 (Mac), A3 to A5 and the A4 inventory (a new snapshot; the A4 "directory exists" stop applies only to its first run, and `/docker/sales-pipeline` now legitimately holds `src` and `records`, but no `data` and no env file) | SHA unchanged; resources and Compose version fine; NPM address unchanged | RB0: nothing changed yet |
| E2 | VPS | R | `git -C "$PM_SRC" rev-parse HEAD` equals `<SHA>`, clean status, no `branded_email.py`; `docker image inspect sales-pipeline:<SHA> --format '{{.Id}}'` | **Equals the image ID in the rehearsal record.** If the image is missing or the ID differs: **stop**; do not rebuild silently, because the rehearsal then no longer covers what will run. Return to A11 and rerun the affected rehearsal parts | RB0 |
| E3 | VPS | C (copy only) | Protect NPM before editing it: Hostinger panel snapshot if available, and a copy `cp -a /docker/nginx-proxy/data /root/npm-data-<ts>` (`command -v sqlite3` allows a consistent `.backup` of NPM's `database.sqlite` instead; a plain copy of a live SQLite file may be inconsistent) | Copy exists; note its size | RB1 |
| E4 | Owner + Mac | C (DNS) | **Create the production DNS record now** (P1): CNAME `pipeline` > `automacao.pluisada.com.br`, DNS-only. Then `dig +short pipeline.pluisada.com.br` from the Mac **and** from the VPS | Resolves to `automacao.pluisada.com.br.` then `187.77.236.218` on both. Wait for propagation and for earlier negative answers to expire; do not proceed to E10 until both resolve | RB2: remove the record |
| E5 | VPS | C | B1 steps 1 to 5 with production values | All B1 checks PASS, including `NO PUBLISHED PORTS` | RB2 |
| E6 | VPS | C | B2: create the four databases | Four files, owner 10001 | RB3: move the four files to a `rejected-<ts>` directory (do not delete) and recreate, only if no real data exists |
| E7 | VPS | C | B3: bootstrap `pluisada@gmail.com`; diagnostics; counts | `users 1`, `active global admins 1`, `with password 0`, `memberships 0`, three schema checks exit 0, limits exit 0 | RB3 |
| E8 | VPS | C | **Baseline backup set** (8.2) of all four files **before the app starts** and before any user data exists, with manifest, encryption, off-host copy and hash verification; then verify the set against its manifest (8.3) | Backup script exit 0; manifest present; hashes equal at the destination; verifier reports no mismatches. **Mandatory gate: if any part fails, do not continue** | RB4: this is the restore point |
| E9 | VPS | C | `pmc up -d`; `pmc ps`; `pmc logs --tail 100 app` | `healthy`; no `Invalid production configuration`, no `ExistingDatabaseRequired`, no restart loop (if it loops: `pmc stop`, read the log). uid 10001; `CapEff` all zero; `docker port "$PM_CTR"` empty | RB5: `pmc stop` (data untouched) |
| E10 | UI | C | NPM proxy host as C4, then C5 | Certificate valid; Advanced block in place | RB1 |
| E11 | Mac / VPS | R + synthetic markers | C1 to C3, C6 to C9, **C10 T1 (as T4) and the equivalence check** (T2 is never run in production) | All pass; any residual risk (inherited from the rehearsal) explicitly accepted by the owner. **Stop here if T1 or the app/Docker check leaks, or if the equivalence check fails.** This precedes B5 and every invitation | RB1/RB5 |
| E12 | VPS | C (sends 1 email) | B4 SMTP test to the owner | Level (5) confirmed by the owner | Fix SMTP; no data impact |
| E13 | VPS + browser | C | B5 activation, B6 role confirmation | Owner signs in; role Super Admin; counts 1/1/1/0; limits exit 0 | RB6 |
| E14 | Browser + VPS | R | Production smoke test (below) | All pass | RB6 |
| E15 | VPS | C | Install the backup schedule (8.2, only the version proven in D8), the pruning review, and the monitoring (Phase F, P5); take a first restore test from the **off-host** copy into scratch (8.4) | Cron entry present; first scheduled run succeeds; restore test matches its manifest | n/a |
| E16 | VPS | C | Complete the production record (section 9) and have the owner sign off | Record complete and signed | n/a |

**Production smoke test** (creates no cards; card workflows were proven in D6): HTTPS valid and HSTS single; sign in as the SUPERADMIN; role shown; the three companies listed; each company opens (no board yet is expected; the Super Admin may create the default boards as a launch task if the owner wants); Reports and Dashboard open with zeros; sign out and in; an invalid login is refused with the generic message; `pmc restart` and sign in again (persistence and key stability); logs contain no errors or tokens; `diagnose_*` scripts exit 0; one scheduled-style backup run succeeds and verifies against its manifest.

**Rollback checkpoints and when restoring a database is necessary**

| Checkpoint | Situation | Action | Database? |
|---|---|---|---|
| RB0 | Failure in preflight or build | Nothing to undo; remove only `sales-pipeline:<SHA>` if wanted (`docker image rm sales-pipeline:<SHA>`, that tag only) | None exists |
| RB1 | NPM change misbehaves | Delete the new proxy host in the UI; if NPM itself is unhealthy, restore NPM from E3 (restart; test the bot) | Not affected |
| RB2 | Wrong env, override or DNS | Fix the file or remove the record; rerun the B1 assertions | Not affected |
| RB3 | Creation or bootstrap failed, no real data | Move the partial files aside and recreate; nothing to restore | Recreate |
| RB4 | Baseline backup set exists | Reference point for every later restore | Source of restore |
| RB5 | App will not start or misbehaves, no data corruption | `pmc stop` (or `pmc down`, never `-v`); fix config or image tag; `pmc up -d`. Roll back the **code** by editing the override's `image:` to the previous tag (first install: there is none, so stop the container and leave the hostname returning 502) | **Not restored**: configuration and code failures never justify touching data |
| RB6 | After real use: data damaged, wrong delete, failed upgrade migration, disk failure | Section 8.5 restore from the latest set that verifies against its manifest | Restore **only** here, after saving the damaged files aside |

### Phase F. Operations

**F1 Backups:** section 8 (mandatory gates). **F2 Monitoring** below. **F3 Upgrades** below. **F4 Log rotation:** the override limits container logs to 5 x 10 MB; NPM's own logs are bounded by NPM. **F5 Certificates:** NPM renews Let's Encrypt automatically; the monitor alerts when expiry is under 14 days. **F6 Adding administrators and users** below.

**Monitoring (what must be watched; the alert channel is P5).**

| Item | Check | Alert when |
|---|---|---|
| HTTPS availability | External HTTPS GET of `https://pipeline.pluisada.com.br/api/health` expecting `"status":"ok"` every 5 minutes, from outside the VPS | Two consecutive failures; certificate expiry under 14 days |
| Application health | `docker inspect -f '{{.State.Health.Status}}' pm-central-app-1` every 5 minutes on the host | Not `healthy`, or restart count increasing (`docker inspect -f '{{.RestartCount}}'`) |
| Backend errors | Daily: `docker logs --since 24h pm-central-app-1 2>&1 \| grep -cE 'Traceback\|ERROR\|Email delivery failed'` | Count above zero (review the lines; they never contain bodies, links or full addresses) |
| Disk space | `df --output=pcent /docker \| tail -1` and the size of `data/`, `backups/` and `restore-test/` | Above 80% |
| Failed or missing backups | The backup script writes `backups/LAST_SUCCESS` (UTC timestamp) only after the off-host copy is hash-verified, renames a failed set to `<ts>.FAILED`, and exits nonzero; a separate check compares the age of `LAST_SUCCESS` and looks for `*.FAILED` | `LAST_SUCCESS` older than 26 hours, any `*.FAILED` directory, or script exit nonzero |
| Restore-test overdue | Date of the last restore-test entry in `records/` | Older than 35 days |
| Trusted-proxy drift | After any NPM recreate or upgrade, compare NPM's address with `RATE_LIMIT_TRUSTED_PROXIES` | Different (otherwise all users share one rate-limit bucket) |

**F3 Routine upgrade (each release).**
1. Owner picks the SHA; `git ls-remote` confirms it is on `origin/main`; read the commit notes and `docs/DEPLOYMENT.md` for new migration scripts (for example `init_company_*` or `migrate_*`).
2. **Functional evidence (A9) and dependency audit (A10, 5.4)** on a clean checkout of that SHA, recorded separately. A new SHA needs both again.
3. **Backup-before-upgrade checkpoint:** run the backup (8.2) with manifest, off-host copy and hash verification, then verify the set (8.3). No upgrade without it.
4. Rehearse: restore that set into a throwaway project (8.4 machinery plus Phase D) and run the upgrade there first.
5. `git -C "$PM_SRC" fetch && git -C "$PM_SRC" checkout --detach <NEW_SHA>`; clean status; `docker build -t sales-pipeline:<NEW_SHA> "$PM_SRC"`; record image ID and digests.
6. `pmc stop`; run the release's documented migrations/init scripts as dry run, then `--apply`, then `diagnose_company_schema.py` x3 (exit 0) and `diagnose_identity_limits.py`.
7. Edit the override `image:` to the new tag; rerun the B1 assertions; `pmc up -d`; health; smoke test; C10 tests if NPM or logging changed.
8. Rollback: `pmc stop`; set `image:` back to the previous tag; if a migration ran and the old code cannot use the schema, restore the pre-upgrade set (8.5); `pmc up -d`. Additive schema changes are normally harmless to older code, but this is **not tested**; keep the pre-upgrade set until the new release has run cleanly for a week. Keep the previous image tag.
9. Update the deployment record.

**F3 shortened procedure for a code-only release (proven twice on 2026-10-26; owner-approved).** Use it only when `git diff --name-only <deployed SHA> <new SHA>` shows no change to schema or models, `init_*`/`create_*`/`migrate_*` scripts, `Dockerfile`, `docker-compose*.yml`, `pyproject.toml`, `uv.lock` or `package*.json`; otherwise follow steps 1 to 9 above, including the rehearsal.
1. **Prepare (Mac only).** A clean isolated clone of the exact SHA (no permanent directory, branch or worktree). Tests and audits on that tree: `npm ci`, `tsc`, lint, unit tests, build, central end-to-end; backend with `uv sync --locked --extra dev` (the test tools live in the `dev` extra); `npm audit` and `pip-audit` **always re-run** (advisory data changes daily). The full backend suite may be **reused** from a run on a commit whose tree hash is identical (`git rev-parse <sha>^{tree}`), but never the audits. Build **one** `linux/amd64` image and record its ID, architecture and layer digests; check inside the image that there is no `.env` or database and that the new code is present.
2. **Protect (VPS).** Read-only state check; the image of the deployed release and the previous one must be present (rollback); run the existing backup script with the cron environment (`PM_DIR`, `PM_PROJECT`, `PM_SRC`), then `sha256sum -c SHA256SUMS` and read the manifest (four files, `integrity_check` ok, 0 foreign-key violations). Stop if anything fails.
3. **Deploy.** `docker save | ssh ... docker load`; compare the image ID and the layer digests with the Mac's. **Order matters:** the backup script takes the release SHA and the image tag from the VPS source tree (`git -C "$PM_SRC" rev-parse HEAD`), so move the source tree (`checkout --detach <NEW_SHA>`) only **after** the pre-upgrade backup and after the new image is loaded. Copy `docker-compose.vps.yml` to `docker-compose.vps.yml.pre-<NEW_SHA7>`, change only the `image:` line, run `pmc config` and the assertions (no published ports, `pull_policy: never`, the `sales-pipeline` alias on `nginx-proxy_default`, `APP_BASE_URL` the production URL; `PM_ENV=production` and an empty or set `OPENROUTER_API_KEY` come from the Compose file and are expected), then `pmc up -d` (recreates only the application container).
4. **Validate.** Container healthy and running the new image ID; public `/api/health` 200 and `/api/mode` central; no published port; `/docs`, `/redoc`, `/openapi.json` 404; one HSTS header and the CSP and other headers; no CORS header for a foreign origin; `diagnose_company_schema.py` x3 and `diagnose_identity_limits.py` exit 0; the new routes answer 401 without a token; the owner signs in and checks the changed screens. Send **no** email unless the release changes email code, and then only to the recipient the owner names.
5. **Complete or roll back.** Record SHA, image ID, backup set and results (`docs/PRODUCTION.md`, a non-secret file in `records/`). **Rollback of the application only:** `pmc stop`, restore the saved override file, `git -C "$PM_SRC" checkout --detach <previous SHA>` (so the next backup records the right release), `pmc up -d`, verify. Databases are never restored automatically; a restore needs separate written authorization.

**F6 Adding company administrators and users after launch** (Super Admin only; none of this is a launch requirement):
1. Sign in as the SUPERADMIN, select the company, create the default board ("Create default board", six stages) or build one under "Manage boards and columns".
2. Users panel: enter the email and role (Sales Member, Visitor or Admin; Admin asks for a confirmation) and send the invitation. Needs working SMTP; the recipient follows the emailed single-use link (valid 7 days) and sets a password. A person who already has an account joins another company with "I already have an account". "Resend" is available on a pending invitation.
3. **Limits enforced by the application:** at most two Admins per company, counting active Admins and pending Admin invitations (a third is refused with a message; an accepted invitation does not count twice); one active global administrator. A joined member can be promoted to Admin or deactivated from the Users list.
4. **Live verification at the first Admin onboarding (deferred from D10):** invite Admin A and Admin B, confirm a third is refused while two are open; run `diagnose_identity_limits.py` (exit 0); with a Sales Member, edit a card and confirm each required reviewer must CLEAR independently (one reviewer's CLEAR does not clear another's) and that deactivating a reviewer voids that reviewer's pending row; cancel a test user and confirm they can no longer sign in; confirm a stale card version is rejected. These create real rows: use clearly labelled test cards and delete them afterwards (audit rows remain). Record the results in the production record.
5. Cards can be loaded with the Cards workbook import (Super Admin only; permanent ledger: the same workbook cannot be imported twice). Product catalog defaults are seeded.

---

## 8. Backups, off-host copy and restore

### 8.0 Status of this section

Everything in 8.2 to 8.5 is a **template that has not been run anywhere**. The underlying repository helpers exist and were used on the developer's Mac (2026-10-22) and in disposable rehearsals, but never inside this image or on Linux. A procedure becomes **proven** only when D8 runs it end to end and the result is recorded in section 9.

| Component | Status |
|---|---|
| `db_target_guard.make_backup`, `restore_backup`, `check_state`, `sha256_file` (repository) | Exist; used on the Mac; untested in the image |
| 8.2 backup script and manifest writer | **Template, untested** |
| 8.3 manifest verifier | **Template, untested** |
| 8.4 restore test | **Template, untested** |
| 8.5 production restore | **Template, untested** |
| Off-host encryption and transfer | **Undefined until P3**; placeholders only, no tool is chosen here |
| Retention pruning | **Listing only** until reviewed and exercised on aged synthetic sets in D8 |

### 8.1 Policy and mandatory gates

| Item | Policy |
|---|---|
| What | All four files, every run, from the live app using SQLite's online-backup API (`make_backup`: consistent snapshot; verifies `integrity_check`, `foreign_key_check` and logical equality with the source; never overwrites). Never copy live `.db` files with `cp` |
| When | Daily at a quiet hour (UTC, to be chosen), plus before every upgrade, import or role-structure change, and once right after the databases are created (baseline, E8) |
| Per-set manifest | Every set carries `MANIFEST.json` (8.2): UTC timestamp, release SHA and image ID, and for **exactly the four files** (`identity.db`, `salcomp.db`, `lity.db`, `valor-log.db`) its size, SHA-256, `integrity_check` result, foreign-key violations, `user_version` (schema version) and the **row count of every table** (counts only; no row data) |
| Local retention | 14 daily sets in `$PM_DIR/backups` |
| **Gate: off-host copy** | Every set is encrypted, transferred to the P3 destination, and re-hashed there; a set counts as successful only when the destination hash equals the local one. Not optional |
| **Gate: retention** | Off-host: 14 daily + 8 weekly + 12 monthly (proposal; the owner confirms and P3 must be able to enforce it). The pruning rule is exercised on aged synthetic sets in D8 and its output reviewed before deletion is enabled |
| **Gate: restore testing** | A full restore drill in D8 and in the first month of production; afterwards a non-destructive restore test **monthly** from the **off-host** copy; each checked against the manifest of the selected set |
| Not a substitute | A Hostinger VPS snapshot may complement this but is not a documented, tested restore path and is not relied on |

If a gate cannot be met, E8 does not proceed and, once running, the monitor raises an alert.

### 8.2 Backup script and manifest (template, untested): `/usr/local/sbin/sales-pipeline-backup.sh`, root

Directories (created in B1): `$PM_DIR/backups` owned `10001:10001` mode 700. The script, running as root, creates each set directory owned by 10001 so the one-off container (uid 10001) can write into it; the live `/data` directory is mounted read-write by the compose service, as the app itself needs.

```bash
#!/usr/bin/env bash
# TEMPLATE - UNTESTED until Phase D8. Values below are the production ones; the rehearsal exports its own.
set -euo pipefail
umask 077
PM_DIR=${PM_DIR:-/docker/sales-pipeline}; PM_PROJECT=${PM_PROJECT:-pm-central}; PM_SRC=${PM_SRC:-/docker/sales-pipeline/src}
pmc() { docker compose --env-file "$PM_DIR/pm-central.env" -p "$PM_PROJECT" \
          -f "$PM_SRC/docker-compose.central.yml" -f "$PM_DIR/docker-compose.vps.yml" "$@"; }

exec 9>"$PM_DIR/backups/.lock"; flock -n 9 || { echo "another backup is running" >&2; exit 1; }

# Provenance: the source must be clean, and the image named in the override must be the one for this SHA.
git -C "$PM_SRC" diff --quiet && git -C "$PM_SRC" diff --cached --quiet || { echo "source tree not clean" >&2; exit 1; }
SHA=$(git -C "$PM_SRC" rev-parse HEAD); IMAGE="sales-pipeline:$SHA"
grep -q "image: $IMAGE" "$PM_DIR/docker-compose.vps.yml" || { echo "override does not name $IMAGE" >&2; exit 1; }
IMAGE_ID=$(docker image inspect --format '{{.Id}}' "$IMAGE")

TS=$(date -u +%Y%m%dT%H%M%SZ)
SET="$PM_DIR/backups/$TS"
install -d -m 700 -o 10001 -g 10001 "$SET"
trap 'rc=$?; if [ $rc -ne 0 ]; then mv "$SET" "$SET.FAILED" 2>/dev/null || true; echo "backup FAILED (exit $rc); set renamed .FAILED" >&2; fi' EXIT

# One container: back up the four files, verify each, write the manifest.
pmc run --rm --no-deps -T -v "$SET:/backup-set" app python - "$TS" "$SHA" "$IMAGE" "$IMAGE_ID" <<'PY'
import json, os, sqlite3, sys
from db_target_guard import make_backup, check_state

ts, sha, image, image_id = sys.argv[1:5]
files = {}
for name in ("identity", "salcomp", "lity", "valor-log"):
    src, dst = f"/data/{name}.db", f"/backup-set/{name}.db"
    result = make_backup(src, dst)                      # refuses to overwrite; verifies integrity, FK and logical equality
    assert result["logical_dump_equal"], name
    counts = check_state(dst, expect_cpm=None, admin_email=None)   # raises unless integrity is ok and FKs are clean; returns every table's row count
    conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    files[f"{name}.db"] = {
        "bytes": os.path.getsize(dst),
        "sha256": result["sha256"],
        "integrity_check": conn.execute("pragma integrity_check").fetchone()[0],
        "foreign_key_violations": len(conn.execute("pragma foreign_key_check").fetchall()),
        "user_version": conn.execute("pragma user_version").fetchone()[0],
        "table_row_counts": counts,
    }
    conn.close()
manifest = {"format": 1, "created_utc": ts, "release_sha": sha, "image": image, "image_id": image_id, "files": files}
with open("/backup-set/MANIFEST.json", "w") as handle:
    json.dump(manifest, handle, indent=2, sort_keys=True)
print("manifest written for", ts)
PY

# Host-side hashes of everything in the set, then re-check them.
( cd "$SET" && sha256sum identity.db salcomp.db lity.db valor-log.db MANIFEST.json > SHA256SUMS && sha256sum -c SHA256SUMS )

# ---- MANDATORY off-host gate: placeholders until P3 is resolved; every step must fail the script (non-zero) on any error ----
# 1. encrypt the set      : <ENCRYPT_COMMAND from P3>  -> "$PM_DIR/backups/$TS.enc"  (the WHOLE set directory: the four databases, MANIFEST.json and SHA256SUMS; key custody per P3; the key is NOT stored in $PM_DIR)
# 2. transfer             : <TRANSFER_COMMAND from P3>
# 3. verify at destination: hash of the transferred object == local hash of "$TS.enc"; exit non-zero otherwise.
#    (Content is verified later by fetching, decrypting and running 8.3 on the off-host copy: monthly restore test, 8.4.)
# ---------------------------------------------------------------------------------------------------------------------------------
echo "$TS" > "$PM_DIR/backups/LAST_SUCCESS.tmp" && mv "$PM_DIR/backups/LAST_SUCCESS.tmp" "$PM_DIR/backups/LAST_SUCCESS"   # only after the off-host verification

# Retention: list only until reviewed (gate). Candidates: older than 14 days, valid set names, never the newest three.
find "$PM_DIR/backups" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended -regex '.*/[0-9]{8}T[0-9]{6}Z' -mtime +14 -print | sort | head -n -3
```

Notes: `flock` serializes runs; a failed set is renamed `<ts>.FAILED` (kept for diagnosis, never counted, flagged by the monitor); `LAST_SUCCESS` is written only after the off-host check; the encrypted `.enc` objects are what leave the host; the retention line prints candidates and deletes nothing. `docker compose run` publishes no ports and takes no service aliases by default [docs]. Whether `python -` receives the heredoc through `compose run -T` is assumed and is verified in D8. Install as `/etc/cron.d/sales-pipeline-backup` (following the `openclaw-limpa-anexos` precedent) **only after D8 has proven the script**, logging to a root-only file that holds no secrets.

### 8.3 Verify a set against its own manifest (template, untested; read-only)

Used after every backup, before a restore test, and before a production restore. It never compares against the live databases.

```bash
SETDIR=<directory holding the set: the local one, or a fetched-and-decrypted off-host copy>
( cd "$SETDIR" && sha256sum -c SHA256SUMS )                                   # host-side hash check
docker run --rm --network none --user 10001:10001 -e EXPECT_SHA="${EXPECT_SHA:-}" -v "$SETDIR:/restore-in:ro" "$PM_IMAGE" python - <<'PY'
import json, os, sqlite3, sys
from db_target_guard import check_state, sha256_file

EXPECTED = {"identity.db", "salcomp.db", "lity.db", "valor-log.db"}
manifest = json.load(open("/restore-in/MANIFEST.json"))
bad = []
if set(manifest["files"]) != EXPECTED:
    bad.append(("manifest", "file set is not exactly the four databases"))
if os.environ.get("EXPECT_SHA") and manifest["release_sha"] != os.environ["EXPECT_SHA"]:
    bad.append(("manifest", "release_sha differs from the expected release"))
for name, exp in manifest["files"].items():
    path = f"/restore-in/{name}"
    if exp["integrity_check"] != "ok" or exp["foreign_key_violations"] != 0:
        bad.append((name, "manifest records a failed check"))
    if sha256_file(path) != exp["sha256"]:
        bad.append((name, "sha256"))
    if check_state(path, expect_cpm=None, admin_email=None) != exp["table_row_counts"]:   # also fails on integrity/FK problems
        bad.append((name, "row counts"))
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    if conn.execute("pragma user_version").fetchone()[0] != exp["user_version"]:
        bad.append((name, "user_version"))
    conn.close()
print(json.dumps({"set": manifest["created_utc"], "release_sha": manifest["release_sha"], "image_id": manifest["image_id"], "mismatches": bad}))
sys.exit(1 if bad else 0)
PY
```

Set `EXPECT_SHA=<SHA>` before a production use (8.5) so a set from another release is flagged; for a routine restore test it is optional and the printed `release_sha` is informational. `docker run --network none` with explicit mounts is used here (and in 8.4 and 8.5) so these checks never mount the live data directory by accident and have no network. The image's default command is overridden by `python -`. If opening a database on a read-only mount fails, copy the set to the validated scratch directory of 8.4 and verify there. Whether the helper modules import in a bare environment (no env vars) is verified in D8.

### 8.4 Restore test into a scratch destination (template, untested; non-destructive; monthly)

The scratch directory is created and **validated before anything is written to it**; the live data and backup directories are never a destination.

```bash
# 1. Create the scratch destination explicitly.
SCRATCH="$PM_DIR/restore-test/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 -o 10001 -g 10001 "$SCRATCH" "$SCRATCH/in" "$SCRATCH/out"

# 2. Validate it (every line must succeed; any failure = stop, nothing has been written).
case "$SCRATCH" in "$PM_DIR/data"*|"$PM_DIR/backups"*) echo "REFUSED: scratch inside live data or backups" >&2; exit 1;; esac
[ "$(stat -c '%u:%g %a' "$SCRATCH/out")" = "10001:10001 700" ]                    # ownership and mode
[ -z "$(ls -A "$SCRATCH/out")" ]                                                   # empty destination
docker run --rm --network none --user 10001:10001 -v "$SCRATCH/out:/restore-out" "$PM_IMAGE" \
  python -c "import os; p='/restore-out/.w'; open(p,'x').close(); os.remove(p); print('scratch writable by uid', os.getuid())"
# free space: need at least 3x the set size
need=$(du -sb "$(ls -1d "$PM_DIR"/backups/2*Z | tail -1)" | cut -f1)    # newest local set as the size proxy
avail=$(df --output=avail -B1 "$SCRATCH" | tail -1)
[ "$avail" -gt $((need*3)) ]

# 3. Fetch the newest OFF-HOST set into "$SCRATCH/in" and decrypt it: <FETCH_COMMAND and DECRYPT_COMMAND from P3>
#    then verify it against its own manifest with 8.3 (SETDIR="$SCRATCH/in"). Stop on any mismatch.

# 4. Restore each database into the empty destination and compare with the MANIFEST (not with the live databases).
docker run --rm --network none --user 10001:10001 -v "$SCRATCH/in:/restore-in:ro" -v "$SCRATCH/out:/restore-out" "$PM_IMAGE" python - <<'PY'
import json, os, sqlite3, sys
from db_target_guard import restore_backup, check_state

EXPECTED = {"identity.db", "salcomp.db", "lity.db", "valor-log.db"}
manifest = json.load(open("/restore-in/MANIFEST.json"))
bad = []
if set(manifest["files"]) != EXPECTED:
    bad.append(("manifest", "file set is not exactly the four databases"))
for name, exp in manifest["files"].items():
    dest = f"/restore-out/{name}"
    assert not os.path.exists(dest), dest
    result = restore_backup(f"/restore-in/{name}", dest)        # refuses to overwrite; verifies integrity, FK and logical equality
    if not result["logical_dump_equal"]:
        bad.append((name, "logical content"))
    if check_state(dest, expect_cpm=None, admin_email=None) != exp["table_row_counts"]:   # also fails on integrity/FK problems
        bad.append((name, "row counts vs manifest"))
    conn = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
    if conn.execute("pragma user_version").fetchone()[0] != exp["user_version"]:
        bad.append((name, "user_version vs manifest"))
    conn.close()
print(json.dumps({"restored_set": manifest["created_utc"], "release_sha": manifest["release_sha"], "mismatches": bad}))
sys.exit(1 if bad else 0)
PY

# 5. Optional schema check of a restored company database (verify the override mechanism in D8 first):
#    docker run --rm --network none --user 10001:10001 -v "$SCRATCH/out:/restore-out" -e PM_COMPANY_DB_SALCOMP=sqlite:////restore-out/salcomp.db \
#      -e PM_COMPANY_DB_LITY=sqlite:////restore-out/lity.db -e PM_COMPANY_DB_VALOR_LOG=sqlite:////restore-out/valor-log.db \
#      -e IDENTITY_DATABASE_URL=sqlite:////restore-out/identity.db "$PM_IMAGE" python diagnose_company_schema.py --company salcomp

# 6. Record the result (set timestamp, manifest SHA-256, the JSON above, date, operator) in records/, then delete ONLY this scratch directory (path printed first, owner-confirmed in D8, scripted afterwards).
```

**Pass criteria:** the set verified against its manifest (8.3, which covers the four file names, SHA-256, recorded integrity and foreign-key results, `user_version`, row counts and the release SHA/image ID fields); every restored file `logical_dump_equal`, integrity ok, foreign keys clean, **`user_version` and row counts equal to the selected set's manifest**. The live system's current counts may be shown for information only; they are never a criterion because the live data may have changed since the backup.

### 8.5 Production restore (template, untested; only at RB6; stops the application)

Prerequisites, all checked before anything is moved: the owner has authorized the restore in writing; the chosen set (local or fetched and decrypted off-host) has just passed 8.3 **with `EXPECT_SHA` set to the SHA of the running image** (a set from another release is not restored silently: that is an upgrade or downgrade decision under F3); the free space is at least twice the data size; this shell is configured for production (`pm_expect pm-central /docker/sales-pipeline`); a second SSH session is open.

```bash
pm_expect pm-central /docker/sales-pipeline && echo "guard ok" || echo "STOP: this shell is not set for production; run nothing below"
SETDIR=<verified set directory>                     # passed 8.3 with EXPECT_SHA set
Q="$PM_DIR/quarantine-$(date -u +%Y%m%dT%H%M%SZ)"
# 1. Stop the application and keep the damaged files (moved, never deleted).
pmc stop
install -d -m 700 -o 10001 -g 10001 "$Q"
ls -la "$PM_DIR/data"                               # read it first
mv "$PM_DIR/data/"* "$Q/"                           # databases and any -journal sidecars
# 2. Restore each file to its original path, as uid 10001, with the verified set mounted read-only.
docker run --rm --network none --user 10001:10001 -v "$PM_DIR/data:/data" -v "$SETDIR:/restore-in:ro" "$PM_IMAGE" python - <<'PY'
import json, os, sqlite3, sys
from db_target_guard import restore_backup, check_state

EXPECTED = {"identity.db", "salcomp.db", "lity.db", "valor-log.db"}
manifest = json.load(open("/restore-in/MANIFEST.json"))
bad = []
if set(manifest["files"]) != EXPECTED:
    bad.append(("manifest", "file set is not exactly the four databases"))
for name, exp in manifest["files"].items():
    dest = f"/data/{name}"
    assert not os.path.exists(dest), dest              # the damaged file was moved aside
    result = restore_backup(f"/restore-in/{name}", dest)
    if not result["logical_dump_equal"]:
        bad.append((name, "logical content"))
    if check_state(dest, expect_cpm=None, admin_email=None) != exp["table_row_counts"]:
        bad.append((name, "row counts vs manifest"))
    conn = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
    if conn.execute("pragma user_version").fetchone()[0] != exp["user_version"]:
        bad.append((name, "user_version vs manifest"))
    conn.close()
print(json.dumps({"restored_set": manifest["created_utc"], "release_sha": manifest["release_sha"], "mismatches": bad}))
sys.exit(1 if bad else 0)
PY
# 3. Re-check the restored files and start.
pmc run --rm --no-deps -T app python diagnose_company_schema.py --company salcomp      # and lity, valor-log: exit 0
pmc run --rm --no-deps -T app python diagnose_identity_limits.py
pmc up -d; docker inspect -f '{{.State.Health.Status}}' "$PM_CTR"
```

Then: sign in; compare what you see with the **manifest** of the restored set (the restore is complete when it matches the set, not the damaged live data); write down what was lost between the set's timestamp and the incident (everything since the last backup is gone, which is why the daily schedule and the pre-change backups exist); keep the quarantine until the owner approves deletion. If the restore check reports any mismatch, stop with the application still stopped and the quarantine intact.

---

## 9. Deployment and rehearsal record (mandatory)

Two records are produced: the **rehearsal record** (Phase D) and the **production record** (Phase E). Phase E cannot start without a complete, signed rehearsal record, and the deployment is not closed without a complete, signed production record. A field that does not apply says why; an empty field is a gap, not a pass.

**Contents rule:** no secret values, no tokens, no links, no passwords, no full recipient addresses other than the SUPERADMIN address already in the plan; configuration is recorded as names with `set`/`MISSING`/PASS/FAIL only. **Location:** `records/<env>-<ts>.md` on the VPS (mode 640), the Mac-side evidence files (`<scratch>/records/<SHA>/`) copied beside it, and a copy off-host; the rehearsal record is copied to production's `records/` before the rehearsal directory is removed. A SHA-256 of the record is noted at sign-off.

### 9.1 Identification and release

| Field | Content |
|---|---|
| Environment, date (UTC), operator, approver | |
| Remote SHA at A1 / clone `HEAD` at A2 / VPS `HEAD` at A11 and E2 | All three equal `<SHA>`; clean-status output; `branded_email.py` absent |
| Image | Tag, **image ID**, **architecture**, build date and host, base-image digests, "image ID equals the rehearsal's" (production only) |
| Host facts | OS, architecture, RAM, free disk, Docker version, **Compose version and "v2.24.4 or higher: yes/no"**, NPM network name, NPM address and **NPM container image ID** |

### 9.2 Functional test evidence (G-A.1)

Commands, versions (Python, uv, Node, npm) and totals: **full backend run** (passed/skipped/failed; "full-suite pass: yes only if 0 failures"), **focused run** (separate), frontend lint / `tsc` / unit tests / build, central E2E. Skipped tests named where known. Log files referenced by name and hash.

### 9.3 Configuration checks without secret values

B1 step 4 output (`set`/`MISSING` per name, "no forbidden variables", file mode and owner), B1 step 5 PASS/FAIL list (including **NO PUBLISHED PORTS**), data/backups directory ownership, container checks (uid, `CapEff`, `SecurityOpt`, `docker port` empty), SMTP settings as `set`/`empty` per name with host and port (non-secret) only.

### 9.4 Dependency-audit record (G-A.2; separate from 9.2)

Tool versions and commands; exit codes; dependency counts; the findings table with the columns of 5.4 (tool, package, version, advisory ID, severity and its source, location, fix available, decision, approver, date, review-by); the list of accepted exceptions with their compensating controls and expiry; the statement of what the audit did not cover.

### 9.5 Remaining mandatory fields

| Area | Required content |
|---|---|
| Schema diagnostics | `diagnose_company_schema.py` x3 exit codes and "Schema complete."; `diagnose_identity_limits.py` exit code; the B3 count query output (`integrity ok`, `users 1`, `active global admins 1`, `with password` 0 then 1, `memberships 0`) |
| Network and TLS | DNS results (A8a/A8b/C2/E4); certificate issuer and expiry; C6 header results incl. single HSTS; C7 result; **C8 results (a) to (d)** |
| Token-logging results | Rehearsal: T3, T1 and T2. Production: T1 (T4) and the equivalence check (NPM image ID, hashes of both Advanced blocks). For each: the synthetic marker used, each log source searched, the positive control, the **classification** (verified clean / residual risk / inconclusive / failure / unverified / inherited from the rehearsal), and the owner's written decision on any residual risk. Plus the list of what was not verified (C10) |
| Backup and restore | Set timestamp and path, manifest SHA-256 and its `release_sha`/`image_id`, 8.3 verifier result (file set, SHA-256, integrity/FK, `user_version`, row counts), off-host destination and the destination hash check, restore test (8.4) result against the manifest, full restore drill (8.5) result and duration, retention listing reviewed; "template proven in D8: yes/no" per procedure |
| Acceptance results | Each of D3 to D9 pass/fail (rehearsal) or the production smoke test (production); D10 recorded as deferred; F6 step 4 scheduled |
| Email evidence | The five levels of B4 and B5 (request persisted / submission attempted / server accepted / provider status / owner-confirmed receipt) |
| Inventory comparison | Before (A4) and after (end of D, end of E) snapshots compared; "other services unchanged: yes/no"; bot test result |
| Deviations and exceptions | Every deviation from this plan, every accepted audit exception and every accepted residual risk, each with reason, approver and date |
| Rehearsal cleanup (rehearsal record) | What was removed, what was kept, the DNS and NPM entries removed, the owner's confirmation |
| **Owner approvals** | Written statements, with date and the form in which they were given: G-A (authorize Phase D); G-D (authorize Phase E, after reading this record); Phase E go/no-go at E0; final acceptance at E16 |

---

## 10. Security and configuration checklist (status after the decisions)

| Area | Status |
|---|---|
| Authentication and authorization | Bearer JWT (8 h) with `pwh` and `sv`; logout, reset and password change revoke all sessions; bcrypt; password policy; per-company role enforcement with a commit-time authority recheck; rate limits in memory per process (cleared on restart). Single global administrator and two-Admin limit enforced in the backend [repo]. Verified live at D6 (session revocation) and F6 step 4 |
| HTTPS | TLS at NPM with Let's Encrypt; app emits one HSTS header; no cookies are used (Bearer header), so cookie flags do not apply. C6 |
| Secrets | Fresh `SECRET_KEY` generated on the VPS; env file mode 600 outside the checkout; SMTP credentials entered without echo; nothing in the repository, image (`.dockerignore` excludes `.env*`, `*.db`; A11 checks), logs, records or this document. The Mac `.env` is never copied |
| CORS / hosts | CORS empty (same origin). No host port is published, so the app is reachable only through NPM (B1 assertion, C8) |
| Exposed ports | None new; firewall unchanged (22/80/443). C8 |
| Debug and test mode | `PM_ENV=production` forced; `/docs`, `/openapi.json` off; `PM_E2E` rejected; `/api/health` reports `test_mode:false` (C6) |
| Email | Transactional only; plain text in this release; needs SMTP; rehearsal recipient rule (D2) |
| Logs | App logs never contain bodies, links, tokens or full addresses. **NPM access log leaks tokens by default; `access_log off;` is a launch requirement (C5)**; the NPM **error log** under upstream failure is tested (T2) and any finding is a recorded residual risk, not hidden |
| Uploads / storage | Cards workbook import only (in memory, tmpfs `/tmp`); only the four SQLite files persist; NPM `client_max_body_size 11m` |
| Sessions and cancellation | Revocation on logout/reset; permanent user cancellation exists (commit `8711717`); D6 and F6 step 4 |
| Wrong data in production | Startup validation rejects development/commercial names, duplicates and sidecars; fresh databases only; no `.db` or backup is ever copied from the Mac; D9 drill |
| Image supply chain | Base images pinned by tag, not digest; dependencies from `uv.lock` and `package-lock.json`; **`npm audit` and `pip-audit` run in A10 under the policy of 5.4 (gate G-A.2)**; digests recorded; OS packages in base images not scanned (stated limitation) |
| Accepted limitations | Rate limits per process; single worker and single node; cross-database authorization race window (F8); no read-only root filesystem [repo: `docs/SECURITY.md`] |

### 10.1 Traceability: every mandatory test appears in the execution sequence

| Mandatory test or check | Step(s) | Rehearsal (D) | Production (E) |
|---|---|---|---|
| Remote SHA, clean clone, release contents | A1, A2, A11, A12 | before D | E1, E2 |
| Functional tests (full and focused, kept separate) | A9 | G-A.1 | F3 for each upgrade |
| `npm audit` and `pip-audit`, findings dispositioned | A10 | G-A.2 | F3 for each upgrade |
| Compose minimum and no published ports (config assertion) | A3, B1 step 5 | D3 | E5 |
| No host listener / not directly reachable | C8 | D4 | E11 |
| HTTPS, redirect, single HSTS, headers, CORS, `/docs` off, `test_mode:false` | C6 | D4 | E11 |
| Trusted-proxy and spoofed `X-Forwarded-For` | C7 | D4 | E11 |
| Token logging: baseline leak (access log) | T3 | D5 | not repeated (the rehearsal establishes it) |
| Token logging: access, NPM files, NPM container log, app log, Docker log | T1 | D5 | E11 (T4), before B5 |
| Token logging: controlled upstream failure and NPM error log | T2 | D5 (rehearsal container only) | **not run in production**; inherited via the E11 equivalence check |
| Container hardening (uid, caps, no-new-privileges) | D3, E9 | D3 | E9 |
| Env file mode/owner, forbidden variables, required variables, image has no secrets | B1 steps 4 to 5, A11 | D3 | E5 |
| Fresh databases, schema, single SUPERADMIN, limits | B2, B3 | D3 | E6, E7 |
| SMTP test and activation (five-level evidence) | B4, B5, B6 | D2a(i) | E12, E13 |
| Session revocation, stale version, restart persistence | D6, D7 | D6, D7 | E14 (restart, login) |
| Backup, manifest, off-host, restore, retention (mandatory gates) | 8.2 to 8.5 | D8 | E8, E15, F |
| Failure drills (wrong database path, container down) | D9 | D9 | not repeated |
| Regression of shared services and the health bot | C9 | D4 | E11 |
| Max two Admins, CLEAR per reviewer, user cancellation | D10 (deferred), F6 step 4 | **deferred, not a pass** | first Admin onboarding |
| Deployment/rehearsal record and sign-offs | Section 9, D11, E16 | G-D | E16 |

No mandatory test is without a step, and the only deferral (Admin limits, per-reviewer CLEAR, cancellation) is stated as a deferral with its scheduled step.

---

## 11. Risks, unknowns and decisions

Severity H/M/L. "Blocks" = blocks the phase named.

| # | Finding or unknown | Evidence | Impact | Sev | Verification or decision | Blocks |
|---|---|---|---|---|---|---|
| 1 | Production hostname has no DNS record | [v1] `dig` | No access, no certificate | H | P1; created at E4; A8b informational | Phase E |
| 2 | SMTP provider, sender and credentials undecided | Decision 9 | The SUPERADMIN cannot be activated | H | P2, A7, B4, D2a(i) | Phase D and E |
| 3 | Backup destination, encryption and off-host retention undecided; no host backup exists | V7 | Data loss risk; mandatory gates cannot close | H | P3, section 8, D8 | Phase D (D8) and E |
| 4 | No functional test result recorded for `fdfe3b5` | 3.3 | Unproven release | H | A9, P8 (G-A.1) | Phase D |
| 5 | No dependency audit ever run for the release | 3.3 | Unknown vulnerability exposure | H | A10, P9 (G-A.2), policy 5.4 | Phase D |
| 6 | NPM default access log records query strings (tokens); the error log may too | [upstream]; [nginx behaviour, inferred] | Token replay from logs | H (access: confirmed in source; error log: tested in the rehearsal only; installed version unverified) | C5, T1 to T4, E11 equivalence check, owner decision on residual risk | Phase E (activation) |
| 7 | NPM edit can disturb the health agent; NPM state is a single SQLite | `CLAUDE.md`; primer 4.1 | Outage of the bot | M | E3, C9, quiet window | No |
| 8 | Compose version on the host may be below the v2.24.4 floor (4.2) | 4.2 | Port would stay published: **deployment stops**, no fallback | M | A3 and the B1 `NO PUBLISHED PORTS` assertion; separate plan and authorization for any Compose upgrade | Phase D |
| 9 | `compose run` creation chain, production mode in a container, `python -` through `compose run -T`, helper imports in a bare environment, and Linux ownership of `data/` have never been run | `docs/DEPLOYMENT.md`; section 8.0 | First-install and backup-script failure | M | Phase D (D3, D8) | No (D gates E) |
| 10 | VPS architecture, free RAM/disk unknown | V8 | Build failure | M | A3, A11 fallback | Phase A |
| 11 | Repository access method for the VPS unknown | P7 | Cannot clone | M | A1/A2, deploy key if private | Phase A (A11) |
| 12 | Trusted-proxy address can change when NPM is recreated | primer; compose file | One shared rate-limit bucket | M | A5, monitor row | No |
| 13 | Rehearsal needs a second hostname and certificate | P6 | Weaker rehearsal if skipped | M | Owner decision; DNS before D | Phase D |
| 14 | Plain-text emails in this release; branded HTML is unreleased local work | 3.1 | Cosmetic | L | Separate release decision | No |
| 15 | AI disabled: assistant panel visible but erroring | 3.4 | UX expectation | L | D6 | No |
| 16 | Max-two-Admins, per-reviewer CLEAR and cancellation cannot be exercised with one SUPERADMIN | D10 | Live proof deferred | M | F6 step 4 at first onboarding | No (accepted deferral) |
| 17 | Plaintext n8n key in the Setup docx | V5 | Pre-existing exposure | M | Owner removes it from the document | No |
| 18 | Backup, restore and manifest procedures are untested templates | 8.0 | Restore may fail when needed | H | D8 proves them; E15 exercises the first restore test | Phase E |
| 19 | OS packages in base images are not scanned | 5.4 | Unknown | L | Digests recorded; revisit | No |
| 20 | Stale repository docs (changelog wording, `pip` mention in SECURITY.md, "no reverse proxy exists" in DEPLOYMENT.md) | docs | Confusion | L | Update after deployment | No |

Decisions still needed from the owner are the open prerequisites P1 to P10. No other decision is required to proceed to Phase A.

---

## 12. Execution sequence at a glance

1. **Authorize Phase A.** A1 to A12 (read-only checks, local test and audit runs, VPS clone and build). Outputs: recorded SHA, host and NPM facts, **G-A.1** (functional evidence) and **G-A.2** (audit evidence), kept separate.
2. **Resolve before D:** P2 (SMTP), P3 (backup destination), P4, P6 (**rehearsal DNS record resolving**), P7. Production DNS (P1) and the alert channel (P5) are **not** needed yet. **Authorize Phase D** (gate G-A).
3. **Phase D rehearsal.** B1 to B3, D3, D4 (rehearsal TLS first), D5 (T3, T1, T2), D6, D7, D8 (full backup, off-host and restore chain), D9, D2a(i), D11. Result: signed rehearsal record (gate G-D). Cleanup after the record is copied out.
4. **Authorize Phase E** with P3, P5, the signed record in hand and the owner ready to create the production DNS record at E4 (P1). E0 preconditions; E1 to E3 (read, image identity, NPM copy); **E4 production DNS**; E5 to E8 (config, databases, bootstrap, baseline backup); E9 to E11 (start, proxy, T1 as T4 and the equivalence check); E12 to E14 (SMTP test, activation, smoke); E15 to E16 (schedule, monitoring, restore test, record). Rollback checkpoints RB0 to RB6 apply throughout.
5. **Phase F** from day one: schedule, monitors, monthly restore test, upgrade procedure with its own A9 and A10, onboarding of Admins and users with the deferred live checks (F6 step 4).

No step bypasses the rehearsal (E0 and E2 require its signed record and image ID) or the owner's explicit authorization of each phase (P10, section 13).

## 13. Final readiness recommendation and authorizations

**Ready after the specified prerequisites.** The release commit is published and the tooling for a fresh central install, the single-administrator bootstrap and the diagnostics exists; the design matches the existing VPS pattern. Phase D must not start before the rehearsal hostname resolves and P2, P3, P4, P7 are resolved with G-A.1 and G-A.2 recorded. Phase E must not start before the signed rehearsal record, P3, P5 and the NPM logging results (with any residual risk decided in writing by the owner).

Actions that each need the owner's separate, explicit authorization:

1. Phase A: the local test and audit runs, the read-only VPS inspection, and the VPS clone and image build (A11).
2. Creating the **rehearsal** DNS record (before Phase D).
3. Placing secrets (SECRET_KEY generation, SMTP credentials) on the VPS, for the rehearsal and again for production.
4. Phase D (rehearsal), including the NPM rehearsal proxy host and certificate request, each real email in it (D2a(i)), and its cleanup and deletions.
5. Creating the **production** DNS record (E4).
6. Phase E: database creation, bootstrap, the NPM production proxy host and certificate, application start.
7. Sending the SMTP test (B4) and the activation email (B5), each to a recipient the owner names.
8. Installing the backup cron job, the off-host copy mechanism, retention deletion and the monitoring.
9. Any change to the host's Compose version (a separate plan).
10. Any deletion (rehearsal directory, quarantine, old backups, scratch restore directories).

The statement that no VPS or production change was made applied to the planning versions of this document. The plan was executed on 2026-10-25/26; see "Execution status and lessons" and `docs/PRODUCTION.md`.
