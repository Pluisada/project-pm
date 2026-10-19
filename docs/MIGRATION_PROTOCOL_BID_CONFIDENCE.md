# Protocol: removing the Bid confidence columns from a real database

Status: PREPARED, NOT EXECUTED (legacy tool corrected and validated on disposable databases only; nothing has been applied to a real one). Applying it to a real database needs the owner's explicit authorization per database.

Decision: `card_bid_details.confidence` and `card_source_refs.source_confidence` leave the live schema. Their values are
not converted to anything. `cards.confidence_level` (LOW / MEDIUM / HIGH / NULL) is the sales team's independent,
manually set classification; the migration only adds it (NULL everywhere) when it is missing and never infers it.

Two tools, for two different kinds of file. Pick by the database's role (step 1); the guarantees differ, so do not assume
one tool's protections apply to the other. Both are dry run by default. Run both from `backend/`, in a shell without
`DATABASE_URL`, `IDENTITY_DATABASE_URL` or `PM_COMPANY_DB_*`. Do one database at a time.

### A. Legacy single database: `migrate_legacy_bid_confidence.py`
`uv run python migrate_legacy_bid_confidence.py --database <abs> [--expect-sha256 <hash> --apply]`. The only flags are
`--database` (required, absolute path), `--expect-sha256` and `--apply`; there is no `--company` and no `--confirm-app-stopped`
(stopping the application is the operator's job and nothing checks it). No version marker. Needs the earlier migrations (stage kind, stage
events and their value columns, the Bid import tables) and refuses before any backup when they are missing.
- Target and pre-conditions (all refused with nothing written, exit 1): relative path, symlink, path behind a symlink, not a
  regular single-link file, not a SQLite file, missing file (never created), `pm.db` / `pm.db.bak` (by path, inode or name), a
  `-wal`/`-journal`/`-shm` sidecar, a WAL-mode file, `--apply` without `--expect-sha256`, a hash mismatch, existing foreign key
  violations, and a file that needs nothing. A Bid import table that differs from the models in any way other than the obsolete
  columns (a stray column, a missing `cpm`) is only detected after the changes are made inside the transaction: the run rolls back,
  the database is unchanged and the verified backup is kept. `DATABASE_URL` is never read. It does not refuse `pm-commercial.db`, other environment-named paths or the acceptance
  directory: those stay procedural stop conditions (step 4).
- `--apply`, in order: (1) path identity (st_dev/st_ino, no symlink) and SHA-256 against `--expect-sha256`; (2) one write
  transaction (`BEGIN IMMEDIATE`, foreign keys on); (3) under that lock, before any change, identity, sidecars and SHA-256 again,
  and the lock holder's own serialized view of the file must hash to the same value, which closes the window between the first
  check and the lock; (4) exclusive backup `<name>-pre-bid-confidence-<time>.db.bak` (`O_EXCL`, mode 0600, never over an existing
  file: if the name exists the run stops, the file is untouched and nothing is migrated), made through a separate read-only connection;
  (5) the backup is verified; (6) only then the schema change.
- Backup verification (the same code as the company tool): same `sqlite_master` (every object with its SQL), same header metadata
  (`user_version`, `application_id`, `page_size`, `encoding`, `auto_vacuum`), a typed digest of every column of every table
  (the two columns about to be dropped included), `integrity_check` = `ok`, a backup file that did not change while it was verified, and
  a source that still hashes to the approved value afterwards. Limits: content equivalence, not byte identity; hashes are checks of
  bytes at those moments, not authentication; SQLite cannot stop a process that ignores its locks or renames files in the directory
  (detected at the moments checked, not prevented); local disk only.
- Failure states: a backup that fails to copy or verify is renamed `<name>.UNVERIFIED.db.bak` (or `.UNVERIFIED2.db.bak`, ... never over
  an existing file), the error names it, says never to restore from it, and the database is unchanged. A failure after the backup was
  verified (schema mismatch, data changed, a non-empty `confidence_level`, foreign key violations, an injected or unexpected error)
  rolls the transaction back and the error says "rolled back, database unchanged" and names the verified backup, which is kept. If the
  `COMMIT` itself fails the error says its outcome is not confirmed: inspect the schema before re-running. A pre-existing backup is
  never removed, renamed or replaced.
- Not provided by this tool (unlike B): no `user_version` handling, no in-memory dry run of the whole migration (the dry run reports
  the plan from a read-only connection and does not print the file's hash: compute it with `shasum -a 256`), no hash in the report,
  no `db_state` classification beyond the two messages above.

### B. Company database (`salcomp`, `lity`, `valor-log`): `migrate_confidence_level.py`
`migrate_confidence_level.py --company <slug> --database <abs> [--apply --confirm-app-stopped --expect-sha256 <hash>]`. Moves
`PRAGMA user_version` 2 or 3 to 4 in the same transaction as the column changes. Beyond A it: requires `--company` and
`--confirm-app-stopped`; refuses the paths named by `DATABASE_URL`, `IDENTITY_DATABASE_URL` and `PM_COMPANY_DB_*` in its own process,
`pm-commercial.db` and the local acceptance directory; refuses a legacy file by its schema; runs the whole migration on an in-memory
copy for the dry run, printing the file's SHA-256 and the report; classifies the outcome after any failure (unchanged, rolled back,
migrated, unknown, rollback failed) in the error notes. Its backup is created and verified exactly as in A.

