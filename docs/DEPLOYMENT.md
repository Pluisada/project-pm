# Deployment with Docker and a separate commercial database

Status: verified on 2026-10-15 on Docker Desktop (macOS) in isolated scratch environments (Phase 2 and Phase 3): image build, commercial-database creation inside the image, bind-mounted persistence across restart and recreation, healthcheck, unprivileged user (uid 10001, no capabilities) and the strict missing-database refusal. Not verified: Linux hosts (ownership of the mounted directory), a TLS reverse proxy, `RATE_LIMIT_TRUSTED_PROXIES` and the administrator's real reset email (a direct SMTP test email was delivered on 2026-10-16, see step 4 below). See "Verify on the real server".

## How the database path is chosen

The app reads `DATABASE_URL` (default `sqlite:///./pm.db`, relative to the working directory). `docker-compose.yml` sets it from `PM_DATABASE_URL` and mounts `PM_DB_HOST_PATH` at `PM_DB_CONTAINER_PATH`. With none of the `PM_*` variables set, nothing changes: `./pm.db` is mounted at `/app/pm.db`. The names are `PM_*` on purpose: `.env` already holds `DATABASE_URL` and `APP_BASE_URL` for native development, and compose must not hand those to the container.

| Variable | Default | Purpose |
|---|---|---|
| `PM_DB_HOST_PATH` | `./pm.db` | Host file or directory to mount |
| `PM_DB_CONTAINER_PATH` | `/app/pm.db` | Where it appears in the container |
| `PM_DATABASE_URL` | `sqlite:///./pm.db` | `DATABASE_URL` inside the container |
| `PM_PUBLIC_URL` | `http://localhost:8000` | `APP_BASE_URL`: base of emailed signup/reset links |
| `RATE_LIMIT_TRUSTED_PROXIES` | empty | Proxy address(es) whose `X-Forwarded-For` is trusted; see "Client IP and the rate limiter" |
| `PM_BEHIND_PROXY` | empty (no proxy) | `true`/`false`: declares a reverse proxy; see "Client IP and the rate limiter" |

The defaults above describe the development setup. In production (`docker-compose.prod.yml`, see "Production mode and reverse proxy") `PM_DATABASE_URL`, `PM_PUBLIC_URL`, `PM_DB_HOST_PATH` and `PM_DB_CONTAINER_PATH` are **required**: they have no default and compose stops when one is missing.

Mount a **directory** for the commercial database, not the file: SQLite writes journal files next to the database, and a bind-mounted single file cannot hold them. A persistent host directory (or a named volume) is what keeps the data across `docker compose down`, image rebuilds and `up`.

## Local development strategy and the local commercial database (decided 2026-10-16)

- All remaining development and validation happen on the user's Mac. Migration to the VPS Hostinger is deferred until development is complete and the user explicitly approves deployment.
- The permanent local commercial database path is `/Users/pauloluisada/Library/Application Support/SalcompSalesPipeline/data/pm-commercial.db`. Do not infer that the Phase 3 scratch path (`/Users/pauloluisada/IA/Projects/pm-phase3-scratch/data/pm-commercial.db`) is the permanent location.
- The original prepared database in the scratch directory stays preserved until the user explicitly authorizes its retirement.
- Update 2026-10-16 (Phase 4C.8): the CPM migration and the import of the 52 opportunities were applied afterwards to the actual database (batch 1, 52 created; post-import SHA-256 `1aa5a32ed990850d557a2ce439866e8530d00ab70060816651d5260e10132509`; integrity ok, 0 foreign-key violations; evidence in `pm-commercial-backups/20261016-import-4c8-run1/`, summarized in `docs/PLAN.md` Phase 4C). A startup smoke test was run once; no login succeeded. The database is still **not activated**. The app starting is not activation: activation is complete only when the administrator has a verified email and a usable password, set through the emailed reset link, and SMTP delivery has not been verified. The paragraph that follows is the historical state when read-only checked earlier on 2026-10-16 (Phase 4C.6, before the migration and import): no app process, migration or import had run against it. Its SHA-256 then was `de0ac1a2efddc550cd3100a79deb4115b9694de10114952817f8cde1f2c00f27`, identical to the scratch original and to `restore-check/pm-commercial.restore-check.db` (same folder tree); integrity ok, no foreign-key violations, six stages (1st Contact, F2F Meeting, RFI, Proposal open; Award won; Lost lost), 0 cards, 0 stage events, 0 import batches, no `cpm` column, one user (id 1, `pluisada@gmail.com`, admin, `email_verified_at` NULL, placeholder password `!`).
- The real report was first used to rehearse the import on disposable copies of this database (Phase 4B.1/4B.2); that never touched either commercial file. At Phase 4C.6 neither the CPM migration nor the import had been run on the actual database; both were run later on 2026-10-16 (see the update above).
- `restore-check/pm-commercial.restore-check.db` (beside `data/`) is a byte-identical verification copy; keep it until its removal is authorized.
- Administrator activation (password-reset email) and cleanup each need a separate explicit authorization. The CPM migration and the workbook import have been done (2026-10-16).

### Running natively against the local commercial database (when activation is authorized)

The backend does not read `.env` itself, so `DATABASE_URL` comes only from the process environment. SQLite URL form is `sqlite:///` plus an absolute path (four slashes); spaces are used literally and the whole value must be quoted:

```bash
cd backend
DATABASE_URL="sqlite:////Users/pauloluisada/Library/Application Support/SalcompSalesPipeline/data/pm-commercial.db" \
PM_REQUIRE_EXISTING_DB=1 \
SECRET_KEY=<>=32 chars> uv run uvicorn main:app --port 8000
```

`PM_REQUIRE_EXISTING_DB=1` makes `init_db()` call `check_existing_database()` first: the file must exist and carry the complete schema, is inspected read-only, and otherwise startup exits with `ExistingDatabaseRequired` instead of letting SQLite create an empty file. Without it, a mistyped path would only be stopped by `PM_ALLOW_CREATE_DB` being unset (`DatabaseCreationNotAllowed`); set it anyway. Note the guards refuse an un-migrated database; before the CPM migration (applied 2026-10-16) the app refused to start on this file because `cpm` was absent from `card_bid_details`. The smoke test on 2026-10-16 shows it now starts.

Do not use `scripts/start-mac.sh` for the commercial database. It runs `export $(grep -vE '^(#|PORT=)' .env | xargs)`, which (a) exports the development `DATABASE_URL` from `.env` over any value set before the script, and (b) splits unquoted values on spaces, so a path containing "Application Support" cannot survive it. Use it only for the development `pm.db`, unless its environment loading is corrected and verified first.

### Docker and the local path (not verified)

Docker was verified only on Docker Desktop (macOS) with the scratch directory mounted (Phase 2/3). Mounting the `Application Support` directory (`PM_DB_HOST_PATH`, with a space in the path), `PM_DATABASE_URL=sqlite:////data/pm-commercial.db` and the file permissions on it have not been tried. Mount the directory, never the file.

### Prerequisites before the CPM migration, the import and activation (each needs its own authorization)

