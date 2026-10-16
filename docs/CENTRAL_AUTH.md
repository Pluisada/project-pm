# Central authentication (Phase 1.5, isolated)

`backend/central_auth.py` implements the credential and session lifecycle on the central
identity database (`identity_models`). It is **not wired** to any route, to `deps.py` or to
`/api/login`; the legacy flow (commercial `users` table, `auth.create_user_access_token`,
`deps.get_current_user`) is unchanged and still authoritative for the running application.

## Legacy dependencies found

| Piece | Depends on |
|---|---|
| `auth.py` | Nothing in the database. Hashing (`hash_password`, `verify_password`, `verify_login_password`), `create_purpose_token`, `password_fingerprint` are reused as is. `create_user_access_token(user)` expects a commercial `User` (`username`, `password_hash`, `session_version`). |
| `deps.get_current_user` | Commercial `User` by `username`; `role` from `users.role`; `E2E_ONLY_USERNAMES`; `set_stage_context`. |
| `main.py` login, logout, forgot, reset, signup | Commercial `User`, `token_store` (commercial `auth_tokens`), `UNUSABLE_PASSWORD_HASH`, `SIGNUP_ALLOWED_*`. |
| `identity_service` | Already independent of credentials; it creates users without a password. |

Incompatibilities that remain: identity users are keyed by email and a numeric id (no
username); the global-admin flag replaces `users.role`; the legacy tokens' `sub` is a username;
`UNUSABLE_PASSWORD_HASH` ("!") is legacy, the identity schema uses `password_hash IS NULL`;
legacy signup has no identity counterpart (no public signup by design).

## Design

- Credentials only in `identity_users`. Same bcrypt scheme, same password policy, same login
  cost equalization (`verify_login_password`) as legacy.
- Access token = purpose token `identity_access` (`sub` = central user id, `pwh`, `sv`, 8 h).
  The `purpose` claim makes legacy `decode_access_token` reject it and `decode_purpose_token`
  reject legacy tokens, so neither is reinterpreted. Every use re-reads the user (active, email
  verified, `pwh`, `sv`); the global-admin flag is never taken from the token.
- Initial password: `accept_invitation_with_password` claims the invitation token, creates the
  membership and user, and stores the hash in one transaction (`identity_service.claim_invitation`
  is `accept_invitation`'s body, now callable inside a caller-owned transaction). A person who
  already has a password gets `InvitationInvalid` and uses `accept_invitation(user_id=...)`.
- Reset: `issue_password_reset` (1 h, newest link only, eligible = active + verified, returns
  `None` otherwise so the route answers identically) and `reset_password` (policy first, then
  claim + new hash + `session_version` bump + token invalidation in one transaction).
- `change_password`, `logout`, `deactivate_user` bump `session_version`; deactivation and
  password changes also invalidate unused tokens. All are audited in `identity_audit_events`
  without secrets.
- No schema change (identity schema version stays 1).

## Integration dependencies (deferred, not done here)

1. A central login route and a central `get_current_user` returning an identity user plus a
   company-scoped role (`identity_service.company_access`). Until company routes are
   company-scoped, replacing `/api/login` would break every commercial route (they need a
   commercial `User`), and a mixed path would be insecure.
2. Mapping from identity user id to commercial rows (`CompanyUser.id` equals the identity id,
   via `sync_company_user`); card ownership and `card_rules` still use the legacy `User`.
3. Routes for invitation email, forgot/reset, rate limiting (`rate_limit.py` keys), and the
   frontend screens; the emailed link format and `mailer.py` reuse.
4. Process wiring: opening the identity engine from `IDENTITY_DATABASE_URL` at startup.
5. Reactivating a deactivated user and deactivating a user's memberships are not provided.
6. `issue_password_reset` does not equalize timing between eligible and ineligible emails; the
   route should send mail in the background (as legacy does) and rate-limit per account.

## Company-scoped authorization (`backend/central_deps.py`, isolated)

Not imported by any route, `deps.py` or `main.py` (a test enforces it). Importing it opens
no database. Contract for the later route migration:

- `company_context_dependency(identity_db)` returns a FastAPI dependency. `identity_db` is a
  session dependency the caller supplies (it owns opening and closing the session; no
  fallback to `DATABASE_URL`, no startup initialization here). The route must have a
  `company_slug` path parameter, e.g. `/api/companies/{company_slug}/...`; the slug is never
  inferred from the user, a header or a token.
- It yields a frozen `CompanyContext(user_id, is_global_admin, company_slug, role, read_only)`
  with no password hash, raw token or token hash. `role` is `admin` (active global admin, every
  active company), `sales_member` or `visitor` (active membership in that company only).
  `read_only` is true for `visitor`.
- Evaluated from the central database on every request: `central_auth.user_for_token`
  (signed `identity_access` token, active verified user, `pwh`/`sv` match; legacy tokens fail
  on their purpose claim) then `identity_service.company_access`. Admin status and role are
  never read from token claims; `CompanyUser` rows and the legacy `User.role` are never consulted;
  no company database is opened.
- Errors: 401 for any authentication problem (missing/malformed header, bad, expired or
  revoked token, inactive user); 403 with one fixed body for every authorization problem
  (unknown slug, missing or inactive company, no active membership), so slugs cannot be probed.
- `can_modify_opportunity(context, assigned_user_id)` is only a coarse gate (admin: any;
  sales member: only opportunities assigned to them; visitor: none). Stage-transition and
  assignment rules stay in `card_rules`, which must be adapted to take a `CompanyContext`
  when routes migrate.
- Still needed before wiring: the identity engine opened from `IDENTITY_DATABASE_URL` in
  process wiring, a central login route, and the identity-id to `CompanyUser` mapping
  (items 1, 2 and 4 above).
