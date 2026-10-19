"""Upgrade a company database to schema version 4 (cards.confidence_level added, obsolete Bid confidence dropped).

    uv run python migrate_confidence_level.py --company lity --database /abs/path/lity.db           # dry run (default)
    uv run python migrate_confidence_level.py --company lity --database /abs/path/lity.db --apply --confirm-app-stopped --expect-sha256 <hash>

Starts from version 2 (before cards.confidence_level) or version 3 (cards.confidence_level present) and ends at 4:
- version 2: adds `cards.confidence_level VARCHAR(6)`, the sales team's own LOW / MEDIUM / HIGH assessment of an
  opportunity (every existing card is NULL = "not assigned": nothing is inferred, converted or randomized);
- both: drops the obsolete Bid columns `card_bid_details.confidence` and `card_source_refs.source_confidence`. Their
  values are deliberately discarded (the sales team reviews opportunities manually after rollout); every other column
  of every table (import batches, ledgers and revert snapshots included) is verified unchanged;
- sets PRAGMA user_version to 4, in the same transaction as the column changes.

Rules (the same as migrate_identity_v2.py, whose target checks this tool reuses):
- The path is explicit and absolute; there is no environment or .env fallback, and the paths named by DATABASE_URL,
  IDENTITY_DATABASE_URL and PM_COMPANY_DB_* in THIS process are refused (run it in a shell without them). pm.db,
  pm-commercial.db, sidecar files, symlinks, the local acceptance data directory and any file with a -wal/-journal
  sidecar are refused. A legacy single-database file is refused by its schema as well (migrate_legacy_bid_confidence.py).
- --company states which company the file belongs to (salcomp, lity, valor-log); it is recorded in the report.
- Dry run (the default) opens the file read-only, runs the whole migration on an in-memory copy and prints the report,
  with the file's SHA-256 (record it: it is the value --apply must be given).
- --apply needs --confirm-app-stopped (stop the application first; this tool never stops a process) and
  --expect-sha256 <the approved file's hash>. The file is opened mode=rw (never created). It is refused when it is in WAL
  mode or has a -wal/-shm/-journal sidecar. Under the write lock (BEGIN IMMEDIATE), before any DDL, the tool checks the
  path still names the same file (st_dev/st_ino, no symlink), re-hashes the file against the approved hash, and checks that
  the SHA-256 of the lock holder's own conn.serialize() equals it too (see Journal modes). It then fingerprints the approved
  content on that connection, writes the backup through a second read-only connection while the lock is held, verifies it and
  hashes the source file again.
- What backup verification proves: the backup is content-equivalent to the approved state - same sqlite_master (every table,
  index, view and trigger with its SQL), same header metadata (user_version, application_id, page_size, encoding,
  auto_vacuum) and a typed digest of every column of every table, the columns this migration drops included (NULL, INTEGER,
  REAL, TEXT and BLOB are distinguished) - plus integrity_check ok, a backup file whose bytes did not change while it was
  verified, and a source that still hashes to the approved value afterwards. Chosen over iterdump(): digests are
  deterministic, type-exact and need no text round trip. It does not prove byte-identity (SQLite backup files may
  legitimately hash differently) and the hashes are checks of bytes at those moments, not authentication.
- Backup files: created exclusively (O_EXCL), mode 0600. If the name already exists (same second, or any earlier backup) the
  run stops, the existing file is untouched and nothing is migrated. If copying or verification fails, the new artifact is
  never left looking valid: it is renamed to <name>.UNVERIFIED.db.bak (never over an existing file) and the error names it
  and says the database was not changed; a pre-existing file is never removed or renamed. When the migration itself fails
  after the backup was verified (including after DDL, before COMMIT) the transaction is rolled back, the database is
  unchanged, and the error says so and names the verified backup that was kept. The error states only what has evidence:
  "rolled back" after a ROLLBACK that succeeded; "COMMITTED, the database IS migrated" if the COMMIT returned (even when
  something later, such as the final report, failed); "outcome of the COMMIT NOT confirmed" if the COMMIT itself failed or
  was interrupted and the transaction is gone (or the ROLLBACK after a failed COMMIT failed); "no COMMIT was attempted, but
  the ROLLBACK failed, state NOT confirmed" if the ROLLBACK that follows a failure BEFORE the COMMIT itself failed. The last
  differs from the one before it: there nothing was committed by this run (the COMMIT was never issued) and the open
  transaction is discarded when the connection closes, but the tool did not see the ROLLBACK succeed, so it does not say
  the database is unchanged. Never re-run the migration after either of the last two; inspect PRAGMA user_version and the
  schema first. An interrupt (KeyboardInterrupt) during the backup copy quarantines the partial file and is re-raised as itself.
- Journal modes: WAL databases are refused. In every rollback-journal mode (delete, truncate, persist, memory, off) a
  connection holding BEGIN IMMEDIATE with no writes yet serializes to exactly the file's bytes, which is what the
  serialize() check relies on (tested); in WAL mode it would not (committed pages can sit in the -wal file), another
  reason WAL is refused. Limits: SQLite file locking can be unreliable on network filesystems (NFS, SMB, some sync
  folders), so run it on a local disk; SQLite cannot stop a process that ignores its locks, nor someone who can rename or
  replace files in the directory - the identity (st_dev/st_ino) and hash checks detect such changes at the moments they run
  but do not close the window between two checks (a rename is detected, not prevented), and the backup path check is
  likewise a narrow, not an eliminated, race. Stopping the application remains the operator's job.
- One transaction (BEGIN IMMEDIATE, held from those checks through the DDL); afterwards it verifies that the surviving columns hold identical rows, that every
  confidence_level is NULL, that the schema satisfies the version-4 contract and that integrity and foreign keys are
  clean. Any failure rolls everything back, so the version never moves without the column changes.
- Refused, nothing written: a database already at version 4, any other version, a half-migrated one, or one that is
  not exactly the expected company schema.
- Rollback: restore the backup and deploy the previous code.
Never run it on a real database before that has been approved; test it on a disposable synthetic one first
(test_migrate_confidence_level.py). The report holds counts only: no card content.
"""
import argparse
import hashlib
import os
import sqlite3
import stat
import sys