1. CPM migration: `migrate_bid_cpm.py --database <abs path>` is a dry run; `--apply` requires `--expect-sha256` of the current file. Take and verify a backup first (below). It also writes its own `-pre-bid-cpm-<time>.db.bak` beside the database, i.e. into the data directory; move it out after verifying it. Until it is applied the app refuses to start on this file.
2. Import: only through `commercial_import.py` (dry run by default; pins the workbook, the database before and the approved preview/decisions hashes), on the migrated database, after a second backup. Approved inputs: workbook `1b758fdf89704fa940433f44c107519da7d75d97a43eee7e14d97c8059b00ea6`, `import-preview-v2.json` `0935683c6943081dce6c2d5e403f54eb403daa7724429fbe90195becc46c7289`, `phase4b2/approved-decisions.json` `cb7593ff0a03e46af6e1b7a87d9ff162028bdc90bfa8e80aa1b384c3896d6fed` (files in `pm-phase4-evidence/`). Expected: 52 cards, stages 0/5/3/15/6/23, 52 `baseline` events, CPM 52/52. Every imported card has `salesperson_id` NULL (admin-only edit until assigned).
3. Backup and restore: use `db_target_guard.make_backup` / `restore_backup` (via `commercial_import.py backup` / `restore`). They never overwrite: the destination must be new, and a restore goes to a separate file. A backup's hash differs from its source's; the pass criterion is logical equality (`logical_dump`), `integrity_check` ok and an empty `foreign_key_check`. Only restoring a backup returns the exact pre-import state; the ledger revert keeps history rows. Status: this procedure was rehearsed on disposable copies only (Phase 4B.2); no backup of the actual database has been taken. Do not delete `restore-check` files or the scratch original without authorization.
4. Activation: see "Administrator provisioning and password recovery". Start the app only with `DATABASE_URL` plus `PM_REQUIRE_EXISTING_DB=1` in the process environment (not `scripts/start-mac.sh`), with `SMTP_*`, `APP_BASE_URL` (the URL the emailed link must carry) and `SECRET_KEY` set, then request "Forgot my password?" for `pluisada@gmail.com` and follow the single-use link from the real mailbox. SMTP delivery was verified on 2026-10-16 with a direct `mailer.send_email` test (no token, no database, no app) from the user's Mac using the `.env` credentials: the server accepted the message and it arrived at `pluisada@gmail.com`. Limits of that check: port 587 with STARTTLS only; the link in the test body was a `localhost` placeholder, so a link host equal to the real `PM_PUBLIC_URL` is still to be checked on the VPS; the 2026-10-16 test ran before the TLS hardening, so it did not itself prove certificate verification. Since then `mailer.send_email` passes `ssl.create_default_context()` to `starttls(context=...)` (chain and hostname verification on, no insecure fallback, no implicit TLS on 465); this is established by mocked tests only (`test_phase1_security.py::TestMailerLogging`: context is `CERT_REQUIRED` with `check_hostname`, a TLS failure sends nothing and never reaches login, logs carry the error type only), and no real handshake with the production SMTP server has been repeated since. Not yet exercised: the real password-reset email, the public `PM_PUBLIC_URL` on the deployment environment, and administrator activation (still pending). The forgot-password response is identical for every address, so the screen cannot show success; the server log shows `Email sent` (accepted by the server) or `Email delivery failed`.

## Prepare, then activate (two separate steps)

- **Preparing** a commercial database means creating the file with `create_commercial_db.py` and checking it. It starts nothing and changes no configuration; the development `backend/pm.db` and `.env` are never touched.
- **Activating** it means pointing the real deployment at it (`PM_DB_HOST_PATH`, `PM_DATABASE_URL`) and starting the app. Do this only as a deliberate, separate decision, after the checks below.

## Always pass an explicit environment file

Compose reads `.env` from the project directory when no file is given, and that file holds the development `DATABASE_URL`, `APP_BASE_URL` and secrets. For a commercial database keep a separate file outside the repository (mode 600, never committed) and pass it on every compose command, including `run`, `exec`, `ps`, `logs` and `down`. Production commands also name both files, `-f docker-compose.yml -f docker-compose.prod.yml` (see "Production mode and reverse proxy"); without the override `PM_ENV=production` and the no-fallback checks are not applied:

```bash
docker compose --env-file /secure/pm-commercial.env -p pm-commercial -f docker-compose.yml -f docker-compose.prod.yml config   # review mounts, DATABASE_URL and PM_BEHIND_PROXY first
docker compose --env-file /secure/pm-commercial.env -p pm-commercial -f docker-compose.yml -f docker-compose.prod.yml up -d
```

With `--env-file` given, the repository `.env` is not loaded (checked: `OPENROUTER_API_KEY` resolved to the empty value from the explicit file). A fixed project name (`-p`) keeps the containers, network and volumes of one environment together. Running with `env -i PATH="$PATH" HOME="$HOME" docker compose ...` additionally stops shell variables from leaking in. Always read `docker compose ... config` before the first start and confirm that the only bind mount is the intended directory and that `DATABASE_URL` points inside it.

Required keys in the file: `PM_DB_HOST_PATH`, `PM_DB_CONTAINER_PATH`, `PM_DATABASE_URL`, `SECRET_KEY` (>= 32 characters, see `docs/SECURITY.md`); also `PM_PUBLIC_URL`, `OPENROUTER_API_KEY` and, in production, the SMTP settings below. Compose refuses to start without `SECRET_KEY`.

### SMTP settings for production

Signup and password-reset emails (and therefore administrator activation) need working SMTP. All five variables are passed into the container from the environment file; there are no defaults for credentials or the sender.

| Variable | Production | Notes |
| --- | --- | --- |
| `SMTP_HOST` | mandatory | Compose (with `docker-compose.prod.yml`) refuses to render without it; the app also refuses to start. |
| `SMTP_FROM` | mandatory | Sender address, plain or `Name <addr@domain>`. Compose requires it; the app itself accepts `SMTP_USER` as the sender when `SMTP_FROM` is unset, but set `SMTP_FROM` explicitly. |
| `SMTP_PORT` | optional | Integer 1-65535; defaults to `587` (STARTTLS). An invalid value stops the app. |
| `SMTP_USER` | conditional | Leave unset for an unauthenticated relay. |
| `SMTP_PASSWORD` | conditional | Required whenever `SMTP_USER` is set; a user without a password stops the app. |

These checks are structural only (`production_config.py`); nothing connects to the server at startup. Real delivery and the STARTTLS certificate and hostname verification against the production server must be verified separately (see "Verify on the real server" and the activation steps).

## Persistent host bind mount

`PM_DB_HOST_PATH` is an absolute host **directory**; compose bind-mounts it read-write at `PM_DB_CONTAINER_PATH`, and `PM_DATABASE_URL` names the file inside it. Verified layout:

```text
PM_DB_HOST_PATH=/srv/pm/data              # host directory, outside the repository
PM_DB_CONTAINER_PATH=/data
PM_DATABASE_URL=sqlite:////data/pm-commercial.db     # four slashes: absolute path
```

`docker inspect` shows a single `bind` mount `<host dir> -> /data` (`RW: true`) and the file listed in the container has the same size as on the host (the container sees it owned by uid 10001). Nothing else is mounted: `/app/pm.db`, `/app/pm.db.bak` and `/app/.env` do not exist in the container. The database survives `docker compose restart`, `up -d --force-recreate` and `down` followed by `up`, because it lives on the host.

## Creating the commercial database (supported procedure)

`create_commercial_db.py` is the only supported way. It needs `--database` (an absolute path that must not exist) and `--email` (the first administrator); it has no password argument and never prompts for one. It builds the full current schema, one board "Salcomp Sales Pipeline" with exactly six stages in this order - 1st Contact, F2F Meeting, RFI, Proposal (open), Award (won), Lost (lost) - and the administrator. It creates no cards, no demo data, no other users, no history and no import records. If anything fails, the new file is removed. An existing file is never overwritten (a second run exits 1 with "already exists; refusing to overwrite or reuse it").

