"""Import an approved Salcomp Bid workbook into the commercial database - guarded, explicit, repeatable.

    uv run python commercial_import.py check   --database <abs> --expect-db-sha256 <h> --admin-email <e> [--cpm present]
    uv run python commercial_import.py backup  --database <abs> --dest <new file>
    uv run python commercial_import.py restore --backup <file> --dest <new file>
    uv run python commercial_import.py import  --database <abs> --expect-db-sha256 <h> --admin-email <e> \\
        --workbook <xlsx> --expect-workbook-sha256 <h> --preview <json> --expect-preview-sha256 <h> \\
        --decisions <json> [--apply]
    uv run python commercial_import.py verify  --database <abs> --preview <json> --expect-preview-sha256 <h>

Nothing here reads DATABASE_URL or ./pm.db (this module doesn't even import database.py); the target is the
path given, validated by db_target_guard (never backend/pm.db or pm.db.bak, no symlink, never created, pinned
by SHA-256, sole expected administrator, cpm present). `import` is a dry run unless --apply, and either way:
1. the workbook and the approved preview must hash to the pinned values;
2. the live preview (workbook + decisions) must equal the approved preview record by record, and be
   exactly the creates it lists - any difference refuses;
3. a preview of nothing but unchanged rows (a repeat) is handed to the service, which makes it a no-op: no card,
   batch, ledger or event is written;
4. --apply runs bid_import_service.apply_bid_import (one transaction) with a before_commit check of foreign
   keys and counts; the importer is the existing administrator, recorded as the batch's user, and the
   technical operator (--operator, default "cli:<os user>") goes into the batch's mapping_json;
5. `verify` re-reads the database read-only against the approved preview.
The decisions file is a JSON list of {"row_number": N, "target_stage": "<column title>"} (RowDecision fields).
No card is assigned and no user is created.

A ledger revert (bid_import_revert / the admin screen) removes the imported cards but keeps their history, batch and
ledger: it is NOT the same as restoring the pre-import backup, which is the only way back to the exact earlier state.
"""
import argparse
import getpass
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from bid_excel_reader import read_bid_excel_file
from bid_import_preview import Action, RowDecision
from bid_import_service import apply_bid_import, preview_bid_import
from db_target_guard import (
    TargetRefused, check_state, logical_dump, make_backup, open_readonly, restore_backup, sha256_file, validate_target,
)
from models import Card, User

BASELINE_COUNT_SQL = "SELECT COUNT(*) FROM card_stage_events WHERE event_type = 'baseline' AND source = 'import'"
PRE_IMPORT_COUNTS = {"cards": 0, "import_batches": 0, "card_stage_events": 0, "card_source_refs": 0, "card_bid_details": 0}


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _pin(path: str, expected_sha256: str, what: str) -> None:
    if not Path(path).is_file() or sha256_file(path) != expected_sha256:
        raise TargetRefused(f"The {what} is missing or differs from the approved one (SHA-256)")


def load_decisions(path: str) -> tuple[RowDecision, ...]:
    return tuple(RowDecision(**item) for item in sorted(json.loads(Path(path).read_text()), key=lambda item: item["row_number"]))


def load_approved(preview_path: str) -> dict:
    return json.loads(Path(preview_path).read_text())


