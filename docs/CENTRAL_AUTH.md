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
  with no password hash, raw token or token hash. `role` is `super_admin` (active global admin, every
  active company), or, from an active membership in that company only, `admin` (the company-scoped Admin below, not the global administrator), `sales_member` or `visitor`.
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

## Roles, card review and loss reasons as implemented (Phases 1-3; verified in the code, development only)

Source of truth: the predicates in `backend/identity_service.py` and `card_rules.py`, enforced in `company_routes.py` on every request (never in the UI, never from token claims). The role comes from the central database per request.

| Role | Cards | Everything else |
|---|---|---|
| `super_admin` (global administrator) | Create for any eligible salesperson, edit any, assign, move forward and backward, delete, export, import | Boards and columns, products, members and invitations |
| `admin` (company Admin) | Create for an eligible salesperson, edit any, assign, move forward and backward, export | Read-only member list. No delete, imports, boards, products or member changes |
| `sales_member` | Create (assigned to themself, a client-supplied salesperson is ignored); edit and move only cards assigned to them, never unassigned ones; move forward or within a column only | None |
| `visitor` | Read only, even a card assigned to them | None |

The company Admin role exists in the schema (version 2) and in the permission predicates. An invitation may carry it (Correction 1, 2026-10-23): `create_invitation` accepts `admin` only from a current Super Admin (`can_grant_admin(inviter role)` is true for the Super Admin only), acceptance re-checks the inviter's authority at that moment, and the role is stored in the membership. The Super Admin can also set it on a joined member (`change_membership_role`, the Users list). `super_admin` is never invitable: it is the `is_global_admin` flag, created only with `identity_admin_cli.py`. An Admin cannot invite, promote or manage members.

**Card review.** Obligations are stored per card and reviewer role (`card_versions`, `card_review_obligations`). A Sales Member's successful edit creates one obligation for the company Admin and one for the Super Admin, independent of each other. An Admin-level user (Admin or Super Admin) moving a card backward creates one for the Super Admin only. Ordinary forward moves and Admin/Super Admin edits create none. **One exception (decided 2026-10-24):** a company Admin moving a card into a LOST stage (from any other stage, by REST, AI or an Admin-level Undo) creates one pending obligation for the Super Admin only (trigger kind `lost_move`, listed beside `member_edit` and `backward_move`); the Admin's own review list stays empty, and the Super Admin sees and clears it like any other. A Super Admin's own move into LOST creates nothing (no self-review), a Sales Member's move of its own card into LOST creates nothing, and a card already in LOST creates no further obligation. The loss reason, the card version and the movement history are unchanged by this. Each role sees and clears only its own (`GET .../reviews`, `POST .../cards/{id}/reviews/{role}/clear`, other role 403). Every change to a reviewable field or the column raises the card's version and moves pending obligations to it; clearing needs the version the reviewer saw (409 `review_stale` otherwise), and a cleared obligation is never reopened or inherited. Deleting a card closes its pending rows as `void`. REST, AI and undo go through the same listener. Without the tables, changes needing an obligation are refused with 503.

**Loss reasons.** Entering a stage whose `stage_kind` is `lost` (by move or by creating a card there, REST or AI; never by the title) needs a non-empty reason of at most 1000 characters after trimming, supplied by the person (422 `loss_reason_required` / `loss_reason_too_long` otherwise, nothing changes). Each entry writes one `card_loss_events` row (card, board, from/to column, reason, actor, `api`/`ai`, time) in the same flush as the move. Rows are never updated or deleted; leaving the stage, editing or losing the card again keeps earlier rows and a new entry adds a new one. Undo never creates a loss event: it may restore a card to a lost stage only when a loss event for that card and that lost column already exists (otherwise the undo is refused and nothing changes). The Cards workbook import cannot move a card into lost. Without the table the move is refused with 503.

Existing company databases need the review and loss tables created by operator scripts: see "Existing company databases: Phase 1-3 schema readiness" in `docs/DEPLOYMENT.md`.

## Resend of an open invitation

`identity_service.begin_invitation_resend` / `finish_invitation_resend` implement the two-phase resend described in `docs/SECURITY.md` (new token first; the old one is retired only after a successful delivery). The HTTP route is `POST /api/central/companies/{slug}/invitations/{id}/resend` (Super Admin only; 503 without SMTP, 502 on a failed send, 409 for an invitation no longer open). The invitation keeps its expiry and its single open row.


## User lifecycle: remove from a company vs. cancel the user (2026-10-24)

Two different operations, both Super Admin only, both in the Members screen of a company:

| | Remove from company | Cancel user |
|---|---|---|
| Route | `POST /api/central/companies/{slug}/members/{id}/deactivate` | `POST /api/central/users/{id}/cancel` |
| Service | `identity_service.deactivate_membership` | `central_auth.cancel_user` |
| Effect | That one membership becomes inactive. Account, password, sessions and other companies are untouched. The person can be invited again (existing account: "I already have an account" joins with the current password). | The person loses every company, the password, every session, unused reset tokens and open invitations to their address. Irreversible. |
| Identity row | Kept as is | Kept (same id), made powerless: `is_active=0`, `password_hash=NULL`, `session_version+1`, email replaced by `cancelled-<id>@cancelled.invalid` |

Why the identity row is kept: commercial rows in the company databases point at `identity_users.id` as a plain number (card owner, history, reviews, import batches, audit). Deleting it would orphan or cascade them. `cancel_user` never opens a company database, so cards, deal values, reports and KPIs are unchanged and stay attributed to the cancelled id; the company-local `users` row keeps its display name (the old email) so history stays readable.

Re-inviting the same address: the unique index on `identity_users.email` still protects every live identity, and the placeholder frees the normalized address. Accepting the new invitation creates a new identity (new id, no password, only the invited company and role), through the normal first-password form. It shares no metrics, authorship or credentials with the cancelled one; reassigning old cards to it is a separate, explicit operation (not implemented). A deactivated but not cancelled identity can no longer be invited (it could never accept); cancel it first.

Retention: audit events keep their ids (no email in `details`). The email is removed from the identity row and from the closed invitations that named it. It remains in the company-local `users.username` display name and in any card text, which this change does not rewrite; erasing it there is a separate decision (it would change what reports show).

Rollout: no schema change and no migration (the existing columns and unique index are used). Roll back by reverting the code; a cancelled identity stays cancelled (the former email is not stored, so it cannot be restored automatically). Back up `identity.db` before the first cancellation on a real database. First validation after deployment: cancel a throwaway invited address, confirm it cannot sign in and its old link is refused, invite it again, set a password and check it has only the new company and no history.