```bash
docker compose --env-file /secure/pm-commercial.env -p pm-commercial -f docker-compose.yml -f docker-compose.prod.yml run --rm --no-deps app \
  python create_commercial_db.py --database /data/pm-commercial.db --email <admin email>
```

The host directory must exist first and be writable by uid 10001 (see the checklist). After creating, check the file from the host without writing to it (`sqlite3 -readonly` or Python `sqlite3.connect("file:<path>?mode=ro", uri=True)`):

- `PRAGMA integrity_check` returns `ok`; `PRAGMA foreign_key_check` returns no rows.
- `users` has one row: the administrator, `role = admin`, `password_hash = "!"`, `email_verified_at` NULL.
- `columns` holds the six stages above in `position` order with `stage_kind` open/open/open/open/won/lost.
- Every other table (cards, card_stage_events, card_edit_history, card_import_changes, card_source_refs, card_bid_details, card_actions, import_*, undo_entries, audit_events, auth_tokens, conversation_messages) has 0 rows.

## Administrator provisioning and password recovery

The administrator is created **without a usable password** and **without a verified email**. Nobody can sign in, and HTTP `/api/setup` stays closed (`needs_setup` is false once the user exists, and setup needs `PM_SETUP_TOKEN` anyway). The owner of the address gets in through "Forgot my password?": the app emails a single-use, 1-hour link; following it sets the password, verifies the email and ends any older session. This requires working SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`) in the environment file and a `PM_PUBLIC_URL` that the owner can open. Without SMTP nothing is sent and the administrator cannot get in. The reset flow (single-use link, replay refused, earlier sessions rejected afterwards) was verified in Phase 2 against a database created the same way; it was not repeated against the Phase 3 file, and delivery to the real address and `PM_PUBLIC_URL` are still to be checked on the real server. Further administrators are created only with `backend/admin_cli.py`.

## Strict startup: the database must already exist

Whenever `PM_DB_HOST_PATH` is set, compose sets `PM_REQUIRE_EXISTING_DB=1`. The app then opens the file read-only, checks that it exists and carries the complete schema, and otherwise exits with `ExistingDatabaseRequired` before serving; it never creates, migrates or repairs a database. Verified in Docker with a mounted empty directory and a URL naming a file that does not exist: exit code 3, message "Commercial database /data/does-not-exist.db not found (the file is missing from its directory)", nothing listening on the port, and the host directory still empty afterwards. Because of `restart: unless-stopped` such a container restarts in a loop (6 restarts in about 8 seconds were seen); stop it with `docker compose ... stop` (or `docker update --restart=no <container>`) after reading the cause with `docker compose ... logs app`. The same check refused, without creating any file (run on copies with the app's own function): a missing directory, an empty file, a non-SQLite file, a database with an incomplete schema, a directory instead of a file, a non-SQLite URL and an unset URL.

## Foreign keys

The schema declares foreign keys with `ON DELETE` rules, and `PRAGMA foreign_key_check` is clean on a new database. SQLite does not enforce them unless a connection runs `PRAGMA foreign_keys=ON`, and the app does not do that (the app's own connection reported `foreign_keys = 0`). Checked on a copy: with the pragma off an orphan card is accepted; with it on the insert is rejected and deleting a board cascades. Referential integrity therefore rests on the application code (ORM relationships), not on the database. Run `PRAGMA foreign_key_check` as part of every database check.

## Startup errors (commercial database)

`docker-compose.yml` sets `PM_REQUIRE_EXISTING_DB=1` in the container whenever `PM_DB_HOST_PATH` is set (set or unset it yourself to override). In that mode the app never creates a database: at startup it checks, read-only, that the file named by `PM_DATABASE_URL` exists, is a SQLite database and has every table and column of the models, and otherwise exits with `ExistingDatabaseRequired` before serving anything. With `PM_DB_HOST_PATH` unset (development) nothing changes: a missing `pm.db` is still created and gets the setup screen.

Because of `restart: unless-stopped` a failing container restarts in a loop; read the cause with `docker compose logs app` (last lines) and fix it, then `docker compose up -d`.

| Message | Cause | Fix |
|---|---|---|
| `needs DATABASE_URL to be a SQLite file URL` | `PM_DATABASE_URL` unset or not `sqlite:////...` | Set it with four slashes for an absolute path, e.g. `sqlite:////data/pm-commercial.db` |
| `not found (the directory ... does not exist)` | Container path in the URL does not match `PM_DB_CONTAINER_PATH` | Make the URL's directory equal `PM_DB_CONTAINER_PATH` |
| `not found (the file is missing from its directory)` | Typo in the file name, wrong `PM_DB_HOST_PATH`, or the database was never created | `ls $PM_DB_HOST_PATH`; fix the name or path; create it once with `create_commercial_db.py` |
| `is a directory, not a database file` | URL points at the folder | Point it at the file inside |
| `exists but has no tables` | Empty file (`touch`, or a copy that failed) | Delete it and run `create_commercial_db.py`, or restore a backup |
| `is not a readable SQLite database` | Corrupt or non-SQLite file | Restore a backup |
| `has an incomplete schema` (lists tables/columns) | Database predates a migration | On a backup, run the migrations in `docs/DATABASE.md`; the app never repairs it |

Quick check without Docker, from `backend/`: `PM_REQUIRE_EXISTING_DB=1 DATABASE_URL=sqlite:////abs/path/pm-commercial.db uv run python -c "import database; database.init_db()"` (silent when fine).

## Client IP and the rate limiter

The limiter (`backend/rate_limit.py`, `docs/SECURITY.md` section 7) keys on `request.client.host` and honours `X-Forwarded-For` only when that direct peer is listed in `RATE_LIMIT_TRUSTED_PROXIES`. `docker-compose.yml` passes both `RATE_LIMIT_TRUSTED_PROXIES` and `PM_BEHIND_PROXY` to the container.

Two variables, two jobs; set them together or not at all:

| Setup | `PM_BEHIND_PROXY` | `RATE_LIMIT_TRUSTED_PROXIES` |
|---|---|---|
| **Direct access** (no proxy; clients reach the published port) | unset, empty or `false` | empty |
| **Behind a reverse proxy** | `true` | the proxy's address as the container sees it, or the narrowest CIDR covering it |