from company_schema import COMPANY_SCHEMA_VERSION, REQUIRED_COMPANY_SCHEMA
from database_targets import COMPANY_SLUGS
from db_target_guard import SIDECAR_SUFFIXES, open_readonly, open_readwrite, sha256_file
from migrate_identity_v2 import check_target
from migrate_stage_kind import BackupError, MigrationError, backup_database

FROM_VERSIONS = (2, 3)
COLUMN = "confidence_level"
ADD_COLUMN = f"ALTER TABLE cards ADD COLUMN {COLUMN} VARCHAR(6)"
OBSOLETE = (("card_bid_details", "confidence"), ("card_source_refs", "source_confidence"))
LEGACY_MARKERS = (("users", "password_hash"), ("auth_tokens", "token_hash"))  # present only in the legacy single database
IGNORED = frozenset({("cards", COLUMN), *OBSOLETE})  # the columns this migration adds or drops; all others must not change


def _tables(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name")]


def _columns(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _required(version: int) -> dict[str, frozenset]:
    """The table/column contract of a company schema `version` (2, 3 or 4)."""
    required = {table: frozenset(columns) for table, columns in REQUIRED_COMPANY_SCHEMA.items()}
    if version < 4:
        for table, column in OBSOLETE:
            required[table] = required[table] | {column}
    if version < 3:
        required["cards"] = required["cards"] - {COLUMN}
    return required


def schema_problems(conn, version: int) -> list[str]:
    tables = set(_tables(conn))
    problems = [f"missing table {t}" for t in sorted(set(REQUIRED_COMPANY_SCHEMA) - tables)]
    for table, required in _required(version).items():
        if table in tables:
            absent = sorted(required - set(_columns(conn, table)))
            if absent:
                problems.append(f"table {table} lacks columns {', '.join(absent)}")
    if version == 4:
        for table, column in OBSOLETE:
            if table in tables and column in _columns(conn, table):
                problems.append(f"table {table} still has {column}")
    return problems


def _is_legacy(conn) -> bool:
    tables = set(_tables(conn))
    return any(t in tables and col in _columns(conn, t) for t, col in LEGACY_MARKERS)


def require_upgradable(conn) -> int:
    """Raise MigrationError unless this is exactly a company schema-2 or schema-3 database; returns its version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if _is_legacy(conn):
        raise MigrationError("This is a legacy single-database file, not a company database; use migrate_legacy_bid_confidence.py")
    if version == COMPANY_SCHEMA_VERSION:
        raise MigrationError(f"The database is already at company schema version {version}; nothing to do")
    if version not in FROM_VERSIONS:
        raise MigrationError(f"Company schema version {version} found; only versions {FROM_VERSIONS[0]} and {FROM_VERSIONS[1]} can be migrated")
    has_column = COLUMN in _columns(conn, "cards") if "cards" in _tables(conn) else False
    if (version == 3) != has_column:
        raise MigrationError("The database is half-migrated (the version and cards.confidence_level disagree); restore a backup")
    problems = schema_problems(conn, version)
    if problems:
        raise MigrationError(f"The database is marked version {version} but is not the company v{version} schema ({'; '.join(problems)})")
    return version


def _digest(conn, table: str, columns: list[str]) -> str:
    h = hashlib.sha256()
    for row in conn.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in columns)} FROM "{table}" ORDER BY rowid'):
        h.update(repr(row).encode())
    return h.hexdigest()


def snapshot(conn) -> dict:
    """Counts and per-table content digests of the columns this migration leaves alone (counts only reach the report)."""
    tables = _tables(conn)
    return {
        "user_version": conn.execute("PRAGMA user_version").fetchone()[0],
        "rows": {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables},
        "digests": {t: _digest(conn, t, [c for c in _columns(conn, t) if (t, c) not in IGNORED]) for t in tables},
        "foreign_key_violations": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
        "levels_assigned": conn.execute(f"SELECT COUNT(*) FROM cards WHERE {COLUMN} IS NOT NULL").fetchone()[0]
        if COLUMN in _columns(conn, "cards") else None,
        "obsolete_columns": [f"{t}.{c}" for t, c in OBSOLETE if t in tables and c in _columns(conn, t)],
    }


# What is known about the database file after a failure; recorded on the exception as `db_state` (see _settle, _annotate).
# Five states: UNCHANGED, ROLLED_BACK, MIGRATED, UNKNOWN and ROLLBACK_FAILED.
UNCHANGED = "unchanged"      # no write transaction had been started
ROLLED_BACK = "rolled_back"  # the transaction was ended by a ROLLBACK that succeeded, before any COMMIT was attempted
MIGRATED = "migrated"        # COMMIT returned: the migration is in the file
UNKNOWN = "unknown"          # COMMIT was attempted and its outcome could not be confirmed
ROLLBACK_FAILED = "rollback_failed"  # no COMMIT was attempted, but the ROLLBACK that should have ended the transaction failed


def _settle(conn, exc: BaseException, phase: str) -> None:
    """End the open transaction after `exc` and record on it what is known: `exc.db_state`. Never raises: a failure of
    the cleanup itself is attached as a note, so the original error stays the one that propagates.

    Evidence used: "committed" means COMMIT returned. "before" is rolled back when no transaction is left (SQLite ended it)
    or the ROLLBACK succeeded. "committing" is rolled back only if the failed COMMIT left the transaction open and the
    ROLLBACK then succeeded; if the transaction is gone, the COMMIT may or may not have reached the file: unknown.

    When the ROLLBACK itself fails the state depends on the phase, and the two cases are deliberately different:
    - "before" (the COMMIT statement was never issued): ROLLBACK_FAILED. Nothing was committed by this run, but the tool did
      not see the transaction end, so it claims neither a rollback nor an unchanged file.
    - "committing" (the COMMIT was issued and failed): UNKNOWN. The COMMIT may or may not have reached the file.
    The failure of the ROLLBACK is attached to `exc` as a note; `exc` itself is never replaced."""
    state = MIGRATED if phase == "committed" else UNKNOWN
    try:
        if phase != "committed":
            if conn.in_transaction:
                conn.execute("ROLLBACK")
                state = ROLLED_BACK
            elif phase == "before":
                state = ROLLED_BACK
    except BaseException as cleanup:
        state = ROLLBACK_FAILED if phase == "before" else UNKNOWN
        exc.add_note(f"the ROLLBACK itself failed ({cleanup!r}); the state of the database is not confirmed")
    exc.db_state = state


def migrate(conn, *, _after_step=None, _prepare=None) -> None:
    """Add the column (version 2 only), drop the obsolete columns and set the version, in one transaction on `conn`;
    rolls back and re-raises on any failure. Every exception leaves with `db_state` set so the caller never has to guess
    what the file holds (see _settle):
    - UNCHANGED: the BEGIN failed, no write transaction existed;
    - ROLLED_BACK: failure before the COMMIT was issued, and the transaction ended (ROLLBACK succeeded or SQLite ended it);
    - ROLLBACK_FAILED: failure before the COMMIT was issued, and the ROLLBACK itself failed: no COMMIT was attempted, the
      state of the file is not confirmed;
    - UNKNOWN: the COMMIT was issued and its outcome could not be confirmed (it failed or was interrupted with no
      transaction left, or the ROLLBACK after it failed);
    - MIGRATED: the COMMIT returned, whatever failed afterwards.

    `conn` must be opened with isolation_level=None (explicit transactions). _after_step(name) is a test hook called
    after each step so a failure can be injected. _prepare(conn, before), when given, runs under the write lock after the
    schema check and before any DDL (apply() uses it for the hash re-check and the backup).
    """
    step = _after_step or (lambda name: None)
    try:
        conn.execute("BEGIN IMMEDIATE")
    except BaseException as exc:  # no write was made: the transaction never held anything
        exc.db_state = UNCHANGED
        raise
    phase = "before"  # "before" the COMMIT statement, "committing" while it runs, "committed" once it returned
    try:
        version = require_upgradable(conn)  # re-checked under the write lock
        before = snapshot(conn)
        if _prepare:
            _prepare(conn, before)
        if version == 2:
            conn.execute(ADD_COLUMN)
            step("column added")
        for table, column in OBSOLETE:
            conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
        step("obsolete columns dropped")
        conn.execute(f"PRAGMA user_version = {COMPANY_SCHEMA_VERSION}")
        step("version set")
        after = snapshot(conn)
        if after["rows"] != before["rows"] or after["digests"] != before["digests"]:
            raise MigrationError("existing rows changed during the migration")
        if after["levels_assigned"] != 0 and not (version == 3 and after["levels_assigned"] == before["levels_assigned"]):
            raise MigrationError("a confidence level was assigned during the migration")
        if after["foreign_key_violations"] or after["integrity"] != "ok":
            raise MigrationError("integrity or foreign-key check failed after the migration")
        problems = schema_problems(conn, COMPANY_SCHEMA_VERSION)
        if problems or conn.execute("PRAGMA user_version").fetchone()[0] != COMPANY_SCHEMA_VERSION:
            raise MigrationError(f"the migrated schema is not the version-{COMPANY_SCHEMA_VERSION} contract ({'; '.join(problems)})")
        step("verified")
        phase = "committing"
        conn.execute("COMMIT")
        phase = "committed"
        step("committed")
    except BaseException as exc:
        _settle(conn, exc, phase)
        raise


def _read_only_copy(path: str) -> sqlite3.Connection:
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(":memory:", isolation_level=None)
        source.backup(copy)
    finally:
        source.close()
    return copy


def dry_run(path: str) -> tuple[dict, dict]:
    """(before, after) from running the migration on an in-memory copy of a read-only view of the file."""
    copy = _read_only_copy(path)
    try:
        require_upgradable(copy)
        before = snapshot(copy)
        migrate(copy)
        return before, snapshot(copy)
    finally:
        copy.close()


PRAGMAS = ("user_version", "application_id", "page_size", "encoding", "auto_vacuum")  # header metadata a page copy preserves


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_digest(conn, table: str) -> tuple[str, int]:
    """(SHA-256, row count) of every column of `table` (the migration's own columns included), typed and ordered.

    Each value is encoded with its SQLite storage class (NULL, INTEGER, REAL as float.hex, TEXT and BLOB as their raw stored
    bytes with a length prefix), so NULL vs '' vs x'', 1 vs 1.0 vs '1' all differ. Rows are read in rowid order (primary-key order
    for WITHOUT ROWID tables) and include the rowid.
    """
    info = conn.execute(f"PRAGMA table_xinfo({_q(table)})").fetchall()
    columns = [r[1] for r in info if r[6] in (0, 2, 3)]  # hidden: 0 normal, 2/3 generated; 1 is a virtual-table internal
    exprs = ", ".join(f"typeof({_q(c)}), CASE WHEN typeof({_q(c)}) IN ('text', 'blob') THEN CAST({_q(c)} AS BLOB) ELSE {_q(c)} END" for c in columns)
    try:
        conn.execute(f"SELECT rowid FROM {_q(table)} LIMIT 0")
        order, lead = "rowid", "rowid, "
    except sqlite3.OperationalError:  # WITHOUT ROWID
        order, lead = ", ".join(_q(r[1]) for r in sorted((r for r in info if r[5]), key=lambda r: r[5])) or "1", ""
    h = hashlib.sha256()
    h.update(repr(columns).encode())
    count = 0
    for row in conn.execute(f"SELECT {lead}{exprs} FROM {_q(table)} ORDER BY {order}"):
        count += 1
        values = row[1:] if lead else row
        if lead:
            h.update(b"#%d;" % row[0])
        for kind, value in zip(values[0::2], values[1::2]):
            if kind == "null":
                h.update(b"N;")
            elif kind == "integer":
                h.update(b"I%d;" % value)
            elif kind == "real":
                h.update(b"R" + value.hex().encode() + b";")
            else:
                h.update((b"T" if kind == "text" else b"B") + b"%d:" % len(value) + value)
        h.update(b"|")
    return h.hexdigest(), count


def content_fingerprint(conn) -> dict:
    """What a faithful copy must reproduce: sqlite_master (every object: tables, indexes, views, triggers, with its SQL),
    the header metadata in PRAGMAS and a digest of all columns of all tables, the ones this migration drops included."""
    schema = hashlib.sha256()
    for row in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"):
        schema.update(repr(row).encode())
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND sql NOT LIKE 'CREATE VIRTUAL%' ORDER BY name")]
    digests = {t: _table_digest(conn, t) for t in tables}
    return {
        "schema": schema.hexdigest(),
        "pragmas": {p: conn.execute(f"PRAGMA {p}").fetchone()[0] for p in PRAGMAS},
        "digests": {t: d[0] for t, d in digests.items()},
        "rows": {t: d[1] for t, d in digests.items()},
    }


def fingerprint_differences(expected: dict, actual: dict) -> list[str]:
    """Names (never values) of what differs between two content_fingerprint results."""
    problems = []
    if expected["schema"] != actual["schema"]:
        problems.append("schema objects (sqlite_master)")
    problems += [f"pragma {p}" for p in PRAGMAS if expected["pragmas"][p] != actual["pragmas"][p]]
    for table in sorted(set(expected["digests"]) | set(actual["digests"])):
        if expected["digests"].get(table) != actual["digests"].get(table):
            problems.append(f"table {table}")
    return problems


def verify_backup(backup_path: str, source: dict) -> None:
    """The backup must be content-equivalent to the approved source state (`source` = content_fingerprint taken on the
    write-locked connection before any DDL): same sqlite_master, header metadata and every column of every table, and it
    must pass integrity_check. This proves the backup holds the approved logical content, including the columns this
    migration drops; it does not make the file byte-identical (SQLite may write a different but equivalent file)."""
    conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        saved = content_fingerprint(conn)
    finally:
        conn.close()
    problems = fingerprint_differences(source, saved)
    if integrity != "ok":
        problems.insert(0, f"integrity_check: {integrity}")
    if problems:
        raise MigrationError(f"the backup does not match the approved database ({'; '.join(problems)})")


def _quarantine(path: str) -> str:
    """Rename a backup artifact this run created to <name>.UNVERIFIED.db.bak (never over an existing file); returns a
    sentence describing where the artifact is and that it must not be used."""
    target_base = path.removesuffix(".db.bak") + ".UNVERIFIED"
    try:
        if not os.path.lexists(path):
            return "no backup artifact remains"
        if stat.S_ISLNK(os.lstat(path).st_mode):
            return f"UNVERIFIED artifact {path} is a symlink and was left alone; do not use it"
        for n in range(1, 100):
            target = f"{target_base}{'' if n == 1 else n}.db.bak"
            try:
                os.link(path, target)  # fails if target exists: nothing is overwritten
            except FileExistsError:
                continue
            try:
                os.unlink(path)
            except OSError as exc:
                return (f"UNVERIFIED backup artifact exists under two names, {path} and {target} (the original name could not be "
                        f"removed: {exc.strerror or exc}); neither is a valid backup, do not restore from either")
            return f"the UNVERIFIED backup artifact was quarantined as {target}; it is not a valid backup, do not restore from it"
    except OSError as exc:
        return f"UNVERIFIED backup artifact left at {path} ({exc}); it is not a valid backup, do not restore from it"
    return f"UNVERIFIED backup artifact left at {path}; it is not a valid backup, do not restore from it"


def _serialized_sha256(conn) -> str:
    """SHA-256 of the bytes SQLite itself serializes for `conn`'s main database."""
    return hashlib.sha256(conn.serialize()).hexdigest()


def _backup_approved_state(path: str, identity: tuple[int, int], fd: int, expected_sha256: str, locked, source: dict,
                           tag: str = "confidence-level") -> str:
    """Under the write lock: copy through a read-only connection, verify it, confirm the source still is the approved
    state. Returns the verified backup's path. Any failure quarantines the new artifact and raises MigrationError
    naming it; the database has not been changed at that point."""
    reader = open_readonly(path)  # SQLite's backup hangs on the connection that holds the write lock; a reader may read
    try:
        backup_path = backup_database(reader, path, tag, exclusive=True)
    except BackupError as exc:
        raise MigrationError(f"{exc}; {_quarantine(exc.path)}; the database was not changed") from exc
    except BaseException as exc:  # KeyboardInterrupt etc. after the file was created (it carries backup_artifact), or a refusal
        artifact = getattr(exc, "backup_artifact", None)
        if artifact:
            exc.add_note(_quarantine(artifact))
        raise
    finally:
        reader.close()
    try:
        digest = sha256_file(backup_path)
        verify_backup(backup_path, source)
        if sha256_file(backup_path) != digest:
            raise MigrationError("the backup file changed while it was being verified")
        if _hash_of(path, identity, fd) != expected_sha256 or _serialized_sha256(locked) != expected_sha256:
            raise MigrationError("The database changed while it was being backed up")
    except BaseException as exc:
        note = _quarantine(backup_path)
        if not isinstance(exc, Exception):
            exc.add_note(note)
            raise
        raise MigrationError(f"{exc}; {note}; the database was not changed") from exc
    return backup_path


def _identity(path: str) -> tuple[int, int]:
    """(st_dev, st_ino) of the regular file `path` names; a symlink or a non-file is refused."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise  # callers that care say "disappeared"
    except OSError as exc:
        raise MigrationError(f"could not inspect {path} ({exc.strerror or exc})") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise MigrationError(f"{path} is no longer a regular file (replaced or linked); not migrating")
    return info.st_dev, info.st_ino


def _check_unchanged(path: str, identity: tuple[int, int]) -> None:
    try:
        now = _identity(path)
    except FileNotFoundError:
        raise MigrationError(f"{path} disappeared; not migrating") from None
    if now != identity:
        raise MigrationError(f"{path} is a different file than the one that was validated (renamed or replaced); not migrating")
    sidecars = [path + suffix for suffix in SIDECAR_SUFFIXES if os.path.exists(path + suffix)]
    if sidecars:
        raise MigrationError(f"Unexpected state: {', '.join(sidecars)} exist; not migrating")


def _hash_of(path: str, identity: tuple[int, int], fd: int) -> str:
    """SHA-256 of the file behind `fd`, after confirming `path` still names the validated file.

    `fd` is one read-only descriptor opened before the SQLite connection and closed after it; it is read with pread. This is
    deliberate: POSIX fcntl locks, which SQLite uses, are dropped when the process closes ANY descriptor of the file, so
    opening and closing the database with open() while the write lock is held silently releases that lock (a second
    process could then write; the test with a real second process found exactly that).
    """
    _check_unchanged(path, identity)
    digest = hashlib.sha256()
    offset = 0
    try:
        while chunk := os.pread(fd, 1 << 20, offset):
            digest.update(chunk)
            offset += len(chunk)
    except OSError as exc:
        raise MigrationError(f"could not read {path} to compute its SHA-256 ({exc.strerror or exc}) after {offset} bytes") from exc
    return digest.hexdigest()


def _refuse_wal(path: str) -> None:
    """Bytes 18-19 of the header are 2 for a WAL database; read without opening it through SQLite."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(20)
    except OSError as exc:
        raise MigrationError(f"could not read the header of {path} ({exc.strerror or exc})") from exc
    if len(header) == 20 and (header[18] == 2 or header[19] == 2):
        raise MigrationError("The database is in WAL mode; the hash protocol needs a rollback-journal database; not migrating")


def _cleanup(close, what: str) -> None:
    """Run a close in a `finally`. If another exception is already in flight, a failure here is only attached to it as a
    note (it must not hide the real cause); otherwise it propagates."""
    in_flight = sys.exc_info()[1]  # the exception being handled by the caller's `finally`, if any
    try:
        close()
    except Exception as exc:
        if in_flight is None:
            raise
        in_flight.add_note(f"closing {what} also failed: {exc!r}")


def _annotate(exc: BaseException, backup_path: str | None) -> None:
    """Say, on `exc`, what is known about the database and where the verified backup is. Only states with evidence are
    stated (see _settle); an exception whose state was not established gets no claim at all."""
    state = getattr(exc, "db_state", None)
    kept = f"verified backup of the approved state at {backup_path}" if backup_path else "no verified backup was made"
    if state == UNCHANGED:
        exc.add_note("the database was not changed (no write was attempted); " + kept)
    elif state == ROLLED_BACK:
        exc.add_note(f"rolled back, database unchanged; {kept}" + (" (kept)" if backup_path else ""))
    elif state == MIGRATED:
        exc.add_note(f"the migration was COMMITTED: the database IS migrated to version {COMPANY_SCHEMA_VERSION}; {kept}. "
                     "Do not run the migration again; check the result before using the database")
    elif state == ROLLBACK_FAILED:
        exc.add_note("no COMMIT was attempted, but the ROLLBACK failed: the state of the database is NOT confirmed (nothing was "
                     "committed by this run; an open transaction is discarded when the connection closes). Do not run the "
                     f"migration again; inspect PRAGMA user_version and the schema first; {kept}")
    elif state == UNKNOWN:
        exc.add_note("the outcome of the COMMIT is NOT confirmed: the database may or may not be migrated. Do not run the "
                     f"migration again; inspect PRAGMA user_version and the schema first; {kept}")


def apply(path: str, expected_sha256: str) -> tuple[dict, dict, str]:
    """Verify, back up and migrate in place under one write lock. Returns (before, after, backup path).

    Under BEGIN IMMEDIATE, before any DDL: same file (st_dev/st_ino), no sidecar, file hash == expected, the connection's
    own serialization == expected, content fingerprint, backup (read-only connection), backup verified, hashes again.
    Any failure before the COMMIT rolls back; nothing has been changed by then, and a backup that was not fully verified is
    quarantined. Every exception leaves here annotated with what is known about the database (see _settle / _annotate).
    """
    backup = {}
    progress = {"phase": "before the write transaction"}
    try:
        result = _apply(path, expected_sha256, backup, progress)
    except BaseException as exc:
        if not hasattr(exc, "db_state"):  # not settled by migrate(): the phase tells what is known
            if progress["phase"] == "before the write transaction":
                exc.db_state = UNCHANGED
            elif progress["phase"] == "after the migration":
                exc.db_state = MIGRATED
        _annotate(exc, backup.get("path"))
        raise
    return result


def _apply(path: str, expected_sha256: str, backup: dict, progress: dict) -> tuple[dict, dict, str]:
    try:
        identity = _identity(path)
    except FileNotFoundError:
        raise MigrationError(f"{path} does not exist; not migrating") from None
    _refuse_wal(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)  # kept open until SQLite is done: see _hash_of
    except OSError as exc:
        raise MigrationError(f"could not open {path} read-only to hash it ({exc.strerror or exc})") from exc
    try:
        try:
            info = os.fstat(fd)
        except OSError as exc:
            raise MigrationError(f"could not inspect the open file {path} ({exc.strerror or exc})") from exc
        if (info.st_dev, info.st_ino) != identity:
            raise MigrationError(f"{path} is a different file than the one that was validated; not migrating")
        if _hash_of(path, identity, fd) != expected_sha256:
            raise MigrationError("The database's SHA-256 differs from the approved one; not migrating")
        conn = open_readwrite(path, isolation_level=None)  # mode=rw: never creates a file
        try:
            _check_unchanged(path, identity)  # the connection opened the validated file, not a replacement
            if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal":
                raise MigrationError("The database is in WAL mode; not migrating")

            def prepare(locked, before):
                _check_unchanged(path, identity)
                if _hash_of(path, identity, fd) != expected_sha256:
                    raise MigrationError("The database changed since it was approved (SHA-256 differs)")
                if _serialized_sha256(locked) != expected_sha256:  # the lock holder's own view of the pages is the approved state
                    raise MigrationError("The locked connection does not see the approved database (serialized SHA-256 differs)")
                source = content_fingerprint(locked)
                backup["before"] = before
                backup["path"] = _backup_approved_state(path, identity, fd, expected_sha256, locked, source)

            progress["phase"] = "in the migration"
            migrate(conn, _prepare=prepare)  # tags any exception with db_state
            progress["phase"] = "after the migration"
            return backup["before"], snapshot(conn), backup["path"]
        finally:
            _cleanup(conn.close, "the database connection")
    finally:
        _cleanup(lambda: os.close(fd), "the read-only descriptor")


def report(company: str, before: dict, after: dict) -> str:
    lines = [f"  company: {company}", f"  schema version: {before['user_version']} -> {after['user_version']}",
             f"  cards.{COLUMN}: present, nullable, {after['levels_assigned']} assigned",
             f"  obsolete Bid confidence columns: {', '.join(before['obsolete_columns']) or 'none'} -> {', '.join(after['obsolete_columns']) or 'dropped'}"]
    for table in sorted(before["rows"]):
        lines.append(f"  {table}: {before['rows'][table]} rows -> {after['rows'].get(table)} rows")
    lines.append(f"  foreign key violations: {before['foreign_key_violations']} -> {after['foreign_key_violations']}")
    lines.append(f"  integrity_check: {before['integrity']} -> {after['integrity']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Upgrade a company database (schema version 2 or 3) to version 4.")
    parser.add_argument("--company", required=True, choices=COMPANY_SLUGS, help="the company this database belongs to")
    parser.add_argument("--database", required=True, help="absolute path of the company database (required; no default)")
    parser.add_argument("--apply", action="store_true", help="write the change (after a verified backup); default is a dry run")
    parser.add_argument("--confirm-app-stopped", action="store_true", help="required with --apply: the application is stopped")
    parser.add_argument("--expect-sha256", help="required with --apply: the approved database file's SHA-256 (the dry run prints it)")
    args = parser.parse_args(argv)
    try:
        path = check_target(args.database)
        if args.apply:
            if not args.confirm_app_stopped:
                raise MigrationError("--apply needs --confirm-app-stopped (stop the application first; this tool never stops it)")
            if not args.expect_sha256:
                raise MigrationError("--apply needs --expect-sha256 (the hash the dry run printed for the approved file)")
            before, after, backup_path = apply(path, args.expect_sha256.lower())
            print(f"Migrated {path} to company schema version {COMPANY_SCHEMA_VERSION}\n{report(args.company, before, after)}")
            print(f"Applied. Verified backup of the previous state: {backup_path}")
            return 0
        file_hash = sha256_file(path)
        before, after = dry_run(path)
        print(f"Dry run on {path} (an in-memory copy was migrated; nothing was written):\n  database SHA-256: {file_hash}\n{report(args.company, before, after)}")
        return 0
    except (MigrationError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        for note in getattr(exc, "__notes__", []):
            print(f"  {note}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