def _engine(path: str):
    """mode=rw: a missing file is an error, never a new empty database."""
    engine = create_engine(f"sqlite:///file:{quote(path)}?mode=rw&uri=true", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _foreign_keys_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def compare_to_approved(preview, approved: dict) -> list[str]:
    """Record-level differences between a live preview and the approved preview JSON."""
    records = {record["source_row"]: record for record in approved["records"]}
    problems = []
    if len(preview.rows) != len(records) or set(records) != {row.row_number for row in preview.rows}:
        problems.append("row set differs")
    for row in preview.rows:
        record = records.get(row.row_number)
        if record is None:
            continue
        live = {
            "source_key": row.source_key, "action": row.action.value, "proposed_stage": row.target_stage,
            "card": dict(row.fields.card), "bid_details": {k: _iso(v) for k, v in row.fields.bid_details.items()},
            "source_ref": {k: _iso(v) for k, v in row.fields.source_ref.items()},
        }
        problems += [f"row {row.row_number}: {key} differs" for key, value in live.items() if record[key] != value]
    return problems


class _CommitGuard:
    """before_commit hook, inside the import transaction after the service's final flush."""

    def __init__(self, expect_cards: int, baseline_before: int, expect_new: int, fail: str | None = None):
        self.expect_cards, self.baseline_before, self.expect_new, self.fail, self.ran = expect_cards, baseline_before, expect_new, fail, False

    def __call__(self, session):
        if self.ran:
            return
        self.ran = True
        if self.fail:
            raise RuntimeError(self.fail)
        if session.execute(text("PRAGMA foreign_key_check")).fetchall():
            raise RuntimeError("foreign_key_check reports violations")
        cards = session.execute(text("SELECT COUNT(*) FROM cards")).scalar()
        new_baselines = session.execute(text(BASELINE_COUNT_SQL)).scalar() - self.baseline_before
        if cards != self.expect_cards or new_baselines != self.expect_new:
            raise RuntimeError(f"pre-commit counts wrong: cards={cards}, new baseline events={new_baselines}")


def run_import(
    database: str, *, expect_db_sha256: str, admin_email: str, workbook: str, expect_workbook_sha256: str,
    preview_path: str, expect_preview_sha256: str, decisions_path: str, board_id: int = 1, apply: bool = False,
    operator: str | None = None, inject_failure: str | None = None,
) -> dict:
    """Guards, then the whole import (or its dry run). Returns a report; TargetRefused/ImportRejected on refusal."""
    path = validate_target(database, expected_sha256=expect_db_sha256)
    _pin(workbook, expect_workbook_sha256, "workbook")
    _pin(preview_path, expect_preview_sha256, "approved preview")
    check_state(path, expect_cpm=True, admin_email=admin_email)  # identity, schema and integrity, read-only

    approved = load_approved(preview_path)
    decisions = load_decisions(decisions_path)
    read_result = read_bid_excel_file(workbook)
    if not read_result.ok:
        raise TargetRefused("The workbook did not read cleanly")
    approved_keys = {record["source_key"] for record in approved["records"]}
    operator = operator or f"cli:{getpass.getuser()}"

    with Session(_engine(path)) as db:
        admin = db.query(User).filter(User.role == "admin").one()
        preview = preview_bid_import(db, read_result, board_id, decisions)
        actions = Counter(row.action for row in preview.rows)
        report = {"database": path, "operator": operator, "administrator_id": admin.id,
                  "preview_actions": {action.value: actions.get(action, 0) for action in Action}}
        total = len(approved["records"])
        if actions == {Action.IGNORE: total} and {row.source_key for row in preview.rows} == approved_keys:
            report["repeat"] = True  # everything already imported: the service turns this into a no-op
        else:
            problems = compare_to_approved(preview, approved)
            if problems:
                raise TargetRefused("The live preview differs from the approved preview: " + "; ".join(problems[:5]))
            if actions != {Action.CREATE: total}:
                raise TargetRefused(f"Expected {total} creates only, got {report['preview_actions']}")
            report["preview_matches_approved"] = True
            if not apply:
                report["result"] = "dry_run"
                return report
        if not apply:
            report["result"] = "nothing_to_do"
            return report

        baseline_before = db.execute(text(BASELINE_COUNT_SQL)).scalar()
        cards_before = db.query(Card).count()
        if not report.get("repeat"):
            event.listen(db, "before_commit", _CommitGuard(cards_before + total, baseline_before, total, inject_failure))
        outcome = apply_bid_import(db, read_result, preview, decisions, board_id, admin, operator=operator)
        report.update(result="no_op" if outcome.no_op else "applied", batch_id=outcome.batch_id,
                      created=len(outcome.created_card_ids), skipped=outcome.skipped, review=outcome.review)
    return report


def verify_import(database: str, preview_path: str, *, expect_cards: int | None = None) -> dict:
    """Read-only: the database against the approved preview. Every value is a bool or a number to inspect."""
    approved = load_approved(preview_path)
    records = {record["source_key"]: record for record in approved["records"]}
    expected = len(records) if expect_cards is None else expect_cards
    conn = open_readonly(database)
    conn.row_factory = sqlite3.Row
    one = lambda sql, *args: conn.execute(sql, args).fetchone()[0]
    result = {
        "integrity_check": [row[0] for row in conn.execute("PRAGMA integrity_check")],
        "foreign_key_violations": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
    }
    counts = {name: one(f'SELECT COUNT(*) FROM "{name}"') for (name,) in
              conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()}
    result["cards"] = counts["cards"]
    result["cards_refs_details_equal"] = counts["cards"] == counts["card_source_refs"] == counts["card_bid_details"] == expected
    stages = {row[0]: row[1] for row in conn.execute(
        "SELECT col.title, COUNT(c.id) FROM columns col LEFT JOIN cards c ON c.column_id = col.id GROUP BY col.id")}
    result["stage_counts"] = stages
    expected_stages = Counter(record["proposed_stage"] for record in records.values()) if expected else Counter()
    result["stage_counts_match_approved"] = {t: n for t, n in stages.items() if n} == dict(expected_stages)
    revenue = {row[0]: [row[1], row[2]] for row in conn.execute(
        "SELECT revenue_currency, SUM(revenue_current_year_cents), SUM(revenue_next_year_cents) FROM card_bid_details GROUP BY 1")}
    grouped: dict = {}
    for record in records.values() if expected else ():
        details = record["bid_details"]
        grouped.setdefault(details["revenue_currency"], []).append(
            (details["revenue_current_year_cents"], details["revenue_next_year_cents"]))
    # SQL SUM semantics: NULL when every value is NULL
    total = lambda values: sum(v for v in values if v is not None) if any(v is not None for v in values) else None
    expected_revenue = {cur: [total([r[0] for r in rows]), total([r[1] for r in rows])] for cur, rows in grouped.items()}
    result["revenue_by_currency"] = revenue
    result["revenue_matches_approved"] = revenue == expected_revenue
    result["cpm_populated"] = one("SELECT COUNT(*) FROM card_bid_details WHERE cpm IS NOT NULL AND cpm <> ''")
    result["cpm_matches_approved"] = result["cpm_populated"] == (sum(1 for r in records.values() if r["bid_details"]["cpm"]) if expected else 0)
    mismatches = []
    rows = conn.execute(
        "SELECT s.source_key, s.source_code, s.source_row_hash, c.company, c.title, c.salesperson_id, c.deal_value, d.*, col.title AS stage "
        "FROM card_source_refs s JOIN cards c ON c.id = s.card_id JOIN card_bid_details d ON d.card_id = c.id "
        "JOIN columns col ON col.id = c.column_id").fetchall()
    result["source_keys_equal_approved"] = ({row["source_key"] for row in rows} == set(records)) if expected else not rows
    for row in rows:
        record = records.get(row["source_key"])
        if record is None:
            mismatches.append(row["source_key"])
            continue
        if (row["stage"], row["company"], row["title"]) != (record["proposed_stage"], record["card"]["company"], record["card"]["title"]):
            mismatches.append(["card", row["source_key"]])
        mismatches += [[key, row["source_key"]] for key, value in record["bid_details"].items() if _iso(row[key]) != value]
        if (row["source_row_hash"], row["source_code"]) != (record["source_ref"]["source_row_hash"], record["source_ref"]["source_code"]):
            mismatches.append(["source_ref", row["source_key"]])
        if row["salesperson_id"] is not None or row["deal_value"] is not None:
            mismatches.append(["fabricated owner or value", row["source_key"]])
    result["record_mismatches"] = mismatches
    per_card = conn.execute(
        "SELECT e.card_id, COUNT(*), MIN(e.event_type), MIN(e.source) FROM card_stage_events e JOIN cards c ON c.id = e.card_id GROUP BY e.card_id").fetchall()
    result["one_baseline_import_event_per_card"] = (
        len(per_card) == expected and all(n == 1 and t == "baseline" and s == "import" for _, n, t, s in per_card))
    result["no_fabrication"] = {
        "users": counts["users"] == 1, "assigned_cards": one("SELECT COUNT(*) FROM cards WHERE salesperson_id IS NOT NULL") == 0,
        "audit_events": counts["audit_events"] == 0, "card_edit_history": counts["card_edit_history"] == 0,
        "card_actions": counts["card_actions"] == 0,
    }
    conn.close()
    return result


def verification_failures(result: dict) -> list[str]:
    bad = [key for key in ("cards_refs_details_equal", "stage_counts_match_approved", "revenue_matches_approved", "cpm_matches_approved",
                           "source_keys_equal_approved", "one_baseline_import_event_per_card") if result[key] is not True]
    if result["integrity_check"] != ["ok"] or result["foreign_key_violations"]:
        bad.append("integrity/foreign keys")
    if result["record_mismatches"]:
        bad.append("record_mismatches")
    bad += [f"no_fabrication.{key}" for key, ok in result["no_fabrication"].items() if not ok]
    return bad


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, *flags):
        command = sub.add_parser(name)
        for flag in flags:
            command.add_argument(f"--{flag}", required=True)
        return command

    check = add("check", "database", "expect-db-sha256", "admin-email")
    check.add_argument("--cpm", choices=("present", "absent"), required=True)
    add("backup", "database", "dest")
    add("restore", "backup", "dest")
    importer = add("import", "database", "expect-db-sha256", "admin-email", "workbook", "expect-workbook-sha256",
                   "preview", "expect-preview-sha256", "decisions")
    importer.add_argument("--operator")
    importer.add_argument("--board-id", type=int, default=1)
    importer.add_argument("--apply", action="store_true")
    add("verify", "database", "preview", "expect-preview-sha256")
    args = parser.parse_args(argv)

    try:
        if args.command == "check":
            path = validate_target(args.database, expected_sha256=args.expect_db_sha256)
            output = {"database": path, "counts": check_state(path, expect_cpm=args.cpm == "present", admin_email=args.admin_email)}
        elif args.command == "backup":
            path = validate_target(args.database)
            output = make_backup(path, args.dest)
        elif args.command == "restore":
            output = restore_backup(args.backup, args.dest)
            output["logical_dump_equal_to_backup"] = logical_dump(args.backup) == logical_dump(args.dest)
        elif args.command == "import":
            output = run_import(
                args.database, expect_db_sha256=args.expect_db_sha256, admin_email=args.admin_email, workbook=args.workbook,
                expect_workbook_sha256=args.expect_workbook_sha256, preview_path=args.preview,
                expect_preview_sha256=args.expect_preview_sha256, decisions_path=args.decisions, board_id=args.board_id,
                apply=args.apply, operator=args.operator,
            )
        else:
            _pin(args.preview, args.expect_preview_sha256, "approved preview")
            path = validate_target(args.database)
            output = verify_import(path, args.preview)
            output["failures"] = verification_failures(output)
            print(json.dumps(output, indent=2, default=str))
            return 1 if output["failures"] else 0
    except Exception as exc:  # refusals and import errors alike: report, exit 1, never a traceback with row data
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