`PM_BEHIND_PROXY` is only a declaration: the app cannot detect a proxy, so in production it uses the flag to refuse to start when you say a proxy exists but name no address to trust (otherwise all users would share the proxy's address in the IP limits). `RATE_LIMIT_TRUSTED_PROXIES` is what actually makes the limiter read `X-Forwarded-For`. Accepted flag values are `1/true/yes/on` and `0/false/no/off` (case-insensitive); any other value stops startup. Production without a proxy is allowed, but with direct access on Docker Desktop and some Linux setups every user may appear as the bridge gateway (one shared bucket), see below.

The address the container sees as the peer is not the user's:

- Docker Desktop and some Linux setups show every published-port connection as the bridge gateway (`172.x.0.1`). With no proxy that makes all users one rate-limit bucket.
- Behind a reverse proxy the peer is the proxy.

Recommended setup: terminate TLS in a reverse proxy that appends the connecting address to `X-Forwarded-For` (nginx `proxy_add_x_forwarded_for`, Traefik, Caddy) and publish port 8000 only to that proxy (`127.0.0.1:8000:8000` or a private network).

1. Find the peer the app sees: `docker compose logs app` and read the address in an access-log line, e.g. `172.18.0.1:53110 - "POST /api/login"`, or `docker network inspect pm_default` for the subnet and gateway.
2. Set `PM_BEHIND_PROXY=true` and `RATE_LIMIT_TRUSTED_PROXIES` to exactly that address (`172.18.0.1`) or the narrowest CIDR covering the proxy. Never a wide range: anyone inside it could then choose their own address. Only a catch-all (`0.0.0.0/0`, `::/0`) and malformed entries are rejected by the app; it does not reject other broad ranges such as `10.0.0.0/8`, so the choice of a narrow value is the operator's responsibility.
3. Check from outside: send a request with `X-Forwarded-For: 1.2.3.4` directly to the app port from an untrusted host (must be ignored), then through the proxy (the real client address must be the bucket).

Notes: uvicorn's own proxy-header handling (`FORWARDED_ALLOW_IPS`, default `127.0.0.1`) is separate; leave it unset. A proxy running on the host with `network_mode: host` or inside the container would be seen as `127.0.0.1`. The proxy should also cap upload bodies slightly above the Bid import limit (see `docs/SECURITY.md`) and rate limit login/signup/forgot as a second layer. Counters are in memory: one container, one worker.

## Verify on the real server

- `docker compose build` succeeds and `docker compose run ... create_commercial_db.py` creates the file in the mounted directory (verified on Docker Desktop, macOS).
- Data survives `restart`, `up -d --force-recreate` and `down` + `up`, and the file is on the host (verified on Docker Desktop, macOS; repeat on the server).
- A deliberately wrong `PM_DATABASE_URL` makes the container exit with `ExistingDatabaseRequired` (see Startup errors) and creates no file (verified).
- Login screen (not setup) appears; the admin created above can sign in; no cards exist.
- The peer address seen in `docker compose logs app`, `RATE_LIMIT_TRUSTED_PROXIES` set to it, and the spoofed `X-Forwarded-For` check above.
- `PM_PUBLIC_URL` and SMTP: a signup/reset email carries the public HTTPS link.
- The container runs as uid/gid 10001 (`docker compose exec app id`). The mounted database file/directory must be writable by it: `chown -R 10001:10001 <PM_DB_HOST_PATH>` (for the default `./pm.db` mount on Linux: `chown 10001:10001 pm.db`). Docker Desktop on macOS maps ownership itself.
- `docker compose ps` shows the service `healthy` (healthcheck on `/api/health`); `docker compose exec app sh -c 'cat /proc/1/status | grep CapEff'` shows `0000000000000000`.
- Port 8000 is published on `127.0.0.1` only (`PM_BIND_ADDRESS`); the TLS proxy reaches it, other hosts cannot.
- Response headers through the proxy (`curl -sI https://<public url>/`): CSP, nosniff, `X-Frame-Options`, `Referrer-Policy`, and exactly one `Strict-Transport-Security`. The login screen and a signup/reset link still work with the CSP (browser console free of CSP violations).
- No CORS needed: with `CORS_ALLOWED_ORIGINS` empty, `curl -sI -H 'Origin: https://other.example' https://<public url>/api/health` returns no `access-control-allow-origin`.

## Production mode and reverse proxy (prerequisites; nothing here has been run on a server)

Production uses the override `docker-compose.prod.yml`; every production compose command (`config`, `up`, `run`, `exec`, `logs`, `down`) is `docker compose --env-file <file> -p <name> -f docker-compose.yml -f docker-compose.prod.yml ...`. It sets `PM_ENV=production` and `PM_REQUIRE_EXISTING_DB=1` itself and has no fallback for `PM_DATABASE_URL`, `PM_PUBLIC_URL`, `PM_DB_HOST_PATH` and `PM_DB_CONTAINER_PATH`, so the development `./pm.db` default of the base file cannot be mounted by accident; compose stops with the missing name. The app repeats the database checks in production (`production_config.database_problems()`): `DATABASE_URL` must be an absolute SQLite URL (`sqlite:////...`), must not name `pm.db` (any letter case, after path normalisation), and `PM_REQUIRE_EXISTING_DB=1` is mandatory. `PM_BEHIND_PROXY` and `RATE_LIMIT_TRUSTED_PROXIES` are passed from the environment file by the base file (not required, see "Client IP and the rate limiter"). Verified on 2026-10-16 with `docker compose ... config` and a temporary dummy environment file (nothing started): `PM_ENV` is `production`, `PM_REQUIRE_EXISTING_DB` is `1`, `PM_BEHIND_PROXY` reaches the container as set, and a missing `PM_DATABASE_URL` stops compose. The base `docker-compose.yml` alone (used by `scripts/start.sh`) is the development setup and is unchanged.

Without the override (not recommended: none of the no-fallback checks apply, so a missing variable silently falls back to the development default): set `PM_ENV=production` in the environment file (`docker-compose.yml` passes it through). `PM_ENV` accepts only `production` or `development` (or unset); any other value, such as `prod`, stops startup instead of running as development. The app then refuses to start unless `PM_PUBLIC_URL` (mapped to `APP_BASE_URL`) is a bare `https://` origin that is not local, `PM_E2E` is unset, any `CORS_ALLOWED_ORIGINS` entry is `https`, and `RATE_LIMIT_TRUSTED_PROXIES` has no catch-all, and `PM_BEHIND_PROXY=true` comes with a non-empty `RATE_LIMIT_TRUSTED_PROXIES`; it also stops serving `/docs`, `/redoc` and `/openapi.json` (details: `docs/SECURITY.md` section 11). Without `PM_ENV=production` the compose default `PM_PUBLIC_URL=http://localhost:8000` is used silently, so emailed links would point at localhost: always set both. Verified by unit tests only (`test_production_config.py`); the container was not started in production mode.

The reverse proxy (not verified, none exists yet) must provide:

| Property | Requirement | Check |
|---|---|---|
| HTTPS and HTTP redirect | TLS terminates at the proxy; port 80 redirects to 443 | `curl -sI http://<host>/` |
| HSTS | Exactly one `Strict-Transport-Security` header: either `HSTS_ENABLED=true` in the app **or** the proxy, never both | `curl -sI https://<host>/ \| grep -ci strict-transport` prints 1 |
| Client address | Proxy appends the peer to `X-Forwarded-For`; `PM_BEHIND_PROXY=true` and `RATE_LIMIT_TRUSTED_PROXIES` is its narrow address (see "Client IP and the rate limiter") | spoofed-header check above |
| Upload size | Bid import accepts 10 MiB of file plus 1 MiB form overhead (the app answers 413 above that and needs a `Content-Length`; the proxy must not strip it or stream chunked). Cap the proxy slightly above, e.g. nginx `client_max_body_size 11m;` | a 12 MiB POST to the import URL is refused by the proxy |
| Token redaction in logs | `/?signup_token=` and `/?reset_token=` must not reach proxy access logs. The app redacts them in its own uvicorn access log only. In nginx log `$uri` instead of `$request`/`$request_uri` (e.g. `log_format pm '$remote_addr [$time_local] $request_method $uri $status';`), or send the `/` location's logs nowhere. Apply the same to any log shipper | request `/?reset_token=abc`, grep the proxy log for `abc` |
| Rate limiting | A second layer on `/api/login`, `/api/password/forgot`, `/api/signup*` | load test from a non-allowlisted host |

Database file ownership: the container runs as uid/gid 10001; `chown -R 10001:10001 <PM_DB_HOST_PATH>` on Linux (verified only on Docker Desktop macOS, which maps ownership). SQLite journal files are created beside the database, hence the directory mount.

Dependencies (checked 2026-10-16, network available): the Dockerfile builds the frontend on `node:24-alpine` (changed from `node:20-alpine`, end of life since April 2026; `next` 16.4.2 declares `node >=20.9.0` and no locked package sets an upper bound; the Node 24 build itself has not been run) and installs the Python dependencies in a separate stage with `uv sync --locked --no-dev --no-install-project` from `backend/uv.lock` (uv pinned to `ghcr.io/astral-sh/uv:0.12.15`, the version that wrote the lock; `--locked` fails the build if `pyproject.toml` and `uv.lock` disagree; the image tag and the build were not verified). The application runs from the copied sources and is no longer installed with `pip install -e .`. Base images are pinned by tag, not by digest. `.dockerignore` excludes `.env` and `.env.*`, databases, SQLite sidecars, backups and test output. `npm audit --omit=dev` reported 6 findings on 2026-10-16 (1 critical in `next` 16.1.6; the lock now has `next` 16.4.2, not re-audited); `pip-audit` was not run.

## Administrator activation and recovery (operator procedure; not executed)

State: the administrator (id 1) has no usable password and no verified email. The only supported route in is the emailed reset link; no HTTP path sets a password without it.

Before activating:

1. Production-mode settings above are in the environment file (never in the repository), including `SMTP_HOST`, `SMTP_PORT` (587, STARTTLS with certificate and hostname verification, no plaintext fallback), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SECRET_KEY`, `PM_PUBLIC_URL`.
2. Start with `PM_REQUIRE_EXISTING_DB=1` on the intended database only, after the backup procedure below.
3. Check `docker compose logs app` shows no startup error.

Activation: on the public login page choose "Forgot my password?" and enter the administrator's address. The answer is identical whether or not the account exists, so success is shown only by the log line `Email sent` (accepted by the SMTP server) or `Email delivery failed` (type only; no body, link or full address is ever logged) and by the mailbox. Open the link from the real mailbox (it expires in 1 hour and works once), choose a password that meets the policy; this verifies the email and ends any earlier session. Activation is complete only when the administrator can sign in.

If the email does not arrive or the link expired:

- Retry "Forgot my password?". Limits: 3 emails per account per hour and 10 requests per address per hour (`docs/SECURITY.md` section 7); each new link invalidates the previous ones. Check spam, `SMTP_*`, `SMTP_FROM` accepted by the provider, and that the link host equals the public HTTPS URL.
- A link opened after consumption or expiry shows "Invalid or expired link"; request a new one.
- SMTP cannot be fixed: this is the security-sensitive case. Options, none executed here, to be chosen by the owner: (a) fix or replace the SMTP provider and retry (no weakening; recommended); (b) with shell access to the server and the app stopped, create a second administrator with a password through `backend/admin_cli.py create --username <name> --password-stdin`; this account is created already verified, so it bypasses the mailbox proof, and its password must be read from stdin, never from arguments, and it should be demoted or its use reviewed afterwards; (c) restore a backup taken before the failure. There is deliberately no CLI command that resets an existing account's password and no flag that prints a reset link; adding either would be a new security decision.
- Anyone with shell access to the server and database can already do (b); activation does not widen that.

SMTP status: direct delivery from the Mac to `pluisada@gmail.com` was observed on 2026-10-16 (port 587). Certificate and hostname verification, error handling and log redaction are covered by mocked tests only. The real reset email with the production link has not been sent, and no end-to-end SMTP claim is made.

## C6 release (probability level, Bid Revenue): scope, validation and pending items

Scope of the first publication: **legacy single-database mode only** (`/api/mode` answers `{"auth":"legacy"}`). Central / multi-company mode is not part of it and was not validated. The release candidate is commit `175a549` (12 commits over base `69e3419`). Nothing has been published or deployed, and no migration has been run on any commercial database; this section does not claim otherwise.

What it changes for operators:
- New `cards.confidence_level`, shown in the app as "Probability level" (LOW / MEDIUM / HIGH or NULL = "Not assigned", set by hand, never inferred); the Reports screen has a "Probability level" filter and a "By probability level" table; commit `175a549` also reported Bid Revenue (BRL cents) beside the USD Deal Value, but that reporting was withdrawn afterwards in the working tree (2026-10-19; committed locally in `086a4a5`, not pushed): reports and KPIs carry no Bid Revenue, and Deal Value is the only financial measure (`docs/KPIS.md`). The Bid revenue columns and the importer are kept.
- The obsolete Bid confidence columns (`card_bid_details.confidence`, `card_source_refs.source_confidence`) are dropped and their values discarded on purpose.
- An existing legacy database must be migrated **before** starting this code, with `migrate_legacy_bid_confidence.py`. Full procedure, stop conditions and audit record: `docs/MIGRATION_PROTOCOL_BID_CONFIDENCE.md`. Summary: stop the app; consistent SQLite backup plus an external copy, with hashes; dry run (it does not print the hash, compute it with `shasum -a 256`); `--apply --expect-sha256 <approved hash>`, which writes an exclusive backup, verifies it against the approved content before changing anything, quarantines a failed one as `*.UNVERIFIED.db.bak` and rolls back on any failure; post-migration checks (obsolete columns gone, `confidence_level` NULL on every card, `integrity_check` ok, `foreign_key_check` empty, row counts unchanged); start with `PM_REQUIRE_EXISTING_DB=1`. Restore: with the app stopped, put back the verified external copy or the tool's verified `.bak` (never an `*.UNVERIFIED*` one) and deploy the previous code; confirm the hash, integrity and counts.

Validation reported by the owner for `175a549` (not re-run when this was written):
- Backend `python -m pytest -p no:cacheprovider -q` in `backend/`, Python 3.11.15: 2527 passed, 1 skipped (not identified), 0 failed, 670.82 s, 15 warnings; disposable virtual environment, `uv sync --frozen --extra dev`, `DATABASE_URL` and `SECRET_KEY` unset. Python 3.12: two full runs with 2524 passed, 3 failed, 1 skipped, the failures due to a missing editable install (needed by `python -I` subprocesses); the three files then passed (52 tests). No clean full run on 3.12.
- Frontend: `tsc --noEmit`, `npm run lint` ok; 137 unit tests (19 files); `npm run build` ok (`frontend/out`); `test_static_files.py` 10 passed.
- E2E: five relevant specs 21/21; full suite 94 passed, 2 skipped (setup tests in `auth.spec.ts`), 1 failed: `bid-import.undo.spec.ts:120`, pre-existing (same failure on `69e3419`), caused by `getByText` looking for text that exists only in the Undo button's `title`. Not fixed.
- Docker, 2026-10-19: `docker build -t pm-c6-local:175a549 .` exit 0, image id `832a396442f8`, about 307 MB, Linux ARM64, Python 3.11.17, some layers from cache. Disposable container: `/api/health` 200, `/api/mode` `{"auth":"legacy"}`, `/` 200, `/api/boards` without credentials 401 (expected). The container was removed; the image was kept locally.

Still pending, not validated: real SMTP and the real reset email, TLS and the production reverse proxy, the real deployment (including Linux host ownership of the mounted directory), central mode, and the migration of any real database. The local smoke test is not a deployment.

## Backup, migration and restore procedure (not executed; the commercial database was not touched)

Applies before `migrate_deal_years.py`, `migrate_deal_year_rollover.py`, `migrate_bid_cpm.py` and any import. Use exact absolute paths; never `pm.db`, never a relative path, never `DATABASE_URL` or a symlink (the scripts refuse them, `db_target_guard.validate_target`). Note the scripts pin the target only by absolute path and `--expect-sha256`; they pass no allow-list, so the hash is the identity check. Re-read the path aloud before every command.

1. **Stop the app** (`docker compose ... stop`, or stop the native process) and confirm nothing has it open (`lsof <file>`). A `-journal`/`-wal` sidecar next to the file means an unfinished write: the scripts refuse, and so should you.
2. **Identify the target:** `ls -l <abs path>`, `shasum -a 256 <abs path>`; compare with the last recorded hash for this database. If it differs from the recorded one, stop and find out why.
3. **Back up:** from `backend/`, `uv run python commercial_import.py backup --database <abs path> --dest <abs new file>` (SQLite online backup into a new file, never overwrites; verifies `integrity_check`, `foreign_key_check` and logical equality with the source). Record the backup's SHA-256 (it differs from the source's; logical equality is the criterion).
4. **Second failure domain:** the migration scripts also write `<name>-pre-<step>-<time>.db.bak` beside the database, which shares the disk with it. Copy the verified backup (and those `.bak` files) off that disk or host: `scp`/`rsync` to another server, or to an encrypted object store or an external drive kept elsewhere. Verify the copy by hashing it at the destination and comparing with step 3. Copying leaves the migration safeguards untouched; never move the original or the live database into the other domain.
5. **Dry run:** `uv run python migrate_deal_years.py --database <abs path> --year <Y>` (no `--apply`); read the counts. `--year` is the year the existing `deal_value` amounts belong to, and the operator must state it (normally the current calendar year); it cannot be inferred, and a wrong year mislabels every card. Rollover: `migrate_deal_year_rollover.py --database <abs path> --from-year <Y>` and only after `Y` has ended.
6. **Apply** with the hash from step 2: `... --year <Y> --expect-sha256 <hash> --apply`. Deal-year migration leaves every `deal_value` numerically unchanged, sets `deal_value_next_year` to 0 and `deal_year` to `<Y>` on every card, and rolls back if any other value changed. It refuses a second `--apply`.
7. **Verify:** re-run the dry run (reports nothing to do); `commercial_import.py check ...` or `sqlite3`: `PRAGMA integrity_check` is `ok`, `PRAGMA foreign_key_check` is empty; compare `SELECT COUNT(*), SUM(deal_value) FROM cards` with the same query on the backup (equal), and `SELECT COUNT(*) FROM cards WHERE deal_year <> <Y> OR deal_value_next_year <> 0` is 0. Start the app (`PM_REQUIRE_EXISTING_DB=1`) and check the login page and a card.
8. **Restore if needed:** stop the app; `commercial_import.py restore --backup <verified backup> --dest <new file>` produces a new verified file (restore never overwrites); then, as a deliberate step, move the damaged database aside (keep it) and put the restored file at the original path with the original ownership (`uid 10001`), and start the app. Re-hash and re-run `check`.

Limitations of rollover and history: the rollover is explicit and never automatic, so amounts keep their entered year until it is run. It overwrites `deal_value` with the next-year amount (NULL when that was 0): the closed year's amount survives only in the backups, not in the database. Stage-history snapshots (`card_stage_events`) are past events and are not rewritten, so closed-won history stays in the amounts as recorded; reports and KPIs read `deal_value` and stage history (`docs/KPIS.md`), so after a rollover year-over-year reports on current cards cannot be reconstructed from the live database. Keep the pre-rollover backup as the record of the closed year.

Practical schedule for the single-instance deployment (no service is added; plain cron on the host, using the same `backup` command, which opens the source read-only and is refused rather than unsafe if a write is in flight):

- daily backup at a quiet hour to a dated new file, retained 14 days, plus one weekly and one monthly kept longer; each also copied to the second failure domain and hash-compared;
- always an extra backup immediately before any migration, import or rollover, and before upgrading the image;
- monthly restore test: `restore` the newest off-host copy into a scratch path, check integrity, foreign keys and row counts against the source, log date and result; never test against the live path;
- alert (any mail or monitor you already have) when the newest backup is older than 26 hours.

## Cards Excel import ledger (central mode)

The Cards workbook import records every applied workbook (create or update) in `card_excel_imports`, one row per board and logical workbook (`repeat_guard`, unique per board). Operational consequences:

- The record is permanent. Deleting cards does not release their workbook: importing the same workbook again is refused with 409 `already_imported` (the message gives the import number and date). To import similar data, change a business value in the workbook. Restoring older values by re-importing an earlier workbook (A, B, A) is refused for the same reason.
- There is no undo or automatic restoration, and no purge or reset feature. Clearing a ledger row is a manual database operation on a backed-up company database and needs explicit authorization.
- Diagnosis: `changes_json` of the row holds the created card ids and the old/new values of every updated field; the preview and the 409 answer give the import number and time.
- Without the table the import answers 503 `import_not_initialized` (`applied: false`: nothing was read or written). Create it with `init_company_excel_imports.py` (dry run by default).
- If the table exists but does not have the current columns or the unique `(board_id, repeat_guard)` guard (a table made by an earlier build), the import answers 503 `import_ledger_incompatible` (`applied: false`) on preview and confirm and does nothing else. The application never alters, rewrites or purges the table; `init_company_excel_imports.py` only creates a missing table and will not repair this one. The repair is a separate, explicitly authorized, backed-up operator task.
- Fingerprint versions: the fingerprint includes its version (now 2). A ledger row recorded under version 1 has a different fingerprint, and rows from older builds have `repeat_guard` NULL, so the guard does not recognize them: a logical workbook whose only ledger record is a version-1 row can be applied once more, and then it is guarded normally. Nothing rewrites old rows: no automatic migration, purge, rewrite or reset is implemented. Whether any real database holds such rows has not been established (it cannot be known from the code, and none was inspected). Before a pilot or production import into an environment that may contain legacy ledger records, an authorized operator must perform the read-only check below on a backed-up copy; do not rely on the assumption that no such rows exist.
- Read-only check for an authorized operator, on a COPY of the company database taken after a backup (never `backend/pm.db`, never the live file): `sqlite3 <copy> "PRAGMA table_info(card_excel_imports); SELECT COUNT(*), SUM(repeat_guard IS NULL) FROM card_excel_imports;"`. A table without `repeat_guard`, or any row with `repeat_guard` NULL, needs review before importing a workbook that may have been applied before. The query returns counts only, no card data.
- Export and template downloads are Super Admin or the company Admin of that company (Part 2D-G12); other roles and other companies are refused before the company database opens.

## Central mode (Salcomp, Lity, Valor Log): configuration, users and boards (Part 2A; nothing here has been run on a server)

Status: prepared and tested in development only, on disposable databases. No commercial or production database was created or opened (disposable development databases were), no container was started, and the VPS was not touched. The first publication (C6 section above) is legacy-only; central mode is a separate release.

### Which file to use

`docker-compose.central.yml` is a standalone file, used **instead of** `docker-compose.yml` + `docker-compose.prod.yml`, never together with them:

```bash
docker compose --env-file /secure/pm-central.env -p pm-central -f docker-compose.central.yml config
```

Why a new file rather than editing the two existing ones: they are built around one legacy database (`DATABASE_URL`, a `pm.db` bind mount, `PM_REQUIRE_EXISTING_DB`). Compose cannot remove an inherited variable or mount through an override, so a central deployment built on them would still carry the legacy defaults, which is the silent fallback to `pm.db` / `pm-commercial.db` that must not exist. The two existing files are unchanged, so legacy behaviour is unchanged. The central file sets `PM_ENV=production` and `PM_AUTH_MODE=central`, has no `DATABASE_URL`, and every database variable is required with no default (compose refuses to run until each is named).

### Required variables (names and purpose; values live in the env file kept outside the repository)

| Variable | Purpose |
|---|---|
| `PM_IDENTITY_DATABASE_URL` (becomes `IDENTITY_DATABASE_URL`) | identity database: users, companies, memberships, invitations, tokens, audit. `sqlite:////data/<file>` (four slashes, container path) |
| `PM_COMPANY_DB_SALCOMP`, `PM_COMPANY_DB_LITY`, `PM_COMPANY_DB_VALOR_LOG` | the three operational databases, one file each, same URL form |
| `PM_CENTRAL_DATA_HOST_PATH`, `PM_CENTRAL_DATA_CONTAINER_PATH` | host directory holding the four files, and where it is mounted (for example `/data`); SQLite writes journal files beside the databases, so it is a directory mount, writable by uid 10001 |
| `SECRET_KEY` | JWT signing key, at least 32 characters, no default |
| `PM_PUBLIC_URL` | public https origin used in emailed invitation and reset links |
| `SMTP_HOST`, `SMTP_FROM` (and optional `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`) | invitations and password resets are emailed; without working SMTP nobody can be activated |
| `OPENROUTER_API_KEY` | optional, only for the AI assistant |
| `RATE_LIMIT_TRUSTED_PROXIES`, `PM_BEHIND_PROXY`, `CORS_ALLOWED_ORIGINS`, `HSTS_ENABLED` | optional, as in the legacy file |

Startup validation (`production_config.central_database_problems`, run in production when `PM_AUTH_MODE=central`; `DATABASE_URL` is not required and not consulted for anything but collision checks): all four variables must be set; each must be a plain SQLite file URL (no query string, no `..`, no `file:` URI); each file must already exist (nothing is created at startup); the four files must be distinct and distinct from `DATABASE_URL`'s file; and none may be a development or commercial database (`pm.db`, `pm.db.bak`, `pm-commercial.db`, the permanent Salcomp path) or a SQLite sidecar. Every problem is reported at once and the app does not start. Legacy mode is unchanged: it still requires `DATABASE_URL` and `PM_REQUIRE_EXISTING_DB=1`. Not verified: file ownership on a Linux host, the TLS proxy, real SMTP, and an actual start of the container.

### Users, roles and invitations

- **Who may invite:** only the global administrator (Super Admin, `identity_users.is_global_admin`; reported as role `super_admin` in every company; created with `identity_admin_cli.py`, no HTTP path grants it). There is no public sign-up in central mode and the legacy `SIGNUP_ALLOWED_*` domain list does not apply: any email address may be invited, because Lity and Valor Log users may use other domains. Whether to restrict invitations to approved domains is a business decision that has **not** been taken; the behaviour is unchanged (the Super Admin decides each address).
- **How:** after signing in, the Super Admin selects a company; the "Users of <company>" panel has an email field and a role (Sales Member or Visitor) and sends `POST /api/central/companies/{slug}/invitations`. The single-use link (`/?central_invite_token=...`, valid 7 days) is emailed and never shown or logged; only its SHA-256 is stored.
- **Accepting:** the link opens "Accept your invitation". A new person chooses a password (8 to 128 characters with upper-case, lower-case, number and symbol) and the account, verified email and membership are created in one transaction. A person who already has an account uses "I already have an account" and proves it with their own email and password (`/invitations/join`) to join another company. Invalid, expired, revoked and already-used links all give the same answer. The page removes the token from the address bar immediately and keeps it in memory only; it is never written to browser storage.
- **Password reset:** "Forgot my password?" on the sign-in page sends a single-use one-hour link (`/?central_reset_token=...`); the answer is the same for every address.
- **Roles:** Super Admin (global, all companies, administers boards, columns, cards, imports, products, members); Sales Member (`sales_member`: edits and moves only their own cards, moves forward only); Visitor (`visitor`: read only). The company-level `admin` role exists in the schema but is reserved: the interface never offers it and no invitation can carry it (the Super Admin's role-change API, `PUT /api/central/companies/{slug}/members/{id}/role`, does accept it, but nothing in the interface calls it that way). The Super Admin can change a member between Sales Member and Visitor and deactivate a membership; a Sales Member or Visitor cannot see or call any of this (403).
- **Availability for assignment:** a person is assignable in a company when the central database says they are an active Sales Member there (or an active Super Admin). Before the Cards import validates `salesperson_email`, the import preview and confirmation create the missing company-local reference rows of exactly those people (idempotent, one row per person, no role or credential stored, nothing for visitors, deactivated members or other companies), so an invited salesperson who has not yet written anything is not rejected. The import is still all-or-nothing and the email rules are unchanged. Remaining limitation: assigning a card by hand (card edit, lead options list) still needs the local row, which exists once the person has written in the company or after any Cards import preview of that company.

### Boards and columns

No initializer creates a board or a column, and none is added automatically. The Super Admin prepares each company in the app: pick the company, create a board (any title), then add stages. Nothing depends on a fixed board id; the Excel template and export are per board and the Cards import matches the workbook's `stage` against the board's column titles (case-insensitive). A stage kind is `open`, `won` or `lost` (at most one won and one lost per board). The recommended starting set, the same as the legacy seeded pipeline, is: `1st Contact` (open), `F2F Meeting` (open), `RFI` (open), `Proposal` (open), `Award` (won), `Lost` (lost). It is created by hand through the interface, once per company, so it cannot be duplicated by a restart; the existing stages of a board are never changed except by the Super Admin through the same screen.

### Starting central mode natively on the Mac (without Docker)

Written from the code (`auth_mode.py`, `database_targets.py`, `identity_database.py`, `identity_admin_cli.py`, `mailer.py`, `production_config.py`, `main.py`); not executed as part of writing this section. This is the central-mode counterpart of "Running natively against the local commercial database" above, which is legacy mode. Do not use `scripts/start-mac.sh` for central mode: it exports the development `.env` (including `DATABASE_URL`) and splits values on spaces.

**How configuration reaches the process.** The backend never loads `.env` or any other file. Every variable below must already be in the environment of the process that starts `uvicorn` (and, separately, of the process that runs `identity_admin_cli.py`). Docker names (`PM_IDENTITY_DATABASE_URL`, `PM_PUBLIC_URL`, `PM_CENTRAL_DATA_*`) are compose-only; a native process reads the names in the table.

**Keep the settings outside the repository.** Put them in a file of your own, outside the repository, readable only by you, and never commit it (and never commit a `.env`). `<EXTERNAL_ENV_FILE>` below is a placeholder you replace with that path:

```bash
chmod 600 "<EXTERNAL_ENV_FILE>"
```

The file holds plain `NAME=value` lines (quote a value that contains spaces or `#`). Load it into the current shell with `set -a` so every line is exported, without printing anything, and check only that the names are set, never their values:

```bash
set -a; source "<EXTERNAL_ENV_FILE>"; set +a
for v in PM_AUTH_MODE IDENTITY_DATABASE_URL PM_COMPANY_DB_SALCOMP PM_COMPANY_DB_LITY PM_COMPANY_DB_VALOR_LOG SECRET_KEY APP_BASE_URL SMTP_HOST SMTP_FROM; do
  if [ -n "$(printenv "$v")" ]; then echo "$v: set"; else echo "$v: MISSING"; fi
done
```

The loop works in Bash and zsh and prints only the variable name and `set` or `MISSING`; the value is captured inside `$(...)` and never shown. It checks presence only: `set` means the variable exists and is not empty, not that its value is valid, and `MISSING` means unset or empty. `SMTP_FROM` is in the loop for convenience; it may show `MISSING` if `SMTP_USER` is set (see the table). Do not run `env`, `printenv` without a name, `set` or `export -p` to check: they print secrets. `source` runs the file as shell code, so use only a file you wrote yourself, at a path you typed. Loading applies to this shell only; open a new terminal tab and every variable must be loaded again.

| Variable | Native value |
|---|---|
| `PM_AUTH_MODE` | `central` (unset means legacy) |
| `IDENTITY_DATABASE_URL` | `sqlite:////<absolute path to the identity database file>` (four slashes) |
| `PM_COMPANY_DB_SALCOMP`, `PM_COMPANY_DB_LITY`, `PM_COMPANY_DB_VALOR_LOG` | same URL form, one distinct file each |
| `SECRET_KEY` | at least 32 characters, no default |
| `APP_BASE_URL` | origin the emailed links open (below) |
| `SMTP_HOST` | required for any email to leave |
| `SMTP_FROM` | sender address; optional when `SMTP_USER` is set (the mailer uses `SMTP_USER` as the sender), otherwise one of the two must provide it. Production validation requires one of them |
| `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | optional: port (default 587, STARTTLS), login user, and password (required whenever `SMTP_USER` is set) |
| `OPENROUTER_API_KEY` | optional, only for the AI assistant |

`DATABASE_URL` is not used by central mode (only to check that no central file collides with it); leave it out of the file so the legacy database is never in play. The file does not clear a variable that the shell already exports: if `DATABASE_URL` is already exported in this shell, run `unset DATABASE_URL` before starting the backend or the CLI (do not print it). Central mode never falls back to it, but a set value is still parsed, and it is refused if it is malformed or equals one of the four central files. `PM_ENV` is not set for local development (unset means development); with `PM_ENV=production` the stricter checks in "Startup validation" above apply, including an https `APP_BASE_URL`.

**Database paths.** Each URL must name a file that already exists, with the schema already created: neither the app nor the CLI creates a database (the identity engine opens with `mode=rw`, and a missing file or a schema version mismatch is refused). When this is detected depends on the mode: with `PM_ENV=production`, startup validation checks that the identity and company files exist and refuses to start; in development (`PM_ENV` unset) startup only checks that the four variables are set, the identity database is opened on the first central request, and each company database on the first request that needs it, so a wrong or missing path can show up later as a `503` ("Central authentication is unavailable") or a failing company request instead of a startup error. Point them at the intended local development databases that you prepared for this purpose with `create_identity_db.py` and `create_company_db.py`, not at temporary test databases (a throwaway file loses the administrator and invitations at the next cleanup) and **never at a production or commercial database**: the app refuses `pm.db`, `pm.db.bak`, `pm-commercial.db`, the permanent Salcomp path and SQLite sidecar files, but anything else you name is opened for writing, and a production copy used for development gets real invitations, resets and card edits. This document does not name a path for you; the four files must be distinct.

**An existing identity database is not necessarily bootstrapped.** `create_identity_db.py` creates an empty database: no users and no companies. The global administrator and the three companies exist only if `identity_admin_cli.py bootstrap` was run against that file at some point (it is idempotent and refuses a different existing account with the email). Before using an identity file, know which of your existing development identity databases holds the administrator you intend to use. Pointing at an empty one means `send-activation` answers `not eligible` and nobody can sign in. This section does not run or recommend `bootstrap`, and nothing here inspects a database.

**`APP_BASE_URL` for local use.** It is the origin placed in every emailed link (`/?central_reset_token=...`, `/?central_invite_token=...`). Use the origin from which you open the interface: `http://localhost:3000` when running the frontend with `npm run dev` (the default if unset, and the origin the development CORS allow-list expects), or `http://localhost:8000` only when `backend/static` holds a frontend export so FastAPI serves the UI. The link must open the page you are actually using, or the single-use token is spent on the wrong place. `PM_PUBLIC_URL` is the compose-side name for the same value; do not set it for a native start (in production the app refuses a `PM_PUBLIC_URL` that differs from `APP_BASE_URL`).

**SMTP.** Without `SMTP_HOST` nothing is sent and the link cannot be recovered from the logs, so no activation, invitation or reset email is delivered. Nothing connects to the SMTP server at startup; the first real attempt is the first email.

**Start the backend** (start each command block below from the repository root, in the shell where the file was loaded; the parentheses run `cd` in a subshell, so your shell stays at the root and the block can be repeated or followed by the next one):

```bash
(cd backend && uv run uvicorn main:app --port 8000)
```

Add `--reload` only if you want auto-reload, and run the frontend separately with `npm run dev` in `frontend/` when using `http://localhost:3000`. Startup validation (`auth_mode.validate_config`) stops the process, naming the missing variable, if any of the four database variables is unset; it does not run `init_db()` in central mode. Environment changes are read once at process start, so after editing the file stop the backend, load the file again in that shell and start it again.

**Activation email for the global administrator.** `identity_admin_cli.py` reads the same environment variables (it opens the identity database from `IDENTITY_DATABASE_URL` and sends through `mailer.py` with `SMTP_*` and `APP_BASE_URL`); it does not read the file. Run it in a shell where the file was loaded:

```bash
(cd backend && uv run python identity_admin_cli.py send-activation --email "<address>")
```

It prints `sent` or `not eligible (nothing sent)` (the account must exist, be active and have a verified email; `bootstrap --admin-email <address>` creates the global administrator and the three companies in an existing identity database, and is idempotent). `sent` means only that the mailer returned without raising an error. It does not confirm SMTP acceptance or inbox delivery. In particular, if `SMTP_HOST` is unset the mailer logs a warning and returns normally without sending anything, and the CLI still prints `sent`; check the output for the `Email not sent (SMTP_HOST not set)` warning. The link is never printed. Each run issues a fresh one-hour link and invalidates the earlier ones, even if the email then fails.

**Safe SMTP diagnostics.** The log carries only a masked recipient, the subject and the error class, never a link, body or credential:

- `Email sent: to=a***@domain subject=...`: accepted by the SMTP server (this does not prove inbox delivery).
- `Email not sent (SMTP_HOST not set)`: the variable is not in this process's environment; load the file and restart.
- `Email delivery failed: ... error=<ExceptionClass>`: connection, TLS (certificate or hostname verification, STARTTLS on the port) or login failed. Check host, port and credentials in the file, without printing them.

The forgot-password screen answers identically for every address, so it cannot show success; read the backend log. For `send-activation` the CLI prints `sent` or `not eligible`. It configures no logging, so only warnings and errors from the mailer are shown (by Python's default handler, on stderr); the `Email sent` line is informational and does not appear. On an SMTP delivery exception the mailer logs the `Email delivery failed` line and the CLI then exits with a Python traceback (`EmailDeliveryError`, which carries no detail) and prints no `sent`. Treat `sent` without a preceding `Email not sent` or `Email delivery failed` line as "no error was raised", not as delivery. Never paste a reset link or an email body into a log, ticket or chat.

Limits: the commands above were derived from the code and have not been run for this section; real SMTP delivery and STARTTLS verification against the real server, and the real activation, remain unverified (see the other sections).

### Pending decisions and limits

- Invitation domain policy (above); Admin-per-company role (reserved, no privileges); manual assignment before first write (above); real SMTP, TLS proxy, Linux ownership of the mounted directory and the real start of the stack are unverified; the full backend suite has not been run for this change.
