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
| `POST /api/setup` | Creates the **first** user as admin, only while the users table is empty (409 afterwards) |
| `POST /api/users` (admin only) | Always creates a member; a `role` field in the request is ignored |
| `POST /api/signup/complete` (public) | Always creates a member; a `role` field in the request is ignored |
| `backend/admin_cli.py` | The only way to add or change admins after setup |

There is no HTTP endpoint that promotes users. The authorization check for `admin_cli.py` is operator access to the server's shell and database.

### `admin_cli.py`

Run from `backend/`, with `SECRET_KEY` set in the environment (the script imports `auth`):

```bash
uv run python admin_cli.py create  --username <name>    # prompts twice for the password, no echo
uv run python admin_cli.py promote --username <name>    # asks you to type the username to confirm
uv run python admin_cli.py demote  --username <name>    # same confirmation; --yes skips it
```

- **Passwords:** never accepted as command-line arguments, because those end up in shell history and the process list. For automation, use `--password-stdin` and pipe the password in from a secret store. The script prints nothing but usernames and roles.
- **`create`:** refuses an existing username, so it can't take over an account or reset its password.
- **`promote`:** works only on a named, existing user.
- **`demote`:** refuses to remove the last admin.
- **Database:** the script runs `init_db()` (the same automatic table/column migration as the app's startup) against the database in `DATABASE_URL`. The default is `backend/pm.db`.

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
| Create cards, edit cards and leads, move cards forward | Any authenticated user (member edits logged) | `card_rules.py` |
| Move cards backward, delete cards | Admin only | `card_rules.py` (REST and AI) |
| Undo | Only the user's own last card change | `undo.py` |
| Manage users | Admin only | `require_admin` on `/api/users` |
| `POST /api/ai/test` (paid OpenRouter call) | Admin only | `require_admin`, checked before any OpenRouter request |
| AI chat (`POST /api/boards/{id}/ai`) | Any authenticated user; its actions follow the card rules above | `ai_kanban.apply_board_actions` |

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
- no rate limiting on login, signup or forgot-password;
- login tokens kept in localStorage with no revocation;
- E2E tests run serially on one shared throwaway board (isolated from real data, but not from each other).