## 1. Identify each database (record before anything else)
For each target write down: role (legacy or company slug), absolute path, `PRAGMA user_version` (company: must be 2 or 3;
legacy: no marker; normally has the two obsolete columns), whether the old columns hold values (count of non-NULL), row counts
of `users`/`boards`/`cards`/`card_bid_details` (legacy) or the company equivalents, and the code commit that will run
afterwards. A database that is not exactly one of these shapes is out of scope: stop.

## 2. Backup, external copy, hash, integrity
With the application stopped (step 5), before the dry run:
1. Consistent copy with SQLite itself, not `cp`: `sqlite3 <db> ".backup '<ext>/<name>-pre-bid-confidence-<UTC time>.db'"`.
   `<ext>` is a different directory and, preferably, a different volume from the database directory.
2. `shasum -a 256` of the live file (the "expected hash") and of the external copy; record both.
3. On the copy: `PRAGMA integrity_check` = `ok`, `PRAGMA foreign_key_check` empty, `PRAGMA user_version` equal to the live one, same row counts.
4. Make the external copy read-only (`chmod 444`). It is the audit evidence of the discarded values.
The tool then writes its own local `.db.bak` next to the database; that is a second copy, not a replacement for the external one.

## 3. Dry run (mandatory)
Run without `--apply`; save the full output with the date. Expected: it lists the column to add (legacy, if missing) and
the two columns to drop, and the counts equal step 1. Re-hash the live file: it must still equal the expected hash.
The dry run is valid only for that hash; any later change to the file repeats steps 2 and 3.

## 4. Stop conditions (do not apply; write down why)
Any of: path, version or schema differs from step 1; non-zero `integrity_check` or `foreign_key_check`; a `-wal`/`-journal`
sidecar exists; the dry run errors or reports "nothing to do" unexpectedly; counts differ; live hash differs from the
recorded one; external copy missing, unverified or on the same directory; the application or any process still has the file
open; the target is `pm.db`, `pm-commercial.db` without separate authorization, or a path named by the environment;
no written approval for this database.

## 5. Apply
1. Stop the application and confirm no process holds the file (`lsof <db>` empty).
2. Re-hash the live file and compare with the expected hash. Legacy (A): `--apply --expect-sha256 <hash>`. Company (B):
   `--apply --confirm-app-stopped --expect-sha256 <hash>` (its dry run prints the hash; A's does not). The guarantees of the
   hash checks, the exclusive backup, its verification, the `*.UNVERIFIED.db.bak` quarantine and the rollback are those described
   in section A (both tools) and B (extra checks). The tool's local `.bak` is a second copy and does not replace the external one.
   Never restore from an `*.UNVERIFIED*` file.
3. Keep the full output. Do not restart the application yet.

## 6. Post-migration checks (read-only, before restart)
- Schema: neither obsolete column exists; `cards.confidence_level` exists, `VARCHAR(6)`; company `user_version` = 4. The tool already verifies the schema contract; confirm with `PRAGMA table_info`.
- Counts: every row count from step 1 is unchanged.
- Integrity: `integrity_check` = `ok`; `foreign_key_check` empty.
- Classification: `SELECT COUNT(*) FROM cards WHERE confidence_level IS NOT NULL` = 0 on the first migration (or equal to the pre-count if the column already existed and was edited); no value was derived from the discarded ones.
- Other data: the tool reports surviving columns identical; keep that line.
- Start the new code and check that startup guards pass and a card opens with `confidence_level` null. Hash the migrated file and record it.

## 7. Rollback
Trigger: any failed check in step 6, or an application problem after restart. Stop the app, keep the migrated file aside
(rename, do not delete), restore the external copy (or the tool's verified local `.bak`, never an `*.UNVERIFIED*` one) into place with the app stopped, and deploy the
previous code. Confirm: restored file hash equals the recorded pre-migration hash, `integrity_check` ok, `user_version`
as before, row counts as step 1. Proving restorability BEFORE applying: restore the external copy into a scratch directory
and repeat the step 2.3 checks on it. Edits made by users after restart are lost on restore; if the app ran, decide that explicitly.

## 8. Audit record (one block per database, stored with the evidence outside the repo)
Database role and path; UTC times; operator and authorizer; code commit; user_version before/after; SHA-256 of the live file
before, of the external copy, of the local `.bak`, and after; dry-run output; apply output; step 6 results; count of non-NULL
obsolete values discarded (counts only, no card content); rollback outcome if any. The external copy is retained until the
owner authorizes its disposal.
