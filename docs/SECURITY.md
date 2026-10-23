# Security Operations Guide

How to configure secrets, provision administrators, keep test and real credentials apart, and validate the security controls. For the code-level details see `CLAUDE.md`.

## 1. SECRET_KEY

`SECRET_KEY` signs every JWT: login tokens and the signup/reset links sent by email. Anyone who knows it can forge a login as any user, admins included.

**The app has no fallback.** `auth.load_secret_key()` runs when `auth.py` is imported. It refuses to start (raises `InsecureSecretKeyError`) when the key:
- is missing or empty;
- is a known insecure value: the old hardcoded default (public in this repo's history) or the `.env.example` placeholder;
- is shorter than 32 characters.

The error message never includes the value. The check covers the API server and every script that imports `auth` (e.g. `admin_cli.py`).

### Setting it locally

Run this from the repository root. It appends a random key to `.env` without printing it to the terminal:

```bash
printf 'SECRET_KEY=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" >> .env
grep -c '^SECRET_KEY=' .env    # should print 1 - counts the line without showing it
```

Then restart the app (`./scripts/start-mac.sh` loads `.env`).

- Don't paste the key into chats, issues or logs.
- Don't commit `.env`; it's already in `.gitignore`.
- Use a different key per environment (dev, Docker, production).

### Docker

`docker-compose.yml` passes the key in with `${SECRET_KEY:?...}`, so `docker compose up` itself fails when the key isn't set. The `Dockerfile` defines no default. Docker Compose reads the same root `.env`.

### Effects of setting or changing the key

- Every existing login token becomes invalid. Users get a 401 and the frontend sends them back to the login screen.
- Signup and password-reset links sent before the change stop working, and must be requested again.
- Nothing in the database needs to change.

## 2. Administrators

### How admin rights are granted

| Path | Result |
|---|---|
| `POST /api/setup` | Creates the **first** user as admin. Closed (403) unless `PM_SETUP_TOKEN` (>= 32 chars) is set and the caller sends it as `X-Setup-Token`; then only while the users table is empty (409 afterwards) |
| `POST /api/users` (admin only) | Takes an email only. Always creates a member, unverified and without a usable password, and emails a single-use link (the reset flow) that verifies the address and sets the first password; a `role` or `password` field in the request is ignored |
| `POST /api/signup/complete` (public, allowed domains only) | Always creates a member; a `role` field in the request is ignored |
| `backend/admin_cli.py` | The only way to add or change admins after setup |

There is no HTTP endpoint that promotes users. The authorization check for `admin_cli.py` is operator access to the server's shell and database.

### Enabling `/api/setup` (first-run HTTP provisioning)

Off by default and fails closed: without `PM_SETUP_TOKEN` (at least 32 characters; shorter counts as unset) every call answers 403, whether or not the database is empty. A missing database never enables it. The preferred provisioning path remains `create_commercial_db.py` / `admin_cli.py create --no-password`, which need no HTTP exposure.

To use HTTP setup, for the provisioning window only:

1. Generate a key outside any file you commit and export it for the server process, e.g. `export PM_SETUP_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"` (Docker: put it in the `.env` that compose reads). Keep a copy to send below.
2. Start the backend and call `POST /api/setup` with header `X-Setup-Token: <key>` and body `{"username": ..., "password": ...}` (the key is never read from the body or query string, and is compared in constant time).
3. Unset `PM_SETUP_TOKEN` and restart. Setup also refuses by itself (409) once any user exists, so a leftover key cannot create a second admin, but do not leave it configured.

`GET /api/setup/status` reports `needs_setup` (no users yet) and `setup_enabled` (a valid key is configured). The browser setup screen has a "Setup key" field for the same header. Failed attempts count against the `login_ip` rate limit.

### `admin_cli.py`

Run from `backend/`, with `SECRET_KEY` set in the environment (the script imports `auth`):

```bash
uv run python admin_cli.py create  --username <name>    # prompts twice for the password, no echo
uv run python admin_cli.py create  --username <email> --no-password   # no usable password; the owner sets it via "Forgot my password?"
uv run python admin_cli.py promote --username <name>    # asks you to type the username to confirm
uv run python admin_cli.py demote  --username <name>    # same confirmation; --yes skips it
```

- **Passwords:** never accepted as command-line arguments, because those end up in shell history and the process list. For automation, use `--password-stdin` and pipe the password in from a secret store. The script prints nothing but usernames and roles.
- **`create`:** refuses an existing username, so it can't take over an account or reset its password.
- **`promote`:** works only on a named, existing user.
- **`demote`:** refuses to remove the last admin.
- **Password policy:** a given password must meet `password_policy.py` (8-128 characters with an upper-case letter, a lower-case letter, a number and a symbol). `--no-password` needs a lowercase email address as the username.
- **Reserved names:** `e2e_admin` and `e2e_member` (public passwords) cannot be created outside the isolated E2E backend.
- **Database:** the script runs `init_db()` against the database in `DATABASE_URL` (default `backend/pm.db`). `init_db()` refuses a missing, empty, legacy or incomplete database instead of creating or migrating it.

### Replacing the `e2e_admin` admin in the development database

`e2e_admin` was created by the Playwright suite. Its password is in `frontend/tests/helpers.ts` in a public repository, so it must not stay an admin of any database that holds real data. The tooling doesn't change it automatically. To replace it:

1. Set `SECRET_KEY` (section 1).
2. Back up the database: `cp backend/pm.db "backend/pm-manual-$(date +%Y%m%d%H%M%S).db.bak"` (the `.db.bak` suffix is what `.gitignore` ignores).
3. Create your admin: `cd backend && uv run python admin_cli.py create --username <you>`.
4. Log in as `<you>` in the app and check that "Manage Users" is visible.
5. Demote the test account: `uv run python admin_cli.py demote --username e2e_admin`.

The E2E suite no longer uses `backend/pm.db` (next section): it creates `e2e_admin` in its own throwaway database on every run, so demoting the account in the dev database doesn't affect the tests.

## 3. Test vs. real credentials

| Credential | Where it lives | Use it for |
|---|---|---|
| `e2e_admin` / `e2e_member` + their passwords | `frontend/tests/helpers.ts` (public) | **Only** throwaway e2e databases |
| `SECRET_KEY` for backend tests | Generated at random per pytest run in `backend/conftest.py` | Nothing else; never stored |
| Your real admin | Created with `admin_cli.py`; password only in your password manager | Development and production databases |
| Real `SECRET_KEY` | `.env` (not committed), or the deployment's secret store | One per environment |

`backend/conftest.py` also points `DATABASE_URL` at a temp file for every pytest run. So no backend test, including one that forgets to override `get_db`, can reach `backend/pm.db`.

The Playwright E2E suite is isolated (Part 17):
- `playwright.config.ts` starts its **own** backend on `:8001` with `PM_E2E=1` and a fresh SQLite file in a new `pm-e2e-*` temp dir. It also generates a random `SECRET_KEY`, which is never printed, and clears `SMTP_HOST` and `OPENROUTER_API_KEY`, so tests send no email and make no paid AI calls.
- It starts its own frontend on `:3001`, with build dir `.next-e2e`.
- Neither server is ever reused.
- With `PM_E2E=1` the backend refuses any `DATABASE_URL` that isn't an explicit file inside the system temp directory, so both `pm.db` files are out of reach.
- `tests/helpers.ts` has no fallback URL.
- `tests/e2e-setup.ts` aborts the run unless `/api/health` reports `test_mode: true`. After the run it fails if either development `pm.db` changed, and it deletes the temp database.

```bash
cd frontend
npm run test:e2e                     # or: npx playwright test [file] [-g "name"]
```

The dev servers (`:3000`/`:8000`) can stay running. If you use the dev app during an E2E run and change its data, the final check will report that `pm.db` changed. `playwright.docker.config.ts` refuses to run until a Docker test profile with `PM_E2E=1` exists.

## 4. How permissions are enforced

Every check is in the backend; hiding a button in the UI is never the control.

| Operation | Who | Where |
|---|---|---|
| Read boards, columns, cards, reports, lead options | Any authenticated user | `deps.get_current_user` |
| Create / update / delete **boards and columns** | Admin only (members get 403) | `deps.require_admin` on the six routes in `routes.py` |
| Create cards | Any authenticated user; a member's card is assigned to its creator | `card_rules.new_card_fields` |
| Edit cards and leads | Admin: all. Member: only cards assigned to them (member edits logged). Unassigned: admin only. Checked against the card's current database row | `card_rules.can_edit_card` / `apply_card_updates` (REST, AI, undo) |
| Assign or reassign a card (`salesperson_id`) | Admin only; audited in `audit_events` | `card_rules.apply_card_updates` |
| Move cards forward | Any authenticated user (unchanged rule; not ownership-bound) | `card_rules.can_move_card` |
| Move cards backward, delete cards | Admin only; deletions audited | `card_rules.py`, `routes.delete_card` (REST and AI) |
| Undo | Only the user's own last card change, and only while they may still make it | `undo.py` |
| Manage users | Admin only | `require_admin` on `/api/users` |
| `POST /api/ai/test` (paid OpenRouter call) | Admin only | `require_admin`, checked before any OpenRouter request |
| AI chat (`POST /api/boards/{id}/ai`) | Any authenticated user; its actions follow the card rules above | `ai_kanban.apply_board_actions` |
| Salcomp Bid import: preview, confirm, batch list and details (`/api/boards/{id}/imports*`, `/api/imports/{id}`) | Admin only; checked before the upload is read; confirmation needs a signed preview token for the same admin, board, file and decisions | `require_admin` in `import_routes.py` |

Upload limits (Bid import): the routes take a bare `Request`, not `File`/`Form` parameters, so FastAPI parses no body before `require_admin` runs; the form is read only afterwards. `Content-Length` is required (411 otherwise, so chunked uploads are refused) and capped at the file limit plus 1 MiB (413). Because that header is only a claim, the bytes actually received are also counted and the upload is cut off with 413 once they pass the same cap. Limits: Starlette 0.41 sets no size cap on file parts, only on text fields (1 MiB), so the counter is what bounds memory and temporary-file disk use. Starlette closes a part's temporary file itself only when its parser rejects the body, so `_read_form` drives the multipart parser and closes the files on every other failure too (size cut-off, dropped connection) and closes the form on every path out, instead of waiting for garbage collection. When deployed behind a proxy, also set its body limit (e.g. nginx `client_max_body_size`) a little above the cap so oversized bodies never reach the app.

The AI path can only perform card actions. Structural requests (`delete_column`, `rename_column`, `delete_board`, …) are refused as "Unsupported action type".

## 5. Validating the controls

From `backend/` (uses only temp/in-memory databases):

```bash
uv run pytest test_security.py -v   # authorization, SECRET_KEY startup, provisioning, /api/ai/test
uv run pytest                       # full suite; 3 failures in test_ai.py/test_static_files.py predate this work
```

`test_security.py` covers:
- members get 403 on every board/column write;
- unauthenticated requests get 401;
- admins can still change the structure;
- the AI refuses structural actions, and the card rules are unchanged;
- startup is refused without a key, with an empty, default, placeholder or short key, and the key is never echoed;
- startup succeeds with a valid key, and tokens signed with the old default are rejected;
- signup and admin user creation can't grant admin, and setup can't run twice;
- `admin_cli.py` covers create, stdin input, password mismatch, takeover refusal, confirmation, last-admin protection and the replacement procedure;
- `/api/ai/test` rejects unauthenticated and member requests without calling OpenRouter.

## 6. Known remaining risks

See `CLAUDE.md` → "Status and known gaps":
- rate limits are per process and in memory (section 7): lost on restart, not shared between workers or replicas;
- no limit on `/api/password/reset`, `/api/signup/complete` or `/api/setup` (their tokens are random 256-bit values stored hashed; setup works once, while no user exists);
- login tokens are kept in localStorage and live 8 h. Revocation is per user, not per token: logout bumps `users.session_version`, ending **all** of that user's sessions on every device, and a password change or reset does too (each access token carries `sv` and `pwh`; tokens without them are rejected);
- Manage Users creates the account unverified and without a usable password; the invitee proves the mailbox by following the emailed single-use link (the reset flow), so no one can sign in as an invited address until then. Without working SMTP an invitation cannot be completed;
- E2E tests run serially on one shared throwaway board (isolated from real data, but not from each other).

## 7. Rate limiting

Implemented in `backend/rate_limit.py`: sliding-window counters in process memory. Over a limit the API answers `429` with `Retry-After` and the same body for every cause, so a limit never says whether an account exists. Limits are checked before any account lookup, and blocked requests are not counted (hammering a limited key doesn't extend the limit).

| Endpoint | Key | Default | Variable |
|---|---|---|---|
| `POST /api/login` | client address, every attempt | 30 / 300 s | `RATE_LIMIT_LOGIN_IP` |
| `POST /api/login` | progressive delay per (typed username/email, client address), failures only; see below | 3 free / 5 s first delay / 300 s cap / 900 s reset | `RATE_LIMIT_LOGIN_BACKOFF` |
| `POST /api/signup` | client address | 5 / 3600 s | `RATE_LIMIT_SIGNUP_IP` |
| `POST /api/signup` | email address (also limits mail to one inbox) | 3 / 3600 s | `RATE_LIMIT_SIGNUP_EMAIL` |
| `POST /api/password/forgot` | client address | 10 / 3600 s | `RATE_LIMIT_FORGOT_IP` |
| `POST /api/password/forgot` | account, counted only when a reset mail is actually issued; over it the answer is the same generic 200 with no mail | 3 / 3600 s | `RATE_LIMIT_FORGOT_ACCOUNT` |
| Bid import preview and confirm | admin user (shared bucket) | 10 / 60 s | `RATE_LIMIT_IMPORT_USER` |
| `POST /api/boards/{id}/ai` | authenticated user, burst | 10 / 60 s | `RATE_LIMIT_AI_USER` |
| `POST /api/boards/{id}/ai` | authenticated user, hourly ceiling | 100 / 3600 s | `RATE_LIMIT_AI_USER_HOUR` |

**Login failure delay.** There is no account-wide lockout: nobody can block the administrator (or anyone) by typing their name, because the failure state is keyed on the typed identifier (case and spaces ignored) **and** the caller's address (`client_ip`, so an IPv6 /64 counts as one). Per client and account:

| Failure | Result |
|---|---|
| 1-2 | `401`, no delay |
| 3 | `401`, then that client is refused with `429` + `Retry-After` for 5 s |
| 4, 5, 6, 7... | delay doubles: 10 s, 20 s, 40 s, 80 s, 160 s, then stays at the 300 s cap |

While a delay runs, even the correct password gets the same generic `429` (otherwise the delay would not slow guessing). Refused attempts are not counted and do not extend the delay. The state resets on a successful login from that client for that account, or after 900 s without a failure from it. There is no administrator exemption and no trusted-address exemption: success only clears the state of the client and account that succeeded. `RATE_LIMIT_LOGIN_BACKOFF` is `free/first/cap/reset` in seconds (the first delay applies at the `free`-th failure; `cap` >= `first`). The per-address `login_ip` limit above still applies to every attempt. Limitation: guessing spread over many addresses is slowed only by each address's own delay and the `login_ip` limit, not by a shared account counter (a shared one is exactly what allowed lockout abuse); use a strong password and the proxy-level limits (item 6 below).

Values are `attempts/seconds`; a malformed value stops the app at startup. `RATE_LIMIT_ENABLED=false` turns everything off (logged as a warning; only the E2E backend does this).

- **Account lock:** after the failure limit, logins for that name are refused, correct password included, until the window passes. A successful login clears the count. The cost is that someone can lock a known account out for 15 minutes; the benefit is that guessing spread over many addresses is capped.
- **Bid import:** the limit runs after the admin check, so anonymous and member calls get 401/403 and never use the counters.
- **AI chat:** the limit runs after authentication (anonymous callers get 401 and never touch the counters) and before any board lookup, validation or model call. Both windows are checked before either is counted, so a refused request uses no quota; requests that are then rejected for content (empty or too-long message) do count. Each message is a paid OpenRouter call, so the limits are per user, not per address.
- **IPv6:** clients are keyed by their /64, so rotating addresses inside it doesn't dodge the limit.

### Production requirements

1. **Direct exposure (no proxy):** nothing to set; the TCP peer address is used and `X-Forwarded-For` is ignored.
2. **Behind a reverse proxy / load balancer:** set `RATE_LIMIT_TRUSTED_PROXIES` to the proxy's address or CIDR (comma-separated). Without it every user shares the proxy's address and the IP limits hit everyone at once. With it, `X-Forwarded-For` is trusted only when the direct peer is in that list, and read right to left skipping trusted proxies, so a client-supplied entry cannot choose its own address. The proxy must append the address it received the connection from to `X-Forwarded-For` (the default for nginx `$proxy_add_x_forwarded_for`, Traefik, ALB). Do not list wide ranges that untrusted hosts can reach. A malformed entry or a catch-all (`0.0.0.0/0`, `::/0`) stops startup in every environment. In production, `PM_BEHIND_PROXY=true` declares that a proxy is in use and then requires a non-empty `RATE_LIMIT_TRUSTED_PROXIES`; without the declaration production accepts direct access with no proxy list (the app cannot detect a proxy by itself). `docker-compose.yml` passes `PM_BEHIND_PROXY` to the container. `PM_BEHIND_PROXY` accepts `1/true/yes/on` or `0/false/no/off` (or unset) and `PM_ENV` accepts `production` or `development` (or unset); any other value stops startup. In Docker the proxy's address is the one the container sees (often a bridge address such as `172.x`); check it before setting. `docker-compose.yml` passes `RATE_LIMIT_TRUSTED_PROXIES` through to the container; how to find the address and verify it is in `docs/DEPLOYMENT.md`.
3. **One worker, one replica:** the counters live in the process. The image runs a single uvicorn worker, which matches SQLite. Running several workers or replicas multiplies every limit by their number; moving to more than one needs a shared store (e.g. Redis), which is not implemented.
4. A restart clears all counters.
5. Memory is bounded (50,000 keys); past that the oldest keys are dropped.
6. Also rate limit at the proxy (login/signup/forgot paths) as a second layer; this module is defense in depth, not DDoS protection.

Tests: `uv run pytest test_rate_limit.py -v` (limits, progressive login delay, recovery after the delay, proxy/header spoofing, config validation, import permissions).

### AI chat size limits (`backend/ai_limits.py`)

| Limit | Default | Variable | Over the limit |
|---|---|---|---|
| Characters in one message | 4000 | `AI_MAX_MESSAGE_CHARS` | `422` "Message too long (maximum N characters)" |
| Request body | 32768 bytes | `AI_MAX_REQUEST_BYTES` | `413` "Request too large." |

4000 characters is several pages of text, far above any lead note or instruction the assistant needs, and bounds the tokens sent to the model per call. The byte cap leaves room for a maximum-length message even if every character is JSON-escaped (`\uXXXX`, 24 KB). It is enforced by a middleware scoped to the chat path that checks `Content-Length` and, for chunked bodies, counts bytes while reading, so nothing larger is parsed. Empty or whitespace-only messages and non-string values are rejected with `422`. Errors never quote the message or any internal detail. Malformed values stop the app at startup. Conversation history sent to the model stays bounded by the existing 10-message window. Tests: `uv run pytest test_ai_limits.py -v`.

## 8. Public signup domains (SIGNUP_ALLOWED_DOMAINS)

`POST /api/signup` and `POST /api/signup/complete` accept only emails whose domain is listed in `SIGNUP_ALLOWED_DOMAINS` (`backend/signup_policy.py`). Anyone else gets `403`.

- **Fail closed:** unset or empty means public signup is disabled (the app starts and logs a warning; admins still create users in Manage Users). There is no default domain.
- **Format:** comma-separated plain domains, e.g. `SIGNUP_ALLOWED_DOMAINS=salcomp.com,partner.example`. Entries are trimmed and lowercased (IDN names become punycode). An entry with `@`, `*`, a port, an empty label or a single label (`com`) makes the app refuse to start.
- **Exact match only:** `salcomp.com` does not allow `sub.salcomp.com` or `evilsalcomp.com`; list each subdomain you want. The address is compared after lowercasing, so `User@SALCOMP.COM` is allowed and `x@salcomp.com.evil.io` is not.
- **Both endpoints check it:** the link request and the completion, so a link issued before the list changed (or forged for another domain with a leaked key) cannot create an account outside it. The flow is otherwise unchanged: the request only emails a link, and the account is created when the link is completed.
- **Individual exceptions:** `SIGNUP_ALLOWED_EMAILS` is a comma-separated list of full addresses (e.g. `pluisada@gmail.com`). Each allows only that address, compared after lowercasing: other addresses at the same provider, and `+tag` or dotted variants, stay refused. A malformed entry makes the app refuse to start. Signup is closed only when both settings are empty.
- Change it in `.env` (docker: `SIGNUP_ALLOWED_DOMAINS`, `SIGNUP_ALLOWED_EMAILS`) and restart. Existing accounts are not affected.
- **Approved policy:** `SIGNUP_ALLOWED_DOMAINS=salcomp.com` and `SIGNUP_ALLOWED_EMAILS=pluisada@gmail.com`. The exception never grants admin: signup always creates a member, and the only admin path is `admin_cli.py`. The code does not hard-code these values; check your local `.env`.
- **Email verification is mandatory:** a signup request creates no user. The account is created, already verified, only when the emailed single-use link is completed; a login or token for an account with `email_verified_at` NULL is refused.

## 9. Container, CORS and security headers

Implemented in `Dockerfile`, `docker-compose.yml` and `backend/http_security.py`. The image build, unprivileged user, dropped capabilities, healthcheck and bind-mount persistence were verified on Docker Desktop (macOS) on 2026-10-15 (`docs/DEPLOYMENT.md`). **Not verified:** any Linux host, the TLS reverse proxy and the checklist in `docs/DEPLOYMENT.md` "Verify on the real server"; treat those as prerequisites for go-live.

### Container (A5)

- Runs as an unprivileged user (uid/gid 10001, no shell, no home). `/app` is owned by it because SQLite writes its journal next to the database. The mounted database must be writable by uid 10001.
- `cap_drop: ALL` and `no-new-privileges`; `/tmp` is a tmpfs (Bid import temporary files).
- Port 8000 is published on `127.0.0.1` by default (`PM_BIND_ADDRESS`); put a TLS reverse proxy in front. Set `PM_BIND_ADDRESS=0.0.0.0` only to expose it directly, which also means no TLS.
- `healthcheck` calls `/api/health` with the image's Python (no extra tools in the image).
- Not done: `read_only: true`. With the default `./pm.db` file mount SQLite cannot create its journal in a read-only `/app`. With the commercial database (a mounted directory) it can be enabled once verified: add `read_only: true` and keep the `/tmp` tmpfs.
- Not done: the base images are not pinned by digest and the Python dependencies are installed with `pip` from `pyproject.toml` (pinned versions, no hash check).

### CORS (A6)

In production the app serves the frontend and the API from one origin, so **no CORS permission is needed**. Before this change any origin was allowed (`*`, with credentials flag).

- `CORS_ALLOWED_ORIGINS`: comma-separated explicit origins (`https://host[:port]`). Unset = the development origins `http://localhost:3000` and `http://127.0.0.1:3000`. Set but empty = none (same-origin only; `docker-compose.yml` passes it through empty by default). `*`, wildcards, paths and credentials in an origin stop the app at startup.
- Methods `GET POST PUT DELETE OPTIONS` and headers `Authorization`, `Content-Type` only (what the frontend uses). `allow_credentials` is off: authentication is a Bearer header, not a cookie.
- If the frontend is ever hosted on a different origin than the API, set `CORS_ALLOWED_ORIGINS` to that exact origin. The production URL is not assumed anywhere.
- The Playwright backend sets it to its own frontend origin (`127.0.0.1:3001`).

### Security headers (A7)

Added to every response by `SecurityHeadersMiddleware` (API, static files, errors, preflight); a header the app already set is not overwritten.

| Header | Value | Why |
|---|---|---|
| `Content-Security-Policy` | `default-src 'self'`; `script-src`/`style-src` `'self' 'unsafe-inline'`; `img-src`/`font-src` `'self' data:`; `connect-src 'self'`; `object-src 'none'`; `base-uri 'self'`; `form-action 'self'`; `frame-ancestors 'none'` | no third-party code or connections; fonts are self-hosted by `next/font` |
| `X-Content-Type-Options` | `nosniff` | |
| `X-Frame-Options` | `DENY` | older browsers; same intent as `frame-ancestors` |
| `Referrer-Policy` | `no-referrer` | signup and reset links carry a token in the URL |
| `Permissions-Policy` | camera, microphone, geolocation, payment off | |
| `Cross-Origin-Opener-Policy` | `same-origin` | |
| `Strict-Transport-Security` | `max-age=31536000`, only when `HSTS_ENABLED=true` | see below |

- **CSP weakness:** `'unsafe-inline'` for scripts is required by the static export (inline bootstrap scripts), so the CSP does not stop injected inline script; it still blocks external script sources, framing, plugins and exfiltration to other origins. A nonce/hash scheme needs a build step that is not in place. `/docs` and `/redoc` are exempt from the CSP (they load assets from a CDN); consider disabling them in production.
- **HSTS:** browsers ignore it over plain HTTP, and once received over HTTPS it pins the host for a year. Enable it (`HSTS_ENABLED=true`) only when the site is reachable over HTTPS only and the proxy does not already send it; otherwise the header would be duplicated. No `includeSubDomains` or `preload` (that is a decision about the whole domain).
- **Proxy responsibility:** TLS, HTTP-to-HTTPS redirect, HSTS (preferably), request size limits and rate limiting belong to the reverse proxy. If the proxy adds its own CSP or frame headers, keep them consistent with these or remove one side.

Tests: `uv run pytest test_http_security.py -v`.

## 10. Sessions, emailed tokens, mail logging and password policy

- **Sessions:** access tokens expire after 8 hours and carry `sub`, `pwh` and `sv`. `deps.get_current_user` re-reads the user on every request and rejects a token whose `sv` differs from `users.session_version` or whose `pwh` differs from the current password fingerprint. `POST /api/logout` atomically increments `session_version`, so it ends **every** session of that user (all devices), not just the calling one; other users are unaffected. Password change and reset also bump it. Expired, malformed, purpose-scoped (`purpose` claim) and `sv`/`pwh`-less tokens are rejected.
- **Signup and reset tokens:** `token_store.py`, table `auth_tokens`. Random 256-bit values from `secrets`; only the SHA-256 is stored. Purpose-scoped (`signup`, `reset`), expiring (24 h / 1 h), single-use (`consume` is one conditional UPDATE inside the caller's transaction, so simultaneous requests cannot both win; a failed request rolls the consumption back), superseded by a newer link for the same subject, and revoked for a user when their password changes. These are not JWTs and cannot be used as access tokens.
- **Password reset responses:** `POST /api/password/forgot` returns the same status and body (`If an account exists for that address, a reset link was sent`) for known, unknown and email-less accounts, never echoes the address, and sends the mail after the response so latency does not reveal whether one was sent. A delivery failure is logged by type and not reported to the caller. It is rate limited like before.
- **Mail logging (`mailer.py`):** message bodies, links, tokens, token hashes and full recipient addresses are never logged, even when `SMTP_HOST` is unset (then nothing is sent and only the subject and a masked recipient such as `a***@example.com` are logged). SMTP exceptions are logged by class name only. There is no flag to log links. Uvicorn's access log is filtered to redact `signup_token=` / `reset_token=` values from request paths. Consequence: without working SMTP, signup and password reset cannot be completed.
- **Required local configuration (not read by this project's tooling):** `SMTP_HOST`, `SMTP_PORT` (default 587, STARTTLS), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `APP_BASE_URL` (the address users open), `SIGNUP_ALLOWED_DOMAINS`, `SIGNUP_ALLOWED_EMAILS`, `SECRET_KEY`. See `.env.example`.
- **Password policy (`password_policy.py`):** 8-128 characters with an upper-case letter, a lower-case letter, a number and a symbol, enforced by the schemas of setup, signup completion, reset and Manage Users, and by `admin_cli.py`. 422 responses for password and token fields omit the rejected input. Hashing is unchanged (SHA-256 pre-hash then bcrypt).
- **Accounts without a usable password:** `password_hash = "!"`. They cannot log in; the owner's reset link sets the password and verifies the email. The commercial administrator is provisioned this way (`create_commercial_db.py --email`).
- **Public test accounts:** `e2e_admin` / `e2e_member` are refused by setup, Manage Users and `admin_cli.py`, cannot log in, and their tokens are rejected, unless `PM_E2E=1` (which already requires a throwaway database in the temp directory).
- **Database startup:** `init_db()` never creates a missing database (it needs `PM_ALLOW_CREATE_DB=1` or the isolated `PM_E2E=1`) and never migrates; a legacy, incomplete or incompatible database is refused unchanged. Run `migrate_auth_security.py` explicitly (dry run by default) on databases that predate this section.

## 11. Login timing and production mode

**Login timing (account enumeration).** `auth.verify_login_password` always costs one bcrypt verification: an unknown username, or an account whose password is the unusable marker `!` (not yet activated), is checked against a dummy bcrypt hash at the normal cost, built once per process from a discarded random password. Without it, unknown names and unactivated accounts answered measurably faster than real accounts. The password format and algorithm are unchanged; errors stay the generic `401`. Tested deterministically by counting `bcrypt.checkpw` calls (`test_login_timing.py`), not wall-clock time. Residual: the delay policy (section 7) and the `login_ip` limit are unchanged, and the difference of one indexed user lookup (microseconds) is not masked.

### Deferred technical risks (F4, F8, F10)

Three known limitations were reviewed and deliberately deferred. They are **technical risks, not confirmed production incidents**: no occurrence is known to the authors of this note, and nothing has been deployed to a server (VPS). That absence of a known occurrence does not show that none has happened. They were first recorded before Phase 7B; for central (company) mode, Phase 7B (pushed to `origin/main`, not deployed, full details in `docs/PLAN.md`) mitigates all three as described below, and none is claimed to be eliminated beyond what each entry states. The legacy single-database mode is unchanged.

**F4 - Optimistic concurrency (central mode: mitigated in Phase 7B; pushed to `origin/main`, not deployed).**
- Risk (original): two users (or a user and the AI or an import) can edit the same card from the same starting state and the later write silently overwrites the earlier one (last write wins).
- Current state, central (company) mode only: `card_versions` is a monotonic per-card counter raised in the same transaction as every tracked card change, column move and Bid-details change (not by position-only changes or renumbering; a card with no row is version 0 while the version schema is valid, and card ids are never reused, `stage_history.py`). The company update, move and delete routes take an optional `expected_version` and answer 409 `card_version_conflict` when it is stale; the UI always sends the version it first saw and never retries a conflict. The AI assistant is shown a version snapshot taken before the model call and refuses (per action, structured `card_version_conflict`) any action on a card whose version moved on. The write routes and Undo also take SQLite's write lock first (`begin_write`).
- Residual: the central REST card update, move and delete **require** `expected_version` (HTTP 428 `card_version_required` without it, 409 `card_version_conflict` when stale), checked after authentication, existence (404) and permission (403) and before anything is changed, so a lost update through those routes is no longer possible; creation, AI, Undo, imports and review clearing use their own version-aware or pinned contracts. A missing or malformed version/review schema fails closed (503) instead of being read as version 0. The legacy single-database paths were not changed.

**F8 - Cross-database authorization race (central mode: mitigated, not eliminated).**
- Risk: identity data (users, memberships, roles, who is eligible to be assigned) and company data live in separate SQLite databases, so no single transaction covers both. A person can lose eligibility (membership deactivated, role changed, company deactivated) between the moment a request checks it in the identity database and the moment its write commits in the company database; that one write can then still succeed.
- Mitigation (Phase 7B): every writer session is bound to the company the request was authorized for (`company_runtime.session_for` refuses a session whose label or engine is another company's; `company_authority`), and at every commit of that session, under the company write lock and after all the changes were made, the caller's membership is read again from the identity database; if the user, membership or company is no longer active, or the role differs from the one the request was authorized with, the commit is refused (403) and the transaction rolled back. The Bid import routes keep their own earlier recheck. The AI workflow additionally takes the write lock (`begin_write`) before it validates versions. Eligibility of an assigned salesperson is still re-read per request (`assignable_ids`).
- Residual risk: the identity database is read, then the SQLite commit completes; a change made in the identity database between those two moments is not seen, because the databases still have no common transaction. This is a window of one commit, not the whole request; the race is **not eliminated**. Closing it would need a design that makes the two databases agree at commit time, which is not planned.

**F10 - Undo and ABA (central mode: mitigated in Phase 7B; pushed to `origin/main`, not deployed).**
- Risk (original): Undo compared field values, so a value changed and later restored (A -> B -> A), or a change to a field `expect` does not cover, was invisible.
- Current state, central mode: every undo entry is stamped, at commit, with the version of each card it names. Undo compares versions first: a card whose version moved on (also A -> B -> A) makes Undo refuse with 409 `card_version_conflict`, nothing is applied and the entry is kept. The value comparison (`expect`) still runs afterwards and is the only check for entries recorded without versions. Changes that do not raise a version (card positions) are not conflicts.
- Residual: single-level per-user Undo, as before; entries recorded before this change have no versions; the legacy module is unchanged.

**Production mode (`PM_ENV=production`).** Opt-in, so development and the isolated E2E run are unchanged. At startup (`production_config.py`) the app refuses to start, listing every problem, when: `APP_BASE_URL` (the base of emailed signup/reset links) is unset, not `https`, local (`localhost`, `*.localhost`, loopback, `0.0.0.0`) or not a bare origin (path, query or credentials); `PM_PUBLIC_URL` is also set and differs from it (docker-compose maps `PM_PUBLIC_URL` to `APP_BASE_URL`; with both set they must agree); `PM_E2E=1` is set; a `CORS_ALLOWED_ORIGINS` entry is not an `https`, non-local origin (unset means no CORS in production, not the localhost development origins); `RATE_LIMIT_TRUSTED_PROXIES` is malformed or contains a catch-all (`0.0.0.0/0`, `::/0`); `PM_BEHIND_PROXY` is true but `RATE_LIMIT_TRUSTED_PROXIES` is empty. In production `/docs`, `/redoc` and `/openapi.json` are not served (404). Not enforced by the app, so an operational prerequisite: that `HSTS_ENABLED` is set at exactly one layer (app or proxy), the proxy body-size limit and access-log redaction (`docs/DEPLOYMENT.md`).

## 11b. Central mode: Cards import, review clearing and the new UI (Phase 7B; pushed to `origin/main`, not deployed to a server)

- **Cards workbook import.** Super Admin and company Admin only, checked before the company database opens; a preview writes nothing; a confirmation is bound (plan digest, recomputed under the write lock) to the exact workbook, board, mode and card state, and applies all rows plus its ledger row in one transaction or nothing. A filled `card_id` that is not a card of the destination board is an error by default and gives the same answer whether the id is missing, on another board or in another company (nothing is disclosed). Creating new cards for such ids is an explicit per-workbook choice; the new cards get fresh ids from the destination database (the file's ids are never kept, and ids of deleted cards are never reused), and ids that do match a card of the board stay updates.
- **At-most-once ledger and recovery.** A logical workbook is applied at most once per board (unique `(board_id, repeat_guard)`, permanent). Restoring the cards of an already-applied workbook is a separate explicit choice that creates only the rows whose card_id is not on the board, skips everything else without writing, requires a fresh preview, and is bound to its mode, plan and the highest card id ever allocated; it has its own ledger row and never alters or deletes existing ledger rows, so a stale plan, a replay or a concurrent second confirmation is refused without writing. Residual: an export from another company can carry ids that coincide with cards of the destination board (those rows are updates, as always; the preview lists the proposed changes), and a modified copy of a workbook is a different workbook.
- **Review clearing.** Reviewers (Super Admin, company Admin) see and clear only their own role's obligations; the endpoints refuse everyone else (403) and the UI only shows markers when the backend's capabilities say the caller reviews. Clear sends the card version on screen; a stale version (409), a refusal or a missing obligation keeps the marker and is reported, and nothing is shown as cleared before the backend confirms. The dialog shows recorded changes only (user ids, not names).
- **UI sections.** Users (Super Admin) and Database (Super Admin, company Admin) are navigation sections gated by the caller's role as the backend reported it; they are presentation only, every request is still authorized by the backend, and there are no URLs for them.
- **AI Assistant.** The button is not offered to a Visitor; the company endpoint is writers-only and applies the version snapshot, permissions and rate limits described above. No model key is configured on the real backend yet.

## 11c. Central mode: Batch 2 (Reports and KPIs, Admin promotion, contact fields; local work, not deployed)

- **Reports and KPIs.** Navigation entries only. The pages call the existing company endpoints (`/api/companies/{slug}/boards/{id}/report` and `/kpis/*`), which already allow every role of the company and are scoped by the company in the URL, the membership check and the `X-Company-Slug` header. No endpoint, permission or calculation changed; the pages are keyed by company and board, so another company's data is never reused. A company without a board offers neither entry.
- **Admin promotion.** Unchanged rules: an invitation never carries the company Admin role (and never `super_admin`), the global Super Admin is created only by `backend/admin_cli.py`, and `change_membership_role` is Super Admin only. The Users screen now exposes the existing role change for Admin (with a browser confirmation that is presentation only). Browser E2E on a temporary environment asserts that a Sales Member and a company Admin get 403 from the role endpoint, that a member cannot promote themself, that inviting as `admin`, `super_admin`, `global_admin` or `owner` is refused, and that non-Super Admins cannot invite.
- **Contact email and telephone.** Validated by the backend (`schemas.py`): an email must look like an address, a telephone accepts digits, spaces and `+ ( ) - . / # * x` (three digits at least, 40 characters at most), blank clears, anything else is 422. They go through `card_rules` like the other fields (ownership, audit, member-edit history), are not in the compact card, and are not read or written by the AI assistant or the Cards workbook. They are personal data of a customer's contact: they appear in card history entries like any card field.
- **Ordering and Undo.** Display-only ordering by Deal Value changes no permission. After a successful Undo no message is shown; refusals, conflicts and uncertain outcomes still are.
- **Migration.** `migrate_card_contact.py` writes only when `--apply` is given, only two nullable columns, in one transaction, and never creates a database; see `docs/DEPLOYMENT.md`. Its `--database` form does not apply the protected-path check.
- **Validation level.** Automated tests on temporary databases only. No real company database was opened, migrated or used to validate any of this.

## 12. Central mode: schema upgrade tools (development only, not run on any real database)

- The company-database init scripts (`init_company_card_reviews.py`, `init_company_card_loss.py`, `init_company_excel_imports.py`, `init_company_products.py`) and the read-only `diagnose_company_schema.py` take their target only from the company's own `PM_COMPANY_DB_<SLUG>` variable through `database_targets.company_database_path`: protected names and paths (`pm.db`, `pm-commercial.db`, the `DATABASE_URL` file, sidecars, the identity database, another company's file) are refused. There is no `--database` option and no fallback.
- The scripts are dry run by default, create only missing additive tables, and never create or stamp a database. The diagnostic opens the file `mode=ro` with `query_only`, never calls a script, and prints only schema names and versions (no path, no row data).
- None of them makes a backup. Authorization to run them against a real database, the backup and the restore rehearsal are separate, not yet performed steps (`docs/DEPLOYMENT.md`).
- The Phase 1-3 role, review and loss-reason rules are in `docs/CENTRAL_AUTH.md`. Review and loss tables are required for the operations that need them: without them those operations answer 503 instead of skipping the review or the reason.

